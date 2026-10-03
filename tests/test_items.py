"""Items and shops (D32): wear better gear, sell what is not needed, buy upgrades and bread, shop as a party."""
import pytest

from test_regressions import together
from test_runtime import MOVES, NEWBIE_ENTRANCE, WORLD, Harness, PartyHarness, enter_game, memoria_proto  # noqa: F401

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")


def shop_room(mem, vnum):
    return mem.world.shops[vnum].rooms[0]


WEAPONS, ARMORY, BAKERY, GENERAL = 3003, 3004, 3001, 3002


def agent(mem, name, vnum=NEWBIE_ENTRANCE, gold=0, inventory=(), equipment=()):
    h = Harness(mem, agent=name)
    enter_game(h, vnum=vnum)
    h.ev("char.score", level=8, gold=gold)
    h.ev("items.inventory", items=[{"text": t, "count": 1} for t in inventory])
    h.ev("items.equipment", slots=[{"slot": s, "text": t} for s, t in equipment])
    h.take()
    return h


def test_a_better_body_armor_replaces_the_old_one(memoria_proto):
    h = agent(memoria_proto, "Vallen", inventory=["a breast plate"],
              equipment=[("worn on body", "a bright green newbie vest")])
    h.advance(6)
    out = h.take()
    assert out[out.index("remove vest")+1] == "wear plate"



def mundi_gear(h, inventory=(), equipment=()):
    """What the Mundi adapter gives: each item with the vnum from its id."""
    h.ev("items.inventory", items=[{"text": t, "count": 1, "vnum": v} for t, v in inventory])
    h.ev("items.equipment", slots=[{"slot": sl, "text": t, "vnum": v} for sl, t, v in equipment])
    h.take()


def test_the_server_says_which_same_named_object_it_is(memoria_proto):
    # round 18: 'a breast plate' was guessed as an AC 6 one of its name (three in the world) and the
    # AC 6 bronze breast plate looked better than the AC 7 one worn: off and on, 34 times
    h = agent(memoria_proto, "Lumina")
    mundi_gear(h, inventory=[("a bronze breast plate", 3046)], equipment=[("worn on body", "a breast plate", 3040)])
    assert h.rt.ctx.items.upgrade_item() is None
    h.advance(6)
    assert not any(t.startswith(("remove", "wear")) for t in h.take())


def test_the_upgrade_is_not_mistaken_for_what_was_just_taken_off(memoria_proto):
    # every keyword of the breast plate is the bronze one's too, and the bronze one goes first in the bag
    h = agent(memoria_proto, "Lumina")
    mundi_gear(h, inventory=[("a breast plate", 3040)], equipment=[("worn on body", "a bronze breast plate", 3046)])
    h.advance(6)
    out = h.take()
    assert "remove bronze" in out or "remove plate" in out, out
    assert "wear 2.plate" in out, out


def test_a_word_only_the_upgrade_answers_to_is_used(memoria_proto):
    h = agent(memoria_proto, "Lumina")
    mundi_gear(h, inventory=[("a bronze breast plate", 3046)], equipment=[("worn on body", "a bright green newbie vest", 18602)])
    h.ev("items.inventory", items=[{"text": "a bronze breast plate", "count": 1, "vnum": 3046},
                                   {"text": "a breast plate", "count": 1, "vnum": 29243}])
    items = h.rt.ctx.items
    assert items._nth("a bronze breast plate", ["a breast plate"]) == "bronze"

def test_class_and_weapon_kind_limits(memoria_proto):
    thief = agent(memoria_proto, "Carmilla", inventory=["a long sword"])      # slashes: no backstab
    thief.advance(6)
    assert not any(t.startswith("wield") for t in thief.take())
    cleric = agent(memoria_proto, "Lumina", inventory=["a long sword"])       # ITEM_ANTI_CLERIC
    cleric.advance(6)
    assert not any(t.startswith("wield") for t in cleric.take())


def test_what_we_cannot_use_is_sold_in_a_shop_that_buys_it(memoria_proto):
    h = agent(memoria_proto, "Lumina", vnum=shop_room(memoria_proto, WEAPONS), inventory=["a long sword"])
    h.advance(4)
    assert "sell sword" in h.take()
    assert h.rt.state.marks.get("shopping")


def test_an_affordable_upgrade_is_bought(memoria_proto):
    h = agent(memoria_proto, "Vallen", vnum=shop_room(memoria_proto, ARMORY), gold=5000,
              equipment=[("worn on body", "a bright green newbie vest")])
    h.advance(4)
    buys = [t for t in h.take() if t.startswith("buy ")]
    assert buys and buys[0] in ("buy plate", "buy helmet")      # the biggest gain it can afford


def test_bread_when_food_runs_low_but_never_the_reserve(memoria_proto):
    poor = agent(memoria_proto, "Lil", vnum=shop_room(memoria_proto, BAKERY), gold=100)
    poor.advance(4)
    assert not any(t.startswith("buy ") for t in poor.take())    # 100 gold is the reserve
    rich = agent(memoria_proto, "Lil", vnum=shop_room(memoria_proto, BAKERY), gold=500)
    rich.advance(4)
    assert "buy waybread" in rich.take()


