"""The strategist (PHASE-2-PLAN S3): when it asks, what it sends, how its answer reaches the overlay."""
import asyncio

from anima.animus.overlay import PARTY, patch_groups
from anima.animus.queue import AnimusQueue, FakeProvider, ReplayProvider
from anima.animus.strategist import Strategist, daylight
from anima.bus import Bus
from anima.protocol.envelope import Event, Stamper
from test_animus_overlay import Rig


class World:
    def __init__(self, tmp_path, answer):
        self.rig = Rig(tmp_path)
        self.bus = Bus()
        self.events = []
        self.bus.subscribe(self.events.append)
        self.stampers = {}
        self.q = AnimusQueue(self.bus, {"claude": FakeProvider(answer)}, self.stamper, clock=lambda: self.rig.t)
        self.s = Strategist(self.q, self.rig.ov, "Vallen", view=lambda: {"members": {}}, knobs=lambda: {},
                            world=lambda: [], zone_of_leader=lambda: 186, clock=lambda: self.rig.t,
                            notes_path=tmp_path / "animus" / "party-notes.md")
        self.bus.subscribe(self.s.on_event)
        assert self.s.tick() is None                           # starts the clock: first look in 5 minutes

    def stamper(self, agent):
        return self.stampers.setdefault(agent, Stamper(agent, clock=lambda: self.rig.t))

    def ev(self, agent, type_, **data):
        self.bus.publish(self.stamper(agent).stamp(type_, data))

    def answer_pending(self):
        async def go():
            task = asyncio.create_task(self.q.run())
            for _ in range(50):
                await asyncio.sleep(0)
            task.cancel()
        asyncio.run(go())


ANSWER = {"changes": [{"layer": "Lumina", "key": "policy.rest_mp_pct", "value": 25, "ttl_s": 1200},
                      {"layer": "party", "key": "policy.rest_pct", "value": 99}],          # out of range
          "reason": "Lumina sleeps too long for mana", "notes": "watch Lumina's waiting time",
          "proposal": "Let the leader hunt with five while a cleric rests nearby."}


def test_first_look_comes_shortly_after_start_then_every_half_hour(tmp_path):
    w = World(tmp_path, {"party_strategy": {"changes": []}})
    w.rig.t += 299
    assert w.s.tick() is None
    w.rig.t += 1
    assert w.s.tick() is not None
    w.answer_pending()
    w.rig.t += 600
    w.ev("Vallen", "combat.death", who="the rat")             # hunting fine: no reason to ask early
    assert w.s.tick() is None
    w.rig.t += 400
    w.ev("Vallen", "combat.death", who="the rat")
    w.rig.t += 800
    assert w.s.tick() is not None


def test_a_death_asks_again_soon_and_with_danger_priority(tmp_path):
    w = World(tmp_path, {"party_strategy": {"changes": []}})
    w.rig.t += 300
    w.s.tick()
    w.answer_pending()
    w.ev("Vallen", "combat.death", who="the rat")
    w.ev("Lumina", "self.died")
    w.rig.t += 60
    assert w.s.tick() is None                                  # min gap 5 minutes
    w.rig.t += 240
    assert w.s.tick() is not None
    req = [e for e in w.events if e.type == "animus.request"][-1]
    assert req.data["priority"] == "danger" and req.data["context"]["why_now"] == ["Lumina died"]


def test_no_kill_for_ten_minutes_is_a_reason(tmp_path):
    w = World(tmp_path, {"party_strategy": {"changes": []}})
    w.rig.t += 300
    w.s.tick()
    w.answer_pending()
    w.rig.t += 600
    w.s.tick()
    req = [e for e in w.events if e.type == "animus.request"][-1]
    assert req.data["context"]["why_now"] == ["no kill for 15 minutes"]     # counted from the start


def test_answer_goes_through_the_overlay_and_leaves_notes_and_a_proposal(tmp_path):
    w = World(tmp_path, {"party_strategy": ANSWER})
    w.rig.t += 300
    w.s.tick()
    w.answer_pending()
    assert w.rig.policy("Lumina", "rest_mp_pct") == 25          # applied, origin strategist
    assert w.rig.ov.layers["Lumina"]["policy.rest_mp_pct"].origin == "strategist"
    assert w.rig.policy("Lumina", "rest_pct") == 30             # rejected: out of range
    out = {o["layer"]: o for o in w.s._outcome}
    assert out["Lumina"]["ok"] and not out[PARTY]["ok"] and out[PARTY]["reason"] == "invalid"
    assert "watch Lumina" in (tmp_path / "animus" / "party-notes.md").read_text()
    assert list((tmp_path / "animus" / "proposals").glob("*.md"))
    w.rig.t += 1800
    w.s.tick()                                                 # the next call is told what was rejected
    ctx = [e for e in w.events if e.type == "animus.request"][-1].data["context"]
    assert ctx["outcome_of_your_last_answer"][1]["reason"] == "invalid"
    assert ctx["your_notes"].startswith("watch Lumina")


def test_a_local_llm_strategist_answer_cannot_lock_itself_out(tmp_path):
    w = World(tmp_path, {"party_strategy": ANSWER})
    w.rig.t += 300
    w.s.tick()
    w.answer_pending()
    assert w.rig.set("Lumina", "policy.rest_mp_pct", 45, origin="local").reason == "locked_by_party"


def test_recorded_answers_replay_to_the_same_patch_without_an_llm(tmp_path):
    live = World(tmp_path / "live", {"party_strategy": ANSWER})
    live.rig.t += 300
    live.s.tick()
    live.answer_pending()
    replay = World(tmp_path / "replay", {})
    replay.q.providers["claude"] = ReplayProvider.from_events(live.events)
    replay.rig.t += 300
    replay.s.tick()
    replay.answer_pending()
    assert replay.rig.ov.show()["layers"].keys() == live.rig.ov.show()["layers"].keys()
    assert replay.rig.policy("Lumina", "rest_mp_pct") == 25


def test_patch_answers_tolerate_the_word_knob_and_drop_garbage():
    groups, reason = patch_groups({"changes": [{"layer": "Lil", "knob": "policy.rest_pct", "value": 40},
                                               "nonsense", {"key": "policy.x"}], "reason": "r"})
    assert groups == {"Lil": [{"key": "policy.rest_pct", "value": 40}]} and reason == "r"
    assert patch_groups({"changes": [{"key": "policy.rest_pct", "value": 40}]}, default_layer="Lil")[0]["Lil"]


def test_daylight_counts_real_minutes_to_the_next_change():
    d = daylight("day", 0)                                    # 06:00 -> sunset at 21:00 = 15 game hours
    assert d == {"now": "day", "next": "sunset", "real_minutes_to_next": 18.8}
    assert daylight("night", 75 * 6)["next"] == "sunrise"      # 22:00 + 6h = 04:00 -> sunrise 05:00
