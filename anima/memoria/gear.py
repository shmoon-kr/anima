"""Equipment from world data: which slot an item goes in, how good it is, whether we may use it.

Engine-neutral: who may use what comes in as flags to avoid (`anti_*` from the world file) and
required weapon kinds, which the packages declare per class. Numbers follow tbaMUD:
- armor counts 3x on the body, 2x on head and legs, 1x elsewhere (handler.c apply_ac)
- a weapon hits for v1 d v2 (fight.c hit), v3 is the attack type (11 = pierce: backstab needs it)
- below the item's level the server refuses it ("You are not experienced enough", act.item.c)
"""
from __future__ import annotations

from typing import Iterable

from anima.memoria.model import Obj

# `equipment` labels (constants.c wear_where) -> slot
SLOT_OF_LABEL = {
    "used as light": "light", "worn on finger": "finger", "worn around neck": "neck", "worn on body": "body",
    "worn on head": "head", "worn on legs": "legs", "worn on feet": "feet", "worn on hands": "hands",
    "worn on arms": "arms", "worn as shield": "shield", "worn about body": "about", "worn about waist": "waist",
    "worn around wrist": "wrist", "wielded": "wield", "held": "hold",
}
CAPACITY = {"finger": 2, "neck": 2, "wrist": 2}
AC_FACTOR = {"body": 3, "head": 2, "legs": 2}
WEAR_ORDER = ["wield", "body", "head", "legs", "shield", "about", "arms", "hands", "feet", "waist",
              "wrist", "finger", "neck", "hold", "light"]
ATTACK_KIND = {0: "hit", 1: "sting", 2: "whip", 3: "slash", 4: "bite", 5: "bludgeon", 6: "crush", 7: "pound",
               8: "claw", 9: "maul", 10: "thrash", 11: "pierce", 12: "blast", 13: "punch", 14: "stab"}
AFFECT_WORTH = {"ac": -1.0, "hitroll": 2.0, "damroll": 3.0, "hit": 0.25, "mana": 0.1, "move": 0.05,
                "str": 2.0, "dex": 2.0, "con": 2.0, "int": 1.0, "wis": 1.0, "cha": 0.5,
                "save_para": -1.0, "save_rod": -1.0, "save_petri": -1.0, "save_breath": -1.0, "save_spell": -1.0}


def slots(obj: Obj) -> list[str]:
    """Slots this item can go in. A light is held in the light slot."""
    if obj.type == "light":
        return ["light"]
    if obj.type == "weapon" and "wield" in obj.wear:
        return ["wield"]
    return [s for s in WEAR_ORDER if s in obj.wear and s != "light"]


def weapon_kind(obj: Obj) -> str | None:
    return ATTACK_KIND.get(obj.values[3]) if obj.type == "weapon" else None


def score(obj: Obj, slot: str) -> float:
    """How much an item helps in that slot (higher is better). Same scale across slots."""
    s = 0.0
    if obj.type == "armor":
        s += AC_FACTOR.get(slot, 1) * max(0, obj.values[0])
    if obj.type == "weapon" and slot == "wield":
        s += 2.0 * obj.values[1] * (obj.values[2] + 1) / 2          # average damage per hit, weighted
    if obj.type == "light" and slot == "light":
        s += 1.0 + (5.0 if obj.values[2] < 0 else 0.0)              # a permanent light (hours -1) is best
    for loc, mod in obj.affects:
        s += AFFECT_WORTH.get(loc, 0.0) * mod
    return round(s, 2)


def usable(obj: Obj, level: int, avoid: Iterable[str] = (), weapon_kinds: Iterable[str] = ()) -> bool:
    if obj.level > level or "take" not in obj.wear or "nodrop" in obj.extra:
        return False
    if set(avoid) & obj.extra:
        return False
    kinds = list(weapon_kinds)
    if kinds and obj.type == "weapon" and weapon_kind(obj) not in kinds:
        return False
    return bool(slots(obj))


def upgrade(equipped: dict[str, list[Obj | None]], candidate: Obj, level: int, avoid: Iterable[str] = (),
            weapon_kinds: Iterable[str] = ()) -> tuple[str, Obj | None, float] | None:
    """(slot, item it replaces or None for a free place, gain) if `candidate` beats what is worn."""
    if not usable(candidate, level, avoid, weapon_kinds):
        return None
    best: tuple[str, Obj | None, float] | None = None
    for slot in slots(candidate):
        worn = equipped.get(slot, [])
        free = CAPACITY.get(slot, 1) - len([w for w in worn if w is not None]) + worn.count(None)
        new = score(candidate, slot)
        if free > 0 and len(worn) < CAPACITY.get(slot, 1):
            gain, out = new, None
        else:
            known = [w for w in worn if w is not None]
            if len(known) < len(worn):                 # something unknown there: do not touch it
                continue
            worst = min(known, key=lambda o: score(o, slot)) if known else None
            gain, out = new - (score(worst, slot) if worst else 0.0), worst
        if gain > 0.5 and (best is None or gain > best[2]):
            best = (slot, out, round(gain, 2))
    return best
