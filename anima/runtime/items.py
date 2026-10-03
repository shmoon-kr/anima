"""Items and shops for one agent (D32): wear what is better, sell what we do not need, buy what helps.

Everything is judged from the world data (anima/memoria/gear.py): an item in the inventory or on
the ground is matched to its world object by its short description, shops by the room we are in.
Who may use what comes from the agent's policies (`gear_avoid` anti flags, `weapon_kinds`).

Prices follow shop.c: we pay cost * buy_profit and get cost * min(sell_profit, buy_profit),
both nudged by charisma, so a 10% margin is kept when deciding what we can afford.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from anima.memoria import gear
from anima.memoria.model import Obj, Shop

if TYPE_CHECKING:
    from anima.runtime.context import Context

PRICE_MARGIN = 1.1
SHOP_REACH = 60                 # steps: shops further than this are not part of a trip
WANTS_EVERY_S = 60.0


class Items:
    def __init__(self, ctx: "Context") -> None:
        self.ctx = ctx
        self._shop_rooms: dict[int, Shop] | None = None
        self._wants: tuple[float, list[int]] = (-1e9, [])
        self.bought: dict[str, Obj] = {}      # a name we bought -> the very object (a shop knows which)

    # ------------------------------------------------------------ lookups
    @property
    def k(self):
        return self.ctx.memoria.knowledge

    def _pol(self, name: str, default: Any) -> Any:
        v = self.ctx.program.policies.get(name)
        return default if v is None else v

    def shop_rooms(self) -> dict[int, Shop]:
        if self._shop_rooms is None:
            self._shop_rooms = {r: sh for sh in self.ctx.memoria.world.shops.values() for r in sh.rooms
                                if sh.keeper in self.ctx.memoria.world.mobs}
        return self._shop_rooms

    def shop_here(self) -> Shop | None:
        v = self.ctx.memoria.locator(self.ctx.agent).vnum
        return self.shop_rooms().get(v) if v is not None else None

    def _obj(self, text: str) -> Obj | None:
        return self.k.item(text)

    def _kw(self, text: str) -> str:
        return self.k.item_keyword(text)

    def equipped(self) -> dict[str, list[Obj | None]]:
        out: dict[str, list[Obj | None]] = {}
        for s in self.ctx.state.equipment:
            slot = gear.SLOT_OF_LABEL.get(s.get("slot", ""))
            if slot:
                out.setdefault(slot, []).append(self._obj(s.get("text", "")))
        return out

    def _who(self) -> tuple[int, list[str], list[str]]:
        return (self.ctx.state.level or 1, list(self._pol("gear_avoid", [])), list(self._pol("weapon_kinds", [])))

    def _upgrade(self, obj: Obj, eq: dict[str, list[Obj | None]] | None = None):
        level, avoid, kinds = self._who()
        return gear.upgrade(self.equipped() if eq is None else eq, obj, level, avoid, kinds)

    def _upgrade_text(self, text: str, eq: dict[str, list[Obj | None]] | None = None):
        """For a seen name: an upgrade only if every world object with that name is one (smallest gain).
        A name can stand for a usable and an unusable object (one restricted to some classes, one not)."""
        if text in self.bought:
            cands = [self.bought[text]]                # we bought it: we know which object it is
        else:
            # in a bag only what can be taken: a fixture of the same name (a torch on a wall) is not it
            named = self.k.items(text)
            cands = [o for o in named if "take" in o.wear] or named
        if not cands:
            return None
        eq = self.equipped() if eq is None else eq
        ups = [self._upgrade(o, eq) for o in cands]
        if any(u is None for u in ups):
            return None
        return min(ups, key=lambda u: u[2])

    # ------------------------------------------------------------ wearing
    def upgrade_item(self) -> str | None:
        best = None
        for text in self.ctx.state.inventory:
            up = self._upgrade_text(text)
            if up and (best is None or up[2] > best[1][2]):
                best = (text, up)
        return self._kw(best[0]) if best else None

    def wear_upgrade(self) -> None:
        best = None
        for text in self.ctx.state.inventory:
            up = self._upgrade_text(text)
            if up and (best is None or up[2] > best[1][2]):
                best = (text, up)
        if best is None:
            return
        text, (slot, out, _) = best
        if out is not None:
            self.ctx.cmd(f"remove {self._kw(out.short)}")
        verb = "wield" if slot == "wield" else "hold" if slot in ("hold", "light") else "wear"
        self.ctx.cmd(f"{verb} {self._kw(text)}")
        self.ctx.cmd("equipment")
        self.ctx.cmd("inventory")

    # ------------------------------------------------------------ sharing
    def gear_gift(self) -> str | None:
        """'<keyword> <member>': gear I carry that is no upgrade for me but is one for a member in my room
        (the one who gains most, judged by their own class, level and what they wear). What nobody
        wants stays for selling or junking."""
        party = self.ctx.party
        if not hasattr(party, "items") or not hasattr(party, "in_room_with"):
            return None
        here = [n for n in party.in_room_with(self.ctx.agent) if n != self.ctx.agent and n in party.items]
        best = None
        for text in dict.fromkeys(self.ctx.state.inventory):
            cands = self.k.items(text)
            if not cands or not any(gear.slots(o) for o in cands) or self._upgrade_text(text):
                continue
            for name in here:
                up = party.items[name]._upgrade_text(text)
                if up and (best is None or up[2] > best[0]):
                    best = (up[2], text, name)
        return f"{self._kw(best[1])} {best[2]}" if best else None

    # ------------------------------------------------------------ selling and buying
    def _keep(self, o: Obj, keep: list[str], text: str) -> bool:
        return o.type in keep or "nosell" in o.extra or self._upgrade_text(text) is not None

    def sellable(self, shop: Shop | None, keep: list[str]) -> list[str]:
        """Inventory texts this shop (any shop if None) would buy and we do not need."""
        out = []
        for text in self.ctx.state.inventory:
            o = self._obj(text)
            if o is None or text.lower() in self.ctx.state.undroppable or self._keep(o, keep, text) or o.cost <= 0:
                continue
            if shop is None or o.type in shop.buys:
                out.append(text)
        return out

    def sellable_here(self, keep: list[str]) -> str | None:
        sh = self.shop_here()
        items = self.sellable(sh, keep or []) if sh else []
        return self._kw(items[0]) if items else None

    def price(self, o: Obj, sh: Shop) -> int:
        return int(o.cost * sh.buy_profit * PRICE_MARGIN) + 1

    def _buy_choice(self, sh: Shop, reserve: int) -> Obj | None:
        gold = (self.ctx.state.gold or 0) - (reserve or 0)
        eq = self.equipped()
        best: tuple[float, Obj] | None = None
        for v in sh.products:
            o = self.ctx.memoria.world.objs.get(v)
            if o is None or self.price(o, sh) > gold:
                continue
            # judged as wearing will judge it (by the name it will have in the bag), and not when one
            # with that name is already carried: else the shop sees an upgrade the bag never wears
            up = self._upgrade(o, eq) and self._upgrade_text(o.short, eq) if o.short not in self.bought else self._upgrade(o, eq)
            if up and o.short not in self.ctx.state.inventory:
                if best is None or up[2] > best[0]:
                    best = (up[2], o)
        if best:
            return best[1]
        if int(self._pol("drink_min", 0)) > 0 and not self._has_type("drinkcon"):     # D33: a water container of our own
            cons = [self.ctx.memoria.world.objs[v] for v in sh.products
                    if v in self.ctx.memoria.world.objs and self.ctx.memoria.world.objs[v].type == "drinkcon"
                    and self.price(self.ctx.memoria.world.objs[v], sh) <= gold]
            if cons:
                return max(cons, key=lambda o: (o.values[0], -o.cost))          # holds the most
        food_min = int(self._pol("food_min", 0))
        foods = [self.ctx.memoria.world.objs[v] for v in sh.products
                 if v in self.ctx.memoria.world.objs and self.ctx.memoria.world.objs[v].type == "food"]
        have = sum(1 for t in self.ctx.state.inventory if (self._obj(t) or Obj(0, [], "", "", "")).type == "food")
        if foods and have < food_min:
            affordable = [o for o in foods if self.price(o, sh) <= gold]
            if affordable:
                return max(affordable, key=lambda o: (o.values[0], -o.cost))     # most filling
        return None

    def _has_type(self, kind: str) -> bool:
        return any((self._obj(t) or Obj(0, [], "", "", "")).type == kind for t in self.ctx.state.inventory)

    def fountain(self) -> int | None:
        name = self._pol("fountain_room", "")
        vs = self.ctx.memoria.graph.rooms_named(name) if name else []
        return vs[0] if vs else None

    def water_want(self) -> bool:
        """D33: thirsty for a while (the clerics could not keep up), or nothing to carry water in."""
        st = self.ctx.state
        long_thirst = st.thirsty and self.ctx.clock() - st.thirsty_since >= float(self._pol("thirst_trip_s", 180))
        return long_thirst or (int(self._pol("drink_min", 0)) > 0 and not self._has_type("drinkcon"))

    def buy_here(self, reserve: int) -> str | None:
        """What to say after `buy`: a keyword no other product here has, else the item's number in this
        shop's list (`#N`, shop.c get_purchase_obj): 'plate' bought the bronze breast plate (840) and
        not the breast plate (216), and the keeper said "You can't afford it!" a hundred times.
        None if there is nothing to buy or the list is needed first (list_needed)."""
        sh = self.shop_here()
        o = self._buy_choice(sh, reserve) if sh else None
        if o is None:
            return None
        word = self._purchase_word(o, sh)
        if word is not None:
            self.bought[o.short] = o          # in the bag it is this one, not every object of its name
        return word

    def _purchase_word(self, o: Obj, sh: Shop) -> str | None:
        others = [self.ctx.memoria.world.objs[v] for v in sh.products
                  if v in self.ctx.memoria.world.objs and self.ctx.memoria.world.objs[v].short != o.short]
        for kw in o.keywords:
            if not any(kw.lower() in (k.lower() for k in x.keywords) for x in others):
                return kw
        st = self.ctx.state
        if st.shop_list and st.shop_list_room == st.room.get("name"):
            for i, it in enumerate(st.shop_list, 1):
                if str(it.get("text", "")).lower() == o.short.lower():
                    return f"#{i}"
        return None

    def list_needed(self, reserve: int) -> bool:
        """A purchase whose name is shared here and no list of this shop yet: ask for the list."""
        sh = self.shop_here()
        o = self._buy_choice(sh, reserve) if sh else None
        return o is not None and self._purchase_word(o, sh) is None

    # ------------------------------------------------------------ the party's shopping trip
    def shop_wants(self, keep: list[str], reserve: int, sell_at: int) -> list[int]:
        """Shop rooms worth a trip for me: enough to sell there, or something to buy. Cached."""
        now = self.ctx.clock()
        if now - self._wants[0] < WANTS_EVERY_S:
            return self._wants[1]
        start = self.ctx.memoria.locator(self.ctx.agent).vnum
        out: list[int] = []
        if start is not None and self.ctx.state.in_game:
            sell_total = len(self.sellable(None, keep))
            for room in self._near_shops(start):
                sh = self.shop_rooms()[room]
                if self._buy_choice(sh, reserve) is not None or (sell_total >= sell_at and self.sellable(sh, keep)):
                    out.append(room)
            f = self.fountain()
            if f is not None and self.water_want() and f not in out:
                out.append(f)                    # the fountain is a stop of the town run too
        self._wants = (now, out)
        return out

    def _near_shops(self, start: int) -> list[int]:
        """Shop rooms within SHOP_REACH steps on foot, by one breadth-first walk."""
        from collections import deque
        g = self.ctx.memoria.graph
        cond = self.ctx.memoria.conditions(has_light=self.ctx.state.has_light, agent=self.ctx.agent)
        shops = self.shop_rooms()
        dist, q, out = {start: 0}, deque([start]), []
        while q:
            v = q.popleft()
            if v in shops:
                out.append(v)
            if dist[v] >= SHOP_REACH:
                continue
            for to in g.exits_from(v, cond).values():
                if to not in dist:
                    dist[to] = dist[v] + 1
                    q.append(to)
        return out

    # ------------------------------------------------------------ picking up
    def pickup_item(self, min_cost: int) -> str | None:
        for o in self.ctx.state.room.get("objects", []):
            obj = self.k.ground_item(o.get("text", ""))
            if obj is None or "take" not in obj.wear:
                continue
            if obj.cost >= (min_cost or 0) or self._upgrade_text(obj.short) is not None or obj.type == "food":
                return self.k.item_keyword(obj.short)
        return None
