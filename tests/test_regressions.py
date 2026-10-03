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
    p.ev("Vallen", "group.change", event="new_leader", who="Vallen", formed=True)   # the leader's group is there
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


def test_striking_back_means_we_are_up_though_no_one_said_so(memoria_proto):
    # tbaMUD and Mundi: a sleeper who is attacked is set fighting silently (fight.c set_fighting);
    # anima kept 'sleeping', sent wake, heard 'You are already awake...', and sent it again.
    p = PartyHarness(memoria_proto, ["Vallen", "Lumina"])
    together(p, ["Vallen", "Lumina"])
    p.ev("Lumina", "position", position="sleeping")
    p.ev("Lumina", "combat.hit", attacker="the quasit", victim="self", outcome="hit", severity=1)
    p.ev("Lumina", "combat.hit", attacker="self", victim="the quasit", outcome="miss", severity=0)
    assert p.rt["Lumina"].state.position == "standing"


def test_spare_gear_goes_to_the_member_who_can_use_it(memoria_proto):
    # Mundi: Vallen carried six spare vests while Lumina wore none.
    vest = "a bright green newbie vest"
    p = PartyHarness(memoria_proto, ["Vallen", "Lumina", "Senia"])
    together(p, ["Vallen", "Lumina", "Senia"])
    p.ev("Vallen", "items.equipment", slots=[{"slot": "worn on body", "text": vest}])
    p.ev("Vallen", "items.inventory", items=[{"text": vest, "count": 1}])
    p.ev("Senia", "items.equipment", slots=[{"slot": "worn on body", "text": vest}])
    p.ev("Lumina", "items.equipment", slots=[])
    assert p.rt["Vallen"].ctx.items.gear_gift() == "vest Lumina", "to the one without, not to Senia who wears one"
    for n in ("Vallen", "Lumina", "Senia"):
        p.take(n)
    sent = []
    for _ in range(6):
        p.tick()
        sent += p.take("Vallen")
    assert "give vest Lumina" in sent


def test_resting_without_prompts_asks_for_the_numbers(memoria_proto):
    # Mundi with never-hungry test characters: no tick message, so no prompt, so a camp never saw
    # itself heal and slept on (a server prompts only after output or input).
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    p.ev("Lil", "position", position="resting")
    p.ev("Lil", "prompt", hp=10, mp=100, mv=90)
    p.take("Lil")
    p.tick(20)
    assert "" not in p.take("Lil"), "a prompt 20 s ago is fresh enough"
    p.tick(15)
    assert "" in p.take("Lil")
    p.tick(5)
    assert "" not in p.take("Lil"), "not again at once"
    p.ev("Vallen", "position", position="standing")
    p.take("Vallen")
    p.tick(40)
    assert "" not in p.take("Vallen"), "standing: the world talks to us anyway"


def test_a_refusal_for_resting_says_we_are_resting(memoria_proto):
    # Round 8: anima restarted, the leader came back linkless and resting ("reconnected" says no
    # position); every command got "You feel too relaxed..." and no kill was made in 30 game minutes.
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    p.ev("Vallen", "command.refused", reason="resting")
    assert p.rt["Vallen"].state.position == "resting"
    p.take("Vallen")
    sent = []
    for _ in range(6):
        p.tick()
        sent += p.take("Vallen")
    assert "stand" in sent, sent


