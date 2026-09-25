"""Offline replay of annotated frames through fused perception arms. No downloads."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from PIL import Image

from loop.adaptive.evidence import iou
from loop.adaptive.orchestrate import (
    MAX_OFFER_AREA_FRACTION,
    _bbox_area_fraction,
    offer_decisions,
    registry_offers,
)
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.tracker import open_tracker

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / ".tmp" / "fusion_eval"
SIZE = (1261, 720)
ARMS = ("none", "fused-gpa", "fused", "fused-base")


def center(bbox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)


def inside(bbox, pt) -> bool:
    return bbox[0] <= pt[0] <= bbox[2] and bbox[1] <= pt[1] <= bbox[3]


def _match_score(gt: dict, pred: dict) -> float:
    if gt.get("role") and pred.get("role") and gt["role"] != pred["role"]:
        return 0.0
    kind = gt.get("kind")
    if kind == "control":
        return 1.0 if inside(gt["bbox"], center(pred["bbox"])) else 0.0
    score = iou(tuple(gt["bbox"]), tuple(pred["bbox"]))
    need = 0.3 if kind == "hud" else 0.5
    return score if score >= need else 0.0


def _box_score(gt: dict, pred: dict) -> float:
    if gt.get("role") and pred.get("role") and gt["role"] != pred["role"]:
        return 0.0
    score = iou(tuple(gt["bbox"]), tuple(pred["bbox"]))
    return score if score >= (0.3 if gt.get("kind") == "hud" else 0.5) else 0.0


def _stable_key(obj: dict) -> tuple:
    """Order-independent tie-break key. Identical keys mean truly interchangeable."""

    bbox = obj.get("bbox") or ()
    try:
        box_key: tuple = tuple(float(v) for v in bbox)
    except (TypeError, ValueError):
        box_key = ()
    return (str(obj.get("id") or ""), box_key)


def _deterministic_assignment(
    expected: list[dict], predicted: list[dict], score_fn
) -> tuple[list[tuple[int, int, float]], list[tuple[float, int, int]]]:
    """Global greedy one-to-one assignment, independent of input order.

    Candidate pairs are ranked by descending score with a stable id/box
    tie-break, so overlapping or reordered boxes cannot silently swap. The
    second return value holds every score tie that shares a row or column, i.e.
    assignments that are not uniquely determined by the evidence.
    """

    pairs: list[tuple[float, int, int]] = []
    for gi, gt in enumerate(expected):
        for pi, pred in enumerate(predicted):
            score = score_fn(gt, pred)
            if score > 0:
                pairs.append((score, gi, pi))
    pairs.sort(key=lambda item: (-item[0], _stable_key(expected[item[1]]), _stable_key(predicted[item[2]])))
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    chosen: list[tuple[int, int, float]] = []
    for score, gi, pi in pairs:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        chosen.append((gi, pi, score))
    ambiguous: list[tuple[float, int, int]] = []
    for a in range(len(pairs)):
        for b in range(a + 1, len(pairs)):
            sa, ga, pa = pairs[a]
            sb, gb, pb = pairs[b]
            if sa == sb and (ga == gb or pa == pb):
                ambiguous.append((sa, ga, pa))
    chosen.sort(key=lambda item: item[0])
    return chosen, ambiguous


def match_objects(expected: list[dict], predicted: list[dict]) -> dict:
    """Deterministic one-to-one greedy match.

    Small controls use center-hit; cards/actors IoU>=0.5. Candidate pairs are
    ranked globally, so overlapping/order-ambiguous boxes do not depend on the
    order annotations or predictions arrive in (assignment stays an
    approximation, not an optimal Hungarian solve). Score ties that share a row
    or column are reported as ``ambiguous`` rather than silently resolved.
    """

    chosen, ambiguous = _deterministic_assignment(expected, predicted, _match_score)
    used_pred = {pi for _gi, pi, _s in chosen}
    hits = [
        {"gt": expected[gi]["id"], "pred": predicted[pi].get("id"), "score": round(score, 3)}
        for gi, pi, score in chosen
    ]
    matched_gt = {gi for gi, _pi, _s in chosen}
    misses = [expected[i]["id"] for i in range(len(expected)) if i not in matched_gt]
    extras = [predicted[i].get("id") for i in range(len(predicted)) if i not in used_pred]
    duplicate_ids = [
        predicted[i].get("id")
        for i in range(len(predicted))
        if i not in used_pred and any(_match_score(gt, predicted[i]) > 0 for gt in expected)
    ]
    return {
        "hits": hits,
        "misses": misses,
        "extras": extras,
        "duplicates": len(duplicate_ids),
        "duplicate_ids": duplicate_ids,
        "ambiguous": bool(ambiguous),
        "ambiguous_pairs": [
            {"gt": expected[gi]["id"], "pred": predicted[pi].get("id"), "score": round(score, 3)}
            for score, gi, pi in ambiguous
        ],
    }


def localization_diagnostics(expected: list[dict], predicted: list[dict]) -> dict:
    """Report control safe-point hits separately from actor/card box overlap."""
    result = {}
    for kind, metric in (("control", "safe_point"), ("actor", "full_box"), ("card", "full_box")):
        gt = [obj for obj in expected if obj.get("kind") == kind]
        role = "world" if kind == "actor" else "ui"
        preds = [obj for obj in predicted if not obj.get("role") or obj.get("role") == role]
        score_fn = _match_score if metric == "safe_point" else _box_score
        chosen, _ambiguous = _deterministic_assignment(gt, preds, score_fn)
        hits = len(chosen)
        result[kind] = {"metric": metric, "expected": len(gt), "matched": hits,
                        "missed": len(gt) - hits}
    return result


_TEMPORAL_KEYS = (
    "expected_visible_opportunities",
    "misses",
    "correct_associations",
    "incorrect_associations",
    "switches",
    "collisions",
    "fragmentation",
    "returns",
    "returns_with_id_change",
    "reset_collisions",
    "reset_boundaries",
)


def _sequence_problems(annotations: dict) -> tuple[list[dict] | None, list[str]]:
    """Reviewed sequence metadata: explicit order, boundaries and physical ids."""

    sequences = annotations.get("sequences")
    if not isinstance(sequences, list) or not sequences:
        return None, [
            "annotations.sequences missing (explicit order + boundary + physical_id required)"
        ]
    problems: list[str] = []
    frames_specs = annotations.get("frames") or {}
    for seq in sequences:
        sid = seq.get("sequence_id") if isinstance(seq, dict) else None
        if not sid:
            problems.append("sequence missing sequence_id")
        if not isinstance(seq.get("frames"), list) or len(seq["frames"]) < 2:
            problems.append(f"{sid or '?'}: needs an ordered frames list of >=2 frames")
        if not seq.get("boundary"):
            problems.append(f"{sid or '?'}: missing explicit sequence boundary")
        for frame_id in seq.get("frames") or []:
            spec = frames_specs.get(frame_id)
            if spec is None:
                problems.append(f"{sid or '?'}: unknown frame {frame_id}")
                continue
            for obj in spec.get("objects") or []:
                if not obj.get("physical_id"):
                    problems.append(f"{frame_id}: {obj.get('id')} missing reviewed physical_id")
    return sequences, problems


def _empty_temporal_totals() -> dict:
    return {key: None for key in _TEMPORAL_KEYS}


def temporal_sequence_metrics(annotations: dict, rows) -> dict:
    """Reviewed-identity temporal association metrics over explicit sequences.

    Requires coordinator-reviewed metadata: a top-level ``sequences`` list with
    explicit ordered frame ids and a boundary, plus a ``physical_id`` on every
    annotated object. Any of that absent => fail closed: ``available`` False and
    every count None (unavailable, not zero). The frozen 3-frame annotations
    have no sequences, so the historical report is unavailable, not measured.

    Reported separately: expected visible opportunities (the denominator for
    misses), misses over all expected adjacent visible pairs, correct and
    incorrect associations, switches, collisions, fragmentation, returns,
    returns with a changed id, and reset collisions across boundaries.
    """

    frames_specs = annotations.get("frames") or {}
    sequences, problems = _sequence_problems(annotations)
    rows_by_frame = {
        row["frame"]: row for row in rows or [] if isinstance(row, dict) and row.get("frame")
    }
    if sequences is not None:
        for seq in sequences:
            for frame_id in seq.get("frames") or []:
                if frame_id in frames_specs and frame_id not in rows_by_frame:
                    problems.append(f"{frame_id}: missing observation row")
    if problems or sequences is None:
        return {
            "available": False,
            "reason": "; ".join(dict.fromkeys(problems)) or "no reviewed sequences",
            "sequences": {},
            "totals": _empty_temporal_totals(),
        }

    physical = {
        frame_id: {
            obj["id"]: obj.get("physical_id")
            for obj in (frames_specs.get(frame_id) or {}).get("objects") or []
        }
        for frame_id in {fid for seq in sequences for fid in seq["frames"]}
    }
    observed: dict[str, dict[str, dict[str, str]]] = {}
    for seq in sequences:
        sid = seq["sequence_id"]
        per_frame: dict[str, dict[str, str]] = {}
        for frame_id in seq["frames"]:
            hits = (rows_by_frame[frame_id].get("matched") or {}).get("hits") or []
            per_frame[frame_id] = {
                physical[frame_id][hit["gt"]]: hit["pred"]
                for hit in hits
                if physical[frame_id].get(hit.get("gt")) and hit.get("pred") is not None
            }
        observed[sid] = per_frame

    per_sequence: dict[str, dict] = {}
    for seq in sequences:
        sid = seq["sequence_id"]
        frame_ids = list(seq["frames"])
        per_frame = observed[sid]
        collisions = sum(
            1
            for frame_id in frame_ids
            for count in Counter(per_frame[frame_id].values()).values()
            if count > 1
        )
        expected_pairs = misses = correct = incorrect = switches = 0
        for before, after in zip(frame_ids, frame_ids[1:]):
            shared = set(physical[before].values()) & set(physical[after].values())
            for physical_id in shared:
                expected_pairs += 1
                first = per_frame[before].get(physical_id)
                second = per_frame[after].get(physical_id)
                if first is None or second is None:
                    misses += 1
                elif first == second:
                    correct += 1
                else:
                    incorrect += 1
                    switches += 1
        distinct: dict[str, set[str]] = {}
        positions: dict[str, list[int]] = {}
        for position, frame_id in enumerate(frame_ids):
            for physical_id, track_id in per_frame[frame_id].items():
                distinct.setdefault(physical_id, set()).add(track_id)
                positions.setdefault(physical_id, []).append(position)
        returns = returns_with_id_change = 0
        for physical_id, seen in positions.items():
            for first, second in zip(seen, seen[1:]):
                if second > first + 1:
                    returns += 1
                    before = per_frame[frame_ids[first]][physical_id]
                    after = per_frame[frame_ids[second]][physical_id]
                    if before != after:
                        returns_with_id_change += 1
        per_sequence[sid] = {
            "boundary": seq["boundary"],
            "frames": frame_ids,
            "expected_visible_opportunities": expected_pairs,
            "misses": misses,
            "correct_associations": correct,
            "incorrect_associations": incorrect,
            "switches": switches,
            "collisions": collisions,
            "fragmentation": sum(1 for ids in distinct.values() if len(ids) > 1),
            "returns": returns,
            "returns_with_id_change": returns_with_id_change,
        }

    reset_boundaries = max(0, len(sequences) - 1)
    prior: dict[str, str] = {}
    reset_collisions = 0
    for seq in sequences:
        current = {
            track_id: physical_id
            for mapping in observed[seq["sequence_id"]].values()
            for physical_id, track_id in mapping.items()
        }
        for track_id, physical_id in current.items():
            previous = prior.get(track_id)
            if previous is not None and previous != physical_id:
                reset_collisions += 1
        prior.update(current)

    totals = {key: 0 for key in _TEMPORAL_KEYS}
    for metrics in per_sequence.values():
        for key in totals:
            if key in metrics:
                totals[key] += metrics[key]
    totals["reset_boundaries"] = reset_boundaries
    totals["reset_collisions"] = reset_collisions
    return {"available": True, "reason": None, "sequences": per_sequence, "totals": totals}


def _expected_frame_bytes(frame_size) -> int | None:
    if not frame_size or len(frame_size) != 2:
        return None
    return int(frame_size[0]) * int(frame_size[1]) * 3


def _frame_identity_problems(spec: dict, *, frame_size, strict: bool) -> list[str]:
    """Validate frozen frame digest/size. Empty list means verified or unchecked.

    Non-strict fixtures without a digest are left unverified and labelled as
    mock by the caller; strict (qualified) runs fail closed when the required
    digest or size is absent or does not match the bytes on disk.
    """

    expected_sha = str(spec.get("sha256") or "").strip().lower()
    expected_size = frame_size or spec.get("frame_size")
    problems: list[str] = []
    if strict and not expected_sha:
        problems.append("digest missing")
    if strict and not expected_size:
        problems.append("frame size missing")
    if not expected_sha and not (strict and expected_size):
        return problems
    try:
        raw = Path(spec["path"]).read_bytes()
    except (OSError, KeyError) as exc:
        problems.append(f"frame unreadable: {exc}")
        return problems
    if expected_sha and hashlib.sha256(raw).hexdigest() != expected_sha:
        problems.append("digest mismatch")
    expected_bytes = _expected_frame_bytes(expected_size)
    if expected_bytes is not None and len(raw) != expected_bytes:
        problems.append(f"size mismatch: {len(raw)} != {expected_bytes} bytes")
    return problems


def _oversized_offer_ids(
    offers, node_bboxes, *, ceiling: float = MAX_OFFER_AREA_FRACTION
) -> list[str]:
    """Offer IDs whose issued box exceeds the frozen area ceiling."""

    ids = []
    for offer in offers:
        area = _bbox_area_fraction(tuple(node_bboxes.get(offer.node_id, ())))
        if area is not None and area > ceiling:
            ids.append(offer.offer_id)
    return sorted(ids)


def offer_counters(
    graph, *, offers=None, decisions=None, max_area_fraction: float = MAX_OFFER_AREA_FRACTION
) -> dict:
    """Actual offers vs source rejections, kept separate.

    ``oversized_offers`` counts offers that were really issued while their box
    exceeded the ceiling; rejected oversized candidates appear only in
    ``source_rejections``. Every count is ``None`` (unavailable, not zero) when
    there is no graph/decision diagnostic to read.
    """

    if graph is None or getattr(graph, "nodes", None) is None:
        return {
            "oversized_offers": None,
            "oversized_offer_ids": None,
            "n_offers": None,
            "source_rejections": None,
            "source_withheld": None,
        }
    if offers is None:
        offers = registry_offers(graph, max_area_fraction=max_area_fraction)
    if decisions is None:
        decisions = offer_decisions(graph, max_area_fraction=max_area_fraction)
    node_bboxes = {node.node_id: tuple(node.bbox) for node in graph.nodes}
    oversized_ids = _oversized_offer_ids(offers, node_bboxes, ceiling=max_area_fraction)
    rejected = Counter(item.reason for item in decisions if item.status == "rejected")
    withheld = Counter(item.reason for item in decisions if item.status == "withheld")
    return {
        "oversized_offers": len(oversized_ids),
        "oversized_offer_ids": oversized_ids,
        "n_offers": len(offers),
        "source_rejections": dict(sorted(rejected.items())),
        "source_withheld": dict(sorted(withheld.items())),
    }


def _load_frame(path: Path) -> Image.Image:
    return Image.frombytes("RGB", SIZE, path.read_bytes())


def _preds(scene) -> list[dict]:
    out = []
    for node in scene.nodes:
        rid = node.attributes.get("registry_id")
        if not rid:
            continue
        out.append(
            {
                "id": str(rid),
                "bbox": list(node.bbox),
                "role": node.attributes.get("track_role") or node.attributes.get("registry_owner"),
                "label": node.attributes.get("label"),
                "source": node.attributes.get("track_source") or "",
            }
        )
    return out


def _vram_mib() -> float | None:
    try:
        import torch

        if torch.cuda.is_available():
            return round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
    except Exception:
        return None
    return None


def _cuda_sync() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:
        pass


def _latency_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "samples": len(ordered),
        "p50_ms": round(ordered[len(ordered) // 2], 1),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 1),
        "max_ms": round(ordered[-1], 1),
    }


def replay_arm(name: str, frames: dict, *, frame_size=None, strict_identity: bool = False) -> dict:
    started = time.perf_counter()
    tracker = None if name == "none" else open_tracker(name)
    load_ms = (time.perf_counter() - started) * 1000.0
    status = "none" if tracker is None else str(getattr(tracker, "status", "unavailable"))
    model = getattr(tracker, "name", name)
    if tracker is not None and status != "ok":
        return {
            "arm": name,
            "status": status,
            "load_ms": round(load_ms, 1),
            "error": "unavailable",
            "frames": [],
        }
    assembler = SceneAssembler(tracker)
    from loop.adaptive import cli

    ocr_fn = cli._live_ocr_for_assembler(assembler)
    small_ocr = bool(getattr(tracker, "require_visual_ownership", False))
    ocr_model = "RapidOCR Small" if small_ocr else "RapidOCR Medium"
    rows = []
    lat = []
    latency_by_ocr_state: dict[str, list[float]] = {}
    role_counts = {role: {"expected": 0, "predicted": 0, "matched": 0} for role in ("ui", "world")}
    last_ids: dict[str, tuple[int, str]] = {}
    id_switches = 0
    control_comparisons = 0
    control_retained = 0
    control_expected_pairs = 0
    previous_control_ids: set[str] = set()
    for frame_index, (frame_id, spec) in enumerate(frames.items()):
        identity_problems = _frame_identity_problems(spec, frame_size=frame_size, strict=strict_identity)
        if identity_problems:
            rows.append(
                {
                    "frame": frame_id,
                    "status": "rejected",
                    "frame_identity_problems": identity_problems,
                    "identity_verified": False,
                    "n_pred": 0,
                    "n_offers": 0,
                    "offer_ids": [],
                    "matched": {
                        "hits": [], "misses": [], "extras": [], "duplicates": 0,
                        "duplicate_ids": [], "ambiguous": False, "ambiguous_pairs": [],
                    },
                    "offer_eligibility": {
                        "expected_ids": sorted(
                            obj["id"] for obj in spec.get("objects") or [] if obj.get("offerable")
                        ),
                        "detected_ids": [],
                        "offered_ids": [],
                        "detected_but_withheld_ids": [],
                    },
                    "oversized_offers": None,
                    "oversized_offer_ids": None,
                    "source_rejections": None,
                    "source_withheld": None,
                    "false_offer_ids": [],
                }
            )
            continue
        image = _load_frame(Path(spec["path"]))
        t0 = time.perf_counter()
        rapid_engine = cli._RAPID_SMALL if small_ocr else cli._RAPID
        ocr_state = "cold" if rapid_engine is None else "unavailable" if rapid_engine is False else "warm"
        ocr_time = 0.0
        ocr_calls = 0

        def timed_ocr(crop):
            nonlocal ocr_time, ocr_calls
            started = time.perf_counter()
            result = ocr_fn(crop)
            _cuda_sync()
            ocr_time += (time.perf_counter() - started) * 1000.0
            ocr_calls += 1
            return result

        _recs, scene = assembler.update(image, ocr=timed_ocr)
        _cuda_sync()
        ms = (time.perf_counter() - t0) * 1000.0
        assembly_ms = max(0.0, ms - ocr_time)
        lat.append(ms)
        latency_by_ocr_state.setdefault(ocr_state, []).append(ms)
        preds = _preds(scene)
        matched = match_objects(spec.get("objects") or [], preds)
        expected_by_id = {obj["id"]: obj for obj in spec.get("objects") or []}
        for obj in expected_by_id.values():
            if obj.get("role") in role_counts:
                role_counts[obj["role"]]["expected"] += 1
        for pred in preds:
            if pred.get("role") in role_counts:
                role_counts[pred["role"]]["predicted"] += 1
        for hit in matched["hits"]:
            role = expected_by_id[hit["gt"]].get("role")
            if role in role_counts:
                role_counts[role]["matched"] += 1
            old = last_ids.get(hit["gt"])
            if old is not None and old[1] != hit["pred"]:
                id_switches += 1
            if expected_by_id[hit["gt"]].get("kind") == "control" and old is not None and old[0] == frame_index - 1:
                control_comparisons += 1
                control_retained += old[1] == hit["pred"]
            last_ids[hit["gt"]] = (frame_index, hit["pred"])
        current_control_ids = {
            obj["id"] for obj in expected_by_id.values() if obj.get("kind") == "control"
        }
        control_expected_pairs += len(previous_control_ids & current_control_ids)
        previous_control_ids = current_control_ids
        offers = registry_offers(scene)
        decisions = offer_decisions(scene)
        counters = offer_counters(scene, offers=offers, decisions=decisions)
        matched_offer_ids = {
            hit["pred"]
            for hit in matched["hits"]
            if expected_by_id[hit["gt"]].get("offerable")
        }
        false_offers = [o.offer_id for o in offers if o.offer_id not in matched_offer_ids]
        offer_ids = {o.offer_id for o in offers}
        offerable_ids = {obj["id"] for obj in expected_by_id.values() if obj.get("offerable")}
        matched_by_gt = {hit["gt"]: hit["pred"] for hit in matched["hits"]}
        detected_offerables = offerable_ids & matched_by_gt.keys()
        eligible_offerables = {
            obj_id for obj_id in detected_offerables if matched_by_gt[obj_id] in offer_ids
        }
        rows.append(
            {
                "frame": frame_id,
                "latency_ms": round(ms, 1),
                "stage_ms": {"ocr": round(ocr_time, 1), "assembly_and_sync": round(assembly_ms, 1)},
                "ocr_calls": ocr_calls,
                "ocr_engine_state": ocr_state,
                "n_pred": len(preds),
                "n_offers": len(offers),
                "offer_ids": [o.offer_id for o in offers],
                "matched": matched,
                "localization_diagnostics": localization_diagnostics(spec.get("objects") or [], preds),
                "offer_eligibility": {
                    "expected_ids": sorted(offerable_ids),
                    "detected_ids": sorted(detected_offerables),
                    "offered_ids": sorted(eligible_offerables),
                    "detected_but_withheld_ids": sorted(detected_offerables - eligible_offerables),
                },
                "oversized_offers": counters["oversized_offers"],
                "oversized_offer_ids": counters["oversized_offer_ids"],
                "source_rejections": counters["source_rejections"],
                "source_withheld": counters["source_withheld"],
                "false_offer_ids": false_offers,
                "frame_identity_problems": [],
                "identity_verified": bool(_expected_frame_bytes(frame_size) is not None),
                "card_proposals": sum("card-split" in str(p["source"]) for p in preds),
                "tracker_status": getattr(assembler, "tracker_status", status),
            }
        )
    lat_sorted = sorted(lat)
    p95 = lat_sorted[min(len(lat_sorted) - 1, int(0.95 * len(lat_sorted)))] if lat_sorted else 0.0
    return {
        "arm": name,
        "status": status,
        "model": model,
        "ocr_model": ocr_model,
        "ocr_fallback": "Tesseract when RapidOCR is unavailable or yields no usable tokens",
        "load_ms": round(load_ms, 1),
        "vram_mib": _vram_mib(),
        "latency_p50_ms": round(lat_sorted[len(lat_sorted) // 2], 1) if lat_sorted else 0.0,
        "latency_p95_ms": round(p95, 1),
        "latency_by_ocr_engine_state": {
            state: _latency_summary(values) for state, values in latency_by_ocr_state.items()
        },
        "annotation_coverage": "partial; precision is an annotated-set diagnostic, not population precision",
        "role_metrics": {
            role: {
                **counts,
                "precision": round(counts["matched"] / counts["predicted"], 3)
                if counts["predicted"] else None,
                "recall": round(counts["matched"] / counts["expected"], 3)
                if counts["expected"] else None,
            }
            for role, counts in role_counts.items()
        },
        "id_switches_diagnostic": id_switches,
        "control_id_retention": round(control_retained / control_comparisons, 3)
        if control_comparisons else None,
        "control_id_comparisons": control_comparisons,
        "control_identity_diagnostic": {
            "retained": control_retained,
            "observed_adjacent_pairs": control_comparisons,
            "expected_adjacent_pairs": control_expected_pairs,
            "note": "retention denominator counts only adjacent frames where the control matched in both frames",
        },
        "frames": rows,
    }


def _qwen_resident_profile() -> dict | None:
    path = EVAL / "qwen_resident_e_path_profile.json"
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not profile.get("qwen_resident"):
        return None
    return {
        "source": str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path),
        "model": profile.get("model"),
        "status": profile.get("status"),
        "method": "manual local CUDA load; separate from normal policy loader",
        "qwen_policy_loaded": False,
        "samples": len(profile.get("samples") or []),
        "stage_stats": profile.get("stage_stats", {}),
        "vram_peak_mib": profile.get("vram_peak_mib"),
        "loader_note": profile.get("qwen_loader_note"),
    }


def _annotation_problems(annotations: dict) -> list[str]:
    """Required annotation rows/roles/fields. Empty list means complete."""

    problems: list[str] = []
    specs = annotations.get("frames")
    if not isinstance(specs, dict) or not specs:
        return ["annotation frames missing"]
    for frame_id, spec in specs.items():
        objects = spec.get("objects")
        if not isinstance(objects, list):
            problems.append(f"{frame_id}: missing objects")
            continue
        for obj in objects:
            for field in ("id", "kind", "role", "bbox"):
                if field not in obj:
                    problems.append(f"{frame_id}: object missing {field}")
    return problems


def _required_roles(specs: dict) -> set[str]:
    roles: set[str] = set()
    for spec in specs.values():
        for obj in spec.get("objects") or []:
            if obj.get("role") in ("ui", "world"):
                roles.add(obj["role"])
    return roles


def qualification_verdict(report: dict, annotations: dict) -> dict:
    failures: dict[str, list[str]] = {}
    specs = annotations.get("frames")
    annotation_problems = _annotation_problems(annotations)
    for arm in report["arms"]:
        if arm["arm"] == "none":
            continue
        reasons: list[str] = list(annotation_problems)
        if arm["status"] != "ok":
            reasons.append("detector unavailable")
        if annotation_problems:
            failures[arm["arm"]] = reasons
            continue
        rows = arm.get("frames") or []
        row_ids = {row.get("frame") for row in rows}
        for frame_id in specs:
            if frame_id not in row_ids:
                reasons.append(f"{frame_id}: missing required frame row")
        role_metrics = arm.get("role_metrics") or {}
        for role in sorted(_required_roles(specs) - set(role_metrics)):
            reasons.append(f"role diagnostic missing: {role}")
        for row in rows:
            spec = specs.get(row["frame"])
            if spec is None:
                reasons.append(f"{row['frame']}: row has no annotation")
                continue
            if row.get("frame_identity_problems"):
                reasons.append(
                    f"{row['frame']}: frame identity " + "; ".join(row["frame_identity_problems"])
                )
            matched = row.get("matched") or {}
            if matched.get("ambiguous"):
                reasons.append(f"{row['frame']}: ambiguous object assignment")
            hits = matched.get("hits") or []
            hit_ids = {hit["gt"] for hit in hits}
            expected_cards = {obj["id"] for obj in spec["objects"] if obj.get("kind") == "card"}
            expected_actors = {obj["id"] for obj in spec["objects"] if obj.get("kind") == "actor"}
            expected_controls = {obj["id"] for obj in spec["objects"] if obj.get("offerable")}
            if not expected_cards <= hit_ids:
                reasons.append(f"{row['frame']}: missing matched cards")
            if not expected_actors <= hit_ids:
                reasons.append(f"{row['frame']}: missing matched actors")
            if not expected_controls <= hit_ids:
                reasons.append(f"{row['frame']}: missing actionable controls")
            expected_offer_ids = {
                hit["pred"] for hit in hits if hit["gt"] in expected_controls
            }
            if not expected_offer_ids <= set(row["offer_ids"]):
                reasons.append(f"{row['frame']}: detected controls withheld from offers")
            if matched.get("duplicates"):
                reasons.append(f"{row['frame']}: duplicate objects")
            if row.get("oversized_offers") is None:
                reasons.append(f"{row['frame']}: oversized-offer diagnostic unavailable")
            elif row["oversized_offers"] or row["false_offer_ids"]:
                reasons.append(f"{row['frame']}: oversized or false offers")
        if arm.get("latency_p95_ms", float("inf")) > 1000:
            reasons.append("perception p95 exceeds 1000 ms")
        if len(rows) < 30:
            reasons.append("30-frame identity sequence absent")
        if arm.get("control_id_retention") is None or arm["control_id_retention"] < 0.95:
            reasons.append("control-ID retention below 0.95 or unmeasured")
        if report.get("missing_surfaces"):
            reasons.append("menu/reward/map/animation/replacement surfaces missing")
        temporal = arm.get("temporal")
        if temporal is None:
            temporal = temporal_sequence_metrics(annotations, rows)
            arm["temporal"] = temporal
        if not temporal.get("available"):
            reasons.append("temporal sequence metrics unavailable")
        if not report.get("qwen_resident"):
            reasons.append("Qwen coresident test absent")
        failures[arm["arm"]] = reasons
    winner = next(
        (arm for arm in ("fused-gpa", "fused", "fused-base") if arm in failures and not failures[arm]),
        None,
    )
    return {
        "E": "PASS" if winner else "FAIL",
        "arm_failures": failures,
        "winner": winner,
        "default_tracker": "none",
        "F": "READY" if winner else "BLOCKED_BY_E",
    }


def _git_identity() -> dict:
    def run(*args: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=REPO,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except Exception:
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    status = run("status", "--porcelain")
    diff = run("diff")
    return {
        "commit": run("rev-parse", "HEAD"),
        "dirty": bool(status),
        "status_sha256": hashlib.sha256((status or "").encode()).hexdigest(),
        "diff_sha256": hashlib.sha256((diff or "").encode()).hexdigest(),
    }


def _environment_versions() -> dict:
    import importlib.metadata
    import platform

    versions: dict[str, object] = {"python": sys.version.split()[0], "platform": platform.platform()}
    for name in ("torch", "ultralytics", "transformers", "rapidocr", "onnxruntime", "pillow", "numpy"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def _check_perception_module():
    import importlib.util

    path = REPO / "scripts" / "check_perception.py"
    spec = importlib.util.spec_from_file_location("_check_perception", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _timing_frame_identity(
    frame_id: str, spec: dict, frame_size
) -> tuple[dict, list[str]]:
    """Hash the bytes actually about to be loaded and check declared size/digest."""

    path = Path(spec["path"])
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {"path": str(path), "sha256": None, "bytes": None}, [f"{frame_id}: unreadable ({exc})"]
    problems: list[str] = []
    digest = hashlib.sha256(raw).hexdigest()
    expected = str(spec.get("sha256") or "").strip().lower()
    if expected and expected != digest:
        problems.append(f"{frame_id}: digest mismatch")
    expected_bytes = _expected_frame_bytes(frame_size or spec.get("frame_size"))
    if expected_bytes is None:
        problems.append(f"{frame_id}: frame size unavailable")
    elif expected_bytes != len(raw):
        problems.append(f"{frame_id}: size mismatch: {len(raw)} != {expected_bytes} bytes")
    return {"path": str(path), "sha256": digest, "bytes": len(raw)}, problems


REQUIRED_WARM_SAMPLES = 100  # plan C3 warm-sample denominator; reporting only, not enforced here


def run_timing_harness(
    *,
    frames,
    out_dir,
    arm: str = "fused-gpa",
    repeats: int = 10,
    warmup: int = 2,
    frame_size=None,
    qwen_required: bool = True,
    tracker_factory=None,
    ocr_factory=None,
    qwen_loader=None,
    qwen_probe=None,
    frame_loader=None,
    command=None,
) -> dict:
    """Offline same-process changed-frame timing harness for one fused arm.

    The live OCR callable is handed to ``SceneAssembler`` and the assembler drives
    every invocation (one full-frame read on the cold frame, then region-crop
    reads through the live path). The harness instruments that callable and
    reports the invocations it actually saw; it makes no separate copy of the
    full-frame read and never claims one read per frame.

    Qwen is loaded before warm-up and stays resident; a synthetic multi-offer
    decision is exercised during every timed sample and its calls and latency are
    reported separately. Synthetic offer ids are a coresidency/route probe only,
    never a scene-choice or accuracy result.

    Cold (one frame), fixed warm-up (``warmup`` unmeasured iterations) and timed
    warm samples (``repeats``) are distinct denominators and are reported as
    such. Output always goes to a brand-new directory; the historical
    ``.tmp/fusion_eval`` directory is never a destination.

    Injected components are allowed for tests, but they make ``harness_accounting``
    report ``blocked``. ``lean_ui_qualification`` is a separate verdict and is
    always ``blocked``: this harness is not a Lean UI qualification.

    The process is forced offline (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE /
    HF_HUB_DISABLE_TELEMETRY) before any tracker or Qwen loader is constructed.
    """

    from loop.adaptive import cli

    out_path = Path(out_dir)
    resolved = out_path.resolve()
    if resolved == EVAL.resolve() or EVAL.resolve() in resolved.parents:
        raise ValueError(
            "refusing to write inside the historical .tmp/fusion_eval directory; "
            "use a separate run directory"
        )
    try:
        offline_env = _check_perception_module().force_offline()
    except Exception:
        offline_env = {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
        os.environ.update(offline_env)
    out_path.mkdir(parents=True, exist_ok=False)

    specs = {
        frame_id: dict(value) if isinstance(value, dict) else {"path": str(value)}
        for frame_id, value in (frames or {}).items()
    }
    reasons: list[str] = []
    blockers: list[str] = []
    if len(specs) < 2:
        blockers.append("need >=2 distinct frames for a changed-frame workload")
    if repeats < 1:
        blockers.append("repeats must be >=1")
    if warmup < 1:
        blockers.append("warm-up must be fixed and >=1")

    frame_records: dict[str, dict] = {}
    for frame_id, spec in specs.items():
        record, problems = _timing_frame_identity(frame_id, spec, frame_size)
        frame_records[frame_id] = record
        blockers.extend(problems)

    real_components = {
        "tracker": tracker_factory is None,
        "ocr": ocr_factory is None,
        "qwen_loader": qwen_loader is None,
        "qwen_probe": qwen_probe is None,
        "frame_loader": frame_loader is None,
    }
    if not all(real_components.values()):
        reasons.append("injected components present: not a real-run qualification")

    load_image = frame_loader or _load_frame
    started = time.perf_counter()
    tracker = (tracker_factory or open_tracker)(arm)
    tracker_load_ms = (time.perf_counter() - started) * 1000.0
    status = "none" if tracker is None else str(getattr(tracker, "status", "unavailable"))
    if tracker is None or status != "ok":
        blockers.append(f"tracker {arm!r} unavailable (status {status!r})")
    assembler = SceneAssembler(tracker)
    ocr_fn = (ocr_factory or cli._live_ocr_for_assembler)(assembler)

    frame_items = list(specs.items())
    updates: list[dict] = []
    update_ms: list[float] = []
    qwen_decision_ms: list[float] = []
    total_ocr_invocations = 0
    total_full_frame_invocations = 0
    ocr_invocations_with_tokens = 0
    last_pred_count = 0
    last_frame_id = None
    changed_transitions = 0
    warm_transitions = 0
    current_frame: list = [None]
    ocr_call_ms_total: list = [0.0]

    def instrumented_ocr(image_arg):
        """Single source of truth for how often the live OCR callable really ran."""

        nonlocal total_ocr_invocations, total_full_frame_invocations, ocr_invocations_with_tokens
        started_call = time.perf_counter()
        tokens = ocr_fn(image_arg)
        call_ms = (time.perf_counter() - started_call) * 1000.0
        total_ocr_invocations += 1
        if image_arg is current_frame[0]:
            total_full_frame_invocations += 1
        if tokens:
            ocr_invocations_with_tokens += 1
        ocr_call_ms_total[0] += call_ms
        return tokens

    qwen_result: dict = {"status": "skipped", "reason": "qwen not requested"}
    policy_holder: list = [None]
    probe = None
    if qwen_required:
        loader = qwen_loader or (lambda: _check_perception_module().load_qwen())
        probe = qwen_probe or (
            lambda policy, **kw: _check_perception_module().qwen_decision(policy, **kw)
        )
        try:
            policy_holder[0], load_meta = loader()
            qwen_result = {
                "status": "loaded",
                "loader": load_meta,
                "resident_before_warmup": True,
                "synthetic_offers": True,
                "claim": (
                    "coresidency and normal-policy-route diagnostic only; the offered "
                    "action ids are synthetic placeholders, not scene-choice accuracy"
                ),
            }
        except Exception as exc:
            qwen_result = {"status": "blocked", "reason": f"{type(exc).__name__}: {exc}"}
            reasons.append("normal Qwen policy route not proved")

    qwen_calls = 0
    qwen_offers_seen: list[int] = []

    def observe(index: int, *, phase: str) -> None:
        nonlocal last_pred_count, last_frame_id, changed_transitions, qwen_calls
        nonlocal warm_transitions
        frame_id, spec = frame_items[index % len(frame_items)]
        frame = load_image(Path(spec["path"]))
        current_frame[0] = frame
        invocations_before = total_ocr_invocations
        full_frame_before = total_full_frame_invocations
        ocr_call_ms_before = ocr_call_ms_total[0]
        t0 = time.perf_counter()
        _recs, scene = assembler.update(frame, ocr=instrumented_ocr)
        _cuda_sync()
        update_time = (time.perf_counter() - t0) * 1000.0
        last_pred_count = len(_preds(scene))
        record = {
            "phase": phase,
            "frame": frame_id,
            "update_ms": round(update_time, 1),
            "ocr_invocations": total_ocr_invocations - invocations_before,
            "ocr_full_frame_invocations": total_full_frame_invocations - full_frame_before,
            "ocr_call_ms": round(ocr_call_ms_total[0] - ocr_call_ms_before, 1),
        }
        record["ocr_crop_invocations"] = (
            record["ocr_invocations"] - record["ocr_full_frame_invocations"]
        )
        if last_frame_id is not None and frame_id != last_frame_id:
            changed_transitions += 1
            if phase == "warm":
                warm_transitions += 1
        last_frame_id = frame_id
        if phase == "warm":
            if qwen_required and policy_holder[0] is not None and probe is not None:
                qwen_started = time.perf_counter()
                decision = probe(
                    policy_holder[0],
                    gpa_proposals=max(2, last_pred_count),
                    ocr_tokens=ocr_invocations_with_tokens,
                )
                qwen_decision_ms.append((time.perf_counter() - qwen_started) * 1000.0)
                qwen_calls += 1
                record["qwen_decision_ms"] = round(qwen_decision_ms[-1], 1)
                record["qwen_offers"] = int(decision.get("offer_count") or 0)
                qwen_offers_seen.append(record["qwen_offers"])
            update_ms.append(update_time)
        updates.append(record)

    if not blockers:
        observe(0, phase="cold")
        for step in range(warmup):
            observe(1 + step, phase="warmup")
        for step in range(repeats):
            observe(1 + warmup + step, phase="warm")
        cold_updates = [item for item in updates if item["phase"] == "cold"]
        warm_updates = [item for item in updates if item["phase"] == "warm"]
        if warm_transitions < 1:
            blockers.append("warm samples covered no changed frame transition")
        cold_full_frame = sum(item["ocr_full_frame_invocations"] for item in cold_updates)
        if cold_full_frame < 1:
            blockers.append("live one-full-frame OCR path was never driven through the assembler")
        if ocr_invocations_with_tokens == 0:
            reasons.append(
                "OCR callable returned no tokens on any invocation (engine absent or unreadable)"
            )
        if qwen_required and policy_holder[0] is not None:
            qwen_result["decision_calls"] = qwen_calls
            qwen_result["decision_ms"] = _latency_summary(qwen_decision_ms)
            qwen_result["offers_per_decision"] = qwen_offers_seen
            if qwen_calls == 0:
                reasons.append("Qwen was not exercised during the measured workload")
            elif qwen_calls != len(warm_updates):
                reasons.append("Qwen was not exercised on every measured warm sample")
            elif any(offers < 2 for offers in qwen_offers_seen):
                reasons.append("Qwen multi-offer decision not exercised during the measured workload")

    lean_ui_blockers: list[str] = []
    if len([item for item in updates if item["phase"] == "warm"]) < REQUIRED_WARM_SAMPLES:
        lean_ui_blockers.append(
            f"timed warm samples {len(update_ms)} < {REQUIRED_WARM_SAMPLES} required by plan C3"
        )
    lean_ui_blockers.append("reviewed held-out / scenario-class labels are not present in this run")
    lean_ui_blockers.append(
        "single same-process run: cold-process separation and cache-hit latency are not measured here"
    )
    lean_ui_blockers.append(
        "Gate E outcomes (false/stale/duplicate offers, coverage, VRAM headroom) are not adjudicated here"
    )
    if not all(real_components.values()):
        lean_ui_blockers.append("injected components present: mock-only workload cannot qualify")

    report = {
        "harness": "c3a-timing",
        "arm": arm,
        "out_dir": str(out_path),
        "identity": {
            "revision": _git_identity(),
            "versions": _environment_versions(),
            "command": list(command if command is not None else sys.argv),
            "offline_env": offline_env,
            "config": {
                "arm": arm,
                "repeats": repeats,
                "warmup": warmup,
                "frame_size": list(frame_size) if frame_size else None,
                "qwen_required": qwen_required,
            },
            "frames": frame_records,
        },
        "real_components": real_components,
        "cold": {
            "tracker_load_ms": round(tracker_load_ms, 1),
            "tracker_status": status,
            "updates": [item for item in updates if item["phase"] == "cold"],
        },
        "warmup": {
            "iterations": warmup,
            "updates": [item for item in updates if item["phase"] == "warmup"],
        },
        "warm": {
            "warmup_iterations": warmup,
            "update": _latency_summary(update_ms),
            "qwen_decision": _latency_summary(qwen_decision_ms),
            "note": (
                "update includes assembler OCR calls with no separate full-frame OCR copy; per-sample counts "
                "distinguish full-frame and crop reads"
            ),
            "samples": [item for item in updates if item["phase"] == "warm"],
        },
        "counters": {
            "cold_frames": len([item for item in updates if item["phase"] == "cold"]),
            "warmup_iterations": len([item for item in updates if item["phase"] == "warmup"]),
            "warm_samples": len(update_ms),
            "update_invocations": len(updates),
            "ocr_invocations": total_ocr_invocations,
            "ocr_full_frame_invocations": total_full_frame_invocations,
            "ocr_crop_invocations": total_ocr_invocations - total_full_frame_invocations,
            "ocr_invocations_with_tokens": ocr_invocations_with_tokens,
            "qwen_decision_calls": qwen_calls,
            "precomputed_ocr": False,
            "unique_warm_frames": len(
                {item["frame"] for item in updates if item["phase"] == "warm"}
            ),
            "changed_frame_transitions": changed_transitions,
            "warm_changed_frame_transitions": warm_transitions,
        },
        "qwen": qwen_result,
        "gate": {
            "harness_accounting": "blocked" if blockers or reasons else "ready",
            "lean_ui_qualification": "blocked",
            "accounting_reasons": list(dict.fromkeys([*blockers, *reasons])),
            "lean_ui_blockers": lean_ui_blockers,
        },
    }
    (out_path / "timing_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    ann = json.loads((EVAL / "annotations.json").read_text(encoding="utf-8"))
    qwen_profile = _qwen_resident_profile()
    report = {
        "missing_surfaces": ann.get("missing_surfaces"),
        "combat_gate_frame": "2a3e4f6ff3b03305",
        # The manual CUDA profile is diagnostic evidence only. Gate E requires
        # the normal Qwen policy path to be resident during the tested replay.
        "qwen_resident": False,
        "qwen_note": "normal Qwen policy loader was not coresident during the tested replay",
        "qwen_resident_profile": qwen_profile,
        "arms": [],
    }
    for arm in ARMS:
        arm_report = replay_arm(
            arm,
            ann["frames"],
            frame_size=ann.get("frame_size"),
            strict_identity=True,
        )
        arm_report["temporal"] = temporal_sequence_metrics(ann, arm_report.get("frames") or [])
        report["arms"].append(arm_report)
        try:
            import gc
            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
    out = EVAL / "offline_qualification.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (EVAL / "e_verdict.json").write_text(
        json.dumps(qualification_verdict(report, ann), indent=2), encoding="utf-8"
    )
    print(out)


if __name__ == "__main__":
    main()
