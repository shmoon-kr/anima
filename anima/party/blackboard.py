"""Party blackboard (D14): the party is one object with shared state, not tells between sessions.

Every member's runtime state and Memoria position are read directly. The party package declares
the roster, the leader, roles and the rally point as policies; this module only computes facts
from them (who is here, who is lost, who is thirsty) and assigns nothing on its own.
Game commands that the server needs for cooperation (follow, group, give) are sent by the
members' Sigils, based on these facts.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from anima.memoria import Memoria


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
    clock: Callable[[], float] = time.monotonic
    states: dict[str, Any] = field(default_factory=dict)        # name -> AgentState
    camp_start: float = 0.5                                     # D30: a member's rest need that makes the room camp
    _apart_since: dict[str, float] = field(default_factory=dict)
    _camps: dict[Any, str | None] = field(default_factory=dict)  # room -> sentry while that room camps

    @classmethod
    def from_policies(cls, memoria: Memoria, policies: dict[str, Any], clock: Callable[[], float] = time.monotonic):
        return cls(memoria, list(policies.get("roster", [])), policies.get("leader", ""),
                   {k: list(v) for k, v in (policies.get("roles") or {}).items()}, policies.get("rally"),
                   dict(policies.get("classes") or {}), list(policies.get("circuit") or []), clock=clock,
                   camp_start=float(policies.get("camp_start", 0.5)))

    def next_rally(self, agent: str) -> str | None:
        """The leader moves the party's hunting ground to the next place in the circuit."""
        if agent != self.leader or not self.circuit:
            return self.rally
        i = self.circuit.index(self.rally) if self.rally in self.circuit else -1
        self.rally = self.circuit[(i + 1) % len(self.circuit)]
        self.save()
        return self.rally

    def save(self) -> None:
        if self.save_path is not None:
            import json
            self.save_path.write_text(json.dumps({"rally": self.rally}))

    def load(self) -> None:
        if self.save_path is not None and self.save_path.exists():
            import json
            rally = json.loads(self.save_path.read_text()).get("rally")
            if rally and (not self.circuit or rally in self.circuit):
                self.rally = rally

    def register(self, name: str, state: Any) -> None:
        self.states[name] = state

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
        room needs rest. Then everyone recovers at once instead of one after another. The sentry,
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
        elif max(needs.values()) <= 0.0:
            del self._camps[key]
            return None
        if self._camps[key] not in here:
            self._camps[key] = max(here, key=lambda n: (self.states[n].hp or 0, n))
        return self._camps[key]

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
                    "with_leader": True, "leader_vnum": None, "min_mv_pct": 100, "unseen_here": 0, "all_following": True, "following": False, "in_group": False, "online": 1,
                    "thirsty_in_room": [], "hungry_in_room": [], "camping": False, "sentry": None,
                    "is_sentry": False}.get(f)
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
        if f == "with_leader":           # same room by estimate AND the leader is actually seen there
            return agent == self.leader or self.leader in here
        if f == "all_following":
            return all(self.states[n].following == self.leader and self.states[n].in_group
                       for n in self.online() if n != self.leader)
        if f == "following":
            return st is not None and st.following == self.leader
        if f == "in_group":
            return st is not None and st.in_group
        if f == "thirsty_in_room":
            return [n for n in here if self.states[n].thirsty]
        if f == "hungry_in_room":
            return [n for n in here if self.states[n].hungry]
        raise KeyError(f)

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
