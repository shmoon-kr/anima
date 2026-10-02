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

    @classmethod
    def from_tbamud(cls, world_dir: Path, hazards_path: Path | None = None) -> "Memoria":
        from anima.memoria.importers.tbamud import load_world
        return cls(load_world(world_dir), Hazards.from_yaml(hazards_path) if hazards_path else None)

    def locator(self, agent: str) -> Locator:
        if agent not in self.locators:
            self.locators[agent] = Locator(self.graph)
        return self.locators[agent]

    def attach(self, bus: Bus) -> None:
        bus.subscribe(self.on_event)

    def conditions(self, has_light: bool = False, zone: int | None = None) -> Conditions:
        return Conditions(has_light=has_light, night=self.night, zone=zone,
                          avoid_zones=frozenset(self.above_level_zones))

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
        elif t == "zone.above_level" and loc.vnum is not None:
            self.above_level_zones.add(self.world.rooms[loc.vnum].zone)
        elif t == "move.failed":
            reason = ev.data.get("reason")
            if before is not None and pending:
                if reason == "locked":
                    self.graph.block_exit(before, pending, LOCKED_RETRY_S)
                elif reason == "closed":
                    self.graph.block_exit(before, pending, CLOSED_RETRY_S)     # time for an `open` to work
                    self._closed[ev.agent] = (before, pending, self.graph.clock())
                elif reason in ("need_boat", "forbidden", "guarded"):
                    self.graph.block_exit(before, pending)
            elif reason == "locked" and ev.agent in self._closed:
                # "It seems to be locked." answers the `open` that followed "The gate seems to be closed."
                vnum, d, at = self._closed.pop(ev.agent)
                if self.graph.clock() - at < 10:
                    self.graph.block_exit(vnum, d, LOCKED_RETRY_S)
