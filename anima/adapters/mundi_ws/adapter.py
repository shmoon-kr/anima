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
from anima.protocol.envelope import Event, Stamper

_ROOM_ID = re.compile(r"^[a-z]+:\d+:room:(\d+)$")


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


def mundi_class(name: str) -> str:
    return _CLASSES.get(name, name)


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
        if env["type"] == "room":
            data["id"] = vnum(data.get("id"))
        if env["type"] in ("room", "room.exits_listed"):
            data["exits"] = [{**e, "to_id": vnum(e.get("to_id"))} if isinstance(e, dict) and "to_id" in e else e
                             for e in data.get("exits", [])]
        if env["type"] == "items.equipment":
            data["slots"] = [{**sl, "slot": _SLOT_LABEL.get(sl.get("slot"), sl.get("slot"))} if isinstance(sl, dict) else sl
                             for sl in data.get("slots", [])]
        raw = [strip_ansi(line) for line in env.get("text", [])] if self.keep_raw else None
        out = [self.stamper.stamp(env["type"], data, raw=raw or None)]
        # "You are already standing." tells the position (PROTOCOL.md `position`, as the text adapter
        # reads it); Mundi says it as position.refused.
        if env["type"] == "position.refused" and data.get("reason") == "already" and data.get("command") in _ALREADY:
            out.append(self.stamper.stamp("position", {"position": _ALREADY[data["command"]]}))
        return out

    def screen(self, frame: str) -> str:
        """The frame's lines as a terminal shows them (colours kept), for people watching."""
        try:
            env = json.loads(frame)
        except ValueError:
            return ""
        lines = env.get("text", []) if isinstance(env, dict) else []
        return "".join(line + "\r\n" for line in lines)
