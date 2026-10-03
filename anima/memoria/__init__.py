"""Memoria: the world model (D13 구조의 아래층). Room graph, position per agent, mob knowledge.

Party members' state lives in the party blackboard (M7), not here.
"""
from __future__ import annotations

from pathlib import Path

from anima.bus import Bus
from anima.memoria.graph import Conditions, Graph, Hazards
from anima.memoria.knowledge import Knowledge
from anima.memoria.locator import Locator
from anima.memoria.model import World
from anima.protocol.envelope import Event

LOCKED_RETRY_S = 600      # gates close at night; try again later (SPEC §1 위험)
CLOSED_RETRY_S = 20


class Memoria:
    def __init__(self, world: World, hazards: Hazards | None = None) -> None:
        self.world = world
        self.graph = Graph(world, hazards)
        self.knowledge = Knowledge(world)
        self.locators: dict[str, Locator] = {}
        self.night = False
        self.above_level_zones: set[int] = set()
        self._closed: dict[str, tuple[int, str, float]] = {}
        self.level_exp: dict[str, dict[int, int]] = {}      # class -> level -> total exp to reach it
        self.blocked: dict[str, set[tuple[int, str]]] = {}   # agent -> exits a guard keeps them from (by class)

    def level_span(self, chclass: str | None, level: int | None) -> int | None:
        """Experience between this level and the next (None if unknown). Progress = exp / span."""
        t = self.level_exp.get(chclass or "")
        if not t or not level or level + 1 not in t or level not in t:
            return None
        return max(1, t[level + 1] - t[level])

    @classmethod
    def from_tbamud(cls, world_dir: Path, hazards_path: Path | None = None) -> "Memoria":
        from anima.memoria.importers.tbamud import load_world
        mem = cls(load_world(world_dir), Hazards.from_yaml(hazards_path) if hazards_path else None)
        table = hazards_path.parent / "level_exp.yaml" if hazards_path else None
        if table is not None and table.exists():
            import yaml
            mem.level_exp = {c: {int(k): int(v) for k, v in t.items()}
                             for c, t in (yaml.safe_load(table.read_text()) or {}).items()}
        return mem

    def locator(self, agent: str) -> Locator:
        if agent not in self.locators:
            self.locators[agent] = Locator(self.graph)
        return self.locators[agent]

    def attach(self, bus: Bus) -> None:
        bus.subscribe(self.on_event)

    def door_tried(self, agent: str, vnum: int, d: str) -> None:
        """An `open` sent for the door of this exit before stepping through: a "seems to be locked"
        answer blocks this exit (it had no "closed" step before it to say which)."""
        self._closed[agent] = (vnum, d, self.graph.clock())

    def conditions(self, has_light: bool = False, zone: int | None = None, agent: str | None = None) -> Conditions:
        """What a traveller can use now; with `agent`, also minus the exits only they are kept from."""
        return Conditions(has_light=has_light, night=self.night, zone=zone,
                          avoid_zones=frozenset(self.above_level_zones),
                          blocked=frozenset(self.blocked.get(agent, ())) if agent else frozenset())

    def on_event(self, ev: Event) -> None:
        if not ev.agent:
            return
        loc = self.locator(ev.agent)
        before = loc.vnum
        pending = loc._pending
        loc.on_event(ev)
        t = ev.type
        if t == "world.time":
            self.night = ev.data.get("phase") in ("sunset", "night")
        elif t == "zone.above_level":
            # The warning comes before the move (act.movement.c:218): the zone is the one being
            # entered, not the one we still stand in. Unknown way: mark nothing rather than the wrong
            # zone (marking the town we stood in cut every path through it).
            ex = self.world.rooms[before].exits.get(pending) if before in self.world.rooms and pending else None
            if ex is not None and ex.to in self.world.rooms:
                self.above_level_zones.add(self.world.rooms[ex.to].zone)
        elif t == "move.failed":
            reason = ev.data.get("reason")
            if before is not None and pending:
                if reason == "locked":
                    self.graph.block_exit(before, pending, LOCKED_RETRY_S)
                elif reason == "closed":
                    self.graph.block_exit(before, pending, CLOSED_RETRY_S)     # time for an `open` to work
                    self._closed[ev.agent] = (before, pending, self.graph.clock())
                elif reason in ("need_boat", "forbidden"):
                    self.graph.block_exit(before, pending)
                elif reason == "guarded":
                    # a guild guard stops other classes: that exit is closed to this agent, not to all
                    self.blocked.setdefault(ev.agent, set()).add((before, pending))
            elif reason == "locked" and ev.agent in self._closed:
                # "It seems to be locked." answers the `open` that followed "The gate seems to be closed."
                vnum, d, at = self._closed.pop(ev.agent)
                if self.graph.clock() - at < 10:
                    self.graph.block_exit(vnum, d, LOCKED_RETRY_S)
