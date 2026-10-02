"""tbaMUD text → protocol events (docs/PROTOCOL.md part 1).

Server output is cut into blocks at the prompt (comm.c make_prompt): everything the server
sent between two prompts is one block. Multi-line outputs (room, group table, inventory, ...)
are recognised by their order inside the block; single lines go through the rule table.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from anima.protocol.envelope import SELF, Event, Stamper

from .act import RuleSet
from .ansi import PLAIN, AnsiState, Segment, StyledLine, strip_ansi
from .combat import combat_rules
from .rules import RULES, normalize_name

# comm.c:1198-1291 make_prompt: [i%d ][%dH ][%dM ][%dV ][BUILDWALKING ][AFK ][(news) ][(motd) ]>
PROMPT_RE = re.compile(
    r"^((?:\x1b\[[0-9;]*m)*)(?:i\d+ )?(?:(-?\d+)H )?(?:(-?\d+)M )?(?:(-?\d+)V )?"
    r"(?:BUILDWALKING )?(?:AFK )?(?:\(news\) )?(?:\(motd\) )?> ")

# interpreter.c nanny / config.c menu: prompts sent without a newline
LOGIN_MARKERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"By what name do you wish to be known\? ?"), "name"),
    (re.compile(r"Invalid name, please try another\.\s*Name: ?"), "name"),
    (re.compile(r"Did I get that right, \S+ \(Y/N\)\? ?"), "confirm_name"),
    (re.compile(r"Wrong password\.\s*Password: ?"), "password_retry"),
    (re.compile(r"Password: ?"), "password"),
    (re.compile(r"\*\*\* PRESS RETURN: ?"), "press_return"),
    (re.compile(r"Make your choice: ?"), "menu"),
]
_LOGIN_ANY = re.compile("|".join(f"(?:{p.pattern})" for p, _ in LOGIN_MARKERS))

EXITS_RE = re.compile(r"^\[ Exits: (.*)\]\s*$")
GROUP_ROW_RE = re.compile(
    r"^(\S+)\s*: \[\s*(-?\d+)/(\d+)\s*\]H \[\s*(-?\d+)/(\d+)\s*\]M \[\s*(-?\d+)/(\d+)\s*\]V")
EQUIP_RE = re.compile(r"^<([^>]+)>\s+(.*)$")
COUNT_RE = re.compile(r"^\(\s*(\d+)\) (.*)$")
EXIT_LIST_RE = re.compile(r"^(north|east|south|west|up|down)\s+- (.*)$")
PROFICIENCY = ("not learned", "awful", "bad", "poor", "average", "fair", "good", "very good", "superb")
# spec_procs.c list_skills "%-20s %s" with how_good() " (good)"
SKILL_ROW_RE = re.compile(r"^(\S.*?)\s+\((" + "|".join(sorted(PROFICIENCY, key=len, reverse=True)) + r")\)\s*$")
# shop.c shopping_list / list_object: " %2d)  %9s   %-48s %6d[ qp]"
SHOP_HEADER_RE = re.compile(r"^ ##\s+Available\s+Item\s+Cost\s*$")
SHOP_ROW_RE = re.compile(r"^\s*(\d+)\)\s+(Unlimited|\d+)\s+(.*?)\s+(\d+)( qp)?\s*$")
DIR_ABBR = {"n": "north", "e": "east", "s": "south", "w": "west", "u": "up", "d": "down"}

# act.informative.c list_one_char positions[] and suffix flags
POSITION_SUFFIX = [
    (" is lying here, dead.", "dead"), (" is lying here, mortally wounded.", "mortally_wounded"),
    (" is lying here, incapacitated.", "incapacitated"), (" is lying here, stunned.", "stunned"),
    (" is sleeping here.", "sleeping"), (" is resting here.", "resting"), (" is sitting here.", "sitting"),
    (" is standing here.", "standing"),
]
OCCUPANT_FLAGS = [(" (invisible)", "invisible"), (" (hidden)", "hidden"), (" (linkless)", "linkless"),
                  (" (writing)", "writing"), (" (buildwalk)", "buildwalk"), (" (AFK)", "afk")]
PLAYER_ONLY_FLAGS = {"linkless", "writing", "buildwalk", "afk"}

# score lines (act.informative.c do_score) that only feed char.score
SCORE_RES = [
    (re.compile(r"^You have (-?\d+)\((\d+)\) hit, (-?\d+)\((\d+)\) mana and (-?\d+)\((\d+)\) movement points\.$"), "vitals"),
    (re.compile(r"^You have (-?\d+) exp, (-?\d+) gold coins, and (-?\d+) questpoints\.$"), "exp_gold"),
    (re.compile(r"^This ranks you as .* \(level (\d+)\)\.$"), "level"),
    (re.compile(r"^You have (\d+) practice sessions? remaining\.$"), "practices"),
    (re.compile(r"^You are \d+ years old\..*$"), None),
    (re.compile(r"^Your armor class is -?\d+/10, and your alignment is -?\d+\.$"), "ac_align"),
    (re.compile(r"^You need -?\d+ exp to reach your next level\.$"), None),
    (re.compile(r"^You have earned -?\d+ quest points\.$"), None),
    (re.compile(r"^You have completed \d+ quests?, .*$"), None),
    (re.compile(r"^You have been playing for \d+ days? and \d+ hours?\.$"), None),
    (re.compile(r"^You know of the following (skill|spell)s:$"), None),
]

LineFilter = Callable[[StyledLine], bool]


class TbamudTextAdapter:
    def __init__(self, agent: str, stamper: Stamper | None = None, keep_raw: bool = True,
                 line_filter: LineFilter | None = None) -> None:
        self.agent = agent
        self.stamper = stamper or Stamper(agent)
        self.keep_raw = keep_raw
        self.line_filter = line_filter
        self.rules = RuleSet(RULES + combat_rules())
        self.ansi = AnsiState()
        self._partial = ""
        self._block: list[StyledLine] = []
        self._group_names: set[str] = set()
        self._greeting = False      # between client detection and the name prompt: lib/text/greetings
        self._out: list[Event] = []

    # ---------------------------------------------------------------- input
    def feed(self, text: str) -> list[Event]:
        """Feed decoded server text (telnet already removed). Partial lines are kept."""
        data = self._partial + text.replace("\r", "")
        *complete, self._partial = data.split("\n")
        for line in complete:
            self._physical_line(line)
        if self._partial:
            if PROMPT_RE.match(self._partial) or self._ends_with_login_marker(self._partial):
                line, self._partial = self._partial, ""
                self._physical_line(line)
        return self._take()

    def feed_line(self, line: str) -> list[Event]:
        """Feed one complete physical line (replay)."""
        self._physical_line(line.rstrip("\r\n"))
        return self._take()

    def flush(self) -> list[Event]:
        """End of input or idle: treat the partial line and the open block as complete."""
        if self._partial:
            line, self._partial = self._partial, ""
            self._physical_line(line)
        self._end_block()
        return self._take()

    def _take(self) -> list[Event]:
        out, self._out = self._out, []
        return out

    # ---------------------------------------------------------------- lines
    def _physical_line(self, raw: str) -> None:
        while True:
            m = PROMPT_RE.match(raw)
            if not m:
                break
            if m.group(1):
                self.ansi.parse(m.group(1))       # keep colour state
            self._end_block()
            self._greeting = False                    # a game prompt means we are in the game
            hp, mp, mv = (int(x) if x is not None else None for x in m.group(2, 3, 4))
            self._emit("prompt", {"hp": hp, "mp": mp, "mv": mv}, [m.group(0)[len(m.group(1)):].rstrip()])
            raw = raw[m.end():]
            if not raw:
                return
        styled = self.ansi.parse(raw)
        if _LOGIN_ANY.search(styled.text):
            self._login_line(styled)
            return
        if self.line_filter and self.line_filter(styled):
            return
        self._block.append(styled)

    def _ends_with_login_marker(self, partial: str) -> bool:
        text = strip_ansi(partial)
        return any(not text[m.end():].strip() for m in _LOGIN_ANY.finditer(text))

    def _login_line(self, styled: StyledLine) -> None:
        """Login prompts arrive without newline and get glued to following text."""
        text = styled.text
        pos = 0
        for m in _LOGIN_ANY.finditer(text):
            before = text[pos:m.start()]
            if before.strip():
                self._block.append(_plain(before))
            self._end_block()
            stage = next(st for p, st in LOGIN_MARKERS if p.fullmatch(m.group(0)))
            if stage == "password_retry":
                self._emit("connection.login_failed", {"reason": "wrong_password"}, [m.group(0)])
                stage = "password"
            elif "Invalid name" in m.group(0):
                self._emit("connection.login_failed", {"reason": "invalid_name"}, [m.group(0)])
            self._emit("connection.login_prompt", {"stage": stage}, [m.group(0).strip()])
            # after name/password the server shows greetings and the MOTD until PRESS RETURN
            self._greeting = stage in ("name", "password", "confirm_name")
            pos = m.end()
        rest = text[pos:]
        if rest.strip():
            self._block.append(_plain(rest))

    # ---------------------------------------------------------------- blocks
    def _end_block(self) -> None:
        lines, self._block = self._block, []
        if lines:
            self._parse_block(lines)

    def _parse_block(self, lines: list[StyledLine]) -> None:
        score: dict[str, Any] = {}
        score_raw: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            ln = lines[i]
            text = ln.text.rstrip()
            if not text.strip():
                i += 1
                continue
            if text.startswith("Attempting to Detect Client"):      # comm.c:1647
                self._greeting = True
                i += 1
                continue
            if self._greeting:
                if self.rules.match(text) is None:   # greeting / MOTD text
                    i += 1
                    continue
                self._greeting = False               # e.g. "Reconnecting." (no PRESS RETURN)
            if SHOP_HEADER_RE.match(text):
                i = self._shop_list(lines, i)
                continue
            j = self._try_room(lines, i)
            if j is not None:
                i = j
                continue
            if text == "Your group consists of:":
                i = self._group_table(lines, i)
                continue
            if text == "You are carrying:":
                i = self._inventory(lines, i)
                continue
            if text == "You are using:":
                i = self._equipment(lines, i)
                continue
            if text == "Obvious exits:":
                i = self._exits_list(lines, i)
                continue
            if self._score_line(text, score):
                score_raw.append(text)
                if text.startswith("You know of the following"):
                    i = self._skills_list(lines, i + 1)
                    continue
                i += 1
                continue
            self._single(text)
            i += 1
        if score:
            vit = score.pop("_vitals", None)
            if vit:
                self._emit("char.vitals_max", vit, score_raw if self.keep_raw else None)
            if score:
                self._emit("char.score", score, score_raw)

    def _single(self, text: str) -> None:
        group = text.startswith("[Group] ")                 # comm.c:2562 send_to_group prefix
        m = self.rules.match(text[len("[Group] "):] if group else text)
        if m is None:
            self._emit("unknown", {"text": text}, None)
            return
        rule, groups = m
        res = rule.build(groups)
        if res is not None:
            type_, data = res
            if group and type_ == "comm.say":                # "[Group] X says, '...'" = gtell
                type_ = "comm.gtell"
            self._emit(type_, data, [text])

    # ---- room (act.informative.c look_at_room) ----
    def _try_room(self, lines: list[StyledLine], i: int) -> int | None:
        title = lines[i]
        if not _all_color(title, "yellow") or title.text.startswith(" "):
            return None
        # find the exits line: description lines in between, nothing else
        e = None
        for k in range(i + 1, min(len(lines), i + 60)):
            if EXITS_RE.match(lines[k].text.strip()):
                e = k
                break
        if e is None:
            return None
        if e > i + 1 and not lines[i + 1].text.startswith("   "):
            return None
        exits = _parse_exits(EXITS_RE.match(lines[e].text.strip()).group(1))
        desc = "\n".join(l.text.rstrip() for l in lines[i + 1:e])
        objects: list[dict[str, Any]] = []
        occupants: list[dict[str, Any]] = []
        k = e + 1
        while k < len(lines):
            ln = lines[k]
            t = ln.text.rstrip()
            if not t.strip():
                k += 1
                continue
            color = ln.first_color.fg
            if t.startswith("...") and occupants:          # sanctuary / blind continuation
                flag = "sanctuary" if "glows with a bright light" in t else "blind" if "groping around blindly" in t else None
                if flag:
                    occupants[-1]["flags"].append(flag)
                k += 1
                continue
            if color == "green":
                m = COUNT_RE.match(t)
                objects.append({"text": m.group(2), "count": int(m.group(1))} if m else {"text": t, "count": 1})
            elif color == "yellow":
                occupants.append(self._occupant(ln, t))
            else:
                break
            k += 1
        raw = [l.text.rstrip() for l in lines[i:k]] if self.keep_raw else None
        self._emit("room", {"name": title.text.strip(), "desc": desc, "exits": exits,
                            "objects": objects, "occupants": occupants, "dark": False}, raw)
        return k

    def _occupant(self, ln: StyledLine, t: str) -> dict[str, Any]:
        hints: list[str] = []
        flags: list[str] = []
        m = re.match(r"^\((group|leader)\) ", t)
        if m:
            c = ln.color_at(1)
            mine = c.fg == "green"
            hints.append(("my_" if mine else "other_") + ("group_leader" if m.group(1) == "leader" else "group"))
            t = t[m.end():]
        aura = re.match(r"^\*?\((Red|Blue) Aura\) ", t)
        if t.startswith("*"):
            flags.append("invisible")
        if aura:
            flags.append(aura.group(1).lower() + "_aura")
            t = t[aura.end():]
        t = t.lstrip("*")
        occ: dict[str, Any] = {"text": t, "flags": flags, "hints": hints}
        body = t
        for suffix, pos in POSITION_SUFFIX:
            if body.endswith(suffix):
                occ["position"] = pos
                body = body[: -len(suffix)]
                break
        else:
            fm = re.search(r" is here, fighting (.*)!$", body)
            if fm:
                occ["position"] = "fighting"
                target = fm.group(1)
                occ["fighting"] = SELF if target == "YOU" else normalize_name(target)
                body = body[: fm.start()]
        for suffix, flag in OCCUPANT_FLAGS:
            if suffix in body:
                flags.append(flag)
                body = body.replace(suffix, "")
        if PLAYER_ONLY_FLAGS & set(flags):
            hints.append("player")
        first = body.split(" ", 1)[0]
        if first in self._group_names:
            hints.append("group_member")
        return occ

    # ---- group table (act.other.c print_group) ----
    def _group_table(self, lines: list[StyledLine], i: int) -> int:
        members = []
        k = i + 1
        while k < len(lines):
            m = GROUP_ROW_RE.match(lines[k].text)
            if not m:
                break
            bracket = lines[k].text.index("[")
            leader = lines[k].color_at(bracket).bold
            members.append({"name": m.group(1), "hp": int(m.group(2)), "hp_max": int(m.group(3)),
                            "mp": int(m.group(4)), "mp_max": int(m.group(5)),
                            "mv": int(m.group(6)), "mv_max": int(m.group(7)), "leader": leader})
            k += 1
        self._group_names = {m["name"] for m in members}
        self._emit("group.status", {"members": members}, [l.text for l in lines[i:k]] if self.keep_raw else None)
        return k

    # ---- inventory / equipment (act.informative.c do_inventory, do_equipment) ----
    def _inventory(self, lines: list[StyledLine], i: int) -> int:
        items = []
        k = i + 1
        while k < len(lines):
            t = lines[k].text.rstrip()
            if not t.strip():
                k += 1
                continue
            if t.strip() == "Nothing.":
                k += 1
                break
            if self.rules.match(t):
                break
            m = COUNT_RE.match(t)
            items.append({"text": m.group(2), "count": int(m.group(1))} if m else {"text": t, "count": 1})
            k += 1
        self._emit("items.inventory", {"items": items}, [l.text for l in lines[i:k]] if self.keep_raw else None)
        return k

    def _equipment(self, lines: list[StyledLine], i: int) -> int:
        slots = []
        k = i + 1
        while k < len(lines):
            t = lines[k].text.rstrip()
            m = EQUIP_RE.match(t)
            if m:
                slots.append({"slot": m.group(1), "text": m.group(2)})
                k += 1
                continue
            if t.strip() == "Nothing.":
                k += 1
            break
        self._emit("items.equipment", {"slots": slots}, [l.text for l in lines[i:k]] if self.keep_raw else None)
        return k

    # ---- shop list (shop.c shopping_list) ----
    def _shop_list(self, lines: list[StyledLine], i: int) -> int:
        items = []
        k = i + 1
        while k < len(lines):
            t = lines[k].text.rstrip()
            if set(t.strip()) == {"-"}:
                k += 1
                continue
            m = SHOP_ROW_RE.match(t)
            if not m:
                break
            item: dict[str, Any] = {"text": m.group(3), "price": int(m.group(4))}
            if m.group(2) != "Unlimited":
                item["count"] = int(m.group(2))
            items.append(item)
            k += 1
        self._emit("shop.list", {"items": items}, [l.text for l in lines[i:k]] if self.keep_raw else None)
        return k

    # ---- exits list (act.informative.c do_exits) ----
    def _exits_list(self, lines: list[StyledLine], i: int) -> int:
        exits = []
        k = i + 1
        while k < len(lines):
            t = lines[k].text.rstrip()
            m = EXIT_LIST_RE.match(t)
            if m:
                name = m.group(2)
                closed = re.match(r"The .* is closed( and hidden)?\.$", name) is not None
                exits.append({"dir": m.group(1), "room_name": None if closed or name == "Too dark to tell." else name,
                              "closed": closed})
                k += 1
                continue
            if t.strip() == "None.":
                k += 1
            break
        self._emit("room.exits_listed", {"exits": exits}, [l.text for l in lines[i:k]] if self.keep_raw else None)
        return k

    # ---- practice list (spec_procs.c list_skills) ----
    def _skills_list(self, lines: list[StyledLine], k: int) -> int:
        skills: dict[str, str] = {}
        start = k
        while k < len(lines):
            m = SKILL_ROW_RE.match(lines[k].text.rstrip())
            if not m:
                break
            skills[m.group(1).strip()] = m.group(2)
            k += 1
        self._emit("char.skills", {"skills": skills}, [l.text for l in lines[start:k]] if self.keep_raw else None)
        return k

    # ---- score (act.informative.c do_score) ----
    def _score_line(self, text: str, score: dict[str, Any]) -> bool:
        for rx, kind in SCORE_RES:
            m = rx.match(text)
            if not m:
                continue
            if kind == "vitals":
                hp, hpm, mp, mpm, mv, mvm = (int(x) for x in m.groups())
                score["_vitals"] = {"hp": hpm, "mp": mpm, "mv": mvm}
            elif kind == "exp_gold":
                score["exp"], score["gold"] = int(m.group(1)), int(m.group(2))
            elif kind == "level":
                score["level"] = int(m.group(1))
            elif kind == "practices":
                score["practices"] = int(m.group(1))
            elif kind == "ac_align":
                pass
            return True
        return False

    # ---------------------------------------------------------------- out
    def _emit(self, type_: str, data: dict[str, Any], raw: list[str] | None) -> None:
        self._out.append(self.stamper.stamp(type_, data, raw if self.keep_raw else None))


def _plain(text: str) -> StyledLine:
    return StyledLine(text=text, segments=[Segment(text, PLAIN)], raw=text)


def _all_color(ln: StyledLine, fg: str) -> bool:
    visible = [s for s in ln.segments if s.text.strip()]
    return bool(visible) and all(s.color.fg == fg for s in visible)


def _parse_exits(body: str) -> list[dict[str, Any]]:
    body = body.strip()
    if body.startswith("None!"):
        return []
    exits = []
    for tok in body.split():
        closed = tok.startswith("(") and tok.endswith(")")
        d = tok.strip("()")
        exits.append({"dir": DIR_ABBR.get(d, d), "closed": closed})
    return exits
