"""Party camp (D30): one member needs rest -> everyone recovers at once, a sentry keeps watch."""
import pytest

from test_regressions import together
from test_runtime import WORLD, Harness, PartyHarness, enter_game, memoria_proto  # noqa: F401

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")
NAMES = ["Vallen", "Lil", "Lumina", "Senia"]


def camp(memoria_proto):
    p = PartyHarness(memoria_proto, NAMES)
    together(p, NAMES)
    p.ev("Senia", "char.vitals_max", hp=100, mp=100, mv=90)
    p.ev("Senia", "prompt", hp=100, mp=100, mv=90)            # the most hit points: the sentry
    p.ev("Lil", "prompt", hp=40, mp=10, mv=90)                # out of mana: needs rest
    for n in NAMES:
        p.take(n)
    p.t += 25
    p.rt["Lil"].tick()                                       # Lil notices first (each member ticks on its own)
    p.tick()
    return p


def asleep(p, *names):
    for n in names:
        p.ev(n, "position", position="sleeping")


def test_one_tired_member_makes_the_room_camp_and_the_strongest_keeps_watch(memoria_proto):
    p = camp(memoria_proto)
    out = {n: p.take(n) for n in NAMES}
    assert p.board.camp("Vallen") == "Senia"
    assert "sleep" in out["Lil"] and "sleep" in out["Vallen"] and "sleep" in out["Lumina"]
    assert "rest" in out["Senia"] and "sleep" not in out["Senia"]
    assert not any(t in ("north", "south", "east", "west") for t in out["Vallen"])   # the leader camps too


def test_a_stranger_arriving_wakes_the_camp(memoria_proto):
    p = camp(memoria_proto)
    asleep(p, "Vallen", "Lil", "Lumina")
    p.ev("Senia", "position", position="resting")
    for n in NAMES:
        p.take(n)
    p.ev("Senia", "occupant.arrived", who="A kobold")
    out = p.take("Senia")
    assert {"wake Vallen", "wake Lil", "wake Lumina", "stand"} <= set(out)
    p.ev("Lil", "position", position="sitting", awakened_by="Senia")
    assert "stand" in p.take("Lil")


def test_the_camp_ends_when_nobody_needs_rest(memoria_proto):
    p = camp(memoria_proto)
    asleep(p, "Vallen", "Lil", "Lumina")
    p.ev("Lil", "prompt", hp=40, mp=50, mv=90)
    p.tick(5)
    assert p.board.camp("Lil") == "Senia"                     # still recovering: the camp holds below camp_start
    p.ev("Lil", "prompt", hp=40, mp=95, mv=90)
    for n in NAMES:
        p.take(n)
    p.tick(6)
    assert p.board.camp("Lil") is None
    p.tick(1)                                                 # the leader saw Lil's need a tick late
    assert p.take("Vallen")[:2] == ["wake", "stand"]


def test_a_hit_does_not_wake_a_sleeper_so_we_wake_ourselves(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.ev("position", position="sleeping")
    h.take()
    h.ev("combat.hit", attacker="the kobold", victim="self", verb="hit", severity=2, kind="weapon")
    assert h.rt.state.position == "sleeping"                  # fight.c update_pos
    assert h.take()[:2] == ["wake", "stand"]
