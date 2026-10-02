"""The party strategist (PHASE-2-PLAN S3): Claude looks at the party now and then and patches knobs.

It is a supervisor-level loop, not part of any agent. It never blocks: it submits one question to the
Animus queue (`claude` tier), keeps watching events, and when the answer arrives as an event it hands
the patch to the overlay, which validates everything (D26-D28). If the answer never comes, nothing changes.

When it asks:
- regularly (every_s, 30 min), and
- soon after something important (min_gap_s apart): a death, a level up, no kill for no_kill_s,
  the hunting ground unreachable, the leader entering another zone.

What it sees: the party snapshot, the last 30 minutes through `anima stats` (the same numbers the
comparison uses), zones for the party's level, the knobs and their current values, recent patches
(and which were followed by a death), the outcome of its last answer, and its own notes.
"""
from __future__ import annotations

import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from anima.animus.overlay import PATCH_SCHEMA, Overlay, patch_groups
from anima.animus.queue import AnimusQueue, Request
from anima.protocol.envelope import Event
from anima.stats import PHASE_HOUR, SECS_PER_GAME_HOUR, compute

WINDOW_S = 1800.0
QUESTION = (
    "You are the strategist of a party of agents playing a text MUD together. Decide the party's direction "
    "for the next half hour: where it hunts (rally, circuit), when members rest or flee, what it hunts, and "
    "the weights of efficiency behaviors. Look first at where time is lost (waiting for resting members, "
    "stretches without kills, revisiting rooms) and at risk (deaths, flees, lowest HP). Change few things and "
    "say why; change nothing if the party is doing well. Your changes go through a validator: only the keys "
    "under `knobs`, inside their ranges and steps; a rejected change tells you why next time. Give `ttl_s` "
    "to a change that is a trial. If the real fix needs a rule the knobs cannot express, say so in `proposal`. "
    "Write `notes` for your next call: what you changed, what to watch."
)
SCHEMA = dict(PATCH_SCHEMA, shape=PATCH_SCHEMA["shape"]
              + ' Also "notes": "<for your next call>", and optionally "proposal": "<a rule change people should'
              ' consider, with the evidence>".')
_ids = itertools.count(1)


def summarize(events: list[Event]) -> dict[str, Any]:
    """The last window through anima stats, compact enough for a prompt."""
    if not events:
        return {}
    st = compute(events, window_min=10)
    h = max(st.hours, 1e-9)
    out: dict[str, Any] = {"minutes": round(st.hours * 60), "kills": len(st.kills),
                           "kills_per_hour": round(len(st.kills) / h, 1)}
    if st.flow:
        fl = st.flow
        out.update(leader_idle_min=round(fl.idle_s / 60, 1),
                   leader_waiting_for_resting_member_min=round(fl.waiting_s / 60, 1),
                   revisit_pct=round(100 * fl.revisits / max(1, fl.rooms_entered)),
                   kills_by_daylight={p: fl.phase_kills.get(p, 0) for p in fl.phase_s})
    out["members"] = {n: {"exp": m.exp, "deaths": m.deaths, "flees": m.flees, "levels": m.levels,
                          "lowest_hp_pct": None if m.min_hp_pct is None else round(m.min_hp_pct),
                          "lowest_mv_pct": None if m.min_mv_pct is None else round(m.min_mv_pct),
                          "apart_from_leader_min": round(m.apart_s / 60, 1),
                          "refused": dict(m.refused)} for n, m in st.members.items()}
    return out


def daylight(phase: str | None, since_s: float) -> dict[str, Any] | None:
    """Game daylight from the last day/night message: now and minutes to the next change."""
    if phase not in PHASE_HOUR:
        return None
    hour = PHASE_HOUR[phase] + since_s / SECS_PER_GAME_HOUR
    marks = sorted(PHASE_HOUR.items(), key=lambda kv: kv[1])
    for name, h in marks + [(marks[0][0], marks[0][1] + 24)]:
        if h > hour:
            return {"now": "day" if phase in ("sunrise", "day") else "night",
                    "next": name, "real_minutes_to_next": round((h - hour) * SECS_PER_GAME_HOUR / 60, 1)}
    return None


