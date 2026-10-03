"""One agent's body state, built only from protocol events (no server text here)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from anima.protocol.envelope import SELF, Event

FIGHT_WINDOW_S = 6.0
NEVER = -1e9


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

    def on_event(self, ev: Event, now: float) -> None:
        self._now = now
        self.last_seq = ev.seq or self.last_seq
        t, d = ev.type, ev.data
        if t == "prompt":
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
        elif t == "items.equipment":
            self.equipment = d.get("slots", [])
            self.has_light = any(s.get("slot") == "used as light" for s in self.equipment)
        elif t == "items.light_out" and d.get("who") == SELF:
            self.has_light = False
        elif t == "items.used":
            self._item_used(d)
        elif t in ("items.got", "items.received"):
            self.inventory.append(d.get("text", ""))
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
            self._drop_occupant(d.get("who", ""))
        elif t == "combat.hit":
            if d.get("victim") and d.get("victim") != SELF and d.get("severity", 0) > 0:
                self.hit_seen[d["victim"]] = now
            if SELF in (d.get("attacker"), d.get("victim")):
                self.last_fight_t = now
                # a hit does NOT wake a sleeper (fight.c update_pos keeps POS_SLEEPING while hp > 0):
                # the position stays, so the wake_when_hit reflex sees "sleeping" and sends wake + stand
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
        elif t == "connection.in_game":
            self.in_game = True
            if d.get("how") == "entered":
                # a fresh entry (after a reboot, a crash, a quit): no group, no one followed. A reconnect
                # keeps both. Else the leader thinks it leads a group that is gone and never forms one.
                self.in_group = False
                self.following = None
        elif t == "connection.closed":
            self.in_game = False

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
