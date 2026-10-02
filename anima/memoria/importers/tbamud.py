"""Read tbaMUD world files (lib/world/{wld,mob,zon}) into the Memoria model.

File formats as read by tbaMUD db.c (parse_room, parse_mobile, load_zones). Flag numbers from
structs.h: ROOM_DARK 0, ROOM_DEATH 1, ROOM_INDOORS 3; sectors 0 inside, 1 city, 7 water-noswim,
8 flying, 9 underwater; MOB_SENTINEL 1, MOB_AGGRESSIVE 5, MOB_WIMPY 7, MOB_AGGR_EVIL 8,
MOB_AGGR_GOOD 9, MOB_AGGR_NEUTRAL 10, MOB_MEMORY 11, MOB_HELPER 12. Objects (db.c parse_object):
ITEM_WEAR_* 0..14, extra ITEM_* (MAGIC 6, NODROP 7, ANTI_* 9..15, NOSELL 16), APPLY_* locations.
Shops (shop.c boot_the_shops, "v3.0" format) and zone resets (db.c load_zones: M, E, G, O).
"""
from __future__ import annotations

import re
from pathlib import Path

from anima.memoria.model import DIRS, ITEM_TYPES, Exit, Mob, Obj, Room, Shop, World, Zone

ROOM_DARK, ROOM_DEATH, ROOM_INDOORS = 0, 1, 3
SECT_INSIDE, SECT_CITY = 0, 1
IMPASSABLE_SECTORS = {7, 8, 9}
MOVEMENT_LOSS = [1, 1, 2, 3, 4, 6, 4, 1, 1, 5]      # constants.c movement_loss[] by sector (act.movement.c:252)
MOB_BITS = {1: "sentinel", 5: "aggressive", 7: "wimpy", 8: "aggr_evil", 9: "aggr_good", 10: "aggr_neutral",
            11: "memory", 12: "helper"}
_HASH = re.compile(r"#(\d+)")
WEAR_BITS = ["take", "finger", "neck", "body", "head", "legs", "feet", "hands", "arms", "shield", "about",
             "waist", "wrist", "wield", "hold"]                                     # structs.h ITEM_WEAR_*
EXTRA_BITS = {0: "glow", 1: "hum", 2: "norent", 3: "nodonate", 4: "noinvis", 5: "invisible", 6: "magic",
              7: "nodrop", 8: "bless", 9: "anti_good", 10: "anti_evil", 11: "anti_neutral", 12: "anti_mage",
              13: "anti_cleric", 14: "anti_thief", 15: "anti_warrior", 16: "nosell", 17: "quest_item"}
APPLY = {1: "str", 2: "dex", 3: "int", 4: "wis", 5: "con", 6: "cha", 12: "mana", 13: "hit", 14: "move",
         17: "ac", 18: "hitroll", 19: "damroll", 20: "save_para", 21: "save_rod", 22: "save_petri",
         23: "save_breath", 24: "save_spell"}                                       # structs.h APPLY_*
ITEM_TYPE_NAMES = ["undefined", "light", "scroll", "wand", "staff", "weapon", "furniture", "free", "treasure",
                   "armor", "potion", "worn", "other", "trash", "free2", "container", "note", "drinkcon", "key",
                   "food", "money", "pen", "boat", "fountain"]                      # constants.c item_types


def _bits(tok: str, names) -> set[str]:
    v = flag_value(tok)
    if isinstance(names, dict):
        return {n for b, n in names.items() if v & (1 << b)}
    return {n for b, n in enumerate(names) if v & (1 << b)}


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
                    impassable=sector in IMPASSABLE_SECTORS, exits=exits,
                    move_cost=MOVEMENT_LOSS[sector] if 0 <= sector < len(MOVEMENT_LOSS) else 1)
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
        money = lines[i + 2].split() if i + 2 < len(lines) else []          # "gold exp" (db.c parse_simple_mob)
        i += 2
        act = flag_value(flags[0]) if flags else 0
        level = int(stats[0]) if stats and stats[0].lstrip("-").isdigit() else 0
        out[vnum] = Mob(vnum=vnum, keywords=names.split(), short=short.strip(), long=long_.strip(),
                        level=level, hp=_dice_avg(stats[3]) if len(stats) > 3 else 0,
                        flags={n for b, n in MOB_BITS.items() if act & (1 << b)},
                        exp=int(money[1]) if len(money) > 1 and money[1].lstrip("-").isdigit() else 0)
    return out


