"""People's screens (`anima play`, `anima watch --mode`): the MUD as the character sees it.

Modes:
- mud      the server's own text with its colours, as tintin showed it
- narrate  the same, with a dim line where an agent decided something or the Animus thought
- events   the protocol events (the developer view, what `anima watch` always showed)

`anima play` splits the terminal like tintin's #split: the focused character's screen scrolls on top,
a party panel (HP/MP/MV and what each one is doing) stays at the bottom, and the last line is where
you type. Typed lines go to the supervisor, which expands aliases and routes them (humans.py).
Local commands: `#focus name`, `#mode mud|narrate|events`, `#quit`.
"""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import sys
import termios
import threading
import tty
from pathlib import Path
from typing import Any

from anima.session.humans import describe_for_narration

DIM, RESET, BOLD = "\033[2m", "\033[0m", "\033[1m"
ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
MODES = ("mud", "narrate", "events")


def _connect(run_dir: Path) -> socket.socket | None:
    path = run_dir / "anima.sock"
    if not path.exists():
        return None
    s = socket.socket(socket.AF_UNIX)
    s.connect(str(path))
    return s


def _lines_of(sock: socket.socket):
    buf = b""
    while True:
        data = sock.recv(65536)
        if not data:
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                yield json.loads(line)


def _clean(text: str) -> str:
    """Server text for a terminal: CR LF -> LF, telnet leftovers out, colours kept."""
    return text.replace("\r\n", "\n").replace("\r", "").replace("\x00", "")


def render(msg: dict[str, Any], mode: str, describe) -> str | None:
    """One message from the supervisor as screen text for this mode (None: nothing to show)."""
    k = msg.get("k")
    if k == "text" and mode in ("mud", "narrate"):
        return _clean(msg["s"])
    if k == "ev":
        ev = msg["ev"]
        if mode == "events":
            return (describe(ev) or "") + "\n"
        if mode == "narrate":
            line = describe_for_narration(ev)
            return f"{DIM}{line}{RESET}\n" if line else None
        if mode == "mud" and ev.get("type") == "command.sent" and ev["data"].get("source", {}).get("kind") == "human":
            return f"{DIM}> {ev['data'].get('text')}{RESET}\n"         # what the person typed, like a local echo
    if k == "notes":
        return "".join(f"{BOLD}# {n}{RESET}\n" for n in msg.get("notes", []))
    return None


# ---------------------------------------------------------------- watch: plain scrolling

def watch(agent: str, mode: str, run_dir: Path, describe) -> int:
    s = _connect(run_dir)
    if s is None:
        print("not running")
        return 1
    s.sendall((json.dumps({"op": "view", "agents": [agent]}) + "\n").encode())

    def typing() -> None:
        for line in sys.stdin:
            s.sendall((json.dumps({"line": line.rstrip("\n"), "focus": agent}) + "\n").encode())
    threading.Thread(target=typing, daemon=True).start()
    at_line_start = True
    try:
        for msg in _lines_of(s):
            out = render(msg, mode, describe)
            if not out:
                continue
            if msg.get("k") != "text" and not at_line_start:
                out = "\n" + out                     # a note never glues onto a prompt
            sys.stdout.write(out)
            sys.stdout.flush()
            at_line_start = out.endswith("\n")
    except KeyboardInterrupt:
        pass
    return 0


# ---------------------------------------------------------------- play: split screen

def _bar(cur: int | None, top: int | None, width: int = 8) -> str:
    if not cur or not top:
        return " " * width
    n = max(0, min(width, round(width * cur / top)))
    return "█" * n + "·" * (width - n)


