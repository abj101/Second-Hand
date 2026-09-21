"""Latest-value in-process Decider policy worker (one inference in flight)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.policy import DecisionError, Policy, ReactiveState, StructuredDecision
from loop.adaptive.snapshot import StateSnapshotV2

if TYPE_CHECKING:
    from collections.abc import Callable


class PolicyTurnStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class PolicyLoadTelemetry:
    env_manifest_hash: str = ""
    model_manifest_hash: str = ""
    load_latency_s: float = 0.0
    device: str = "cpu"
    dtype: str = "bfloat16"
    independent: bool = True
    layout: str = "state_first"
    use_graphs: bool = False


@dataclass(frozen=True)
class PolicyInferenceTelemetry:
    inference_latency_s: float
    peak_vram_mib: float | None = None


@dataclass(frozen=True)
class PolicyTurnResult:
    status: PolicyTurnStatus
    turn_id: int
    decision: StructuredDecision | None = None
    reason: str = ""
    load: PolicyLoadTelemetry | None = None
    inference: PolicyInferenceTelemetry | None = None


@dataclass(frozen=True)
class _Job:
    turn_id: int
    snapshot: StateSnapshotV2 | ReactiveState
    directive: Directive
    candidates: tuple[CandidateAction, ...]
    deadline_at: float
    submitted_at: float


@dataclass(frozen=True)
class _Finished:
    turn_id: int
    decision: StructuredDecision | None
    reason: str
    inference: PolicyInferenceTelemetry | None


class PolicyWorker:
    """Non-blocking latest-value wrapper around ``Policy.score``."""

    def __init__(
        self,
        policy: Policy,
        *,
        inference_timeout_s: float = 30.0,
        load_telemetry: PolicyLoadTelemetry | None = None,
        peak_vram_reader: Callable[[], float | None] | None = None,
    ) -> None:
        self._policy = policy
        self._default_timeout_s = inference_timeout_s
        self._load = load_telemetry or PolicyLoadTelemetry()
        self._peak_vram = peak_vram_reader
        self._lock = threading.Lock()  # ponytail: one worker thread; submit/poll only
        self._cond = threading.Condition(self._lock)
        self._turn = 0
        self._latest_turn = 0
        self._pending: _Job | None = None
        self._inflight: _Job | None = None
        self._finished: _Finished | None = None
        self._deadlines: dict[int, float] = {}
        self._shutdown = False
        self._thread = threading.Thread(target=self._loop, name="policy-worker", daemon=True)
        self._thread.start()

    @property
    def load_telemetry(self) -> PolicyLoadTelemetry:
        return self._load

    def submit(
        self,
        snapshot: StateSnapshotV2 | ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
        *,
        timeout_s: float | None = None,
        now: float | None = None,
    ) -> int:
        """Enqueue the newest turn; never blocks on inference."""

        if len(candidates) < 2:
            return 0

        monotonic_now = time.monotonic() if now is None else now
        limit = self._default_timeout_s if timeout_s is None else timeout_s
        with self._cond:
            self._turn += 1
            turn_id = self._turn
            self._latest_turn = turn_id
            job = _Job(
                turn_id=turn_id,
                snapshot=snapshot,
                directive=directive,
                candidates=candidates,
                deadline_at=monotonic_now + limit,
                submitted_at=monotonic_now,
            )
            self._pending = job
            self._deadlines[turn_id] = job.deadline_at
            self._cond.notify_all()
        return turn_id

    def poll(self, turn_id: int, *, now: float | None = None) -> PolicyTurnResult:
        """Non-blocking turn status; timed-out or superseded work is unavailable."""

        if turn_id <= 0:
            return PolicyTurnResult(
                PolicyTurnStatus.UNAVAILABLE,
                turn_id,
                reason="decider_requires_at_least_two_candidates",
                load=self._load,
            )

        monotonic_now = time.monotonic() if now is None else now
        with self._lock:
            latest_turn = self._latest_turn
            deadline = self._deadlines.get(turn_id)
            finished = self._finished if self._finished and self._finished.turn_id == turn_id else None
            inflight = self._inflight.turn_id if self._inflight else None

        if deadline is not None and monotonic_now > deadline:
            return PolicyTurnResult(
                PolicyTurnStatus.UNAVAILABLE,
                turn_id,
                reason="inference_deadline",
                load=self._load,
            )

        if turn_id < latest_turn:
            return PolicyTurnResult(
                PolicyTurnStatus.UNAVAILABLE,
                turn_id,
                reason="superseded",
                load=self._load,
            )

        if finished is not None:
            if finished.decision is not None:
                return PolicyTurnResult(
                    PolicyTurnStatus.READY,
                    turn_id,
                    decision=finished.decision,
                    load=self._load,
                    inference=finished.inference,
                )
            return PolicyTurnResult(
                PolicyTurnStatus.UNAVAILABLE,
                turn_id,
                reason=finished.reason or "policy_rejected",
                load=self._load,
                inference=finished.inference,
            )

        if inflight == turn_id or turn_id == latest_turn:
            return PolicyTurnResult(PolicyTurnStatus.PENDING, turn_id, load=self._load)

        return PolicyTurnResult(
            PolicyTurnStatus.UNAVAILABLE,
            turn_id,
            reason="unknown_turn",
            load=self._load,
        )

    def shutdown(self, *, wait: bool = True) -> None:
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()
        if wait:
            self._thread.join(timeout=5.0)

    def _loop(self) -> None:
        while True:
            with self._cond:
                while (
                    not self._shutdown
                    and self._pending is None
                    and self._inflight is None
                ):
                    self._cond.wait()
                if self._shutdown and self._pending is None and self._inflight is None:
                    return
                if self._pending is None or self._inflight is not None:
                    continue
                job = self._pending
                self._pending = None
                self._inflight = job

            started = time.monotonic()
            try:
                decision = self._policy.score(job.snapshot, job.directive, job.candidates)
            except DecisionError as exc:
                finished = _Finished(
                    job.turn_id,
                    None,
                    str(exc),
                    PolicyInferenceTelemetry(time.monotonic() - started, self._read_peak_vram()),
                )
            except Exception as exc:
                finished = _Finished(
                    job.turn_id,
                    None,
                    f"worker_failed:{type(exc).__name__}",
                    PolicyInferenceTelemetry(time.monotonic() - started, self._read_peak_vram()),
                )
            else:
                finished = _Finished(
                    job.turn_id,
                    decision,
                    "",
                    PolicyInferenceTelemetry(time.monotonic() - started, self._read_peak_vram()),
                )

            with self._cond:
                self._inflight = None
                if finished.turn_id != self._latest_turn:
                    self._finished = None
                elif job.deadline_at < time.monotonic():
                    self._finished = _Finished(
                        job.turn_id,
                        None,
                        "inference_deadline",
                        finished.inference,
                    )
                else:
                    self._finished = finished
                if self._pending is not None:
                    self._cond.notify_all()

    def _read_peak_vram(self) -> float | None:
        if self._peak_vram is None:
            return None
        try:
            return self._peak_vram()
        except Exception:
            return None
