"""Product-owned candidate actions, directives, and action masks.

Optional fixture adapters supply benchmark catalogs; native compilation uses
``loop.adaptive.contracts`` (``NativeCandidate`` / ``NativeCatalog``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from loop.adaptive.contracts import (
    CatalogCompileError,
    NativeCandidate,
    NativeTrustedConstraints,
    ObservationQuality,
    compile_native_catalog,
)
from loop.adaptive.schema import ActionSpec, AdaptiveSchema, EffectPredicate, StateSnapshot


@dataclass(frozen=True)
class CandidateAction:
    action_id: str
    command: str
    description: str
    available: bool = True
    input_budget_s: float = 0.2
    suppress_no_change: bool = False
    retry_policy: str = "allow"


@dataclass(frozen=True)
class ActionCatalog:
    revision: str
    candidates: tuple[CandidateAction, ...]


@dataclass(frozen=True)
class ActionMask:
    allowed_ids: tuple[str, ...]
    blocked: dict[str, str]


@dataclass(frozen=True)
class Directive:
    revision: int
    episode_generation: int
    intent: str
    ttl_s: float
    published_at: float
    forbidden_ids: tuple[str, ...] = ()
    required_mode: str | None = None


@dataclass(frozen=True)
class MaskGateContext:
    """Optional native gates; ``None`` at compile time keeps benchmark behavior."""

    quality: ObservationQuality | None = None
    recovery_locked: bool = False
    terminal: bool = False
    snapshot_mode: str | None = None
    snapshot_confidence: float = 1.0
    min_snapshot_confidence: float = 0.0
    surface_ids: frozenset[str] = frozenset()
    disallowed_risk_levels: frozenset[str] = frozenset()
    action_risk: dict[str, str] | None = None
    cooldown_blocked: frozenset[str] = frozenset()


def mask_digest(mask: ActionMask) -> str:
    payload = {
        "allowed_ids": list(mask.allowed_ids),
        "blocked": {k: mask.blocked[k] for k in sorted(mask.blocked)},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(blob.encode("utf-8")).hexdigest()


def _effect_description(effects: tuple[EffectPredicate, ...]) -> str:
    if not effects:
        return "verified action"
    parts: list[str] = []
    for effect in sorted(effects, key=lambda e: (e.field_id, e.op, e.entity_id or "")):
        suffix = f" ({effect.entity_id})" if effect.entity_id else ""
        parts.append(f"{effect.field_id} {effect.op}{suffix}")
    return "; ".join(parts)


def native_constraints_from_spec(spec: ActionSpec) -> NativeTrustedConstraints:
    raw = spec.constraints
    missing = [k for k in ("risk", "retry", "cooldown_s", "max_plan_duration_s") if k not in raw]
    if missing:
        raise CatalogCompileError(
            f"action {spec.action_id!r} missing native constraints: {', '.join(missing)}"
        )
    return NativeTrustedConstraints(
        risk=str(raw["risk"]),
        retry=str(raw["retry"]),
        cooldown_s=float(raw["cooldown_s"]),
        max_plan_duration_s=float(raw["max_plan_duration_s"]),
    )


def compile_catalog_from_surface(
    schema: AdaptiveSchema,
    snapshot: StateSnapshot,
) -> ActionCatalog:
    """Compile opaque catalog + content hash from the current verified surface only."""

    surface = snapshot.surface
    specs = {action.action_id: action for action in schema.actions}
    instances = {inst.action_id: inst for inst in surface.actions}
    native: list[NativeCandidate] = []
    for action_id in sorted(surface.action_ids):
        spec = specs.get(action_id)
        inst = instances.get(action_id)
        if spec is None or inst is None:
            raise CatalogCompileError(f"surface action {action_id!r} missing schema instance")
        description = _effect_description(inst.expected_effects)
        native.append(
            NativeCandidate(
                action_id,
                description,
                native_constraints_from_spec(spec),
            )
        )
    compiled = compile_native_catalog(snapshot.schema_revision_id, tuple(native))
    candidates = tuple(
        CandidateAction(
            nc.action_id,
            nc.action_id,
            nc.description,
            available=True,
            retry_policy=nc.constraints.retry,
            input_budget_s=min(nc.constraints.max_plan_duration_s, 30.0),
        )
        for nc in compiled.candidates
    )
    return ActionCatalog(revision=compiled.revision, candidates=candidates)


def compile_mask(
    catalog: ActionCatalog,
    directive: Directive,
    *,
    gate: MaskGateContext | None = None,
) -> ActionMask:
    blocked: dict[str, str] = {}
    allowed: list[str] = []
    forbidden = set(directive.forbidden_ids)

    if gate is not None:
        if gate.terminal:
            for cand in catalog.candidates:
                blocked[cand.action_id] = "terminal"
            return ActionMask(allowed_ids=(), blocked=blocked)
        if gate.recovery_locked:
            for cand in catalog.candidates:
                blocked[cand.action_id] = "recovery_locked"
            return ActionMask(allowed_ids=(), blocked=blocked)
        if gate.quality is ObservationQuality.untrusted:
            for cand in catalog.candidates:
                blocked[cand.action_id] = "untrusted_capture"
            return ActionMask(allowed_ids=(), blocked=blocked)
        if gate.snapshot_confidence < gate.min_snapshot_confidence:
            for cand in catalog.candidates:
                blocked[cand.action_id] = "low_confidence"
            return ActionMask(allowed_ids=(), blocked=blocked)
        if directive.required_mode is not None and gate.snapshot_mode != directive.required_mode:
            for cand in catalog.candidates:
                blocked[cand.action_id] = "mode_mismatch"
            return ActionMask(allowed_ids=(), blocked=blocked)

    risks = gate.action_risk if gate is not None else None
    disallowed_risk = gate.disallowed_risk_levels if gate is not None else frozenset()
    cooldown_blocked = gate.cooldown_blocked if gate is not None else frozenset()
    surface_ids = gate.surface_ids if gate is not None else frozenset()

    for cand in catalog.candidates:
        if not cand.available:
            blocked[cand.action_id] = "unavailable"
            continue
        if cand.action_id in forbidden:
            blocked[cand.action_id] = "forbidden"
            continue
        if surface_ids and cand.action_id not in surface_ids:
            blocked[cand.action_id] = "not_on_surface"
            continue
        if cand.action_id in cooldown_blocked:
            blocked[cand.action_id] = "cooldown"
            continue
        if risks and cand.action_id in risks:
            level = risks[cand.action_id]
            if level in disallowed_risk:
                blocked[cand.action_id] = "risk_policy"
                continue
        allowed.append(cand.action_id)
    return ActionMask(allowed_ids=tuple(allowed), blocked=blocked)


def select_if_unambiguous(mask: ActionMask) -> str | None:
    if len(mask.allowed_ids) == 1:
        return mask.allowed_ids[0]
    return None
