from __future__ import annotations

import json
from pathlib import Path

from avatar.config import project_root
from avatar.schema import GsmSpec


def gsm_dir(root: Path | None = None) -> Path:
    base = root if root is not None else project_root()
    return Path(base) / "data" / "gsm"


def spec_path(fixture: str, root: Path | None = None) -> Path:
    return gsm_dir(root) / fixture / "registry.json"


def load_specs(path: Path) -> list[GsmSpec]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("specs") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    return [GsmSpec.model_validate(item) for item in rows]


def save_specs(path: Path, specs: list[GsmSpec], extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"specs": [s.model_dump() for s in specs]}
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
