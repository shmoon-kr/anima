"""Run statistics from a recording or a replayed play log (PHASE-1-CLOSE §2, `anima stats`).

Works on events only, so the same numbers come out of an Anima recording (real time) and a
replayed tintin log. Tintin logs carry no time: there the clock is estimated from the day/night
messages the server sends at fixed game hours (see `game_clock`).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

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

    @property
    def unknown_ratio(self) -> float:
        ev = sum(m.events for m in self.members.values())
        return sum(m.unknown for m in self.members.values()) / max(1, ev)


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


def compute(events: Iterable[Event], shared_clock: bool = True) -> Stats:
    evs = sorted(events, key=lambda e: e.t)
    members: dict[str, Member] = {}
    deaths: list[tuple[float, str, str]] = []
    phases: list[str] = []
    follows: dict[str, int] = defaultdict(int)
    named_leader: str | None = None
    room: dict[str, object] = {}

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
            me.exp += int(d.get("amount") or 0)
        elif ev.type == "self.died":
            me.deaths += 1
        elif ev.type == "self.fled":
            me.flees += 1
        elif ev.type == "self.flee_failed":
            me.flee_failed += 1
        elif ev.type == "level.up":
            me.levels += int(d.get("levels") or 1)
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
    return Stats((end - start) / 3600.0, kills, gaps, members, leader, phases, shared_clock, dict(follows))


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


def render(st: Stats) -> str:
    h = max(st.hours, 1e-9)
    lines = [
        f"duration {st.hours:.2f} h{'' if st.shared_clock else ' (estimated from day/night messages)'}"
        f"   leader {st.leader or '?'}   day/night seen: {_phases(st.phases)}",
        f"kills {len(st.kills)} ({len(st.kills) / h:.1f}/h)   unknown ratio {st.unknown_ratio:.2%}",
        f"stretches ≥{GAP_S / 60:.0f} min without a kill: {len(st.gaps)}"
        + (" — " + ", ".join(f"+{a / 60:.0f}m for {d / 60:.1f}m" for a, d in st.gaps) if st.gaps else "")
        if st.shared_clock else
        "stretches without a kill: not measured (estimated clock: sessions without two day/night messages"
        " have no time of their own)",
        "",
        f"{'member':10} {'exp/h':>8} {'die':>4} {'flee':>5} {'lvl':>4} {'cmds':>6} {'refused':>8}"
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
            f"{name:10} {mem.exp / max(mem.hours or h, 1e-9):8.0f} {mem.deaths:4d} {mem.flees:5d} {mem.levels:4d} {mem.commands if st.shared_clock else '-':>6}"
            f" {refused:8d} {seen:>11} {apart:>8} {_fmt_min(mem.min_hp, mem.min_hp_pct):>11}"
            f" {_fmt_min(mem.min_mv, mem.min_mv_pct):>11}")
    reasons: dict[str, int] = defaultdict(int)
    for mem in st.members.values():
        for r, c in mem.refused.items():
            reasons[r] += c
    if reasons:
        lines += ["", "refused by reason: " + ", ".join(f"{r} {c}" for r, c in
                                                      sorted(reasons.items(), key=lambda x: -x[1]))]
    return "\n".join(lines)
