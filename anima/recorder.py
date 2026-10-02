"""JSONL recording and replay (PROTOCOL.md §10).

One envelope per line, every message type, in publish order. Recordings keep each agent's own
point of view ("self"). As a last line of defence the recorder redacts known secret strings
from anything it writes, even though secrets should already be masked upstream (§5).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import IO, Iterable, Iterator

from anima.bus import Bus
from anima.protocol.commands import SECRET_MASK
from anima.protocol.envelope import Event


class Recorder:
    def __init__(self, path: Path, secrets: Iterable[str] = ()) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f: IO[str] = path.open("a", encoding="utf-8")
        self._secrets = [s for s in secrets if s]
        self.count = 0

    def attach(self, bus: Bus, agents: Iterable[str] | None = None):
        return bus.subscribe(self.write, agents=agents)

    def write(self, ev: Event) -> None:
        line = ev.to_json()
        for s in self._secrets:
            if s in line:
                line = line.replace(s, SECRET_MASK)
        self._f.write(line + "\n")
        self._f.flush()
        self.count += 1

    def close(self) -> None:
        self._f.close()


def read_recording(path: Path) -> Iterator[Event]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield Event.from_dict(json.loads(line))


async def replay_recording(path: Path, bus: Bus, speed: float = 0.0) -> int:
    """Publish a recording onto a bus. speed=0: as fast as possible; 1.0: real time."""
    n = 0
    prev_t: float | None = None
    for ev in read_recording(path):
        if speed > 0 and prev_t is not None and ev.t > prev_t:
            await asyncio.sleep((ev.t - prev_t) / speed)
        prev_t = ev.t
        bus.publish(ev)
        n += 1
    return n