def test_a_member_trains_at_its_guild_door_on_the_town_run(memoria_proto):
    # Round 13: followers set out for their guilds alone, go_to_leader called them back at once
    # (Carmilla: 173 starts, 146 abandoned) and the party split. Now the guild door is a stop of the
    # town run; there the member goes in, trains, and comes back, the trip waiting.
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"])
    p.ev("Elysia", "char.score", level=5, practices=8)
    p.ev("Elysia", "char.skills", skills={"cure light": "not learned"})
    ctx = p.rt["Elysia"].ctx
    assert ctx._train_wanted()
    door = ctx._guild_door()
    assert door is not None
    for n in ("Vallen", "Elysia"):
        p.room(n, door)
    p.board.trip = {"stops": [door], "visited": [], "arrived": None, "started": p.t}
    assert p.board.trip_stop() == door and ctx._guild_door() == door
    for n in ("Vallen", "Elysia"):
        p.take(n)
    sent = []
    for _ in range(4):
        p.tick()
        sent += p.take("Elysia")
    guild = p.mem.graph.rooms_named(p.rt["Elysia"].program.policies["guild_room"])[0]
    inward = p.mem.graph.path(door, guild, p.mem.conditions())[0]
    assert inward in sent, sent
    # round 78: the trip moved on while she walked in (the mark came only with practising); she
    # turned back at the guild, and set off again: 300 steps, out of movement
    assert p.board.shop_busy("Vallen"), "the trip waits from the moment she sets off"
    p.tick(15)
    assert p.board.shop_busy("Vallen"), "and while she is on her way in"


def test_the_floor_forgets_what_others_took(memoria_proto):
    # Round 15: 'get leggings' 23 times, "You don't see a leggings here.": another member had taken
    # them, and our picture of the room still showed them.
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    st = p.rt["Lil"].state
    st.room["objects"] = [{"text": "A pair of bronze leggings lies here.", "count": 1},
                          {"text": "A dagger lies here.", "count": 2}]
    p.ev("Lil", "occupant.item", who="Vallen", action="get", text="a dagger")
    assert [o["count"] for o in st.room["objects"] if "dagger" in o["text"]] == [1]
    p.ev("Lil", "items.failed", action="get", reason="not_here", keyword="leggings")
    assert not any("leggings" in o["text"] for o in st.room["objects"])


def test_a_spell_the_server_has_not_got_is_not_cast_or_practised_again(memoria_proto):
    # round 20: Mundi has 7 of tbaMUD's spells; Lil cast 'burning hands' into "그 주문은 아직 Mundi에 없다."
    h = Harness(memoria_proto, agent="Lil")
    enter_game(h)
    h.ev("char.skills", skills={"burning hands": "superb", "magic missile": "average"})
    h.ev("combat.hit", attacker="the kobold", victim="self", verb="hit", severity=1, kind="weapon")
    h.take()
    h.advance(4)
    assert "cast 'burning hands'" in " ".join(h.take())
    h.ev("skill.result", skill="cast", ok=False, reason="not_yet")
    h.ev("combat.hit", attacker="the kobold", victim="self", verb="hit", severity=1, kind="weapon")
    h.advance(4)
    out = " ".join(h.take())
    assert "burning hands" not in out and "magic missile" in out, out
    assert not h.rt.ctx._knows("burning hands")


def test_login_housekeeping_queues_behind_what_behaviors_send(memoria_proto):
    # round 25: eight setup commands went first (reflexes jump the queue) and the follower's
    # `follow`/`group join` left 3 s late, after the leader had moved: no_person, join_who
    h = Harness(memoria_proto, agent="Lil")
    enter_game(h)
    prio = {s[0]: s[2] for s in h.sent}
    assert prio.get("autoloot") == 1 and prio.get("inventory") == 1, prio


def test_members_arriving_a_moment_after_me_are_not_recounted(memoria_proto):
    # round 30: their own room views come before word of their arrival reaches the leader, so
    # right after each step all five were "unseen" and the leader looked again: ~90 looks a round
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"])
    p.take("Vallen")
    p.rt["Vallen"].state.room["occupants"] = []
    p.rt["Vallen"].state.marks["room_seen"] = p.t           # the leader just walked in
    p.tick(1)
    assert "look" not in p.take("Vallen"), "a moment: they are on their way in"
    p.tick(3)
    assert "look" in p.take("Vallen"), "still not listed after a while: the listing is stale"


