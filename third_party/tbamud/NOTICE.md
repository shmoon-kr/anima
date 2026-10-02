# tbaMUD-derived material

Everything in this directory comes from, or was extracted from, **tbaMUD**
(https://github.com/tbamud/tbamud), which is based on CircleMUD by Jeremy Elson and DikuMUD by
Hans Henrik Staerfeldt, Katja Nyboe, Tom Madsen, Michael Seifert and Sebastian Hammer.

**This directory is NOT covered by Anima's own license.** It is distributed under the CircleMUD
license and the DikuMUD license it incorporates (copies: `LICENSE-CircleMUD.md`,
`license-tbamud.txt`). In short, those licenses forbid using the material to make money or be
compensated in any way, and require crediting the authors. Read the full texts before any use.

| File | What it is |
|---|---|
| `messages` | Copy of tbaMUD `lib/misc/messages` (combat messages for skills, spells and weapons). The text adapter builds its combat patterns from it. |
| `hazards.yaml` | Trap exits, bad exits and deadly rooms of the stock tbaMUD world, learned while playing it (room vnums and names). |
| `fixtures/*.log` | Short excerpts of real play logs on a tbaMUD server, used as adapter test fixtures. They contain room descriptions and mob/server messages from the stock world. |

## Not in this directory, but also tbaMUD-specific

- `anima/adapters/tbamud_text/rules.py` and `combat.py` contain short server message templates
  (`dam_weapons[]`, `attack_hit_text[]` and single-line messages), each annotated with its source
  file and line in tbaMUD, so the adapter can recognise the server's output.
- `anima/memoria/importers/tbamud.py` reads the tbaMUD world file *format*; it contains no world data.
- `packages/party-midgaard` and `docs/SPEC-from-tintin.md` mention room, mob and zone names of the
  stock world.

The Anima engine does not ship the tbaMUD world: it reads world files from your own local tbaMUD
installation (`config/anima.toml` `world_dir`, or the `ANIMA_TBAMUD_WORLD` environment variable).

This notice is not legal advice. If you plan any commercial use, get proper legal review first.
