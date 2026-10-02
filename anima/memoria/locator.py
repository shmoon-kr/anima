"""Where am I? Track one agent's room vnum from protocol events.

Text servers do not send room numbers, so the position is inferred:
- a sent movement command (or the leader's departure when following) gives the expected exit;
- the `room` event confirms it by name + description, or the room is looked up anywhere by
  name + description (descriptions make most same-named rooms distinct);
- a dark room confirms the expected exit if there was one.
If the event carries `id` (engines that know vnums), that is used directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from anima.memoria.graph import Graph
from anima.memoria.model import ABBR, DIRS, REVERSE
from anima.protocol.envelope import Event

_WS = re.compile(r"\s+")
_MOVE_WORDS = {**{d: d for d in DIRS}, **ABBR}


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


@dataclass
class Step:
    frm: int | None
    dir: str
    to: int | None


@dataclass
class Locator:
    graph: Graph
    vnum: int | None = None
    certain: bool = False
    trail: list[Step] = field(default_factory=list)       # for walking back; reverse moves cancel
    seen: dict[tuple[int, str], int] = field(default_factory=dict)
    max_trail: int = 60
    _pending: str | None = None
    _leader: str | None = None
    _leader_dir: str | None = None
    _index: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for v, r in self.graph.world.rooms.items():
            self._index.setdefault((r.name, _norm(r.desc)), []).append(v)

    # ------------------------------------------------------------ events
    def on_event(self, ev: Event) -> None:
        t, d = ev.type, ev.data
        if t == "command.sent":
            word = d.get("text", "").strip().lower()
            if word in _MOVE_WORDS:
                self._pending = _MOVE_WORDS[word]
        elif t == "group.change" and d.get("event") == "following" and d.get("who"):
            self._leader = d.get("who")
        elif t == "occupant.left" and self._leader and d.get("who") == self._leader:
            self._leader_dir = d.get("dir")
        elif t == "follow.moved":
            self._pending = self._leader_dir if d.get("leader") == self._leader else None
        elif t == "room":
            self._arrive(d)
        elif t == "room.dark":
            self._arrive_dark()
        elif t == "move.failed":
            if self._pending and self.vnum is not None and d.get("reason") in ("no_exit", "need_boat", "forbidden", "guarded", "closed", "locked"):
                self.seen[(self.vnum, self._pending)] = self.seen.get((self.vnum, self._pending), 0) + 1000
            self._pending = None
        elif t == "self.fled":
            self._pending = None
        elif t in ("self.died", "connection.closed"):
            self.vnum, self.certain, self._pending = None, False, None
            self.trail.clear()

    # ------------------------------------------------------------ arrival
    def _arrive(self, d: dict) -> None:
        prev, pending = self.vnum, self._pending
        self._pending = None
        if isinstance(d.get("id"), int) and d["id"] in self.graph.world.rooms:
            self._set(prev, pending, d["id"], True)
            return
        name, desc = d.get("name", ""), _norm(d.get("desc", ""))
        expected = self._expected(prev, pending)
        if expected is not None:
            r = self.graph.world.rooms[expected]
            if r.name == name:
                self._set(prev, pending, expected, True)
                return
        candidates = self._index.get((name, desc)) or self.graph.rooms_named(name)
        if len(candidates) == 1:
            self._set(prev, pending, candidates[0], True)
            return
        if prev is not None and candidates:
            neighbours = {e.to for e in self.graph.world.rooms[prev].exits.values()}
            near = [v for v in candidates if v in neighbours]
            if len(near) == 1:
                self._set(prev, pending, near[0], True)
                return
        if candidates:
            # still ambiguous: closest vnum to where we were (rooms of one area are numbered together)
            pick = min(candidates, key=lambda v: abs(v - prev)) if prev is not None else candidates[0]
            self._set(prev, pending, pick, False)
            return
        self.vnum, self.certain = None, False

    def _arrive_dark(self) -> None:
        prev, pending = self.vnum, self._pending
        self._pending = None
        expected = self._expected(prev, pending)
        if expected is not None:
            self._set(prev, pending, expected, True)
        else:
            self.vnum, self.certain = None, False

    def _expected(self, prev: int | None, pending: str | None) -> int | None:
        if prev is None or pending is None:
            return None
        ex = self.graph.world.rooms[prev].exits.get(pending)
        return ex.to if ex and ex.to in self.graph.world.rooms else None

    def _set(self, prev: int | None, pending: str | None, vnum: int, certain: bool) -> None:
        if prev is not None and pending is not None:
            self.seen[(prev, pending)] = self.seen.get((prev, pending), 0) + 1
            if self.trail and self.trail[-1].dir == REVERSE.get(pending) and self.trail[-1].frm == vnum:
                self.trail.pop()
            else:
                self.trail.append(Step(prev, pending, vnum))
                del self.trail[:-self.max_trail]
        elif prev != vnum:
            self.trail.clear()           # teleported, fled, respawned: the way back is unknown
        self.vnum, self.certain = vnum, certain

    # ------------------------------------------------------------ queries
    def way_back(self) -> str | None:
        """Direction that undoes the last step of the trail."""
        return REVERSE.get(self.trail[-1].dir) if self.trail else None