@dataclass
class Strategist:
    queue: AnimusQueue
    overlay: Overlay
    leader: str
    view: Callable[[], dict[str, Any]]                 # party snapshot
    knobs: Callable[[], dict[str, Any]]                # knob table with current values
    world: Callable[[], list[dict[str, Any]]]          # zones for the party's level
    zone_of_leader: Callable[[], int | None]
    clock: Callable[[], float] = time.time
    notes_path: Path | None = None
    every_s: float = 1800.0
    min_gap_s: float = 300.0
    no_kill_s: float = 600.0
    timeout_s: float = 300.0
    first_after_s: float = 300.0                       # first look shortly after start
    _events: deque = field(default_factory=deque)
    _reasons: list[str] = field(default_factory=list)
    _pending: str | None = None
    _last_call: float | None = None
    _start: float | None = None
    _last_kill: float | None = None
    _drought: bool = False
    _zone: int | None = None
    _phase: tuple[str, float] | None = None
    _outcome: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------ watching
    def on_event(self, ev: Event) -> None:
        now = self.clock()
        if ev.type not in ("animus.request",):
            self._events.append(ev)
        while self._events and self._events[0].t < ev.t - WINDOW_S:
            self._events.popleft()
        d = ev.data
        if ev.type == "self.died":
            self._reason(f"{ev.agent} died")
        elif ev.type == "level.up":
            self._reason(f"{ev.agent} gained a level")
        elif ev.type == "combat.death":
            self._last_kill, self._drought = now, False
        elif ev.type == "world.time" and d.get("phase") in PHASE_HOUR:
            self._phase = (d["phase"], now)
        elif ev.type == "runtime.behavior" and ev.agent == self.leader and d.get("to") == "give_up_unreachable_ground":
            self._reason("the hunting ground could not be reached")
        elif ev.type == "room" and ev.agent == self.leader:
            z = self.zone_of_leader()
            if z is not None and self._zone is not None and z != self._zone:
                self._reason(f"the leader entered zone {z}")
            if z is not None:
                self._zone = z
        elif ev.type in ("animus.response", "animus.timeout", "animus.rejected") and d.get("id") == self._pending:
            self._pending = None
            if ev.type == "animus.response":
                self._apply(d["id"], d.get("answer"))
            else:
                self._outcome = [{"ok": False, "reason": f"{ev.type.split('.')[1]}: {d.get('reason', '')}"}]

    def _reason(self, why: str) -> None:
        if why not in self._reasons:
            self._reasons.append(why)

    # ------------------------------------------------------------ asking
    def tick(self) -> str | None:
        """Submit a question if one is due. Returns the request id, or None."""
        now = self.clock()
        if self._start is None:
            self._start = now
            self._last_kill = now
        if self._last_kill is not None and now - self._last_kill >= self.no_kill_s and not self._drought:
            self._drought = True
            self._reason(f"no kill for {round((now - self._last_kill) / 60)} minutes")
        if self._pending or not self.overlay.enabled:
            return None
        last = self._last_call if self._last_call is not None else self._start - self.every_s + self.first_after_s
        regular = now - last >= self.every_s
        urgent = bool(self._reasons) and now - last >= self.min_gap_s
        if not (regular or urgent):
            return None
        rid = f"strategist:{next(_ids)}"
        reasons, self._reasons = self._reasons or ["regular check"], []
        priority = "danger" if any("died" in r for r in reasons) else "stuck" if urgent else "routine"
        self.queue.submit(Request(id=rid, agent=self.leader, asker="animus/strategist", tier="claude",
                                  priority=priority, question_kind="party_strategy", question=QUESTION,
                                  context=self.context(reasons), answer_schema=SCHEMA, timeout_s=self.timeout_s,
                                  default={"changes": [], "notes": self.notes()}))
        self._pending, self._last_call = rid, now
        return rid

    def context(self, reasons: list[str]) -> dict[str, Any]:
        ov = self.overlay
        ctx: dict[str, Any] = {
            "why_now": reasons,
            "party": self.view(),
            "last_30_minutes": summarize(list(self._events)),
            "zones_for_party_level": self.world(),
            "knobs": self.knobs(),
            "current_overrides": ov.show()["layers"],
            "recent_patches": [{k: h.get(k) for k in ("event", "layer", "changes", "reason", "origin",
                                                      "followed_by_death") if h.get(k) is not None}
                               for h in ov.history[-8:]],
            "deaths_after_patches": ov.deaths[-3:],
            "outcome_of_your_last_answer": self._outcome,
            "your_notes": self.notes(),
        }
        if self._phase:
            ctx["daylight"] = daylight(self._phase[0], self.clock() - self._phase[1])
        return ctx

    # ------------------------------------------------------------ answers
    def _apply(self, rid: str, answer: Any) -> None:
        groups, reason = patch_groups(answer)
        self._outcome = []
        for layer, changes in groups.items():
            res = self.overlay.patch(layer, changes, reason or "strategist", origin="strategist", request_id=rid)
            self._outcome.append({"layer": layer, "ok": res.ok, "reason": res.reason, "errors": res.errors[:5],
                                  "applied": res.changes})
        if isinstance(answer, dict):
            if isinstance(answer.get("notes"), str):
                self._write_notes(answer["notes"])
            if isinstance(answer.get("proposal"), str) and answer["proposal"].strip():
                self._write_proposal(rid, answer["proposal"], reason)

    def notes(self) -> str:
        if self.notes_path and self.notes_path.exists():
            return self.notes_path.read_text(encoding="utf-8")[-4000:]
        return ""

    def _write_notes(self, text: str) -> None:
        if self.notes_path:
            self.notes_path.parent.mkdir(parents=True, exist_ok=True)
            self.notes_path.write_text(text[:4000], encoding="utf-8")

    def _write_proposal(self, rid: str, text: str, reason: str) -> None:
        """A rule change the knobs cannot express: people decide (PHASE-2-PLAN S1, never applied)."""
        if not self.notes_path:
            return
        d = self.notes_path.parent / "proposals"
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (d / f"{stamp}-{rid.replace(':', '-')}.md").write_text(
            f"# Proposal from the strategist ({stamp})\n\n{text.strip()}\n\nReason given: {reason}\n", encoding="utf-8")
