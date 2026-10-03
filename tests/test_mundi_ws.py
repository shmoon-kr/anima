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


def test_equipment_slots_are_the_labels_anima_reads():
    # Mundi said "body"; anima knows "worn on body" (gear.SLOT_OF_LABEL), so it saw nothing worn and
    # tried to wear a second vest again and again ("You're already wearing something on your body.")
    from anima.memoria.gear import SLOT_OF_LABEL
    ev = MundiWsAdapter("Vallen").feed(env("items.equipment", {"slots": [
        {"slot": "body", "id": "tba:186:obj:18602/1", "text": "a bright green newbie vest"},
        {"slot": "finger_left", "text": "a ring"}]}))[0]
    assert [s["slot"] for s in ev.data["slots"]] == ["worn on body", "worn on finger"]
    assert all(s["slot"] in SLOT_OF_LABEL for s in ev.data["slots"])


def test_what_mundi_says_its_own_way_also_comes_as_the_protocols_event():
    a = MundiWsAdapter("Vallen")
    types = lambda frame: [(e.type, e.data) for e in a.feed(frame)][1:]
    assert types(env("group.failed", {"reason": "already_in_group"})) == [("group.change", {"event": "joined", "who": "self"})]
    assert types(env("group.failed", {"reason": "already_following", "who": "Vallen"})) == [("group.change", {"event": "following", "who": "Vallen"})]
    assert types(env("items.failed", {"action": "give", "reason": "hands_full", "text": "a waybread"})) == [("items.give_failed", {"reason": "hands_full"})]
    assert types(env("items.failed", {"action": "drop", "reason": "cursed", "text": "a ring"})) == [("items.cannot_drop", {"text": "a ring", "reason": "cursed"})]
    assert types(env("group.failed", {"reason": "not_in_group"})) == []
    assert types(env("move.blocked", {"who": "self"})) == [("move.failed", {"reason": "guarded"})]
    assert types(env("move.blocked", {"who": "Ana", "who_id": "pc:ana"})) == [], "someone else blocked: not ours"
    room = a.feed(env("room", {**ROOM, "occupants": [{"id": "pc:ana", "text": "Ana is standing here.", "hints": [], "flags": []},
                                                     {"id": "tba:30:mob:3060/1", "text": "A cityguard stands here.", "hints": [], "flags": []}]}))[0]
    assert [o["hints"] for o in room.data["occupants"]] == [["player"], []]


def test_a_dark_room_with_an_id_still_says_where_we_are():
    # Mundi: the leader came back in a dark alley (room.dark has the room's id); anima took the
    # place for unknown and the party stood recounting itself.
    from anima.memoria import Memoria
    from conftest import world_dir
    import pytest
    if not world_dir().exists():
        pytest.skip("tbaMUD world files not present")
    ev = MundiWsAdapter("Vallen").feed(env("room.dark", {"id": "tba:30:room:3066"}))[0]
    assert ev.data["id"] == 3066
    mem = Memoria.from_tbamud(world_dir())
    mem.locator("Vallen").on_event(ev)
    assert mem.locator("Vallen").vnum == 3066 and mem.locator("Vallen").certain


def test_a_shop_failure_is_said_as_the_protocol_says_it():
    a = MundiWsAdapter("Senia")
    ev = a.feed(env("shop.result", {"action": "too_many", "who": "self", "text": "pants"}))[0]
    assert ev.data["action"] == "buy" and ev.data["ok"] is False and ev.data["reason"] == "too_many"
    assert a.feed(env("shop.result", {"action": "buy", "who": "self", "text": "pants"}))[0].data["ok"] is True


def test_already_following_names_the_leader():
    evs = MundiWsAdapter("Lil").feed(env("group.failed", {"reason": "already_following", "who": "Vallen"}))
    assert evs[1].type == "group.change" and evs[1].data == {"event": "following", "who": "Vallen"}