def test_a_door_the_room_shows_closed_is_opened_before_the_step(memoria_proto):
    # round 33: eight steps a round into "The door seems to be closed."
    h = Harness(memoria_proto, agent="Vallen")
    enter_game(h)
    h.rt.state.room["exits"] = [{"dir": "south", "closed": True}, {"dir": "north", "closed": False}]
    h.rt.ctx.source = __import__("anima.protocol.commands", fromlist=["Source"]).Source("behavior", "t")
    h.take()
    h.rt.ctx._step("south")
    out = h.take()
    assert out[0].startswith("open ") and out[1] == "south", out
    h.ev("door.changed", command="open", who="Carmilla", door="door")
    h.rt.ctx.move_pending_until = -1e9
    h.rt.ctx.last_sent.clear()
    h.rt.ctx._step("south")
    assert h.take() == ["south"], "opened by someone here: just walk"


def test_rest_and_stand_up_agree_and_mana_is_not_a_reason_without_spells(memoria_proto):
    # round 39: rest scored a little for mana under 90% and stand_up fired at 50%: rest, stand,
    # rest, stand every 6 s. And rest_mp_pct 0 (a warrior) still rested for mana.
    h = Harness(memoria_proto, agent="Vallen")             # rest_mp_pct 0
    enter_game(h)
    h.ev("prompt", hp=40, mp=30, mv=90)
    h.take()
    for _ in range(4):
        h.advance(6)
    assert not any(t in ("rest", "sleep") for t in h.take()), "full hit points: mana is no reason to rest"
    h2 = Harness(memoria_proto, agent="Lil")               # a mage: rests for mana
    enter_game(h2)
    h2.ev("prompt", hp=40, mp=70, mv=90)
    h2.ev("position", position="resting", **{"from": "standing"})
    h2.take()
    for _ in range(4):
        h2.advance(6)
    assert "stand" not in h2.take(), "70% mana: rest still has something to give"


def test_a_follower_who_does_not_know_where_it_is_looks_before_going_to_the_leader(memoria_proto):
    # round 40: reconnected asleep (no room view, `look` refused), woken by go_to_leader, which
    # then beat orient for 34 minutes with nowhere to go from; the leader waited for everyone
    p = PartyHarness(memoria_proto, ["Vallen", "Carmilla"])
    p.ev("Vallen", "connection.in_game", how="entered")
    v = memoria_proto.world.rooms[NEWBIE_ENTRANCE]
    p.ev("Vallen", "room", name=v.name, desc=v.desc, exits=[{"dir": d, "closed": False} for d in v.exits],
         objects=[], occupants=[], dark=False)
    p.ev("Carmilla", "connection.in_game", how="reconnected")
    p.ev("Carmilla", "char.vitals_max", hp=40, mp=100, mv=90)
    p.ev("Carmilla", "prompt", hp=40, mp=100, mv=90)
    p.ev("Carmilla", "command.refused", reason="sleeping")    # "In your dreams, or what?"
    p.tick(1)
    p.ev("Carmilla", "position", position="standing", **{"from": "sleeping"})   # woken
    p.take("Carmilla")
    for _ in range(8):
        p.tick(1)
    assert "look" in p.take("Carmilla")


def test_coins_taken_are_gold_not_a_thing_in_the_bag(memoria_proto):
    # round 49: "junk coins" -> "You don't seem to have any coins." (not_carried)
    h = Harness(memoria_proto, agent="Lumina")
    enter_game(h)
    h.ev("items.inventory", items=[{"text": "a bread", "count": 1}])
    h.ev("items.got", text="a little pile of gold coins", id="money:50/41682", **{"from": "the corpse of the orc"})
    h.ev("items.got", text="a pile of coins", **{"from": "the corpse of the orc"})       # text adapter: no id
    assert h.rt.state.inventory == ["a bread"]


