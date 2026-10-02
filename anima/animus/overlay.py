"""Animus override layers: the only place an LLM's decisions take effect (PHASE-2-PLAN S1).

Two kinds of layer sit on top of every agent's package stack (D12):
`animus:party` (the strategist, every agent) and `animus:NAME` (that agent's local LLM).
They hold only knob values the packages declared (anima/sigil/knobs.py, D27).

A patch is checked (knob, range, step, grow-only, lock, hold time, hourly cap), then every
affected agent's program is rebuilt with the new layers and validated as a whole. Only if all
pass are the new values hot-applied (policies and weights; tasks and the current behavior stay).

Safety rules:
- D26: a key the strategist (or a person) set, in the party layer or an agent's layer, is locked for that
  agent's local LLM while it holds (ttl, else MIN_HOLD_S)
- D28: when an agent dies, patches applied in the DEATH_WINDOW_S before are reverted, and remembered
  so the strategist hears "patch X, then a death N minutes later"
- `off()` drops the layers' effect (they are kept on disk) and refuses new patches until `on()`

Every change is published as `runtime.animus` and kept in `history`.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from anima.sigil.knobs import check_value, check_weight
from anima.sigil.program import Program, SigilError

PARTY = "party"
MIN_HOLD_S = 600.0
MAX_CHANGES_PER_HOUR = 12          # per layer
DEATH_WINDOW_S = 1800.0
ORIGINS = ("strategist", "local", "human")

_ids = itertools.count(1)


@dataclass
class Entry:
    value: Any
    patch_id: str
    t: float
    ttl_s: float | None = None
    reason: str = ""
    origin: str = "human"

    def holds_until(self) -> float:
        return self.t + (self.ttl_s if self.ttl_s else MIN_HOLD_S)


@dataclass
class Result:
    ok: bool
    patch_id: str
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Overlay:
    agents: list[str]
    base: Callable[[str], Program]                                  # program without Animus layers
    rebuild: Callable[[str, list[tuple[str, dict[str, Any]]]], Program]   # raises SigilError
    apply: Callable[[str, Program], None]                           # hot-apply values into the runtime
    publish: Callable[[str, str, dict[str, Any]], None]             # (agent, type, data)
    clock: Callable[[], float]
    is_room: Callable[[str], bool] = lambda _: True
    apply_party: Callable[[str, Any], None] = lambda key, value: None   # party knobs that live on the board
    leader: str | None = None
    save_path: Path | None = None
    layers: dict[str, dict[str, Entry]] = field(default_factory=dict)     # PARTY | agent -> key -> entry
    history: list[dict[str, Any]] = field(default_factory=list)
    deaths: list[dict[str, Any]] = field(default_factory=list)            # for the strategist
    enabled: bool = True

    # ------------------------------------------------------------ keys
    # "policy.NAME" | "weight.BEHAVIOR"
    def _knob(self, agent: str, key: str) -> tuple[str, Any] | None:
        kind, _, name = key.partition(".")
        prog = self.base(agent)
        if kind == "policy" and name in prog.knobs:
            return "policy", prog.knobs[name]
        if kind == "weight" and name in prog.weight_knobs():
            return "weight", prog.weight_knobs()[name]
        return None

    def _party_knob(self, key: str) -> bool:
        """A policy only the party layer may set (`party: true`); it may also live on the party board."""
        for a in self.agents:
            k = self._knob(a, key)
            if k and k[0] == "policy" and k[1].get("party"):
                return True
        return False

    def _current(self, agent: str, key: str, layers: dict[str, dict[str, Entry]] | None = None) -> Any:
        layers = self.layers if layers is None else layers
        for layer in (agent, PARTY):
            if self.enabled and key in layers.get(layer, {}):
                return layers[layer][key].value
        kind, _, name = key.partition(".")
        prog = self.base(agent)
        return prog.policies.get(name) if kind == "policy" else prog.behaviors[name].spec.get("weight", 1.0)

    def _targets(self, layer: str, key: str) -> list[str]:
        """Agents a key in this layer applies to."""
        if layer != PARTY:
            return [layer] if self._knob(layer, key) is not None else []
        return [a for a in self.agents if self._knob(a, key) is not None]

    # ------------------------------------------------------------ building
    def extra_for(self, agent: str) -> list[tuple[str, dict[str, Any]]]:
        """The Animus layers to stack on this agent's packages now (also for `anima reload`)."""
        return self._extra(agent, self.layers)

    def _extra(self, agent: str, layers: dict[str, dict[str, Entry]]) -> list[tuple[str, dict[str, Any]]]:
        out = []
        if not self.enabled:
            return out
        for layer, label in ((PARTY, "animus:party"), (agent, f"animus:{agent}")):
            data: dict[str, Any] = {}
            for key, e in layers.get(layer, {}).items():
                if self._knob(agent, key) is None:
                    continue                         # e.g. a party weight for a behavior this agent lacks
                kind, _, name = key.partition(".")
                if kind == "policy":
                    data.setdefault("policies", {})[name] = e.value
                else:
                    data.setdefault("behaviors", {})[name] = {"weight": e.value}
            if data:
                out.append((label, data))
        return out

    def _build_all(self, agents: list[str], layers: dict[str, dict[str, Entry]]) -> tuple[dict[str, Program], list[str]]:
        progs, errors = {}, []
        for a in agents:
            try:
                progs[a] = self.rebuild(a, self._extra(a, layers))
            except SigilError as e:
                errors += [f"{a}: {x}" for x in e.errors]
        return progs, errors

    def _commit(self, layers: dict[str, dict[str, Entry]], agents: list[str], progs: dict[str, Program],
                party_keys: list[str]) -> None:
        self.layers = layers
        for a in agents:
            self.apply(a, progs[a])
        for key in party_keys:
            name = key.partition(".")[2]
            ref = self.leader if self.leader in self.agents else self.agents[0]
            self.apply_party(name, self._current(ref, key))
        self.save()

    # ------------------------------------------------------------ patches
    def patch(self, layer: str, changes: list[dict[str, Any]], reason: str, origin: str = "human",
              request_id: str | None = None) -> Result:
        """changes: [{key: "policy.NAME" | "weight.BEHAVIOR", value, ttl_s?}]"""
        pid = f"p{next(_ids)}-{int(self.clock())}"
        now = self.clock()
        res = Result(False, pid)

        def reject(why: str, errors: list[str] = ()) -> Result:
            res.reason, res.errors = why, list(errors)
            self._event(layer, {"event": "rejected", "patch_id": pid, "layer": layer, "reason": why,
                                "errors": res.errors, "origin": origin, "request_id": request_id,
                                "changes": changes, "why": reason})
            return res

        if not self.enabled:
            return reject("off")
        if origin not in ORIGINS:
            return reject("bad_origin")
        if layer != PARTY and layer not in self.agents:
            return reject("unknown_layer")
        if layer == PARTY and origin == "local":
            return reject("not_allowed", ["a local LLM may only change its own agent's layer"])
        if not changes:
            return reject("empty")
        recent = sum(len(h["changes"]) for h in self.history
                     if h["layer"] == layer and h["event"] == "applied" and now - h["t"] < 3600)
        if recent + len(changes) > MAX_CHANGES_PER_HOUR:
            return reject("rate", [f"{recent} changes in this layer in the last hour (max {MAX_CHANGES_PER_HOUR})"])

        layers = {k: dict(v) for k, v in self.layers.items()}
        mine = layers.setdefault(layer, {})
        errors: list[str] = []
        lock = ""
        applied: list[dict[str, Any]] = []
        party_keys: list[str] = []
        for ch in changes:
            key, value = ch.get("key", ""), ch.get("value")
            targets = self._targets(layer, key)
            if not targets:
                errors.append(f"{key}: not an Animus knob here")
                continue
            for a in targets:
                kind, spec = self._knob(a, key)
                if kind == "policy" and spec.get("party") and layer != PARTY:
                    errors.append(f"{key}: only the party layer may set it")
                    break
                old = self._current(a, key, layers)
                why = (check_weight(spec, value) if kind == "weight" else
                       check_value(spec, value, old, self.is_room, self.base(a).policies.get(key.partition(".")[2])))
                if why:
                    errors.append(f"{key} ({a}): {why}")
                    break
                if origin == "local":                  # D26: the strategist's (or a person's) value holds
                    for held in (layers.get(PARTY, {}).get(key), layers.get(a, {}).get(key)):
                        if held and held.origin != "local" and now < held.holds_until():
                            lock = f"{key}: set by the {held.origin} until +{held.holds_until() - now:.0f}s"
                    if lock:
                        break
            cur = mine.get(key)
            if cur and now - cur.t < MIN_HOLD_S:
                errors.append(f"{key}: changed {now - cur.t:.0f}s ago (hold {MIN_HOLD_S:.0f}s)")
            if errors or lock:
                continue
            olds = {a: self._current(a, key, layers) for a in targets}
            ttl = ch.get("ttl_s")
            mine[key] = Entry(value, pid, now, float(ttl) if ttl else None, reason, origin)
            if layer == PARTY:                       # the party decision replaces any agent's own value
                for a in self.agents:
                    layers.get(a, {}).pop(key, None)
                if self._party_knob(key):
                    party_keys.append(key)
            applied.append({"key": key, "old": olds[targets[0]] if len(set(map(repr, olds.values()))) == 1 else olds,
                            "new": value, "ttl_s": ttl})
        if lock:
            return reject("locked_by_party", [lock])
        if errors:
            return reject("invalid", errors)
        agents = sorted({a for ch in changes for a in self._targets(layer, ch["key"])})
        progs, errs = self._build_all(agents, layers)
        if errs:
            return reject("sigil", errs)
        self._commit(layers, agents, progs, party_keys)
        res.ok, res.changes = True, applied
        self._record("applied", pid, layer, applied, reason, origin, request_id)
        return res

    # ------------------------------------------------------------ removing
    def _remove(self, pred: Callable[[str, str, Entry], bool], event: str, reason: str) -> list[dict[str, Any]]:
        layers = {k: dict(v) for k, v in self.layers.items()}
        gone: list[dict[str, Any]] = []
        for layer, entries in layers.items():
            for key, e in list(entries.items()):
                if pred(layer, key, e):
                    del entries[key]
                    gone.append({"layer": layer, "key": key, "old": e.value, "patch_id": e.patch_id})
        if not gone:
            return gone
        agents = sorted({a for g in gone for a in self._targets(g["layer"], g["key"])})
        progs, errs = self._build_all(agents, layers)
        if errs:                                     # cannot happen with a valid base, but never half-apply
            self._event(PARTY, {"event": "rejected", "reason": "sigil", "errors": errs, "why": reason})
            return []
        party = [g["key"] for g in gone if g["layer"] == PARTY and self._party_knob(g["key"])]
        self._commit(layers, agents, progs, party)
        for layer in sorted({g["layer"] for g in gone}):
            self._record(event, "", layer, [g for g in gone if g["layer"] == layer], reason, "system", None)
        return gone

    def expire(self) -> list[dict[str, Any]]:
        now = self.clock()
        return self._remove(lambda l, k, e: e.ttl_s is not None and now >= e.t + e.ttl_s, "expired", "ttl")

    def revert(self, patch_id: str | None = None, reason: str = "human") -> list[dict[str, Any]]:
        """One patch's entries, or everything (patch_id None)."""
        return self._remove(lambda l, k, e: patch_id is None or e.patch_id == patch_id, "reverted", reason)

    def died(self, agent: str) -> list[dict[str, Any]]:
        """D28: an agent died; undo what the Animus changed for it shortly before."""
        now = self.clock()
        gone = self._remove(lambda l, k, e: l in (agent, PARTY) and now - e.t <= DEATH_WINDOW_S,
                            "reverted", "death")
        pids = {g["patch_id"] for g in gone}
        for h in self.history:
            if h.get("patch_id") in pids and h["event"] == "applied":
                h["followed_by_death"] = {"agent": agent, "after_s": round(now - h["t"])}
        if gone:
            self.deaths.append({"agent": agent, "t": now, "reverted": gone})
        return gone

    # ------------------------------------------------------------ off / on
    def off(self) -> None:
        if not self.enabled:
            return
        self.enabled = False
        progs, _ = self._build_all(self.agents, self.layers)       # enabled False: no extra layers
        for a in self.agents:
            if a in progs:
                self.apply(a, progs[a])
        ref = self.leader if self.leader in self.agents else self.agents[0]
        for key in self.layers.get(PARTY, {}):
            if self._party_knob(key):
                self.apply_party(key.partition(".")[2], self._current(ref, key))
        self._record("off", "", PARTY, [], "animus off", "human", None)
        self.save()

    def on(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        progs, errs = self._build_all(self.agents, self.layers)
        if errs:
            self.enabled = False
            self._event(PARTY, {"event": "rejected", "reason": "sigil", "errors": errs, "why": "animus on"})
            return
        party = [k for k in self.layers.get(PARTY, {}) if self._party_knob(k)]
        self._commit(self.layers, self.agents, progs, party)
        self._record("on", "", PARTY, [], "animus on", "human", None)
        self.expire()

    # ------------------------------------------------------------ views
    def locked_keys(self, agent: str | None = None) -> dict[str, float]:
        """Keys this agent's local LLM cannot change now, with seconds left (for self_tune questions)."""
        now = self.clock()
        out: dict[str, float] = {}
        for layer in (PARTY, agent):
            for k, e in self.layers.get(layer, {}).items() if layer else ():
                if e.origin != "local" and now < e.holds_until():
                    out[k] = max(out.get(k, 0), round(e.holds_until() - now))
        return out

    def show(self) -> dict[str, Any]:
        return {"enabled": self.enabled,
                "layers": {l: {k: asdict(e) for k, e in v.items()} for l, v in self.layers.items() if v},
                "locked": {a: lk for a in self.agents if (lk := self.locked_keys(a))}}

    # ------------------------------------------------------------ records
    def _event(self, layer: str, data: dict[str, Any]) -> None:
        agent = layer if layer in self.agents else (self.leader if self.leader in self.agents else self.agents[0])
        self.publish(agent, "runtime.animus", data)

    def _record(self, event: str, pid: str, layer: str, changes: list[dict[str, Any]], reason: str,
                origin: str, request_id: str | None) -> None:
        h = {"t": self.clock(), "event": event, "patch_id": pid, "layer": layer, "changes": changes,
             "reason": reason, "origin": origin, "request_id": request_id}
        self.history.append(h)
        self._event(layer, {k: v for k, v in h.items() if k != "t"})

    def save(self) -> None:
        if self.save_path is None:
            return
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"enabled": self.enabled, "layers": {l: {k: asdict(e) for k, e in v.items()}
                                                    for l, v in self.layers.items()},
                "history": self.history[-500:], "deaths": self.deaths[-50:]}
        tmp = self.save_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str))
        tmp.replace(self.save_path)

    def load(self) -> None:
        """Restore saved layers (after a restart). Invalid ones are dropped, never half-applied."""
        if self.save_path is None or not self.save_path.exists():
            return
        data = json.loads(self.save_path.read_text())
        layers = {l: {k: Entry(**e) for k, e in v.items()} for l, v in data.get("layers", {}).items()}
        self.history = data.get("history", [])
        self.deaths = data.get("deaths", [])
        self.enabled = data.get("enabled", True)
        progs, errs = self._build_all(self.agents, layers)
        if errs:
            self._event(PARTY, {"event": "rejected", "reason": "sigil", "errors": errs, "why": "load"})
            return
        party = [k for k in layers.get(PARTY, {}) if self._party_knob(k)]
        self._commit(layers, self.agents, progs, party)
        self.expire()


