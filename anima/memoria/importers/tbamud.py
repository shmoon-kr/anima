"""Read tbaMUD world files (lib/world/{wld,mob,zon}) into the Memoria model.

File formats as read by tbaMUD db.c (parse_room, parse_mobile, load_zones). Flag numbers from
structs.h: ROOM_DARK 0, ROOM_DEATH 1, ROOM_INDOORS 3; sectors 0 inside, 1 city, 7 water-noswim,
8 flying, 9 underwater; MOB_SENTINEL 1, MOB_AGGRESSIVE 5, MOB_WIMPY 7, MOB_AGGR_EVIL 8,
MOB_AGGR_GOOD 9, MOB_AGGR_NEUTRAL 10, MOB_MEMORY 11, MOB_HELPER 12.
"""
from __future__ import annotations

import re
from pathlib import Path

from anima.memoria.model import DIRS, ITEM_TYPES, Exit, Mob, Obj, Room, World, Zone

ROOM_DARK, ROOM_DEATH, ROOM_INDOORS = 0, 1, 3
SECT_INSIDE, SECT_CITY = 0, 1
IMPASSABLE_SECTORS = {7, 8, 9}
MOB_BITS = {1: "sentinel", 5: "aggressive", 7: "wimpy", 8: "aggr_evil", 9: "aggr_good", 10: "aggr_neutral",
            11: "memory", 12: "helper"}
_HASH = re.compile(r"#(\d+)")


def flag_value(tok: str) -> int:
    """Flags are written as a number or as letters (a = bit 0, A = bit 26)."""
    if tok.lstrip("-").isdigit():
        return int(tok)
    v = 0
    for ch in tok:
        if "a" <= ch <= "z":
            v |= 1 << (ord(ch) - ord("a"))
        elif "A" <= ch <= "Z":
            v |= 1 << (26 + ord(ch) - ord("A"))
    return v


def _read_tilde(lines: list[str], i: int) -> tuple[str, int]:
    buf = []
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.endswith("~"):
            buf.append(line[:-1])
            return "\n".join(buf), i
        buf.append(line)
    return "\n".join(buf), i


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="latin-1").replace("\r", "").split("\n")


def parse_wld(path: Path) -> dict[int, tuple[Room, int]]:
    """vnum -> (room, sector)."""
    lines = _lines(path)
    out: dict[int, tuple[Room, int]] = {}
    i = 0
    while i < len(lines):
        m = _HASH.fullmatch(lines[i].strip())
        if not m:
            i += 1
            continue
        vnum = int(m.group(1))
        name, i = _read_tilde(lines, i + 1)
        desc, i = _read_tilde(lines, i)
        parts = lines[i].split()
        i += 1
        zone = int(parts[0]) if parts and parts[0].lstrip("-").isdigit() else -1
        flags = flag_value(parts[1]) if len(parts) > 1 else 0
        sector = int(parts[-1]) if len(parts) > 2 and parts[-1].lstrip("-").isdigit() else 0
        exits: dict[str, Exit] = {}
        while i < len(lines):
            line = lines[i].strip()
            if line == "S":
                i += 1
                break
            if re.fullmatch(r"D\d+", line):
                d = int(line[1:])
                _, i = _read_tilde(lines, i + 1)          # exit description
                keyword, i = _read_tilde(lines, i)        # door keyword
                nums = lines[i].split()
                i += 1
                if d < len(DIRS) and len(nums) >= 3 and int(nums[2]) >= 0:
                    exits[DIRS[d]] = Exit(to=int(nums[2]), door=int(nums[0]) != 0, keyword=keyword.strip())
            elif line == "E":
                _, i = _read_tilde(lines, i + 1)
                _, i = _read_tilde(lines, i)
            elif line.startswith("#") or line == "$":
                break
            else:
                i += 1
        room = Room(vnum=vnum, name=name.strip(), desc=desc, zone=zone,
                    dark=bool(flags & (1 << ROOM_DARK)), deadly=bool(flags & (1 << ROOM_DEATH)),
                    outdoors=not (flags & (1 << ROOM_INDOORS)) and sector not in (SECT_INSIDE, SECT_CITY),
                    impassable=sector in IMPASSABLE_SECTORS, exits=exits)
        out[vnum] = (room, sector)
    return out


