"""The game's clock, which may run faster than the wall's (Anima Mundi's `--speed N`, `[server] speed`).

Everything that measures game time — the runtime's clock, the party board, the command queue's spacing,
the ticks, recordings' timestamps — reads `now()`/`wall()` and waits with `sleep()`, so a bot run at N
times speed behaves as at 1, only sooner. Things that wait on the outside world (an LLM's answer, a
reconnect to a server that is down) keep real time. The scale is 1 unless set.
"""
from __future__ import annotations

import asyncio
import time

_scale = 1.0
_mono0 = time.monotonic()                 # the wall's monotonic clock when the scale was last set ...
_base = [_mono0, time.time()]             # ... and the game's now() and wall() then


def set_scale(n: float) -> None:
    """From here on, game time runs `n` times the wall's (now() and wall() stay continuous)."""
    global _scale, _mono0
    _base[0], _base[1] = now(), wall()
    _mono0 = time.monotonic()
    _scale = max(1.0, float(n))


def scale() -> float:
    return _scale


def now() -> float:
    """A monotonic game clock (seconds)."""
    return _base[0] + (time.monotonic() - _mono0) * _scale


def wall() -> float:
    """Game time as unix seconds (recordings' timestamps)."""
    return _base[1] + (time.monotonic() - _mono0) * _scale


async def sleep(game_seconds: float) -> None:
    await asyncio.sleep(game_seconds / _scale)