def test_the_party_goes_shopping_when_someone_has_enough_to_sell(memoria_proto):
    p = PartyHarness(memoria_proto, ["Vallen", "Lumina"])
    together(p, ["Vallen", "Lumina"])
    p.ev("Lumina", "char.score", level=8, gold=0)
    p.ev("Lumina", "items.inventory", items=[{"text": "a long sword", "count": 6}])   # cleric cannot use them
    p.board._last_trip_t = -1e9
    for n in ("Vallen", "Lumina"):
        p.take(n)
    p.tick(31)
    p.tick(1)
    assert p.board.trip is not None and shop_room(memoria_proto, WEAPONS) in p.board.trip["stops"]
    assert any(t in MOVES for t in p.take("Vallen"))


WATER = 3009


def test_long_thirst_puts_the_fountain_on_the_town_run(memoria_proto):
    h = agent(memoria_proto, "Senia", inventory=["a canteen"])
    h.ev("condition", thirsty=True)
    h.advance(181)
    fountain = memoria_proto.graph.rooms_named("The Temple Square")[0]
    assert fountain in h.rt.ctx.items.shop_wants([], 100, 6)


def test_containers_are_filled_at_the_fountain(memoria_proto):
    fountain = memoria_proto.graph.rooms_named("The Temple Square")[0]
    h = agent(memoria_proto, "Senia", vnum=fountain, inventory=["a canteen"])
    r = memoria_proto.world.rooms[fountain]
    h.ev("room", name=r.name, desc=r.desc, exits=[{"dir": d, "closed": False} for d in r.exits], occupants=[],
         objects=[{"text": "A large fountain carved from blue-streaked marble is here, bubbling merrily.", "count": 1}],
         dark=False)
    h.advance(4)
    assert not any(t.startswith("fill") for t in h.take()), "full since it was bought: nothing to fill"
    h.ev("items.used", action="drink", text="a canteen")
    h.advance(4)
    assert "fill canteen fountain" in h.take() and h.rt.state.marks.get("shopping")
    h.ev("items.failed", action="fill", reason="no_room")
    for _ in range(3):
        h.advance(600)
    assert not any(t.startswith("fill") for t in h.take()), "round 21: 'no room for more' at every fountain"
    h.ev("items.failed", action="drink", reason="empty", text="a canteen")
    h.advance(4)
    assert "fill canteen fountain" in h.take(), "found empty: fill it"


def test_a_member_without_a_container_buys_a_canteen(memoria_proto):
    h = agent(memoria_proto, "Senia", vnum=shop_room(memoria_proto, WATER), gold=500)
    h.advance(4)
    assert "buy canteen" in h.take()


def test_after_the_town_run_the_party_hunts_somewhere_else(memoria_proto):
    from anima.party.ladder import Ladder
    p = PartyHarness(memoria_proto, ["Vallen", "Lumina"])
    together(p, ["Vallen", "Lumina"])
    p.board.ladder = Ladder(memoria_proto, hub=lambda: memoria_proto.locator("Vallen").vnum, clock=lambda: p.t)
    p.board._refresh_circuit()
    assert len(p.board.circuit) >= 2
    p.board.rally = p.board.circuit[0]
    p.board.trip = {"stops": [], "visited": [], "arrived": None, "started": p.t}
    assert p.board.trip_stop() is None                       # nothing left: the trip ends
    assert p.board.rally == p.board.circuit[1]


def test_a_bought_light_is_held_and_not_bought_again(memoria_proto):
    # Mundi: "a torch" names 13 world objects, one a fixture and one anti-good; buying saw the shop's
    # torch as an upgrade, wearing judged every torch and never held it, and buy torch ran ~100 times.
    h = agent(memoria_proto, "Vallen", vnum=shop_room(memoria_proto, GENERAL), gold=500)
    names = {"torch": "a torch", "lantern": "a lantern"}
    bag, sent = [], []
    for _ in range(8):                                   # the server's side: what is bought is in the bag
        h.advance(5)
        out = h.take()
        sent += out
        for t in out:
            if t.startswith("buy ") and t.split()[1] in names:
                bag.append(names[t.split()[1]])
            if t.startswith("hold ") and names.get(t.split()[1]) in bag:
                bag.remove(names[t.split()[1]])
                h.ev("items.equipment", slots=[{"slot": "used as light", "text": names[t.split()[1]]}])
        h.ev("items.inventory", items=[{"text": b, "count": 1} for b in bag])
    buys = [t for t in sent if t.startswith("buy ")]
    assert any(t.startswith("hold ") for t in sent), sent
    assert len(buys) <= 2, f"a light or two, not a pile: {buys}"


