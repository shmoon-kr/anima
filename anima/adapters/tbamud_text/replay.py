"""Replay mud-agents tintin logs through the text adapter.

The logs are raw tintin output: server text with ANSI codes, plus lines tintin itself printed
(`#...` system messages, `[Name] ...` #showme lines in bold yellow, echoed commands). Those are
not server output and are dropped here only, never in the live adapter.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterator

from anima.protocol.envelope import Event, Stamper

from .ansi import StyledLine
from .parser import TbamudTextAdapter

_SHOWME_RE = re.compile(r"^\[([A-Z][a-z]+)\] ")
_TINTIN_CMD_RE = re.compile(r"^#[A-Za-z]")
_SERVER_BRACKETS = {"Group", "Client"}   # comm.c send_to_group, interpreter.c get_protocols


def tintin_noise(ln: StyledLine) -> bool:
    text = ln.text.lstrip()
    if _TINTIN_CMD_RE.match(text):          # "#OK", "#SHORTEST PATH", "#read ..." (not the shop's " ##")
        return True
    m = _SHOWME_RE.match(text)
    return bool(m) and m.group(1) not in _SERVER_BRACKETS


def replay_file(path: Path, agent: str | None = None, keep_raw: bool = True) -> Iterator[Event]:
    agent = agent or path.stem
    clock_t = [0.0]

    def clock() -> float:          # logs carry no time: use line order
        return clock_t[0]

    adapter = TbamudTextAdapter(agent, stamper=Stamper(agent, clock=clock), keep_raw=keep_raw,
                                line_filter=tintin_noise)
    with path.open(encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            clock_t[0] = float(lineno)
            yield from adapter.feed_line(line)
    yield from adapter.flush()


def stats(events: Iterator[Event], top: int = 40) -> dict:
    types: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    total = 0
    for ev in events:
        total += 1
        types[ev.type] += 1
        if ev.type == "unknown":
            unknown[_shape(ev.data["text"])] += 1
    return {"total": total, "types": types, "unknown": unknown.most_common(top),
            "unknown_ratio": types["unknown"] / max(1, total - types["prompt"])}


def _shape(text: str) -> str:
    """Group similar unknown lines: digits → #."""
    return re.sub(r"\d+", "#", text)[:120]
