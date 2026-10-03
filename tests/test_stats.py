"""`anima stats`: the same numbers from an Anima recording and from a replayed tintin log."""
import pytest
from anima.protocol.envelope import Event
from anima.stats import compute, game_clock, merge

ROOM_A = {"name": "A", "desc": "a"}
ROOM_B = {"name": "B", "desc": "b"}


def ev(t, agent, type_, **data):
    return Event(type_, data, agent, 0, float(t))


def party_run():
    return [
        ev(0, "Lead", "group.change", event="new_leader", who="Lead"),
        ev(0, "Lead", "room", **ROOM_A), ev(0, "Mem", "room", **ROOM_A),
        ev(0, "Lead", "char.vitals_max", hp=100, mp=100, mv=100),
        ev(10, "Lead", "combat.death", who="the rat"), ev(10.5, "Mem", "combat.death", who="the rat"),
        ev(11, "Lead", "exp.gain", amount=50, kind="share"), ev(11, "Mem", "exp.gain", amount=50, kind="share"),
        ev(20, "Lead", "room", **ROOM_B),                     # leader walks on, member stays 60 s
        ev(80, "Mem", "room", **ROOM_B),
        ev(90, "Mem", "self.fled"), ev(95, "Mem", "self.died"),
        ev(100, "Lead", "prompt", hp=30, mp=1, mv=20),
        ev(100, "Lead", "command.refused", reason="no_target"),
        ev(500, "Lead", "combat.death", who="the rat"),       # a different rat, much later
        ev(3600, "Lead", "prompt", hp=100, mp=1, mv=100),
    ]


def test_kills_are_counted_once_per_death_however_many_members_saw_it():
    st = compute(party_run())
    assert len(st.kills) == 2
    assert st.leader == "Lead"
    assert st.members["Mem"].deaths_seen == 1 and st.members["Lead"].deaths_seen == 2


def test_member_counters_and_lowest_vitals():
    st = compute(party_run())
    mem, lead = st.members["Mem"], st.members["Lead"]
    assert (mem.exp, mem.flees, mem.deaths) == (50, 1, 1)
    assert dict(lead.refused) == {"no_target": 1}
    assert (lead.min_hp, lead.min_hp_pct, lead.min_mv) == (30, 30.0, 20)


def test_time_apart_from_the_leader_and_stretches_without_kills():
    st = compute(party_run())
    assert st.members["Mem"].apart_s == 60.0
    assert st.hours == 1.0
    assert [round(d) for _, d in st.gaps] == [490, 3100]


def test_tintin_log_clock_comes_from_day_and_night_messages():
    # line numbers as time; sunrise (5h) -> sunset (21h) = 16 game hours = 20 real minutes
    log = [ev(1, "Lead", "connection.in_game", how="entered"),
           ev(2, "Lead", "world.time", phase="sunrise"),
           ev(50, "Lead", "combat.death", who="the rat"),
           ev(98, "Lead", "world.time", phase="sunset"),
           ev(99, "Lead", "self.died")]                      # after the last anchor: counted, adds no time
    st = compute(game_clock(log), shared_clock=False)
    assert round(st.hours * 60) == 20
    assert st.members["Lead"].deaths == 1 and len(st.kills) == 1


def test_separate_logs_merge_with_the_leader_from_follow_messages():
    lead = compute([ev(0, "Lead", "combat.death", who="x"), ev(10, "Lead", "combat.death", who="y")], False)
    mem = compute([ev(0, "Mem", "follow.moved", leader="Lead"), ev(5, "Mem", "combat.death", who="x")], False)
    st = merge([mem, lead])
    assert st.leader == "Lead" and len(st.kills) == 2


def test_leader_waiting_for_a_resting_member_and_revisits():
    evs = [ev(0, "Lead", "group.change", event="new_leader", who="Lead"),
           ev(0, "Lead", "room", **ROOM_A), ev(0, "Mem", "room", **ROOM_A),
           ev(0, "Lead", "runtime.behavior", to="hunt"),
           ev(60, "Lead", "runtime.behavior", to=None),        # nothing to do: idle
           ev(120, "Mem", "position", position="sleeping"),   # from here the leader is waiting
           ev(300, "Mem", "position", position="standing"),
           ev(360, "Lead", "runtime.behavior", to="explore"),
           ev(370, "Lead", "room", **ROOM_B), ev(380, "Lead", "room", **ROOM_B),   # a look: not a move
           ev(390, "Lead", "room", **ROOM_A),
           ev(600, "Lead", "prompt", hp=1, mp=1, mv=1)]
    fl = compute(evs, window_min=5).flow
    assert (fl.idle_s, fl.waiting_s) == (300.0, 180.0)
    assert (fl.rooms_entered, fl.revisits) == (3, 1)
    assert [round(w.get("waiting_s", 0)) for w in fl.windows] == [180, 0, 0]


def test_growth_is_exp_over_the_level_span_and_a_death_costs_half_of_all_exp():
    span = lambda agent, level: 10_000                      # noqa: E731
    evs = [ev(0, "A", "char.score", level=5, exp=50_000),
           ev(10, "A", "exp.gain", amount=1_000, kind="solo"),
           ev(20, "A", "self.died"),
           ev(3600, "A", "prompt", hp=1, mp=1, mv=1)]
    m = compute(evs, span=span).members["A"]
    assert m.death_cost == pytest.approx(2.55)              # (50k + 1k) / 2 over a 10k span
    assert m.progress == pytest.approx(0.1 - 2.55)


def test_loops_caught_are_counted_by_behavior():
    from anima.stats import render
    log = [ev(0, "Lil", "connection.in_game", how="entered"),
           ev(5, "Lil", "runtime.loop", source="base/drink", command="drink canteen", pause_s=30),
           ev(90, "Lil", "runtime.loop", source="base/drink", command="drink canteen", pause_s=60),
           ev(95, "Vallen", "runtime.loop", source="base/hunt", command="kill fido", pause_s=30)]
    st = compute(log)
    assert st.loops == {"base/drink: drink": 2, "base/hunt: kill": 1} and not st.loops_replayed
    assert "loops caught 3: base/drink: drink 2, base/hunt: kill 1" in render(st)
