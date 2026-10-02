"""Event envelope shared by every message on the bus (docs/PROTOCOL.md §2)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

PROTOCOL_VERSION = 0
SELF = "self"


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    agent: str = ""
    seq: int = 0
    t: float = 0.0
    raw: list[str] | None = None
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"v": self.v, "t": self.t, "seq": self.seq, "agent": self.agent,
                             "type": self.type, "data": self.data}
        if self.raw:
            d["raw"] = self.raw
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(type=d["type"], data=d.get("data", {}), agent=d.get("agent", ""),
                   seq=d.get("seq", 0), t=d.get("t", 0.0), raw=d.get("raw"), v=d.get("v", PROTOCOL_VERSION))


@dataclass
class Stamper:
    """Assigns agent, monotonically increasing seq and time to events of one agent."""
    agent: str
    clock: Callable[[], float] = time.time
    seq: int = field(default=0)

    def stamp(self, type: str, data: dict[str, Any], raw: list[str] | None = None) -> Event:
        self.seq += 1
        return Event(type=type, data=data, agent=self.agent, seq=self.seq, t=self.clock(), raw=raw)