class Play:
    def __init__(self, focus: str, mode: str, run_dir: Path, describe) -> None:
        self.focus, self.mode, self.run_dir, self.describe = focus, mode, run_dir, describe
        self.party: dict[str, Any] = {}
        self.input = ""
        self.history: list[str] = []
        self.hist_i = 0
        self.partial = ""                            # the last unfinished line (a prompt) of the screen
        self.lock = threading.Lock()
        self.done = False

    # ---- layout
    def size(self) -> tuple[int, int]:
        c = shutil.get_terminal_size((100, 30))
        return c.lines, c.columns

    def panel_h(self) -> int:
        return len(self.party) + 1 if self.party else 1

    def region_bottom(self) -> int:
        rows, _ = self.size()
        return max(3, rows - self.panel_h() - 1)

    def out(self, s: str) -> None:
        sys.stdout.write(s)

    def setup(self) -> None:
        self.out("\033[2J")
        self.out(f"\033[1;{self.region_bottom()}r")       # scroll region: the MUD screen
        self.redraw()

    def teardown(self) -> None:
        self.out("\033[r\033[2J\033[H")
        sys.stdout.flush()

    # ---- drawing
    def write_screen(self, text: str) -> None:
        """Server text into the scroll region. Complete lines go out at once; an unfinished line (a prompt)
        is shown too and replaced when the rest of it arrives."""
        bottom = self.region_bottom()
        data = self.partial + text
        lines = data.split("\n")
        self.partial = lines.pop()
        self.out(f"\033[{bottom};1H\033[2K")               # clear the shown partial line
        for ln in lines:
            self.out(ln + RESET + "\n" if ln else "\n")
        self.out(self.partial + RESET)
        self.draw_panel()
        self.draw_input()

    def note(self, text: str) -> None:
        """A whole line of our own (narration, notes): goes above the unfinished prompt line."""
        bottom = self.region_bottom()
        self.out(f"\033[{bottom};1H\033[2K{text.rstrip(chr(10))}{RESET}\n{self.partial}{RESET}")
        self.draw_panel()
        self.draw_input()

    def draw_panel(self) -> None:
        rows, cols = self.size()
        top = self.region_bottom() + 1
        head = f" {self.focus} · {self.mode}   #name cmd · #all · #party · #go room · #take/#release · #focus · #mode "
        self.out(f"\033[{top};1H\033[2K{BOLD}\033[7m{head[:cols].ljust(cols)}{RESET}")
        for i, (name, a) in enumerate(sorted(self.party.items(), key=lambda kv: kv[0] != self.focus)):
            mark = "✋" if a.get("taken") else "⏸" if a.get("held") else "  "
            conn = "" if a.get("in_game") else " (offline)"
            line = (f"{'>' if name == self.focus else ' '}{name:9s}{mark} HP {_bar(a.get('hp'), a.get('hp_max'))} "
                    f"MP {_bar(a.get('mp'), a.get('mp_max'))} MV {_bar(a.get('mv'), a.get('mv_max'))} "
                    f"{(a.get('position') or ''):8s} {a.get('behavior') or '-'}{' / ' + a['task'] if a.get('task') else ''}"
                    f"  {DIM}{a.get('room') or ''}{RESET}{conn}")
            self.out(f"\033[{top + 1 + i};1H\033[2K{line}")

    def draw_input(self) -> None:
        rows, cols = self.size()
        shown = self.input[-(cols - 3):]
        self.out(f"\033[{rows};1H\033[2K{BOLD}>{RESET} {shown}")
        sys.stdout.flush()

    def redraw(self) -> None:
        self.out(f"\033[1;{self.region_bottom()}r")
        self.draw_panel()
        self.draw_input()

    # ---- running
    def run(self) -> int:
        s = _connect(self.run_dir)
        if s is None:
            print("not running")
            return 1
        self.sock = s
        s.sendall((json.dumps({"op": "view", "agents": [self.focus]}) + "\n").encode())
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        reader = threading.Thread(target=self._read_server, daemon=True)
        try:
            with self.lock:
                self.setup()
            reader.start()
            self._read_keys(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            with self.lock:
                self.teardown()
            s.close()
        return 0

    def _read_server(self) -> None:
        try:
            for msg in _lines_of(self.sock):
                with self.lock:
                    if msg.get("k") == "status":
                        old_h = self.panel_h()
                        self.party = msg["party"]
                        if self.panel_h() != old_h:
                            self.out("\033[2J")
                            self.redraw()
                        self.draw_panel()
                        self.draw_input()
                        continue
                    if msg.get("a") not in (None, self.focus):
                        continue
                    out = render(msg, self.mode, self.describe)
                    if not out:
                        continue
                    if msg.get("k") == "text":
                        self.write_screen(out)
                    else:
                        for ln in out.rstrip("\n").split("\n"):
                            self.note(ln)
        finally:
            self.done = True

    def _send_line(self, line: str) -> None:
        self.sock.sendall((json.dumps({"line": line, "focus": self.focus}) + "\n").encode())

    def _local(self, line: str) -> bool:
        """#focus, #mode, #quit are handled here; everything else goes to the supervisor."""
        word, _, rest = line.strip().partition(" ")
        if word == "#quit":
            self.done = True
            return True
        if word == "#focus" and rest:
            name = next((n for n in self.party if n.lower().startswith(rest.strip().lower())), None)
            if name:
                self.focus, self.partial = name, ""
                self.sock.sendall((json.dumps({"agents": [name]}) + "\n").encode())
                self.out("\033[2J")
                self.redraw()
            else:
                self.note(f"{BOLD}# who is {rest}?{RESET}")
            return True
        if word == "#mode" and rest.strip() in MODES:
            self.mode = rest.strip()
            self.draw_panel()
            return True
        return False

    def _read_keys(self, fd: int) -> None:
        esc = ""
        while not self.done:
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            ch = os.read(fd, 1).decode(errors="ignore")
            with self.lock:
                if esc or ch == "\x1b":                  # arrow keys: history
                    esc += ch
                    if len(esc) >= 3:
                        if esc == "\x1b[A" and self.history:
                            self.hist_i = max(0, self.hist_i - 1)
                            self.input = self.history[self.hist_i]
                        elif esc == "\x1b[B" and self.history:
                            self.hist_i = min(len(self.history), self.hist_i + 1)
                            self.input = self.history[self.hist_i] if self.hist_i < len(self.history) else ""
                        esc = ""
                    self.draw_input()
                    continue
                if ch in ("\r", "\n"):
                    line, self.input = self.input, ""
                    if line.strip():
                        self.history.append(line)
                    self.hist_i = len(self.history)
                    if not self._local(line):
                        self._send_line(line)
                elif ch in ("\x7f", "\x08"):
                    self.input = self.input[:-1]
                elif ch == "\x04":                       # ctrl-d
                    self.done = True
                elif ch == "\x15":                       # ctrl-u
                    self.input = ""
                elif ch.isprintable():
                    self.input += ch
                self.draw_input()


def play(focus: str, mode: str, run_dir: Path, describe) -> int:
    return Play(focus, mode, run_dir, describe).run()
