"""Engine-neutral world model types. Importers (e.g. importers/tbamud.py) fill these."""
from __future__ import annotations

from dataclasses import dataclass, field

DIRS = ("north", "east", "south", "west", "up", "down")
REVERSE = {"north": "south", "south": "north", "east": "west", "west": "east", "up": "down", "down": "up"}
ABBR = {"n": "north", "e": "east", "s": "south", "w": "west", "u": "up", "d": "down"}


@dataclass
class Exit:
    to: int
    door: bool = False
    keyword: str = ""


@dataclass
class Room:
    vnum: int
    name: str
    desc: str
    zone: int
    dark: bool = False          # always dark (ROOM_DARK)
    deadly: bool = False        # death trap
    outdoors: bool = False      # dark at night unless lit
    impassable: bool = False    # needs boat / flying / underwater: not reachable on foot
    exits: dict[str, Exit] = field(default_factory=dict)


@dataclass
class Mob:
    vnum: int
    keywords: list[str]
    short: str
    long: str
    level: int
    hp: int
    flags: set[str]             # aggressive, memory, helper, sentinel, wimpy, aggr_evil, ...
    zone: int = -1


ITEM_TYPES = {1: "light", 5: "weapon", 8: "treasure", 9: "armor", 11: "worn", 15: "container",
              17: "drinkcon", 18: "key", 19: "food", 20: "money", 23: "fountain"}   # structs.h ITEM_*


@dataclass
class Obj:
    vnum: int
    keywords: list[str]
    short: str
    long: str
    type: str                   # light | food | drinkcon | weapon | armor | ... | other


@dataclass
class Zone:
    num: int
    name: str
    min_level: int
    max_level: int
    bottom: int
    top: int


@dataclass
class World:
    rooms: dict[int, Room] = field(default_factory=dict)
    mobs: dict[int, Mob] = field(default_factory=dict)
    objs: dict[int, Obj] = field(default_factory=dict)
    zones: dict[int, Zone] = field(default_factory=dict)

    def zone_of(self, vnum: int) -> Zone | None:
        room = self.rooms.get(vnum)
        return self.zones.get(room.zone) if room else None
