import asyncio
import json

import pytest

from anima.bus import Bus
from anima.protocol.commands import CommandQueue, InvalidSource, Source
from anima.protocol.envelope import Event, Stamper
from anima.recorder import Recorder, read_recording, replay_recording

SRC = Source(kind="behavior", id="base/hunt", reason="target in room")


def make_queue(bus: Bus, sent: list[str], agent: str = "Vallen") -> CommandQueue:
    async def writer(text: str) -> None:
        sent.append(text)
    return CommandQueue(agent, bus, Stamper(agent), writer, interval=0.0)


async def drain(q: CommandQueue) -> None:
    task = asyncio.create_task(q.run())
    while q.pending():
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()


def test_bus_filters_by_agent_and_type_prefix():
    bus = Bus()
    got: list[str] = []
    bus.subscribe(lambda e: got.append(f"{e.agent}:{e.type}"), agents=["Lil"], types=["combat."])
    for agent, t in [("Lil", "combat.hit"), ("Vallen", "combat.hit"), ("Lil", "room"), ("Lil", "combat.death")]:
        bus.publish(Event(t, {}, agent=agent))
    assert got == ["Lil:combat.hit", "Lil:combat.death"]


async def test_bus_queue_subscriber():
    bus = Bus()
    q = bus.queue(types=["room"])
    bus.publish(Event("room", {"name": "x"}, agent="A"))
    bus.publish(Event("prompt", {}, agent="A"))
    assert (await q.get()).data == {"name": "x"}
    assert q.empty()


async def test_command_requires_valid_source():
    bus, sent = Bus(), []
    q = make_queue(bus, sent)
    with pytest.raises(InvalidSource):
        q.send("kill fido", None)
    with pytest.raises(InvalidSource):
        q.send("kill fido", Source(kind="magic", id="x"))
    with pytest.raises(InvalidSource):
        q.send("kill fido", Source(kind="behavior", id=""))
    assert q.pending() == 0


async def test_command_sent_carries_source():
    bus, sent = Bus(), []
    out: list[Event] = []
    bus.subscribe(out.append, types=["command."])
    q = make_queue(bus, sent)
    q.send("kill fido", Source("behavior", "base/hunt", "fido in room", trigger_seq=41,
                               scores=[{"id": "base/hunt", "score": 0.8}, {"id": "base/rest", "score": 0.3}]))
    await drain(q)
    assert sent == ["kill fido"]
    d = out[0].data
    assert d["text"] == "kill fido" and d["secret"] is False
    assert d["source"] == {"kind": "behavior", "id": "base/hunt", "reason": "fido in room", "trigger_seq": 41,
                           "scores": [{"id": "base/hunt", "score": 0.8}, {"id": "base/rest", "score": 0.3}]}


async def test_secret_is_masked_everywhere(tmp_path):
    bus, sent = Bus(), []
    rec = Recorder(tmp_path / "r.jsonl")
    rec.attach(bus)
    q = make_queue(bus, sent)
    q.send("hunter2", Source("system", "session/login"), secret=True)
    await drain(q)
    rec.close()
    assert sent == ["hunter2"]                      # the server gets it
    text = (tmp_path / "r.jsonl").read_text()
    assert "hunter2" not in text                     # nothing else does
    assert json.loads(text)["data"]["text"] == "***"


async def test_command_after_password_prompt_is_secret_even_unmarked(tmp_path):
    bus, sent = Bus(), []
    out: list[Event] = []
    bus.subscribe(out.append, types=["command."])
    q = make_queue(bus, sent)
    bus.publish(Event("connection.login_prompt", {"stage": "password"}, agent="Vallen"))
    q.send("hunter2", Source("system", "session/login"))       # forgot secret=True
    q.send("look", Source("system", "session/login"))
    await drain(q)
    assert [e.data["text"] for e in out] == ["***", "look"]


def test_recorder_redacts_known_secrets_as_last_defence(tmp_path):
    rec = Recorder(tmp_path / "r.jsonl", secrets=["hunter2"])
    rec.write(Event("unknown", {"text": "your password is hunter2"}, agent="A", raw=["hunter2"]))
    rec.close()
    assert "hunter2" not in (tmp_path / "r.jsonl").read_text()


async def test_recording_roundtrip_and_replay(tmp_path):
    bus = Bus()
    rec = Recorder(tmp_path / "r.jsonl")
    rec.attach(bus)
    st = Stamper("Lil", clock=lambda: 100.0)
    bus.publish(st.stamp("prompt", {"hp": 1, "mp": 2, "mv": 3}))
    bus.publish(st.stamp("runtime.behavior", {"from": "rest", "to": "hunt", "scores": []}))
    rec.close()
    evs = list(read_recording(tmp_path / "r.jsonl"))
    assert [(e.seq, e.type) for e in evs] == [(1, "prompt"), (2, "runtime.behavior")]
    bus2, got = Bus(), []
    bus2.subscribe(got.append)
    assert await replay_recording(tmp_path / "r.jsonl", bus2) == 2
    assert got[0].data == {"hp": 1, "mp": 2, "mv": 3}
