"""Product policy protocol and decision validation (deterministic, random, Decider)."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, replace
from typing import Protocol

from loop.adaptive.candidates import ActionCatalog, ActionMask, CandidateAction, Directive
from loop.adaptive.contracts import NativeObservation, ObservationQuality
from loop.adaptive.snapshot import (
    EntityView,
    RelationView,
    StateSnapshotV2,
    assert_no_privileged,
)


class DecisionError(ValueError):
    pass


@dataclass(frozen=True)
class ReactiveField:
    value: object
    confidence: float
    unknown: bool


@dataclass(frozen=True)
class ReactiveState:
    snapshot_id: str
    schema_revision_id: str
    episode_id: str
    episode_generation: int
    mode: str | None
    confidence: float
    fields: dict[str, ReactiveField]
    entities: tuple[EntityView, ...]
    relations: tuple[RelationView, ...]
    prior_verified_outcome: dict[str, object] | None = None


def reactive_state_from_native(observation: NativeObservation) -> ReactiveState:
    snap = observation.snapshot
    fields = {
        name: ReactiveField(value, 1.0, value is None)
        for name, value in sorted(snap.fields.items())
    }
    entities = tuple(
        EntityView(
            type=str(info.get("type_id", "unknown")),
            stable_id=entity_id,
            attributes={},
            confidence=1.0,
        )
        for entity_id, info in sorted(snap.entities.items())
    )
    state = ReactiveState(
        snapshot_id=snap.snapshot_id,
        schema_revision_id=snap.schema_revision_id,
        episode_id=observation.episode_id,
        episode_generation=observation.episode_generation,
        mode=snap.mode_id,
        confidence=1.0 if observation.quality is ObservationQuality.trusted else 0.0,
        fields=fields,
        entities=entities,
        relations=(),
        prior_verified_outcome=None,
    )
    assert_no_privileged(state)
    return state


def reactive_state_from_v2(snapshot: StateSnapshotV2) -> ReactiveState:
    fields = {
        name: ReactiveField(field.value, field.confidence, field.unknown)
        for name, field in snapshot.fields.items()
    }
    entities = tuple(
        EntityView(
            type=entity.type,
            stable_id=entity.stable_id,
            attributes={},
            confidence=entity.confidence,
        )
        for entity in snapshot.entities
    )
    prior: dict[str, object] | None = None
    if snapshot.recent_events:
        last = snapshot.recent_events[-1]
        if isinstance(last, dict):
            prior = {
                k: last[k]
                for k in sorted(last)
                if k in ("outcome", "verified", "action_id", "success")
            } or None
    state = ReactiveState(
        snapshot_id=snapshot.snapshot_id,
        schema_revision_id=snapshot.schema_revision_id,
        episode_id=snapshot.episode_id,
        episode_generation=snapshot.episode_generation,
        mode=snapshot.mode,
        confidence=snapshot.confidence,
        fields=fields,
        entities=entities,
        relations=snapshot.relations,
        prior_verified_outcome=prior,
    )
    assert_no_privileged(state)
    return state


def coerce_reactive(snapshot: StateSnapshotV2 | ReactiveState) -> ReactiveState:
    if isinstance(snapshot, ReactiveState):
        return snapshot
    return reactive_state_from_v2(snapshot)


def reactive_episode_meta(snapshot: StateSnapshotV2 | ReactiveState) -> tuple[str, int]:
    if isinstance(snapshot, ReactiveState):
        return snapshot.snapshot_id, snapshot.episode_generation
    return snapshot.snapshot_id, snapshot.episode_generation


@dataclass(frozen=True)
class StructuredDecision:
    candidate_id: str
    confidence: float
    probabilities: dict[str, float]
    decided_at: float
    episode_generation: int
    snapshot_id: str
    directive_revision: int
    catalog_revision: str
    bound_entity_id: str | None = None
    scene_id: str | None = None


def project_reactive_for_model(reactive: ReactiveState, directive: Directive) -> ReactiveState:
    """Apply directive attention to the model request projection only."""

    if not directive.attention:
        return reactive
    keep = frozenset(directive.attention)
    fields = {name: field for name, field in reactive.fields.items() if name in keep}
    return replace(reactive, fields=fields)


class Policy(Protocol):
    def score(
        self,
        snapshot: StateSnapshotV2 | ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision: ...


def validate_decision(
    decision: StructuredDecision,
    mask: ActionMask,
    catalog: ActionCatalog,
    snapshot: StateSnapshotV2 | ReactiveState,
    directive: Directive,
) -> StructuredDecision:
    if decision.candidate_id not in mask.allowed_ids:
        raise DecisionError("candidate not in mask")
    catalog_ids = {c.action_id for c in catalog.candidates}
    if decision.candidate_id not in catalog_ids:
        raise DecisionError("candidate not in catalog")
    supplied = {c.action_id for c in catalog.candidates if c.action_id in mask.allowed_ids}
    if not set(decision.probabilities).issubset(supplied):
        raise DecisionError("probability keys")
    snap_id, episode_gen = reactive_episode_meta(snapshot)
    if decision.episode_generation != episode_gen:
        raise DecisionError("episode_generation mismatch")
    if decision.directive_revision != directive.revision:
        raise DecisionError("directive_revision mismatch")
    if decision.catalog_revision != catalog.revision:
        raise DecisionError("catalog_revision mismatch")
    if decision.snapshot_id != snap_id:
        raise DecisionError("snapshot_id mismatch")
    return decision


class DeterministicPolicy:
    def __init__(
        self,
        preferred_id: str | None = None,
        *,
        catalog_revision: str = "",
    ) -> None:
        self._preferred_id = preferred_id
        self.catalog_revision = catalog_revision

    def score(
        self,
        snapshot: StateSnapshotV2 | ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision:
        if not candidates:
            raise DecisionError("empty")
        chosen = candidates[0].action_id
        if self._preferred_id:
            for c in candidates:
                if c.action_id == self._preferred_id:
                    chosen = c.action_id
                    break
        probs = {c.action_id: (1.0 if c.action_id == chosen else 0.0) for c in candidates}
        snap_id, episode_gen = reactive_episode_meta(snapshot)
        return StructuredDecision(
            candidate_id=chosen,
            confidence=1.0,
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=episode_gen,
            snapshot_id=snap_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
            scene_id=snap_id,
        )


class RandomPolicy:
    def __init__(self, rng: random.Random, *, catalog_revision: str = "") -> None:
        self._rng = rng
        self.catalog_revision = catalog_revision

    def score(
        self,
        snapshot: StateSnapshotV2 | ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision:
        if not candidates:
            raise DecisionError("empty")
        chosen = self._rng.choice(candidates)
        probs = {c.action_id: (1.0 if c.action_id == chosen.action_id else 0.0) for c in candidates}
        snap_id, episode_gen = reactive_episode_meta(snapshot)
        return StructuredDecision(
            candidate_id=chosen.action_id,
            confidence=1.0 / len(candidates),
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=episode_gen,
            snapshot_id=snap_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
            scene_id=snap_id,
        )
