"""Bounded recovery for repeated card-source failures."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class RecoveryAction(str, Enum):
    REBUILD = "rebuild"
    END_TURN = "end_turn"
    SOFTLOCK = "softlock"


@dataclass(frozen=True)
class RecoveryResult:
    action: RecoveryAction
    fingerprint: tuple[Any, ...]
    quarantine: bool = False
    intent: dict[str, Any] | None = None
    reason: str = ""


def _source_bbox(failure: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    evidence = failure.get("evidence")
    source = evidence.get("source") if isinstance(evidence, Mapping) else None
    bbox = source.get("bbox") if isinstance(source, Mapping) else failure.get("source_bbox")
    if isinstance(bbox, Mapping):
        bbox = [bbox.get(key) for key in ("x0", "y0", "x1", "y1")]
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        return tuple(round(float(value), 2) for value in bbox[:4])  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def source_failure_fingerprint(failure: Mapping[str, Any]) -> tuple[Any, ...]:
    """Stable across regenerated mark/card/target ids and chord names."""
    screen = str(failure.get("screen") or "").strip().lower()
    code = str(failure.get("code") or failure.get("dispatch") or "").strip().lower()
    family = "source" if "source" in code else code
    evidence_fingerprint = str(failure.get("evidence_fingerprint") or "").strip()
    return screen, evidence_fingerprint, family, _source_bbox(failure)


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
