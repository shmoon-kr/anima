from pathlib import Path

import pytest
from conftest import world_dir

from anima.adapters.tbamud_text.replay import replay_file
from anima.bus import Bus
from anima.memoria import Memoria
from anima.memoria.graph import Conditions
from anima.protocol.envelope import Event

WORLD = world_dir()
HAZARDS = Path(__file__).parents[1] / "third_party" / "tbamud" / "hazards.yaml"
FIX = Path(__file__).parents[1] / "third_party" / "tbamud" / "fixtures"
pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")

TEMPLE_SQUARE = 3005


@pytest.fixture(scope="module")
def mem() -> Memoria:
    return Memoria.from_tbamud(WORLD, HAZARDS)


def test_world_loaded(mem):
    assert len(mem.world.rooms) > 12000
    assert mem.world.rooms[TEMPLE_SQUARE].name == "The Temple Square"
    assert mem.world.zones[186].name == "Newbie Zone"


@pytest.mark.parametrize("guild,route", [
    ("The Tournament And Practice Yard", "s e e s e s"),
    ("The Mages' Laboratory", "s w w s s e"),
    ("The Secret Yard", "s s e s e s"),
    ("The Clerics' Inner Sanctum", "w n w"),
])
def test_guild_paths_match_known_routes(mem, guild, route):
    path = mem.graph.path_to_name(TEMPLE_SQUARE, guild, Conditions(has_light=True))
    assert " ".join(d[0] for d in path) == route


def test_traps_and_bad_exits_are_never_used(mem):
    mid_air = mem.graph.rooms_named("Mid-Air")
    assert mid_air and all(v in mem.graph.traps for v in mid_air)
    dump = mem.graph.rooms_named("The Dump")[0]
    assert "down" not in mem.graph.exits_from(dump, Conditions(has_light=True))
    assert mem.graph.path(TEMPLE_SQUARE, mid_air[0], Conditions(has_light=True)) is None


def test_darkness_needs_light_and_night_darkens_outdoors(mem):
    g = mem.graph
    dark = next(v for v, r in mem.world.rooms.items() if r.dark and v not in g.traps)
    assert not g.enterable(dark, Conditions(has_light=False))
    assert g.enterable(dark, Conditions(has_light=True))
    field = next(v for v, r in mem.world.rooms.items() if r.outdoors and not r.dark and v not in g.traps)
    assert g.enterable(field, Conditions(night=False))
    assert not g.enterable(field, Conditions(night=True))


def test_locked_exit_is_blocked_for_a_while(mem):
    t = [0.0]
    mem.graph.clock = lambda: t[0]
    try:
        bus = Bus()
        mem.attach(bus)
        loc = mem.locator("Gate")
        loc.vnum = TEMPLE_SQUARE
        bus.publish(Event("command.sent", {"text": "north", "source": {}}, agent="Gate"))
        bus.publish(Event("move.failed", {"reason": "locked"}, agent="Gate"))
        assert "north" not in mem.graph.exits_from(TEMPLE_SQUARE, Conditions(has_light=True))
        t[0] = 601
        assert "north" in mem.graph.exits_from(TEMPLE_SQUARE, Conditions(has_light=True))
    finally:
        import time
        mem.graph.clock = time.monotonic


def test_locator_follows_commands_and_trail(mem):
    loc = mem.locator("Walker")
    sq = mem.world.rooms[TEMPLE_SQUARE]
    loc.on_event(Event("room", {"name": sq.name, "desc": sq.desc}))
    assert loc.vnum == TEMPLE_SQUARE and loc.certain
    for d in ("north", "south", "south"):
        loc.on_event(Event("command.sent", {"text": d}))
        nxt = mem.world.rooms[mem.world.rooms[loc.vnum].exits[d].to]
        loc.on_event(Event("room", {"name": nxt.name, "desc": nxt.desc}))
    assert loc.vnum == sq.exits["south"].to
    assert [s.dir for s in loc.trail] == ["south"]        # north+south cancelled
    assert loc.way_back() == "north"


