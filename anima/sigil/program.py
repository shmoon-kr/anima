"""Sigil packages, layering and validation (D12, D19).

A package is a directory with one or more `*.yaml` files:

    sigil: 0                    # API version the package needs
    package: my-package
    policies:  {name: value}
    behaviors: {name: {weight, when?, considerations: [expr], targets?, do?: [action], task?, every_s?}}
    reflexes:  {name: {on: type | [types], if?: expr, do: [action], cooldown_s?}}
    tasks:     {name: {timeout_s?, steps: [step]}}
    asks:      {name: {when: expr, question, context?: {k: expr}, tier, priority, timeout_s, default, schema}}
    animus:    {policies: {name: knob}}   values an LLM may change (anima/sigil/knobs.py, D27)

An agent manifest stacks packages (`layers`) and may override policies and items.
Later layers merge into earlier ones key by key; `replace: true` replaces an item, `disabled: true`
switches it off. Every merged item remembers which layer it came from (for `explain` and for
the `source.id` of commands, D11).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from anima.sigil import api
from anima.sigil.expr import Expr, ExprError, parse
from anima.sigil.knobs import check_spec, check_value, check_weight_spec

SECTIONS = ("behaviors", "reflexes", "tasks", "asks")
STEP_KINDS = ("do", "go_to", "wait_until", "wait", "repeat", "go_back")
TIERS = ("local_fast", "local_think", "claude")
PRIORITIES = ("danger", "stuck", "routine")
ANSWER_TYPES = ("boolean", "number", "string", "enum")


class _Loader(yaml.SafeLoader):
    """YAML 1.2 booleans: only true/false. YAML 1.1 would turn the key `on:` (reflexes) into True."""


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool",
                              __import__("re").compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def yaml_load(text: str) -> Any:
    return yaml.load(text, Loader=_Loader)


class SigilError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass
class Package:
    name: str
    api: int
    data: dict[str, Any]
    path: Path | None = None


def load_package(path: Path) -> Package:
    errors: list[str] = []
    merged: dict[str, Any] = {}
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    if not files:
        raise SigilError([f"{path}: no yaml files"])
    for f in files:
        try:
            doc = yaml_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append(f"{f.name}: YAML: {e}")
            continue
        if not isinstance(doc, dict):
            errors.append(f"{f.name}: top level must be a mapping")
            continue
        for k, v in doc.items():
            if k in SECTIONS or k in ("policies", "animus"):
                if not isinstance(v, dict):
                    errors.append(f"{f.name}: {k} must be a mapping")
                    continue
                for name in v:
                    if name in merged.get(k, {}):
                        errors.append(f"{f.name}: {k}.{name} defined twice in package")
                merged.setdefault(k, {}).update(v)
            elif k in ("sigil", "package", "version", "requires", "description"):
                if k in merged and merged[k] != v:
                    errors.append(f"{f.name}: {k} differs between files")
                merged[k] = v
            else:
                errors.append(f"{f.name}: unknown top-level key {k!r}")
    name = merged.get("package") or path.name
    want = merged.get("sigil")
    if want is None:
        errors.append(f"{name}: missing `sigil:` (API version)")
    elif want != api.API_VERSION:
        errors.append(f"{name}: needs Sigil API v{want}, engine has v{api.API_VERSION}")
    if errors:
        raise SigilError(errors)
    return Package(name=name, api=want, data=merged, path=path)


# ---------------------------------------------------------------- merged program

@dataclass
class Item:
    kind: str             # behavior | reflex | task | ask
    name: str
    layer: str            # package (or agent manifest) that last defined it
    spec: dict[str, Any]
    exprs: dict[str, Expr] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return f"{self.layer}/{self.name}"


@dataclass
class Program:
    agent: str
    layers: list[str]
    policies: dict[str, Any]
    policy_origin: dict[str, str]
    behaviors: dict[str, Item]
    reflexes: dict[str, Item]
    tasks: dict[str, Item]
    asks: dict[str, Item]
    disabled: list[str]
    knobs: dict[str, dict[str, Any]] = field(default_factory=dict)       # policy -> knob spec
    knob_origin: dict[str, str] = field(default_factory=dict)

    def weight_knobs(self) -> dict[str, list[float]]:
        return {n: it.spec["animus_weight"] for n, it in self.behaviors.items() if "animus_weight" in it.spec}

    def explain(self) -> str:
        out = [f"agent {self.agent}: layers {' → '.join(self.layers)}", "policies:"]
        out += [f"  {k} = {v!r}   ({self.policy_origin[k]})" for k, v in sorted(self.policies.items())]
        for title, items in (("behaviors", self.behaviors), ("reflexes", self.reflexes),
                             ("tasks", self.tasks), ("asks", self.asks)):
            out.append(f"{title}:")
            out += [f"  {it.name}   ({it.layer})" for it in items.values()]
        if self.disabled:
            out.append("disabled: " + ", ".join(self.disabled))
        return "\n".join(out)


def build_program(agent: str, packages: list[Package], manifest: dict[str, Any] | None = None,
                  extra: list[tuple[str, dict[str, Any]]] = ()) -> Program:
    """Stack packages in order, apply the manifest's overrides, then any `extra` layers (the Animus
    overrides, `animus:party` then `animus:NAME`), then validate everything."""
    policies: dict[str, Any] = {}
    origin: dict[str, str] = {}
    items: dict[str, dict[str, Item]] = {s: {} for s in SECTIONS}
    disabled: list[str] = []
    layers = [p.name for p in packages]
    sources: list[tuple[str, dict[str, Any]]] = [(p.name, p.data) for p in packages]
    if manifest:
        sources.append((f"agent:{agent}", manifest))
        layers.append(f"agent:{agent}")
    for layer, data in extra:
        sources.append((layer, data))
        layers.append(layer)
    knobs: dict[str, dict[str, Any]] = {}
    knob_origin: dict[str, str] = {}
    for layer, data in sources:
        if not layer.startswith("animus:"):          # an LLM never declares what it may change
            for k, v in ((data.get("animus") or {}).get("policies") or {}).items():
                knobs[k] = v
                knob_origin[k] = layer
        for k, v in (data.get("policies") or {}).items():
            policies[k] = v
            origin[k] = layer
        for section in SECTIONS:
            for name, spec in (data.get(section) or {}).items():
                spec = spec or {}
                if not isinstance(spec, dict):
                    raise SigilError([f"{layer}: {section}.{name} must be a mapping"])
                kind = section[:-1]
                cur = items[section].get(name)
                if spec.get("disabled"):
                    if name in items[section]:
                        del items[section][name]
                    disabled.append(f"{section}.{name} ({layer})")
                    continue
                if cur is None or spec.get("replace"):
                    merged = {k: v for k, v in spec.items() if k != "replace"}
                else:
                    merged = copy.deepcopy(cur.spec)
                    merged.update(spec)
                items[section][name] = Item(kind, name, layer, merged)
    prog = Program(agent, layers, policies, origin, items["behaviors"], items["reflexes"],
                   items["tasks"], items["asks"], disabled, knobs, knob_origin)
    errors = validate(prog)
    if errors:
        raise SigilError(errors)
    return prog


# ---------------------------------------------------------------- validation

def validate(prog: Program) -> list[str]:
    v = _Validator(prog)
    for it in prog.behaviors.values():
        v.behavior(it)
    for it in prog.reflexes.values():
        v.reflex(it)
    for it in prog.tasks.values():
        v.task(it)
    for it in prog.asks.values():
        v.ask(it)
    v.knobs()
    return v.errors


class _Validator:
    def __init__(self, prog: Program) -> None:
        self.prog = prog
        self.errors: list[str] = []

    def err(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def expr(self, where: str, src: Any, *, actions: bool, scope: set[str]) -> Expr | None:
        try:
            e = parse(src)
        except ExprError as ex:
            self.err(where, str(ex))
            return None
        for name in e.names():
            self.name(where, name, scope)
        for fn, n in e.calls():
            f = api.FUNCS_BY_NAME.get(fn)
            if f is None:
                self.err(where, f"unknown function {fn}()")
                continue
            if f.action and not actions:
                self.err(where, f"action {fn}() is only allowed in `do`")
            if f.variadic:
                if n > len(f.params):
                    self.err(where, f"{fn}() takes at most {len(f.params)} arguments")
            elif n != len(f.params):
                self.err(where, f"{fn}() takes {len(f.params)} arguments, got {n}")
        if actions:
            if e.ast[0] != "call" or not api.FUNCS_BY_NAME.get(e.ast[1], api.Func("", (), "")).action:
                self.err(where, f"`do` entries must be action calls: {e.src!r}")
            self._check_send(where, e)
        return e

    def _check_send(self, where: str, e: Expr) -> None:
        if e.ast[0] == "call" and e.ast[1] == "send" and e.ast[2] and e.ast[2][0][0] == "lit":
            text = str(e.ast[2][0][1])
            word = text.strip().split(" ", 1)[0].lower() if text.strip() else ""
            if word in api.FORBIDDEN_COMMANDS or set(text) & api.FORBIDDEN_CHARS:
                self.err(where, f"forbidden command {text!r}")

    def name(self, where: str, name: str, scope: set[str]) -> None:
        root, _, rest = name.partition(".")
        if name in api.STATE_PATHS and root != "target":
            return
        if root == "policy":
            if rest not in self.prog.policies:
                self.err(where, f"undeclared policy {rest!r}")
            return
        if root == "answer":
            if rest not in self.prog.asks:
                self.err(where, f"no ask named {rest!r}")
            return
        if root in ("event", "target"):
            if root not in scope:
                self.err(where, f"`{root}.*` is not available here")
            elif root == "target" and name not in api.STATE_PATHS:
                self.err(where, f"unknown target field {name!r}")
            return
        self.err(where, f"unknown name {name!r}")

    def exprs(self, it: Item, key: str, srcs: Any, *, actions: bool, scope: set[str]) -> None:
        if srcs is None:
            return
        if not isinstance(srcs, list):
            srcs = [srcs]
        for i, s in enumerate(srcs):
            e = self.expr(f"{it.id}.{key}[{i}]", s, actions=actions, scope=scope)
            if e is not None:
                it.exprs[f"{key}[{i}]"] = e

    def behavior(self, it: Item) -> None:
        s = it.spec
        self.allowed(it, {"weight", "when", "considerations", "targets", "do", "task", "every_s", "cooldown_s",
                          "retry_s", "delay_s", "description", "animus_weight"})
        if "animus_weight" in s:
            for e in check_weight_spec(it.id, s["animus_weight"]):
                self.err(it.id, e)
        scope = {"target"} if "targets" in s else set()
        if not isinstance(s.get("weight", 1.0), (int, float)):
            self.err(it.id, "weight must be a number")
        if ("do" in s) == ("task" in s):
            self.err(it.id, "needs exactly one of `do` or `task`")
        if "task" in s and s["task"] not in self.prog.tasks:
            self.err(it.id, f"unknown task {s['task']!r}")
        if not s.get("considerations"):
            self.err(it.id, "needs at least one consideration")
        self.exprs(it, "when", s.get("when"), actions=False, scope=set())
        self.exprs(it, "targets", s.get("targets"), actions=False, scope=set())
        self.exprs(it, "considerations", s.get("considerations"), actions=False, scope=scope)
        self.exprs(it, "do", s.get("do"), actions=True, scope=scope)

    def reflex(self, it: Item) -> None:
        s = it.spec
        self.allowed(it, {"on", "if", "do", "cooldown_s", "description"})
        if not s.get("on"):
            self.err(it.id, "needs `on` (event type)")
        if not s.get("do"):
            self.err(it.id, "needs `do`")
        self.exprs(it, "if", s.get("if"), actions=False, scope={"event"})
        self.exprs(it, "do", s.get("do"), actions=True, scope={"event"})

    def task(self, it: Item) -> None:
        s = it.spec
        self.allowed(it, {"steps", "timeout_s", "description"})
        steps = s.get("steps")
        if not isinstance(steps, list) or not steps:
            self.err(it.id, "needs a non-empty `steps` list")
            return
        for i, st in enumerate(steps):
            self.step(it, f"steps[{i}]", st)

    def step(self, it: Item, key: str, st: Any) -> None:
        if not isinstance(st, dict) or not (set(st) & set(STEP_KINDS)):
            self.err(f"{it.id}.{key}", f"a step needs one of {', '.join(STEP_KINDS)}")
            return
        kind = next(k for k in STEP_KINDS if k in st)
        extra = set(st) - {kind, "timeout_s", "description"}
        if extra:
            self.err(f"{it.id}.{key}", f"unknown keys {sorted(extra)}")
        if kind == "do":
            self.exprs(it, f"{key}.do", st["do"], actions=True, scope=set())
        elif kind in ("go_to", "wait_until"):
            self.exprs(it, f"{key}.{kind}", st[kind], actions=False, scope=set())
        elif kind == "wait" and not isinstance(st["wait"], (int, float)):
            self.err(f"{it.id}.{key}", "wait needs seconds")
        elif kind == "repeat":
            r = st["repeat"]
            if not isinstance(r, dict) or "do" not in r:
                self.err(f"{it.id}.{key}", "repeat needs `do`")
                return
            self.exprs(it, f"{key}.repeat.do", r["do"], actions=True, scope=set())
            self.exprs(it, f"{key}.repeat.while", r.get("while", True), actions=False, scope=set())

    def ask(self, it: Item) -> None:
        s = it.spec
        self.allowed(it, {"when", "question", "context", "tier", "priority", "timeout_s", "default", "schema",
                          "description"})
        if s.get("tier") not in TIERS:
            self.err(it.id, f"tier must be one of {TIERS}")
        if s.get("priority", "routine") not in PRIORITIES:
            self.err(it.id, f"priority must be one of {PRIORITIES}")
        if not isinstance(s.get("timeout_s"), (int, float)):
            self.err(it.id, "timeout_s (seconds) is required")
        if "default" not in s:
            self.err(it.id, "default is required (used until an answer arrives)")
        schema = s.get("schema") or {}
        if schema.get("type") not in ANSWER_TYPES:
            self.err(it.id, f"schema.type must be one of {ANSWER_TYPES}")
        if not isinstance(s.get("question"), str) or not s["question"].strip():
            self.err(it.id, "question text is required")
        self.exprs(it, "when", s.get("when"), actions=False, scope=set())
        for k, src in (s.get("context") or {}).items():
            e = self.expr(f"{it.id}.context.{k}", src, actions=False, scope=set())
            if e is not None:
                it.exprs[f"context.{k}"] = e

    def knobs(self) -> None:
        p = self.prog
        for name, spec in p.knobs.items():
            for e in check_spec(name, spec):
                self.errors.append(f"{p.knob_origin[name]}: {e}")
            if name not in p.policies:
                self.errors.append(f"{p.knob_origin[name]}: animus.policies.{name}: no such policy")
        for name, layer in p.policy_origin.items():   # values an Animus layer set must be declared knobs
            if not layer.startswith("animus:"):
                continue
            spec = p.knobs.get(name)
            why = "not an Animus knob" if spec is None or check_spec(name, spec) else check_value(spec, p.policies[name])
            if why:
                self.errors.append(f"{layer}: policy {name}: {why}")
        for it in p.behaviors.values():               # an Animus layer may only touch an opted-in weight
            if it.layer.startswith("animus:"):
                rng = it.spec.get("animus_weight")
                w = it.spec.get("weight", 1.0)
                if rng is None:
                    self.errors.append(f"{it.id}: weight is not an Animus knob (no animus_weight)")
                elif not check_weight_spec(it.id, rng) and not rng[0] <= w <= rng[1]:
                    self.errors.append(f"{it.id}: weight {w} out of range {rng[0]}..{rng[1]}")

    def allowed(self, it: Item, keys: set[str]) -> None:
        extra = set(it.spec) - keys
        if extra:
            self.err(it.id, f"unknown keys {sorted(extra)}")


# ---------------------------------------------------------------- manifests

def load_agent(manifest_path: Path, packages_dir: Path, extra: list[tuple[str, dict[str, Any]]] = ()) -> Program:
    """agents/NAME.yaml: {agent, layers: [package...], policies?, behaviors?, ...}; `extra`: Animus layers"""
    m = yaml_load(manifest_path.read_text(encoding="utf-8")) or {}
    agent = m.get("agent") or manifest_path.stem
    pkgs = [load_package(packages_dir / name) for name in m.get("layers", [])]
    overrides = {k: v for k, v in m.items() if k in SECTIONS or k == "policies"}
    return build_program(agent, pkgs, overrides, extra)
