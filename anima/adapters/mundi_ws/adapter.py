"""Anima Mundi's WebSocket frames → protocol events (PROTOCOL.md part 1; the engine already speaks it).

Mundi sends one envelope per frame: `{v, t, tick, seq, agent, type, data, text}`. The event is the
type and data as they are; `text` (the lines Mundi rendered for this reader) becomes `raw`. Each
event is stamped again here, so `seq` and `t` are ours like any adapter's. Mundi's content IDs
(`tba:30:room:3001`) name the vnum Memoria knows, so room IDs become that number.
Like the text adapter, this does no I/O.
"""
from __future__ import annotations

import json
import re
from typing import Any

from anima.adapters.tbamud_text.ansi import strip_ansi
from anima.protocol.envelope import SELF, Event, Stamper

_ROOM_ID = re.compile(r"^[a-z]+:\d+:room:(\d+)$")
_OBJ_ID = re.compile(r"^[a-z]+:\d+:obj:(\d+)/\d+$")


def obj_vnum(id_: Any) -> int | None:
    """`tba:30:obj:3040/36527` → 3040: which of the world's same-named objects this one is."""
    m = _OBJ_ID.match(id_) if isinstance(id_, str) else None
    return int(m.group(1)) if m else None


def _with_vnum(item: Any) -> Any:
    if isinstance(item, dict) and (v := obj_vnum(item.get("id"))) is not None:
        return {**item, "vnum": v}
    return item


def vnum(id_: Any) -> Any:
    """`tba:30:room:3001` → 3001; anything else as it is."""
    if isinstance(id_, str) and (m := _ROOM_ID.match(id_)):
        return int(m.group(1))
    return id_


_CLASSES = {"mage": "magic_user"}
_ALREADY = {"stand": "standing", "sit": "sitting", "rest": "resting", "sleep": "sleeping"}
# Mundi's wear positions → the equipment list's labels, as anima reads them (act.informative.c wear_where)
_SLOT_LABEL = {"light": "used as light", "finger_right": "worn on finger", "finger_left": "worn on finger",
               "neck_1": "worn around neck", "neck_2": "worn around neck", "body": "worn on body",
               "head": "worn on head", "legs": "worn on legs", "feet": "worn on feet", "hands": "worn on hands",
               "arms": "worn on arms", "shield": "worn as shield", "about": "worn about body",
               "waist": "worn about waist", "wrist_right": "worn around wrist", "wrist_left": "worn around wrist",
               "wield": "wielded", "hold": "held"}      # the party's class words → Mundi's (crates/mundi-sim Class)


def _how_good(percent: int) -> str:
    """spell_parser.c how_good: the word the practice list shows for a proficiency."""
    for top, word in ((0, "not learned"), (10, "awful"), (20, "bad"), (40, "poor"), (55, "average"),
                      (70, "fair"), (80, "good"), (85, "very good")):
        if percent <= top:
            return word
    return "superb"


def mundi_class(name: str) -> str:
    return _CLASSES.get(name, name)


def _also(type_: str, d: dict) -> list[tuple[str, dict]]:
    """The events PROTOCOL.md (and the text adapter) has for what Mundi says its own way. Mundi's
    event is kept; these come after it."""
    reason = d.get("reason")
    if type_ == "position.refused" and reason == "already" and d.get("command") in _ALREADY:
        return [("position", {"position": _ALREADY[d["command"]]})]          # "You are already standing."
    if type_ == "group.failed" and reason == "already_in_group":
        return [("group.change", {"event": "joined", "who": SELF})]             # "But you are already part of a group."
    if type_ == "group.failed" and reason == "already_following":
        return [("group.change", {"event": "following", "who": d.get("who")})]  # "You are already following $M." (Mundi names $M)
    if type_ == "items.failed" and d.get("action") == "give" and reason in ("hands_full", "cant_carry"):
        return [("items.give_failed", {"reason": "hands_full" if reason == "hands_full" else "too_heavy"})]
    if type_ == "char.skills" and isinstance(d.get("practices"), int):
        # tbaMUD's practice list says "You have N practice sessions remaining." (char.score practices);
        # Mundi gives the number in the list: without it the state had 0 and no one ever trained
        return [("char.score", {"practices": d["practices"]})]
    if type_ == "move.blocked" and d.get("who") == SELF:
        return [("move.failed", {"reason": "guarded"})]                         # "The guard humiliates you, and blocks your way."
    if type_ == "items.failed" and d.get("action") == "drop" and reason == "cursed":
        return [("items.cannot_drop", {"text": d.get("text", ""), "reason": "cursed"})]
    return []


