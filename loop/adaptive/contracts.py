"""Frozen native product contracts (Wave 0 types only; no live wiring)."""

from __future__ import annotations

import json
import math
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
    # Acquisition-bound provenance.  ``capture_age_known`` is True only when
    # ``capture_ts_monotonic`` actually bounds the true frame age (a fresh
    # synchronous grab or a verified backend frame time).  Buffered/unknown
    # capture keeps it False and must fail closed for actuation.
    capture_age_known: bool = False
    acquisition_origin: str = "unknown"
    # Monotonic observation sequence scoped to (target, episode generation).
    # None means the producer supplied none: unavailable, never zero, and
    # never a substitute for the image digest.
    observation_sequence: int | None = None

    def acquisition_is_actuation_eligible(self) -> bool:
        """Whether this observation's age bound may authorize native input."""
        return (
            self.capture_age_known
            and self.observation_sequence is not None
            and self.observation_sequence > 0
            and math.isfinite(self.capture_ts_monotonic)
            and math.isfinite(self.publication_ts_monotonic)
            and 0 < self.capture_ts_monotonic <= self.publication_ts_monotonic
            and self.quality is ObservationQuality.trusted
        )


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

    @property
    def selected_registry_node_id(self) -> str | None:
        """Frozen provisional target, when this lease came from a registry offer."""

        value = self.target_identity.get("leased_node_id")
        return str(value) if value else None


EFFECT_STATUS_VERIFIED = "verified"
EFFECT_STATUS_NO_EFFECT = "no_effect"
EFFECT_STATUS_WRONG_EFFECT = "wrong_effect"
EFFECT_STATUS_UNOBSERVABLE = "unobservable"
EFFECT_STATUS_NOT_EXECUTED = "not_executed"
EFFECT_STATUSES = (
    EFFECT_STATUS_VERIFIED,
    EFFECT_STATUS_NO_EFFECT,
    EFFECT_STATUS_WRONG_EFFECT,
    EFFECT_STATUS_UNOBSERVABLE,
    EFFECT_STATUS_NOT_EXECUTED,
)


@dataclass(frozen=True)
class ExpectedEffectOutcome:
    """Execution acceptance is separate from this effect verdict.

    ``status`` is the authority; ``verified`` is true only for
    ``status == 'verified'`` so no caller can read ``no_effect`` as success.
    ``evidence`` holds one record per after-capture (observation reference,
    provenance and trust) so the verdict can be audited.
    """

    verified: bool
    reason: str
    status: str = EFFECT_STATUS_NOT_EXECUTED
    evidence: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.status not in EFFECT_STATUSES:
            raise ValueError(f"unknown effect status {self.status!r}")
        if self.verified != (self.status == EFFECT_STATUS_VERIFIED):
            raise ValueError("verified is true only for status 'verified'")


@dataclass(frozen=True)
class PostActionObservationPolicy:
    """Frozen bounded post-action observation/settling policy.

    ``max_window_s`` and ``max_frames`` bound the settling window; the defaults
    keep the legacy single immediate after-frame.  ``required_negative_frames``
    is the trusted negative evidence needed before ``no_effect`` may be
    recorded; the schema's expected effect is the positive evidence and
    verifies immediately.  Inadequate evidence at the deadline is
    ``unobservable``, never ``no_effect``.
    """

    max_window_s: float = 0.0
    cadence_s: float = 0.0
    max_frames: int = 1
    required_negative_frames: int = 1


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

    def __bool__(self) -> bool:
        return self.accepted


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


class OperatingTransition(str, Enum):
    boot_complete = "boot_complete"
    arm_experiment = "arm_experiment"
    experiment_probe_failed = "experiment_probe_failed"
    experiment_promoted = "experiment_promoted"
    surface_promoted = "surface_promoted"
    operator_arm_react = "operator_arm_react"
    react_pressure = "react_pressure"
    quality_lost_or_empty = "quality_lost_or_empty"
    armed_fault_halt = "armed_fault_halt"
    observe_fault_halt = "observe_fault_halt"
    recover_from_halt = "recover_from_halt"


