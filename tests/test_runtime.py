"""Runtime scenarios with the real world data and Vallen's real package stack (fake clock)."""
from pathlib import Path

import pytest
from conftest import world_dir

from anima.animus.queue import AnimusQueue, FakeProvider
from anima.bus import Bus
from anima.memoria import Memoria
from anima.protocol.envelope import Event, Stamper
from anima.runtime.agent import AgentRuntime
from anima.sigil.program import Package, build_program, load_agent, load_package

ROOT = Path(__file__).parents[1]
WORLD = world_dir()
pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")


@pytest.fixture(scope="module")
def memoria_proto():
    return Memoria.from_tbamud(WORLD, ROOT / "third_party" / "tbamud" / "hazards.yaml")


class Harness:
    def __init__(self, mem: Memoria, agent: str = "Vallen", program=None, animus=None):
        self.t = 1000.0
        self.bus = Bus()
        self.mem = Memoria(mem.world, mem.graph.hazards)
        self.mem.attach(self.bus)
        self.stamper = Stamper(agent, clock=lambda: self.t)
        self.sent: list[tuple[str, object, int]] = []
        self.events: list[Event] = []
        self.bus.subscribe(self.events.append)
        prog = program or load_agent(ROOT / "agents" / f"{agent}.yaml", ROOT / "packages")
        self.rt = AgentRuntime(agent, prog, self.mem, self.bus, self.stamper, self._send, lambda: self.t,
                               animus=animus)
        self.rt.attach()

    def _send(self, text, source, priority):
        self.sent.append((text, source, priority))
        self.bus.publish(self.stamper.stamp("command.sent", {"text": text, "source": source.to_dict()}))

    def ev(self, type_, **data):
        self.bus.publish(self.stamper.stamp(type_, data))

    def room(self, vnum, occupants=()):
        r = self.mem.world.rooms[vnum]
        self.ev("room", name=r.name, desc=r.desc, exits=[{"dir": d, "closed": False} for d in r.exits],
                objects=[], occupants=[{"text": o, "flags": [], "hints": []} for o in occupants], dark=False)

    def advance(self, secs, tick=True):
        self.t += secs
        if tick:
            self.rt.tick()

    def texts(self):
        return [s[0] for s in self.sent]

    def take(self):
        out, self.sent = self.texts(), []
        return out


NEWBIE_ENTRANCE = 18600
TEMPLE_SQUARE = 3005


def enter_game(h: Harness, vnum=NEWBIE_ENTRANCE, hp=40, occupants=()):
    h.ev("connection.in_game", how="entered")
    h.ev("char.vitals_max", hp=40, mp=100, mv=90)
    h.ev("char.score", level=3, gold=20, practices=0)
    h.ev("prompt", hp=hp, mp=100, mv=90)
    h.room(vnum, occupants)


