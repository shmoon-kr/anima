"""Run statistics from a recording or a replayed play log (PHASE-1-CLOSE §2, `anima stats`).

Works on events only, so the same numbers come out of an Anima recording (real time) and a
replayed tintin log. Tintin logs carry no time: there the clock is estimated from the day/night
messages the server sends at fixed game hours (see `game_clock`).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Iterable

from anima.protocol.envelope import Event

KILL_WINDOW_S = 3.0          # the same death seen by several members is one kill
GAP_S = 300.0                # a stretch this long without a kill is reported
PHASE_HOUR = {"sunrise": 5, "day": 6, "sunset": 21, "night": 22}   # tbaMUD weather.c another_hour
SECS_PER_GAME_HOUR = 75      # tbaMUD utils.h SECS_PER_MUD_HOUR


@dataclass
class Member:
    name: str
    exp: int = 0
    deaths: int = 0
    flees: int = 0
    flee_failed: int = 0
    levels: int = 0
    refused: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    commands: int = 0
    deaths_seen: int = 0
    events: int = 0
    unknown: int = 0
    min_hp: int | None = None
    min_mv: int | None = None
    min_hp_pct: float | None = None
    min_mv_pct: float | None = None
    max_vitals: dict[str, int] = field(default_factory=dict)
    apart_s: float = 0.0
    known_s: float = 0.0
    hours: float | None = None   # own observed time when members have separate clocks
    progress: float = 0.0        # net levels gained: exp / span of the level it was gained at, minus deaths
    death_cost: float = 0.0      # levels lost to deaths (fight.c:323: a death costs half of all experience)
    exp_unscaled: int = 0        # exp that could not be scaled (level or class unknown)


@dataclass
class Stats:
    hours: float
    kills: list[float]
    gaps: list[tuple[float, float]]
    members: dict[str, Member]
    leader: str | None
    phases: list[str]
    shared_clock: bool
    follows: dict[str, int] = field(default_factory=dict)
    scaled: bool = False                 # a level table and some member's level were known
    flow: "Flow | None" = None
    animus: "AnimusCost | None" = None

    @property
    def progress_per_hour(self) -> float | None:
        """The party's growth: mean net levels per member per hour (None if nothing could be scaled)."""
        if not self.scaled or not self.members:
            return None
        return sum(m.progress for m in self.members.values()) / len(self.members) / max(self.hours, 1e-9)

    @property
    def unknown_ratio(self) -> float:
        ev = sum(m.events for m in self.members.values())
        return sum(m.unknown for m in self.members.values()) / max(1, ev)


@dataclass
class Flow:
    """Where the party's time went (shared clock only). Phase-2 task metrics (PHASE-2-PLAN S0)."""
    idle_s: float = 0.0               # leader had no behavior to run
    waiting_s: float = 0.0            # ... while a member was resting or sleeping (the leader waits for them)
    leader_rest_s: float = 0.0        # the leader itself resting or sleeping (a party camp, D30)
    rooms_entered: int = 0            # leader
    revisits: int = 0                 # entered a room seen among the last REVISIT_WINDOW rooms
    gold: dict[str, tuple[int, int]] = field(default_factory=dict)       # member -> (first, last) reported
    phase_s: dict[str, float] = field(default_factory=lambda: defaultdict(float))   # day | night
    phase_kills: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    windows: list[dict[str, float]] = field(default_factory=list)     # per WINDOW: kills, exp, waiting_s, idle_s


@dataclass
class AnimusCost:
    requests: dict[str, int] = field(default_factory=lambda: defaultdict(int))     # per tier
    answered: int = 0
    timeouts: int = 0
    rejected: dict[str, int] = field(default_factory=lambda: defaultdict(int))     # per reason
    latency_s: list[float] = field(default_factory=list)
    patches: dict[str, int] = field(default_factory=lambda: defaultdict(int))      # runtime.animus event -> n


REVISIT_WINDOW = 20
DAYLIGHT = {"sunrise": "day", "day": "day", "sunset": "night", "night": "night"}


