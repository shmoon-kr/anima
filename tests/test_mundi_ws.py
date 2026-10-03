"""Session over WebSocket to a fake Anima Mundi: the login message, frames as events, commands."""
import asyncio
import json

import websockets

from anima.adapters.mundi_ws.adapter import MundiWsAdapter, vnum
from anima.bus import Bus
from anima.protocol.commands import Source
from anima.protocol.envelope import Stamper
from anima.recorder import Recorder, read_recording
from anima.session.session import Session

ROOM = {"id": "tba:30:room:3001", "name": "The Temple Of Midgaard", "desc": "...",
        "exits": [{"dir": "south", "closed": False, "to_id": "tba:30:room:3005"}], "objects": [],
        "occupants": [], "dark": False}


def env(type_, data, text=(), seq=1):
    return json.dumps({"v": 0, "t": 1.0, "tick": 5, "seq": seq, "agent": "Vallen", "type": type_,
                       "data": data, "text": list(text)})


class FakeMundi:
    """Mundi's server side (crates/mundi-server): prompt, one login message, then events."""

    def __init__(self):
        self.received: list[dict] = []

    async def handle(self, ws):
        await ws.send(env("connection.login_prompt", {"stage": "name"}, ["By what name do you wish to be known?"]))
        async for frame in ws:
            msg = json.loads(frame)
            self.received.append(msg)
            if msg["type"] == "login":
                await ws.send(env("connection.in_game", {"how": "entered"}, seq=2))
                await ws.send(env("room", ROOM, ["\x1b[33mThe Temple Of Midgaard\x1b[0m"], seq=3))
                await ws.send(env("prompt", {"hp": 20, "mp": 100, "mv": 82}, ["20H 100M 82V > "], seq=4))
            elif msg == {"type": "command", "text": "quit"}:
                await ws.close()


def test_adapter_keeps_the_event_and_names_rooms_by_vnum():
    ev = MundiWsAdapter("Vallen").feed(env("room", ROOM, ["\x1b[33mThe Temple Of Midgaard\x1b[0m"]))[0]
    assert ev.type == "room" and ev.data["id"] == 3001 and ev.data["exits"][0]["to_id"] == 3005
    assert ev.raw == ["The Temple Of Midgaard"] and ev.agent == "Vallen" and ev.seq == 1
    assert vnum("tba:30:mob:3060/1") == "tba:30:mob:3060/1"
    assert MundiWsAdapter("Vallen").feed("not json") == []


async def test_login_is_one_message_and_the_password_is_never_recorded(tmp_path):
    mud = FakeMundi()
    server = await websockets.serve(mud.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    bus = Bus()
    rec = Recorder(tmp_path / "r.jsonl", secrets=["hunter2"])
    rec.attach(bus)
    screens = []
    sess = Session("Vallen", "127.0.0.1", port, "hunter2", bus, Stamper("Vallen"), interval=0.0,
                   protocol="mundi", profile={"class": "warrior", "sex": "male"})
    sess.on_text = screens.append
    task = asyncio.create_task(sess.run())
    await asyncio.sleep(0.5)
    sess.send("look", Source("human", "test", "a look"))
    await asyncio.sleep(0.3)
    await sess.stop()
    task.cancel()
    server.close()
    rec.close()
    login = mud.received[0]
    assert login == {"type": "login", "name": "Vallen", "password": "hunter2", "plain": False, "lang": "en",
                     "class": "warrior", "sex": "male"}
    assert {"type": "command", "text": "look"} in mud.received and mud.received[-1] == {"type": "command", "text": "quit"}
    evs = list(read_recording(tmp_path / "r.jsonl"))
    types = [e.type for e in evs]
    assert types[:2] == ["connection.opened", "connection.login_prompt"]
    assert "connection.in_game" in types and "prompt" in types
    assert next(e for e in evs if e.type == "room").data["id"] == 3001
    assert "hunter2" not in (tmp_path / "r.jsonl").read_text()
    assert any("Temple" in s for s in screens)
    assert sess.error is None


def test_the_profile_carries_the_screens_language_and_the_class(tmp_path):
    from pathlib import Path
    from anima.session.supervisor import Config, Supervisor
    cfg = Config(host="h", port=1, password="p", world_dir=Path("."), hazards=Path("."), protocol="mundi",
                 lang="ko", characters={"Lil": {"sex": "female"}, "Bo": {"lang": "en"}})
    sup = Supervisor(cfg, ["Lil", "Bo"])
    assert sup._profile("Lil", {"classes": {"Lil": "mage"}}) == {"lang": "ko", "sex": "female", "class": "magic_user"}
    assert sup._profile("Bo", {})["lang"] == "en", "a character's own setting wins"


def test_already_in_a_position_tells_the_position():
    a = MundiWsAdapter("Vallen")
    evs = a.feed(env("position.refused", {"command": "stand", "reason": "already"}, ["You are already standing."]))
    assert [e.type for e in evs] == ["position.refused", "position"] and evs[1].data == {"position": "standing"}
    assert len(a.feed(env("position.refused", {"command": "stand", "reason": "fighting"}))) == 1