def test_a_full_bag_junks_extra_lights_but_keeps_one_to_spare(memoria_proto):
    # Mundi: ten torches from a buying loop filled a level-1 bag (it holds a few); buying, giving and
    # swapping gear then all failed with "too many", and nothing was junked below inv_hard (16).
    h = agent(memoria_proto, "Senia", inventory=["a torch"] * 10 + ["a bread"])
    assert h.rt.ctx._surplus_item(["food", "drinkcon", "light", "key"]) == "torch"
    h.advance(5)
    assert not any(t.startswith("junk ") for t in h.take()), "not full yet: nothing to junk"
    h.ev("items.failed", action="get", reason="too_many", text="a dagger")
    out = []
    for _ in range(4):                       # it holds one as its light first (wear before junk), then junks
        h.advance(5)
        got = h.take()
        out += got
        if "hold torch" in got:
            h.ev("items.equipment", slots=[{"slot": "used as light", "text": "a torch"}])
    assert "junk torch" in out, out
    h.ev("items.inventory", items=[{"text": "a torch", "count": 2}, {"text": "a bread", "count": 1}])
    assert h.rt.ctx._surplus_item(["food", "drinkcon", "light", "key"]) is None, "two kept"


def test_a_shared_name_is_bought_by_its_number_in_the_list(memoria_proto):
    # Mundi: 'buy plate' took the bronze breast plate (840) for the breast plate (216); the keeper
    # said "You can't afford it!" ~100 times. Every keyword of the breast plate is the bronze one's too.
    h = agent(memoria_proto, "Vallen", vnum=shop_room(memoria_proto, ARMORY), gold=400)
    items = h.rt.ctx.items
    want = items._buy_choice(items.shop_here(), 100)
    assert want is not None
    shared = all(any(kw in memoria_proto.world.objs[v].keywords for v in items.shop_here().products
                     if memoria_proto.world.objs[v].short != want.short) for kw in want.keywords)
    assert want.short == "a breast plate" and shared, "the case: every keyword shared with the bronze one"
    assert items.buy_here(100) is None and items.list_needed(100)
    h.advance(10)
    assert "list" in h.take()
    stock = [memoria_proto.world.objs[v].short for v in items.shop_here().products]
    h.ev("shop.list", items=[{"text": t, "price": 1} for t in stock])
    assert items.buy_here(100) == f"#{stock.index(want.short) + 1}"
    h.advance(10)
    assert f"buy #{stock.index(want.short) + 1}" in h.take(), "round 16: the number was refused as a forbidden '#'"


def test_a_bug_in_a_tick_is_recorded_and_the_agent_keeps_going(memoria_proto):
    # round 16: one exception ended the tick loop of all six; only reflexes were left, for 30 minutes
    h = agent(memoria_proto, "Vallen", vnum=shop_room(memoria_proto, ARMORY), gold=400)
    real = h.rt.selector.tick
    calls = []

    def broken(seq=None):
        calls.append(seq)
        if len(calls) == 1:
            raise ValueError("boom")
        return real(seq)
    h.rt.selector.tick = broken
    h.advance(5)
    errs = [e for e in h.events if e.type == "runtime.error"]
    assert errs and "ValueError: boom" in errs[0].data["error"]
    h.advance(5)
    assert len(calls) == 2, "the next tick still chooses"


def test_the_list_is_asked_again_when_the_stock_changes(memoria_proto):
    # round 17: members sold while one bought by number; '#23' pointed at another product, and the
    # keeper's "Haven't got that on storage - try list!" came ten times
    h = agent(memoria_proto, "Vallen", vnum=shop_room(memoria_proto, ARMORY), gold=400)
    items = h.rt.ctx.items
    stock = [memoria_proto.world.objs[v].short for v in items.shop_here().products]
    h.ev("shop.list", items=[{"text": t, "price": 1} for t in stock])
    assert items.buy_here(100).startswith("#")
    h.ev("shop.result", action="sell", who="Lumina", text="a vest", ok=True)
    assert items.buy_here(100) is None and items.list_needed(100), "someone sold: the numbers moved"
    h.ev("shop.list", items=[{"text": t, "price": 1} for t in stock])
    h.advance(10)
    assert any(t.startswith("buy #") for t in h.take())
    h.ev("comm.tell", **{"from": "the armorer", "to": "Vallen", "text": "Haven't got that on storage - try list!",
                         "direction": "in"})
    assert items.list_needed(100), "the keeper said try list"


def test_a_full_bag_stops_buying_for_a_minute(memoria_proto):
    h = agent(memoria_proto, "Vallen", vnum=shop_room(memoria_proto, ARMORY), gold=400)
    stock = [memoria_proto.world.objs[v].short for v in h.rt.ctx.items.shop_here().products]
    h.ev("shop.list", items=[{"text": t, "price": 1} for t in stock])
    h.ev("shop.result", action="buy", text="leggings", ok=False, reason="too_many")
    for _ in range(5):
        h.advance(5)
    assert not any(t.startswith(("buy ", "list")) for t in h.take()), "bag full: sell or junk first"
