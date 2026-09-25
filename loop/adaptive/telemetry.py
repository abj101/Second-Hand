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


@dataclass(frozen=True)
class NativeShadowRow:
    """Shadow-react telemetry; no primitive plans or model chain-of-thought."""

    episode_id: str
    episode_generation: int
    frame_index: int
    schema_revision_id: str
    snapshot_id: str
    observation_age_s: float
    capture_integrity: str
    hwnd: int
    pid: int
    focused: bool
    directive_revision: int
    catalog_revision: str
    mask_digest: str
    mask_allowed_ids: tuple[str, ...]
    decision_source: str
    selected_action_id: str | None
    deterministic_action_id: str | None
    dispatch_accepted: bool
    dispatch_reason: str
    primitive_plan_digest: str | None
    policy_turn_id: int
    latency_policy_s: float
    latency_e2e_s: float
    frame_digest: str = ""
    registry_ids: tuple[str, ...] = ()
    offer_ids: tuple[str, ...] = ()
    effect_happened: bool | None = None
    candidate_scores: dict[str, float] | None = None
    candidate_labels: dict[str, str] | None = None
    ocr_items: tuple[dict, ...] = ()
    directive_intent: str = ""
    directive_forbidden_ids: tuple[str, ...] = ()
    directive_attention: tuple[str, ...] = ()
    orchestrator_events: tuple[dict, ...] = ()
    capture_ts_monotonic: float = 0.0
    publication_ts_monotonic: float = 0.0
    capture_age_known: bool = False
    acquisition_origin: str = "unknown"
    observation_sequence: int | None = None
    capture_to_row_s: float | None = None
    frame_reused: bool | None = None
    cache_hits: int | None = None
    measurement_digest: str | None = None
    screen_replaced: bool | None = None
    source_diagnostics: dict | None = None


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
        self._shadow_rows: list[NativeShadowRow] = []
        self._capture_to_lease_s: list[float] = []
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

    def append_shadow(self, row: NativeShadowRow, *, frame: bytes | None = None) -> None:
        self._shadow_rows.append(row)
        line = json.dumps(asdict(row), sort_keys=True)
        shadow_path = self.path.with_name("shadow_react.jsonl")
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        with shadow_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if frame is not None:
            self.put_frame(row.frame_digest, frame)

    def rows(self) -> list[DecisionRow]:
        return list(self._rows)

    def shadow_rows(self) -> list[NativeShadowRow]:
        return list(self._shadow_rows)

    def record_capture_to_lease(self, latency_s: float) -> None:
        if latency_s >= 0.0:
            self._capture_to_lease_s.append(latency_s)

    def capture_to_lease_latencies(self) -> list[float]:
        return list(self._capture_to_lease_s)


QUALIFICATION_WARMUP_FRAMES = 3


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _pairwise_jaccard_mean(sets: list[frozenset[str]]) -> float:
    if len(sets) < 2:
        return 1.0
    scores = [_jaccard(sets[i], sets[i + 1]) for i in range(len(sets) - 1)]
    return sum(scores) / len(scores)


def _ocr_node_ids(frame: dict) -> tuple[str, ...]:
    if "ocr_node_ids" in frame:
        return tuple(frame["ocr_node_ids"])
    return tuple(frame.get("field_keys") or ())


def _ocr_set_continuity(frames: list[dict]) -> float:
    if not frames:
        return 0.0
    tail = frames
    first_keys: tuple[str, ...] | None = None
    hits = 0
    for frame in tail:
        keys = _ocr_node_ids(frame)
        if keys and first_keys is None:
            first_keys = keys
        if first_keys and keys == first_keys:
            hits += 1
    return hits / len(tail) if tail and first_keys else 0.0


def _valid_promoted_value(value: object) -> bool:
    return value is not None and value != ""


