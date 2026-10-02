"""D12: the engine knows no character, class, role or party convention. Those live in packages."""
import re
from pathlib import Path

ENGINE = Path(__file__).parents[1] / "anima"
# tbaMUD adapter and importer may name server facts (they translate the server); the layers above may not.
LAYERS = [p for p in ENGINE.rglob("*.py") if "adapters" not in p.parts and "importers" not in p.parts]

FORBIDDEN = [
    # characters
    "Vallen", "Lil", "Senia", "Lumina", "Elysia", "Carmilla",
    # classes and roles
    "warrior", "cleric", "mage", "thief", "healer", "opener",   # ("provider" also means LLM provider)
    # tintin party code words (D10, D14)
    "sendcon", "canopen", "noopen", "canteen",
    # world specifics
    "Midgaard", "Temple Square", "Newbie",
]


def test_engine_layers_have_no_agent_knowledge():
    hits = []
    for path in LAYERS:
        text = path.read_text(encoding="utf-8")
        for word in FORBIDDEN:
            for m in re.finditer(rf"\b{re.escape(word)}\b", text, re.I):
                line = text[:m.start()].count("\n") + 1
                hits.append(f"{path.relative_to(ENGINE.parent)}:{line}: {word}")
    assert not hits, "\n".join(hits)


def test_new_character_needs_only_a_manifest(tmp_path):
    from anima.sigil.program import load_agent
    root = Path(__file__).parents[1]
    m = tmp_path / "Newcomer.yaml"
    m.write_text("agent: Newcomer\nlayers: [base, class-mage, party-midgaard]\npolicies: {flee_pct: 50}\n")
    prog = load_agent(m, root / "packages")
    assert prog.policies["flee_pct"] == 50 and "fight_spell" in prog.behaviors
