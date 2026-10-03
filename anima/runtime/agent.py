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
    ask_facts: Callable[[str, list[str]], dict] | None = None      # (agent, facts) -> engine context for asks
    on_patch: Callable[[str, Any, str], None] | None = None         # (agent, answer, request id): `answer: patch`
    _ask_prev: dict[str, bool] = field(default_factory=dict)
    _ask_last: dict[str, float] = field(default_factory=dict)
    human_until: float = -1e9          # a person typed for this character: behavior selection waits
    taken: bool = False                # a person holds the wheel (#take) until #release
    human_goal: str | None = None      # #go: a room the person wants to walk to
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
        from anima.runtime.loops import LoopGuard
        self.loops = LoopGuard(self.clock, self._publish)
        self.ctx.loops = self.loops

    def _publish(self, type_: str, data: dict[str, Any]) -> None:
        self.bus.publish(self.stamper.stamp(type_, data))

    def attach(self) -> None:
        self.bus.subscribe(self.on_event, agents=[self.agent])

    # ------------------------------------------------------------ events
    def on_event(self, ev: Event) -> None:
        now = self.clock()
        self.loops.observe(ev)
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
            item = self.program.asks.get(name)
            if item is not None and item.spec.get("answer") == "patch" and self.on_patch is not None:
                self.on_patch(self.agent, ev.data.get("answer"), ev.data["id"])
        else:
            self.ctx.answers.pop(name, None)           # default stands

    # ------------------------------------------------------------ ticking
    def held(self) -> bool:
        return self.taken or self.clock() < self.human_until

    def hold(self, seconds: float) -> None:
        self.human_until = max(self.human_until, self.clock() + seconds)
        self.tasks.abandon("a person took over")

    def tick(self) -> None:
        if not self.state.in_game:
            return
        if self.human_goal is not None:
            self._human_step()
        if self.held():                  # reflexes still answer events; choosing and tasks wait for the person
            return
        pol = self.program.policies
        if pol.get("sell_at") is not None:          # D32: which shops are worth a trip (cached a minute)
            self.state.shop_wants = self.ctx.items.shop_wants(pol.get("inv_keep") or [], pol.get("gold_reserve") or 0,
                                                              pol["sell_at"])
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
            s = item.spec
            rising = cond and not self._ask_prev.get(name, False)
            self._ask_prev[name] = cond
            if "every_s" in s:                  # periodic: while `when` holds, at most every every_s
                now = self.clock()
                due = cond and now - self._ask_last.get(name, now - s["every_s"]) >= s["every_s"]
                if name not in self._ask_last:
                    self._ask_last[name] = now   # the first one comes a full period after entering
                    due = False
            else:
                due = rising
            if not due or name in self._ask_open.values():
                continue
            self._ask_last[name] = self.clock()
            ctx_vals = {k[len("context."):]: self.ctx.eval(e) for k, e in item.exprs.items() if k.startswith("context.")}
            if s.get("facts") and self.ask_facts is not None:
                ctx_vals["facts"] = self.ask_facts(self.agent, list(s["facts"]))
            schema = s["schema"]
            if s.get("answer") == "patch":
                from anima.animus.overlay import PATCH_SCHEMA
                schema = {**PATCH_SCHEMA, "shape": PATCH_SCHEMA["shape"].replace('"layer": "party" | "<agent name>", ', "")}
            rid = f"{self.agent}:{name}:{next(_req_ids)}"
            self._ask_open[rid] = name
            self.animus.submit(Request(id=rid, agent=self.agent, asker=item.id, tier=s["tier"],
                                       priority=s.get("priority", "routine"), question_kind=name,
                                       question=s["question"], context=ctx_vals, answer_schema=schema,
                                       timeout_s=float(s["timeout_s"]), default=s["default"]))

    def _human_step(self) -> None:
        """One step toward the person's #go room (Memoria paths), as a human-sourced command."""
        import json
        from anima.sigil.expr import parse
        goal = self.human_goal
        v = self.memoria.locator(self.agent).vnum
        room = self.memoria.world.rooms.get(v) if v is not None else None
        if room is not None and (room.name == goal or str(v) == goal):
            self.human_goal = None
            return
        self.ctx.run_actions([parse(f"go_to({json.dumps(goal)})")],
                             Source("human", "play/go", f"walking to {goal}"), priority=0)

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
