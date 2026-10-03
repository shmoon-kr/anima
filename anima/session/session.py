"""One character's connection: socket ↔ telnet filter ↔ text adapter ↔ bus, plus login.

Or, with `protocol="mundi"`, a WebSocket to Anima Mundi: its frames are already protocol events
(adapters/mundi_ws), and login is one message with the name, password and the character's class
and sex (Mundi makes the character on its first login; it is our own world).

Login is a system concern, not a Sigil (the password must never pass through a package):
name → password (secret) → press return → menu "1". The same menu answer brings the character
back after death. A name confirmation prompt means the character does not exist: we never
create characters, so the session stops with an error instead.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable

import json

from anima.adapters.mundi_ws.adapter import MundiWsAdapter
from anima.adapters.tbamud_text.parser import TbamudTextAdapter
from anima.bus import Bus
from anima.protocol.commands import CommandQueue, Source
from anima.protocol.envelope import Event, Stamper
from anima.session.telnet import TelnetFilter

LOGIN = Source("system", "session/login", "login dialogue")
IDLE_FLUSH_S = 0.4
RECONNECT_S = (5, 15, 30, 60)


class LoginRefused(Exception):
    pass


@dataclass
class Session:
    agent: str
    host: str
    port: int
    password: str
    bus: Bus
    stamper: Stamper
    interval: float = 0.25
    clock: Callable[[], float] = time.monotonic
    connected: bool = False
    stopped: bool = False
    error: str | None = None
    _writer: asyncio.StreamWriter | None = None
    _menu_entries: int = 0
    queue: CommandQueue = field(init=False)
    on_text: Callable[[str], None] | None = None     # the server's text with its colours, for people watching
    protocol: str = "telnet"                         # or "mundi" (WebSocket, JSON)
    profile: dict = field(default_factory=dict)      # mundi: class, sex, lang for the login message
    _ws: object | None = None

    def __post_init__(self) -> None:
        if self.protocol == "mundi":
            self.adapter = MundiWsAdapter(self.agent, stamper=self.stamper, keep_raw=True)
        else:
            self.adapter = TbamudTextAdapter(self.agent, stamper=self.stamper, keep_raw=True)
        self.queue = CommandQueue(self.agent, self.bus, self.stamper, self._write, interval=self.interval,
                                  clock=self.clock)
        self.bus.subscribe(self._login, agents=[self.agent], types=["connection."])

    # ------------------------------------------------------------ io
    async def _write(self, text: str) -> None:
        if self._ws is not None:
            await self._ws.send(json.dumps({"type": "command", "text": text}))
            return
        if self._writer is None:
            return
        self._writer.write(text.encode("latin-1", errors="replace") + b"\r\n")
        await self._writer.drain()

    def send(self, text: str, source: Source, priority: int = 1) -> None:
        if self.connected:
            self.queue.send(text, source, priority=priority)

    def _publish(self, events: list[Event]) -> None:
        for ev in events:
            self.bus.publish(ev)

    async def run(self) -> None:
        """Connect, keep the connection, reconnect with backoff, until stop()."""
        sender = asyncio.create_task(self.queue.run())
        attempt = 0
        try:
            while not self.stopped:
                try:
                    await self._connect_once()
                    attempt = 0
                except LoginRefused as e:
                    self.error = str(e)
                    self.stopped = True
                    break
                except (OSError, asyncio.IncompleteReadError) as e:
                    self.bus.publish(self.stamper.stamp("connection.closed", {"reason": "error", "detail": str(e)}))
                if self.stopped:
                    break
                await asyncio.sleep(RECONNECT_S[min(attempt, len(RECONNECT_S) - 1)])
                attempt += 1
        finally:
            sender.cancel()

    async def _connect_once(self) -> None:
        if self.protocol == "mundi":
            return await self._connect_ws()
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer
        self.connected = True
        self._menu_entries = 0
        self.bus.publish(self.stamper.stamp("connection.opened", {"host": self.host, "port": self.port}))
        tf = TelnetFilter()
        try:
            while not self.stopped:
                try:
                    data = await asyncio.wait_for(reader.read(4096), IDLE_FLUSH_S)
                except asyncio.TimeoutError:
                    self._publish(self.adapter.flush())
                    continue
                if not data:
                    break
                text, reply, mark = tf.feed(data)
                if reply:
                    writer.write(reply)
                decoded = text.decode("latin-1")
                if self.on_text is not None:
                    self.on_text(decoded)
                self._publish(self.adapter.feed(decoded))
                if mark:
                    self._publish(self.adapter.flush())
        finally:
            self.connected = False
            self._writer = None
            writer.close()
            self._publish(self.adapter.flush())
            self.bus.publish(self.stamper.stamp("connection.closed",
                                                {"reason": "local" if self.stopped else "remote"}))

    async def _connect_ws(self) -> None:
        import websockets
        async with websockets.connect(f"ws://{self.host}:{self.port}") as ws:
            self._ws = ws
            self.connected = True
            self.bus.publish(self.stamper.stamp("connection.opened", {"host": self.host, "port": self.port}))
            try:
                async for frame in ws:
                    if self.stopped:
                        break
                    if isinstance(frame, bytes):
                        continue
                    if self.on_text is not None and (screen := self.adapter.screen(frame)):
                        self.on_text(screen)
                    self._publish(self.adapter.feed(frame))
            except websockets.ConnectionClosed:
                pass
            finally:
                self.connected = False
                self._ws = None
                self.bus.publish(self.stamper.stamp("connection.closed",
                                                    {"reason": "local" if self.stopped else "remote"}))

    async def _ws_login(self) -> None:
        """Mundi's login: one message. The password goes only to the socket, never to the queue."""
        if self._ws is None:
            return
        msg = {"type": "login", "name": self.agent, "password": self.password, "plain": False,
               "lang": self.profile.get("lang", "en")}
        for k in ("class", "sex"):
            if self.profile.get(k):
                msg[k] = self.profile[k]
        await self._ws.send(json.dumps(msg))

    async def stop(self, quit_game: bool = True) -> None:
        self.stopped = True
        if quit_game and self.connected and (self._writer is not None or self._ws is not None):
            # leaving cleanly is the system's job (Sigils may not send quit)
            await self._write("quit")
            await asyncio.sleep(0.5)
        if self._writer is not None:
            self._writer.close()
        if self._ws is not None:
            await self._ws.close()

    def _give_up(self) -> None:
        self.stopped = True
        if self._writer is not None:
            self._writer.close()

    # ------------------------------------------------------------ login
    def _login(self, ev: Event) -> None:
        if ev.type == "connection.login_prompt":
            stage = ev.data.get("stage")
            if self.protocol == "mundi":
                if stage == "name":
                    asyncio.get_running_loop().create_task(self._ws_login())
                return
            if stage == "name":
                self.queue.send(self.agent, LOGIN)
            elif stage == "password":
                self.queue.send(self.password, LOGIN, secret=True)
            elif stage == "press_return":
                self.queue.send("", LOGIN)
            elif stage == "menu":
                self._menu_entries += 1
                if self._menu_entries > 5:
                    self.error = "stuck at the menu"
                    self.stopped = True
                    return
                self.queue.send("1", LOGIN)
            elif stage == "confirm_name":
                self.error = f"character {self.agent} does not exist (server asked to confirm a new name)"
                self.queue.send("N", LOGIN)              # never create characters
                asyncio.get_running_loop().call_later(1.0, self._give_up)
        elif ev.type == "connection.login_failed":
            self.error = f"login failed: {ev.data.get('reason')}"
            self.stopped = True
