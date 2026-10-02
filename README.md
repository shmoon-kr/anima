# Anima

A runtime that lets AI agents (and people) live in MUD worlds — an agent-first replacement for
tintin++. Phase 1 runs a six-character party on a tbaMUD server.

- `docs/VISION.md` — the big picture and terms
- `docs/PHASE-1.md` — current phase: goals, milestones, completion criteria
- `docs/DECISIONS.md` — decisions and why
- `docs/PROTOCOL.md` — event protocol v0 (engine contract + Anima internals)
- `docs/SIGIL-API.md` — what a Sigil (agent rule package) may read and call (generated)

## How it is layered

```
 Animus (LLM)       slow sense: asks questions asynchronously, answers arrive as events   (phase 2)
 Party              blackboard (who is where, needs, roles) + party goals (rally, circuit)
 Behavior selection utility AI: every behavior scores itself 0..1, the best one runs
 Tasks              multi-step procedures (walk to the guild, practise, walk back)
 Reflexes           immediate reactions (flee, wake, avoid danger)
 Memoria / bus / text adapter (server text → protocol events)
```

Agents are Sigil packages (YAML + a small pure expression language), validated before they load.
Every command records which layer and rule sent it, and why.

## Layout

```
anima/         engine: adapter, bus, recorder, Memoria (world model), Sigil, reflex,
               utility selection, tasks, party blackboard, Animus (LLM slot), sessions
packages/      agent packages (Sigil YAML), stacked base → class → party → role → agent
agents/        one manifest per character
third_party/   tbaMUD-derived data under its own license (see third_party/tbamud/NOTICE.md)
tests/
```

## Run

```
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp config/secret.example.toml config/secret.toml      # server and password (never committed)
cp config/anima.example.toml config/anima.toml        # path to your local tbaMUD lib/world
.venv/bin/anima sigil check
.venv/bin/anima start            # all agents in the background (leader first)
.venv/bin/anima status           # per agent: connection, HP/MP/MV, room, behavior, top scores
.venv/bin/anima watch Vallen     # live events and why each command was sent; typed lines go out as human commands
.venv/bin/anima reload           # reload Sigil packages into running agents (a failing one keeps the old program)
.venv/bin/anima stop             # quit the game cleanly
.venv/bin/anima stats recordings/<file>.jsonl     # kills, deaths, flees, separation ... (also works on tintin logs)
.venv/bin/anima animus show       # phase 2: values the LLM layers changed; history | revert ID | off | on | set
.venv/bin/anima animus bench claude   # provider latency (config/anima.toml [animus] picks providers per tier)
.venv/bin/pytest -q              # world-file tests need ANIMA_TBAMUD_WORLD or config path
```

## License

Anima's own code is under the MIT License (`LICENSE`).
**Exception:** `third_party/tbamud/` is tbaMUD-derived material under the CircleMUD/DikuMUD license
(non-commercial use only, attribution required) — see `third_party/tbamud/NOTICE.md`. The MIT
License does not apply to it. Anima does not ship the tbaMUD world; it reads your local installation.