def test_danger_arriving_at_a_camp_wakes_the_sleepers_before_stepping_away(memoria_proto):
    # round 52: the sentry stepped away from a green gelatinous blob, and her `wake`s went out
    # in the next room ("no one by that name here"); five slept on beside it
    p = PartyHarness(memoria_proto, ["Vallen", "Carmilla"])
    together(p, ["Vallen", "Carmilla"])
    p.rt["Vallen"].state.position = "sleeping"
    p.take("Carmilla")
    danger = p.rt["Carmilla"].program.policies["danger"][0]
    p.ev("Carmilla", "occupant.arrived", who=danger)
    out = p.take("Carmilla")
    assert "wake Vallen" in out, out
    moves = [i for i, t in enumerate(out) if t in ("north", "south", "east", "west", "up", "down")]
    assert not moves or out.index("wake Vallen") < moves[0], out



def test_a_follower_waits_for_the_leaders_group_before_joining(memoria_proto):
    # round 53: at login the followers' `group join` came before the leader's `group new`
    # ("Vallen is not in a group"), then follow again: already_following
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"], following=False)
    p.tick()
    assert not any(t.startswith("group join") for t in p.take("Elysia"))
    p.ev("Vallen", "group.change", event="new_leader", who="Vallen", formed=True)
    p.tick(6)
    assert "group join Vallen" in p.take("Elysia")


def test_leader_waits_until_everyone_follows_before_going_home(memoria_proto):
    # round 61: at login the leader set off home once all were in the room, before they followed;
    # their `group join` went out after he had left ("no one by that name here")
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"], vnum=3001, following=False)    # the temple: away from the rally
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


def test_one_thing_on_the_floor_is_gone_for_by_one_member(memoria_proto):
    # round 69: two or three members sent `get sleeves` for one pair; all but one got "not here"
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    together(p, ["Vallen", "Lil"])
    sleeves = next(o for o in memoria_proto.world.objs.values() if o.short == "some cool newbie sleeves")
    for n in ("Vallen", "Lil"):
        p.rt[n].state.room["objects"] = [{"text": sleeves.long, "count": 1}]
    a = p.rt["Vallen"].ctx.items.pickup_item(0)
    b = p.rt["Lil"].ctx.items.pickup_item(0)
    assert a == "sleeves" and b is None, (a, b)
    assert p.rt["Vallen"].ctx.items.pickup_item(0) == "sleeves", "my own claim does not stop me"
    for n in ("Vallen", "Lil"):
        p.rt[n].state.room["objects"] = [{"text": sleeves.long, "count": 2}]
    assert p.rt["Lil"].ctx.items.pickup_item(0) == "sleeves", "two pairs: one each"


def test_a_member_sets_off_for_its_guild_when_the_party_is_near_the_door_not_from_afar(memoria_proto):
    # round 78: from the newbie zone she ran ahead and turned back; round 81: the stop (inside the
    # guard) was a room the leader can never stand in, so "only in that room" never came
    def setup(vnum):
        p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
        together(p, ["Vallen", "Elysia"], vnum=vnum)
        p.ev("Elysia", "char.score", level=5, practices=8)
        p.ev("Elysia", "char.skills", skills={"cure light": "not learned"})
        door = p.rt["Elysia"].ctx._guild_door()
        p.board.trip = {"stops": [door], "visited": [], "arrived": None, "started": p.t}
        for n in ("Vallen", "Elysia"):
            p.take(n)
        for _ in range(3):
            p.tick()
        return p.rt["Elysia"].tasks.name
    assert setup(3004) == "train_at_guild", "the entrance, a step from the stop inside the guard: in she goes"
    assert setup(NEWBIE_ENTRANCE) != "train_at_guild", "from afar: wait for the party"


def test_the_guild_door_stays_the_same_from_inside_the_guild(memoria_proto):
    # round 83: started at the entrance, at the guild room train_on_trip no longer held (no way
    # "toward where I am" from where I am) and go_to_leader took her out: four times, no practice
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    together(p, ["Vallen", "Elysia"], vnum=3004)
    ctx = p.rt["Elysia"].ctx
    door = ctx._guild_door()
    guild = memoria_proto.graph.rooms_named(p.rt["Elysia"].program.policies["guild_room"])[0]
    p.room("Elysia", guild)
    assert door is not None and ctx._guild_door() == door