def _promoted_field_coverage_block(
    frames: list[dict],
    schema_field_ids: tuple[str, ...],
) -> dict:
    if not schema_field_ids:
        return {
            "status": "not_applicable",
            "numerator": 0,
            "denominator": 0,
            "ratio": None,
        }
    denom = len(schema_field_ids)
    numerators: list[int] = []
    for frame in frames:
        values = frame.get("field_values") or {}
        numerators.append(
            sum(1 for fid in schema_field_ids if _valid_promoted_value(values.get(fid)))
        )
    if not numerators:
        return {"status": "ok", "numerator": 0, "denominator": denom, "ratio": 0.0}
    avg_num = sum(numerators) / len(numerators)
    return {
        "status": "ok",
        "numerator": avg_num,
        "denominator": denom,
        "ratio": avg_num / denom,
    }


def _merge_exclusion_maps(frames: list[dict]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for frame in frames:
        for reason, count in (frame.get("exclusion_reasons") or {}).items():
            merged[str(reason)] = merged.get(str(reason), 0) + int(count)
    return merged


def qualification_metrics(
    frames: list[dict],
    *,
    schema_field_ids: tuple[str, ...] = (),
    warmup: int = QUALIFICATION_WARMUP_FRAMES,
    required_target_ids: tuple[str, ...] = (),
) -> dict:
    """Whole-run and post-warm-up qualification metrics from per-frame rows."""

    whole = frames
    post = frames[warmup:] if len(frames) > warmup else list(frames)

    track_sets_whole = [frozenset(f.get("track_ids") or ()) for f in whole]
    track_sets_post = [frozenset(f.get("track_ids") or ()) for f in post]
    track_whole = _pairwise_jaccard_mean(track_sets_whole)
    track_post = _pairwise_jaccard_mean(track_sets_post)

    registry_whole = _skip_empty_pairwise(
        [frozenset(f.get("registry_ids") or ()) for f in whole]
    )
    registry_post = _skip_empty_pairwise(
        [frozenset(f.get("registry_ids") or ()) for f in post]
    )

    eligible_whole = _eligible_surface_block(
        [frozenset(f.get("eligible_offer_ids") or ()) for f in whole]
    )
    eligible_post = _eligible_surface_block(
        [frozenset(f.get("eligible_offer_ids") or ()) for f in post]
    )

    ocr_whole = _ocr_set_continuity(whole)
    ocr_post = _ocr_set_continuity(post)

    rev_ids = {f.get("revision_id") for f in whole}
    surfaces = [frozenset(f.get("surface_action_ids") or ()) for f in whole]
    surf_j = [_jaccard(surfaces[i], surfaces[i + 1]) for i in range(len(surfaces) - 1)]
    action_surface_stability = sum(surf_j) / len(surf_j) if surf_j else 1.0

    ambiguity_whole = sum(int(f.get("ambiguity_count") or 0) for f in whole)
    ambiguity_post = sum(int(f.get("ambiguity_count") or 0) for f in post)

    return {
        "warmup_frames": warmup,
        "track_stability": track_whole,
        "raw_region_track_stability": track_whole,
        "field_continuity": ocr_whole,
        "ocr_node_set_continuity": {
            "whole_run": ocr_whole,
            "post_warmup": ocr_post,
        },
        "control_identity_stability": {
            "whole_run": registry_whole,
            "post_warmup": registry_post,
        },
        "eligible_surface_stability": {
            "whole_run": eligible_whole["ratio"],
            "post_warmup": eligible_post["ratio"],
            "whole_run_nonempty_frames": eligible_whole["nonempty_frames"],
            "post_warmup_nonempty_frames": eligible_post["nonempty_frames"],
            "whole_run_empty_frames": eligible_whole["empty_frames"],
            "post_warmup_empty_frames": eligible_post["empty_frames"],
        },
        "required_target_coverage": {
            "whole_run": _required_target_coverage(whole, required_target_ids),
            "post_warmup": _required_target_coverage(post, required_target_ids),
        },
        "ambiguity_count": {
            "whole_run": ambiguity_whole,
            "post_warmup": ambiguity_post,
        },
        "exclusion_reasons": {
            "whole_run": _merge_exclusion_maps(whole),
            "post_warmup": _merge_exclusion_maps(post),
        },
        "promoted_field_coverage": {
            "whole_run": _promoted_field_coverage_block(whole, schema_field_ids),
            "post_warmup": _promoted_field_coverage_block(post, schema_field_ids),
        },
        "mode_ids": sorted({m for f in whole for m in (f.get("mode_id"),) if m is not None}),
        "revision_churn": max(0, len(rev_ids) - 1),
        "action_surface_stability": action_surface_stability,
        "restart_not_applicable": True,
        "raw_region_track_stability_post_warmup": track_post,
    }


ADMISSION_MIN_TRUSTED_FRAMES = 30
ADMISSION_STABILITY_MIN = 0.95


def _post_warmup_ratio(metrics: dict, key: str) -> float | None:
    block = metrics.get(key)
    if not isinstance(block, dict) or "post_warmup" not in block:
        return None
    val = block["post_warmup"]
    if val is None:
        return None
    return float(val)


def _skip_empty_pairwise(sets: list[frozenset[str]]) -> float:
    nonempty = [item for item in sets if item]
    if len(nonempty) >= 2:
        return _pairwise_jaccard_mean(nonempty)
    if len(nonempty) == 1:
        return 1.0
    return _pairwise_jaccard_mean(sets)


def _eligible_surface_block(sets: list[frozenset[str]]) -> dict:
    """Stability of a useful offer surface. Empty frames are not a stable pass."""

    nonempty = [item for item in sets if item]
    if not nonempty:
        ratio = None
    elif len(nonempty) == 1:
        ratio = 1.0
    else:
        ratio = _pairwise_jaccard_mean(nonempty)
    return {
        "ratio": ratio,
        "nonempty_frames": len(nonempty),
        "empty_frames": len(sets) - len(nonempty),
    }


def _required_target_coverage(
    frames: list[dict],
    required_target_ids: tuple[str, ...],
) -> dict:
    """Population coverage from evaluation labels. Absent labels stay unavailable."""

    if not required_target_ids:
        return {
            "status": "unavailable",
            "numerator": None,
            "denominator": None,
            "ratio": None,
        }
    if not frames:
        return {
            "status": "ok",
            "numerator": 0,
            "denominator": 0,
            "ratio": 0.0,
        }
    denom = len(required_target_ids) * len(frames)
    hits = 0
    for frame in frames:
        present = set(frame.get("registry_ids") or ()) | set(
            frame.get("eligible_offer_ids") or ()
        )
        hits += sum(1 for rid in required_target_ids if rid in present)
    return {
        "status": "ok",
        "numerator": hits,
        "denominator": denom,
        "ratio": hits / denom if denom else 0.0,
    }


def qualification_passed(
    metrics: dict,
    *,
    trusted_frames: int,
    identities: list,
) -> tuple[bool, str]:
    """Admission and qualification share this bar. The old 0.85 track gate is gone."""

    if int(trusted_frames) < ADMISSION_MIN_TRUSTED_FRAMES:
        return False, "trusted_frames"
    eligible_block = metrics.get("eligible_surface_stability")
    if isinstance(eligible_block, dict):
        nonempty = eligible_block.get("post_warmup_nonempty_frames")
        if nonempty is not None and int(nonempty) < 1:
            return False, "empty_stable_surface"
    coverage = metrics.get("required_target_coverage") or {}
    post_cov = coverage.get("post_warmup") if isinstance(coverage, dict) else None
    if isinstance(post_cov, dict) and post_cov.get("status") == "ok":
        ratio = post_cov.get("ratio")
        if ratio is None or float(ratio) < ADMISSION_STABILITY_MIN:
            return False, "required_target_coverage"
    control = _post_warmup_ratio(metrics, "control_identity_stability")
    eligible = _post_warmup_ratio(metrics, "eligible_surface_stability")
    if (
        control is None
        or eligible is None
        or control < ADMISSION_STABILITY_MIN
        or eligible < ADMISSION_STABILITY_MIN
    ):
        return False, "stability"
    ambiguity = metrics.get("ambiguity_count") or {}
    whole = int(ambiguity.get("whole_run") or 0) if isinstance(ambiguity, dict) else int(ambiguity or 0)
    if whole != 0:
        return False, "ambiguous_bindings"
    if not identities:
        return False, "target_identity"
    keys = [json.dumps(item, sort_keys=True, default=str) for item in identities]
    if len(set(keys)) != 1:
        return False, "target_identity"
    return True, "ok"
