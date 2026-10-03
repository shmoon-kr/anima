"""One agent's body state, built only from protocol events (no server text here)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from anima.protocol.envelope import SELF, Event

FIGHT_WINDOW_S = 6.0
NEVER = -1e9
BUY_ANSWER_S = 3.0      # a tell this soon after my `buy` is the keeper answering it


def _same(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


@dataclass
class AgentState:
    name: str
    hp: int | None = None
    hp_max: int | None = None
    mp: int | None = None
    mp_max: int | None = None
    mv: int | None = None
    mv_max: int | None = None
    level: int = 0
    gold: int = 0
    practices: int = 0
    position: str = "standing"
    rest_need: float = 0.0          # 0..1, strongest need among `camp: true` behaviors (party camp, D30)
    shop_wants: list = field(default_factory=list)   # shop rooms (and the fountain) worth a trip for me (D32, D33)
    thirsty_since: float = NEVER
    exp: int | None = None          # total experience: from score, plus gains, halved by a death (fight.c:323)
    hungry: bool = False
    thirsty: bool = False
    has_light: bool = False
    in_game: bool = False
    skills: dict[str, str] = field(default_factory=dict)
    toggles: dict[str, Any] = field(default_factory=dict)
    inventory: list[str] = field(default_factory=list)
    equipment: list[dict[str, str]] = field(default_factory=list)
    room: dict[str, Any] = field(default_factory=dict)
    room_dark: bool = False
    last_fight_t: float = NEVER
    last_kill_t: float = NEVER
    last_fled_t: float = NEVER
    last_prompt_t: float = NEVER
    shop_list: list = field(default_factory=list)   # the last `list` answer: [{text, price, ...}] in order
    shop_list_room: str | None = None               # the room it was given in (by name)
    fled_who: str | None = None          # a mob that panicked while we fought it ...
    fled_seen_t: float = NEVER
    chase_dir: str | None = None         # ... and the way it went (chase it one room)
    chase_t: float = NEVER
    last_alert_t: float = NEVER
    last_died_t: float = NEVER
    marks: dict[str, float] = field(default_factory=dict)
    capped: set[str] = field(default_factory=set)        # skills the guildmaster won't raise further
    following: str | None = None                         # whom the server makes us follow
    in_group: bool = False
    group_members: list[str] = field(default_factory=list)
    drink_empty_t: float = NEVER
    hit_seen: dict[str, float] = field(default_factory=dict)
    undroppable: set[str] = field(default_factory=set)       # item texts the server will not let go   # victim name -> last time we saw them hit
    last_practice: str | None = None
    last_seq: int = 0
    item_type: Callable[[str], str | None] = lambda _text: None    # world knowledge: text -> light/food/...
    # world knowledge: does this occupant line show the creature named `who` ("the mountain lion")?
    is_occupant: Callable[[dict, str], bool] = lambda _occ, _who: False
    is_ally: Callable[[str], bool] = lambda _name: False      # party member (from the party layer)

    # ------------------------------------------------------------ derived
    def pct(self, cur: int | None, mx: int | None) -> float:
        if cur is None or not mx:
            return 100.0
        return max(0.0, min(100.0, cur * 100.0 / mx))

    def fighting(self, now: float) -> bool:
        return now - self.last_fight_t < FIGHT_WINDOW_S

    @property
    def occupants(self) -> list[dict[str, Any]]:
        return self.room.get("occupants", [])

    # ------------------------------------------------------------ events
    _now: float = 0.0
    last_buy_t: float = -1e9
    obj_vnum: dict[str, int] = field(default_factory=dict)  # a name → the object it is, when the server says

    def _learn_vnums(self, items: list) -> None:
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("vnum"), int) and it.get("text"):
                self.obj_vnum[it["text"]] = it["vnum"]

    def on_event(self, ev: Event, now: float) -> None:
        self._now = now
        self.last_seq = ev.seq or self.last_seq
        t, d = ev.type, ev.data
        if t == "command.refused" and d.get("reason") in ("resting", "sitting", "sleeping"):
            # "Nah... You feel too relaxed to do that.." says where we are: after a reconnect nothing
            # else does, and every behavior's stand() trusts this
            self.position = d["reason"]
        if t == "shop.list":
            self.shop_list, self.shop_list_room = list(d.get("items", [])), self.room.get("name")
        elif t == "shop.result" and d.get("ok", True) or t == "comm.tell" and now - self.last_buy_t < BUY_ANSWER_S:
            # the stock changed (a sale adds a line, a purchase can take one) or the keeper said "try
            # list!": the numbers of the old list point at other things now (round 17: '#23' bought a
            # chain mail shirt, then "Haven't got that on storage" ten times)
            self.shop_list, self.shop_list_room = [], None
        if t == "prompt":
            self.last_prompt_t = now
            self.hp, self.mp, self.mv = d.get("hp"), d.get("mp"), d.get("mv")
            for cur, attr in ((self.hp, "hp_max"), (self.mp, "mp_max"), (self.mv, "mv_max")):
                if cur is not None and (getattr(self, attr) is None or cur > getattr(self, attr)):
                    setattr(self, attr, cur)
            self.in_game = True
        elif t == "char.vitals_max":
            self.hp_max, self.mp_max, self.mv_max = d.get("hp"), d.get("mp"), d.get("mv")
        elif t == "exp.gain":
            if self.exp is not None:
                self.exp += int(d.get("amount") or 0)
        elif t == "char.score":
            self.level = d.get("level", self.level)
            self.gold = d.get("gold", self.gold)
            self.practices = d.get("practices", self.practices)
            if d.get("exp") is not None:
                self.exp = d["exp"]
        elif t == "char.skills":
            self.skills.update(d.get("skills", {}))
        elif t == "char.practiced":
            if d.get("result") == "improved":
                self.practices = max(0, self.practices - 1)
            elif d.get("result") == "maxed" and self.last_practice:
                self.capped.add(self.last_practice)
            elif d.get("result") == "cannot":
                if d.get("reason") == "no_practices":
                    self.practices = 0
                elif self.last_practice:
                    self.capped.add(self.last_practice)     # not teachable here / not known at this level
        elif t == "command.sent":
            text = d.get("text", "")
            if text.startswith("buy "):
                self.last_buy_t = now
            if text.startswith("practice "):
                self.last_practice = text[len("practice "):].strip()
        elif t == "level.up":
            self.level += d.get("levels", 1)
        elif t == "condition":
            if "hungry" in d:
                self.hungry = d["hungry"]
            if "thirsty" in d:
                if d["thirsty"] and not self.thirsty:
                    self.thirsty_since = now
                self.thirsty = d["thirsty"]
        elif t == "position":
            self.position = d["position"]
        elif t == "toggle.state":
            self.toggles[d["name"]] = d["value"]
        elif t == "items.inventory":
            self.inventory = [i["text"] for i in d.get("items", []) for _ in range(i.get("count", 1))]
            self._learn_vnums(d.get("items", []))
        elif t == "items.equipment":
            self.equipment = d.get("slots", [])
            self._learn_vnums(self.equipment)
            self.has_light = any(s.get("slot") == "used as light" for s in self.equipment)
        elif t == "items.light_out" and d.get("who") == SELF:
            self.has_light = False
        elif t == "items.used":
            self._learn_vnums([d])
            self._item_used(d)
        elif t in ("items.got", "items.received"):
            self.inventory.append(d.get("text", ""))
            if t == "items.got" and not d.get("from"):
                self._room_object(d.get("text", ""), -1)          # taken from the floor
        elif t == "occupant.item" and not d.get("other"):
            # someone else took a thing off the floor, or put one down (Mundi): the room's list changes
            if d.get("action") == "get":
                self._room_object(d.get("text", ""), -1)
            elif d.get("action") == "drop":
                self._room_object(d.get("text", ""), +1)
        elif t == "items.failed" and d.get("action") == "get" and d.get("reason") == "not_here" and d.get("keyword"):
            # "You don't see a leggings here.": our picture of the floor is stale; forget what it named
            w = str(d["keyword"]).lower()
            self.room["objects"] = [o for o in self.room.get("objects", []) if w not in o.get("text", "").lower()]
        elif t == "items.cannot_drop":
            self.undroppable.add(d.get("text", "").lower())
        elif t == "items.gave":
            self._drop_item(d.get("text", ""))
        elif t == "room":
            self.room, self.room_dark = d, False
        elif t == "room.dark":
            self.room, self.room_dark = {"name": None, "occupants": [], "objects": [], "exits": []}, True
        elif t == "occupant.arrived":
            self.room.setdefault("occupants", []).append({"text": d.get("who", ""), "hints": [], "flags": []})
            if not self.is_ally(d.get("who", "")):
                self.last_alert_t = now                    # a stranger: not a party member arriving
        elif t == "occupant.left":
            if d.get("dir") and d.get("who") == self.fled_who and now - self.fled_seen_t < 10:
                self.chase_dir, self.chase_t, self.fled_who = d["dir"], now, None     # the mob that fled went that way
            self._drop_occupant(d.get("who", ""))
        elif t == "combat.hit":
            if d.get("victim") and d.get("victim") != SELF and d.get("severity", 0) > 0:
                self.hit_seen[d["victim"]] = now
            if SELF in (d.get("attacker"), d.get("victim")):
                self.last_fight_t = now
            if d.get("attacker") == SELF and self.position in ("sleeping", "resting", "sitting"):
                # a sleeper who is hit is set fighting without a word (fight.c:665 damage -> set_fighting,
                # POS_FIGHTING): no message says so, so until we strike the wake_when_hit reflex still
                # wakes us; once we strike, we are up whatever we last heard
                self.position = "standing"
        elif t == "combat.death":
            if now - self.last_fight_t < FIGHT_WINDOW_S:
                self.last_kill_t = now
                self.last_fight_t = NEVER
            self._drop_occupant(d.get("who", ""))
        elif t == "self.fled":
            self.last_fled_t = now
            self.last_fight_t = NEVER
        elif t == "self.died":
            self.last_died_t = now
            if self.exp is not None:
                self.exp -= self.exp // 2           # fight.c die(): gain_exp(ch, -(GET_EXP(ch) / 2))
            self.last_fight_t = NEVER
            self.in_game = False
            self.has_light = False
            self.following = None            # handler.c:1073-1077 extract_char: die_follower, leave_group
            self.in_group = False
        elif t == "position" and d.get("awakened_by"):
            self.last_alert_t = now
        elif t == "group.change":
            ev_, who = d.get("event"), d.get("who")
            if ev_ == "following":
                self.following = who if who else self.following   # "already following $M" names no one
            elif ev_ == "stopped_following":
                self.following = None
            elif ev_ in ("joined", "new_leader") and who in (self.name, SELF):
                self.in_group = True
            elif ev_ in ("left", "disbanded") and who == self.name:
                self.in_group = False
        elif t == "group.status":
            self.group_members = [m["name"] for m in d.get("members", [])]
            self.in_group = self.name in self.group_members
        elif t == "combat.flee_seen" and now - self.last_fight_t < 10:
            self.fled_who, self.fled_seen_t = d.get("who"), now
        elif t == "connection.in_game":
            self.in_game = True
            if d.get("how") == "entered":
                self.position = "standing"           # one comes into the game on one's feet
                # a fresh entry (after a reboot, a crash, a quit): no group, no one followed. A reconnect
                # keeps both. Else the leader thinks it leads a group that is gone and never forms one.
                self.in_group = False
                self.following = None
        elif t == "connection.closed":
            self.in_game = False

    def _room_object(self, text: str, n: int) -> None:
        """One more or fewer of a thing on the floor of our picture of the room (by its short text)."""
        objs = self.room.setdefault("objects", [])
        for o in objs:
            if o.get("text", "").lower().endswith(text.lower()) or text.lower() in o.get("text", "").lower():
                o["count"] = o.get("count", 1) + n
                if o["count"] <= 0:
                    objs.remove(o)
                return
        if n > 0 and text:
            objs.append({"text": text, "count": n})

    def _item_used(self, d: dict[str, Any]) -> None:
        action, text = d.get("action"), d.get("text", "")
        if action in ("eat", "drop", "junk", "donate"):
            self._drop_item(text)
            if action == "eat":
                self.hungry = False
        elif action == "drink":
            if d.get("empty"):
                self.drink_empty_t = self._now
                self.marks["drink_empty"] = self._now
            else:
                self.thirsty = False
        elif action == "hold" and self.item_type(text) == "light":
            self.has_light = True

    def _drop_item(self, text: str) -> None:
        for i, it in enumerate(self.inventory):
            if _same(it, text):
                del self.inventory[i]
                return

    def _drop_occupant(self, who: str) -> None:
        occ = self.room.get("occupants")
        if not occ or not who:
            return
        w = who.lower()
        bare = w
        for art in ("the ", "a ", "an ", "some "):
            if bare.startswith(art):
                bare = bare[len(art):]
                break
        for i, o in enumerate(occ):
            text = o.get("text", "").lower()
            first = o.get("text", "").split(" ", 1)[0]
            if self.is_ally(first) and first.lower() != w:
                continue                      # a party member's title may contain a mob's name
            if self.is_occupant(o, who) or text.startswith(w) or bare in text:
                del occ[i]
                return
