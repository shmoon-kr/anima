"""Text adapter tests. Fixtures are unmodified excerpts of mud-agents tintin logs."""
from pathlib import Path

import pytest

from anima.adapters.tbamud_text.act import compile_template
from anima.adapters.tbamud_text.ansi import AnsiState
from anima.adapters.tbamud_text.parser import TbamudTextAdapter
from anima.adapters.tbamud_text.replay import replay_file

FIX = Path(__file__).parents[1] / "third_party" / "tbamud" / "fixtures"     # excerpts of real play logs
ESC = "\x1b"


def events(name: str) -> list:
    return [e for e in replay_file(FIX / name, "X", keep_raw=False) if e.type != "prompt"]


def types(evs) -> list[str]:
    return [e.type for e in evs]


def first(evs, type_):
    return next(e for e in evs if e.type == type_)


# ---------------------------------------------------------------- building blocks

def test_ansi_colour_carries_across_lines():
    st = AnsiState()
    st.parse(f"{ESC}[0;33mA cityguard stands here.")
    nxt = st.parse("still yellow")
    assert nxt.first_color.fg == "yellow"
    assert st.parse(f"{ESC}[0mplain").first_color.fg is None


def test_act_template_capitalises_first_letter_and_pronoun():
    c = compile_template("$E is already awake.")
    assert c.regex.match("She is already awake.")
    c = compile_template("$n tells you, '%s'")
    m = c.regex.match("Lil tells you, 'food'")
    assert m and m.group("n") == "Lil" and m.group("s0") == "food"


def test_act_template_backreference_for_repeated_name():
    c = compile_template("$n hits $N, and $N cries.")
    assert c.regex.match("Lil hits the rat, and the rat cries.")
    assert not c.regex.match("Lil hits the rat, and the dog cries.")


# ---------------------------------------------------------------- live stream

def test_prompt_without_newline_closes_block():
    a = TbamudTextAdapter("Vallen")
    evs = a.feed("You are hungry.\r\n26H 100M 85V (news) (motd) > ")
    assert types(evs) == ["condition", "prompt"]
    assert evs[1].data == {"hp": 26, "mp": 100, "mv": 85}


def test_reply_glued_after_prompt():
    a = TbamudTextAdapter("Vallen")
    evs = a.feed("26H 100M 85V > You stop resting, and stand up.\r\n27H 100M 85V > ")
    assert types(evs) == ["prompt", "position", "prompt"]
    assert evs[1].data == {"position": "standing"}


def test_negative_hp_prompt():
    a = TbamudTextAdapter("Vallen")
    assert a.feed("-6H 100M 83V (news) (motd) > ")[0].data["hp"] == -6


def test_login_prompts_without_newline_emit_immediately():
    a = TbamudTextAdapter("Vallen")
    evs = a.feed("Attempting to Detect Client, Please Wait...\r\n")
    assert evs == []
    evs = a.feed("\r\n   T B A M U D\r\nBy what name do you wish to be known? ")
    assert [(e.type, e.data) for e in evs] == [("connection.login_prompt", {"stage": "name"})]
    evs = a.feed("Password: ")
    assert evs[-1].data == {"stage": "password"}


def test_seq_is_monotonic_and_agent_set():
    a = TbamudTextAdapter("Lil")
    evs = a.feed("You are hungry.\r\nYou are thirsty.\r\n10H 1M 1V > ")
    assert [e.seq for e in evs] == [1, 2, 3]
    assert {e.agent for e in evs} == {"Lil"}


# ---------------------------------------------------------------- golden fixtures

def test_room_block():
    evs = events("room_temple.log")
    room = first(evs, "room")
    d = room.data
    assert d["name"] == "The Temple Of Midgaard"
    assert d["desc"].startswith("   You are in the southern end of the temple hall")
    assert [x["dir"] for x in d["exits"]] == ["north", "east", "south", "west", "down"]
    assert d["objects"] == [{"text": "An automatic teller machine has been installed in the wall here.", "count": 1}]
    occ = d["occupants"]
    assert occ[0]["text"] == "Lumina the Believer is standing here."
    assert occ[0]["position"] == "standing"
    assert occ[1]["text"] == "Lil the Apprentice of Magic is standing here."
    assert "my_group" in occ[1]["hints"]
    assert first(evs, "condition").data == {"hungry": True}


def test_combat_round_and_kill():
    evs = events("combat.log")
    hits = [e.data for e in evs if e.type == "combat.hit"]
    assert {"attacker": "Lil", "victim": "the janitor", "verb": "magic missile", "severity": 4, "kind": "spell"} in hits
    # weapon death blow comes from lib/misc/messages (hit type), severity 8
    assert {"attacker": "self", "victim": "the janitor", "verb": "hit", "severity": 8, "kind": "weapon"} in hits
    assert first(evs, "combat.death").data == {"who": "the janitor"}
    assert first(evs, "exp.gain").data == {"amount": 9, "kind": "share"}


