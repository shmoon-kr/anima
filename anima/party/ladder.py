"""Zone ladder: hunt where the party's level fits, learn which zones are too dangerous, try again later.

From the world data (zone resets say where each mob lives): the median mob level of a zone, its
strongest aggressive mob, and the room of the zone nearest to us on foot (its entrance).
A zone fits when its median mob level is a little below the party's average level (or the zone's own
level range covers us), and no aggressive mob is far above the weakest member.

From play: deaths, flees and very low hit points in a zone mark it too dangerous until the weakest
member has gained `retry_levels` levels. Kills per hour are kept so a quiet zone can be told apart.
The ladder is saved with the party (run/party.json) so it survives restarts.
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from anima.memoria import Memoria
from anima.memoria.graph import Conditions


@dataclass
class ZoneFit:
    zone: int
    name: str
    entrance: str                   # room name, usable as a rally point
    steps: int
    median_level: float
    aggressive_max: int
    mobs: int


@dataclass
class ZoneRisk:
    deaths: list[float] = field(default_factory=list)
    flees: list[float] = field(default_factory=list)
    lowest_hp_pct: float = 100.0
    kills: int = 0
    seconds: float = 0.0
    blocked_below_level: int = 0    # too dangerous until the weakest member reaches this level
    why: str = ""


@dataclass
class Ladder:
    memoria: Memoria
    hub: Callable[[], int | None]                 # where paths are measured from (the leader's room)
    clock: Callable[[], float]
    below: int = 3                                # fit: median mob level in [avg - below, avg + above]
    above: int = 1
    aggressive_margin: int = 3                    # no aggressive mob above weakest + margin
    retry_levels: int = 2
    death_window_s: float = 3600.0
    flee_limit: int = 3                           # flees in flee_window_s that make a zone too dangerous
    flee_window_s: float = 1800.0
    low_hp_pct: float = 20.0
    risk: dict[int, ZoneRisk] = field(default_factory=dict)
    _stats: dict[int, tuple[float, int, int]] | None = None      # zone -> (median, aggressive max, mobs)

    # ------------------------------------------------------------ world data
    def _zone_stats(self) -> dict[int, tuple[float, int, int]]:
        if self._stats is None:
            w = self.memoria.world
            by_zone: dict[int, list[tuple[int, bool]]] = {}
            for mob in w.mobs.values():
                for z in {w.rooms[h].zone for h in mob.homes if h in w.rooms}:
                    by_zone.setdefault(z, []).append((mob.level, "aggressive" in mob.flags))
            self._stats = {z: (statistics.median(l for l, _ in ms), max((l for l, a in ms if a), default=0), len(ms))
                           for z, ms in by_zone.items()}
        return self._stats

    def _entrances(self, start: int, cond: Conditions) -> dict[int, tuple[int, int]]:
        """zone -> (nearest room, steps) by one breadth-first walk from start."""
        g, rooms = self.memoria.graph, self.memoria.world.rooms
        dist = {start: 0}
        q = deque([start])
        best: dict[int, tuple[int, int]] = {}
        while q:
            v = q.popleft()
            z = rooms[v].zone
            if z not in best:
                best[z] = (v, dist[v])
            for to in g.exits_from(v, cond).values():
                if to not in dist:
                    dist[to] = dist[v] + 1
                    q.append(to)
        return best

    def candidates(self, levels: Iterable[int], has_light: bool = True) -> list[ZoneFit]:
        levels = [lv for lv in levels if lv]
        start = self.hub()
        if not levels or start is None:
            return []
        weakest, avg = min(levels), sum(levels) / len(levels)
        cond = self.memoria.conditions(has_light=has_light)
        ents = self._entrances(start, cond)
        out = []
        for z, (median, aggr, n) in self._zone_stats().items():
            if z not in ents or n < 3:
                continue
            zone = self.memoria.world.zones.get(z)
            meant_for_us = (zone is not None and 0 < zone.min_level <= weakest and avg <= zone.max_level
                            and median <= avg + self.above + 1)     # the builder's level range covers us
            if not (avg - self.below <= median <= avg + self.above or meant_for_us):
                continue
            if aggr > weakest + self.aggressive_margin:
                continue
            r = self.risk.get(z)
            if r and r.blocked_below_level > weakest:
                continue
            room, steps = ents[z]
            out.append(ZoneFit(z, zone.name if zone else str(z), self.memoria.world.rooms[room].name, steps,
                               median, aggr, n))
        target = avg - 1
        out.sort(key=lambda f: abs(f.median_level - target) + f.steps / 10)     # a level ~ ten steps
        return out

    def circuit(self, levels: Iterable[int], has_light: bool = True, size: int = 2) -> list[str]:
        """Rally points for the party: the best fitting zones' entrances."""
        names: list[str] = []
        for f in self.candidates(levels, has_light):
            if f.entrance not in names:
                names.append(f.entrance)
            if len(names) == size:
                break
        return names

    # ------------------------------------------------------------ learning from play
    def _r(self, zone: int) -> ZoneRisk:
        return self.risk.setdefault(zone, ZoneRisk())

    def died(self, zone: int | None, weakest_level: int) -> bool:
        if zone is None:
            return False
        r = self._r(zone)
        r.deaths.append(self.clock())
        return self._judge(zone, weakest_level, "a member died here")

    def fled(self, zone: int | None, weakest_level: int) -> bool:
        if zone is None:
            return False
        r = self._r(zone)
        now = self.clock()
        r.flees = [t for t in r.flees if now - t < self.flee_window_s] + [now]
        return self._judge(zone, weakest_level, f"{len(r.flees)} flees in {self.flee_window_s / 60:.0f} min")

    def hurt(self, zone: int | None, hp_pct: float, weakest_level: int) -> bool:
        if zone is None:
            return False
        r = self._r(zone)
        r.lowest_hp_pct = min(r.lowest_hp_pct, hp_pct)
        return hp_pct < self.low_hp_pct and self._judge(zone, weakest_level, f"a member fell to {hp_pct:.0f}% hp")

    def killed(self, zone: int | None) -> None:
        if zone is not None:
            self._r(zone).kills += 1

    def spent(self, zone: int | None, seconds: float) -> None:
        if zone is not None:
            self._r(zone).seconds += seconds

    def _judge(self, zone: int, weakest_level: int, why: str) -> bool:
        """True if the zone just became too dangerous for us."""
        r = self.risk[zone]
        now = self.clock()
        dead = [t for t in r.deaths if now - t < self.death_window_s]
        if dead or len(r.flees) >= self.flee_limit or "hp" in why:
            if r.blocked_below_level <= weakest_level:
                r.blocked_below_level = weakest_level + self.retry_levels
                r.why = why
                r.flees.clear()
                return True
        return False

    def blocked(self, weakest_level: int) -> dict[int, dict[str, Any]]:
        return {z: {"until_level": r.blocked_below_level, "why": r.why}
                for z, r in self.risk.items() if r.blocked_below_level > weakest_level}

    # ------------------------------------------------------------ saving
    def to_json(self) -> dict[str, Any]:
        return {str(z): asdict(r) for z, r in self.risk.items()}

    def from_json(self, data: dict[str, Any]) -> None:
        self.risk = {int(z): ZoneRisk(**r) for z, r in (data or {}).items()}
