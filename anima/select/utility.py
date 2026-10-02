"""Utility-based behavior selection (D18).

score(behavior) = weight × Π clamp(consideration, 0, 1), for the best target if it has `targets`.
The current behavior gets an inertia bonus so the agent does not flip back and forth.
The scores of the top candidates are the reason recorded with every command (D11).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from anima.protocol.commands import Source
from anima.runtime.context import Context
from anima.sigil.expr import ExprError
from anima.sigil.program import Item

DEFAULT_INERTIA = 0.1
DEFAULT_EVERY_S = 3.0
DEFAULT_COOLDOWN_S = 30.0       # after a task-behavior's task succeeds
DEFAULT_RETRY_S = 600.0         # after it fails


@dataclass
class Choice:
    item: Item
    score: float
    target: dict[str, Any] | None = None


@dataclass
class UtilitySelector:
    ctx: Context
    publish: Callable[[str, dict], None]
    start_task: Callable[[str, Source], None]
    task_running: Callable[[], str | None]
    abandon_task: Callable[[str], None] = lambda reason: None
    current: Choice | None = None
    last_scores: list[dict[str, Any]] = field(default_factory=list)
    _last_do: float = -1e9
    _blocked_until: dict[str, float] = field(default_factory=dict)
    _held: dict[str, float] = field(default_factory=dict)      # task-behavior -> score when its task started
    errors: list[str] = field(default_factory=list)

    def block(self, behavior: str, seconds: float) -> None:
        self._blocked_until[behavior] = self.ctx.clock() + seconds

    def task_finished(self, task: str, ok: bool) -> None:
        for it in self.ctx.program.behaviors.values():
            if it.spec.get("task") == task:
                self._held.pop(it.name, None)
                self.block(it.name, it.spec.get("cooldown_s", DEFAULT_COOLDOWN_S) if ok
                           else it.spec.get("retry_s", DEFAULT_RETRY_S))

    # ------------------------------------------------------------ scoring
    def _score_one(self, item: Item, target: dict[str, Any] | None) -> float:
        score = float(item.spec.get("weight", 1.0))
        for key in sorted((k for k in item.exprs if k.startswith("considerations[")), key=_order):
            v = self.ctx.eval(item.exprs[key], target=target)
            v = 0.0 if v is None else float(v)
            score *= max(0.0, min(1.0, v))
            if score == 0.0:
                break
        return score

    def score(self, item: Item) -> Choice:
        now = self.ctx.clock()
        if now < self._blocked_until.get(item.name, -1e9):
            return Choice(item, 0.0)
        when = item.exprs.get("when[0]")
        if when is not None and not self.ctx.eval(when):
            return Choice(item, 0.0)
        tex = item.exprs.get("targets[0]")
        if tex is None:
            score = self._score_one(item, None)
            task = item.spec.get("task")
            if task and self.task_running() == task and item.name in self._held:
                # commitment: a started task keeps its behavior's starting score until it ends
                # (its `when` gate still applies, so a fight still interrupts it)
                score = max(score, self._held[item.name])
            return Choice(item, score)
        best = Choice(item, 0.0)
        for t in self.ctx.eval(tex) or []:
            t = t if isinstance(t, dict) else {"name": t}
            s = self._score_one(item, t)
            if s > best.score:
                best = Choice(item, s, t)
        return best

    def evaluate(self) -> list[Choice]:
        out = []
        inertia = float(self.ctx.program.policies.get("inertia", DEFAULT_INERTIA))
        for item in self.ctx.program.behaviors.values():
            try:
                c = self.score(item)
            except (ExprError, KeyError, TypeError, ValueError) as e:
                self.errors.append(f"{item.id}: {e}")
                c = Choice(item, 0.0)
            if self.current and c.item.name == self.current.item.name and c.score > 0:
                c.score += inertia
            out.append(c)
        out.sort(key=lambda c: -c.score)
        return out

    # ------------------------------------------------------------ acting
    def tick(self, trigger_seq: int | None = None) -> Choice | None:
        ranked = self.evaluate()
        self.last_scores = [{"id": c.item.id, "score": round(c.score, 3)} for c in ranked[:5] if c.score > 0]
        best = ranked[0] if ranked and ranked[0].score > 0 else None
        now = self.ctx.clock()
        prev = self.current
        changed = (best is None) != (prev is None) or (best and prev and best.item.name != prev.item.name)
        if changed:
            self.publish("runtime.behavior", {"from": prev.item.name if prev else None,
                                              "to": best.item.name if best else None,
                                              "scores": self.last_scores, "trigger_seq": trigger_seq})
            self.ctx.behavior = best.item.name if best else None
            self.ctx.behavior_since = now
            self._last_do = -1e9
        self.current = best
        running = self.task_running()
        if running and (best is None or best.item.spec.get("task") != running):
            # a task belongs to the behavior that started it: switching away ends it
            self.abandon_task(f"behavior switched to {best.item.name if best else 'nothing'}")
        if best is None:
            return None
        spec = best.item.spec
        src = Source("behavior", best.item.id, reason=f"score {best.score:.2f}", trigger_seq=trigger_seq,
                     scores=self.last_scores[:3])
        if "task" in spec:
            if self.task_running() != spec["task"]:
                self._held[best.item.name] = best.score
                self.start_task(spec["task"], src)
            return best
        every = float(spec.get("every_s", DEFAULT_EVERY_S))
        if changed and spec.get("delay_s"):
            self._last_do = now - every + float(spec["delay_s"])     # first action only after delay_s
        if now - self._last_do >= every:
            self._last_do = now
            do = [best.item.exprs[k] for k in sorted(best.item.exprs, key=_order) if k.startswith("do[")]
            self.ctx.run_actions(do, src, priority=1, target=best.target)
        return best


def _order(key: str) -> int:
    return int(key[key.index("[") + 1:key.index("]")])
