"""Regression tests for problems found on the live server during phase 1 (docs/PHASE-1-CLOSE.md §3).

Each test is named after the symptom that was seen live.
"""
import pytest

from anima.adapters.tbamud_text.parser import TbamudTextAdapter
from anima.protocol.envelope import Event
from test_runtime import (MOVES, NEWBIE_ENTRANCE, TEMPLE_SQUARE, WORLD, Harness, PartyHarness, drive,  # noqa: F401
                          enter_game, memoria_proto)

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")


def feed(*lines):
    a = TbamudTextAdapter("Vallen")
    return [(e.type, e.data) for e in a.feed("\r\n".join(lines) + "\r\n10H 1M 1V > ") if e.type != "prompt"]


# ---------------------------------------------------------------- adapter

def test_already_following_and_already_grouped_are_not_failures():
    assert feed("You are already following him.")         # $M: him/her/it == [("group.change", {"event": "following", "who": None})]
    assert feed("But you are already part of a group.") == [("group.change", {"event": "joined", "who": "self"})]


def test_cursed_item_that_cannot_be_junked_is_recognised():
    assert feed("You can't junk a yellow and green ring, it must be CURSED!") == [
        ("items.cannot_drop", {"text": "a yellow and green ring", "reason": "cursed"})]


def test_poison_and_snake_bite_are_recognised():
    assert feed("You feel very sick.") == [("affect.changed", {"who": "self", "affect": "poison", "on": True})]
    assert feed("Carmilla gets violently ill!") == [("affect.changed", {"who": "Carmilla", "affect": "poison", "on": True})]
    assert feed("The green snake bites you!")[0] == (
        "combat.hit", {"attacker": "the green snake", "victim": "self", "verb": "bite", "severity": 4, "kind": "skill"})


# ---------------------------------------------------------------- one agent

def test_reconnected_without_a_room_view_looks_until_located(memoria_proto):
    h = Harness(memoria_proto)
    h.ev("connection.in_game", how="reconnected")       # server shows no room on reconnect
    h.ev("char.vitals_max", hp=40, mp=100, mv=90)
    h.ev("prompt", hp=40, mp=100, mv=90)
    h.take()
    h.advance(6)
    src = [s[1].id for s in h.sent if s[0] == "look"]
    assert src and src[0] == "base/orient"