def _dice_avg(s: str) -> int:
    m = re.fullmatch(r"(\d+)d(\d+)\+(-?\d+)", s)
    if not m:
        return 0
    n, d, b = map(int, m.groups())
    return n * (d + 1) // 2 + b


def parse_mob(path: Path) -> dict[int, Mob]:
    lines = _lines(path)
    out: dict[int, Mob] = {}
    i = 0
    while i < len(lines):
        m = _HASH.fullmatch(lines[i].strip())
        if not m:
            i += 1
            continue
        vnum = int(m.group(1))
        names, i = _read_tilde(lines, i + 1)
        short, i = _read_tilde(lines, i)
        long_, i = _read_tilde(lines, i)
        _, i = _read_tilde(lines, i)
        flags = lines[i].split() if i < len(lines) else []
        stats = lines[i + 1].split() if i + 1 < len(lines) else []
        i += 2
        act = flag_value(flags[0]) if flags else 0
        level = int(stats[0]) if stats and stats[0].lstrip("-").isdigit() else 0
        out[vnum] = Mob(vnum=vnum, keywords=names.split(), short=short.strip(), long=long_.strip(),
                        level=level, hp=_dice_avg(stats[3]) if len(stats) > 3 else 0,
                        flags={n for b, n in MOB_BITS.items() if act & (1 << b)})
    return out


def parse_obj(path: Path) -> dict[int, Obj]:
    """db.c parse_object: keywords~ short~ long~ action~ then 'type extra... wear...' line."""
    lines = _lines(path)
    out: dict[int, Obj] = {}
    i = 0
    while i < len(lines):
        m = _HASH.fullmatch(lines[i].strip())
        if not m:
            i += 1
            continue
        vnum = int(m.group(1))
        names, i = _read_tilde(lines, i + 1)
        short, i = _read_tilde(lines, i)
        long_, i = _read_tilde(lines, i)
        _, i = _read_tilde(lines, i)
        head = lines[i].split() if i < len(lines) else []
        i += 1
        t = int(head[0]) if head and head[0].lstrip("-").isdigit() else 0
        out[vnum] = Obj(vnum=vnum, keywords=names.split(), short=short.strip(), long=long_.strip(),
                        type=ITEM_TYPES.get(t, "other"))
    return out


def parse_zon(path: Path) -> Zone | None:
    lines = _lines(path)
    m = _HASH.fullmatch(lines[0].strip())
    if not m:
        return None
    _, i = _read_tilde(lines, 1)          # builders
    name, i = _read_tilde(lines, i)
    head = [int(t) for t in lines[i].split() if t.lstrip("-").isdigit()]
    bottom, top = (head[0], head[1]) if len(head) >= 2 else (0, 0)
    lo, hi = (head[-2], head[-1]) if len(head) >= 6 else (-1, -1)
    return Zone(num=int(m.group(1)), name=name.strip(), min_level=lo, max_level=hi, bottom=bottom, top=top)


def load_world(world_dir: Path) -> World:
    world = World()
    sectors: dict[int, int] = {}
    for f in sorted((world_dir / "wld").glob("*.wld")):
        for vnum, (room, sector) in parse_wld(f).items():
            world.rooms[vnum] = room
            sectors[vnum] = sector
    world.rooms.pop(0, None)
    for f in sorted((world_dir / "zon").glob("*.zon")):
        z = parse_zon(f)
        if z:
            world.zones[z.num] = z
    for f in sorted((world_dir / "mob").glob("*.mob")):
        zone_num = int(f.stem) if f.stem.isdigit() else -1
        for vnum, mob in parse_mob(f).items():
            mob.zone = zone_num
            world.mobs[vnum] = mob
    for f in sorted((world_dir / "obj").glob("*.obj")):
        world.objs.update(parse_obj(f))
    return world
