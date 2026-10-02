"""Shared test setup: where the local tbaMUD world files are (never committed, D-NOTICE)."""
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(Path(__file__).parent))


def world_dir() -> Path:
    """ANIMA_TBAMUD_WORLD, else config/anima.toml [paths] world_dir, else ../tbamud/lib/world."""
    if os.environ.get("ANIMA_TBAMUD_WORLD"):
        return Path(os.environ["ANIMA_TBAMUD_WORLD"])
    cfg = ROOT / "config" / "anima.toml"
    if cfg.exists():
        wd = tomllib.loads(cfg.read_text(encoding="utf-8")).get("paths", {}).get("world_dir")
        if wd:
            return Path(wd).expanduser()
    return ROOT.parent / "tbamud" / "lib" / "world"
