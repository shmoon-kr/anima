"""What a person types (`anima play`, `anima watch`): aliases, routing to sessions, taking the wheel.

tintin habits carry over:
- `;` separates commands, an alias is the first word (`$1`..`$9`, `$*` for its arguments)
- `#name command` sends to that character, `#all command` to everyone, `#party command` to the
  members in the focused character's room; a plain line goes to the focused character
- `#go <room>` walks there with Memoria's paths (also `#name #go <room>`), `#stop` cancels it
- `#take name` / `#release name`: hand the character to the person and back. Any typed command
  pauses that character's behavior selection for HOLD_S (reflexes keep running): otherwise the
  agent would turn around on its next tick and the two would fight over the wheel.

Aliases live in config/aliases.yaml (people's own; config/aliases.example.yaml as a start).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HOLD_S = 30.0
MAX_ALIAS_DEPTH = 5


@dataclass
class Order:
    agent: str
    kind: str              # send | go | stop | take | release
    text: str = ""


@dataclass
class Desk:
    roster: list[str]
    alias_paths: list[Path] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    _mtime: float = -1.0

    def load_aliases(self) -> None:
        """The first alias file that exists; reloaded when it changes."""
        for p in self.alias_paths:
            if p.exists():
                m = p.stat().st_mtime
                if m != self._mtime:
                    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                    self.aliases = {str(k): str(v) for k, v in data.items()}
                    self._mtime = m
                return

    def agent(self, word: str) -> str | None:
        """A character by name, case-insensitive, or by a unique prefix (`#va` for a name starting va)."""
        w = word.lower()
        exact = [n for n in self.roster if n.lower() == w]
        if exact:
            return exact[0]
        pre = [n for n in self.roster if n.lower().startswith(w)]
        return pre[0] if len(pre) == 1 else None

    # ------------------------------------------------------------ text
    @staticmethod
    def split(line: str) -> list[str]:
        """`;` separates commands (`\\;` is a literal semicolon)."""
        parts = re.split(r"(?<!\\);", line)
        return [p.replace("\\;", ";").strip() for p in parts if p.strip()]

    def expand(self, cmd: str, depth: int = 0) -> list[str]:
        self.load_aliases()
        words = cmd.split()
        if not words or depth >= MAX_ALIAS_DEPTH or words[0] not in self.aliases:
            return [cmd]
        args = words[1:]
        body = self.aliases[words[0]]
        used = bool(re.search(r"\$(\d|\*)", body))
        body = re.sub(r"\$\*", " ".join(args), body)
        body = re.sub(r"\$(\d)", lambda m: args[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(args) else "", body)
        if not used and args:
            body += " " + " ".join(args)
        out: list[str] = []
        for part in self.split(body):
            out += self.expand(part, depth + 1)
        return out

    # ------------------------------------------------------------ routing
    def route(self, line: str, focus: str | None, room_mates: list[str] | None = None) -> tuple[list[Order], list[str]]:
        """A typed line -> orders for sessions, plus notes for the person."""
        orders: list[Order] = []
        notes: list[str] = []
        for part in self.split(line):
            for cmd in self.expand(part):
                self._one(cmd, focus, room_mates or ([focus] if focus else []), orders, notes)
        return orders, notes

    def _one(self, cmd: str, focus: str | None, mates: list[str], orders: list[Order], notes: list[str]) -> None:
        if not cmd.startswith("#"):
            if focus is None:
                notes.append("no character in focus: use #name command")
            else:
                orders.append(Order(focus, "send", cmd))
            return
        head, _, rest = cmd[1:].partition(" ")
        head, rest = head.lower(), rest.strip()
        if head in ("go", "stop", "take", "release"):
            if head in ("take", "release") and rest:
                who = self.agent(rest)
                if who is None:
                    notes.append(f"#{head}: who is {rest!r}?")
                else:
                    orders.append(Order(who, head))
                return
            if focus is None:
                notes.append(f"#{head}: no character in focus")
                return
            if head == "go" and not rest:
                notes.append("#go <room name>")
                return
            orders.append(Order(focus, head, rest))
            return
        targets = (list(self.roster) if head == "all" else
                   mates if head == "party" else
                   [a] if (a := self.agent(head)) else [])
        if not targets:
            notes.append(f"#{head}: no such character (#all, #party, or a name)")
            return
        if not rest:
            notes.append(f"#{head}: what should {', '.join(targets)} do?")
            return
        for t in targets:
            self._one(rest, t, mates, orders, notes)


def describe_for_narration(ev: dict[str, Any]) -> str | None:
    """One dim line between the server text: what an agent decided and why (the `narrate` view)."""
    t, d = ev.get("type"), ev.get("data", {})
    if t == "command.sent":
        src = d.get("source", {})
        if src.get("kind") in ("system",) or d.get("secret"):
            return None
        why = src.get("reason", "")
        who = src.get("id", "")
        return f"» {d.get('text')}   ({src.get('kind')} {who.split('/')[-1]}{': ' + why if why else ''})"
    if t == "runtime.behavior":
        sc = ", ".join(f"{x['id'].split('/')[-1]} {x['score']}" for x in d.get("scores", [])[:3])
        return f"» now {d.get('to')}   ({sc})" if d.get("to") else "» idle"
    if t == "runtime.task" and d.get("event") in ("started", "succeeded", "failed", "abandoned"):
        return f"» task {d.get('name')} {d.get('event')}"
    if t == "animus.request":
        why = d.get("context", {}).get("why_now")
        return f"💭 asking: {d.get('question_kind')}" + (f" — {', '.join(why)}" if why else "")
    if t == "animus.response":
        a = d.get("answer")
        return f"💭 {a.get('reason') or a.get('why') or ''}" if isinstance(a, dict) else f"💭 {a!r}"
    if t == "runtime.animus" and d.get("event") in ("applied", "reverted", "expired"):
        ch = ", ".join(f"{c.get('key')}→{c.get('new')!r}" if "new" in c else f"{c.get('key')} back"
                       for c in d.get("changes") or [])
        return f"💭 {d.get('event')}: {ch}"
    return None
