"""Frozen native product contracts (Wave 0 types only; no live wiring)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any

from loop.adaptive.schema import StateSnapshot


class OperatingState(Enum):
    Boot = "Boot"
    Observe = "Observe"
    Experiment = "Experiment"
    ShadowReact = "ShadowReact"
    ArmedReact = "ArmedReact"
    Halted = "Halted"


class ObservationQuality(str, Enum):
    trusted = "trusted"
    degraded = "degraded"
    untrusted = "untrusted"


class CatalogCompileError(ValueError):
    pass


@dataclass(frozen=True)
class NativeTrustedConstraints:
    risk: str
    retry: str
    cooldown_s: float
    max_plan_duration_s: float


@dataclass(frozen=True)
class NativeCandidate:
    action_id: str
    description: str
    constraints: NativeTrustedConstraints


@dataclass(frozen=True)
class NativeCatalog:
    schema_revision_id: str
    revision: str
    candidates: tuple[NativeCandidate, ...]


@dataclass(frozen=True)
class NativeObservation:
    episode_id: str
    episode_generation: int
    capture_ts_monotonic: float
    publication_ts_monotonic: float
    observation_age_s: float
    capture_integrity: str
    capture_provenance: tuple[str, ...]
    hwnd: int
    pid: int
    target_identity: dict[str, str]
    snapshot_id: str
    schema_revision_id: str
    quality: ObservationQuality
    missing_required_fields: tuple[str, ...]
    snapshot: StateSnapshot


@dataclass(frozen=True)
class ActuationLease:
    episode_generation: int
    snapshot_id: str
    schema_revision_id: str
    catalog_revision: str
    directive_revision: int
    policy_revision: str
    hwnd: int
    pid: int
    target_identity: dict[str, str]
    expires_at_monotonic: float
    selected_action_id: str
    hard_mask_digest: str


@dataclass(frozen=True)
class ExpectedEffectOutcome:
    verified: bool
    reason: str


@dataclass(frozen=True)
class ExecutionReceipt:
    accepted: bool
    reason: str
    primitive_results: tuple[dict[str, Any], ...]
    release_status: str
    after_hwnd: int | None
    after_pid: int | None
    after_capture_provenance: tuple[str, ...]
    expected_effect: ExpectedEffectOutcome


def _constraints_payload(constraints: NativeTrustedConstraints) -> dict[str, Any]:
    return {
        "risk": constraints.risk,
        "retry": constraints.retry,
        "cooldown_s": constraints.cooldown_s,
        "max_plan_duration_s": constraints.max_plan_duration_s,
    }


def _validate_constraints(constraints: NativeTrustedConstraints) -> None:
    if not constraints.risk.strip():
        raise CatalogCompileError("native risk constraint is required")
    if not constraints.retry.strip():
        raise CatalogCompileError("native retry constraint is required")
    if constraints.cooldown_s < 0:
        raise CatalogCompileError("native cooldown_s must be non-negative")
    if constraints.max_plan_duration_s <= 0:
        raise CatalogCompileError("native max_plan_duration_s must be positive")


def _catalog_hash_payload(schema_revision_id: str, candidates: tuple[NativeCandidate, ...]) -> dict:
    ordered = sorted(candidates, key=lambda c: c.action_id)
    return {
        "schema_revision_id": schema_revision_id,
        "candidates": [
            {
                "action_id": c.action_id,
                "description": c.description,
                "constraints": _constraints_payload(c.constraints),
            }
            for c in ordered
        ],
    }


def catalog_revision_for(schema_revision_id: str, candidates: tuple[NativeCandidate, ...]) -> str:
    blob = json.dumps(
        _catalog_hash_payload(schema_revision_id, candidates),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(blob.encode("utf-8")).hexdigest()


def compile_native_catalog(
    schema_revision_id: str,
    candidates: tuple[NativeCandidate, ...],
) -> NativeCatalog:
    if not schema_revision_id.strip():
        raise CatalogCompileError("schema_revision_id is required")
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.action_id in seen:
            raise CatalogCompileError(f"duplicate native candidate id {candidate.action_id!r}")
        seen.add(candidate.action_id)
        if not candidate.action_id.strip():
            raise CatalogCompileError("native candidate action_id is required")
        if not candidate.description.strip():
            raise CatalogCompileError("native candidate description is required")
        _validate_constraints(candidate.constraints)
    ordered = tuple(sorted(candidates, key=lambda c: c.action_id))
    revision = catalog_revision_for(schema_revision_id, ordered)
    return NativeCatalog(
        schema_revision_id=schema_revision_id,
        revision=revision,
        candidates=ordered,
    )


def after_preflight(stack_ok: bool, model_ok: bool | None) -> OperatingState:
    """Boot lands in Observe. Failed/skipped preflight still cannot arm or react."""
    del stack_ok, model_ok
    return OperatingState.Observe


def decider_may_arm(preflight_ok: bool) -> bool:
    return preflight_ok
