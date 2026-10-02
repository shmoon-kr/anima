"""In-process event bus (docs/PROTOCOL.md §2 envelope).

Every message — game events, command.sent, runtime.*, animus.* — goes through publish().
Subscribers choose agents and type prefixes. Sync callbacks run inline (recorder, Memoria);
async subscribers get an asyncio.Queue (layers that do their own work).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Iterable

from anima.protocol.envelope import Event


@dataclass
class _Sub:
    agents: frozenset[str] | None
    prefixes: tuple[str, ...] | None
    callback: Callable[[Event], None] | None = None
    queue: asyncio.Queue[Event] | None = None

    def wants(self, ev: Event) -> bool:
        if self.agents is not None and ev.agent not in self.agents:
            return False
        if self.prefixes is not None and not ev.type.startswith(self.prefixes):
            return False
        return True


@dataclass
class Bus:
    _subs: list[_Sub] = field(default_factory=list)

    def subscribe(self, callback: Callable[[Event], None], agents: Iterable[str] | None = None,
                  types: Iterable[str] | None = None) -> Callable[[], None]:
        """`types` are prefixes: "combat." matches combat.hit, "room" matches room and room.dark."""
        sub = _Sub(frozenset(agents) if agents is not None else None,
                   tuple(types) if types is not None else None, callback=callback)
        self._subs.append(sub)
        return lambda: self._subs.remove(sub)

    def queue(self, agents: Iterable[str] | None = None, types: Iterable[str] | None = None,
              maxsize: int = 0) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self._subs.append(_Sub(frozenset(agents) if agents is not None else None,
                               tuple(types) if types is not None else None, queue=q))
        return q

    def publish(self, ev: Event) -> None:
        for sub in list(self._subs):
            if not sub.wants(ev):
                continue
            if sub.callback is not None:
                sub.callback(ev)
            elif sub.queue is not None:
                sub.queue.put_nowait(ev)

    def publish_all(self, events: Iterable[Event]) -> None:
        for ev in events:
            self.publish(ev)
