"""Animus knobs: the values an LLM may change, as packages declare them (PHASE-2-PLAN S1, D27).

A package declares policy knobs under a top-level `animus:` section, and opts a behavior's
weight in with `animus_weight: [min, max]` on the behavior. Nothing else is changeable.

    animus:
      policies:
        flee_pct:  {type: number, min: 15, max: 50, step: 10}   # step: largest change per patch
        targets:   {type: words, max_len: 40}
        skill_plan: {type: plan, max_len: 20}
        rally:     {type: room, party: true}                   # party: only the party layer may set it
        circuit:   {type: rooms, party: true, max_len: 4}
        danger:    {type: words, grow_only: true}              # may add to the package's list, never remove
"""
from __future__ import annotations

from typing import Any, Callable

KNOB_TYPES = ("number", "words", "plan", "room", "rooms")
KNOB_KEYS = {"type", "min", "max", "step", "max_len", "party", "grow_only", "description"}


def check_spec(name: str, spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return [f"animus.policies.{name}: must be a mapping"]
    errs = []
    extra = set(spec) - KNOB_KEYS
    if extra:
        errs.append(f"animus.policies.{name}: unknown keys {sorted(extra)}")
    t = spec.get("type")
    if t not in KNOB_TYPES:
        errs.append(f"animus.policies.{name}: type must be one of {KNOB_TYPES}")
    if t == "number":
        lo, hi = spec.get("min"), spec.get("max")
        if not all(isinstance(x, (int, float)) for x in (lo, hi)) or lo > hi:
            errs.append(f"animus.policies.{name}: number needs min <= max")
    return errs


def check_weight_spec(where: str, spec: Any) -> list[str]:
    if (not isinstance(spec, list) or len(spec) != 2 or not all(isinstance(x, (int, float)) for x in spec)
            or spec[0] > spec[1] or spec[0] < 0):
        return [f"{where}: animus_weight must be [min, max] with 0 <= min <= max"]
    return []


def check_value(spec: dict[str, Any], value: Any, old: Any = None,
                is_room: Callable[[str], bool] = lambda _: True, base: Any = None) -> str | None:
    """None if `value` is allowed for this knob, else why not. `old` enables the step limit,
    `base` (the value without any Animus layer) the grow-only rule."""
    t = spec["type"]
    if spec.get("grow_only") and isinstance(base, list) and isinstance(value, list):
        missing = [w for w in base if w not in value]
        if missing:
            return f"may only add to this list, not remove {missing}"
    max_len = spec.get("max_len", 40)
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "not a number"
        if not spec["min"] <= value <= spec["max"]:
            return f"out of range {spec['min']}..{spec['max']}"
        step = spec.get("step")
        if step is not None and isinstance(old, (int, float)) and abs(value - old) > step:
            return f"changes by more than {step} at once"
        return None
    if t == "room":
        return None if isinstance(value, str) and is_room(value) else "not a known room name"
    if not isinstance(value, list) or len(value) > max_len:
        return f"must be a list of at most {max_len}"
    if t == "words":
        ok = all(isinstance(w, str) and w and len(w) <= 40 and w.replace(" ", "").replace("-", "").isalnum()
                 for w in value)
        return None if ok else "words must be short alphanumeric strings"
    if t == "rooms":
        return None if all(isinstance(r, str) and is_room(r) for r in value) and value else "not known room names"
    if t == "plan":
        for e in value:
            if (not isinstance(e, dict) or set(e) - {"skill", "target", "attack"} or not isinstance(e.get("skill"), str)
                    or not isinstance(e.get("target"), int) or not 0 <= e["target"] <= 8):
                return "plan entries are {skill, target 0..8, attack?}"
        return None
    return "unknown knob type"


def check_weight(rng: list[float], value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "not a number"
    return None if rng[0] <= value <= rng[1] else f"out of range {rng[0]}..{rng[1]}"