def test_closed_door_is_opened_instead_of_walked_into_again(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.take()
    h.ev("move.failed", reason="closed", door="gate")
    assert h.texts() == ["open gate"]


def test_rested_agent_stands_up_instead_of_lying_there(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.ev("position", position="resting")
    h.take()
    h.advance(25)
    assert "stand" in h.texts()


def test_low_movement_percent_starts_rest_and_waits_for_sixty(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.ev("prompt", hp=40, mp=100, mv=30)               # 33% < rest_mv_pct 40, above rest_mv 25
    h.take()
    h.advance(25)
    assert "sleep" in h.texts()
    h.ev("position", position="sleeping")
    h.take()
    h.ev("prompt", hp=40, mp=100, mv=50)               # 55%: keep sleeping
    h.advance(10)
    assert h.texts() == []
    h.ev("prompt", hp=40, mp=100, mv=60)               # 66%: up
    h.advance(10)
    assert "wake" in h.texts()


def test_item_keyword_is_the_noun_the_server_matches(memoria_proto):
    k = memoria_proto.knowledge
    assert k.item_keyword("a canteen") == "canteen"           # first keyword in the world file is "water"
    assert k.item_keyword("a shiny newbie dagger") == "dagger"


def test_empty_container_is_not_drunk_again_for_a_minute(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.ev("items.inventory", items=[{"text": "a canteen", "count": 1}])
    h.ev("condition", thirsty=True)
    h.take()
    h.advance(1)
    assert "drink canteen" in h.take()
    h.ev("items.used", action="drink", empty=True)
    for _ in range(5):
        h.advance(10)
    assert "drink canteen" not in h.texts()


def test_surplus_is_junked_but_a_cursed_item_is_skipped(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.ev("items.inventory", items=[{"text": "a yellow and green ring", "count": 1},
                                   {"text": "a scroll of recall", "count": 1},
                                   {"text": "a waybread", "count": 16}])            # over inv_hard (16)
    h.take()
    h.advance(1)
    first = [t for t in h.take() if t.startswith("junk")]
    assert first == ["junk ring"]
    h.ev("items.cannot_drop", text="a yellow and green ring", reason="cursed")
    h.advance(5)
    assert [t for t in h.texts() if t.startswith("junk")] == ["junk recall"]


def test_missing_skill_target_refreshes_the_room(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.advance(3, tick=False)
    h.take()
    h.ev("skill.result", skill="backstab", ok=False, reason="no_target")      # "Backstab who?"
    assert h.texts() == ["look"]
    assert "skill_target_missing" in h.rt.state.marks


# ---------------------------------------------------------------- party

def together(p, names, vnum=NEWBIE_ENTRANCE, following=True):
    for n in names:
        p.enter(n, vnum)
    if following:
        for n in names[1:]:
            p.ev(n, "group.change", event="following", who="Vallen")
            p.ev(n, "group.change", event="joined", who=n)
        p.ev("Vallen", "group.change", event="new_leader", who="Vallen")
    for n in names:
        p.take(n)


def test_party_member_arriving_is_not_an_alert(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    p.enter("Vallen", NEWBIE_ENTRANCE)
    before = p.rt["Vallen"].state.last_alert_t
    p.enter("Lil", NEWBIE_ENTRANCE)                    # Vallen sees "Lil has arrived."
    assert p.rt["Vallen"].state.last_alert_t == before


def test_same_estimated_room_but_not_seen_is_not_together(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"])
    p.ev("Elysia", "items.inventory", items=[{"text": "a waybread", "count": 3}])
    p.rt["Elysia"].state.room["occupants"] = []        # her room listing does not show Vallen
    p.ev("Vallen", "condition", hungry=True)
    p.take("Elysia")
    p.tick(25)
    out = p.take("Elysia")
    assert "give waybread Vallen" not in out
    assert "look" in out                               # recount_party


def test_non_cleric_hands_the_container_to_a_thirsty_cleric(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lumina"])
    together(p, ["Vallen", "Lumina"])
    p.ev("Vallen", "items.inventory", items=[{"text": "a canteen", "count": 1}])
    p.ev("Lumina", "char.skills", skills={"create water": "good", "cure light": "good"})
    p.ev("Lumina", "condition", thirsty=True)
    p.take("Vallen")
    p.tick()
    assert "give canteen Lumina" in p.take("Vallen")


def test_rejoining_the_leader_comes_before_handing_out_water(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"], following=False)
    p.ev("Elysia", "char.skills", skills={"create water": "superb"})
    p.ev("Elysia", "items.inventory", items=[{"text": "a canteen", "count": 1}])
    p.ev("Vallen", "condition", thirsty=True)
    p.take("Elysia")
    p.tick()
    out = p.take("Elysia")
    assert "follow Vallen" in out and not any(t.startswith("cast") for t in out)


def test_mob_death_does_not_remove_a_member_whose_title_has_the_mob_name(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    p.rt["Vallen"].state.room["occupants"] = [{"text": "Lil the Lost Newbie is standing here.", "hints": [], "flags": []},
                                              {"text": "A lost newbie is here.", "hints": [], "flags": []}]
    p.ev("Vallen", "combat.death", who="the lost newbie")
    assert [o["text"] for o in p.rt["Vallen"].state.room["occupants"]] == ["Lil the Lost Newbie is standing here."]


def test_leader_waits_until_everyone_follows_before_exploring(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"], following=False)
    p.ev("Vallen", "group.change", event="new_leader", who="Vallen")
    p.ev("Lil", "char.skills", skills={})
    p.take("Vallen")
    for _ in range(3):
        p.tick(3)
    assert not [t for t in p.take("Vallen") if t in MOVES]
    p.ev("Lil", "group.change", event="following", who="Vallen")
    p.ev("Lil", "group.change", event="joined", who="Lil")
    for _ in range(3):
        p.tick(3)
    assert [t for t in p.take("Vallen") if t in MOVES]


def test_leader_does_not_drag_a_member_with_no_movement_left(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    p.ev("Lil", "prompt", hp=40, mp=100, mv=20)        # 22%
    p.take("Vallen")
    for _ in range(3):
        p.tick(3)
    assert not [t for t in p.take("Vallen") if t in MOVES]


# ---------------------------------------------------------------- hunting grounds

def test_rally_circuit_moves_on_and_survives_a_restart(memoria_proto, tmp_path):
    from anima.party.blackboard import PartyBoard
    b = PartyBoard(memoria_proto, ["Vallen"], "Vallen", rally="A", circuit=["A", "B"], save_path=tmp_path / "party.json")
    assert b.next_rally("Vallen") == "B"
    assert b.next_rally("Lil") == "B"                  # only the leader moves the party
    b2 = PartyBoard(memoria_proto, ["Vallen"], "Vallen", rally="A", circuit=["A", "B"], save_path=tmp_path / "party.json")
    b2.load()
    assert b2.rally == "B"


def test_unreachable_new_rally_reverts_to_the_previous_one(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen"])
    p.board.rally = "The End Of The Path"
    p.board.circuit = ["The Entrance To The Newbie Zone", "The End Of The Path"]
    p.enter("Vallen", TEMPLE_SQUARE)
    p.ev("Vallen", "items.inventory", items=[{"text": "a canteen", "count": 1}])   # no town run for water (D33)
    for d in p.mem.world.rooms[TEMPLE_SQUARE].exits:   # every way out is shut (a locked gate at night)
        p.mem.graph.block_exit(TEMPLE_SQUARE, d, 600)
    for _ in range(3):
        p.tick(30)
    assert p.board.rally == "The Entrance To The Newbie Zone"


def test_hands_full_stops_giving_for_a_while(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"])
    p.ev("Elysia", "char.skills", skills={"create water": "superb", "create food": "superb", "cure light": "good"})
    p.ev("Elysia", "items.inventory", items=[{"text": "a canteen", "count": 1}])
    p.ev("Vallen", "condition", thirsty=True)
    p.take("Elysia")
    p.tick()
    assert "give canteen Vallen" in p.take("Elysia")
    p.ev("Elysia", "items.give_failed", to="Vallen", reason="hands_full")
    for _ in range(10):
        p.tick(5)
    assert not any(t.startswith("give ") for t in p.take("Elysia"))


def test_a_room_whose_description_is_indented_two_spaces_is_still_a_room():
    a = TbamudTextAdapter("Vallen")
    text = ("\x1b[0;33mThe Edge Of The Ravine\x1b[0m\r\n"
            "  You stand at the edge of a really long drop down.  A small rope\r\n"
            "mountains to the east.\r\n"
            "\x1b[0;36m[ Exits: e w ]\x1b[0m\r\n10H 1M 1V > ")
    rooms = [e for e in a.feed(text) if e.type == "room"]
    assert rooms and rooms[0].data["name"] == "The Edge Of The Ravine"


def test_a_guild_guard_keeps_a_follower_out_so_it_waits_at_the_door_and_eats(memoria_proto):
    # Live (2026-10-03): the leader and Senia (warriors) drank in the swordsmen's bar (3022); the
    # other four stood at its door (3021), sent there by go_to_leader again and again, and never ate.
    entrance, bar = 3021, 3022
    p = PartyHarness(memoria_proto, ["Vallen", "Lil", "Senia"])
    p.enter("Vallen", bar)
    p.enter("Senia", bar)
    p.enter("Lil", entrance)
    p.tick()
    assert "east" in p.take("Lil"), "it tries the way to the leader first"
    p.ev("Lil", "move.failed", reason="guarded")
    assert (entrance, "east") in p.mem.blocked["Lil"] and "Senia" not in p.mem.blocked, "the guard stops Lil, not the warriors"
    assert p.mem.graph.path(entrance, bar, p.mem.conditions(agent="Senia")) == ["east"]
    assert p.board.value("leader_reachable", "Lil") is False
    assert p.board.value("wait_vnum", "Lil") == entrance
    p.ev("Lil", "condition", hungry=True)
    p.ev("Lil", "items.inventory", items=[{"text": "a waybread", "count": 1}])
    p.take("Lil")
    for _ in range(6):
        p.tick()
    out = p.take("Lil")
    assert "east" not in out, out
    assert any(t.startswith("eat ") for t in out), out


def test_a_follower_kept_out_walks_to_the_door_to_wait(memoria_proto):
    entrance, bar, market = 3021, 3022, 3014
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    p.enter("Vallen", bar)
    p.mem.blocked["Lil"] = {(entrance, "east")}
    p.enter("Lil", market)
    p.tick()
    assert p.board.value("wait_vnum", "Lil") == entrance
    first = p.mem.graph.path(market, entrance, p.mem.conditions(agent="Lil"))[0]
    assert first in p.take("Lil"), "on its way to the door"


def test_a_fresh_entry_forgets_the_group_so_the_leader_forms_it_again(memoria_proto):
    # Mundi (2026-10-03): after a server restart the group was gone but the leader still thought it
    # led one; the others got "Vallen is not in a group!" and nobody assisted in its fights.
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    assert p.rt["Vallen"].state.in_group and p.rt["Lil"].state.following == "Vallen"
    p.ev("Lil", "connection.in_game", how="reconnected")
    assert p.rt["Lil"].state.following == "Vallen", "a reconnect keeps them"
    p.ev("Vallen", "position", position="resting")
    for n in ("Vallen", "Lil"):
        p.ev(n, "connection.closed", reason="remote")
        p.ev(n, "connection.in_game", how="entered")
    assert not p.rt["Vallen"].state.in_group and p.rt["Lil"].state.following is None
    assert p.rt["Vallen"].state.position == "standing", "one comes in standing, whatever it was"
    for n in ("Vallen", "Lil"):
        p.room(n, NEWBIE_ENTRANCE)
    p.take("Vallen")
    sent = []
    for _ in range(15):
        p.tick()
        sent += p.take("Vallen")
    assert "group new" in sent, sent


def test_autoassist_found_off_is_turned_back_on(memoria_proto):
    # Toggles flip: at each login `autoassist` was sent once, so the saved setting alternated on, off,
    # on... and a follower who came in with it off watched the leader fight alone.
    p = PartyHarness(memoria_proto, ["Vallen", "Carmilla"])
    together(p, ["Vallen", "Carmilla"])
    p.take("Carmilla")
    p.ev("Carmilla", "toggle.state", name="autoassist", value=False)
    p.tick()
    assert "autoassist" in p.take("Carmilla")


def test_the_above_level_warning_marks_the_zone_entered_not_the_one_left(memoria_proto):
    # Mundi: stepping out of Midgaard's east gate toward Miden'nir (35) marked Midgaard (30) itself;
    # every way through the city was then avoided and the leader gave up on the newbie zone forever.
    p = PartyHarness(memoria_proto, ["Vallen"])
    p.enter("Vallen", 3053)                                   # outside the east gate of Midgaard
    way = next(d for d, e in p.mem.world.rooms[3053].exits.items() if p.mem.world.rooms[e.to].zone != 30)
    p.ev("Vallen", "command.sent", text=way, source={"kind": "behavior", "id": "x/y"})
    p.ev("Vallen", "zone.above_level")
    entered = p.mem.world.rooms[p.mem.world.rooms[3053].exits[way].to].zone
    assert p.mem.above_level_zones == {entered} and 30 not in p.mem.above_level_zones
    assert p.mem.graph.path(3053, NEWBIE_ENTRANCE, p.mem.conditions()) is not None


def _fled(memoria_proto, fought=True):
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    way = next(iter(p.mem.world.rooms[NEWBIE_ENTRANCE].exits))
    if fought:
        p.ev("Vallen", "combat.hit", attacker="self", victim="the quasit", outcome="hit")
    p.ev("Vallen", "combat.flee_seen", who="the quasit")
    p.ev("Vallen", "occupant.left", who="the quasit", dir=way)
    for n in ("Vallen", "Lil"):
        p.take(n)
    return p, way


def test_the_leader_chases_a_fled_mob_one_room(memoria_proto):
    # Mundi: the quasit fled west; the leader explored north and the kill was lost.
    p, way = _fled(memoria_proto)
    assert p.rt["Vallen"].state.chase_dir == way
    sent = []
    for _ in range(12):                  # once the fight is over (its window), within the chase's 20 s
        p.tick()
        sent += p.take("Vallen")
    assert way in sent
    assert p.rt["Vallen"].state.chase_dir is None, "one room: the chase is over"


def test_no_chase_after_a_mob_we_did_not_fight_or_into_a_zone_above_us(memoria_proto):
    p, way = _fled(memoria_proto, fought=False)
    assert p.rt["Vallen"].state.chase_dir is None
    p, way = _fled(memoria_proto)
    p.mem.above_level_zones.add(p.mem.world.rooms[p.mem.world.rooms[NEWBIE_ENTRANCE].exits[way].to].zone)
    sent = []
    for _ in range(12):
        p.tick()
        sent += p.take("Vallen")
    assert way not in sent
