"""Short shared guidance and optional per-game notes."""
import json
from pathlib import Path

BRIEF_DIR = Path(__file__).parent / "briefs"
GENERAL = "Read all on-screen text, try common UI conventions, and explore before committing. In top-down views, the way out of an enclosed room is often a distinct floor patch or opening at its edge; name that patch or opening as the exit."
DEFAULTS = {"name": "Hand"}


def _load(game_id: str) -> dict:
    path = BRIEF_DIR / f"{game_id}.json"
    if not path.is_file() or path.stat().st_size > 8192:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid game brief: {path}")
    return data


def load_brief(game_id: str) -> str:
    data = _load(game_id)
    return "\n".join([*data.get("controls", []), *data.get("notes", [])])


def defaults(game_id: str) -> dict:
    return {**DEFAULTS, **_load(game_id).get("defaults", {})}
