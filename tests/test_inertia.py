"""Inertia (D38): it keeps the agent from flip-flopping, but must never keep it from a strict upgrade
or an urgent response. Two of these were live bugs (a sleeping sentry, members resting forever)."""
from pathlib import Path

import pytest

from anima.sigil.program import load_agent

ROOT = Path(__file__).parents[1]
# (behavior that must be able to take over, behaviors it must be able to interrupt)
MUST_INTERRUPT = {
    "sleep": ["rest"],                                          # +50% recovery over +25%
    "camp_watch": ["sleep", "camp_sleep", "camp_rest", "rest"],  # the sentry wakes up to keep watch
    "camp_sleep": ["camp_rest", "rest"],
    "rescue": ["fight_skill", "fight_spell", "open_fight"],     # save a member even mid-fight
    "heal_fight": ["fight_spell", "give_food", "give_water", "make_food", "create_food"],
}


def blocked(prog, a_name, b_name):
    """True if B, at full score, would lose to A at full score only because A is current."""
    a, b = prog.behaviors[a_name], prog.behaviors[b_name]
    inertia = float(a.spec.get("inertia", prog.policies.get("inertia", 0.1)))
    wa, wb = float(a.spec.get("weight", 1.0)), float(b.spec.get("weight", 1.0))
    return wb > wa and wb <= wa + inertia and a_name not in (b.spec.get("supersedes") or [])


@pytest.mark.parametrize("manifest", sorted((ROOT / "agents").glob("*.yaml")), ids=lambda p: p.stem)
def test_urgent_and_upgrade_behaviors_are_never_held_back_by_inertia(manifest):
    prog = load_agent(manifest, ROOT / "packages")
    bad = [f"{a} holds back {b}" for b, olds in MUST_INTERRUPT.items() if b in prog.behaviors
           for a in olds if a in prog.behaviors and blocked(prog, a, b)]
    assert not bad, bad


def test_a_superseding_behavior_takes_over_despite_inertia():
    from anima.select.utility import Choice, UtilitySelector

    class Ctx:
        def __init__(self, prog):
            self.program, self.state = prog, type("S", (), {"rest_need": 0.0})()

        def clock(self):
            return 0.0

        def eval(self, expr, target=None):
            return 1.0
    prog = load_agent(ROOT / "agents" / "Lil.yaml", ROOT / "packages")
    sel = UtilitySelector(Ctx(prog), lambda *a: None, lambda *a: None, lambda: None)
    rest, sleep = prog.behaviors["rest"], prog.behaviors["sleep"]
    rest.spec["inertia"] = 0.5                                  # even a big inertia on rest
    sel.current = Choice(rest, 0.9)
    scores = {c.item.name: c.score for c in sel.evaluate()}
    assert scores["sleep"] > scores["rest"]
