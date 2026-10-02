"""Zone ladder: hunt where the party's level fits, learn which zones are too dangerous, try again later.

From the world data (zone resets say where each mob lives): the median mob level of a zone, its
strongest aggressive mob, and the room of the zone nearest to us on foot (its entrance).
A zone fits when its median mob level is a little below the party's average level (or the zone's own
level range covers us), and no aggressive mob is far above the weakest member.

From play: deaths, flees and very low hit points in a zone mark it too dangerous until the weakest
member has gained `retry_levels` levels. Kills per hour are kept so a quiet zone can be told apart.
Zones are ranked by expected growth, not kills: a kill gives the victim's exp / 3 shared by the group
(fight.c:363), and progress is that exp over the span of the level it is gained at (class.c level_exp).
Before we have hunted a zone the prior is its median mob exp; after ten minutes there the measured
growth (deaths subtracted: a death costs half of all experience, fight.c:323) takes over.
Terrain (D37): a step costs movement by sector (constants.c movement_loss: inside/city 1, field 2,
forest 3, hills 4, mountain 6; a step is the mean of both rooms). Hunting walks ~STEPS_PER_HOUR steps;
standing regenerates ~REGEN_STAND, sleeping ~REGEN_SLEEP per hour (limits.c move_gain: 20 per game
hour of 75 s at our age, x1.5 asleep). Where the walking drains more than standing regenerates, part
of every hour goes to camping: the prior growth is scaled by that hunting fraction, and distance is
measured in movement points rather than steps.
The ladder is saved with the party (run/party.json) so it survives restarts.
"""
from __future__ import annotations

import heapq
import statistics
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable

from anima.memoria import Memoria
from anima.memoria.graph import Conditions


STEPS_PER_HOUR = 350.0       # the leader's moves per hour while hunting (phase-1 control runs: 330-370)
REGEN_STAND = 960.0          # 20 per 75 s
REGEN_SLEEP = 1440.0         # x1.5 asleep


def hunting_fraction(step_cost: float) -> float:
    """Share of an hour spent hunting rather than camping to get the movement back."""
    deficit = STEPS_PER_HOUR * step_cost - REGEN_STAND
    return 1.0 if deficit <= 0 else 1.0 / (1.0 + deficit / REGEN_SLEEP)


@dataclass
class ZoneFit:
    zone: int
    name: str
    entrance: str                   # room name, usable as a rally point
    steps: int
    median_level: float
    aggressive_max: int
    mobs: int
    expected_growth: float | None = None     # levels per member per hour (prior, or measured once hunted)
    measured: bool = False
    step_cost: float = 1.0                   # mean movement per step inside the zone
    path_mv: int = 0                         # movement points to walk there


