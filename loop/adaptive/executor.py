"""Product dispatch gate and watchdog; benchmark path uses ``Environment.step`` only."""

from __future__ import annotations

import time
from dataclasses import dataclass

from loop.adaptive.candidates import ActionCatalog, Directive
from loop.adaptive.policy import StructuredDecision
from loop.adaptive.snapshot import StateSnapshotV2


@dataclass(frozen=True)
class DispatchReceipt:
    accepted: bool
    action_id: str | None
    reason: str
    watchdog: str | None
    dispatched_at: float


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
    ) -> DispatchReceipt:
        now = time.monotonic() if now is None else now

        def reject(reason: str) -> DispatchReceipt:
            return DispatchReceipt(
                accepted=False,
                action_id=None,
                reason=reason,
                watchdog="release",
                dispatched_at=now,
            )

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

        self.held = decision.candidate_id
        return DispatchReceipt(
            accepted=True,
            action_id=decision.candidate_id,
            reason="ok",
            watchdog=None,
            dispatched_at=now,
        )
