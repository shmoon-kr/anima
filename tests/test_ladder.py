"""Zone ladder (D31): zones that fit the party's level, avoided after danger, tried again after levels."""
import pytest

from anima.party.ladder import Ladder
from test_runtime import WORLD, memoria_proto  # noqa: F401

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")


@pytest.fixture
def ladder(memoria_proto):
    temple = memoria_proto.graph.rooms_named("The Temple Square")[0]
    t = [0.0]
    lad = Ladder(memoria_proto, hub=lambda: temple, clock=lambda: t[0])
    lad.t = t
    return lad


def names(fits):
    return [f.name for f in fits]


def test_zones_follow_the_party_level(ladder):
    low = names(ladder.candidates([2, 2, 3]))
    mid = names(ladder.candidates([8, 8, 9, 7, 8, 8]))
    assert "Newbie Zone" in low and "Miden'Nir" not in low
    assert "Miden'Nir" in mid and "Newbie Zone" not in mid
    assert all(f.aggressive_max <= 7 + 3 for f in ladder.candidates([8, 8, 9, 7, 8, 8]))


def test_no_light_means_no_dark_zones(ladder):
    lit = {f.zone for f in ladder.candidates([8, 8, 8])}
    dark = {f.zone for f in ladder.candidates([8, 8, 8], has_light=False)}
    assert dark <= lit


def test_a_death_blocks_the_zone_until_two_more_levels(ladder):
    zone = ladder.candidates([8, 8, 8])[0].zone
    assert ladder.died(zone, weakest_level=8)
    assert zone not in {f.zone for f in ladder.candidates([8, 8, 8])}
    assert ladder.blocked(8)[zone]["until_level"] == 10
    assert zone not in ladder.blocked(10)


def test_three_flees_block_but_two_do_not(ladder):
    zone = ladder.candidates([8, 8, 8])[0].zone
    assert not ladder.fled(zone, 8) and not ladder.fled(zone, 8)
    ladder.t[0] += 1801                                    # the old flees are forgotten
    assert not ladder.fled(zone, 8) and not ladder.fled(zone, 8)
    assert ladder.fled(zone, 8)


def test_risk_survives_a_restart(ladder, memoria_proto):
    zone = ladder.candidates([8, 8, 8])[0].zone
    ladder.died(zone, 8)
    again = Ladder(memoria_proto, hub=ladder.hub, clock=ladder.clock)
    again.from_json(ladder.to_json())
    assert again.blocked(8) == ladder.blocked(8)


def test_zones_are_ranked_by_growth_and_measured_growth_takes_over(ladder):
    lv = [8, 8, 7, 7, 8, 9]
    first = ladder.candidates(lv, span=75000)
    assert first[0].name == "Miden'Nir" and first[0].expected_growth > first[1].expected_growth
    z = first[0].zone
    ladder.spent(z, 1800)                                   # half an hour there, almost nothing learned
    ladder.gained(z, 0.001)
    again = ladder.candidates(lv, span=75000)
    assert again[0].zone != z and next(f for f in again if f.zone == z).measured


def test_a_death_counts_against_the_zone_growth(ladder):
    z = ladder.candidates([8, 8, 8], span=75000)[0].zone
    ladder.spent(z, 1800)
    ladder.gained(z, 0.2)
    ladder.gained(z, -0.5)                                  # half of everything, on the level's scale
    assert ladder.growth(z, 0, 75000, 3)[0] < 0


def test_after_a_restart_we_stay_in_a_fitting_zone_where_we_are(memoria_proto):
    lv = [8, 8, 7, 7, 8, 9]
    temple = memoria_proto.graph.rooms_named("The Temple Square")[0]
    zone = Ladder(memoria_proto, hub=lambda: temple, clock=lambda: 0).candidates(lv, span=75000)[0].zone
    inside = max(v for v, r in memoria_proto.world.rooms.items() if r.zone == zone)
    lad = Ladder(memoria_proto, hub=lambda: inside, clock=lambda: 0)
    circuit = lad.start_here(lv, span=75000)
    first = memoria_proto.graph.rooms_named(circuit[0])
    assert len(first) == 1 and memoria_proto.world.rooms[first[0]].zone == zone     # stay, unambiguous name


def test_after_a_restart_in_an_unfit_zone_we_go_to_a_near_good_one(ladder):
    circuit = ladder.start_here([8, 8, 7, 7, 8, 9], span=75000)      # the temple: not a hunting zone
    fits = {f.entrance: f for f in ladder.candidates([8, 8, 7, 7, 8, 9], span=75000)}
    best = max(f.expected_growth for f in fits.values())
    good = [f for f in fits.values() if f.expected_growth >= best / 2]
    assert fits[circuit[0]].steps == min(f.steps for f in good)
