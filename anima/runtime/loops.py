"""A general guard against doing the same useless thing again and again.

Phase 1 kept falling into one pattern: a behavior sent the same command to the same target and got
the same refusal, or nothing, over and over (kill 87 times, backstab 41, drink 114, give 35, junk
129, wear 'vest' every few seconds). Each had its own cause; this catches the pattern itself.

A command's answer is what the server sends back until the next prompt. If the same source
(behavior, reflex or task) sends the same command and gets the same refusal or no answer N times
within a short time, that command from that source is held back, longer each time it happens again,
and `runtime.loop` says so. Commands people type are never held back.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from anima.protocol.envelope import Event

REPEATS = 3            # the same useless answer this many times ...
WINDOW_S = 60.0        # ... within this long is a loop
PAUSE_S = 30.0         # the first pause; each further loop of the same command doubles it ...
PAUSE_MAX_S = 600.0    # ... up to this
GUARDED = ("behavior", "reflex", "task")
_FAILURE_TYPES = {"unknown", "items.cannot_take", "items.cannot_drop", "items.not_found", "items.give_failed"}


def useless(events: list[Event]) -> tuple | None:
    """What an answer says if it says no (refused, failed, nothing changed); None if it did something."""
    if not events:
        return (("nothing", ""),)
    no = []
    for ev in events:
        d = ev.data or {}
        if ev.type in _FAILURE_TYPES or ev.type.endswith((".failed", ".refused")):
            no.append((ev.type, str(d.get("reason") or d.get("result") or "")))
        elif ev.type == "items.used" and d.get("empty"):
            no.append((ev.type, "empty"))                    # "It is empty."
        else:
            return None
    return tuple(sorted(no))


@dataclass
class _Streak:
    answer: tuple
    count: int
    since: float


@dataclass
class LoopGuard:
    clock: Callable[[], float]
    publish: Callable[[str, dict[str, Any]], None]
    _open: tuple[str, str, float] | None = None              # (source id, command, sent at) awaiting its answer
    _answer: list[Event] = field(default_factory=list)
    _streaks: dict[tuple[str, str], _Streak] = field(default_factory=dict)
    _paused: dict[tuple[str, str], float] = field(default_factory=dict)
    _level: dict[tuple[str, str], int] = field(default_factory=dict)   # how many times this one looped

    def observe(self, ev: Event) -> None:
        if ev.type == "command.sent":
            self._close()
            src = (ev.data or {}).get("source") or {}
            if src.get("kind") in GUARDED and not (ev.data or {}).get("secret"):
                self._open = (src.get("id", ""), (ev.data or {}).get("text", ""), self.clock())
            return
        if self._open is None or ev.type.startswith("runtime.") or ev.type.startswith("animus."):
            return
        if ev.type == "prompt":
            self._close()
        else:
            self._answer.append(ev)

    def _close(self) -> None:
        if self._open is None:
            return
        source, text, _ = self._open
        key = (source, text)
        answer, self._open, self._answer = useless(self._answer), None, []
        now = self.clock()
        if answer is None:                                    # it did something: start over
            self._streaks.pop(key, None)
            self._level.pop(key, None)
            return
        s = self._streaks.get(key)
        if s is None or s.answer != answer or now - s.since > WINDOW_S:
            self._streaks[key] = _Streak(answer, 1, now)
            return
        s.count += 1
        if s.count < REPEATS:
            return
        level = self._level.get(key, 0)
        pause = min(PAUSE_MAX_S, PAUSE_S * 2 ** level)
        self._level[key] = level + 1
        self._paused[key] = now + pause
        del self._streaks[key]
        self.publish("runtime.loop", {"source": source, "command": text, "answer": [list(a) for a in answer],
                                      "count": s.count, "pause_s": pause})

    def paused(self, source: str, text: str) -> float:
        """Seconds this source must still not send this command (0: free)."""
        return max(0.0, self._paused.get((source, text), 0.0) - self.clock())
