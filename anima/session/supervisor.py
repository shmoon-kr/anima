"""Run several agents in one process (replaces mud-agents run.sh).

One bus, one Memoria, one recorder, one Animus queue; per agent a Session and an AgentRuntime.
A unix control socket serves `anima status`, `anima watch NAME` (with human input) and `anima stop`.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anima import timescale
from anima.animus.overlay import Overlay
from anima.animus.queue import AnimusQueue, FakeProvider
from anima.bus import Bus
from anima.memoria import Memoria
from anima.protocol.commands import Source
from anima.protocol.envelope import Event, Stamper
from anima.recorder import Recorder
from anima.runtime.agent import AgentRuntime
from anima.session.session import Session
from anima.sigil.program import SigilError, load_agent

FAST_TICK_S = 0.1
ANIMUS_TICK_S = 5.0


@dataclass
class Config:
    host: str
    port: int
    password: str
    world_dir: Path
    hazards: Path
    agents_dir: Path = Path("agents")
    packages_dir: Path = Path("packages")
    recordings: Path = Path("recordings")
    run_dir: Path = Path("run")
    animus: dict[str, Any] = field(default_factory=dict)     # config/anima.toml [animus]; empty = phase-1 fakes
    protocol: str = "telnet"                                  # [server] protocol: telnet (tbaMUD) or mundi
    characters: dict[str, dict[str, Any]] = field(default_factory=dict)   # [characters.NAME]: sex, lang (mundi)
    lang: str = "en"                                          # [server] lang: the screens' language (mundi: en, ko)
    speed: float = 1.0                                        # [server] speed: game time x N (a Mundi run with --speed N)

    @classmethod
    def load(cls, root: Path = Path(".")) -> "Config":
        """ANIMA_PROFILE=NAME: config/secret.NAME.toml, run/NAME, recordings/NAME (a second world,
        e.g. Anima Mundi, beside the running party without sharing its files)."""
        profile = os.environ.get("ANIMA_PROFILE", "")
        secret = tomllib.loads((root / "config" / (f"secret.{profile}.toml" if profile else "secret.toml")).read_text())
        sub = (lambda p: p / profile) if profile else (lambda p: p)
        local = {}
        if (root / "config" / "anima.toml").exists():
            local = tomllib.loads((root / "config" / "anima.toml").read_text())
        paths = local.get("paths", {})
        auth = secret["auth"]
        password = auth.get("password", "")
        if not password and auth.get("password_from_tintin"):
            password = _tintin_pass((root / auth["password_from_tintin"]).expanduser())
        if not password:
            raise SystemExit("config/secret.toml: set auth.password or auth.password_from_tintin")
        return cls(host=secret["server"]["host"], port=int(secret["server"]["port"]),
                   password=password,
                   world_dir=Path(os.environ.get("ANIMA_TBAMUD_WORLD")
                                  or paths.get("world_dir", "../tbamud/lib/world")).expanduser(),
                   hazards=root / paths.get("hazards", "third_party/tbamud/hazards.yaml"),
                   agents_dir=root / "agents", packages_dir=root / "packages",
                   recordings=sub(root / "recordings"), run_dir=sub(root / "run"), animus=local.get("animus", {}),
                   protocol=secret["server"].get("protocol", "telnet"), characters=secret.get("characters", {}),
                   lang=secret["server"].get("lang", "en"), speed=float(secret["server"].get("speed", 1)))


def _tintin_pass(path: Path) -> str:
    """mud-agents secret.tin: `#var pass VALUE` (read only, never copied)."""
    import re
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*#var(?:iable)?\s+\{?pass\}?\s+\{?([^}\s]+)\}?", line)
        if m:
            return m.group(1)
    return ""


def default_answer(req) -> Any:          # phase 1: no LLM, every question gets its default (D16)
    return req.default


@dataclass
class Supervisor:
    cfg: Config
    agents: list[str]
    bus: Bus = field(default_factory=Bus)
    sessions: dict[str, Session] = field(default_factory=dict)
    runtimes: dict[str, AgentRuntime] = field(default_factory=dict)
    _stamper: dict[str, Stamper] = field(default_factory=dict)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _stopping: asyncio.Event = field(default_factory=asyncio.Event)
    _watchers: list[tuple[str, asyncio.Queue]] = field(default_factory=list)
    _viewers: list[tuple[set[str], asyncio.Queue]] = field(default_factory=list)   # (agents, queue) of `view`
    _screen: dict[str, Any] = field(default_factory=dict)                          # agent -> recent coloured text

    def _profile(self, name: str, policies: dict[str, Any]) -> dict[str, Any]:
        """What a Mundi login says about the character: its class from the party's `classes`, and
        sex or language from [characters.NAME] in the secret file."""
        out = {"lang": self.cfg.lang, **self.cfg.characters.get(name, {})}
        cls = (policies.get("classes") or {}).get(name)
        if cls and "class" not in out:
            from anima.adapters.mundi_ws.adapter import mundi_class
            out["class"] = mundi_class(cls)
        return out

    def build(self) -> None:
        timescale.set_scale(self.cfg.speed)        # before any clock is read: game time x speed (Mundi --speed)
        self.memoria = Memoria.from_tbamud(self.cfg.world_dir, self.cfg.hazards)
        self.memoria.attach(self.bus)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.recorder = Recorder(self.cfg.recordings / f"{stamp}.jsonl", secrets=[self.cfg.password])
        self.recorder.attach(self.bus)
        from anima.animus.providers import make_providers
        providers, budget = make_providers(self.cfg.animus, FakeProvider(default_answer, name="default"))
        self.animus = AnimusQueue(self.bus, providers, lambda a: self._stamper[a], budget_per_hour=budget)
        self.bus.subscribe(self._to_watchers)
        from anima.session.humans import Desk
        self.desk = Desk(list(self.agents), [Path("config/aliases.yaml"), Path("config/aliases.example.yaml")])
        programs = {n: load_agent(self.cfg.agents_dir / f"{n}.yaml", self.cfg.packages_dir) for n in self.agents}
        from anima.party.blackboard import PartyBoard
        first = programs[self.agents[0]].policies
        self.party = PartyBoard.from_policies(self.memoria, first, clock=timescale.now)
        self.cfg.run_dir.mkdir(parents=True, exist_ok=True)
        self.party.save_path = self.cfg.run_dir / "party.json"
        if first.get("zone_ladder"):
            from anima.party.ladder import Ladder
            self.party.ladder = Ladder(self.memoria, hub=lambda: self.memoria.locator(self.party.leader).vnum,
                                       clock=timescale.wall)
        self.party.load()
        for name in self.agents:
            prog = programs[name]
            st = Stamper(name, clock=timescale.wall)
            self._stamper[name] = st
            sess = Session(name, self.cfg.host, self.cfg.port, self.cfg.password, self.bus, st,
                           protocol=self.cfg.protocol, profile=self._profile(name, first))
            rt = AgentRuntime(name, prog, self.memoria, self.bus, st, sess.send, timescale.now,
                              party=self.party, animus=self.animus)
            sess.on_text = lambda text, n=name: self._text(n, text)
            self.party.register(name, rt.state)
            rt.attach()
            self.sessions[name], self.runtimes[name] = sess, rt
        self.bus.subscribe(self.party.on_event)
        self._base = {n: self._load(n) for n in self.agents}       # separate objects: hot-apply mutates the runtime's
        self.overlay = Overlay(
            self.agents, base=lambda n: self._base[n], rebuild=self._load,
            apply=lambda n, prog: self.runtimes[n].apply_values(prog),
            publish=lambda n, t, d: self.bus.publish(self._stamper[n].stamp(t, d)),
            clock=timescale.wall, is_room=lambda r: bool(self.memoria.graph.rooms_named(r)),
            apply_party=self._apply_party, leader=self.party.leader,
            save_path=self.cfg.run_dir / "animus" / "overlay.json")
        self.overlay.load()
        self.bus.subscribe(lambda ev: self.overlay.died(ev.agent) if ev.type == "self.died" else None)
        from collections import deque
        self._recent: deque[Event] = deque()
        self.bus.subscribe(self._remember)
        for rt in self.runtimes.values():
            rt.ask_facts, rt.on_patch = self._ask_facts, self._local_patch
        self.strategist = None
        a = self.cfg.animus
        if a.get("strategist", a.get("claude", "fake") != "fake"):
            from anima.animus.strategist import Strategist
            self.strategist = Strategist(
                self.animus, self.overlay, self.party.leader, view=self._party_view, knobs=self._knob_table,
                world=self._zones, zone_of_leader=lambda: self._zone(self.memoria.locator(self.party.leader).vnum),
                span=self._span,
                notes_path=self.cfg.run_dir / "animus" / "party-notes.md",
                every_s=float(a.get("strategist_every_min", 30)) * 60, min_gap_s=float(a.get("strategist_gap_min", 5)) * 60)
            self.bus.subscribe(self.strategist.on_event)

    # ------------------------------------------------------------ local LLM questions (S4)
    def _remember(self, ev: Event) -> None:
        if ev.type == "animus.request":
            return
        self._recent.append(ev)
        while self._recent and self._recent[0].t < ev.t - 1800:
            self._recent.popleft()

    def _span(self, agent: str, level: int) -> int | None:
        return self.memoria.level_span(self.party.classes.get(agent), level)

    def _ask_facts(self, agent: str, facts: list[str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "knobs" in facts:
            out["knobs"] = {k: {**{x: v for x, v in row.items() if x != "value"},
                                "value": row["value"] if not isinstance(row["value"], dict) else row["value"].get(agent)}
                            for k, row in self._knob_table().items()
                            if not row.get("party") and (not isinstance(row["value"], dict) or agent in row["value"])}
        if "locked" in facts:
            out["locked_by_strategist_s"] = self.overlay.locked_keys(agent)
        if "recent" in facts:
            from anima.animus.strategist import summarize
            sm = summarize(list(self._recent), self._span)
            out["last_30_minutes"] = {"me": sm.get("members", {}).get(agent),
                                      **{k: v for k, v in sm.items() if k != "members"}}
        return out

    def _local_patch(self, agent: str, answer: Any, request_id: str) -> None:
        from anima.animus.overlay import patch_groups
        groups, reason = patch_groups(answer, default_layer=agent)
        if groups.get(agent):
            self.overlay.patch(agent, groups[agent], reason or "local", origin="local", request_id=request_id)

    # ------------------------------------------------------------ what the strategist sees
    def _zone(self, vnum: int | None) -> int | None:
        z = self.memoria.world.zone_of(vnum) if vnum is not None else None
        return z.num if z else None

    def _party_view(self) -> dict[str, Any]:
        members = {}
        for name, rt in self.runtimes.items():
            s, vnum = rt.state, self.memoria.locator(name).vnum
            members[name] = {"in_game": s.in_game, "level": s.level, "class": self.party.classes.get(name),
                             "roles": [r for r, who in self.party.roles.items() if name in who],
                             "hp_pct": round(s.pct(s.hp, s.hp_max)), "mp_pct": round(s.pct(s.mp, s.mp_max)),
                             "mv_pct": round(s.pct(s.mv, s.mv_max)), "position": s.position,
                             "room": s.room.get("name"), "zone": self._zone(vnum), "gold": s.gold,
                             "hungry": s.hungry, "thirsty": s.thirsty, "behavior": rt.ctx.behavior,
                             "task": rt.tasks.name}
        out = {"leader": self.party.leader, "rally": self.party.rally, "circuit": self.party.circuit,
               "members": members}
        lad = self.party.ladder
        if lad is not None:
            lv = self.party.levels()
            out["zone_ladder"] = {"fitting_zones": [vars(f) for f in lad.candidates(lv, span=self.party.mean_span())[:6]],
                                  "too_dangerous_for_now": lad.blocked(min(lv or [0]))}
        return out

    def _knob_table(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self.agents:
            prog, live = self._base[name], self.runtimes[name].program
            for k, spec in prog.knobs.items():
                row = out.setdefault(f"policy.{k}", {**{x: v for x, v in spec.items() if x != "description"},
                                                    "value": {}})
                row["value"][name] = live.policies.get(k)
            for b, rng in prog.weight_knobs().items():
                row = out.setdefault(f"weight.{b}", {"type": "weight", "min": rng[0], "max": rng[1], "value": {}})
                row["value"][name] = live.behaviors[b].spec.get("weight", 1.0)
        for row in out.values():                           # one value when everyone has the same
            vals = list(row["value"].values())
            if all(v == vals[0] for v in vals) and len(row["value"]) == len(self.agents):
                row["value"] = vals[0]
        return out

    def _zones(self) -> list[dict[str, Any]]:
        levels = [rt.state.level for rt in self.runtimes.values() if rt.state.level]
        if not levels:
            return []
        lo, hi = min(levels), max(levels)
        start = self.memoria.locator(self.party.leader).vnum
        out = []
        seen: set[int] = set()
        for lvl in range(lo, hi + 1):
            for z in self.memoria.knowledge.zones_for_level(lvl):
                if z.num in seen:
                    continue
                seen.add(z.num)
                rooms = [r for v, r in sorted(self.memoria.world.rooms.items()) if z.bottom <= v <= z.top]
                mobs = [m.level for m in self.memoria.world.mobs.values() if z.bottom <= m.vnum <= z.top]
                path = self.memoria.graph.path(start, rooms[0].vnum, self.memoria.conditions()) \
                    if start is not None and rooms else None
                out.append({"zone": z.num, "name": z.name, "levels": f"{z.min_level}-{z.max_level}",
                            "mob_levels": f"{min(mobs)}-{max(mobs)}" if mobs else None, "mobs": len(mobs),
                            "rooms": [r.name for r in rooms[:3]],
                            "steps_from_leader": None if path is None else len(path)})
        return out[:15]

    def _load(self, name: str, extra=()):
        return load_agent(self.cfg.agents_dir / f"{name}.yaml", self.cfg.packages_dir, extra)

    def _apply_party(self, key: str, value: Any) -> None:
        if key in ("rally", "circuit"):                 # the strategist's choice holds before the ladder picks again
            self.party.override_until = self.party.clock() + 1800
        if key == "rally":
            self.party.rally = value
            self.party.save()
        elif key == "circuit":
            self.party.circuit = list(value)

    async def run(self) -> None:
        self.build()
        self.cfg.run_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.run_dir / "anima.pid").write_text(str(os.getpid()))
        server = await asyncio.start_unix_server(self._control, path=str(self.cfg.run_dir / "anima.sock"))
        self._tasks.append(asyncio.create_task(self.animus.run()))
        self._tasks.append(asyncio.create_task(self._animus_loop()))
        for i, name in enumerate(self.agents):
            self._tasks.append(asyncio.create_task(self._start_later(name, i * 3.0)))
            self._tasks.append(asyncio.create_task(self._tick_loop(name)))
        try:
            await self._stopping.wait()
        finally:
            for s in self.sessions.values():
                await s.stop(quit_game=True)
            for t in self._tasks:
                t.cancel()
            server.close()
            self.recorder.close()
            for f in ("anima.sock", "anima.pid"):
                (self.cfg.run_dir / f).unlink(missing_ok=True)

    async def _start_later(self, name: str, delay: float) -> None:
        await timescale.sleep(delay)                    # leader first, the others a few seconds apart
        await self.sessions[name].run()

    async def _tick_loop(self, name: str) -> None:
        rt = self.runtimes[name]
        last = 0.0
        while True:
            now = timescale.now()
            if now - last >= 1.0:
                rt.tick()
                last = now
            else:
                rt.tick_if_dirty()
            self.animus.expire_due()
            await timescale.sleep(FAST_TICK_S)

    async def _animus_loop(self) -> None:
        while True:
            await asyncio.sleep(ANIMUS_TICK_S)
            self.overlay.expire()
            if self.strategist:
                self.strategist.tick()

    def stop(self) -> None:
        self._stopping.set()

    # ------------------------------------------------------------ status / control
    def status(self) -> dict[str, Any]:
        out = {}
        for name, rt in self.runtimes.items():
            s, sess = rt.state, self.sessions[name]
            loc = self.memoria.locator(name)
            out[name] = {"connected": sess.connected, "in_game": s.in_game, "error": sess.error,
                         "hp": s.hp, "hp_max": s.hp_max, "mp": s.mp, "mv": s.mv, "position": s.position,
                         "room": s.room.get("name"), "vnum": loc.vnum, "behavior": rt.ctx.behavior,
                         "task": rt.tasks.name, "scores": rt.selector.last_scores[:3]}
        return out

    def _to_watchers(self, ev: Event) -> None:
        for agent, q in list(self._watchers):
            if ev.agent == agent:
                q.put_nowait(ev)
        if ev.type != "prompt":
            for agents, q in list(self._viewers):
                if ev.agent in agents:
                    q.put_nowait({"k": "ev", "a": ev.agent, "ev": ev.to_dict()})

    # ------------------------------------------------------------ people: screens and input
    SCREEN_KEEP = 16000                                  # characters of scrollback per character

    def _text(self, agent: str, text: str) -> None:
        buf = (self._screen.get(agent, "") + text)[-self.SCREEN_KEEP:]
        self._screen[agent] = buf
        for agents, q in list(self._viewers):
            if agent in agents:
                q.put_nowait({"k": "text", "a": agent, "s": text})

    def human_input(self, line: str, focus: str | None) -> list[str]:
        """Route a typed line (aliases, #name, #all, #party, #go, #take); returns notes for the person."""
        mates = self.party.in_room_with(focus) if focus in self.runtimes else []
        orders, notes = self.desk.route(line, focus, mates)
        from anima.session.humans import HOLD_S
        for o in orders:
            rt, sess = self.runtimes.get(o.agent), self.sessions.get(o.agent)
            if rt is None or sess is None:
                continue
            if o.kind == "send":
                rt.hold(HOLD_S)
                sess.send(o.text, Source("human", "play", "typed by a person"), priority=0)
            elif o.kind == "go":
                rt.hold(HOLD_S)
                rt.human_goal = o.text
                notes.append(f"{o.agent}: walking to {o.text}")
            elif o.kind == "stop":
                rt.human_goal = None
                notes.append(f"{o.agent}: stopped")
            elif o.kind == "take":
                rt.taken = True
                rt.tasks.abandon("a person took over")
                notes.append(f"{o.agent}: yours until #release {o.agent}")
            elif o.kind == "release":
                rt.taken, rt.human_until, rt.human_goal = False, -1e9, None
                notes.append(f"{o.agent}: back to the agent")
            rt._publish("runtime.human", {"event": o.kind, "text": o.text, "taken": rt.taken})
        return notes

    def party_line(self) -> dict[str, Any]:
        st = self.status()
        for name, rt in self.runtimes.items():
            st[name]["held"] = rt.taken or rt.held()
            st[name]["taken"] = rt.taken
            st[name]["mp_max"], st[name]["mv_max"] = rt.state.mp_max, rt.state.mv_max
        return st

    async def _control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = json.loads((await reader.readline()).decode() or "{}")
            op = req.get("op")
            if op == "status":
                writer.write((json.dumps(self.status(), ensure_ascii=False) + "\n").encode())
            elif op == "reload":
                res = {}
                for name, rt in self.runtimes.items():          # the Animus layers stay on top of the new packages
                    res[name] = rt.reload(lambda n=name: self._load(n, self.overlay.extra_for(n)))
                    if res[name]:
                        self._base[name] = self._load(name)
                writer.write((json.dumps(res) + "\n").encode())
            elif op == "animus":
                writer.write((json.dumps(self._animus_op(req), ensure_ascii=False, default=str) + "\n").encode())
            elif op == "stop":
                writer.write(b'{"ok": true}\n')
                self.stop()
            elif op == "send":
                agent = req.get("agent")
                if agent in self.sessions:
                    self.sessions[agent].send(req.get("text", ""), Source("human", "watch", "typed by a person"),
                                              priority=0)
                    writer.write(b'{"ok": true}\n')
            elif op == "watch":
                await self._watch(req.get("agent", ""), reader, writer)
            elif op == "view":
                await self._view(req, reader, writer)
            elif op == "input":
                writer.write((json.dumps({"notes": self.human_input(req.get("line", ""), req.get("focus"))}) + "\n").encode())
            await writer.drain()
        except (ConnectionError, json.JSONDecodeError):
            pass
        finally:
            writer.close()

    def _animus_op(self, req: dict[str, Any]) -> Any:
        ov, sub = self.overlay, req.get("sub")
        if sub == "show":
            return ov.show()
        if sub == "history":
            return ov.history[-int(req.get("n", 20)):]
        if sub == "revert":
            return ov.revert(None if req.get("all") else req.get("patch_id"), reason="human")
        if sub == "off":
            ov.off()
            return {"enabled": ov.enabled}
        if sub == "on":
            ov.on()
            return {"enabled": ov.enabled}
        if sub == "set":                       # a person sets a knob by hand (testing, or overriding the LLM)
            res = ov.patch(req["layer"], [{"key": req["key"], "value": req["value"], "ttl_s": req.get("ttl_s")}],
                           req.get("reason", "set by hand"), origin="human")
            return {"ok": res.ok, "patch_id": res.patch_id, "reason": res.reason, "errors": res.errors,
                    "changes": res.changes}
        return {"error": f"unknown animus op {sub!r}"}

    async def _view(self, req: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Stream to a person's screen: coloured server text and events of the agents asked for, the party
        status every second; lines the person types come back as {"line", "focus"} and are routed."""
        agents = set(req.get("agents") or self.agents)
        q: asyncio.Queue = asyncio.Queue()
        entry = (agents, q)
        self._viewers.append(entry)
        for a in req.get("agents") or []:            # scrollback first, so the screen is not empty
            if self._screen.get(a):
                q.put_nowait({"k": "text", "a": a, "s": self._screen[a][-4000:]})

        async def pump_in() -> None:
            while line := await reader.readline():
                try:
                    msg = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue
                if "agents" in msg:                  # the person switched focus
                    agents.clear()
                    agents.update(msg["agents"])
                    for a in msg["agents"]:
                        if self._screen.get(a):
                            q.put_nowait({"k": "text", "a": a, "s": self._screen[a][-4000:]})
                if msg.get("line"):
                    q.put_nowait({"k": "notes", "notes": self.human_input(msg["line"], msg.get("focus"))})

        async def status() -> None:
            while True:
                q.put_nowait({"k": "status", "party": self.party_line()})
                await asyncio.sleep(1.0)

        inp, st = asyncio.create_task(pump_in()), asyncio.create_task(status())
        try:
            while not inp.done():
                msg = await q.get()
                writer.write((json.dumps(msg, ensure_ascii=False, default=str) + "\n").encode())
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            inp.cancel()
            st.cancel()
            self._viewers.remove(entry)

    async def _watch(self, agent: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        q: asyncio.Queue[Event] = asyncio.Queue()
        entry = (agent, q)
        self._watchers.append(entry)

        async def pump_in() -> None:                     # lines typed in `anima watch` → human commands
            while line := await reader.readline():
                text = line.decode().rstrip("\r\n")
                if text and agent in self.sessions:
                    self.sessions[agent].send(text, Source("human", "watch", "typed by a person"), priority=0)

        inp = asyncio.create_task(pump_in())
        try:
            while not inp.done():
                ev = await q.get()
                writer.write((ev.to_json() + "\n").encode())
                await writer.drain()
        finally:
            inp.cancel()
            self._watchers.remove(entry)
