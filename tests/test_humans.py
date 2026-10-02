"""What people type (anima play): aliases, #name / #all / #party, #go, #take, and the wheel rule."""
import pytest

from anima.session.humans import Desk, describe_for_narration
from anima.session.terminal import render
from test_runtime import WORLD, Harness, TEMPLE_SQUARE, MOVES, enter_game, memoria_proto  # noqa: F401

ROSTER = ["Vallen", "Lil", "Lumina", "Elysia"]


@pytest.fixture
def desk(tmp_path):
    a = tmp_path / "aliases.yaml"
    a.write_text('tg: "#go The Temple Square"\nzz: "#all sleep"\nk: "kill $1"\n'
                 "heal: \"#lumina cast 'cure light' $1\"\nbuy3: \"buy $1;buy $1;buy $1\"\nloop: loop\n")
    return Desk(ROSTER, [a])


def orders(desk, line, focus="Vallen", mates=None):
    o, notes = desk.route(line, focus, mates)
    return [(x.agent, x.kind, x.text) for x in o], notes


def test_a_plain_line_goes_to_the_focused_character(desk):
    assert orders(desk, "look")[0] == [("Vallen", "send", "look")]


def test_hash_name_all_and_party(desk):
    assert orders(desk, "#lil cast 'magic missile' orc")[0] == [("Lil", "send", "cast 'magic missile' orc")]
    assert orders(desk, "#lu rest")[0] == [("Lumina", "send", "rest")]           # a unique prefix
    assert [a for a, *_ in orders(desk, "#all rest")[0]] == ROSTER
    assert orders(desk, "#party look", mates=["Vallen", "Lil"])[0] == [("Vallen", "send", "look"), ("Lil", "send", "look")]
    assert orders(desk, "#nobody look")[1]


def test_aliases_with_arguments_and_several_commands(desk):
    assert orders(desk, "k orc")[0] == [("Vallen", "send", "kill orc")]
    assert orders(desk, "heal Vallen")[0] == [("Lumina", "send", "cast 'cure light' Vallen")]
    assert [t for *_, t in orders(desk, "buy3 bread")[0]] == ["buy bread"] * 3
    assert len(orders(desk, "zz")[0]) == 4
    assert orders(desk, "look; k rat")[0] == [("Vallen", "send", "look"), ("Vallen", "send", "kill rat")]
    assert orders(desk, "loop")[0] == [("Vallen", "send", "loop")]            # an alias of itself stops


def test_go_take_release(desk):
    assert orders(desk, "tg")[0] == [("Vallen", "go", "The Temple Square")]
    assert orders(desk, "#lil #go Market Square")[0] == [("Lil", "go", "Market Square")]
    assert orders(desk, "#take lil")[0] == [("Lil", "take", "")]
    assert orders(desk, "#release Lil")[0] == [("Lil", "release", "")]


@pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")
def test_a_typed_command_pauses_the_agent_and_go_walks_there(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.take()
    h.rt.hold(30)
    h.rt.human_goal = "The Temple Square"
    h.advance(1)
    moves = [(t, src.kind) for t, src, _ in h.sent if t in MOVES]
    assert moves and all(kind == "human" for _, kind in moves)     # the person's walk, not the agent's choice
    assert not [t for t, src, _ in h.sent if src.kind == "behavior"]
    h.take()
    h.rt.human_goal = None
    h.advance(31)                                           # the hold is over: the agent chooses again
    assert not h.rt.held()
    h.rt.taken = True
    h.advance(60)
    assert h.rt.held()


def test_narration_and_mud_echo():
    ev = {"type": "command.sent", "data": {"text": "kick", "source": {"kind": "behavior", "id": "class-warrior/fight_skill",
                                                                       "reason": "score 0.95"}}}
    assert describe_for_narration(ev) == "» kick   (behavior fight_skill: score 0.95)"
    assert render({"k": "ev", "ev": ev}, "mud", lambda e: "") is None             # mud: no agent noise
    human = {"type": "command.sent", "data": {"text": "look", "source": {"kind": "human"}}}
    assert "> look" in render({"k": "ev", "ev": human}, "mud", lambda e: "")
    assert render({"k": "text", "s": "Hi\r\n"}, "mud", None) == "Hi\n"