def test_locator_on_replayed_log(mem):
    loc = mem.locator("Replay")
    names = []
    for e in replay_file(FIX / "flee.log", "Replay", keep_raw=False):
        loc.on_event(e)
        if e.type == "room":
            names.append((e.data["name"], mem.world.rooms[loc.vnum].name, loc.certain))
    assert names == [("Main Street", "Main Street", True),
                     ("Inside The East Gate Of Midgaard", "Inside The East Gate Of Midgaard", True)]


def test_knowledge_identifies_occupants(mem):
    k = mem.knowledge
    guard = k.identify({"text": "A cityguard stands here.", "hints": []})
    assert guard.kind == "mob" and any("cityguard" in m.keywords for m in guard.mobs)
    blob = k.identify({"text": "An oozing green gelatinous blob is here, sucking in bits of debris.", "hints": []})
    assert blob.kind == "mob" and blob.has_flag("memory")
    pc = k.identify({"text": "Lil the Apprentice of Magic is standing here.", "hints": ["my_group"]})
    assert pc.kind == "player" and pc.name == "Lil"
    fido = k.identify({"text": "The beastly fido is resting here.", "hints": []})
    assert fido.kind == "mob"


def test_closed_then_locked_gate_blocks_the_exit(mem):
    t = [0.0]
    mem.graph.clock = lambda: t[0]
    try:
        bus = Bus()
        mem.attach(bus)
        loc = mem.locator("Night")
        gate = 3041
        loc.vnum = gate
        bus.publish(Event("command.sent", {"text": "east", "source": {}}, agent="Night"))
        bus.publish(Event("move.failed", {"reason": "closed", "door": "gate"}, agent="Night"))
        bus.publish(Event("command.sent", {"text": "open gate", "source": {}}, agent="Night"))
        bus.publish(Event("move.failed", {"reason": "locked"}, agent="Night"))
        t[0] = 300
        assert "east" not in mem.graph.exits_from(gate, Conditions(has_light=True))
        t[0] = 700
        assert "east" in mem.graph.exits_from(gate, Conditions(has_light=True))
    finally:
        import time
        mem.graph.clock = time.monotonic


def test_an_avoided_zone_is_not_crossed_but_its_ends_can_be_reached(mem):
    # round 26: the leader explored from the Hidden Valley (zone 40) into zone 64 (levels 8-19);
    # no member could path to him and he waited there for them, half an hour
    cond = Conditions(has_light=True, avoid_zones=frozenset({64}))
    assert mem.graph.path(4077, 6400, cond) == ["east"], "into the zone the goal is in"
    assert mem.graph.path(6400, 4077, cond) is not None, "out of the zone I stand in"
    assert mem.graph.path(6401, 4077, cond) is not None, "from deeper in it too"


def test_open_before_the_step_then_locked_blocks_the_exit(mem):
    # round 35: the door west of 6505 was opened before stepping (no "closed" first), "It seems
    # to be locked." blocked nothing, and the shopping trip tried it every 75 seconds
    t = [0.0]
    mem.graph.clock = lambda: t[0]
    try:
        bus = Bus()
        mem.attach(bus)
        gate = 3041
        mem.locator("Day").vnum = gate
        mem.door_tried("Day", gate, "east")
        bus.publish(Event("command.sent", {"text": "open gate", "source": {}}, agent="Day"))
        bus.publish(Event("move.failed", {"reason": "locked"}, agent="Day"))
        bus.publish(Event("command.sent", {"text": "east", "source": {}}, agent="Day"))
        bus.publish(Event("move.failed", {"reason": "closed", "door": "gate"}, agent="Day"))
        t[0] = 300
        assert "east" not in mem.graph.exits_from(gate, Conditions(has_light=True)), "the step's 'closed' did not shorten it"
    finally:
        mem.graph.clock = __import__("anima.timescale", fromlist=["now"]).now
