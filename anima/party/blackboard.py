"""Party blackboard (D14): the party is one object with shared state, not tells between sessions.

Every member's runtime state and Memoria position are read directly. The party package declares
the roster, the leader, roles and the rally point as policies; this module only computes facts
from them (who is here, who is lost, who is thirsty) and assigns nothing on its own.
Game commands that the server needs for cooperation (follow, group, give) are sent by the
members' Sigils, based on these facts.
"""
from __future__ import annotations

import time

from anima import timescale
from dataclasses import dataclass, field
from typing import Any, Callable

from anima.memoria import Memoria


CLAIM_S = 5.0          # a member going for a thing on the floor holds it this long


@dataclass
class PartyBoard:
    memoria: Memoria
    roster: list[str]
    leader: str
    roles: dict[str, list[str]] = field(default_factory=dict)
    rally: str | None = None
    classes: dict[str, str] = field(default_factory=dict)
    circuit: list[str] = field(default_factory=list)
    save_path: Any = None                                       # where the current rally survives restarts
    clock: Callable[[], float] = timescale.now
    states: dict[str, Any] = field(default_factory=dict)        # name -> AgentState
    items: dict[str, Any] = field(default_factory=dict)       # name -> its Items (share_gear)
    claims: dict[tuple[int, str], dict[str, float]] = field(default_factory=dict)   # (room, thing) -> member -> when
    camp_start: float = 0.9                                     # D30: a member's rest need that makes the room camp
    camp_end: float = 0.3                                       # ... and the need below which nobody keeps it going
    ladder: Any = None                                          # anima.party.ladder.Ladder: choose zones by level (D31)
    override_until: float = 0.0                                 # the strategist chose the hunting ground until then
    _zone_of: dict[str, int] = field(default_factory=dict)      # member -> last zone seen in
    trip_cooldown: float = 1200.0                               # D32: between shopping trips
    trip_timeout: float = 1200.0
    trip: dict[str, Any] | None = None
    _last_trip_t: float = -1e9
    _levels: tuple[int, ...] = ()
    _ladder_started: bool = False
    _last_t: float | None = None
    _apart_since: dict[str, float] = field(default_factory=dict)
    _camps: dict[Any, str | None] = field(default_factory=dict)  # room -> sentry while that room camps

    @classmethod
    def from_policies(cls, memoria: Memoria, policies: dict[str, Any], clock: Callable[[], float] = timescale.now):
        return cls(memoria, list(policies.get("roster", [])), policies.get("leader", ""),
                   {k: list(v) for k, v in (policies.get("roles") or {}).items()}, policies.get("rally"),
                   dict(policies.get("classes") or {}), list(policies.get("circuit") or []), clock=clock,
                   camp_start=float(policies.get("camp_start", 0.9)), camp_end=float(policies.get("camp_end", 0.3)),
                   trip_cooldown=float(policies.get("trip_cooldown_s", 1200)))

    def next_rally(self, agent: str) -> str | None:
        """The leader moves the party's hunting ground to the next place in the circuit."""
        if agent != self.leader:
            return self.rally
        self._refresh_circuit()
        if not self.circuit:
            return self.rally
        i = self.circuit.index(self.rally) if self.rally in self.circuit else -1
        self.rally = self.circuit[(i + 1) % len(self.circuit)]
        self.save()
        return self.rally

    # ------------------------------------------------------------ zone ladder (D31)
    def levels(self) -> list[int]:
        return [self.states[n].level for n in self.online() if self.states[n].level]

    def _refresh_circuit(self) -> bool:
        """With a ladder, the circuit is the best fitting zones for our levels now. True if it changed."""
        if self.ladder is None or self.clock() < self.override_until:
            return False
        new = self.ladder.circuit(self.levels(), has_light=self._leader_lit(), size=3, span=self.mean_span())
        if new and new != self.circuit:
            self.circuit = new
            return True
        return False

    def _leader_lit(self) -> bool:
        """Followers walk where the leader walks (the server moves them), so the leader's light decides."""
        st = self.states.get(self.leader)
        return bool(st and st.has_light)

    def span(self, name: str) -> int | None:
        st = self.states.get(name)
        return self.memoria.level_span(self.classes.get(name), st.level if st else None)

    def mean_span(self) -> float | None:
        spans = [s for n in self.online() if (s := self.span(n))]
        return sum(spans) / len(spans) if spans else None

    def _start_here(self) -> None:
        """After a (re)start, hunt near where we are instead of walking back to the saved rally (D31)."""
        if self.clock() < self.override_until:
            return
        new = self.ladder.start_here(self.levels(), has_light=self._leader_lit(), span=self.mean_span())
        if new:
            self.circuit, self.rally = new, new[0]
            lead = self.states.get(self.leader)
            if lead is not None:
                lead.marks["rally_changed"] = self.clock()
            self.save()

    def _move_on(self, why: str) -> None:
        """Go somewhere else now (a zone just proved too dangerous, or we outgrew it)."""
        if self._refresh_circuit() or self.rally not in self.circuit:
            if self.circuit and self.rally != self.circuit[0]:
                self.rally = self.circuit[0]
                lead = self.states.get(self.leader)
                if lead is not None:
                    lead.marks["rally_changed"] = self.clock()
                self.save()

    def on_event(self, ev: Any) -> None:
        """Feed the ladder from play (the supervisor subscribes this to the bus)."""
        if self.ladder is None or ev.agent not in self.states:
            return
        st = self.states[ev.agent]
        weakest = min(self.levels() or [0])
        if ev.type == "room":
            v = self.memoria.locator(ev.agent).vnum
            room = self.memoria.world.rooms.get(v) if v is not None else None
            if room is not None:
                self._zone_of[ev.agent] = room.zone
            return
        zone = self._zone_of.get(ev.agent)
        rally_zone = self._rally_zone()
        n = max(1, len(self.roster))
        if ev.agent == self.leader and zone is not None:          # time spent per zone, by the leader's clock
            last = self._last_t
            if last is not None and 0 < ev.t - last < 60:
                self.ladder.spent(zone, ev.t - last)
            self._last_t = ev.t
        if ev.type == "exp.gain" and (s := self.span(ev.agent)):
            self.ladder.gained(zone, int(ev.data.get("amount") or 0) / s / n)
        elif ev.type == "self.died" and st.exp is not None and (s := self.span(ev.agent)):
            self.ladder.gained(zone, -st.exp / s / n)     # st.exp is already halved: what remains = what was lost
        bad = False
        if ev.type == "self.died":
            bad = self.ladder.died(zone, weakest)
        elif ev.type == "self.fled":
            bad = self.ladder.fled(zone, weakest)
        elif ev.type == "prompt" and ev.data.get("hp") is not None and st.hp_max:
            bad = self.ladder.hurt(zone, st.pct(ev.data["hp"], st.hp_max), weakest)
        elif ev.type == "combat.death" and ev.agent == self.leader:
            self.ladder.killed(zone)
        if bad and zone == rally_zone:
            self._move_on(f"zone {zone} is too dangerous")
        levels = tuple(sorted(self.levels()))
        if not self._ladder_started and len(levels) == len(self.roster) and self.ladder.hub() is not None:
            self._ladder_started = True                    # everyone's level and the leader's room known
            self._start_here()
        if levels != self._levels:
            first = False
            grew = bool(self._levels) and min(levels or (0,)) > min(self._levels)
            self._levels = levels
            if grew or first:
                self._move_on("we gained a level" if grew else "levels known")

    def _rally_zone(self) -> int | None:
        vs = self.memoria.graph.rooms_named(self.rally) if self.rally else []
        return self.memoria.world.rooms[vs[0]].zone if vs else None

    def save(self) -> None:
        if self.save_path is not None:
            import json
            data: dict[str, Any] = {"rally": self.rally}
            if self.ladder is not None:
                data.update(circuit=self.circuit, ladder=self.ladder.to_json())
            self.save_path.write_text(json.dumps(data))

    def load(self) -> None:
        if self.save_path is not None and self.save_path.exists():
            import json
            data = json.loads(self.save_path.read_text())
            if self.ladder is not None:
                self.ladder.from_json(data.get("ladder", {}))
                if data.get("circuit"):
                    self.circuit = list(data["circuit"])
            rally = data.get("rally")
            if rally and (not self.circuit or rally in self.circuit):
                self.rally = rally

    def register(self, name: str, state: Any) -> None:
        self.states[name] = state

    def register_items(self, name: str, items: Any) -> None:
        """A member's item judgment (anima.runtime.items.Items), so others can ask what it would wear."""
        self.items[name] = items

    def claim(self, name: str, thing: str, copies: int = 1) -> bool:
        """I go for this thing on the floor of my room. False if as many others went for it in the last
        CLAIM_S as there are copies (round 69: two or three members sent `get sleeves` for one pair)."""
        v = self.vnum(name)
        if v is None:
            return True
        now = self.clock()
        who = {n: t for n, t in self.claims.get((v, thing), {}).items() if now - t < CLAIM_S}
        if name not in who and len(who) >= copies:
            return False
        who[name] = now
        self.claims[(v, thing)] = who
        return True

    # ------------------------------------------------------------ facts
    def vnum(self, name: str) -> int | None:
        st = self.states.get(name)
        if st is None or not st.in_game:
            return None
        return self.memoria.locator(name).vnum

    def online(self) -> list[str]:
        return [n for n in self.roster if n in self.states and self.states[n].in_game]

    def in_room_with(self, agent: str) -> list[str]:
        """Members in my room: same estimated vnum AND actually seen in my room listing.
        (Position estimates can be wrong; the server only lets us give to / heal whom it shows us.)"""
        v = self.vnum(agent)
        me = self.states.get(agent)
        seen = {o.get("text", "").split(" ", 1)[0] for o in (me.occupants if me else [])}
        return [n for n in self.online()
                if v is not None and self.vnum(n) == v and (n == agent or n in seen)]

    # ------------------------------------------------------------ camp (D30)
    def camp(self, agent: str) -> str | None:
        """The sentry if the members in my room are camping, else None.

        A camp starts when one member's rest need reaches camp_start and ends when nobody in the
        room needs more than camp_end (not "everyone full": that kept six asleep for one member's mana). Then everyone recovers at once instead of one after another. The sentry,
        the member with the most hit points when the camp starts, rests awake; the rest sleep."""
        here = self.in_room_with(agent)
        key = self.vnum(agent)
        if key is None or not here:
            return None
        needs = {n: getattr(self.states[n], "rest_need", 0.0) for n in here}
        if key not in self._camps:
            if max(needs.values()) < self.camp_start:
                return None
            self._camps[key] = None
        elif max(needs.values()) <= self.camp_end:
            del self._camps[key]
            return None
        if self._camps[key] not in here:
            self._camps[key] = max(here, key=lambda n: (self.states[n].hp or 0, n))
        return self._camps[key]

    # ------------------------------------------------------------ shopping trip (D32)
    def trip_wants(self) -> list[int]:
        out: list[int] = []
        for n in self.online():
            for v in getattr(self.states[n], "shop_wants", []) or []:
                if v not in out:
                    out.append(v)
        return out

    def start_trip(self, agent: str) -> None:
        if agent != self.leader or self.trip is not None:
            return
        stops = self.trip_wants()
        if stops:
            self.trip = {"stops": stops, "visited": [], "arrived": None, "started": self.clock()}

    def _end_trip(self) -> None:
        """D33: after the town run, hunt in the best fitting zone other than the one we came from."""
        self.trip = None
        self._last_trip_t = self.clock()
        if self.ladder is None:                    # a fixed circuit: back to where we were
            return
        self._refresh_circuit()
        others = [r for r in self.circuit if r != self.rally]
        if others and self.clock() >= self.override_until:
            self.rally = others[0]
            lead = self.states.get(self.leader)
            if lead is not None:
                lead.marks["rally_changed"] = self.clock()
            self.save()

    def trip_stop(self) -> int | None:
        """The shop room the leader should walk to next (nearest first), or None. Cached 2 s."""
        t = self.trip
        if t is None:
            return None
        cached = t.get("_stop")
        if cached and self.clock() - cached[0] < 2.0 and cached[1] not in t["visited"]:
            return cached[1]
        stop = self._trip_stop(t)
        if self.trip is not None:
            t["_stop"] = (self.clock(), stop)
        return stop

    def _trip_stop(self, t: dict[str, Any]) -> int | None:
        if self.clock() - t["started"] > self.trip_timeout:
            self._end_trip()
            return None
        for v in self.trip_wants():                # wants that came up since the start join the trip
            if v not in t["stops"]:
                t["stops"].append(v)
        left = [v for v in t["stops"] if v not in t["visited"]]
        if not left:
            self._end_trip()
            return None
        here = self.vnum(self.leader)
        if here in left:
            return here
        if here is None:
            return left[0]
        cond = self.memoria.conditions(has_light=self._leader_lit(), agent=self.leader)

        def steps(v: int) -> int:
            p = self.memoria.graph.path(here, v, cond, max_rooms=4000)
            return len(p) if p is not None else 10 ** 6
        best = min(left, key=steps)
        if steps(best) >= 10 ** 6:                 # none reachable from here: give up the trip
            self._end_trip()
            return None
        return best

    def shop_busy(self, agent: str) -> bool:
        """Someone here is still buying or selling, or a member is training in a guild (out of the room)."""
        now = self.clock()
        return any(now - self.states[n].marks.get("shopping", -1e9) < 6 for n in self.in_room_with(agent)) or \
            any(now - self.states[n].marks.get("training", -1e9) < 6 for n in self.online())

    def trip_progress(self, agent: str) -> None:
        """Leader at a stop: stay until nobody has bought or sold for a few seconds, then move on."""
        t = self.trip
        if agent != self.leader or t is None:
            return
        here = self.vnum(agent)
        if here not in t["stops"] or here in t["visited"]:
            return
        now = self.clock()
        if t["arrived"] is None:
            t["arrived"] = now
        elif now - t["arrived"] >= 8 and not self.shop_busy(agent):
            t["visited"].append(here)
            t["arrived"] = None

    def sleepers_here(self, agent: str) -> list[str]:
        return [n for n in self.in_room_with(agent) if n != agent and self.states[n].position == "sleeping"]

    def is_party_member(self, name: str) -> bool:
        return name in self.roster

    def lost_secs(self) -> float:
        """How long the member farthest from the leader has been apart (0 if all together)."""
        now = self.clock()
        lv = self.vnum(self.leader)
        worst = 0.0
        for n in self.online():
            if n == self.leader:
                continue
            apart = lv is None or self.vnum(n) != lv
            if apart:
                since = self._apart_since.setdefault(n, now)
                worst = max(worst, now - since)
            else:
                self._apart_since.pop(n, None)
        return worst

    def member(self, name: str, me: str) -> dict[str, Any]:
        st = self.states[name]
        k = self.memoria.knowledge
        kinds = {(k.item(t) or _NoObj).type for t in st.inventory}
        return {"name": name, "hp_pct": st.pct(st.hp, st.hp_max), "hp_max": st.hp_max, "is_self": name == me,
                "thirsty": st.thirsty, "hungry": st.hungry, "has_drink": "drinkcon" in kinds,
                "has_food": "food" in kinds, "class": self.classes.get(name, ""),
                "being_hit": self._being_hit(name, me)}

    def _being_hit(self, name: str, observer: str) -> bool:
        now = self.clock()
        own = self.states[name]
        if name == observer:
            return own.fighting(now)
        seen = self.states.get(observer)
        return seen is not None and now - seen.hit_seen.get(name, -1e9) < 4.0

    # ------------------------------------------------------------ PartyView
    def value(self, f: str, agent: str) -> Any:
        if agent not in self.roster:
            return {"leader": agent, "is_leader": True, "size": 1, "here": 1, "all_here": True,
                    "lost_secs": 0, "resting": False, "rally": None, "role": "", "leader_room": None,
                    "with_leader": True, "leader_reachable": True, "wait_vnum": None, "leader_vnum": None, "min_mv_pct": 100, "unseen_here": 0, "all_following": True, "following": False, "in_group": False, "leader_in_group": True, "online": 1,
                    "thirsty_in_room": [], "hungry_in_room": [], "camping": False, "sentry": None,
                    "is_sentry": False, "trip_stop": None, "trip_wanted": False, "shop_busy": False}.get(f)
        here = self.in_room_with(agent)
        st = self.states.get(agent)
        if f == "leader":
            return self.leader
        if f == "is_leader":
            return agent == self.leader
        if f == "size":
            return len(self.roster)
        if f == "online":
            return len(self.online())
        if f == "unseen_here":
            v = self.vnum(agent)
            return sum(1 for n in self.online() if n != agent and v is not None and self.vnum(n) == v) \
                - (len(here) - 1)
        if f == "min_mv_pct":
            return min((self.states[n].pct(self.states[n].mv, self.states[n].mv_max) for n in self.online()),
                       default=100.0)
        if f == "here":
            return len(here)
        if f == "all_here":
            return len(here) == len(self.online()) and agent in here
        if f == "lost_secs":
            return self.lost_secs()
        if f == "resting":
            return any(self.states[n].position in ("resting", "sleeping") for n in here if n != agent)
        if f == "trip_stop":
            return self.trip_stop()
        if f == "trip_wanted":
            return self.trip is None and self.clock() - self._last_trip_t >= self.trip_cooldown and bool(self.trip_wants())
        if f == "shop_busy":
            return self.shop_busy(agent)
        if f == "camping":
            return self.camp(agent) is not None
        if f == "sentry":
            return self.camp(agent)
        if f == "is_sentry":
            return self.camp(agent) == agent
        if f == "rally":
            return self.rally
        if f == "role":
            return ",".join(self.roles.get(agent, []))
        if f == "leader_room":
            ls = self.states.get(self.leader)
            return ls.room.get("name") if ls is not None and ls.in_game and not ls.room_dark else None
        if f == "leader_vnum":
            return self.vnum(self.leader)
        if f == "leader_reachable":
            return self._toward_leader(agent)[0]
        if f == "wait_vnum":
            return self._toward_leader(agent)[1]
        if f == "with_leader":           # same room by estimate AND the leader is actually seen there
            return agent == self.leader or self.leader in here
        if f == "all_following":
            return all(self.states[n].following == self.leader and self.states[n].in_group
                       for n in self.online() if n != self.leader)
        if f == "following":
            return st is not None and st.following == self.leader
        if f == "in_group":
            return st is not None and st.in_group
        if f == "leader_in_group":
            ls = self.states.get(self.leader)
            return ls is not None and ls.in_group
        if f == "thirsty_in_room":
            return [n for n in here if self.states[n].thirsty]
        if f == "hungry_in_room":
            return [n for n in here if self.states[n].hungry]
        raise KeyError(f)

    def _toward_leader(self, agent: str) -> tuple[bool, int | None]:
        """Can this member walk to the leader? If an exit only they are kept from (a guild guard) is
        in the way: no, and the room before it, where they wait (the guild's entrance)."""
        me, there = self.memoria.locator(agent).vnum, self.vnum(self.leader)
        if me is None or there is None or me == there:
            return True, None
        st = self.states.get(agent)
        lit = bool(st and st.has_light)
        mine = self.memoria.conditions(has_light=lit, agent=agent)
        if self.memoria.graph.path(me, there, mine, max_rooms=4000) is not None:
            return True, None
        free = self.memoria.conditions(has_light=lit)
        steps = self.memoria.graph.path(me, there, free, max_rooms=4000)
        v = me
        for d in steps or []:
            if (v, d) in mine.blocked:
                return False, v
            v = self.memoria.graph.exits_from(v, free)[d]
        return False, None

    def members_in_room(self, agent: str) -> list[dict[str, Any]]:
        if agent not in self.roster:
            return []
        return [self.member(n, agent) for n in self.in_room_with(agent)]

    def member_with_role(self, role: str, agent: str) -> str | None:
        for n in self.in_room_with(agent):
            if n != agent and role in self.roles.get(n, []):
                return n
        return None

    def has_role(self, role: str, agent: str) -> bool:
        return role in self.roles.get(agent, [])


class _NoObj:
    type = None
