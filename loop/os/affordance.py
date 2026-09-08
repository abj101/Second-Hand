"""Frame-diff affordance detector — game-agnostic UI legality signals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from PIL import Image

from loop.log.schema import ActionLog
from loop.os.aim import AimGuide, AimMark, fill_from_aim

_BRIGHTNESS_THRESHOLD = 12.0
_EDGE_THRESHOLD = 0.04
_SATURATION_THRESHOLD = 0.06
_CONFIDENT = 0.65
_HAND_Y = 0.70


@dataclass(frozen=True)
class AffordanceSignal:
    delta: str
    confidence: float
    signal: str | None
    verdict: str
    # Frame diffs are inherently weak evidence unless they were collected for
    # one object in one known snapshot.  Keep that fact attached to the
    # signal so callers cannot accidentally turn generic CV telemetry into a
    # typed-action veto.
    provenance: str = "cv"
    scope: str = "unscoped"
    object_id: str | None = None
    snapshot_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "delta": self.delta,
            "confidence": round(self.confidence, 3),
            "signal": self.signal,
            "verdict": self.verdict,
            "provenance": self.provenance,
            "scope": self.scope,
            "object_id": self.object_id,
            "snapshot_id": self.snapshot_id,
        }


def _patch_saturation_mean(patch: np.ndarray) -> float:
    """HSV S-channel mean in 0–1 (colorize-on-hover; not raw green)."""
    if patch.size == 0:
        return 0.0
    rgb = np.clip(patch, 0.0, 255.0).astype(np.uint8)
    hsv = Image.fromarray(rgb, mode="RGB").convert("HSV")
    sat = np.asarray(hsv, dtype=np.float32)[:, :, 1]
    return float(sat.mean()) / 255.0


def _region_means(
    pre: np.ndarray,
    post: np.ndarray,
    mark: AimMark,
) -> tuple[float, float, float, float, float, float]:
    """Return pre/post brightness, edge density, saturation means for a mark bbox."""
    h, w = pre.shape[:2]
    ix0 = max(0, int(mark.x0 * w))
    ix1 = min(w, max(ix0 + 1, int(mark.x1 * w)))
    iy0 = max(0, int(mark.y0 * h))
    iy1 = min(h, max(iy0 + 1, int(mark.y1 * h)))
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    pre_patch = pre[iy0:iy1, ix0:ix1]
    post_patch = post[iy0:iy1, ix0:ix1]
    if pre_patch.size == 0 or post_patch.size == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

    pre_luma = pre_patch.mean(axis=2).astype(np.float32)
    post_luma = post_patch.mean(axis=2).astype(np.float32)
    pre_bright = float(pre_luma.mean())
    post_bright = float(post_luma.mean())

    def _edge_density(luma: np.ndarray) -> float:
        if luma.shape[0] < 3 or luma.shape[1] < 3:
            return 0.0
        gx = np.abs(np.diff(luma, axis=1)).mean()
        gy = np.abs(np.diff(luma, axis=0)).mean()
        return float((gx + gy) / 2.0)

    pre_edge = _edge_density(pre_luma)
    post_edge = _edge_density(post_luma)
    pre_sat = _patch_saturation_mean(pre_patch)
    post_sat = _patch_saturation_mean(post_patch)

    return pre_bright, post_bright, pre_edge, post_edge, pre_sat, post_sat


def _classify_delta(
    *,
    bright_delta: float,
    edge_delta: float,
    sat_delta: float,
) -> tuple[str, float, str | None]:
    """Short-circuit channel evaluation — strongest signal wins."""
    signals: list[tuple[str, float, str]] = []

    if abs(bright_delta) >= _BRIGHTNESS_THRESHOLD:
        conf = min(0.95, 0.5 + abs(bright_delta) / 80.0)
        if bright_delta > 0:
            signals.append(("highlighted", conf, "brightness"))
        else:
            signals.append(("dimmed", conf, "brightness"))

    if abs(edge_delta) >= _EDGE_THRESHOLD:
        conf = min(0.92, 0.45 + abs(edge_delta) * 4.0)
        if edge_delta > 0:
            signals.append(("highlighted", conf, "outline"))
        else:
            signals.append(("dimmed", conf, "outline"))

    if abs(sat_delta) >= _SATURATION_THRESHOLD:
        conf = min(0.88, 0.4 + abs(sat_delta) * 3.0)
        if sat_delta > 0:
            signals.append(("highlighted", conf, "saturation"))
        else:
            signals.append(("dimmed", conf, "saturation"))

    if not signals:
        return "unchanged", 0.35, None

    signals.sort(key=lambda row: row[1], reverse=True)
    delta, conf, channel = signals[0]
    if len(signals) > 1 and signals[1][1] >= conf * 0.85:
        channel = "+".join(sorted({signals[0][2] or "", signals[1][2] or ""} - {""}))
    return delta, conf, channel or None


def _delta_to_verdict(delta: str, confidence: float) -> str:
    if confidence < _CONFIDENT:
        return "uncertain"
    if delta == "highlighted":
        return "legal"
    if delta == "dimmed":
        return "illegal"
    return "uncertain"


def detect_affordance_diff(
    pre: Image.Image,
    post: Image.Image,
    marks: Sequence[AimMark],
    *,
    snapshot_id: str | None = None,
    scope: str = "unscoped",
) -> dict[str, AffordanceSignal]:
    """Classify per-mark appearance change between pre and post frames.

    The default result is deliberately *unscoped*.  A generic comparison of
    two frames does not establish that the pixels belong to the same object,
    or even that the frames represent the same UI state.  Such results remain
    useful telemetry, but must not veto typed combat actions.

    ``scope="object"`` is reserved for callers that performed a same-object,
    same-snapshot probe and can provide the corresponding ``AimMark``
    ``object_id``.  The function does not infer that scope from a mark alone.
    """
    if scope not in {"unscoped", "object"}:
        raise ValueError("scope must be 'unscoped' or 'object'")
    pre_rgb = np.asarray(pre.convert("RGB"), dtype=np.float32)
    post_rgb = np.asarray(post.convert("RGB"), dtype=np.float32)
    if pre_rgb.shape != post_rgb.shape:
        post_pil = post.convert("RGB").resize(pre.size, Image.Resampling.BILINEAR)
        post_rgb = np.asarray(post_pil, dtype=np.float32)

    out: dict[str, AffordanceSignal] = {}
    for mark in marks:
        pre_b, post_b, pre_e, post_e, pre_s, post_s = _region_means(pre_rgb, post_rgb, mark)
        bright_delta = post_b - pre_b
        edge_delta = post_e - pre_e
        sat_delta = post_s - pre_s
        delta, confidence, signal = _classify_delta(
            bright_delta=bright_delta,
            edge_delta=edge_delta,
            sat_delta=sat_delta,
        )
        verdict = _delta_to_verdict(delta, confidence)
        out[f"mk_{mark.id}"] = AffordanceSignal(
            delta=delta,
            confidence=confidence,
            signal=signal,
            verdict=verdict,
            scope=scope,
            object_id=mark.object_id if scope == "object" else None,
            snapshot_id=snapshot_id,
        )
    return out


def _cv_is_unscoped(row: dict[str, object]) -> bool:
    """Return whether a legal-action row is only generic CV telemetry."""
    provenance = str(row.get("provenance") or row.get("source") or "").strip().lower()
    scope = str(row.get("scope") or "").strip().lower()
    return provenance == "cv" and scope != "object"


def _parse_mark_id(value: object) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw.split("_", 1)[-1]) if "_" in raw else int(raw)
    except (TypeError, ValueError):
        return None


def _signal_row(mark_id: str, signal: AffordanceSignal) -> dict[str, object]:
    return {
        "mark": mark_id,
        "verdict": signal.verdict,
        "confidence": signal.confidence,
        "source": signal.provenance,
        "provenance": signal.provenance,
        "scope": signal.scope,
        "object_id": signal.object_id,
        "snapshot_id": signal.snapshot_id,
    }


def merge_legal_actions(
    parsed_actions: list[dict[str, object]] | None,
    cv_verdicts: dict[str, AffordanceSignal],
) -> list[dict[str, object]]:
    """Attach CV-derived verdicts to LLM1 legal_actions entries."""
    merged: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in parsed_actions or []:
        if not isinstance(row, dict):
            continue
        mark = str(row.get("mark") or row.get("id") or "").strip()
        if not mark:
            continue
        if not mark.startswith("mk_"):
            mark = f"mk_{mark}"
        cv = cv_verdicts.get(mark)
        verdict = str(row.get("verdict") or "").strip().lower()
        confidence = row.get("confidence")
        # Parsed/current typed legality is authoritative.  Unscoped CV may
        # fill a missing observation, but never overwrite a parsed verdict.
        # This is the key guard against cross-frame mark drift.
        if not verdict:
            verdict = cv.verdict if cv is not None else "uncertain"
            confidence = cv.confidence if cv is not None else confidence
        if verdict not in {"legal", "illegal", "uncertain"}:
            verdict = "uncertain"
        merged_row: dict[str, object] = {
            "mark": mark,
            "verdict": verdict,
            "confidence": confidence,
        }
        if cv is not None:
            # Preserve CV as diagnostics without making it the row's source
            # of legality when a parsed row already exists.
            merged_row.update(
                {
                    "cv_verdict": cv.verdict,
                    "cv_confidence": cv.confidence,
                    "cv_provenance": cv.provenance,
                    "cv_scope": cv.scope,
                    "cv_object_id": cv.object_id,
                    "cv_snapshot_id": cv.snapshot_id,
                }
            )
        merged.append(merged_row)
        seen.add(mark)

    for mark_id, cv in cv_verdicts.items():
        if mark_id in seen:
            continue
        merged.append(_signal_row(mark_id, cv))
    return merged


def effective_legal_actions(
    legal_actions: Sequence[dict[str, object]] | None,
    cv_verdicts: dict[str, AffordanceSignal] | None,
) -> list[dict[str, object]]:
    """Merged gate list — CV fills gaps when LLM1 legal_actions is empty."""
    return merge_legal_actions(
        list(legal_actions) if legal_actions else None,
        cv_verdicts or {},
    )


def _illegal_mark_ids(
    legal_actions: Sequence[dict[str, object]] | None,
    *,
    confidence_threshold: float = _CONFIDENT,
) -> set[int]:
    blocked: set[int] = set()
    for row in legal_actions or []:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("mark") or "").strip()
        if not raw:
            continue
        try:
            mark_id = int(raw.split("_", 1)[-1]) if "_" in raw else int(raw)
        except (TypeError, ValueError):
            continue
        verdict = str(row.get("verdict") or "uncertain").strip().lower()
        conf_raw = row.get("confidence")
        conf = float(conf_raw) if isinstance(conf_raw, (int, float)) else 0.0
        if verdict == "illegal" and conf >= confidence_threshold:
            blocked.add(mark_id)
    return blocked


def validate_legal(
    action: ActionLog,
    legal_actions: Sequence[dict[str, object]] | None,
    *,
    confidence_threshold: float = _CONFIDENT,
) -> tuple[ActionLog, str | None]:
    """Validate a legacy chord without letting generic CV veto typed combat.

    Legacy/noncombat callers retain the old fail-closed behavior.  Typed
    combat transactions are different: an unscoped CV row is only telemetry,
    so it cannot produce ``illegal-chord``.  Object-scoped evidence can still
    block when it names the exact typed source/target object.
    """
    if not legal_actions:
        return action, None
    by_mark: dict[int, tuple[str, float]] = {}
    for row in legal_actions:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("mark") or "").strip()
        if not raw:
            continue
        try:
            mark_id = int(raw.split("_", 1)[-1]) if "_" in raw else int(raw)
        except (TypeError, ValueError):
            continue
        verdict = str(row.get("verdict") or "uncertain").strip().lower()
        conf_raw = row.get("confidence")
        conf = float(conf_raw) if isinstance(conf_raw, (int, float)) else 0.0
        by_mark[mark_id] = (verdict, conf)

    typed_txn = bool(getattr(action, "source_role", None) or getattr(action, "source_object_id", None))

    for attr in ("mark", "target"):
        mark_val = getattr(action, attr, None)
        if mark_val is None:
            continue
        row = next(
            (
                item
                for item in legal_actions
                if isinstance(item, dict)
                and _parse_mark_id(item.get("mark")) == int(mark_val)
            ),
            None,
        )
        if typed_txn and isinstance(row, dict) and _cv_is_unscoped(row):
            continue
        if typed_txn and isinstance(row, dict):
            provenance = str(row.get("provenance") or row.get("source") or "").strip().lower()
            if provenance == "cv" and str(row.get("scope") or "").strip().lower() == "object":
                object_id = str(row.get("object_id") or "")
                allowed_ids = {
                    str(getattr(action, "source_object_id", None) or ""),
                    str(getattr(action, "target_object_id", None) or ""),
                }
                if not object_id or object_id not in allowed_ids:
                    continue
        verdict, conf = by_mark.get(int(mark_val), ("uncertain", 0.0))
        if verdict == "illegal" and conf >= confidence_threshold:
            return action, f"illegal-chord:mk_{int(mark_val)}:{conf:.2f}"
    return action, None


def resolve_legal_chord(
    action: ActionLog,
    guide: AimGuide | None,
    legal_actions: Sequence[dict[str, object]] | None,
    *,
    recent: list[str],
    duration_ms: int,
    hud: dict[str, Any] | None = None,
    max_tries: int = 4,
) -> tuple[ActionLog, str | None]:
    """Subtract confidently-illegal marks; never invent legal ones.

    Returns an empty chord when no safe fallback exists — caller must skip execution.
    """
    effective = list(legal_actions or [])
    blocked = _illegal_mark_ids(effective)
    current = action
    last_reason: str | None = None

    # Hover is a read-only perception operation.  If its current-frame mark is
    # rejected, never run the click fallback below: doing so changes both the
    # mark and the input mode, and can select a hand card while the caller is
    # trying to inspect an enemy.  A stale/illegal hover must fail closed so a
    # fresh frame can supply another inspection target.
    # Typed combat transactions already carry source_role / source_object_id.
    # Generic fill_from_aim recovery can swap card→enemy; fail closed instead.
    typed_txn = bool(getattr(action, "source_role", None) or getattr(action, "source_object_id", None))
    if action.hover or typed_txn:
        _, reason = validate_legal(action, effective)
        if reason is not None:
            return ActionLog(duration_ms=duration_ms), reason
        return action, None

    for _ in range(max_tries):
        _, reason = validate_legal(current, effective)
        if reason is None:
            return current, None
        last_reason = reason
        try:
            mark_token = reason.split(":", 2)[1]
            blocked.add(int(mark_token.split("_", 1)[-1]))
        except (IndexError, TypeError, ValueError):
            pass
        if guide is None or not guide.marks:
            break
        filled = fill_from_aim(
            guide,
            recent=recent,
            duration_ms=duration_ms,
            hud=hud,
            exclude_marks=blocked,
        )
        if filled is None:
            break
        current = filled

    _, reason = validate_legal(current, effective)
    if reason is not None:
        return ActionLog(duration_ms=duration_ms), reason
    return current, last_reason if last_reason and current.mark is None else None


def affordance_hit_for_action(
    action: ActionLog,
    legal_actions: Sequence[dict[str, object]] | None,
) -> bool:
    """True when mark or target has a non-uncertain affordance verdict."""
    if not legal_actions:
        return False
    by_mark = {
        str(row.get("mark") or ""): str(row.get("verdict") or "uncertain")
        for row in legal_actions
        if isinstance(row, dict)
    }
    for mark_val in (action.mark, action.target):
        if mark_val is None:
            continue
        verdict = by_mark.get(f"mk_{int(mark_val)}", "uncertain")
        if verdict in {"legal", "illegal"}:
            return True
    return False


def sub_action_warrants_affordance_refresh(action: ActionLog, guide: AimGuide | None) -> bool:
    """State-changing sub-actions that may shift UI affordance signaling."""
    if action.mark is None:
        return False
    if action.hover:
        return True
    if action.target is not None:
        return True
    if guide is None:
        return False
    hit = guide.by_id(int(action.mark))
    if hit is not None and hit.y >= _HAND_Y:
        return True
    return False
