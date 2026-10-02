"""Items and shops (D32): wear better gear, sell what is not needed, buy upgrades and bread, shop as a party."""
import pytest

from test_regressions import together
from test_runtime import MOVES, NEWBIE_ENTRANCE, WORLD, Harness, PartyHarness, enter_game, memoria_proto  # noqa: F401

pytestmark = pytest.mark.skipif(not WORLD.exists(), reason="tbaMUD world files not present")


def shop_room(mem, vnum):
    return mem.world.shops[vnum].rooms[0]


WEAPONS, ARMORY, BAKERY = 3003, 3004, 3001


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
