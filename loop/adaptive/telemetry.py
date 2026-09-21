"""Append-only JSONL telemetry for react episodes (native and benchmark adapters)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from loop.adaptive.snapshot import assert_no_privileged


@dataclass(frozen=True)
class DecisionRow:
    episode_id: str
    episode_generation: int
    seed: int
    step: int
    environment_revision: str
    action_catalog_revision: str
    frame_digest: str
    frame_history_digests: tuple[str, ...]
    schema_revision_id: str
    snapshot: dict
    perception_confidence: float
    observation_age_s: float
    directive: dict
    mask_allowed_ids: tuple[str, ...]
    candidates: tuple[dict, ...]
    decision: dict
    selected_action_id: str | None
    dispatched_action_id: str | None
    executed_action_id: str | None
    next_snapshot: dict | None
    score: float
    score_delta: float
    reward: float
    terminated: bool
    truncated: bool
    termination_reason: str | None
    capture_ts: float
    dispatch_ts: float
    latency_perception_s: float
    latency_policy_s: float
    latency_e2e_s: float


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ordered = sorted(xs)
    k = (len(ordered) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


class TelemetryStore:
    def __init__(self, path: Path, *, workspace: Path | None = None) -> None:
        self.path = path
        self.workspace = workspace
        self._rows: list[DecisionRow] = []
        if workspace is not None:
            (workspace / "frames").mkdir(parents=True, exist_ok=True)

    def put_frame(self, digest: str, data: bytes) -> None:
        if self.workspace is None or not digest:
            return
        dest = self.workspace / "frames" / f"{digest}.bin"
        dest.write_bytes(data)

    def append(self, row: DecisionRow) -> None:
        assert_no_privileged(row.snapshot)
        if row.next_snapshot is not None:
            assert_no_privileged(row.next_snapshot)
        assert_no_privileged(row.decision)
        self._rows.append(row)
        line = json.dumps(asdict(row), sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def rows(self) -> list[DecisionRow]:
        return list(self._rows)
