"""Reflexes: immediate reactions to events (D13). They never wait and they preempt tasks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from anima.protocol.commands import Source
from anima.protocol.envelope import Event
from anima.runtime.context import Context
from anima.sigil.program import Item

PREEMPT_S = 3.0


@dataclass
class ReflexEngine:
    ctx: Context
    publish: Callable[[str, dict], None]
    on_fire: Callable[[], None] = lambda: None          # e.g. pause the running task
    _last: dict[str, float] = field(default_factory=dict)

    def _matches(self, item: Item, ev: Event) -> bool:
        on = item.spec.get("on")
        types = on if isinstance(on, list) else [on]
        return any(ev.type == t or (t.endswith("*") and ev.type.startswith(t[:-1])) for t in types)

    def on_event(self, ev: Event) -> None:
        now = self.ctx.clock()
        for item in self.ctx.program.reflexes.values():
            if not self._matches(item, ev):
                continue
            cond = item.exprs.get("if[0]")
            if cond is not None and not self.ctx.eval(cond, event=ev):
                continue
            if now - self._last.get(item.name, -1e9) < item.spec.get("cooldown_s", 0):
                continue
            self._last[item.name] = now
            do = [item.exprs[k] for k in sorted(item.exprs, key=_order) if k.startswith("do[")]
            src = Source("reflex", item.id, reason=f"on {ev.type}", trigger_seq=ev.seq or None)
            self.ctx.run_actions(do, src, priority=0, event=ev)
            self.publish("runtime.reflex", {"id": item.id, "trigger_seq": ev.seq, "action": [e.src for e in do]})
            self.on_fire()


def _order(key: str) -> int:
    return int(key[key.index("[") + 1:key.index("]")])
