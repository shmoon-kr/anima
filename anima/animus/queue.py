"""Animus: the LLM as a slow sense (D16, PROTOCOL.md §9).

Layers submit questions and keep going. Answers come back as `animus.response` events; if none
arrives in time an `animus.timeout` says the default stands. Nothing here ever blocks a caller.

- one worker per tier (a local GPU answers one request at a time; queue by priority, then age)
- stale requests (past their timeout) are dropped before reaching the provider
- `claude` has an hourly budget; requests over it are rejected at once
- every request and answer is published, so recordings can replay answers without an LLM
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from anima.bus import Bus
from anima.protocol.envelope import Event, Stamper

PRIORITY_ORDER = {"danger": 0, "stuck": 1, "routine": 2}


@dataclass
class Request:
    id: str
    agent: str
    asker: str
    tier: str
    priority: str
    question_kind: str
    question: str
    context: dict[str, Any]
    answer_schema: dict[str, Any]
    timeout_s: float
    default: Any
    created: float = 0.0

    def to_data(self) -> dict[str, Any]:
        return {"id": self.id, "asker": self.asker, "tier": self.tier, "priority": self.priority,
                "question_kind": self.question_kind, "question": self.question, "context": self.context,
                "answer_schema": self.answer_schema, "timeout_s": self.timeout_s, "default": self.default}


@dataclass
class Reply:
    answer: Any
    provider: str
    model: str = ""
    prompt: str = ""          # what was actually sent (recorded for replay and for the spectator)
    raw: str = ""


class Provider(Protocol):
    name: str

    async def answer(self, req: Request) -> Reply: ...


def check_schema(value: Any, schema: dict[str, Any]) -> bool:
    t = schema.get("type")
    if t == "boolean":
        return isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "enum":
        return value in schema.get("values", [])
    if t == "list":
        return isinstance(value, list) and len(value) <= schema.get("max_len", 100)
    if t == "object":                 # shape only; the consumer (e.g. the overlay) validates the content
        return isinstance(value, dict) and all(k in value for k in schema.get("required", []))
    return False


@dataclass
class AnimusQueue:
    bus: Bus
    providers: dict[str, Provider]
    stampers: Callable[[str], Stamper]                 # agent -> its stamper (shared seq space)
    budget_per_hour: dict[str, int] = field(default_factory=lambda: {"claude": 20})
    clock: Callable[[], float] = time.monotonic
    _heaps: dict[str, list] = field(default_factory=dict)
    _ready: dict[str, asyncio.Event] = field(default_factory=dict)
    _spent: dict[str, list[float]] = field(default_factory=dict)
    _open: dict[str, Request] = field(default_factory=dict)
    _counter: itertools.count = field(default_factory=itertools.count)

    def _publish(self, agent: str, type_: str, data: dict[str, Any]) -> Event:
        ev = self.stampers(agent).stamp(type_, data)
        self.bus.publish(ev)
        return ev

    def submit(self, req: Request) -> None:
        req.created = self.clock()
        self._publish(req.agent, "animus.request", req.to_data())
        if req.tier not in self.providers:
            self._finish(req, "animus.rejected", {"id": req.id, "reason": "provider_error",
                                                  "detail": f"no provider for tier {req.tier}"})
            return
        if not self._within_budget(req.tier):
            self._finish(req, "animus.rejected", {"id": req.id, "reason": "budget"})
            return
        self._open[req.id] = req
        heapq.heappush(self._heaps.setdefault(req.tier, []),
                       (PRIORITY_ORDER.get(req.priority, 2), req.created, next(self._counter), req))
        self._ready.setdefault(req.tier, asyncio.Event()).set()
        try:
            asyncio.get_running_loop().call_later(req.timeout_s, self._expire, req.id)
        except RuntimeError:
            pass        # no loop (sync tests): expire_due() does the same

    def _within_budget(self, tier: str) -> bool:
        limit = self.budget_per_hour.get(tier)
        if limit is None:
            return True
        now = self.clock()
        spent = [t for t in self._spent.get(tier, []) if now - t < 3600]
        self._spent[tier] = spent
        if len(spent) >= limit:
            return False
        spent.append(now)
        return True

    def _expire(self, req_id: str) -> None:
        req = self._open.get(req_id)
        if req is not None:
            self._finish(req, "animus.timeout", {"id": req.id, "used_default": True})

    def expire_due(self) -> None:
        now = self.clock()
        for req in list(self._open.values()):
            if now - req.created >= req.timeout_s:
                self._expire(req.id)

    def _finish(self, req: Request, type_: str, data: dict[str, Any]) -> None:
        self._open.pop(req.id, None)
        self._publish(req.agent, type_, data)

    def pending(self, tier: str | None = None) -> int:
        return sum(len(h) for t, h in self._heaps.items() if tier is None or t == tier)

    async def run(self) -> None:
        await asyncio.gather(*(self._worker(t) for t in self.providers))

    async def _worker(self, tier: str) -> None:
        ready = self._ready.setdefault(tier, asyncio.Event())
        heap = self._heaps.setdefault(tier, [])
        while True:
            if not heap:
                ready.clear()
                await ready.wait()
                continue
            *_, req = heapq.heappop(heap)
            if req.id not in self._open:              # timed out while waiting in line
                continue
            remaining = req.timeout_s - (self.clock() - req.created)
            if remaining <= 0:
                self._expire(req.id)
                continue
            await self.answer_one(req, remaining)

    async def answer_one(self, req: Request, remaining: float) -> None:
        provider = self.providers[req.tier]
        started = self.clock()
        try:
            reply = await asyncio.wait_for(provider.answer(req), remaining)
        except asyncio.TimeoutError:
            self._expire(req.id)
            return
        except Exception as e:                       # provider failure: default stands
            if req.id in self._open:
                self._finish(req, "animus.rejected", {"id": req.id, "reason": "provider_error", "detail": str(e)})
            return
        if req.id not in self._open:
            return
        if not check_schema(reply.answer, req.answer_schema):
            self._finish(req, "animus.rejected", {"id": req.id, "reason": "schema", "detail": repr(reply.answer)[:200]})
            return
        self._finish(req, "animus.response", {
            "id": req.id, "answer": reply.answer, "latency_s": round(self.clock() - started, 3),
            "provider": reply.provider, "model": reply.model, "prompt": reply.prompt, "raw": reply.raw})


# ---------------------------------------------------------------- providers

@dataclass
class FakeProvider:
    """Answers from a function or a {question_kind: answer} table, after a simulated delay."""
    answers: Callable[[Request], Any] | dict[str, Any]
    delay_s: float = 0.0
    name: str = "fake"

    async def answer(self, req: Request) -> Reply:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        a = self.answers(req) if callable(self.answers) else self.answers[req.question_kind]
        return Reply(answer=a, provider=self.name, model="fake", prompt=req.question, raw=repr(a))


@dataclass
class ReplayProvider:
    """Gives back recorded answers in order, per (agent, asker). Built from a recording's events."""
    recorded: dict[tuple[str, str], list[Any]]
    name: str = "replay"

    @classmethod
    def from_events(cls, events: list[Event]) -> "ReplayProvider":
        askers: dict[str, tuple[str, str]] = {}
        rec: dict[tuple[str, str], list[Any]] = {}
        for ev in events:
            if ev.type == "animus.request":
                askers[ev.data["id"]] = (ev.agent, ev.data["asker"])
            elif ev.type == "animus.response" and ev.data["id"] in askers:
                rec.setdefault(askers[ev.data["id"]], []).append(ev.data["answer"])
        return cls(rec)

    async def answer(self, req: Request) -> Reply:
        q = self.recorded.get((req.agent, req.asker))
        if not q:
            raise LookupError(f"no recorded answer for {req.agent}/{req.asker}")
        return Reply(answer=q.pop(0), provider=self.name, model="replay")
