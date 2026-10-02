"""Tasks: multi-step procedures (D13), e.g. walk to the guild, practise, walk back.

A task runs one step at a time on each tick. Reflexes pause it briefly; when it resumes, movement
steps re-plan from wherever the agent now is. A task fails on timeout, on a step's own timeout, or
when a destination cannot be reached.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable

from anima.protocol.commands import Source
from anima.runtime.context import Context
from anima.sigil.program import Item

DEFAULT_TASK_TIMEOUT_S = 900.0
DEFAULT_STEP_TIMEOUT_S = 300.0
_ids = itertools.count(1)


@dataclass
class Running:
    item: Item
    task_id: str
    source: Source
    started: float
    step: int = 0
    step_started: float = 0.0
    step_count: int = 0
    step_last: float = -1e9
    wait_until: float = 0.0
    trail_len: int = 0
    paused_until: float = 0.0


@dataclass
class Sequencer:
    ctx: Context
    publish: Callable[[str, dict], None]
    on_finish: Callable[[str, bool], None] = lambda name, ok: None
    running: Running | None = None

    def __post_init__(self) -> None:
        self.ctx.task_hooks["start"] = lambda name: self.start(name, self.ctx.source or Source("system", "task"))
        self.ctx.task_hooks["wait"] = self._wait

    @property
    def name(self) -> str | None:
        return self.running.item.name if self.running else None

    def _event(self, event: str, **extra: Any) -> None:
        r = self.running
        if r:
            self.publish("runtime.task", {"task_id": r.task_id, "name": r.item.name, "event": event,
                                          "step": r.step, **extra})

    def start(self, name: str, source: Source) -> None:
        item = self.ctx.program.tasks.get(name)
        if item is None:
            return
        if self.running:
            self.abandon(f"replaced by {name}")
        now = self.ctx.clock()
        loc = self.ctx.memoria.locator(self.ctx.agent)
        self.running = Running(item, f"{name}#{next(_ids)}", source, now, step_started=now,
                               trail_len=len(loc.trail))
        self.ctx.task_name = name
        self._event("started")

    def abandon(self, reason: str) -> None:
        if self.running:
            self._event("abandoned", reason=reason)
            self._end()

    def pause(self, seconds: float) -> None:
        if self.running:
            self.running.paused_until = self.ctx.clock() + seconds
            self._event("paused")

    def _end(self) -> None:
        self.running = None
        self.ctx.task_name = None

    def _finish(self, ok: bool, reason: str = "") -> None:
        r = self.running
        if not r:
            return
        self._event("succeeded" if ok else "failed", **({"reason": reason} if reason else {}))
        if not ok:
            self.ctx.state.marks[f"failed:{r.item.name}"] = self.ctx.clock()
        name = r.item.name
        self._end()
        self.on_finish(name, ok)

    def _wait(self, secs: float) -> None:
        if self.running:
            self.running.wait_until = self.ctx.clock() + float(secs)

    # ------------------------------------------------------------ stepping
    def tick(self) -> None:
        r = self.running
        if r is None:
            return
        now = self.ctx.clock()
        if now < r.paused_until or now < r.wait_until:
            return
        if now - r.started > r.item.spec.get("timeout_s", DEFAULT_TASK_TIMEOUT_S):
            self._finish(False, "timeout")
            return
        steps = r.item.spec["steps"]
        if r.step >= len(steps):
            self._finish(True)
            return
        st = steps[r.step]
        if now - r.step_started > st.get("timeout_s", DEFAULT_STEP_TIMEOUT_S):
            self._finish(False, f"step {r.step} timeout")
            return
        src = Source("task", r.item.id, reason=f"{r.item.name} step {r.step}", task_step={"task_id": r.task_id,
                                                                                          "step": r.step},
                     trigger_seq=r.source.trigger_seq)
        key = f"steps[{r.step}]"
        done = self._step(r, st, key, src, now)
        if done is True:
            self._next(r, now)
        elif done is False:
            self._finish(False, f"step {r.step} failed")

    def _next(self, r: Running, now: float) -> None:
        r.step += 1
        r.step_started = now
        r.step_count = 0
        r.step_last = -1e9
        self._event("step")
        if r.step >= len(r.item.spec["steps"]):
            self._finish(True)

    def _exprs(self, r: Running, prefix: str) -> list:
        ks = [k for k in r.item.exprs if k.startswith(prefix + "[")]
        return [r.item.exprs[k] for k in sorted(ks, key=lambda k: int(k[len(prefix) + 1:k.index("]", len(prefix))]))]

    def _step(self, r: Running, st: dict, key: str, src: Source, now: float) -> bool | None:
        """True: step done. False: task failed. None: still working."""
        ctx = self.ctx
        if "do" in st:
            ctx.run_actions(self._exprs(r, f"{key}.do"), src)
            return True
        if "go_to" in st:
            ctx.source = src
            try:
                room = ctx.eval(r.item.exprs[f"{key}.go_to[0]"])
                status = ctx.a_go_to(room)
            finally:
                ctx.source = None
            return True if status == "arrived" else False if status == "no_path" else None
        if "wait_until" in st:
            return True if ctx.eval(r.item.exprs[f"{key}.wait_until[0]"]) else None
        if "wait" in st:
            return True if now - r.step_started >= float(st["wait"]) else None
        if "repeat" in st:
            rep = st["repeat"]
            cond = r.item.exprs.get(f"{key}.repeat.while[0]")
            if r.step_count >= int(rep.get("max", 20)) or (cond is not None and not ctx.eval(cond)):
                return True
            if now - r.step_last >= float(rep.get("every_s", 2.0)) and not ctx.moving():
                r.step_last = now
                r.step_count += 1
                ctx.run_actions(self._exprs(r, f"{key}.repeat.do"), src)
            return None
        if "go_back" in st:
            loc = ctx.memoria.locator(ctx.agent)
            if len(loc.trail) <= r.trail_len:
                return True
            ctx.source = src
            try:
                status = ctx.a_go_back()
            finally:
                ctx.source = None
            return False if status == "no_path" else None
        return False
