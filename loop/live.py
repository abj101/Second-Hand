"""File-backed live status for the collect-loop operator UI.

Workers (collect, curate, perceive, analyze) write `data/loop/live.json`.
The UI polls it. Cooperative stop is `data/loop/stop`.
Override the root with `AVATAR_LIVE_ROOT` (tests).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loop.config import project_root

LIVE_NAME = "live.json"
STOP_NAME = "stop"


def resolve_root(root: Path | None = None) -> Path:
    if root is not None:
        return root
    env = os.environ.get("AVATAR_LIVE_ROOT")
    if env:
        return Path(env)
    return project_root()


def live_dir(root: Path | None = None) -> Path:
    path = resolve_root(root) / "data" / "loop"
    path.mkdir(parents=True, exist_ok=True)
    return path


def live_path(root: Path | None = None) -> Path:
    return live_dir(root) / LIVE_NAME


def stop_path(root: Path | None = None) -> Path:
    return live_dir(root) / STOP_NAME


def request_stop(root: Path | None = None) -> None:
    path = stop_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1\n", encoding="utf-8")


def clear_stop(root: Path | None = None) -> None:
    path = stop_path(root)
    if path.exists():
        path.unlink()


def stop_requested(root: Path | None = None) -> bool:
    return stop_path(root).exists()


def reset_live(root: Path | None = None, *, message: str = "") -> dict[str, Any]:
    """Replace live.json with a clean idle blob (no merged collect fields)."""
    payload = {
        "job": "idle",
        "phase": "idle",
        "thought": "",
        "message": message,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    path = live_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    tmp.replace(path)
    return payload


def publish(root: Path | None = None, **fields: Any) -> dict[str, Any]:
    """Merge `fields` onto the current live blob and write atomically."""
    current = read(root)
    for key, value in fields.items():
        if value is not None:
            current[key] = value
    current["ts"] = datetime.now(timezone.utc).isoformat()
    path = live_path(root)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)
    return current


def read(root: Path | None = None) -> dict[str, Any]:
    path = live_path(root)
    if not path.exists():
        return {"job": "idle", "phase": "idle", "thought": "", "message": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"job": "idle", "phase": "idle", "thought": "", "message": ""}
    return payload if isinstance(payload, dict) else {"job": "idle", "phase": "idle"}


def list_runs(root: Path | None = None, *, limit: int = 40) -> list[Path]:
    runs = resolve_root(root) / "runs"
    if not runs.exists():
        return []
    dirs = [
        p
        for p in runs.iterdir()
        if p.is_dir() and p.name.endswith("_collect")
    ]
    dirs.sort(key=lambda p: p.name, reverse=True)
    return dirs[:limit]


def tail_text(path: Path, *, lines: int = 40) -> str:
    if not path.exists() or path.stat().st_size == 0:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(rows[-lines:])
