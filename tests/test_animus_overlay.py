"""Animus override layers (PHASE-2-PLAN S1): only declared knobs, validated, hot-applied, reversible."""
import pytest

from anima.animus.overlay import DEATH_WINDOW_S, MIN_HOLD_S, PARTY, Overlay
from anima.sigil.program import SigilError, load_agent
from test_runtime import MOVES, TEMPLE_SQUARE, WORLD, Harness, drive, enter_game, memoria_proto, ROOT  # noqa: F401

AGENTS = ["Vallen", "Lumina"]


def load(name, extra=()):
    return load_agent(ROOT / "agents" / f"{name}.yaml", ROOT / "packages", extra)


class Rig:
    """An overlay over the real packages; 'runtimes' are just the programs it hot-applies."""

    def __init__(self, tmp_path=None):
        self.t = 10_000.0
        self.base = {n: load(n) for n in AGENTS}
        self.live = {n: load(n) for n in AGENTS}
        self.events = []
        self.party = {}
        self.ov = Overlay(AGENTS, base=lambda n: self.base[n], rebuild=load, apply=self._apply,
                          publish=lambda a, t, d: self.events.append((a, t, d)), clock=lambda: self.t,
                          is_room=lambda r: r in ("The Entrance To The Newbie Zone", "The End Of The Path", "Market Square"),
                          apply_party=self.party.__setitem__, leader="Vallen",
                          save_path=tmp_path / "overlay.json" if tmp_path else None)

    def _apply(self, name, prog):
        self.live[name] = prog

    def policy(self, name, key):
        return self.live[name].policies[key]

    def set(self, layer, key, value, ttl=None, origin="human"):
        return self.ov.patch(layer, [{"key": key, "value": value, "ttl_s": ttl}], "test", origin=origin)


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_a_declared_knob_in_range_is_applied_and_recorded(rig):
    res = rig.set("Lumina", "policy.rest_mp_pct", 60)
    assert res.ok and res.changes == [{"key": "policy.rest_mp_pct", "old": 45, "new": 60, "ttl_s": None}]
    assert rig.policy("Lumina", "rest_mp_pct") == 60
    assert rig.live["Lumina"].policy_origin["rest_mp_pct"] == "animus:Lumina"
    assert rig.policy("Vallen", "rest_mp_pct") == rig.base["Vallen"].policies["rest_mp_pct"]
    assert rig.events[-1][1] == "runtime.animus" and rig.events[-1][2]["event"] == "applied"


@pytest.mark.parametrize("key,value,why", [
    ("policy.inertia", 0.5, "invalid"),                 # not declared as a knob
    ("policy.rest_mp_pct", 95, "invalid"),              # out of range
    ("policy.flee_pct", 15, "invalid"),                 # Lumina's 45 → 15 is more than one step (10)
    ("policy.danger", [], "invalid"),                   # grow only: cannot drop the package's dangers
    ("policy.rally", "Market Square", "invalid"),       # party-only key in an agent layer
    ("weight.rest", 0.1, "invalid"),                    # survival/recovery weights never opt in
])
def test_values_outside_the_knobs_are_rejected_and_nothing_changes(rig, key, value, why):
    before = dict(rig.live["Lumina"].policies)
    res = rig.set("Lumina", key, value)
    assert not res.ok and res.reason == why and res.errors
    assert rig.live["Lumina"].policies == before
    assert rig.events[-1][2]["event"] == "rejected"


def test_a_local_llm_cannot_write_the_party_layer(rig):
    res = rig.set(PARTY, "policy.rest_pct", 40, origin="local")
    assert not res.ok and res.reason == "not_allowed"


def test_party_layer_sets_shared_knobs_for_every_agent_that_has_them(rig):
    assert rig.set(PARTY, "policy.rally", "The End Of The Path").ok
    assert rig.party == {"rally": "The End Of The Path"}
    assert rig.policy("Vallen", "rally") == rig.policy("Lumina", "rally") == "The End Of The Path"
    assert rig.set(PARTY, "weight.hunt", 0.8).ok                     # only the leader opted in
    assert rig.live["Vallen"].behaviors["hunt"].spec["weight"] == 0.8


def test_weight_must_stay_in_its_declared_range(rig):
    assert not rig.set("Vallen", "weight.hunt", 0.95).ok              # above rest (0.9): never
    assert rig.set("Vallen", "weight.explore", 0.4).ok


def test_strategist_value_locks_the_local_llm_until_it_expires(rig):
    assert rig.set("Lumina", "policy.rest_mp_pct", 60, ttl=1200, origin="strategist").ok
    res = rig.set("Lumina", "policy.rest_mp_pct", 50, origin="local")
    assert not res.ok and res.reason == "locked_by_party"
    assert "policy.rest_mp_pct" in rig.ov.locked_keys("Lumina")
    rig.t += 1201
    assert rig.ov.expire() and rig.policy("Lumina", "rest_mp_pct") == 45
    assert rig.set("Lumina", "policy.rest_mp_pct", 55, origin="local").ok


def test_party_layer_value_locks_every_agents_local_llm(rig):
    assert rig.set(PARTY, "policy.rest_pct", 40, origin="strategist").ok
    assert rig.set("Lumina", "policy.rest_pct", 35, origin="local").reason == "locked_by_party"
    rig.t += MIN_HOLD_S
    assert rig.set("Lumina", "policy.rest_pct", 35, origin="local").ok


