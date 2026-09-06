from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loop.log.schema import EpisodeLog, SessionSummary, StepLog


def registry_tool_names(tool_events: list[dict[str, Any]]) -> list[str]:
    """Tool names from registry-call events only (not collector audits)."""
    return [
        str(event["tool"])
        for event in tool_events
        if event.get("kind") == "mcp_registry" or event.get("provenance") == "mcp_registry"
    ]


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_run_dir(fixture: str, root: Path) -> Path:
    path = root / "runs" / f"{utc_stamp()}_{fixture}_collect"
    path.mkdir(parents=True, exist_ok=True)
    (path / "frames").mkdir(exist_ok=True)
    return path


def iter_fixture_runs(fixture: str, root: Path) -> list[Path]:
    runs = root / "runs"
    if not runs.exists():
        return []
    return sorted(
        (p for p in runs.iterdir() if p.is_dir() and p.name.endswith(f"_{fixture}_collect")),
        key=lambda p: p.name,
        reverse=True,
    )


def latest_run_dir(fixture: str, root: Path) -> Path | None:
    candidates = iter_fixture_runs(fixture, root)
    return candidates[0] if candidates else None


def resolve_run_dirs(
    fixture: str,
    root: Path,
    run_dirs: list[Path] | None = None,
    *,
    all_runs: bool = False,
) -> list[Path]:
    if run_dirs:
        return [path.resolve() for path in run_dirs]
    if all_runs:
        return iter_fixture_runs(fixture, root)
    latest = latest_run_dir(fixture, root)
    return [latest] if latest is not None else []


class EpisodeStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "frames").mkdir(exist_ok=True)
        self.steps_path = run_dir / "turns.jsonl"
        self.steps_path.touch(exist_ok=True)

    def frame_path(self, episode_id: str, step_id: int, kind: str = "obs") -> Path:
        dest = self.run_dir / "frames" / episode_id
        dest.mkdir(parents=True, exist_ok=True)
        return dest / f"{step_id:06d}_{kind}.jpg"

    def write_step(self, record: StepLog) -> None:
        with self.steps_path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def write_episode(self, record: EpisodeLog) -> None:
        path = self.run_dir / "episodes.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def write_summary(self, summary: SessionSummary) -> Path:
        path = self.run_dir / "summary.json"
        path.write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def read_steps(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.steps_path)

    def read_episodes(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.run_dir / "episodes.jsonl")

    def rewrite_steps(self, records: list[StepLog]) -> None:
        with self.steps_path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(record.model_dump_json() + "\n")

    def replace_episode_steps(self, episode_id: str, records: list[StepLog]) -> None:
        kept = [
            StepLog.model_validate(row)
            for row in self.read_steps()
            if str(row.get("episode_id") or "") != episode_id
        ]
        self.rewrite_steps(kept + records)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