def test_death_then_menu_then_reentry():
    evs = events("death.log")
    t = types(evs)
    assert "combat.condition" in t
    i = t.index("self.died")
    assert t[i + 1:i + 4] == ["connection.login_prompt", "connection.in_game", "room"]
    killer = [e.data for e in evs if e.type == "combat.hit"][-1]
    assert killer == {"attacker": "the green gelatinous blob", "victim": "self", "verb": "crush", "severity": 8,
                      "kind": "weapon"}


def test_flee_room_comes_before_flee_message():
    evs = events("flee.log")
    t = types(evs)
    assert t.index("room") < t.index("self.fled")
    room = evs[t.index("room")]
    assert room.data["name"] == "Main Street"
    assert room.data["occupants"][0]["hints"] == ["my_group_leader"]
    assert first(evs, "combat.flee_seen").data == {"who": "Vallen"}


def test_group_table_and_group_prefix():
    evs = events("group.log")
    members = first(evs, "group.status").data["members"]
    assert members[0] == {"name": "Vallen", "hp": 20, "hp_max": 39, "mp": 100, "mp_max": 100,
                          "mv": 38, "mv_max": 88, "leader": True}
    assert [m["leader"] for m in members[1:]] == [False] * (len(members) - 1)
    evs = events("exits_dark.log")
    assert first(evs, "group.change").data == {"event": "joined", "who": "Lumina"}


def test_dark_and_exits_list():
    evs = events("exits_dark.log")
    assert "room.dark" in types(evs)
    ex = first(evs, "room.exits_listed").data["exits"]
    assert ex == [{"dir": "north", "room_name": None, "closed": False},
                  {"dir": "east", "room_name": None, "closed": False},
                  {"dir": "west", "room_name": None, "closed": False}]


def test_login_sequence():
    evs = events("login.log")
    stages = [e.data["stage"] for e in evs if e.type == "connection.login_prompt"]
    assert stages == ["name", "password", "press_return", "menu"]
    assert first(evs, "connection.in_game").data == {"how": "entered"}
    assert "unknown" not in types(evs)


def test_equipment_and_practice_and_shop():
    slots = first(events("equipment.log"), "items.equipment").data["slots"]
    assert slots[0] == {"slot": "used as light", "text": "a candle"}
    evs = events("practice.log")
    assert first(evs, "char.skills").data == {"skills": {"magic missile": "not learned"}}
    assert {"practices": 2} in [e.data for e in evs if e.type == "char.score"]
    items = first(events("shop.log"), "shop.list").data["items"]
    assert items[0] == {"text": "A danish pastry", "price": 6}


def test_level_up():
    evs = events("levelup.log")
    assert first(evs, "level.up").data == {"levels": 1}
    assert first(evs, "skill.result").data == {"skill": "kick", "ok": False, "reason": "no_target"}


def test_tell_and_gtell():
    a = TbamudTextAdapter("Vallen")
    evs = a.feed(f"{ESC}[0;31mLil tells you, 'water'{ESC}[0m\r\n"
                 f"{ESC}[0;32m[{ESC}[1;32mGroup{ESC}[0;32m]{ESC}[0m {ESC}[0;32mVallen says, 'hangul'{ESC}[0m\r\n10H 1M 1V > ")
    assert [(e.type, e.data) for e in evs[:2]] == [
        ("comm.tell", {"from": "Lil", "to": "self", "text": "water", "direction": "in"}),
        ("comm.gtell", {"from": "Vallen", "text": "hangul", "direction": "in"}),
    ]


# ---------------------------------------------------------------- whole logs (coverage)

LOGS = Path(__file__).parents[2] / "mud-agents" / "logs"


@pytest.mark.skipif(not (LOGS / "Vallen.log").exists(), reason="mud-agents logs not present")
@pytest.mark.slow
def test_full_log_unknown_ratio_below_one_percent():
    from anima.adapters.tbamud_text.replay import stats
    st = stats(replay_file(LOGS / "Vallen.log", keep_raw=False))
    assert st["unknown_ratio"] < 0.01
    assert st["types"]["room"] > 10000


def _documented_types() -> set[str]:
    import re
    doc = (Path(__file__).parents[1] / "docs" / "PROTOCOL.md").read_text()
    return set(re.findall(r"^\| `([a-z_]+(?:\.[a-z_]+)*)` \|", doc, re.M))


@pytest.mark.skipif(not (LOGS / "Lil.log").exists(), reason="mud-agents logs not present")
@pytest.mark.slow
def test_every_emitted_type_is_in_protocol_doc():
    seen: set[str] = set()
    for name in ("Lil.log", "Lumina.log"):
        seen |= {e.type for e in replay_file(LOGS / name, keep_raw=False)}
    missing = seen - _documented_types()
    assert not missing, f"PROTOCOL.md 에 없는 이벤트: {missing}"