def parse_obj(path: Path) -> dict[int, Obj]:
    """db.c parse_object: keywords~ short~ long~ action~, then
    `type extra[4] wear[4] perm[4]`, `v0 v1 v2 v3`, `weight cost rent level timer`, then E/A/T blocks."""
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
        nums = lines[i + 1].split() if i + 1 < len(lines) else []
        more = lines[i + 2].split() if i + 2 < len(lines) else []
        i += 3
        t = int(head[0]) if head and head[0].lstrip("-").isdigit() else 0
        extra = _bits(head[1], EXTRA_BITS) if len(head) >= 13 else set()
        wear = _bits(head[5], WEAR_BITS) if len(head) >= 13 else set()
        vals = [int(x) for x in nums[:4] if x.lstrip("-").isdigit()] + [0, 0, 0, 0]
        num = [int(x) for x in more[:5] if x.lstrip("-").isdigit()] + [0, 0, 0, 0, 0]
        affects: list[tuple[str, int]] = []
        while i < len(lines) and not _HASH.fullmatch(lines[i].strip()) and not lines[i].startswith("$"):
            if lines[i].startswith("A") and i + 1 < len(lines):
                a = lines[i + 1].split()
                if len(a) == 2 and int(a[0]) in APPLY:
                    affects.append((APPLY[int(a[0])], int(a[1])))
                i += 2
            elif lines[i].startswith("E"):
                _, i = _read_tilde(lines, i + 1)
                _, i = _read_tilde(lines, i)
            else:
                i += 1
        out[vnum] = Obj(vnum=vnum, keywords=names.split(), short=short.strip(), long=long_.strip(),
                        type=ITEM_TYPES.get(t, "other"), wear=wear, extra=extra, values=vals[:4],
                        weight=num[0], cost=num[1], level=num[3], affects=affects)
    return out


def parse_resets(path: Path) -> list[tuple[str, int, int]]:
    """db.c load_zones commands we use: (M, mob, room), (E|G, obj, mob loaded last), (O, obj, room)."""
    out: list[tuple[str, int, int]] = []
    last_mob = -1
    for line in _lines(path):
        f = line.split()
        if not f or f[0] not in "MEGO" or len(f) < 4:
            continue
        try:
            a = [int(x) for x in f[1:6] if x.lstrip("-").isdigit()]
        except ValueError:
            continue
        if f[0] == "M" and len(a) >= 4:
            last_mob = a[1]
            out.append(("M", a[1], a[3]))
        elif f[0] in "EG" and len(a) >= 2 and last_mob >= 0:
            out.append((f[0], a[1], last_mob))
        elif f[0] == "O" and len(a) >= 4:
            out.append(("O", a[1], a[3]))
    return out


def parse_shp(path: Path) -> dict[int, Shop]:
    """shop.c boot_the_shops (v3.0 format): products -1, buy profit, sell profit, buy types -1,
    7 messages~, temper, bitvector, keeper, trade_with, rooms -1, open/close times."""
    lines = _lines(path)
    out: dict[int, Shop] = {}
    i = 0

    def ints_until_minus1() -> list[int]:
        nonlocal i
        vals = []
        while i < len(lines):
            tok = lines[i].split(";")[0].strip()
            i += 1
            if tok.startswith("-1"):
                return vals
            word = tok.split()[0] if tok else ""
            if word.lstrip("-").isdigit():
                vals.append(int(word))
            elif word.upper() in [n.upper() for n in ITEM_TYPE_NAMES]:
                vals.append([n.upper() for n in ITEM_TYPE_NAMES].index(word.upper()))
        return vals

    while i < len(lines):
        m = re.fullmatch(r"#(\d+)~?", lines[i].strip())
        if not m:
            i += 1
            continue
        vnum = int(m.group(1))
        i += 1
        products = ints_until_minus1()
        try:
            buy_profit, sell_profit = float(lines[i]), float(lines[i + 1])
        except (ValueError, IndexError):
            continue
        i += 2
        types = ints_until_minus1()
        for _ in range(7):
            _, i = _read_tilde(lines, i)
        try:
            keeper = int(lines[i + 2].split()[0])
        except (ValueError, IndexError):
            continue
        i += 4                                   # temper, bitvector, keeper, trade_with
        rooms = ints_until_minus1()
        out[vnum] = Shop(vnum, keeper, rooms, products,
                         [ITEM_TYPES.get(t, ITEM_TYPE_NAMES[t] if 0 <= t < len(ITEM_TYPE_NAMES) else "other")
                          for t in types], buy_profit, sell_profit)
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
    for f in sorted((world_dir / "zon").glob("*.zon")):
        for kind, a, b in parse_resets(f):
            if kind == "M" and a in world.mobs:
                world.mobs[a].homes.append(b)
            elif kind in "EG" and b in world.mobs:
                world.mobs[b].carries.append(a)
    for f in sorted((world_dir / "shp").glob("*.shp")) if (world_dir / "shp").exists() else ():
        world.shops.update(parse_shp(f))
    return world