@dataclass
class ZoneRisk:
    deaths: list[float] = field(default_factory=list)
    flees: list[float] = field(default_factory=list)
    lowest_hp_pct: float = 100.0
    kills: int = 0
    seconds: float = 0.0
    gained: float = 0.0                       # net levels per member gained here (deaths subtracted)
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
    kills_per_hour: float = 25.0                  # prior: phase-1 baseline and control runs (27-28/h)
    measured_after_s: float = 600.0
    risk: dict[int, ZoneRisk] = field(default_factory=dict)
    _stats: dict[int, tuple[float, int, int]] | None = None      # zone -> (median, aggressive max, mobs)

    # ------------------------------------------------------------ world data
    _terrain: dict[int, float] | None = None

    def _step_costs(self) -> dict[int, float]:
        """zone -> mean movement per step over the zone's own connections."""
        if self._terrain is None:
            g, rooms = self.memoria.graph, self.memoria.world.rooms
            acc: dict[int, list[int]] = {}
            for v, r in rooms.items():
                for to in g.usable_exits(r).values():
                    if rooms[to].zone == r.zone:
                        acc.setdefault(r.zone, []).append(g.step_cost(v, to))
            self._terrain = {z: statistics.mean(c) for z, c in acc.items()}
        return self._terrain

    def _zone_stats(self) -> dict[int, tuple[float, int, int]]:
        if self._stats is None:
            w = self.memoria.world
            by_zone: dict[int, list[tuple[int, bool]]] = {}
            for mob in w.mobs.values():
                for z in {w.rooms[h].zone for h in mob.homes if h in w.rooms}:
                    by_zone.setdefault(z, []).append((mob.level, "aggressive" in mob.flags))
            exp: dict[int, list[int]] = {}
            for mob in w.mobs.values():
                for z in {w.rooms[h].zone for h in mob.homes if h in w.rooms}:
                    exp.setdefault(z, []).append(mob.exp)
            self._stats = {z: (statistics.median(l for l, _ in ms), max((l for l, a in ms if a), default=0), len(ms),
                               statistics.median(exp[z]))
                           for z, ms in by_zone.items()}
        return self._stats

    def growth(self, zone: int, median_exp: float, span: float | None, party_size: int) -> tuple[float | None, bool]:
        """Expected levels per member per hour here: measured once hunted long enough, else from mob exp
        scaled by the share of the hour the terrain leaves for hunting."""
        frac = hunting_fraction(self._step_costs().get(zone, 1.0))
        prior = (median_exp / 3 / max(1, party_size) / span * self.kills_per_hour * frac) if span else None
        r = self.risk.get(zone)
        if r is None or r.seconds < self.measured_after_s:
            return prior, False
        measured = r.gained / (r.seconds / 3600)
        w = min(1.0, r.seconds / (3 * self.measured_after_s))
        return (measured if prior is None else w * measured + (1 - w) * prior), True

    def _entrances(self, start: int, cond: Conditions) -> dict[int, tuple[int, int, int]]:
        """zone -> (nearest room by movement, steps, movement points), one cheapest-path walk from start."""
        g, rooms = self.memoria.graph, self.memoria.world.rooms
        cost = {start: (0, 0)}
        heap = [(0, 0, start)]
        best: dict[int, tuple[int, int, int]] = {}
        while heap:
            mv, steps, v = heapq.heappop(heap)
            if cost.get(v, (1 << 30,))[0] < mv:
                continue
            z = rooms[v].zone
            if z not in best:
                best[z] = (v, steps, mv)
            for to in g.exits_from(v, cond).values():
                nmv = mv + g.step_cost(v, to)
                if nmv < cost.get(to, (1 << 30,))[0]:
                    cost[to] = (nmv, steps + 1)
                    heapq.heappush(heap, (nmv, steps + 1, to))
        return best

    def candidates(self, levels: Iterable[int], has_light: bool = True, span: float | None = None) -> list[ZoneFit]:
        levels = [lv for lv in levels if lv]
        start = self.hub()
        if not levels or start is None:
            return []
        weakest, avg = min(levels), sum(levels) / len(levels)
        cond = self.memoria.conditions(has_light=has_light)
        ents = self._entrances(start, cond)
        out = []
        for z, (median, aggr, n, median_exp) in self._zone_stats().items():
            if z not in ents or n < 3:
                continue
            zone = self.memoria.world.zones.get(z)
            meant_for_us = (zone is not None and 0 < zone.min_level <= weakest and avg <= zone.max_level
                            and avg - self.below - 1 <= median <= avg + self.above + 1)   # the builder's range covers us
            if not (avg - self.below <= median <= avg + self.above or meant_for_us):
                continue
            if aggr > weakest + self.aggressive_margin:
                continue
            r = self.risk.get(z)
            if r and r.blocked_below_level > weakest:
                continue
            room, steps, mv = ents[z]
            g, measured = self.growth(z, median_exp, span, len(levels))
            out.append(ZoneFit(z, zone.name if zone else str(z), self.memoria.world.rooms[room].name, steps,
                               median, aggr, n, None if g is None else round(g, 4), measured,
                               round(self._step_costs().get(z, 1.0), 2), mv))
        if span:                                   # growth first; walking there costs a little (movement)
            out.sort(key=lambda f: -(f.expected_growth or 0) / (1 + f.path_mv / 300))
        else:
            target = avg - 1
            out.sort(key=lambda f: abs(f.median_level - target) + f.steps / 10)     # a level ~ ten steps
        return out

    def rally_in_zone(self, zone: int, near: int, has_light: bool = True) -> str | None:
        """A rally point inside `zone` close to `near`: the nearest room whose name exists only once in
        the world, so "are we in the rally zone" is never confused by a same-named room elsewhere."""
        g, rooms = self.memoria.graph, self.memoria.world.rooms
        cond = self.memoria.conditions(has_light=has_light, zone=zone)
        dist, q = {near: 0}, deque([near])
        while q:
            v = q.popleft()
            if len(g.rooms_named(rooms[v].name)) == 1:
                return rooms[v].name
            for to in g.exits_from(v, cond).values():
                if to not in dist:
                    dist[to] = dist[v] + 1
                    q.append(to)
        return None

    def start_here(self, levels: Iterable[int], has_light: bool = True, span: float | None = None,
                   size: int = 3) -> list[str]:
        """After a (re)start: the ladder's own ranking measured from where we stand. Our zone costs no
        walking, so we stay unless another one is better even after paying the walk (in movement).
        Staying uses a rally inside our zone near the leader, with a name that exists only once."""
        start = self.hub()
        fits = self.candidates(levels, has_light, span)
        if start is None or not fits:
            return []
        here_zone = self.memoria.world.rooms[start].zone
        first = fits[0].entrance
        if fits[0].zone == here_zone:
            first = self.rally_in_zone(here_zone, start, has_light) or first
        out = [first]
        for f in fits:
            if f.entrance not in out and f.zone != (fits[0].zone if fits[0].zone == here_zone else -1):
                out.append(f.entrance)
            if len(out) == size:
                break
        return out

    def circuit(self, levels: Iterable[int], has_light: bool = True, size: int = 2,
                span: float | None = None) -> list[str]:
        """Rally points for the party: the best fitting zones' entrances."""
        names: list[str] = []
        for f in self.candidates(levels, has_light, span):
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

    def gained(self, zone: int | None, levels: float) -> None:
        """Net progress made here, per member (negative for a death)."""
        if zone is not None:
            self._r(zone).gained += levels

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
