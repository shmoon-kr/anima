"""Room graph queries: path finding and exploration scores.

Static facts come from the world files; dynamic facts (locked doors, blocked exits, rooms marked
to avoid) are added at run time with optional expiry.
"""
from __future__ import annotations

import heapq
import time

from anima import timescale
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable

import yaml

from anima.memoria.model import REVERSE, Room, World


@dataclass
class Hazards:
    trap_exits: set[tuple[int, str]] = field(default_factory=set)
    bad_exit_names: set[tuple[str, str]] = field(default_factory=set)
    deadly_names: set[str] = field(default_factory=set)

    @classmethod
    def from_yaml(cls, path: Path) -> "Hazards":
        d = yaml.safe_load(path.read_text()) or {}
        return cls(trap_exits={(e["room"], e["dir"]) for e in d.get("trap_exits", [])},
                   bad_exit_names={(e["room_name"], e["dir"]) for e in d.get("bad_exits", [])},
                   deadly_names=set(d.get("deadly_rooms", [])))


@dataclass
class Conditions:
    """What the traveller can do right now."""
    has_light: bool = False
    night: bool = False
    zone: int | None = None             # stay inside this zone (None: anywhere)
    avoid_zones: frozenset[int] = frozenset()
    blocked: frozenset[tuple[int, str]] = frozenset()   # this traveller's own blocked exits (a guard who stops their class)


class Graph:
    def __init__(self, world: World, hazards: Hazards | None = None,
                 clock: Callable[[], float] = timescale.now) -> None:
        self.world = world
        self.hazards = hazards or Hazards()
        self.clock = clock
        self._avoid_rooms: dict[int, float] = {}            # vnum -> expiry (inf = permanent)
        self._blocked_exits: dict[tuple[int, str], float] = {}
        self._by_name: dict[str, list[int]] = {}
        for v, r in world.rooms.items():
            self._by_name.setdefault(r.name, []).append(v)
        self.traps = {v for v, r in world.rooms.items() if self._is_trap(r)}

    # ------------------------------------------------------------ static
    def usable_exits(self, room: Room) -> dict[str, int]:
        out = {}
        for d, ex in room.exits.items():
            to = self.world.rooms.get(ex.to)
            if to is None or to.impassable:
                continue
            if (room.vnum, d) in self.hazards.trap_exits or (room.name, d) in self.hazards.bad_exit_names:
                continue
            out[d] = ex.to
        return out

    def step_cost(self, a: int, b: int) -> int:
        """Movement points for one step from a to b (tbaMUD act.movement.c: the mean of both terrains)."""
        ra, rb = self.world.rooms[a], self.world.rooms[b]
        return max(1, (ra.move_cost + rb.move_cost) // 2)

    def _is_trap(self, room: Room) -> bool:
        return room.deadly or room.name in self.hazards.deadly_names or not self.usable_exits(room)

    def rooms_named(self, name: str) -> list[int]:
        return self._by_name.get(name, [])

    def is_dark(self, vnum: int, night: bool) -> bool:
        """utils.c room_is_dark without the room's own light sources."""
        r = self.world.rooms[vnum]
        return r.dark or (r.outdoors and night)

    # ------------------------------------------------------------ dynamic
    def avoid_room(self, vnum: int, seconds: float | None = None) -> None:
        self._avoid_rooms[vnum] = float("inf") if seconds is None else self.clock() + seconds

    def block_exit(self, vnum: int, d: str, seconds: float | None = None) -> None:
        self._blocked_exits[(vnum, d)] = float("inf") if seconds is None else self.clock() + seconds

    def _active(self, table: dict, key) -> bool:
        exp = table.get(key)
        if exp is None:
            return False
        if exp < self.clock():
            del table[key]
            return False
        return True

    def enterable(self, vnum: int, cond: Conditions) -> bool:
        if vnum in self.traps or self._active(self._avoid_rooms, vnum):
            return False
        room_zone = self.world.rooms[vnum].zone
        if (cond.zone is not None and room_zone != cond.zone) or room_zone in cond.avoid_zones:
            return False
        if not cond.has_light and self.is_dark(vnum, cond.night):
            return False
        return True

    def exits_from(self, vnum: int, cond: Conditions) -> dict[str, int]:
        room = self.world.rooms[vnum]
        return {d: to for d, to in self.usable_exits(room).items()
                if not self._active(self._blocked_exits, (vnum, d)) and (vnum, d) not in cond.blocked
                and self.enterable(to, cond)}

    # ------------------------------------------------------------ queries
    def path(self, start: int, goal: int | Callable[[int], bool], cond: Conditions | None = None,
             max_rooms: int = 20000) -> list[str] | None:
        """Shortest list of directions from start to goal (a vnum or a predicate). None if unreachable."""
        cond = cond or Conditions()
        # An avoided zone (above our level) is not walked through, but one can walk out of the one
        # we stand in, and into the one the goal is in: a leader who strayed into it was otherwise
        # beyond every member's reach, and waited there for them (round 26, all half an hour)
        mine = {self.world.rooms[v].zone for v in (start, None if callable(goal) else goal) if v in self.world.rooms}
        if cond.avoid_zones & mine:
            cond = replace(cond, avoid_zones=cond.avoid_zones - mine)
        is_goal = goal if callable(goal) else (lambda v, g=goal: v == g)
        if is_goal(start):
            return []
        prev: dict[int, tuple[int, str]] = {start: (start, "")}
        frontier = [start]
        seen = 0
        while frontier and seen < max_rooms:
            nxt = []
            for v in frontier:
                for d, to in self.exits_from(v, cond).items():
                    if to in prev:
                        continue
                    prev[to] = (v, d)
                    if is_goal(to):
                        return self._unwind(prev, start, to)
                    nxt.append(to)
            seen += len(frontier)
            frontier = nxt
        return None

    def path_to_name(self, start: int, name: str, cond: Conditions | None = None) -> list[str] | None:
        targets = set(self.rooms_named(name))
        return self.path(start, lambda v: v in targets, cond) if targets else None

    @staticmethod
    def _unwind(prev: dict[int, tuple[int, str]], start: int, end: int) -> list[str]:
        dirs = []
        v = end
        while v != start:
            v, d = prev[v]
            dirs.append(d)
        return dirs[::-1]

    def explore_scores(self, vnum: int, cond: Conditions, seen: dict[tuple[int, str], int],
                       came_from: str | None = None, home_zone: int | None = None) -> dict[str, float]:
        """Lower is better. Unvisited exits first; going back and leaving the home zone cost extra.
        Unenterable exits are left out."""
        scores = {}
        for d, to in self.exits_from(vnum, cond).items():
            s = min(seen.get((vnum, d), 0), 50) * 100.0
            if came_from and d == REVERSE.get(came_from):
                s += 150
            if home_zone is not None and self.world.rooms[to].zone != home_zone:
                s += 5000
            scores[d] = s
        return scores

    def zones_containing(self, vnums: Iterable[int]) -> set[int]:
        return {self.world.rooms[v].zone for v in vnums if v in self.world.rooms}