# ---------------------------------------------------------------- patch answers (from an LLM)

PATCH_SCHEMA = {
    "type": "object", "required": ["changes"],
    "shape": '{"changes": [{"layer": "party" | "<agent name>", "key": "policy.NAME" | "weight.BEHAVIOR", '
             '"value": <new value>, "ttl_s": <seconds, optional>}], "reason": "<why, one sentence>"}. '
             'Use only keys listed under knobs. An empty "changes" list means: change nothing.',
}


def patch_groups(answer: Any, default_layer: str | None = None) -> tuple[dict[str, list[dict[str, Any]]], str]:
    """An LLM's patch answer -> ({layer: [change]}, reason). Malformed entries are dropped here; the
    overlay still validates every value. `default_layer` fills in a missing layer (a local LLM)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(answer, dict):
        return groups, ""
    for ch in answer.get("changes") or []:
        if not isinstance(ch, dict):
            continue
        key = ch.get("key") or ch.get("knob")              # models drift between the two words
        layer = default_layer or ch.get("layer")
        if not isinstance(key, str) or not isinstance(layer, str) or "value" not in ch:
            continue
        c = {"key": key, "value": ch["value"]}
        if isinstance(ch.get("ttl_s"), (int, float)) and ch["ttl_s"] > 0:
            c["ttl_s"] = float(ch["ttl_s"])
        groups.setdefault(layer, []).append(c)
    return groups, str(answer.get("reason") or "")[:300]
