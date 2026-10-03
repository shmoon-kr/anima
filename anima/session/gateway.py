"""The web entrance to the view stream (atrium's spectator page, read-only).

A browser connects to ws://127.0.0.1:[web] port/ws/view?token=...&lang=en|ko, the token a short-lived one the
web app (muse's atrium) signed for a logged-in person with the key both share ([web] stream_key).
It gets what `anima play` gets from `view` (coloured text of the character watched, the party every
second) plus the narration and event lines the terminal would print, already worded here. From the
browser only a change of character is taken: typed lines, sends and takes are not (v1 is for
watching). The format is in docs/PROTOCOL.md §11.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

STATUS_EVERY_S = 1.0
SCROLLBACK = 4000            # characters of a character's recent screen sent when it is chosen


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(payload: dict[str, Any], key: str) -> str:
    """payload (json) . HMAC-SHA256 of it. The web app makes these; here for tests and tools."""
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    mac = hmac.new(key.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(mac)}"


def verify_token(token: str, key: str, target: str, now: float | None = None) -> dict[str, Any] | None:
    """The payload if the signature is right, it has not expired and it is for this target; else None."""
    if not key or not token or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    want = hmac.new(key.encode(), body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(want, _unb64(mac)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("target") != target:
        return None
    if float(payload.get("exp", 0)) < (time.time() if now is None else now):
        return None
    return payload


def browser_message(raw: str, members: list[str]) -> list[str] | None:
    """What a browser may ask: to watch another member ({"agents": [name]}). Anything else, None."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    agents = msg.get("agents") if isinstance(msg, dict) else None
    if isinstance(agents, list) and len(agents) == 1 and agents[0] in members:
        return agents
    return None


LANGS = ("en", "ko")         # what a viewer may read in (first version)


def outgoing(msg: dict[str, Any], describe: Callable[[dict], str | None],
             narrate: Callable[[dict, str], str | None], lang: str = "en") -> list[dict[str, Any]]:
    """A viewer-queue message as the browser's messages, in the viewer's language: text in `lang`
    when the server gave it (else the screen language), status as it is, an event as its event line
    and narration line (the terminal's wording), when it has them."""
    k = msg.get("k")
    if k == "text":
        out = {"k": "text", "a": msg.get("a"), "s": (msg.get("t") or {}).get(lang) or msg["s"]}
        if msg.get("reset"):
            out["reset"] = True
        return [out]
    if k != "ev":
        return [msg] if k == "status" else []
    ev, a = msg["ev"], msg.get("a")
    out = []
    if ev.get("type") == "world.time" and (ev.get("data") or {}).get("phase"):
        out.append({"k": "clock", "a": a, "phase": ev["data"]["phase"]})      # the header's game time
    if line := describe(ev):
        out.append({"k": "evline", "a": a, "s": line})
    if line := narrate(ev, lang):
        out.append({"k": "narr", "a": a, "s": line})
    return out


class Gateway:
    """Serves the view stream to browsers; `sup` is the Supervisor (its viewer queues and screens)."""

    def __init__(self, sup: Any, key: str, target: str, describe: Callable, narrate: Callable) -> None:
        self.sup, self.key, self.target = sup, key, target
        self.describe, self.narrate = describe, narrate

    async def serve(self, host: str, port: int) -> Any:
        from websockets.asyncio.server import serve
        return await serve(self.handle, host, port)

    async def handle(self, ws: Any) -> None:
        query = parse_qs(urlparse(ws.request.path).query)
        payload = verify_token((query.get("token") or [""])[0], self.key, self.target)
        if payload is None:
            await ws.close(4401, "token")
            return
        members = list(self.sup.agents)
        lang = (query.get("lang") or [""])[0]
        lang = lang if lang in LANGS else LANGS[0]
        first = (query.get("agent") or [""])[0]
        agents = {first if first in members else members[0]}
        q: asyncio.Queue = asyncio.Queue()
        entry = (agents, q)
        self.sup._viewers.append(entry)
        self._scrollback(agents, q, lang)

        async def from_browser() -> None:
            async for raw in ws:
                wanted = browser_message(raw, members)
                if wanted is None:
                    continue                          # read-only: typed lines and the rest are dropped
                agents.clear()
                agents.update(wanted)
                self._scrollback(agents, q, lang)

        async def status() -> None:
            while True:
                q.put_nowait({"k": "status", "party": self.sup.party_line()})
                await asyncio.sleep(STATUS_EVERY_S)

        inp, st = asyncio.create_task(from_browser()), asyncio.create_task(status())
        try:
            while not inp.done():
                get = asyncio.create_task(q.get())
                done, _ = await asyncio.wait({get, inp}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    break
                for out in outgoing(get.result(), self.describe, self.narrate, lang):
                    await ws.send(json.dumps(out, ensure_ascii=False, default=str))
        except Exception:                             # noqa: BLE001 — the browser went away
            pass
        finally:
            inp.cancel()
            st.cancel()
            if entry in self.sup._viewers:
                self.sup._viewers.remove(entry)

    def _scrollback(self, agents: set[str], q: asyncio.Queue, lang: str) -> None:
        for a in agents:
            text = (getattr(self.sup, "_screens", {}).get(a) or {}).get(lang) or self.sup._screen.get(a)
            if text:
                q.put_nowait({"k": "text", "a": a, "s": text[-SCROLLBACK:], "reset": True})
