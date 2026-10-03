"""The web entrance to the view stream: signed short-lived tokens, read-only, a character switch."""
import asyncio
import json

import pytest
import websockets

from anima.session.gateway import Gateway, browser_message, outgoing, sign_token, verify_token

KEY = "test-key"


def test_a_token_is_good_only_signed_unexpired_and_for_this_target():
    tok = sign_token({"user": 1, "target": "mundi", "exp": 1000}, KEY)
    assert verify_token(tok, KEY, "mundi", now=999)["user"] == 1
    assert verify_token(tok, KEY, "mundi", now=1001) is None, "expired"
    assert verify_token(tok, KEY, "default", now=999) is None, "for another party"
    assert verify_token(tok, "other-key", "mundi", now=999) is None, "signed with another key"
    body, mac = tok.split(".")
    forged = sign_token({"user": 2, "target": "mundi", "exp": 1000}, KEY).split(".")[0] + "." + mac
    assert verify_token(forged, KEY, "mundi", now=999) is None, "a body that is not the one signed"
    assert verify_token("", KEY, "mundi") is None and verify_token(tok, "", "mundi", now=999) is None


def test_from_the_browser_only_a_character_switch():
    members = ["Vallen", "Lil"]
    assert browser_message('{"agents": ["Lil"]}', members) == ["Lil"]
    assert browser_message('{"agents": ["Evan"]}', members) is None, "not a member"
    assert browser_message('{"line": "kill fido", "focus": "Vallen"}', members) is None, "v1 is for watching"
    assert browser_message('{"op": "send", "agent": "Vallen", "text": "quit"}', members) is None
    assert browser_message("not json", members) is None


def test_an_event_goes_out_as_its_event_line_and_narration():
    ev = {"type": "combat.death", "data": {"who": "the kobold"}}
    out = outgoing({"k": "ev", "a": "Vallen", "ev": ev}, lambda e: "death: kobold", lambda e: "Vallen이 코볼트를 쓰러뜨렸다")
    assert out == [{"k": "evline", "a": "Vallen", "s": "death: kobold"},
                   {"k": "narr", "a": "Vallen", "s": "Vallen이 코볼트를 쓰러뜨렸다"}]
    assert outgoing({"k": "notes", "notes": ["x"]}, str, str) == [], "the terminal's notes are not for a watcher"
    clock = outgoing({"k": "ev", "a": "Vallen", "ev": {"type": "world.time", "data": {"phase": "night"}}},
                     lambda e: None, lambda e: None)
    assert clock == [{"k": "clock", "a": "Vallen", "phase": "night"}]


class FakeSup:
    def __init__(self):
        self.agents = ["Vallen", "Lil"]
        self._viewers = []
        self._screen = {"Vallen": "\x1b[32mVallen's screen\x1b[0m", "Lil": "Lil's screen"}
        self.sent = []

    def party_line(self):
        return {"Vallen": {"hp": 10, "hp_max": 20, "class": "warrior"}}


def test_a_browser_watches_switches_and_cannot_type():
    async def run():
        sup = FakeSup()
        gw = Gateway(sup, KEY, "mundi", lambda e: "ev line", lambda e: None)
        server = await gw.serve("127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            bad = await websockets.connect(f"ws://127.0.0.1:{port}/ws/view?token=nope")
            with pytest.raises(websockets.ConnectionClosed) as ei:
                await bad.recv()
            assert ei.value.rcvd.code == 4401
            tok = sign_token({"user": 1, "target": "mundi", "exp": 9e12}, KEY)
            async with websockets.connect(f"ws://127.0.0.1:{port}/ws/view?token={tok}") as ws:
                got = [json.loads(await ws.recv()) for _ in range(2)]
                kinds = {m["k"] for m in got}
                assert kinds == {"text", "status"}
                assert next(m for m in got if m["k"] == "text")["a"] == "Vallen", "the first member by default"
                await ws.send(json.dumps({"line": "quit", "focus": "Vallen"}))
                await ws.send(json.dumps({"agents": ["Lil"]}))
                for _ in range(5):
                    m = json.loads(await ws.recv())
                    if m["k"] == "text":
                        assert m["a"] == "Lil" and m["s"] == "Lil's screen"
                        break
                else:
                    pytest.fail("no switch to Lil")
                assert len(sup._viewers) == 1
                agents, q = sup._viewers[0]
                assert agents == {"Lil"}
                q.put_nowait({"k": "ev", "a": "Lil", "ev": {"type": "x", "data": {}}})
                for _ in range(5):
                    m = json.loads(await ws.recv())
                    if m["k"] == "evline":
                        break
                else:
                    pytest.fail("no event line")
            await asyncio.sleep(0.1)
            assert sup._viewers == [], "a closed page is no longer a viewer"
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(run())
