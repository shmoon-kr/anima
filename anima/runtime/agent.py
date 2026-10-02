"""One agent's runtime: state + reflexes + utility selection + tasks + Animus questions.

Events come in through on_event(); tick() runs selection, the running task and Animus questions.
Reflexes and selection never wait for the LLM (D16): questions go out, answers come back as
events and only change what `answer.*` returns.
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from anima.animus.queue import AnimusQueue, Request
from anima.bus import Bus
from anima.memoria import Memoria
from anima.protocol.commands import Source
from anima.protocol.envelope import Event, Stamper
from anima.reflex.engine import PREEMPT_S, ReflexEngine
from anima.runtime.context import Context, PartyView, SendFn, SoloParty
from anima.runtime.state import AgentState
from anima.select.utility import UtilitySelector
from anima.sigil.program import Program

TICK_S = 1.0
# events after which selection runs at once instead of waiting for the next tick
URGENT = ("combat.", "self.", "room", "occupant.arrived", "condition", "position", "animus.response",
          "animus.timeout", "connection.in_game")
_req_ids = itertools.count(1)


@dataclass
class AgentRuntime:
    agent: str
    program: Program
    memoria: Memoria
    bus: Bus
    stamper: Stamper
    send: SendFn
    clock: Callable[[], float]
    party: PartyView = field(default_factory=SoloParty)
    animus: AnimusQueue | None = None
    _ask_prev: dict[str, bool] = field(default_factory=dict)
    _ask_open: dict[str, str] = field(default_factory=dict)          # request id -> ask name
    _dirty: bool = True
    _last_seq: int | None = None

    def __post_init__(self) -> None:
        k = self.memoria.knowledge
        self.state = AgentState(self.agent, item_type=lambda text: (k.item(text) or _Untyped).type,
                                is_occupant=lambda occ, who: any(m.short.lower() == who.lower()
                                                                 for m in k.identify(occ).mobs),
                                is_ally=lambda name: self.party.is_party_member(name.split(" ", 1)[0]))
        self.ctx = Context(self.agent, self.program, self.state, self.memoria, self.send, self.clock, self.party)
        from anima.tasks.sequencer import Sequencer
        self.tasks = Sequencer(self.ctx, self._publish)
        self.selector = UtilitySelector(self.ctx, self._publish, self.tasks.start, lambda: self.tasks.name,
                                        self.tasks.abandon)
        self.tasks.on_finish = self.selector.task_finished
        self.reflexes = ReflexEngine(self.ctx, self._publish, on_fire=lambda: self.tasks.pause(PREEMPT_S))

    def _publish(self, type_: str, data: dict[str, Any]) -> None:
        self.bus.publish(self.stamper.stamp(type_, data))

    def attach(self) -> None:
        self.bus.subscribe(self.on_event, agents=[self.agent])

    # ------------------------------------------------------------ events
    def on_event(self, ev: Event) -> None:
        now = self.clock()
        if ev.type == "command.sent":
            self.state.on_event(ev, now)
            return
        if ev.type.startswith("runtime.") or ev.type == "command.send":
            return                                     # our own records; command.refused is a game event
        self.state.on_event(ev, now)
        if ev.type in ("room", "room.dark", "move.failed"):
            self.ctx.arrived()
        elif ev.type == "toggle.state":
            self.ctx.toggle_pending.pop(ev.data.get("name"), None)
        if ev.type.startswith("animus."):
            self._animus_event(ev)
        self.reflexes.on_event(ev)
        if ev.type.startswith(URGENT):
            self._dirty = True
            self._last_seq = ev.seq or self._last_seq

    def _animus_event(self, ev: Event) -> None:
        name = self._ask_open.get(ev.data.get("id", ""))
        if name is None or ev.type == "animus.request":
            return
        del self._ask_open[ev.data["id"]]
        if ev.type == "animus.response":
            self.ctx.answers[name] = ev.data.get("answer")
        else:
            self.ctx.answers.pop(name, None)           # default stands

    # ------------------------------------------------------------ ticking
    def tick(self) -> None:
        if not self.state.in_game:
            return
        self._asks()
        self.selector.tick(self._last_seq)
        self.tasks.tick()
        self._dirty = False

    def tick_if_dirty(self) -> None:
        if self._dirty:
            self.tick()

    async def run(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(TICK_S)

    def _asks(self) -> None:
        if self.animus is None:
            return
        for name, item in self.program.asks.items():
            when = item.exprs.get("when[0]")
            cond = bool(self.ctx.eval(when)) if when is not None else False
            rising = cond and not self._ask_prev.get(name, False)
            self._ask_prev[name] = cond
            if not rising or name in self._ask_open.values():
                continue
            s = item.spec
            ctx_vals = {k[len("context."):]: self.ctx.eval(e) for k, e in item.exprs.items() if k.startswith("context.")}
            rid = f"{self.agent}:{name}:{next(_req_ids)}"
            self._ask_open[rid] = name
            self.animus.submit(Request(id=rid, agent=self.agent, asker=item.id, tier=s["tier"],
                                       priority=s.get("priority", "routine"), question_kind=name,
                                       question=s["question"], context=ctx_vals, answer_schema=s["schema"],
                                       timeout_s=float(s["timeout_s"]), default=s["default"]))

    # ------------------------------------------------------------ reloading
    def reload(self, build: Callable[[], Program]) -> bool:
        """Swap in a new program. A Sigil that fails validation is refused and the old one stays."""
        from anima.sigil.program import SigilError
        try:
            prog = build()
        except SigilError as e:
            self._publish("runtime.sigil", {"event": "rejected", "package": self.agent, "errors": e.errors})
            return False
        self.program = prog
        self.ctx.program = prog
        self.ctx._fns = None
        self.tasks.abandon("program reloaded")
        self.selector.current = None
        self._publish("runtime.sigil", {"event": "loaded", "package": self.agent, "layers": prog.layers})
        return True

    def apply_values(self, prog: Program) -> None:
        """Hot-apply an already validated program's policies and behavior weights (Animus layers).
        Unlike `reload`, the running task and the current behavior stay: changing a number must not
        turn back a member on the way to the guild."""
        self.program.policies = prog.policies
        self.program.policy_origin = prog.policy_origin
        self.program.layers = prog.layers
        for name, it in prog.behaviors.items():
            cur = self.program.behaviors.get(name)
            if cur is not None and cur.spec.get("weight", 1.0) != it.spec.get("weight", 1.0):
                cur.spec["weight"] = it.spec.get("weight", 1.0)

    # ------------------------------------------------------------ direct control
    def act(self, actions: list[str], reason: str = "") -> None:
        """Run action expressions as the system (session start-up etc.)."""
        from anima.sigil.expr import parse
        self.ctx.run_actions([parse(a) for a in actions], Source("system", "runtime/act", reason))


class _Untyped:
    type = None