class MundiWsAdapter:
    def __init__(self, agent: str, stamper: Stamper | None = None, keep_raw: bool = True) -> None:
        self.agent = agent
        self.stamper = stamper or Stamper(agent)
        self.keep_raw = keep_raw

    def feed(self, frame: str) -> list[Event]:
        """One text frame → its event (none for a frame that is not an envelope)."""
        try:
            env = json.loads(frame)
        except ValueError:
            return []
        if not isinstance(env, dict) or not isinstance(env.get("type"), str):
            return []
        data = env.get("data")
        data = dict(data) if isinstance(data, dict) else {}
        if env["type"] in ("room", "room.dark"):
            data["id"] = vnum(data.get("id"))
        if env["type"] in ("room", "room.exits_listed"):
            data["exits"] = [{**e, "to_id": vnum(e.get("to_id"))} if isinstance(e, dict) and "to_id" in e else e
                             for e in data.get("exits", [])]
        if env["type"] == "shop.result" and data.get("who", SELF) == SELF:
            # PROTOCOL.md: {action: buy|sell, ok, reason?}; Mundi puts a failure's reason in action
            if data.get("action") in ("buy", "sell"):
                data["ok"] = True
            else:
                data = {**data, "action": "buy", "ok": False, "reason": data.get("action")}
        if env["type"] == "char.skills" and isinstance(data.get("skills"), list):
            # PROTOCOL.md: {skills: {name: proficiency}}, the practice list's words; Mundi gives percents
            data["skills"] = {k.get("name"): _how_good(int(k.get("percent", 0))) for k in data["skills"] if k.get("name")}
        if env["type"] == "items.equipment":
            data["slots"] = [{**sl, "slot": _SLOT_LABEL.get(sl.get("slot"), sl.get("slot"))} if isinstance(sl, dict) else sl
                             for sl in data.get("slots", [])]
        # which object of its name each one is (three 'a breast plate' in the world, AC 6 and 7)
        if env["type"] == "items.equipment":
            data["slots"] = [_with_vnum(sl) for sl in data["slots"]]
        if env["type"] == "items.inventory":
            data["items"] = [_with_vnum(it) for it in data.get("items", [])]
        if env["type"] == "items.used":
            data = _with_vnum(data)
        raw = [strip_ansi(line) for line in env.get("text", [])] if self.keep_raw else None
        if env["type"] == "room":
            # a player is a player (the text adapter's hint); Mundi names them pc:<name>
            data["occupants"] = [{**o, "hints": list(o.get("hints", [])) + ["player"]}
                                 if isinstance(o, dict) and str(o.get("id", "")).startswith("pc:") else o
                                 for o in data.get("occupants", [])]
        out = [self.stamper.stamp(env["type"], data, raw=raw or None)]
        out += [self.stamper.stamp(t, d) for t, d in _also(env["type"], data)]
        return out

    def screen(self, frame: str) -> str:
        """The frame's lines as a terminal shows them (colours kept), for people watching."""
        try:
            env = json.loads(frame)
        except ValueError:
            return ""
        lines = env.get("text", []) if isinstance(env, dict) else []
        return "".join(line + "\r\n" for line in lines)