@dataclass(frozen=True)
class TransitionFacts:
    manifests_checked: bool = False
    inputs_released: bool = False
    model_preflight_ok: bool | None = None
    observation_stable: bool = False
    experiment_armed: bool = False
    probe_failed_or_rolled_back: bool = False
    surface_has_promoted_action: bool = False
    react_gates_pass: bool = False
    operator_arms: bool = False
    latency_or_release_pressure: bool = False
    quality_actionable: bool = True
    surface_empty: bool = False
    focus_loss: bool = False
    release_failure: bool = False
    executor_fault: bool = False
    capture_fault: bool = False
    owner_fault: bool = False
    explicit_recovery: bool = False
    generation_incremented: bool = False


def transition_operating_state(
    current: OperatingState,
    request: OperatingTransition,
    facts: TransitionFacts,
) -> OperatingState:
    """Apply one requested edge from the north-star diagram; illegal requests keep current."""
    if request is OperatingTransition.boot_complete:
        if current is not OperatingState.Boot:
            return current
        if facts.manifests_checked and facts.inputs_released:
            return OperatingState.Observe
        return current

    if request is OperatingTransition.arm_experiment:
        if current is not OperatingState.Observe:
            return current
        if facts.experiment_armed and facts.observation_stable:
            return OperatingState.Experiment
        return current

    if request is OperatingTransition.experiment_probe_failed:
        if current is not OperatingState.Experiment:
            return current
        if facts.probe_failed_or_rolled_back:
            return OperatingState.Observe
        return current

    if request is OperatingTransition.experiment_promoted:
        if current is not OperatingState.Experiment:
            return current
        if facts.surface_has_promoted_action:
            return OperatingState.ShadowReact
        return current

    if request is OperatingTransition.surface_promoted:
        if current is not OperatingState.Observe:
            return current
        if facts.surface_has_promoted_action:
            return OperatingState.ShadowReact
        return current

    if request is OperatingTransition.operator_arm_react:
        if current is not OperatingState.ShadowReact:
            return current
        if facts.react_gates_pass and facts.operator_arms:
            return OperatingState.ArmedReact
        return current

    if request is OperatingTransition.react_pressure:
        if current is not OperatingState.ArmedReact:
            return current
        if facts.latency_or_release_pressure:
            return OperatingState.ShadowReact
        return current

    if request is OperatingTransition.quality_lost_or_empty:
        if current not in (OperatingState.ShadowReact, OperatingState.ArmedReact):
            return current
        if not facts.quality_actionable or facts.surface_empty:
            return OperatingState.Observe
        return current

    if request is OperatingTransition.armed_fault_halt:
        if current is not OperatingState.ArmedReact:
            return current
        if facts.focus_loss or facts.release_failure or facts.executor_fault:
            return OperatingState.Halted
        return current

    if request is OperatingTransition.observe_fault_halt:
        if current is not OperatingState.Observe:
            return current
        if facts.capture_fault or facts.owner_fault:
            return OperatingState.Halted
        return current

    if request is OperatingTransition.recover_from_halt:
        if current is not OperatingState.Halted:
            return current
        if (
            facts.explicit_recovery
            and facts.inputs_released
            and facts.generation_incremented
        ):
            return OperatingState.Observe
        return current

    return current


def after_preflight(stack_ok: bool, model_ok: bool | None) -> OperatingState:
    """Boot lands in Observe once manifests and release are ready; model preflight does not block."""
    del stack_ok, model_ok
    return transition_operating_state(
        OperatingState.Boot,
        OperatingTransition.boot_complete,
        TransitionFacts(manifests_checked=True, inputs_released=True),
    )


def decider_may_arm(preflight_ok: bool) -> bool:
    return preflight_ok
