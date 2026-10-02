"""Combat message rules, generated from the server's own message tables.

- Weapon damage: `dam_weapons[]` and `attack_hit_text[]` in tbaMUD src/fight.c (copied below,
  these live in C source). severity = table index 0..8.
- Skills, spells, weapon misses and death blows: lib/misc/messages (third_party/tbamud/messages), read at
  start-up. die/miss/hit are approximated as severity 8/0/4 (PROTOCOL.md §3 전투).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anima.protocol.envelope import SELF

from .act import Rule
from .rules import normalize_name

# fight.c:34 attack_hit_text[] (singular, plural)
ATTACK_HIT_TEXT = [
    ("hit", "hits"), ("sting", "stings"), ("whip", "whips"), ("slash", "slashes"),
    ("bite", "bites"), ("bludgeon", "bludgeons"), ("crush", "crushes"), ("pound", "pounds"),
    ("claw", "claws"), ("maul", "mauls"), ("thrash", "thrashes"), ("pierce", "pierces"),
    ("blast", "blasts"), ("punch", "punches"), ("stab", "stabs"),
]
_SINGULAR_OF = {pl: sg for sg, pl in ATTACK_HIT_TEXT}

# fight.c dam_message() dam_weapons[]: (to_room, to_char, to_victim), index = severity
DAM_WEAPONS = [
    ("$n tries to #w $N, but misses.", "You try to #w $N, but miss.", "$n tries to #w you, but misses."),
    ("$n tickles $N as $e #W $M.", "You tickle $N as you #w $M.", "$n tickles you as $e #W you."),
    ("$n barely #W $N.", "You barely #w $N.", "$n barely #W you."),
    ("$n #W $N.", "You #w $N.", "$n #W you."),
    ("$n #W $N hard.", "You #w $N hard.", "$n #W you hard."),
    ("$n #W $N very hard.", "You #w $N very hard.", "$n #W you very hard."),
    ("$n #W $N extremely hard.", "You #w $N extremely hard.", "$n #W you extremely hard."),
    ("$n massacres $N to small fragments with $s #w.", "You massacre $N to small fragments with your #w.",
     "$n massacres you to small fragments with $s #w."),
    ("$n OBLITERATES $N with $s deadly #w!!", "You OBLITERATE $N with your deadly #w!!",
     "$n OBLITERATES you with $s deadly #w!!"),
]

_VERBS = {
    "#w": "(?P<w>" + "|".join(sg for sg, _ in ATTACK_HIT_TEXT) + ")",
    "#W": "(?P<W>" + "|".join(pl for _, pl in ATTACK_HIT_TEXT) + ")",
}

MAX_SPELLS = 130      # spells.h
TYPE_HIT = 300        # spells.h
TYPE_SUFFERING = 399  # spells.h

ROOM, CHAR, VICT = "room", "char", "vict"


def _who(perspective: str, g: dict[str, Any]) -> tuple[str, str]:
    n, N = normalize_name(g.get("n") or ""), normalize_name(g.get("N") or "")
    if perspective == CHAR:
        return SELF, N
    if perspective == VICT:
        return n, SELF
    return n, N


def _hit_builder(perspective: str, severity: int, kind: str, verb: str | None):
    def build(g: dict[str, Any]):
        attacker, victim = _who(perspective, g)
        v = verb
        if v is None:
            v = g.get("w") or _SINGULAR_OF.get(g.get("W") or "", g.get("W"))
        return "combat.hit", {"attacker": attacker, "victim": victim, "verb": v,
                              "severity": severity, "kind": kind}
    return build


def weapon_rules() -> list[Rule]:
    rules: list[Rule] = []
    for severity, (to_room, to_char, to_vict) in enumerate(DAM_WEAPONS):
        for persp, tmpl in ((ROOM, to_room), (CHAR, to_char), (VICT, to_vict)):
            rules.append(Rule(tmpl, _hit_builder(persp, severity, "weapon", None), extra=_VERBS))
    return rules


@dataclass
class MessageSet:
    attack_type: int
    name: str
    die: tuple[str | None, str | None, str | None]
    miss: tuple[str | None, str | None, str | None]
    hit: tuple[str | None, str | None, str | None]


def parse_messages(path: Path) -> list[MessageSet]:
    """Parse lib/misc/messages (format read by msgedit.c load_messages / db.c fread_action)."""
    lines = path.read_text(encoding="latin-1").splitlines()
    sets: list[MessageSet] = []
    last_comment = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("*"):
            last_comment = line[1:].strip()
            i += 1
            continue
        if line.strip() == "M":
            attack_type = int(lines[i + 1].strip())
            msgs = [None if l.startswith("#") else l for l in lines[i + 2:i + 14]]
            name = _name_from_comment(last_comment, attack_type)
            sets.append(MessageSet(attack_type, name,
                                   die=(msgs[0], msgs[1], msgs[2]),
                                   miss=(msgs[3], msgs[4], msgs[5]),
                                   hit=(msgs[6], msgs[7], msgs[8])))
            i += 14
            continue
        i += 1
    return sets


def _name_from_comment(comment: str, attack_type: int) -> str:
    if TYPE_HIT <= attack_type < TYPE_HIT + len(ATTACK_HIT_TEXT):
        return ATTACK_HIT_TEXT[attack_type - TYPE_HIT][0]
    name = re.sub(r"\s*\d+\s*$", "", comment).strip()
    return name or str(attack_type)


def _kind(attack_type: int) -> str:
    if attack_type <= MAX_SPELLS:
        return "spell"
    if attack_type < TYPE_HIT:
        return "skill"
    return "weapon"


def message_rules(path: Path) -> list[Rule]:
    rules: list[Rule] = []
    for ms in parse_messages(path):
        if ms.attack_type == TYPE_SUFFERING or ms.name.startswith("!UNUSED!"):
            continue
        kind = _kind(ms.attack_type)
        for severity, triple in ((8, ms.die), (0, ms.miss), (4, ms.hit)):
            att, vict, room = triple
            for persp, tmpl in ((CHAR, att), (VICT, vict), (ROOM, room)):
                if tmpl:
                    rules.append(Rule(tmpl, _hit_builder(persp, severity, kind, ms.name)))
    return rules


# tbaMUD data lives in third_party/tbamud (CircleMUD/DikuMUD license, see the NOTICE there)
DEFAULT_MESSAGES = Path(__file__).resolve().parents[3] / "third_party" / "tbamud" / "messages"


def combat_rules(messages_path: Path = DEFAULT_MESSAGES) -> list[Rule]:
    return weapon_rules() + message_rules(messages_path)
