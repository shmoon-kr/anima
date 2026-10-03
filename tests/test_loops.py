"""The general loop guard: the same command, the same no, again and again -> held back, longer each time."""
from anima.protocol.envelope import Event, Stamper
from anima.runtime.loops import PAUSE_S, LoopGuard, useless
from test_runtime import memoria_proto  # noqa: F401  (the fixture)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def guard():
    clock, seen = Clock(), []
    return LoopGuard(clock, lambda t, d: seen.append(d)), clock, seen


def answer(g, clock, text, events, source="base/drink", kind="behavior"):
    st = Stamper("Ana", clock=clock)
    g.observe(st.stamp("command.sent", {"text": text, "source": {"kind": kind, "id": source}}))
    for t, d in events:
        g.observe(st.stamp(t, d))
    g.observe(st.stamp("prompt", {"hp": 1}))
    clock.t += 5


EMPTY = [("items.used", {"action": "drink", "empty": True})]


def test_what_counts_as_no():
    st = Stamper("Ana")
    assert useless([]) == (("nothing", ""),)
    assert useless([st.stamp("command.refused", {"reason": "no_target"})]) == (("command.refused", "no_target"),)
    assert useless([st.stamp("items.used", {"empty": True})]) == (("items.used", "empty"),)
    assert useless([st.stamp("room", {"name": "x"})]) is None


def test_three_times_the_same_no_holds_the_command_back_and_says_so():
    g, clock, seen = guard()
    for _ in range(3):
        answer(g, clock, "drink canteen", EMPTY)
    assert len(seen) == 1 and seen[0]["source"] == "base/drink" and seen[0]["pause_s"] == PAUSE_S
    assert g.paused("base/drink", "drink canteen") > 0
    assert g.paused("base/drink", "drink fountain") == 0, "the same behavior on another target is free"
    assert g.paused("base/eat", "drink canteen") == 0


def test_the_pause_grows_and_a_success_forgets():
    g, clock, seen = guard()
    for _ in range(3):
        answer(g, clock, "kill fido", [("command.refused", {"reason": "no_target"})], source="base/hunt")
    clock.t += PAUSE_S
    for _ in range(3):
        answer(g, clock, "kill fido", [("command.refused", {"reason": "no_target"})], source="base/hunt")
    assert [d["pause_s"] for d in seen] == [PAUSE_S, 2 * PAUSE_S]
    clock.t += 2 * PAUSE_S
    answer(g, clock, "kill fido", [("combat.hit", {"attacker": "self"})], source="base/hunt")
    for _ in range(3):
        answer(g, clock, "kill fido", [("command.refused", {"reason": "no_target"})], source="base/hunt")
    assert seen[-1]["pause_s"] == PAUSE_S, "a success starts the pauses over"


def test_different_answers_slow_spacing_and_people_are_not_loops():
    g, clock, seen = guard()
    answer(g, clock, "give bread Lil", [("items.give_failed", {"reason": "hands_full"})], source="x/give")
    answer(g, clock, "give bread Lil", [("command.refused", {"reason": "no_target"})], source="x/give")
    answer(g, clock, "give bread Lil", [("items.give_failed", {"reason": "hands_full"})], source="x/give")
    for _ in range(3):
        answer(g, clock, "drink canteen", EMPTY)
        clock.t += 60
    for _ in range(5):
        answer(g, clock, "stand", [("position.refused", {"reason": "already"})], source="play", kind="human")
    assert seen == []


def test_through_the_runtime_a_held_command_is_not_sent(memoria_proto):
    import pytest
    from test_items import agent
    from test_runtime import WORLD
    if not WORLD.exists():  # pragma: no cover
        pytest.skip("tbaMUD world files not present")
    h = agent(memoria_proto, "Senia", inventory=["a canteen"])
    h.ev("condition", thirsty=True)
    h.advance(6)
    assert "drink canteen" in h.take(), "the drink behavior drinks"
    h.rt.loops._paused[("base/drink", "drink canteen")] = h.t + 30      # as after three empty drinks
    for _ in range(4):
        h.advance(6)
    assert "drink canteen" not in h.take(), "held back while paused"
    h.advance(31)
    h.advance(6)
    assert "drink canteen" in h.take(), "free again when the pause is over"


def test_a_no_beside_an_added_event_is_still_a_no():
    # Round 6: 'follow Vallen' x329. The adapter adds group.change beside Mundi's "already following";
    # it counted as progress and the streak never reached three.
    g, clock, seen = guard()
    for _ in range(3):
        answer(g, clock, "follow Vallen", [("group.failed", {"reason": "already_following"}),
                                           ("group.change", {"event": "following", "who": "Vallen"})],
               source="role-follower/join_leader")
    assert seen and seen[0]["command"] == "follow Vallen"


def test_the_same_command_too_often_is_held_whatever_the_answer():
    # "You can't afford it!" comes as a tell, not a refusal: still a hundred buys in a few minutes.
    g, clock, seen = guard()
    for _ in range(9):
        answer(g, clock, "buy plate", [("comm.tell", {"from": "the armorer", "text": "You can't afford it!"})],
               source="base/buy_here")
        clock.t -= 4                                       # 9 sends within a minute
    assert seen and seen[0]["answer"][0][0] == "repeated"
    assert g.paused("base/buy_here", "buy plate") > 0


def test_walking_a_corridor_is_not_a_loop():
    # Round 7: the frequency rule held the leader's exploring ('east' x9 along a corridor) and kills fell.
    g, clock, seen = guard()
    for i in range(12):
        answer(g, clock, "east", [("room", {"name": f"room {i}"})], source="role-leader/explore")
        clock.t -= 4
    assert seen == []
