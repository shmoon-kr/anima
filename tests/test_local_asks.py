"""Local LLM questions (PHASE-2-PLAN S4): periodic asks, engine facts, answers that patch the own layer."""
import pytest

from anima.sigil.program import SigilError, build_program, load_package
from test_runtime import ROOT, WORLD, Harness, enter_game, memoria_proto  # noqa: F401

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")


class Queue:
    def __init__(self):
        self.asked = []

    def submit(self, req):
        self.asked.append(req)


def rig(memoria_proto):
    q = Queue()
    h = Harness(memoria_proto, agent="Lumina", animus=q)
    patches = []
    h.rt.ask_facts = lambda agent, facts: {f: f"<{f} for {agent}>" for f in facts}
    h.rt.on_patch = lambda agent, answer, rid: patches.append((agent, answer, rid))
    return h, q, patches


def asked(q, kind):
    return [r for r in q.asked if r.question_kind == kind]


def test_self_tune_is_asked_every_twenty_minutes_with_engine_facts(memoria_proto):
    h, q, _ = rig(memoria_proto)
    enter_game(h)
    h.advance(1)                                        # the period starts at the first tick in game
    h.advance(1199)
    assert not asked(q, "self_tune")
    h.advance(1)
    first = asked(q, "self_tune")
    assert len(first) == 1 and first[0].tier == "local_fast"
    assert first[0].context["facts"] == {"knobs": "<knobs for Lumina>", "locked": "<locked for Lumina>",
                                         "recent": "<recent for Lumina>"}
    assert '"layer"' not in first[0].answer_schema["shape"]   # a local LLM writes only its own layer


def test_a_patch_answer_goes_to_the_overlay_hook(memoria_proto):
    h, q, patches = rig(memoria_proto)
    enter_game(h)
    for _ in range(21):
        h.advance(60)
    req = asked(q, "self_tune")[0]
    answer = {"changes": [{"key": "policy.rest_mp_pct", "value": 25}], "reason": "less sleeping"}
    h.ev("animus.response", id=req.id, answer=answer, latency_s=3.0, provider="fake", model="m")
    assert patches == [("Lumina", answer, req.id)]


def test_an_unknown_creature_is_asked_about_at_once(memoria_proto):
    h, q, _ = rig(memoria_proto)
    enter_game(h, occupants=["A shimmering thing from beyond is floating here."])
    h.advance(1)
    r = asked(q, "mob_risk")
    assert r and r[0].priority == "danger" and r[0].context["creatures"] == [
        "A shimmering thing from beyond is floating here."]


def test_ask_options_are_validated():
    base = load_package(ROOT / "packages" / "base")
    bad = load_package(ROOT / "packages" / "base")
    bad.data = {"asks": {"x": {"when": "true", "question": "q", "tier": "local_fast", "timeout_s": 5,
                               "default": 1, "schema": {"type": "number"}, "facts": ["secrets"], "every_s": 5}}}
    bad.name = "bad"
    with pytest.raises(SigilError) as e:
        build_program("Lumina", [base, bad])
    assert any("facts" in x for x in e.value.errors) and any("every_s" in x for x in e.value.errors)
