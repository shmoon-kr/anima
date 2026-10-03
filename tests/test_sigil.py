from pathlib import Path

import pytest
import yaml

from anima.sigil import api
from anima.sigil.expr import ExprError, evaluate, parse
from anima.sigil.program import Package, SigilError, build_program, load_package

FUNCS = {"min": min, "max": max, "contains": lambda h, n: n in h}


def ev(src, **names):
    return evaluate(parse(src), lambda n: names[n.replace(".", "_")], FUNCS)


# ---------------------------------------------------------------- expressions

def test_expression_precedence_and_logic():
    assert ev("1 + 2 * 3") == 7
    assert ev("not self.hungry and self.hp_pct >= 50", self_hungry=False, self_hp_pct=60) is True
    assert ev("'kobold' in room.occupants", room_occupants=["kobold"]) is True
    assert ev("x not in [1, 2]", x=3) is True
    assert ev("min(3, -x)", x=5) == -5
    assert ev("a / 0", a=1) == 0


@pytest.mark.parametrize("bad", [
    "__import__('os')",          # no attribute/index tricks
    "a[0]", "a = 1", "lambda: 1", "x.y()", "1 +", "(1", "f(1,", "'unterminated",
])
def test_expression_rejects_non_grammar(bad):
    with pytest.raises(ExprError):
        e = parse(bad)
        evaluate(e, lambda n: 0, {})


def test_expression_size_is_bounded():
    with pytest.raises(ExprError):
        parse(" + ".join(["1"] * 300))


# ---------------------------------------------------------------- packages

def pkg(name, **data):
    return Package(name=name, api=api.API_VERSION, data={"sigil": 0, "package": name, **data})


BASE = pkg("base",
           policies={"rest_pct": 50, "flee_pct": 30},
           behaviors={
               "rest": {"weight": 0.9, "considerations": ["inverse_linear(self.hp_pct, policy.rest_pct, 90)"],
                        "do": ["rest()"]},
               "hunt": {"weight": 0.7, "considerations": ["bool(pick_target(['kobold'], []))"],
                        "do": ["attack(pick_target(['kobold'], []))"]},
           },
           reflexes={"wake_when_hit": {"on": "combat.hit", "if": "event.victim == 'self' and self.position == 'sleeping'",
                                       "do": ["stand()"]}})


def test_layers_merge_and_record_origin():
    cls = pkg("class-x", policies={"rest_pct": 30}, behaviors={"hunt": {"weight": 0.8}})
    prog = build_program("Vallen", [BASE, cls], {"policies": {"flee_pct": 45}})
    assert prog.policies == {"rest_pct": 30, "flee_pct": 45}
    assert prog.policy_origin == {"rest_pct": "class-x", "flee_pct": "agent:Vallen"}
    hunt = prog.behaviors["hunt"]
    assert hunt.spec["weight"] == 0.8 and hunt.spec["do"] == ["attack(pick_target(['kobold'], []))"]
    assert hunt.id == "class-x/hunt"
    assert prog.behaviors["rest"].id == "base/rest"
    assert "class-x" in prog.explain()


def test_disable_and_replace():
    over = pkg("mine", behaviors={"rest": {"disabled": True},
                                  "hunt": {"replace": True, "considerations": ["1"], "do": ["explore()"]}})
    prog = build_program("A", [BASE, over])
    assert "rest" not in prog.behaviors
    assert prog.behaviors["hunt"].spec == {"considerations": ["1"], "do": ["explore()"]}


@pytest.mark.parametrize("bad,needle", [
    ({"behaviors": {"b": {"considerations": ["self.nope"], "do": ["rest()"]}}}, "unknown name 'self.nope'"),
    ({"behaviors": {"b": {"considerations": ["policy.missing"], "do": ["rest()"]}}}, "undeclared policy"),
    ({"behaviors": {"b": {"considerations": ["attack('x')"], "do": ["rest()"]}}}, "only allowed in `do`"),
    ({"behaviors": {"b": {"considerations": ["1"], "do": ["send('quit')"]}}}, "forbidden command"),
    ({"behaviors": {"b": {"considerations": ["1"], "do": ["send('say hi; quit')"]}}}, "forbidden command"),
    ({"behaviors": {"b": {"considerations": ["1"], "do": ["send('#alias x')"]}}}, "forbidden command"),
    ({"behaviors": {"b": {"considerations": ["1"], "do": ["send('buy #2;quit')"]}}}, "forbidden command"),
    ({"behaviors": {"b": {"considerations": ["1"], "do": ["self.hp"]}}}, "must be action calls"),
    ({"behaviors": {"b": {"considerations": ["1"], "do": ["rest()"], "task": "t"}}}, "exactly one"),
    ({"behaviors": {"b": {"considerations": ["event.victim"], "do": ["rest()"]}}}, "`event.*` is not available"),
    ({"behaviors": {"b": {"considerations": ["nosuch(1)"], "do": ["rest()"]}}}, "unknown function"),
    ({"behaviors": {"b": {"considerations": ["linear(1)"], "do": ["rest()"]}}}, "takes 3 arguments"),
    ({"reflexes": {"r": {"if": "true", "do": ["flee()"]}}}, "needs `on`"),
    ({"reflexes": {"r": {"on": "prompt", "do": ["flee()"], "priority": 2}}}, "priority must be"),
    ({"tasks": {"t": {"steps": [{"fly": 1}]}}}, "a step needs one of"),
    ({"asks": {"a": {"when": "true", "question": "q", "tier": "gpt", "timeout_s": 5, "default": True,
                     "schema": {"type": "boolean"}}}}, "tier must be one of"),
    ({"asks": {"a": {"when": "true", "question": "q", "tier": "claude", "timeout_s": 5,
                     "schema": {"type": "boolean"}}}}, "default is required"),
])
def test_validator_rejects(bad, needle):
    with pytest.raises(SigilError) as ei:
        build_program("A", [BASE, pkg("bad", **bad)])
    assert any(needle in e for e in ei.value.errors), ei.value.errors


def test_package_api_version_mismatch_is_rejected(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "a.yaml").write_text(yaml.safe_dump({"sigil": 99, "package": "p"}))
    with pytest.raises(SigilError, match="needs Sigil API v99"):
        load_package(d)


def test_package_from_files_merges_and_rejects_duplicates(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    (d / "a.yaml").write_text(yaml.safe_dump({"sigil": 0, "package": "p", "policies": {"x": 1}}))
    (d / "b.yaml").write_text(yaml.safe_dump({"sigil": 0, "package": "p", "policies": {"y": 2}}))
    assert load_package(d).data["policies"] == {"x": 1, "y": 2}
    (d / "c.yaml").write_text(yaml.safe_dump({"sigil": 0, "package": "p", "policies": {"x": 3}}))
    with pytest.raises(SigilError, match="defined twice"):
        load_package(d)


def test_api_doc_is_generated_from_registry():
    doc_path = Path(__file__).parents[1] / "docs" / "SIGIL-API.md"
    assert doc_path.read_text(encoding="utf-8") == api.generate_markdown(), \
        "docs/SIGIL-API.md is stale: run `anima sigil api-doc`"


def test_an_item_number_after_the_command_word_is_allowed():
    from anima.sigil import api
    assert not api.forbidden_text("buy #3") and not api.forbidden_text("get #12 corpse")
    assert api.forbidden_text("#3") and api.forbidden_text("say #hi") and api.forbidden_text("buy #3x")
