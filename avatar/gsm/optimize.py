from __future__ import annotations

import json
from typing import Any

from avatar.gsm.sandbox import sandbox_rule
from avatar.schema import GsmLabelRewrite, GsmSpec


def _as_parsed(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    parsed = getattr(result, "parsed", result)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str) and parsed.strip():
        try:
            data = json.loads(parsed)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def _parse_labels(parsed: dict[str, Any] | None) -> list[str]:
    if not parsed or "error" in parsed:
        return []
    try:
        rewrite = GsmLabelRewrite.model_validate(parsed)
    except Exception:  # noqa: BLE001 — VLM JSON is often slightly off-schema
        raw = parsed.get("labels")
        if not isinstance(raw, list):
            return []
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return [item.strip() for item in rewrite.labels if isinstance(item, str) and item.strip()]


_REWRITE_REASONS = ("no-piece", "noop", "stuck", "bad-occupancy")
_GARBAGE_LABELS = {
    "miss",
    "empty",
    "none",
    "no-piece",
    "noop",
    "stuck",
    "bad-occupancy",
    "n/a",
    "null",
    "placement error",
    "missed opportunity",
}


def _detector_label_ok(label: str, fixture: str) -> bool:
    """Keep tetromino / tile vocab. Drop reflection-speak."""
    key = label.strip().lower()
    if not key:
        return False
    fx = (fixture or "").lower()
    if fx == "tetris":
        return "tetromino" in key
    if fx in {"2048", "g2048"}:
        return key == "tile"
    return True


def _usable_labels(labels: list[str], reason: str, *, fixture: str = "") -> list[str]:
    reason_tokens = {
        token.strip().lower()
        for token in (reason or "").replace("_", " ").replace("-", " ").split()
        if token.strip()
    }
    out: list[str] = []
    seen: set[str] = set()
    for lab in labels:
        key = lab.strip().lower()
        if not key or key in seen:
            continue
        if key in _GARBAGE_LABELS or key in reason_tokens:
            continue
        if not _detector_label_ok(lab, fixture):
            continue
        seen.add(key)
        out.append(lab.strip())
    return out


def maybe_rewrite_labels(
    registry: Any,
    spec: GsmSpec,
    *,
    last_boxes: list[Any] | None,
    reason: str,
    complete_json: Any,
) -> int:
    """VLM label rewrite after a miss: empty boxes, noop, stuck, or over-occupancy. Cap 3."""
    if str(getattr(spec, "detector_family", "") or "") == "pixel-blob":
        return spec.rewrites
    reason_l = (reason or "").lower()
    if last_boxes and not any(token in reason_l for token in _REWRITE_REASONS):
        return spec.rewrites
    max_rewrites = int(getattr(registry, "max_rewrites", 3) or 3)
    if spec.rewrites >= max_rewrites:
        return spec.rewrites

    try:
        result = complete_json(
            system=(
                'Reply with a single JSON object {"labels": ["..."]}. '
                "Propose detector labels for the miss. No markdown."
            ),
            user=(
                f"GSM {spec.name}. Current labels: {list(spec.labels)}. "
                f"Reason: {reason or 'empty boxes'}. "
                "ROI unchanged or occupancy missed. Propose a short label list."
            ),
        )
    except Exception:  # noqa: BLE001 — optimizer must not break the turn
        return spec.rewrites

    labels = _usable_labels(
        _parse_labels(_as_parsed(result)),
        reason,
        fixture=str(getattr(spec, "fixture", "") or getattr(registry, "fixture", "") or ""),
    )
    if not labels:
        return spec.rewrites

    previous_labels = list(spec.labels)
    was_enabled = bool(spec.enabled)
    candidate = GsmSpec(name=spec.name, kind=spec.kind, labels=labels)
    outcome = registry.rewrite(spec.name, candidate)
    if outcome != "rewritten":
        return spec.rewrites

    ok, _msg = sandbox_rule(spec)
    if not ok:
        spec.labels = previous_labels
        if not was_enabled:
            spec.enabled = False
        return spec.rewrites
    if hasattr(registry, "enable"):
        try:
            registry.enable(spec.name, True)
        except Exception:  # noqa: BLE001 — live spec may already be enabled
            spec.enabled = True
    else:
        spec.enabled = True
    save = getattr(registry, "save", None)
    if callable(save):
        try:
            save()
        except Exception:  # noqa: BLE001 — persist must not abort the turn
            pass
    return spec.rewrites
