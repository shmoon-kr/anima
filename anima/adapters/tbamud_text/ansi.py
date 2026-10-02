"""ANSI SGR parsing. Colour is kept as a hint (tbaMUD marks room names, objects, mobs by colour).

Colour state carries across lines, like a terminal: tbaMUD often sends the reset code at the
start of the next line (fight.c, act.informative.c).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
# Other escape sequences (cursor moves, clear screen) carry no meaning for us.
OTHER_ESC_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]|\x1b[()][A-Za-z0-9]")

_FG = {30: "black", 31: "red", 32: "green", 33: "yellow", 34: "blue", 35: "magenta", 36: "cyan", 37: "white"}


@dataclass(frozen=True)
class Color:
    fg: str | None = None
    bold: bool = False


PLAIN = Color()


@dataclass
class Segment:
    text: str
    color: Color


@dataclass
class StyledLine:
    text: str                 # visible text, escapes removed
    segments: list[Segment]
    raw: str

    @property
    def first_color(self) -> Color:
        """Colour of the first non-space character (PLAIN if the line is blank)."""
        for seg in self.segments:
            if seg.text.strip():
                return seg.color
        return PLAIN

    def color_at(self, index: int) -> Color:
        pos = 0
        for seg in self.segments:
            if index < pos + len(seg.text):
                return seg.color
            pos += len(seg.text)
        return PLAIN


def _apply(color: Color, params: str) -> Color:
    codes = [int(c) for c in params.split(";") if c != ""] or [0]
    fg, bold = color.fg, color.bold
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            fg, bold = None, False
        elif c == 1:
            bold = True
        elif c == 22:
            bold = False
        elif c in _FG:
            fg = _FG[c]
        elif c == 39:
            fg = None
        elif c in (38, 48):            # extended colour: 38;5;n or 38;2;r;g;b
            if i + 1 < len(codes) and codes[i + 1] == 5:
                i += 2
            elif i + 1 < len(codes) and codes[i + 1] == 2:
                i += 4
            if c == 38:
                fg = "other"
        i += 1
    return Color(fg, bold)


class AnsiState:
    """Parses lines while carrying the current colour from one line to the next."""

    def __init__(self) -> None:
        self.color = PLAIN

    def parse(self, raw: str) -> StyledLine:
        raw_clean = OTHER_ESC_RE.sub("", raw)
        segments: list[Segment] = []
        pos = 0
        for m in SGR_RE.finditer(raw_clean):
            if m.start() > pos:
                segments.append(Segment(raw_clean[pos:m.start()], self.color))
            self.color = _apply(self.color, m.group(1))
            pos = m.end()
        if pos < len(raw_clean):
            segments.append(Segment(raw_clean[pos:], self.color))
        text = "".join(s.text for s in segments)
        return StyledLine(text=text, segments=segments, raw=raw)


def strip_ansi(s: str) -> str:
    return SGR_RE.sub("", OTHER_ESC_RE.sub("", s))
