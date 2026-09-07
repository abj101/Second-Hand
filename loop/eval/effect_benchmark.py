"""Offline scorer for perception-action effect feedback.

Scores frozen step sequences without a live window. Mirrors production
``_record_dispatch_effect`` semantics: dispatch ok with no pixel change is
unverified; hover or global-tiny tooltip motion stays inconclusive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from loop.config import project_root
from loop.eval.grounding_benchmark import box_iou

EffectKind = Literal["verified", "unverified", "inconclusive", "none"]

_IDENTITY_IOU = 0.5


def _bbox(row: dict[str, Any]) -> list[float] | None:
    bbox = row.get("bbox")
    if not bbox or len(bbox) < 4:
        return None
    return [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]


def same_object_identity(left: dict[str, Any], right: dict[str, Any], *, iou_threshold: float = _IDENTITY_IOU) -> bool:
    """Match targets by stable object_id; never by temporary mark numbers."""
    left_id = str(left.get("object_id") or "").strip()
    right_id = str(right.get("object_id") or "").strip()
    if left_id and right_id:
        return left_id == right_id
    left_bbox = _bbox(left)
    right_bbox = _bbox(right)
    if left_bbox is None or right_bbox is None:
        return False
    return box_iou(left_bbox, right_bbox) >= iou_threshold


def unchanged_evidence(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Screen and evidence fingerprint stayed the same between attempts."""
    if str(left.get("screen") or "") != str(right.get("screen") or ""):
        return False
    left_fp = str(left.get("evidence_fingerprint") or left.get("observation_id") or "")
    right_fp = str(right.get("evidence_fingerprint") or right.get("observation_id") or "")
    return bool(left_fp) and left_fp == right_fp


def classify_step_effect(row: dict[str, Any]) -> EffectKind:
    """Per-step effect label aligned with production dispatch/effect reporting."""
    dispatch = str(row.get("dispatch") or "")
    executed = list(row.get("executed") or [])
    pixel_changed = bool(row.get("pixel_changed"))
    hover = bool(row.get("hover"))
    screen_changed = bool(row.get("screen_changed"))
    tiny = bool(row.get("global_tiny_change")) or (
        hover and pixel_changed and not screen_changed
    )

    dispatch_error = dispatch not in {"", "ok"}
    if hover and (not pixel_changed or tiny):
        return "inconclusive"
    if pixel_changed:
        return "verified"
    if not dispatch_error and executed:
        return "unverified"
    return "none"


def score_effect_step(row: dict[str, Any]) -> dict[str, Any]:
    effect = classify_step_effect(row)
    dispatch = str(row.get("dispatch") or "")
    dispatch_ok = dispatch in {"", "ok"}
    code: str | None = None
    if effect == "unverified":
        code = "effect-unverified"
    elif not dispatch_ok:
        code = dispatch
    return {
        "id": row.get("id"),
        "sequence_id": row.get("sequence_id"),
        "observation_id": row.get("observation_id"),
        "screen": row.get("screen"),
        "object_id": row.get("object_id"),
        "dispatch": "ok" if dispatch_ok else dispatch,
        "effect": effect,
        "code": code,
        "pixel_changed": bool(row.get("pixel_changed")),
        "hover": bool(row.get("hover")),
        "global_tiny_change": bool(row.get("global_tiny_change")),
    }


def score_effect_sequence(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Score one ordered sequence and detect cycle vs distinct-retry patterns."""
    scored = [score_effect_step(row) for row in steps]
    cycle_repeat = False
    cycle_at: int | None = None
    unresolved_recommended = False
    unresolved_at: int | None = None

    for index in range(1, len(steps)):
        prev_row = steps[index - 1]
        curr_row = steps[index]
        prev_effect = scored[index - 1]["effect"]
        curr_effect = scored[index]["effect"]
        if prev_effect != "unverified" or curr_effect != "unverified":
            continue
        if same_object_identity(prev_row, curr_row) and unchanged_evidence(prev_row, curr_row):
            cycle_repeat = True
            cycle_at = index
            continue
        if not same_object_identity(prev_row, curr_row) and unchanged_evidence(prev_row, curr_row):
            unresolved_recommended = True
            unresolved_at = index

    return {
        "sequence_id": steps[0].get("sequence_id") if steps else None,
        "steps": scored,
        "cycle_repeat": cycle_repeat,
        "cycle_at": cycle_at,
        "unresolved_recommended": unresolved_recommended,
        "unresolved_at": unresolved_at,
        "transfer_gate": "pending",
    }


def load_effect_replay(path: Path | None = None) -> list[dict[str, Any]]:
    dest = path or (project_root() / "loop" / "fixtures" / "sts2_screens" / "effect_replay.jsonl")
    rows: list[dict[str, Any]] = []
    for line in dest.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def run_effect_benchmark(labels_path: Path | None = None) -> dict[str, Any]:
    """Load JSONL step rows, group by sequence_id, and score each sequence."""
    rows = load_effect_replay(labels_path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        seq = str(row.get("sequence_id") or row.get("id") or "default")
        if seq not in grouped:
            grouped[seq] = []
            order.append(seq)
        grouped[seq].append(row)

    sequences = [score_effect_sequence(grouped[seq]) for seq in order]
    return {
        "path": str(labels_path or (project_root() / "loop" / "fixtures" / "sts2_screens" / "effect_replay.jsonl")),
        "sequences": sequences,
        "sequence_count": len(sequences),
        "step_count": len(rows),
        "cycle_repeats": sum(1 for seq in sequences if seq["cycle_repeat"]),
        "unresolved_recommended": sum(1 for seq in sequences if seq["unresolved_recommended"]),
        "transfer_gate": "pending",
    }
