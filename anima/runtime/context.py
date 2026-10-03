"""What a Sigil expression sees (names, functions) and what its actions do.

Names and functions here are exactly the ones in anima/sigil/api.py. Actions turn into commands
with a Source (D11); movement goes through Memoria. Nothing here knows any class, role or
character: those come from packages.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from anima.memoria import Memoria
from anima.protocol.commands import Source
from anima.runtime.state import AgentState, NEVER
from anima.sigil import api
from anima.sigil.expr import Expr, evaluate
from anima.sigil.program import Program

PROF_RANK = {"not learned": 0, "awful": 1, "bad": 2, "poor": 3, "average": 4, "fair": 5, "good": 6,
             "very good": 7, "superb": 8}
SAME_COMMAND_GAP_S = 2.0
MOVE_WAIT_S = 5.0

SendFn = Callable[[str, Source, int], None]       # text, source, priority


CHASE_S = 20.0                # a fled mob is chased this soon after it left, or not at all


class PartyView(Protocol):
    """What the party layer exposes to one agent (M7). SoloParty is the default."""
    def value(self, field: str, agent: str) -> Any: ...
    def members_in_room(self, agent: str) -> list[dict[str, Any]]: ...
    def is_party_member(self, name: str) -> bool: ...


class SoloParty:
    def value(self, field: str, agent: str) -> Any:
        return {"leader": agent, "is_leader": True, "size": 1, "here": 1, "all_here": True, "lost_secs": 0,
                "resting": False, "rally": None, "role": "", "leader_room": None, "leader_vnum": None,
                "with_leader": True, "leader_reachable": True, "wait_vnum": None, "following": False, "in_group": False, "online": 1, "min_mv_pct": 100, "unseen_here": 0, "all_following": True,
                "thirsty_in_room": [], "hungry_in_room": [], "camping": False, "sentry": None,
                "is_sentry": False, "trip_stop": None, "trip_wanted": False, "shop_busy": False}.get(field)

    def sleepers_here(self, agent: str) -> list[str]:
        return []

    def members_in_room(self, agent: str) -> list[dict[str, Any]]:
        return []

    def is_party_member(self, name: str) -> bool:
        return False


class ActionError(Exception):
    pass


@dataclass
class Context:
    agent: str
    program: Program
    state: AgentState
    memoria: Memoria
    send_fn: SendFn
    clock: Callable[[], float]
    party: PartyView = field(default_factory=SoloParty)
    answers: dict[str, Any] = field(default_factory=dict)
    rng: random.Random = field(default_factory=lambda: random.Random(7))
    behavior: str | None = None
    behavior_since: float = 0.0
    task_name: str | None = None
    # per-evaluation bindings
    event: Any = None
    target: dict[str, Any] | None = None
    source: Source | None = None
    priority: int = 1
    # bookkeeping
    move_pending_until: float = NEVER
    toggle_pending: dict[str, float] = field(default_factory=dict)
    last_sent: dict[str, float] = field(default_factory=dict)
    task_hooks: dict[str, Callable[..., Any]] = field(default_factory=dict)

    # ------------------------------------------------------------ names
    def resolve(self, path: str) -> Any:
        root, _, rest = path.partition(".")
        s, now = self.state, self.clock()
        if root == "self":
            return self._self(rest, s, now)
        if root == "room":
            return self._room(rest, s)
        if root == "world":
            return {"night": self.memoria.night}.get(rest)
        if root == "party":
            if rest == "members_in_room":
                return self.party.members_in_room(self.agent) or [self.self_member()]
            return self.party.value(rest, self.agent)
        if root == "policy":
            return self.program.policies.get(rest)
        if root == "answer":
            if rest in self.answers:
                return self.answers[rest]
            ask = self.program.asks.get(rest)
            return ask.spec.get("default") if ask else None
        if root == "event":
            ev = self.event
            if ev is None:
                return None
            if rest == "type":
                return ev.type
            if rest == "agent":
                return ev.agent
            return ev.data.get(rest)
        if root == "target":
            return (self.target or {}).get(rest)
        raise KeyError(path)

    def self_member(self) -> dict[str, Any]:
        s = self.state
        return {"name": self.agent, "hp_pct": s.pct(s.hp, s.hp_max), "hp_max": s.hp_max, "is_self": True}

    def _self(self, k: str, s: AgentState, now: float) -> Any:
        simple = {"name": self.agent, "hp": s.hp, "hp_max": s.hp_max, "mp": s.mp, "mp_max": s.mp_max, "mv": s.mv,
                  "mv_max": s.mv_max, "level": s.level, "gold": s.gold, "practices": s.practices,
                  "position": s.position, "hungry": s.hungry, "thirsty": s.thirsty, "has_light": s.has_light,
                  "in_game": s.in_game, "inventory": s.inventory, "equipment": [e.get("text") for e in s.equipment]}
        if k in simple:
            return simple[k]
        return {
            "hp_pct": lambda: s.pct(s.hp, s.hp_max), "mp_pct": lambda: s.pct(s.mp, s.mp_max),
            "mv_pct": lambda: s.pct(s.mv, s.mv_max), "fighting": lambda: s.fighting(now),
            "secs_since_fight": lambda: now - s.last_fight_t, "secs_since_kill": lambda: now - s.last_kill_t,
            "secs_since_fled": lambda: now - s.last_fled_t,
            "chase_dir": lambda: s.chase_dir if now - s.chase_t < CHASE_S else None, "secs_since_alert": lambda: now - s.last_alert_t,
            "secs_in_behavior": lambda: now - self.behavior_since if self.behavior else 0,
            "behavior": lambda: self.behavior, "task": lambda: self.task_name,
        }[k]()

    def _room(self, k: str, s: AgentState) -> Any:
        loc = self.memoria.locator(self.agent)
        if k == "vnum":
            return loc.vnum
        if k == "zone":
            return self.memoria.world.rooms[loc.vnum].zone if loc.vnum is not None else None
        if k == "name":
            return s.room.get("name")
        if k == "dark":
            return s.room_dark
        if k == "occupants":
            return [o.get("text", "") for o in s.occupants]
        if k == "strangers":
            return [o.get("text", "") for o in s.occupants
                    if not self.party.is_party_member(o.get("text", "").split(" ", 1)[0])
                    and not any(h.startswith("my_group") for h in o.get("hints", []))]
        if k == "shop":                     # a shopkeeper's room (world data)
            return self.items.shop_here() is not None
        if k == "unidentified":            # strangers the world data cannot name (a question for the Animus)
            strangers = self._room("strangers", s)
            return [o.get("text", "") for o in s.occupants if o.get("text", "") in strangers
                    and self.memoria.knowledge.identify(o).kind == "unknown"]
        if k == "objects":
            return [o.get("text", "") for o in s.room.get("objects", [])]
        if k == "exits":
            return [e["dir"] for e in s.room.get("exits", [])]
        if k == "has_fountain":
            return any((self.memoria.knowledge.ground_item(o.get("text", "")) or _NoObj).type == "fountain"
                       for o in s.room.get("objects", []))
        raise KeyError(k)

    # ------------------------------------------------------------ evaluation
    def eval(self, e: Expr, *, event: Any = None, target: dict[str, Any] | None = None) -> Any:
        old = (self.event, self.target)
        if event is not None:
            self.event = event
        if target is not None:
            self.target = target
        try:
            return evaluate(e, self.resolve, self.functions)
        finally:
            self.event, self.target = old

    def run_actions(self, exprs: list[Expr], source: Source, priority: int = 1, *,
                    event: Any = None, target: dict[str, Any] | None = None) -> None:
        old = (self.source, self.priority)
        self.source, self.priority = source, priority
        try:
            for e in exprs:
                self.eval(e, event=event, target=target)
        finally:
            self.source, self.priority = old

    @property
    def functions(self) -> dict[str, Callable[..., Any]]:
        f = getattr(self, "_fns", None)
        if f is None:
            f = self._fns = self._build_functions()
        return f

    def _build_functions(self) -> dict[str, Callable[..., Any]]:
        def lin(x, lo, hi):
            x = x if x is not None else 0
            if hi == lo:
                return 1.0 if x >= hi else 0.0
            return max(0.0, min(1.0, (x - lo) / (hi - lo)))

        fns: dict[str, Callable[..., Any]] = {
            "linear": lin,
            "inverse_linear": lambda x, lo, hi: 1.0 - lin(x, lo, hi),
            "step": lambda x, t: 1.0 if x is not None and x >= t else 0.0,
            "below": lambda x, t: 1.0 if x is not None and x < t else 0.0,
            "window": lambda x, lo, hi: 1.0 if x is not None and lo <= x < hi else 0.0,
            "bool": lambda x: 1.0 if x else 0.0,
            "min": lambda a, b: min(a, b), "max": lambda a, b: max(a, b), "abs": abs,
            "clamp": lambda x, lo, hi: max(lo, min(hi, x)),
            "len": lambda x: len(x) if x is not None else 0,
            "contains": _contains,
            "any_contains": lambda items, words: any(_contains(i, w) for i in (items or []) for w in (words or [])),
            "knows": self._knows, "proficiency": self._proficiency,
            "first_known": lambda skills: next((sk for sk in skills if self._knows(sk)), None),
            "pick_target": self._pick_target,
            "path_len": self._path_len, "in_zone_of": self._in_zone_of,
            "secs_since": lambda key: self.clock() - self.state.marks.get(key, NEVER),
            "if_else": lambda c, a, b: a if c else b,
            "item_of": self._inventory_of,
            "item_count": lambda kind: sum(1 for t in self.state.inventory
                                           if (self.memoria.knowledge.item(t) or _NoObj).type == kind),
            "surplus_item": self._surplus_item,
            "member_with_role": lambda role: getattr(self.party, "member_with_role", lambda r, a: None)(role, self.agent),
            "has_role": lambda role: getattr(self.party, "has_role", lambda r, a: False)(role, self.agent),
            "has_item": lambda kind: self._inventory_of(kind) is not None,
            "next_skill": self._next_skill,
            "practices_spare": self._practices_spare,
            "guild_door": self._guild_door,
            "train_wanted": self._train_wanted,
            "upgrade_item": lambda: self.items.upgrade_item(),
            "gear_gift": lambda: self.items.gear_gift(),
            "sellable_here": lambda keep: self.items.sellable_here(keep),
            "buy_here": lambda reserve: self.items.buy_here(reserve),
            "list_needed": lambda reserve: self.items.list_needed(reserve),
            "pickup_item": lambda min_cost: self.items.pickup_item(min_cost),
        }
        for name, fn in {
            "send": self.a_send, "attack": self.a_attack, "use": self.a_use, "cast": self.a_cast,
            "flee": lambda: self.cmd("flee"), "rest": self.a_rest, "sleep": self.a_sleep, "stand": self.a_stand,
            "wake": self.a_wake, "wake_party": self.a_wake_party, "eat": self.a_eat, "drink": self.a_drink,
            "practice": lambda skill: self.cmd(f"practice {skill}"),
            "go_to": self.a_go_to, "go_back": self.a_go_back, "chase": self.a_chase, "explore": self.a_explore,
            "ensure_toggle": self.a_toggle, "set_wimpy": self.a_wimpy,
            "start_task": lambda name: self.task_hooks["start"](name),
            "next_rally": lambda: getattr(self.party, "next_rally", lambda a: None)(self.agent),
            "mark": lambda key: self.state.marks.__setitem__(key, self.clock()),
            "wait": lambda secs: self.task_hooks.get("wait", lambda s: None)(secs),
            "wear_upgrade": lambda: self.items.wear_upgrade(),
            "start_trip": lambda: getattr(self.party, "start_trip", lambda a: None)(self.agent),
            "trip_progress": lambda: getattr(self.party, "trip_progress", lambda a: None)(self.agent),
        }.items():
            fns[name] = fn
        assert set(fns) == set(api.FUNCS_BY_NAME), set(fns) ^ set(api.FUNCS_BY_NAME)
        return fns

    # ------------------------------------------------------------ knowledge
    def _knows(self, skill: str) -> bool:
        return self._proficiency(skill) > 0

    def _proficiency(self, skill: str) -> int:
        return PROF_RANK.get(self.state.skills.get(skill.replace("_", " "), "not learned"), 0)

    def _surplus_item(self, keep: list[str]) -> str | None:
        """What to junk: first what is of no kept type, then a kept type beyond policy.inv_keep_each of it
        (two lights are a spare; ten torches from a buying loop are a full bag)."""
        k = self.memoria.knowledge
        each = int(self.program.policies.get("inv_keep_each", 2) or 2)
        kept: dict[str, int] = {}
        extra = None
        for text in self.state.inventory:
            if text.lower() in self.state.undroppable:
                continue
            o = k.item(text)
            if o is None or o.type not in (keep or []):
                return k.item_keyword(text)
            kept[o.type] = kept.get(o.type, 0) + 1
            if kept[o.type] > each and extra is None:
                extra = k.item_keyword(text)
        return extra

    def _next_skill(self, plan: list[dict[str, Any]]) -> str | None:
        newer_attack_known = False
        for entry in plan or []:
            skill = str(entry.get("skill", "")).replace("_", " ")
            attack = bool(entry.get("attack"))
            known = self._knows(skill)
            if attack and newer_attack_known:
                continue
            if attack and known:
                newer_attack_known = True
            if skill in self.state.capped or skill not in self.state.skills:
                continue                     # capped, or not available at this level
            if self._proficiency(skill) < int(entry.get("target", 8)):
                return skill
        return None

    def _train_wanted(self) -> bool:
        """Practices to spend now (beyond the reserve) on a skill of the plan."""
        pol = self.program.policies
        return bool(pol.get("guild_room")) and self._practices_spare(pol.get("reserve_for")) > 0 \
            and self._next_skill(pol.get("skill_plan") or []) is not None

    def _guild_door(self) -> int | None:
        """The room outside my guild room (the first step from it toward where I am): the party's stop
        while I train inside, where a guard lets only my class through."""
        name = self.program.policies.get("guild_room")
        here = self.memoria.locator(self.agent).vnum
        rooms = self.memoria.graph.rooms_named(name) if name else []
        if not rooms or here is None:
            return None
        g = self.memoria.graph
        path = g.path(rooms[0], here, self.memoria.conditions(self.state.has_light), max_rooms=4000)
        if not path:
            return None
        return g.exits_from(rooms[0], self.memoria.conditions(self.state.has_light))[path[0]]

    def _practices_spare(self, reserve_for: dict[str, int] | None) -> int:
        keep = 2 if any(int(lv) == self.state.level + 1 for lv in (reserve_for or {}).values()) else 0
        return max(0, self.state.practices - keep)

    def _pick_target(self, targets: list[str], shun: list[str]) -> str | None:
        occ = [o for o in self.state.occupants if not self._is_player(o)]
        texts = [o.get("text", "") for o in self.state.occupants]
        if any(_contains(t, w) for t in texts for w in (shun or [])):
            return None
        for word in targets or []:
            if any(_contains(o.get("text", ""), word) for o in occ):
                return word
        return None

    def _is_player(self, occ: dict[str, Any]) -> bool:
        who = self.memoria.knowledge.identify(occ)
        return who.kind == "player" or self.party.is_party_member(occ.get("text", "").split(" ", 1)[0])

    def _path_len(self, room: str) -> int | None:
        vnum = self.memoria.locator(self.agent).vnum
        if vnum is None:
            return None
        if self.state.room.get("name") == room:
            return 0
        p = self.memoria.graph.path_to_name(vnum, room, self.memoria.conditions(self.state.has_light, agent=self.agent))
        return len(p) if p is not None else None

    def _in_zone_of(self, room: str) -> bool:
        vnum = self.memoria.locator(self.agent).vnum
        rooms = self.memoria.graph.rooms_named(room)
        return vnum is not None and bool(rooms) and self.memoria.world.rooms[vnum].zone in \
            {self.memoria.world.rooms[v].zone for v in rooms}

    # ------------------------------------------------------------ actions
    @property
    def items(self):
        it = self.__dict__.get("_items")
        if it is None:
            from anima.runtime.items import Items
            it = self.__dict__["_items"] = Items(self)
        return it

    def cmd(self, text: str, *, move: bool = False, force: bool = False) -> bool:
        now = self.clock()
        if not force and now - self.last_sent.get(text, NEVER) < SAME_COMMAND_GAP_S:
            return False
        if self.source is None:
            raise ActionError("action outside a do-list")
        word = text.strip().split(" ", 1)[0].lower()
        if word in api.FORBIDDEN_COMMANDS or set(text) & api.FORBIDDEN_CHARS:
            raise ActionError(f"forbidden command {text!r}")
        loops = getattr(self, "loops", None)
        if loops is not None and self.source.kind in ("behavior", "reflex", "task"):
            wait = loops.paused(self.source.id, text)
            if wait > 0:                       # this got the same no again and again: held back (runtime.loop)
                self.loop_blocked = (self.source.id, wait)
                return False
        self.last_sent[text] = now
        if move:
            self.move_pending_until = now + MOVE_WAIT_S
        self.send_fn(text, self.source, self.priority)
        return True

    def moving(self) -> bool:
        return self.clock() < self.move_pending_until

    def arrived(self) -> None:
        self.move_pending_until = NEVER

    def a_send(self, text: str) -> None:
        self.cmd(str(text))

    def a_attack(self, target: str | None) -> None:
        if target and not self.state.fighting(self.clock()):
            self.cmd(f"kill {target}")

    def a_use(self, skill: str, target: str | None = None) -> None:
        if skill:
            self.cmd(f"{skill} {target}".strip() if target else skill)

    def a_cast(self, spell: str | None, target: str | None = None) -> None:
        if spell:
            sp = spell.replace("_", " ")
            self.cmd(f"cast '{sp}' {target}".strip() if target else f"cast '{sp}'")

    def a_rest(self) -> None:
        """Rest awake. From sleep: wake first (rest is chosen over sleep only when sleeping is not safe,
        e.g. the camp's sentry, D30)."""
        if self.state.position == "sleeping":
            self.cmd("wake")
            self.cmd("rest")
        elif self.state.position != "resting":
            self.cmd("rest")

    def a_sleep(self) -> None:
        if self.state.position != "sleeping":
            self.cmd("sleep")

    def a_stand(self) -> None:
        if self.state.position == "sleeping":
            self.cmd("wake")
            self.cmd("stand")
        elif self.state.position in ("sitting", "resting"):
            self.cmd("stand")

    def a_wake(self, who: str | None = None) -> None:
        self.cmd(f"wake {who}" if who else "wake")

    def a_wake_party(self) -> None:
        """Wake every sleeping party member in my room (act.movement.c do_wake: only while I am awake)."""
        if self.state.position == "sleeping":
            self.cmd("wake")
        for name in self.party.sleepers_here(self.agent):
            self.cmd(f"wake {name}")

    def _inventory_of(self, kind: str) -> str | None:
        k = self.memoria.knowledge
        for text in self.state.inventory:
            o = k.item(text)
            if o and o.type == kind:
                return k.item_keyword(text)
        return None

    def a_eat(self) -> None:
        kw = self._inventory_of("food")
        if kw:
            self.cmd(f"eat {kw}")

    def a_drink(self) -> None:
        kw = self._inventory_of("drinkcon")
        if kw and self.clock() - self.state.marks.get("drink_empty", NEVER) < 60 and self.resolve("room.has_fountain"):
            kw = None                        # the container just said "It is empty."; use the fountain
        if kw:
            self.cmd(f"drink {kw}")
        elif self.resolve("room.has_fountain"):
            self.cmd("drink fountain")

    def a_go_to(self, room: str | int | None) -> str:
        """One step toward the room (name or vnum). Returns arrived | moving | no_path | lost."""
        if room is None or room == "":
            return "no_path"
        vnum = self.memoria.locator(self.agent).vnum
        by_vnum = isinstance(room, (int, float)) and not isinstance(room, bool)
        if (by_vnum and vnum == int(room)) or (not by_vnum and self.state.room.get("name") == room
                                               and not self.state.room_dark):
            return "arrived"
        if self.moving():
            return "moving"
        if vnum is None:
            self.a_explore()
            return "lost"
        cond = self.memoria.conditions(self.state.has_light, agent=self.agent)
        path = self.memoria.graph.path(vnum, int(room), cond) if by_vnum else \
            self.memoria.graph.path_to_name(vnum, room, cond)
        if not path:
            return "no_path" if path is None else "arrived"
        self.cmd(path[0], move=True, force=True)
        return "moving"

    def a_chase(self) -> str:
        """One room after a mob that fled from our fight, the way it went; not into a zone known to be
        above our level. The chase is then over (the hunt attacks it if it is there)."""
        d, self.state.chase_dir = self.state.chase_dir, None
        vnum = self.memoria.locator(self.agent).vnum
        if d is None or self.moving():
            return "no_path"
        ex = self.memoria.world.rooms[vnum].exits.get(d) if vnum in self.memoria.world.rooms else None
        if ex is not None and ex.to in self.memoria.world.rooms and \
                self.memoria.world.rooms[ex.to].zone in self.memoria.above_level_zones:
            return "no_path"
        self.cmd(d, move=True, force=True)
        return "moving"

    def a_go_back(self) -> str:
        if self.moving():
            return "moving"
        d = self.memoria.locator(self.agent).way_back()
        if d is None:
            return "no_path"
        self.cmd(d, move=True, force=True)
        return "moving"

    def a_explore(self) -> None:
        if self.moving():
            return
        loc = self.memoria.locator(self.agent)
        cond = self.memoria.conditions(self.state.has_light, agent=self.agent)
        rally = self.party.value("rally", self.agent)
        home_zone = None
        if rally:
            rooms = self.memoria.graph.rooms_named(rally)
            home_zone = self.memoria.world.rooms[rooms[0]].zone if rooms else None
        if loc.vnum is not None:
            came = loc.trail[-1].dir if loc.trail else None
            scores = self.memoria.graph.explore_scores(loc.vnum, cond, loc.seen, came, home_zone)
            if scores:
                best = min(scores.values())
                d = self.rng.choice(sorted(k for k, v in scores.items() if v == best))
                self.cmd(d, move=True, force=True)
            return
        exits = [e["dir"] for e in self.state.room.get("exits", []) if not e.get("closed")]
        if exits:
            self.cmd(self.rng.choice(exits), move=True, force=True)

    def a_toggle(self, name: str, value: Any) -> None:
        now = self.clock()
        if self.state.toggles.get(name) == value or now - self.toggle_pending.get(name, NEVER) < 5:
            return
        self.toggle_pending[name] = now
        self.cmd(name, force=True)

    def a_wimpy(self, hp: Any) -> None:
        mx = self.state.hp_max
        if not mx:
            return
        v = int(min(float(hp or 0), mx // 3))
        now = self.clock()
        if self.state.toggles.get("wimpy") == v or now - self.toggle_pending.get("wimpy", NEVER) < 5:
            return
        self.toggle_pending["wimpy"] = now
        self.cmd(f"toggle wimpy {v}", force=True)


class _NoObj:
    type = None


def _contains(haystack: Any, needle: Any) -> bool:
    if haystack is None or needle is None:
        return False
    if isinstance(haystack, str):
        return str(needle).lower() in haystack.lower()
    return any((isinstance(h, str) and str(needle).lower() == h.lower()) or h == needle for h in haystack)
