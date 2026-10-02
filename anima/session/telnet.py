"""Minimal telnet: strip negotiation, refuse every option, report GA/EOR as prompt marks.

tbaMUD negotiates MSDP, MSSP, TTYPE, CHARSET and more on connect (protocol.c). Phase 1 uses plain
text only, so every DO is answered WONT and every WILL is answered DONT. The server then treats
us as a plain client, which is what the text adapter expects.
"""
from __future__ import annotations

from dataclasses import dataclass, field

IAC, DONT, DO, WONT, WILL, SB, SE, GA, EOR = 255, 254, 253, 252, 251, 250, 240, 249, 239


@dataclass
class TelnetFilter:
    _state: str = "data"
    _cmd: int = 0
    _out: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> tuple[bytes, bytes, bool]:
        """Returns (text bytes, bytes to send back, prompt_mark_seen)."""
        text = bytearray()
        reply = bytearray()
        mark = False
        for b in data:
            st = self._state
            if st == "data":
                if b == IAC:
                    self._state = "iac"
                else:
                    text.append(b)
            elif st == "iac":
                if b == IAC:
                    text.append(IAC)
                    self._state = "data"
                elif b in (DO, DONT, WILL, WONT):
                    self._cmd = b
                    self._state = "opt"
                elif b == SB:
                    self._state = "sb"
                elif b in (GA, EOR):
                    mark = True
                    self._state = "data"
                else:
                    self._state = "data"
            elif st == "opt":
                if self._cmd == DO:
                    reply += bytes([IAC, WONT, b])
                elif self._cmd == WILL:
                    reply += bytes([IAC, DONT, b])
                self._state = "data"
            elif st == "sb":
                if b == IAC:
                    self._state = "sb_iac"
            elif st == "sb_iac":
                self._state = "data" if b == SE else "sb"
        return bytes(text), bytes(reply), mark