def test_a_party_decision_replaces_an_agents_own_value(rig):
    assert rig.set("Lumina", "policy.rest_pct", 40).ok
    assert rig.set(PARTY, "policy.rest_pct", 35).ok
    assert rig.policy("Lumina", "rest_pct") == 35 and "policy.rest_pct" not in rig.ov.layers.get("Lumina", {})


def test_same_key_is_held_and_hourly_changes_are_capped(rig):
    assert rig.set("Lumina", "policy.rest_pct", 40).ok
    assert rig.set("Lumina", "policy.rest_pct", 45).reason == "invalid"      # held for MIN_HOLD_S
    rig.t += MIN_HOLD_S
    assert rig.set("Lumina", "policy.rest_pct", 45).ok
    many = [{"key": "policy.targets", "value": ["rat"]}] * 11
    assert rig.ov.patch("Lumina", many, "spam").reason == "rate"


def test_death_reverts_recent_patches_and_tells_the_strategist(rig):
    assert rig.set("Lumina", "policy.rest_pct", 40).ok                       # long before the death
    rig.t += DEATH_WINDOW_S + 100
    late = rig.set("Lumina", "policy.flee_pct", 35)
    assert late.ok
    rig.t += 100
    gone = rig.ov.died("Lumina")
    assert [g["key"] for g in gone] == ["policy.flee_pct"]
    assert rig.policy("Lumina", "flee_pct") == 45 and rig.policy("Lumina", "rest_pct") == 40
    applied = next(h for h in rig.ov.history if h["patch_id"] == late.patch_id)
    assert applied["followed_by_death"] == {"agent": "Lumina", "after_s": 100}
    assert rig.ov.deaths[-1]["agent"] == "Lumina"
    assert rig.events[-1][2]["event"] == "reverted" and rig.events[-1][2]["reason"] == "death"


def test_off_restores_phase_one_values_and_refuses_patches_until_on(rig):
    assert rig.set("Lumina", "policy.rest_mp_pct", 60).ok
    rig.ov.off()
    assert rig.live["Lumina"].policies == rig.base["Lumina"].policies
    assert rig.set("Lumina", "policy.rest_pct", 40).reason == "off"
    rig.ov.on()
    assert rig.policy("Lumina", "rest_mp_pct") == 60


def test_revert_one_patch_or_all(rig):
    a = rig.set("Lumina", "policy.rest_pct", 40)
    rig.set("Vallen", "weight.explore", 0.3)
    rig.ov.revert(a.patch_id)
    assert rig.policy("Lumina", "rest_pct") == 30
    assert rig.live["Vallen"].behaviors["explore"].spec["weight"] == 0.3
    rig.ov.revert()
    assert rig.live["Vallen"].behaviors["explore"].spec["weight"] == 0.2


def test_layers_survive_a_restart(rig, tmp_path):
    rig.set(PARTY, "policy.circuit", ["The End Of The Path", "The Entrance To The Newbie Zone"])
    rig.set("Lumina", "policy.rest_mp_pct", 60)
    again = Rig(tmp_path)
    again.ov.load()
    assert again.policy("Lumina", "rest_mp_pct") == 60
    assert again.party["circuit"] == ["The End Of The Path", "The Entrance To The Newbie Zone"]


def test_sigil_validator_refuses_animus_layers_outside_the_knobs():
    with pytest.raises(SigilError):
        load("Lumina", [("animus:Lumina", {"policies": {"inertia": 0.9}})])
    with pytest.raises(SigilError):
        load("Vallen", [("animus:Vallen", {"behaviors": {"rest": {"weight": 0.1}}})])
    with pytest.raises(SigilError):     # an LLM layer cannot declare new knobs for itself
        load("Lumina", [("animus:Lumina", {"animus": {"policies": {"inertia": {"type": "number", "min": 0, "max": 1}}},
                                           "policies": {"inertia": 0.9}})])


def test_survival_and_recovery_never_opt_in():
    survival = {"rest", "sleep", "eat", "drink", "stand_up", "flee_when_low", "flee_from_danger"}
    for m in (ROOT / "agents").glob("*.yaml"):
        prog = load(m.stem)
        assert not survival & set(prog.weight_knobs()), m.stem
        assert all("animus_weight" not in it.spec for it in prog.reflexes.values())


@pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")
def test_hot_apply_keeps_the_running_task(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, vnum=TEMPLE_SQUARE)
    h.ev("char.score", practices=2)
    h.ev("char.skills", skills={"kick": "not learned"})
    drive(h, lambda out: any(t in MOVES for t in out))
    assert h.rt.tasks.name == "train_at_guild"
    current = h.rt.selector.current
    h.rt.apply_values(load("Vallen", [("animus:Vallen", {"policies": {"rest_pct": 45},
                                                         "behaviors": {"explore": {"weight": 0.4}}})]))
    assert h.rt.program.policies["rest_pct"] == 45 and h.rt.program.behaviors["explore"].spec["weight"] == 0.4
    assert h.rt.tasks.name == "train_at_guild" and h.rt.selector.current is current
    assert not [e for e in h.events if e.type == "runtime.task" and e.data["event"] == "abandoned"]
