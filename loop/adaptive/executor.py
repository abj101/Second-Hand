"""Product dispatch gate and watchdog; benchmark path uses ``Environment.step`` only."""

from __future__ import annotations

import time
from dataclasses import dataclass

from loop.adaptive.candidates import ActionCatalog, ActionMask, Directive, mask_digest
from loop.adaptive.contracts import ActuationLease, NativeObservation, ObservationQuality
from loop.adaptive.policy import StructuredDecision
from loop.adaptive.snapshot import StateSnapshotV2


@dataclass(frozen=True)
class NativeDispatchContext:
    observation: NativeObservation
    mask: ActionMask
    expected_mask_digest: str
    policy_revision: str
    surface_action_ids: frozenset[str]
    recovery_locked: bool = False
    lease_ttl_s: float = 0.5


@dataclass(frozen=True)
class DispatchReceipt:
    accepted: bool
    action_id: str | None
    reason: str
    watchdog: str | None
    dispatched_at: float
    lease: ActuationLease | None = None


class Executor:
    def __init__(
        self,
        *,
        snapshot_max_age_s: float = 0.5,
        decision_max_age_s: float = 0.25,
    ) -> None:
        self.snapshot_max_age_s = snapshot_max_age_s
        self.decision_max_age_s = decision_max_age_s
        self.held: str | None = None
        self.release_count = 0

    def reset(self) -> None:
        self.held = None

    def release_all(self, env, *, count: bool = True) -> None:
        env.release_all()
        self.held = None
        if count:
            self.release_count += 1

    def dispatch(
        self,
        decision: StructuredDecision,
        snapshot: StateSnapshotV2,
        catalog: ActionCatalog,
        directive: Directive,
        *,
        now: float | None = None,
        focused: bool = True,
        terminal: bool = False,
        native: NativeDispatchContext | None = None,
    ) -> DispatchReceipt:
        now = time.monotonic() if now is None else now

        def reject(reason: str) -> DispatchReceipt:
            return DispatchReceipt(
                accepted=False,
                action_id=None,
                reason=reason,
                watchdog="release",
                dispatched_at=now,
                lease=None,
            )

        if native is not None:
            obs = native.observation
            if decision.snapshot_id != obs.snapshot_id:
                return reject("snapshot_id_mismatch")
            if decision.snapshot_id != obs.snapshot.snapshot_id:
                return reject("snapshot_id_mismatch")
            if obs.schema_revision_id != obs.snapshot.schema_revision_id:
                return reject("schema_mismatch")
            if obs.quality is ObservationQuality.untrusted:
                return reject("untrusted_capture")
            if obs.missing_required_fields:
                return reject("missing_required_fields")
            if native.recovery_locked:
                return reject("recovery_locked")
            if mask_digest(native.mask) != native.expected_mask_digest:
                return reject("mask_mismatch")
            if decision.candidate_id not in native.mask.allowed_ids:
                return reject("masked")
            if decision.candidate_id not in native.surface_action_ids:
                return reject("not_on_surface")
            if obs.episode_generation != decision.episode_generation:
                return reject("generation_mismatch")
            if obs.observation_age_s > self.snapshot_max_age_s:
                return reject("stale_snapshot")
            if obs.hwnd <= 0 or obs.pid <= 0:
                return reject("target_identity")

        if snapshot.episode_generation != decision.episode_generation:
            return reject("generation_mismatch")
        if snapshot.observation_age_s > self.snapshot_max_age_s:
            return reject("stale_snapshot")
        if now - decision.decided_at > self.decision_max_age_s:
            return reject("stale_decision")
        if directive.revision != decision.directive_revision:
            return reject("directive_mismatch")
        if now - directive.published_at > directive.ttl_s:
            return reject("directive_expired")
        if directive.episode_generation != snapshot.episode_generation:
            return reject("generation_mismatch")
        if catalog.revision != decision.catalog_revision:
            return reject("catalog_mismatch")
        by_id = {c.action_id: c for c in catalog.candidates}
        cand = by_id.get(decision.candidate_id)
        if cand is None or not cand.available:
            return reject("unavailable")
        if not focused:
            return reject("unfocused")
        if terminal:
            return reject("terminal")

        lease: ActuationLease | None = None
        if native is not None:
            obs = native.observation
            lease = ActuationLease(
                episode_generation=obs.episode_generation,
                snapshot_id=obs.snapshot_id,
                schema_revision_id=obs.schema_revision_id,
                catalog_revision=catalog.revision,
                directive_revision=directive.revision,
                policy_revision=native.policy_revision,
                hwnd=obs.hwnd,
                pid=obs.pid,
                target_identity=dict(obs.target_identity),
                expires_at_monotonic=now + native.lease_ttl_s,
                selected_action_id=decision.candidate_id,
                hard_mask_digest=native.expected_mask_digest,
            )

        self.held = decision.candidate_id
        return DispatchReceipt(
            accepted=True,
            action_id=decision.candidate_id,
            reason="ok",
            watchdog=None,
            dispatched_at=now,
            lease=lease,
        )
