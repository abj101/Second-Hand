"""Bounded recovery for card-source failures and control no-ops."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class RecoveryAction(str, Enum):
    REBUILD = "rebuild"
    END_TURN = "end_turn"
    SOFTLOCK = "softlock"
    REPLAN = "replan"
    ESCAPE = "escape"


@dataclass(frozen=True)
class RecoveryResult:
    action: RecoveryAction
    fingerprint: tuple[Any, ...]
    quarantine: bool = False
    intent: dict[str, Any] | None = None
    reason: str = ""


_EPHEMERAL_OID_PREFIXES = ("mk_", "mark_", "object:")


def _bbox_tuple(raw: Any) -> tuple[float, float, float, float] | None:
    bbox = raw
    if isinstance(bbox, Mapping):
        bbox = [bbox.get(key) for key in ("x0", "y0", "x1", "y1")]
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        return tuple(round(float(value), 2) for value in bbox[:4])  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _source_bbox(failure: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    evidence = failure.get("evidence")
    source = evidence.get("source") if isinstance(evidence, Mapping) else None
    bbox = source.get("bbox") if isinstance(source, Mapping) else failure.get("source_bbox")
    return _bbox_tuple(bbox)


def source_failure_fingerprint(failure: Mapping[str, Any]) -> tuple[Any, ...]:
    """Stable across regenerated mark/card/target ids and chord names."""
    screen = str(failure.get("screen") or "").strip().lower()
    code = str(failure.get("code") or failure.get("dispatch") or "").strip().lower()
    family = "source" if "source" in code else code
    evidence_fingerprint = str(failure.get("evidence_fingerprint") or "").strip()
    return screen, evidence_fingerprint, family, _source_bbox(failure)


def semantic_object_id(raw: Any) -> str:
    """Durable object id; never mk_N / mark_N / object:N."""
    text = str(raw or "").strip()
    if not text:
        return ""
    compact = text.lower()
    if compact.startswith(_EPHEMERAL_OID_PREFIXES):
        return ""
    return text


def _control_bbox(failure: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    bbox = _bbox_tuple(failure.get("bbox") or failure.get("source_bbox"))
    if bbox is not None:
        return bbox
    evidence = failure.get("evidence")
    if isinstance(evidence, Mapping):
        source = evidence.get("source") if isinstance(evidence.get("source"), Mapping) else evidence
        if isinstance(source, Mapping):
            return _bbox_tuple(source.get("bbox"))
    return None


def control_noop_fingerprint(failure: Mapping[str, Any]) -> tuple[Any, ...]:
    """Surface evidence + dispatched bbox; oid is audit-only and ignored on rename."""
    evidence_fingerprint = str(failure.get("evidence_fingerprint") or "").strip()
    intent = failure.get("intent") if isinstance(failure.get("intent"), Mapping) else {}
    oid = semantic_object_id(
        failure.get("object_id") or intent.get("control_id") or intent.get("object_id")
    )
    return evidence_fingerprint, _control_bbox(failure), oid


def _same_control_noop(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
    if left == right:
        return True
    if len(left) < 2 or len(right) < 2:
        return False
    if left[1] is None or left[1] != right[1]:
        return False
    return left[0] == right[0]


class SourceFailureRecovery:
    def __init__(self) -> None:
        self.fingerprint: tuple[Any, ...] | None = None
        self.rebuilt = False

    def reset(self) -> None:
        self.fingerprint = None
        self.rebuilt = False

    def mark_rebuilt(self) -> None:
        if self.fingerprint is not None:
            self.rebuilt = True

    def observe(
        self,
        failure: Mapping[str, Any],
        *,
        overlay_active: bool = False,
        end_turn: dict[str, Any] | None = None,
    ) -> RecoveryResult:
        fingerprint = source_failure_fingerprint(failure)
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            self.rebuilt = False
        if not self.rebuilt:
            return RecoveryResult(RecoveryAction.REBUILD, fingerprint, quarantine=True)
        if overlay_active:
            return RecoveryResult(
                RecoveryAction.SOFTLOCK, fingerprint,
                reason="overlay-blocks-end-turn-fallback",
            )
        if end_turn is None:
            return RecoveryResult(
                RecoveryAction.SOFTLOCK, fingerprint, reason="no-end-turn-fallback",
            )
        return RecoveryResult(
            RecoveryAction.END_TURN, fingerprint, intent=dict(end_turn),
            reason="repeated-source-failure",
        )


class ControlNoopRecovery:
    """Two-strike control no-op: quarantine+rescan, then replan/escape/softlock."""

    def __init__(self) -> None:
        self.fingerprint: tuple[Any, ...] | None = None
        self.rescanned = False

    def reset(self) -> None:
        self.fingerprint = None
        self.rescanned = False

    def mark_rescanned(self) -> None:
        if self.fingerprint is not None:
            self.rescanned = True

    def observe(
        self,
        failure: Mapping[str, Any],
        *,
        other_controls: bool = False,
        overlay_active: bool = False,
    ) -> RecoveryResult:
        fingerprint = control_noop_fingerprint(failure)
        if self.fingerprint is None or not _same_control_noop(fingerprint, self.fingerprint):
            self.fingerprint = fingerprint
            self.rescanned = False
        if not self.rescanned:
            return RecoveryResult(
                RecoveryAction.REBUILD, fingerprint,
                quarantine=True, reason="control-noop-rescan",
            )
        if other_controls:
            return RecoveryResult(
                RecoveryAction.REPLAN, fingerprint,
                quarantine=True, reason="control-noop-other",
            )
        if overlay_active:
            return RecoveryResult(
                RecoveryAction.ESCAPE, fingerprint,
                reason="control-noop-overlay-escape",
            )
        return RecoveryResult(
            RecoveryAction.SOFTLOCK, fingerprint, reason="control-noop-exhausted",
        )
