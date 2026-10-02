"""Who is that? Identify room occupants and judge zones by level.

A mob in its default position is shown by its long description ("A cityguard stands here.");
otherwise by its short description plus position ("The beastly fido is resting here.")
(act.informative.c list_one_char). Players show their name and title.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from anima.memoria.model import Mob, Obj, World, Zone

_POS_SUFFIX = re.compile(r" is (?:lying here, \w[\w ]*|sleeping here|resting here|sitting here|standing here"
                         r"|here, fighting .*|sitting upon .*|resting upon .*|sleeping upon .*)[.!]$")


def _key(text: str) -> str:
    return text.strip().lower()


@dataclass
class Identified:
    kind: str                        # mob | player | unknown
    mobs: list[Mob] = field(default_factory=list)
    name: str = ""                   # player name when kind == player

    @property
    def level(self) -> int | None:
        return max(m.level for m in self.mobs) if self.mobs else None

    def has_flag(self, flag: str) -> bool:
        return any(flag in m.flags for m in self.mobs)


class Knowledge:
    def __init__(self, world: World) -> None:
        self.world = world
        self._by_long: dict[str, list[Mob]] = {}
        self._by_short: dict[str, list[Mob]] = {}
        self._obj_by_short: dict[str, list[Obj]] = {}
        self._obj_by_long: dict[str, list[Obj]] = {}
        for o in world.objs.values():
            self._obj_by_short.setdefault(_key(o.short), []).append(o)
            if o.long:
                self._obj_by_long.setdefault(_key(o.long), []).append(o)
        for m in world.mobs.values():
            if m.long:
                self._by_long.setdefault(_key(m.long), []).append(m)
            if m.short:
                self._by_short.setdefault(_key(m.short), []).append(m)

    def identify(self, occupant: dict, known_players: Iterable[str] = ()) -> Identified:
        """occupant: one entry of a `room` event's `occupants`."""
        text = occupant.get("text", "")
        hints = set(occupant.get("hints", []))
        first = text.split(" ", 1)[0]
        if "player" in hints or first in set(known_players):
            return Identified("player", name=first)
        mobs = self._by_long.get(_key(text))
        if mobs:
            return Identified("mob", list(mobs))
        body = _POS_SUFFIX.sub("", text)
        mobs = self._by_short.get(_key(body))
        if mobs:
            return Identified("mob", list(mobs))
        if hints & {"my_group", "my_group_leader", "other_group", "other_group_leader", "group_member"}:
            return Identified("player", name=first)
        return Identified("unknown")

    def mob_by_name(self, name: str) -> list[Mob]:
        """A name as seen in events ('the beastly fido')."""
        return list(self._by_short.get(_key(name), []))

    def item(self, text: str) -> Obj | None:
        """An inventory / equipment line ('a danish pastry', 'a candle (glowing)') → object."""
        objs = self.items(text)
        return objs[0] if objs else None

    def items(self, text: str) -> list[Obj]:
        """Every object with that short description (many share one, e.g. twelve 'a long sword')."""
        t = re.sub(r"\s*(\([^)]*\)|\.\.[^.]*)\s*$", "", text).strip()
        return list(self._obj_by_short.get(_key(t), []))

    def ground_item(self, long_text: str) -> Obj | None:
        """A room object line (long description) → object."""
        t = re.sub(r"\s*\.\.[^.]*$", "", long_text).strip()
        objs = self._obj_by_long.get(_key(t))
        return objs[0] if objs else None

    def item_keyword(self, text: str) -> str:
        """Keyword to name this item in commands: the object's first keyword, else the last word."""
        o = self.item(text)
        words = [w.lower() for w in re.findall(r"[A-Za-z]+", o.short if o else text)]
        if o and o.keywords:
            # a keyword that is also in the short name is the one players see ("a brass lantern" -> lantern);
            # the first keyword can be a generic one the server does not match (e.g. "water")
            kws = {k.lower() for k in o.keywords}
            shown = [w for w in reversed(words) if w in kws]       # the noun ends the name
            return shown[0] if shown else o.keywords[0]
        return words[-1] if words else ""

    def zones_for_level(self, level: int) -> list[Zone]:
        return sorted((z for z in self.world.zones.values() if 0 < z.min_level <= level <= z.max_level),
                      key=lambda z: (z.max_level, z.num))