def test_entering_sets_up_toggles_and_wimpy(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    sent = h.sent
    texts = [s[0] for s in sent]
    for t in ("autoloot", "autogold", "autosplit", "score", "practice", "inventory", "equipment"):
        assert t in texts
    assert "toggle wimpy 13" in texts                     # min(40*45%, 40/3)
    src = next(s[1] for s in sent if s[0] == "autoloot")
    assert src.kind == "reflex" and src.id == "base/setup_on_enter"
    h.take()
    h.ev("toggle.state", name="autoloot", value=False)    # it was already on: we just turned it off
    assert h.texts() == ["autoloot"]


def test_hunts_target_and_records_why(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, occupants=["A kobold is here, looking for trouble."])
    h.take()
    h.advance(1)
    assert "kill kobold" in h.texts()
    src = next(s[1] for s in h.sent if s[0] == "kill kobold")
    assert src.kind == "behavior" and src.id == "role-leader/hunt"      # layer that last defined it
    assert src.scores[0]["id"] == "role-leader/hunt"
    switch = [e for e in h.events if e.type == "runtime.behavior"][-1]
    assert switch.data["to"] == "hunt"


def test_shunned_room_is_not_hunted(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, occupants=["A kobold is here.", "A cityguard stands here."])
    h.take()
    h.advance(1)
    assert not any(t.startswith("kill") for t in h.texts())


def test_flee_reflex_jumps_queue(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.take()
    h.ev("combat.hit", attacker="the kobold", victim="self", verb="hit", severity=4, kind="weapon")
    h.ev("prompt", hp=15, mp=100, mv=90)                  # 37% < flee 45%
    assert ("flee", 0) in [(s[0], s[2]) for s in h.sent]
    assert h.sent[-1][1].id == "base/flee_when_low"


def test_danger_mob_makes_us_flee_at_once(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.take()
    h.ev("combat.hit", attacker="the green gelatinous blob", victim="self", verb="crush", severity=0, kind="weapon")
    assert "flee" in h.texts()


def test_rests_when_low_then_hunts_again(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, hp=10)                                  # 25% < rest 30%, empty lit room -> sleep
    h.take()
    h.advance(25)                                         # past the post-arrival alert window
    assert "sleep" in h.texts()
    h.ev("position", position="sleeping")
    h.take()
    h.ev("prompt", hp=30, mp=100, mv=90)                  # 75%: keep sleeping until 90%
    h.advance(10)
    assert h.texts() == []
    h.ev("prompt", hp=38, mp=100, mv=90)                  # 95%: rested
    h.room(NEWBIE_ENTRANCE, ["A kobold is here, looking for trouble."])
    h.advance(25)
    assert h.texts()[:3] == ["wake", "stand", "kill kobold"]


def test_eats_food_from_inventory(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.ev("items.inventory", items=[{"text": "a waybread", "count": 1}])
    h.ev("condition", hungry=True)
    h.take()
    h.advance(1)
    assert "eat waybread" in h.texts()


def drive(h: Harness, until, steps=40, on_move=None):
    """Tick every 4s; answer each movement command with the room it leads to. Returns all commands."""
    log: list[str] = []
    for _ in range(steps):
        h.advance(4)
        out = h.take()
        log += out
        for d in [t for t in out if t in ("north", "south", "east", "west", "up", "down")]:
            here = h.mem.locator("Vallen").vnum
            h.room(h.mem.world.rooms[here].exits[d].to)
            if on_move:
                on_move(log)
        if until(out):
            break
    return log


MOVES = ("north", "south", "east", "west", "up", "down")


def test_training_task_walks_practises_and_comes_back_and_survives_a_reflex(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, vnum=TEMPLE_SQUARE)
    h.ev("char.score", practices=2)
    h.ev("char.skills", skills={"kick": "not learned"})
    h.take()
    woke = []

    def wake_once(log):
        if [t for t in log if t in MOVES] == ["south", "east"] and not woke:
            woke.append(1)                                # someone wakes us on the way: reflex fires, task pauses
            h.ev("position", position="sitting", awakened_by="Lil")

    log = drive(h, lambda out: "practice kick" in out, on_move=wake_once)
    assert [t for t in log if t in MOVES] == ["south", "east", "east", "south", "east", "south"]
    assert "practice kick" in log
    h.ev("char.practiced", result="improved")
    h.ev("char.practiced", result="improved")
    h.ev("char.skills", skills={"kick": "poor"})
    log = drive(h, lambda out: h.mem.locator("Vallen").vnum == TEMPLE_SQUARE)
    assert [t for t in log if t in MOVES] == ["north", "west", "north", "west", "west", "north"]
    h.advance(4)
    events = [e.data["event"] for e in h.events if e.type == "runtime.task"]
    assert "paused" in events and "abandoned" not in events and events[-1] == "succeeded"


def test_bad_sigil_is_refused_and_old_program_kept(memoria_proto):
    h = Harness(memoria_proto)
    old = h.rt.program
    bad = Package("broken", 0, {"sigil": 0, "package": "broken",
                                "behaviors": {"x": {"considerations": ["self.nope"], "do": ["rest()"]}}})
    base = load_package(ROOT / "packages" / "base")
    assert h.rt.reload(lambda: build_program("Vallen", [base, bad])) is False
    assert h.rt.program is old
    rej = [e for e in h.events if e.type == "runtime.sigil"][-1]
    assert rej.data["event"] == "rejected" and any("self.nope" in x for x in rej.data["errors"])


ASKING = Package("asking", 0, {"sigil": 0, "package": "asking", "asks": {
    "safe_to_hunt": {"when": "bool(pick_target(policy.targets, policy.shun))", "question": "Is it safe to attack?",
                     "context": {"room": "room.name", "occupants": "room.occupants"},
                     "tier": "local_fast", "priority": "routine", "timeout_s": 30, "default": True,
                     "schema": {"type": "boolean"}}},
    "behaviors": {"hunt": {"considerations": ["bool(pick_target(policy.targets, policy.shun))",
                                              "linear(self.hp_pct, policy.flee_pct, policy.hunt_min_hp)",
                                              "bool(party.all_here)", "bool(answer.safe_to_hunt)"]}}})


async def test_animus_answer_arrives_as_event_and_changes_choice(memoria_proto):
    import asyncio
    base = load_package(ROOT / "packages" / "base")
    party = load_package(ROOT / "packages" / "party-midgaard")
    prog = build_program("Vallen", [base, party, ASKING])
    gate = asyncio.Event()

    async def slow_answer(req):
        await gate.wait()
        return False
    bus_holder = {}
    stampers = {}
    h = None

    def stamper_for(agent):
        return h.stamper
    animus = AnimusQueue(Bus(), {"local_fast": FakeProvider(lambda r: False)}, stamper_for)
    h = Harness(memoria_proto, program=prog, animus=animus)
    animus.bus = h.bus
    animus.providers["local_fast"].answer = lambda req, _f=slow_answer: _wrap(_f, req)
    worker = asyncio.create_task(animus.run())
    enter_game(h, occupants=["A kobold is here, looking for trouble."])
    h.take()
    h.advance(1)                                       # question goes out; default (True) lets us hunt now
    assert "kill kobold" in h.texts()
    req = [e for e in h.events if e.type == "animus.request"][-1]
    assert req.data["context"]["room"] == "The Entrance To The Newbie Zone"
    h.take()
    gate.set()
    for _ in range(20):
        await asyncio.sleep(0)
    resp = [e for e in h.events if e.type == "animus.response"]
    assert resp and resp[-1].data["answer"] is False
    h.ev("combat.death", who="the kobold")
    h.room(NEWBIE_ENTRANCE, ["A kobold is here, looking for trouble."])
    h.advance(5)
    assert not any(t.startswith("kill") for t in h.texts())   # answer says no
    worker.cancel()


async def _wrap(f, req):
    from anima.animus.queue import Reply
    return Reply(answer=await f(req), provider="fake")


async def test_animus_timeout_keeps_default_and_selection_never_waits(memoria_proto):
    import asyncio
    base = load_package(ROOT / "packages" / "base")
    party = load_package(ROOT / "packages" / "party-midgaard")
    prog = build_program("Vallen", [base, party, ASKING])
    never = asyncio.Event()
    h = None
    animus = AnimusQueue(Bus(), {"local_fast": FakeProvider(lambda r: False)}, lambda a: h.stamper,
                         clock=lambda: h.t)
    h = Harness(memoria_proto, program=prog, animus=animus)
    animus.bus = h.bus

    async def hang(req):
        await never.wait()
    animus.providers["local_fast"].answer = hang
    enter_game(h, occupants=["A kobold is here, looking for trouble."])
    h.advance(1)
    assert "kill kobold" in h.texts()                  # did not wait for the LLM
    h.t += 31
    animus.expire_due()
    assert [e for e in h.events if e.type == "animus.timeout"][-1].data["used_default"] is True
    assert h.rt.ctx.resolve("answer.safe_to_hunt") is True


def test_fight_interrupts_task_which_restarts_from_where_we_are(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, vnum=TEMPLE_SQUARE)
    h.ev("char.score", practices=2)
    h.ev("char.skills", skills={"kick": "not learned"})
    h.take()
    log = drive(h, lambda out: any(t in MOVES for t in out))
    first = [t for t in log if t in MOVES][0]
    assert first == "south"
    h.ev("combat.hit", attacker="the beastly fido", victim="self", verb="bite", severity=1, kind="weapon")
    h.advance(1)
    tasks = [(e.data["name"], e.data["event"]) for e in h.events if e.type == "runtime.task"]
    assert tasks[-1] == ("train_at_guild", "abandoned")
    h.ev("combat.death", who="the beastly fido")
    h.take()
    log = drive(h, lambda out: any(t in MOVES for t in out))
    tasks = [(e.data["name"], e.data["event"]) for e in h.events if e.type == "runtime.task"]
    assert ("train_at_guild", "started") in tasks[-3:]
    assert [t for t in log if t in MOVES][0] == "east"   # continues from Market Square, not from the start


def test_killed_mob_leaves_the_room_and_hunting_stops(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h, occupants=["A mountain lion is here, ready to pounce."])
    h.take()
    h.ev("combat.death", who="the mountain lion")         # death names the short description
    assert h.rt.state.occupants == []
    h.advance(5)
    assert not any(t.startswith("kill") for t in h.texts())


def test_refused_target_refreshes_the_room(memoria_proto):
    h = Harness(memoria_proto)
    enter_game(h)
    h.advance(3, tick=False)                              # entry setup already sent a look
    h.take()
    h.ev("command.refused", reason="no_target")
    assert h.texts() == ["look"]


class PartyHarness:
    """Several runtimes on one bus with a PartyBoard (no network)."""

    def __init__(self, mem: Memoria, names):
        from anima.party.blackboard import PartyBoard
        self.t = 1000.0
        self.bus = Bus()
        self.mem = Memoria(mem.world, mem.graph.hazards)
        self.mem.attach(self.bus)
        progs = {n: load_agent(ROOT / "agents" / f"{n}.yaml", ROOT / "packages") for n in names}
        self.board = PartyBoard.from_policies(self.mem, progs["Vallen"].policies, clock=lambda: self.t)
        self.board.roster = list(names)
        self.st = {n: Stamper(n, clock=lambda: self.t) for n in names}
        self.sent = {n: [] for n in names}
        self.rt = {}
        for n in names:
            rt = AgentRuntime(n, progs[n], self.mem, self.bus, self.st[n], self._sender(n), lambda: self.t,
                              party=self.board)
            rt.attach()
            self.board.register(n, rt.state)
            self.rt[n] = rt

    def _sender(self, n):
        def send(text, source, priority):
            self.sent[n].append(text)
            self.bus.publish(self.st[n].stamp("command.sent", {"text": text, "source": source.to_dict()}))
        return send

    def ev(self, n, type_, **data):
        self.bus.publish(self.st[n].stamp(type_, data))

    def room(self, n, vnum, occupants=()):
        """Like the server: the room lists members already there, and they see us arrive."""
        r = self.mem.world.rooms[vnum]
        there = [m for m in self.rt if m != n and self.mem.locator(m).vnum == vnum]
        occ = [f"{m} is standing here." for m in there] + list(occupants)
        self.ev(n, "room", name=r.name, desc=r.desc, exits=[{"dir": d, "closed": False} for d in r.exits],
                objects=[], occupants=[{"text": o, "flags": [], "hints": []} for o in occ], dark=False)
        for m in there:
            self.ev(m, "occupant.arrived", who=n)

    def enter(self, n, vnum, **extra):
        self.ev(n, "connection.in_game", how="entered")
        self.ev(n, "char.vitals_max", hp=40, mp=100, mv=90)
        self.ev(n, "char.score", level=5, practices=0)
        self.ev(n, "prompt", hp=40, mp=100, mv=90)
        self.room(n, vnum)

    def tick(self, secs=1):
        self.t += secs
        for rt in self.rt.values():
            rt.tick()

    def take(self, n):
        out, self.sent[n] = self.sent[n], []
        return out


def test_follower_walks_to_leader_then_follows_and_joins(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lil"])
    p.enter("Vallen", NEWBIE_ENTRANCE)
    p.enter("Lil", TEMPLE_SQUARE)
    for n in p.sent:
        p.take(n)
    p.tick()
    assert "group new" in p.take("Vallen")
    p.ev("Vallen", "group.change", event="new_leader", who="Vallen", formed=True)
    assert not any(t.startswith("kill") for t in p.sent["Vallen"])     # waits: not all here
    walked = []
    for _ in range(30):
        moves = [t for t in p.take("Lil") if t in MOVES]
        for d in moves:
            walked.append(d)
            here = p.mem.locator("Lil").vnum
            p.room("Lil", p.mem.world.rooms[here].exits[d].to)
        if p.mem.locator("Lil").vnum == NEWBIE_ENTRANCE:
            break
        p.tick(2)
    assert walked == ["north"] * 5 + ["east"]
    p.tick(2)
    out = p.take("Lil")
    assert "follow Vallen" in out and "group join Vallen" in out
    p.ev("Lil", "group.change", event="following", who="Vallen")
    p.ev("Lil", "group.change", event="joined", who="Lil")
    assert p.board.value("following", "Lil") and p.board.value("in_group", "Lil")
    assert p.board.value("all_here", "Vallen")


def test_provider_fills_her_container_and_gives_it_to_the_thirsty(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Elysia"])
    p.enter("Vallen", NEWBIE_ENTRANCE)
    p.enter("Elysia", NEWBIE_ENTRANCE)
    p.ev("Elysia", "char.skills", skills={"create water": "superb", "create food": "superb", "cure light": "good"})
    p.ev("Elysia", "items.inventory", items=[{"text": "a canteen", "count": 1}])
    p.ev("Elysia", "group.change", event="following", who="Vallen")
    p.ev("Elysia", "group.change", event="joined", who="Elysia")
    p.ev("Vallen", "condition", thirsty=True)
    for n in p.sent:
        p.take(n)
    p.tick()
    out = p.take("Elysia")
    assert "cast 'create water' canteen" in out and "give canteen Vallen" in out
