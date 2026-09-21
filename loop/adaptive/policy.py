"""Product policy protocol and decision validation (deterministic, random, Decider)."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Protocol

from loop.adaptive.candidates import ActionCatalog, ActionMask, CandidateAction, Directive
from loop.adaptive.snapshot import StateSnapshotV2


class DecisionError(ValueError):
    pass


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


class Policy(Protocol):
    def score(
        self,
        snapshot: StateSnapshotV2,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision: ...


def validate_decision(
    decision: StructuredDecision,
    mask: ActionMask,
    catalog: ActionCatalog,
    snapshot: StateSnapshotV2,
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
    if decision.episode_generation != snapshot.episode_generation:
        raise DecisionError("episode_generation mismatch")
    if decision.directive_revision != directive.revision:
        raise DecisionError("directive_revision mismatch")
    if decision.catalog_revision != catalog.revision:
        raise DecisionError("catalog_revision mismatch")
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
        snapshot: StateSnapshotV2,
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
        return StructuredDecision(
            candidate_id=chosen,
            confidence=1.0,
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=snapshot.episode_generation,
            snapshot_id=snapshot.snapshot_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )


class RandomPolicy:
    def __init__(self, rng: random.Random, *, catalog_revision: str = "") -> None:
        self._rng = rng
        self.catalog_revision = catalog_revision

    def score(
        self,
        snapshot: StateSnapshotV2,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision:
        if not candidates:
            raise DecisionError("empty")
        chosen = self._rng.choice(candidates)
        probs = {c.action_id: (1.0 if c.action_id == chosen.action_id else 0.0) for c in candidates}
        return StructuredDecision(
            candidate_id=chosen.action_id,
            confidence=1.0 / len(candidates),
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=snapshot.episode_generation,
            snapshot_id=snapshot.snapshot_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )
