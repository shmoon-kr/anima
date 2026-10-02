"""Commands from the runtime to the adapter (PROTOCOL.md §4–§6).

Every command carries a `source` (D11): which layer sent it and why. A command without a valid
source is refused. Secrets (passwords) never leave this module in readable form (§5).
"""
from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from anima.bus import Bus
from anima.protocol.envelope import Event, Stamper

SOURCE_KINDS = ("reflex", "behavior", "task", "party", "human", "system")
SECRET_MASK = "***"


@dataclass(frozen=True)
class Source:
    kind: str
    id: str
    reason: str = ""
    task_step: dict[str, Any] | None = None
    trigger_seq: int | None = None
    scores: list[dict[str, Any]] | None = None

    def validate(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise InvalidSource(f"unknown source kind {self.kind!r}")
        if not self.id:
            raise InvalidSource("source id is empty")

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


class InvalidSource(ValueError):
    pass


Writer = Callable[[str], Awaitable[None]]


@dataclass
class _Pending:
    text: str
    source: Source
    secret: bool
    queued_at: float


@dataclass
class CommandQueue:
    """Per-agent outgoing command queue.

    - refuses commands without a valid Source
    - sends at most one command per `interval` seconds (servers drop floods)
    - publishes `command.sent` for every command, with the text masked when secret
    - treats the command right after a password prompt as secret even if not marked (§5)
    """
    agent: str
    bus: Bus
    stamper: Stamper
    writer: Writer
    interval: float = 0.25
    clock: Callable[[], float] = time.monotonic
    _queue: asyncio.PriorityQueue = field(default_factory=asyncio.PriorityQueue)
    _counter: itertools.count = field(default_factory=itertools.count)
    _password_pending: bool = False
    _unsub: Callable[[], None] | None = None

    def __post_init__(self) -> None:
        self._unsub = self.bus.subscribe(self._watch, agents=[self.agent], types=["connection."])

    def _watch(self, ev: Event) -> None:
        if ev.type == "connection.login_prompt":
            self._password_pending = ev.data.get("stage") == "password"

    def send(self, text: str, source: Source | None, secret: bool = False, priority: int = 1) -> None:
        """priority 0 jumps ahead of 1 (reflexes: flee before anything queued)."""
        if source is None:
            raise InvalidSource("command without source")
        source.validate()
        if "\n" in text or "\r" in text:
            raise ValueError("one command per send")
        if self._password_pending:
            secret = True
            self._password_pending = False
        self._queue.put_nowait((priority, next(self._counter), _Pending(text, source, secret, self.clock())))

    def pending(self) -> int:
        return self._queue.qsize()

    async def run(self) -> None:
        """Drain the queue forever (cancel to stop)."""
        last = -1e9
        while True:
            _, _, item = await self._queue.get()
            wait = last + self.interval - self.clock()
            if wait > 0:
                await asyncio.sleep(wait)
            await self.writer(item.text)
            last = self.clock()
            self.bus.publish(self.stamper.stamp("command.sent", {
                "text": SECRET_MASK if item.secret else item.text,
                "source": item.source.to_dict(),
                "secret": item.secret,
                "queued_ms": int((last - item.queued_at) * 1000),
            }))

    def close(self) -> None:
        if self._unsub:
            self._unsub()