def _flow(evs: list[Event], leader: str, kills: list[float], window_s: float) -> Flow:
    fl = Flow()
    start = evs[0].t
    behavior: str | None = None
    resting: set[str] = set()
    recent: list[object] = []
    phase: str | None = None
    lead_pos = "standing"
    win: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    prev_t = start
    for ev in evs:
        dt = ev.t - prev_t
        if dt > 0:
            w = win[int((prev_t - start) // window_s)]
            if behavior is None:
                fl.idle_s += dt
                w["idle_s"] += dt
                if resting:
                    fl.waiting_s += dt
                    w["waiting_s"] += dt
            if phase:
                fl.phase_s[phase] += dt
            if lead_pos in ("resting", "sleeping"):
                fl.leader_rest_s += dt
                w["rest_s"] += dt
            prev_t = ev.t
        d = ev.data
        if ev.agent == leader and ev.type == "runtime.behavior":
            behavior = d.get("to")
        elif ev.type == "position" and ev.agent == leader:
            lead_pos = d.get("position", lead_pos)
        elif ev.type == "position" and ev.agent != leader:
            (resting.add if d.get("position") in ("resting", "sleeping") else resting.discard)(ev.agent)
        elif ev.type == "connection.closed":
            resting.discard(ev.agent)
        elif ev.type == "world.time" and d.get("phase") in DAYLIGHT:
            phase = DAYLIGHT[d["phase"]]
        elif ev.type == "exp.gain":
            win[int((ev.t - start) // window_s)]["exp"] += int(d.get("amount") or 0)
        elif ev.type == "char.score" and d.get("gold") is not None:
            first = fl.gold.get(ev.agent, (d["gold"], d["gold"]))[0]
            fl.gold[ev.agent] = (first, d["gold"])
        elif ev.type == "room" and ev.agent == leader:
            key = _room_key(ev)
            if recent and key == recent[-1]:
                continue                                  # a `look` in the same room is not a move
            fl.rooms_entered += 1
            fl.revisits += key in recent
            recent = (recent + [key])[-REVISIT_WINDOW:]
    ph: str | None = None
    k = 0
    for ev in evs:                                      # kills by daylight
        if ev.type == "world.time" and ev.data.get("phase") in DAYLIGHT:
            ph = DAYLIGHT[ev.data["phase"]]
        while k < len(kills) and kills[k] <= ev.t:
            if ph:
                fl.phase_kills[ph] += 1
            k += 1
    for t in kills:
        win[int((t - start) // window_s)]["kills"] += 1
    n = int((evs[-1].t - start) // window_s) + 1
    fl.windows = [dict(win[i]) for i in range(n)]
    return fl


def _animus(evs: list[Event]) -> AnimusCost:
    ac = AnimusCost()
    for ev in evs:
        d = ev.data
        if ev.type == "animus.request":
            ac.requests[d.get("tier", "?")] += 1
        elif ev.type == "animus.response":
            ac.answered += 1
            if d.get("latency_s") is not None:
                ac.latency_s.append(float(d["latency_s"]))
        elif ev.type == "animus.timeout":
            ac.timeouts += 1
        elif ev.type == "animus.rejected":
            ac.rejected[d.get("reason", "?")] += 1
        elif ev.type == "runtime.animus":
            ac.patches[d.get("event", "?")] += 1
    return ac


def game_clock(events: list[Event]) -> list[Event]:
    """Re-time a replayed log (t = line number) from its day/night messages.

    Each `world.time` phase names a game hour, so the hours between two consecutive phases of one
    session are known modulo 24. We assume no whole game day (30 real minutes) passes indoors
    unseen. Time only advances between anchors: events before a session's first phase or after its
    last one are pinned to that anchor, so they still count but add no time (logins, a death
    that ends a session). Offline time between sessions is unknown and is not counted.
    """
    sessions: list[list[Event]] = [[]]
    for ev in events:
        if ev.type == "connection.in_game" and sessions[-1]:
            sessions.append([])
        sessions[-1].append(ev)

    out: list[Event] = []
    est = 0.0
    for session in sessions:
        anchors: list[tuple[float, float]] = []      # (line, estimated seconds)
        hour: int | None = None
        for ev in session:
            if ev.type == "world.time" and ev.data.get("phase") in PHASE_HOUR:
                h = PHASE_HOUR[ev.data["phase"]]
                if hour is not None:
                    est += ((h - hour) % 24 or 24) * SECS_PER_GAME_HOUR
                anchors.append((ev.t, est))
                hour = h
        i = 0
        for ev in session:
            if not anchors:
                t = est
            else:
                while i + 1 < len(anchors) and anchors[i + 1][0] <= ev.t:
                    i += 1
                (l0, s0), (l1, s1) = anchors[i], anchors[min(i + 1, len(anchors) - 1)]
                t = s0 if l1 == l0 or ev.t <= l0 else s0 + (s1 - s0) * min(1.0, (ev.t - l0) / (l1 - l0))
            out.append(Event(ev.type, ev.data, ev.agent, ev.seq, t, None, ev.v))
    return out


def _room_key(ev: Event):
    if ev.type == "room":
        return ev.data.get("id") or (ev.data.get("name"), ev.data.get("desc"))
    return None                                   # room.dark: we do not know where we are


Span = Callable[[str, int], "int | None"]        # (agent, level) -> exp between that level and the next


def compute(events: Iterable[Event], shared_clock: bool = True, window_min: float = 10.0,
            span: Span | None = None) -> Stats:
    evs = sorted(events, key=lambda e: e.t)
    members: dict[str, Member] = {}
    deaths: list[tuple[float, str, str]] = []
    phases: list[str] = []
    follows: dict[str, int] = defaultdict(int)
    named_leader: str | None = None
    room: dict[str, object] = {}
    level: dict[str, int] = {}
    exp_total: dict[str, int] = {}

    def scale(agent: str, amount: float) -> float | None:
        s = span(agent, level[agent]) if span and level.get(agent) else None
        return amount / s if s else None

    def m(name: str) -> Member:
        if name not in members:
            members[name] = Member(name)
        return members[name]

    for ev in evs:
        me = m(ev.agent)
        d = ev.data
        if not ev.type.startswith(("runtime.", "animus.", "command.sent")) and ev.type != "prompt":
            me.events += 1
            me.unknown += ev.type == "unknown"
        if ev.type == "command.sent":
            if d.get("source", {}).get("kind") != "system":
                me.commands += 1
        elif ev.type == "command.refused":
            me.refused[d.get("reason", "?")] += 1
        elif ev.type == "exp.gain":
            amount = int(d.get("amount") or 0)
            me.exp += amount
            if ev.agent in exp_total:
                exp_total[ev.agent] += amount
            lv = scale(ev.agent, amount)
            if lv is None:
                me.exp_unscaled += amount
            else:
                me.progress += lv
        elif ev.type == "self.died":
            me.deaths += 1
            if ev.agent in exp_total:
                lost = exp_total[ev.agent] // 2
                exp_total[ev.agent] -= lost
                lv = scale(ev.agent, lost)
                if lv is not None:
                    me.death_cost += lv
                    me.progress -= lv
        elif ev.type == "self.fled":
            me.flees += 1
        elif ev.type == "self.flee_failed":
            me.flee_failed += 1
        elif ev.type == "level.up":
            me.levels += int(d.get("levels") or 1)
            if ev.agent in level:
                level[ev.agent] += int(d.get("levels") or 1)
        elif ev.type == "char.score":
            if d.get("level"):
                level[ev.agent] = int(d["level"])
            if d.get("exp") is not None:
                exp_total[ev.agent] = int(d["exp"])
        elif ev.type == "combat.death":
            me.deaths_seen += 1
            deaths.append((ev.t, str(d.get("who", "")).lower(), ev.agent))
        elif ev.type == "world.time":
            if not phases or phases[-1] != d.get("phase"):
                phases.append(d.get("phase"))
        elif ev.type == "char.vitals_max":
            me.max_vitals = {k: v for k, v in d.items() if v}
        elif ev.type == "prompt":
            for key, lo, pct in (("hp", "min_hp", "min_hp_pct"), ("mv", "min_mv", "min_mv_pct")):
                v = d.get(key)
                if v is None:
                    continue
                if getattr(me, lo) is None or v < getattr(me, lo):
                    setattr(me, lo, v)
                top = me.max_vitals.get(key)
                if top:
                    p = 100.0 * v / top
                    if getattr(me, pct) is None or p < getattr(me, pct):
                        setattr(me, pct, p)
        elif ev.type == "group.change" and d.get("event") == "new_leader":
            named_leader = d.get("who")
        elif ev.type in ("follow.moved",) or (ev.type == "group.change" and d.get("event") == "following"):
            who = d.get("leader") or d.get("who")
            if who and who != ev.agent:
                follows[who] += 1

    leader = named_leader if named_leader in members else (
        max(follows, key=follows.get) if follows and max(follows, key=follows.get) in members else None)

    # separation: time a member's last seen room differs from the leader's (shared clock only)
    if shared_clock and leader:
        prev_t: float | None = None
        for ev in evs:
            if prev_t is not None and ev.t > prev_t:
                dt = ev.t - prev_t
                lr = room.get(leader)
                for name, mem in members.items():
                    if name == leader or name not in room or lr is None or room[name] is None:
                        continue
                    mem.known_s += dt
                    if room[name] != lr:
                        mem.apart_s += dt
            prev_t = ev.t
            if ev.type in ("room", "room.dark"):
                room[ev.agent] = _room_key(ev)
            elif ev.type == "connection.closed":
                room.pop(ev.agent, None)

    kills: list[float] = []
    last: dict[str, tuple[float, set[str]]] = {}     # victim -> (time, agents who saw it)
    for t, who, agent in deaths:
        prev = last.get(who)
        if prev and t - prev[0] <= KILL_WINDOW_S and agent not in prev[1]:
            prev[1].add(agent)                           # another member saw the same death
            continue
        last[who] = (t, {agent})
        kills.append(t)

    start, end = (evs[0].t, evs[-1].t) if evs else (0.0, 0.0)
    gaps: list[tuple[float, float]] = []
    marks = [start, *kills, end]
    for a, b in zip(marks, marks[1:]):
        if b - a >= GAP_S:
            gaps.append((a - start, b - a))
    st = Stats((end - start) / 3600.0, kills, gaps, members, leader, phases, shared_clock, dict(follows))
    st.scaled = bool(span) and any(span(a, lv) for a, lv in level.items())
    if shared_clock and leader and evs:
        st.flow = _flow(evs, leader, kills, 60.0 * window_min)
        st.animus = _animus(evs)
    return st


def merge(parts: list[Stats]) -> Stats:
    """Combine per-log stats of separately clocked logs. Kills and gaps come from the leader's log."""
    members: dict[str, Member] = {}
    follows: dict[str, int] = defaultdict(int)
    for st in parts:
        for name, mem in st.members.items():
            mem.hours = st.hours
            members[name] = mem
        for name, c in st.follows.items():
            follows[name] += c
    leader = max(follows, key=follows.get) if follows else None
    base = next((st for st in parts if leader in st.members), parts[0])
    phases = base.phases
    return Stats(base.hours, base.kills, base.gaps, members, leader, phases, False, dict(follows))


def _phases(phases: list[str]) -> str:
    if len(phases) <= 8:
        return " → ".join(phases) or "-"
    return ", ".join(f"{p} {phases.count(p)}" for p in PHASE_HOUR if p in phases)


def _fmt_min(v, pct) -> str:
    if v is None:
        return "-"
    return f"{v}" + (f" ({pct:.0f}%)" if pct is not None else "")


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def _render_flow(st: Stats, window_min: float | None) -> list[str]:
    fl, h = st.flow, max(st.hours, 1e-9)
    out = ["", f"leader idle {fl.idle_s / 60:.1f}m ({fl.idle_s / 36 / h:.0f}%), of which waiting for a resting member"
               f" {fl.waiting_s / 60:.1f}m ({fl.waiting_s / 36 / h:.0f}%)",
           f"leader resting or sleeping itself {fl.leader_rest_s / 60:.1f}m ({fl.leader_rest_s / 36 / h:.0f}%)",
           f"leader moves {fl.rooms_entered}, revisits within {REVISIT_WINDOW} rooms {fl.revisits}"
           f" ({100 * fl.revisits / max(1, fl.rooms_entered):.0f}%)"]
    if fl.phase_s:
        out.append("by daylight: " + ", ".join(
            f"{p} {fl.phase_s[p] / 60:.0f}m {fl.phase_kills.get(p, 0)} kills"
            f" ({fl.phase_kills.get(p, 0) / max(fl.phase_s[p] / 3600, 1e-9):.1f}/h)" for p in ("day", "night") if p in fl.phase_s))
    if fl.gold:
        out.append("gold: " + ", ".join(f"{n} {a}→{b} ({b - a:+d})" for n, (a, b) in sorted(fl.gold.items())))
    if window_min:
        out += ["", f"{'window':>8} {'kills':>6} {'exp':>7} {'idle':>6} {'waiting':>8} {'camp':>6}"]
        for i, w in enumerate(fl.windows):
            out.append(f"{f'+{i * window_min:.0f}m':>8} {w.get('kills', 0):6.0f} {w.get('exp', 0):7.0f}"
                       f" {w.get('idle_s', 0) / 60:5.1f}m {w.get('waiting_s', 0) / 60:7.1f}m {w.get('rest_s', 0) / 60:5.1f}m")
    ac = st.animus
    if ac and (ac.requests or ac.patches):
        lat = (f", latency median {_pct(ac.latency_s, .5):.1f}s p90 {_pct(ac.latency_s, .9):.1f}s"
               if ac.latency_s else "")
        out += ["", "animus: requests " + (", ".join(f"{t} {c}" for t, c in sorted(ac.requests.items())) or "0")
                + f" (claude {ac.requests.get('claude', 0) / h:.1f}/h); answered {ac.answered}, timeouts {ac.timeouts}"
                + (", rejected " + ", ".join(f"{r} {c}" for r, c in ac.rejected.items()) if ac.rejected else "") + lat]
        if ac.patches:
            out.append("animus patches: " + ", ".join(f"{e} {c}" for e, c in sorted(ac.patches.items())))
    return out


def render(st: Stats, window_min: float | None = None) -> str:
    h = max(st.hours, 1e-9)
    lines = [
        f"duration {st.hours:.2f} h{'' if st.shared_clock else ' (estimated from day/night messages)'}"
        f"   leader {st.leader or '?'}   day/night seen: {_phases(st.phases)}",
        (f"growth {st.progress_per_hour:+.3f} levels/h per member (exp scaled by each level's span, deaths subtracted)"
         if st.progress_per_hour is not None else "growth: not scaled (no level table or no member's level known)"),
        f"kills {len(st.kills)} ({len(st.kills) / h:.1f}/h)   unknown ratio {st.unknown_ratio:.2%}",
        f"stretches ≥{GAP_S / 60:.0f} min without a kill: {len(st.gaps)}"
        + (" — " + ", ".join(f"+{a / 60:.0f}m for {d / 60:.1f}m" for a, d in st.gaps) if st.gaps else "")
        if st.shared_clock else
        "stretches without a kill: not measured (estimated clock: sessions without two day/night messages"
        " have no time of their own)",
        "",
        f"{'member':10} {'lvl/h':>7} {'d.cost':>6} {'exp/h':>8} {'die':>4} {'flee':>5} {'lvl':>4} {'cmds':>6} {'refused':>8}"
        f" {'kills seen':>11} {'apart':>8} {'min HP':>11} {'min MV':>11}",
    ]
    lead_seen = st.members[st.leader].deaths_seen if st.leader in st.members else 0
    for name in sorted(st.members, key=lambda n: (n != st.leader, n)):
        mem = st.members[name]
        refused = sum(mem.refused.values())
        seen = f"{mem.deaths_seen}" + (f" ({100 * mem.deaths_seen / lead_seen:.0f}%)" if lead_seen else "")
        if name == st.leader or not st.shared_clock or not mem.known_s:
            apart = "-"
        else:
            apart = f"{mem.apart_s / 60:.1f}m"
        lines.append(
            f"{name:10} {mem.progress / max(mem.hours or h, 1e-9):+7.3f} {mem.death_cost:6.2f} {mem.exp / max(mem.hours or h, 1e-9):8.0f} {mem.deaths:4d} {mem.flees:5d} {mem.levels:4d} {mem.commands if st.shared_clock else '-':>6}"
            f" {refused:8d} {seen:>11} {apart:>8} {_fmt_min(mem.min_hp, mem.min_hp_pct):>11}"
            f" {_fmt_min(mem.min_mv, mem.min_mv_pct):>11}")
    reasons: dict[str, int] = defaultdict(int)
    for mem in st.members.values():
        for r, c in mem.refused.items():
            reasons[r] += c
    if reasons:
        lines += ["", "refused by reason: " + ", ".join(f"{r} {c}" for r, c in
                                                      sorted(reasons.items(), key=lambda x: -x[1]))]
    if st.flow:
        lines += _render_flow(st, window_min)
    return "\n".join(lines)
