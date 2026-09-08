from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from avatar.config import project_root
from avatar.schema import TurnLog


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_run_dir(stage: str, fixture: str) -> Path:
    path = project_root() / "runs" / f"{utc_stamp()}_{fixture}_{stage}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "frames").mkdir(exist_ok=True)
    (path / "scout").mkdir(exist_ok=True)
    return path


class JsonlLogger:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.path = run_dir / "turns.jsonl"
        self.path.touch(exist_ok=True)

    def write(self, record: TurnLog | dict[str, Any]) -> None:
        payload = record.model_dump() if isinstance(record, TurnLog) else record
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")

    def read(self) -> list[dict[str, Any]]:
        rows = []
        if not self.path.exists():
            return rows
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)
