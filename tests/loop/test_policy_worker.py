from __future__ import annotations

import math
import threading
import time

import pytest

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.decider_policy import DeciderPolicy
from loop.adaptive.policy import DecisionError
from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
from loop.adaptive.snapshot import EntityView, Provenanced, StateSnapshotV2

from tests.loop.test_decider_policy import _candidates, _directive, _snapshot


class _FakeDecider:
    def __init__(
        self,
        *,
        delay_s: float = 0.0,
        response: dict[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.calls = 0
        self._delay = delay_s
        self._response = response
        self._error = error
        self._started = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    def wait_until_called(self, timeout: float = 2.0) -> None:
        if not self._started.wait(timeout):
            raise TimeoutError("fake decider was not invoked")

    def release(self) -> None:
        self._resume.set()

    def block_next(self) -> None:
        self._started.clear()
        self._resume.clear()

    def system_one(self, state, questions, *, independent, layout):
        self.calls += 1
        self._started.set()
        self._resume.wait(timeout=5.0)
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _good_response(choice: str = "left") -> dict[str, object]:
    other = "right" if choice == "left" else "left"
    return {
        "answers": {
            "action": {
                "type": "choice",
                "choice": choice,
                "confidence": 0.7,
                "probabilities": {choice: 0.7, other: 0.3},
            }
        }
    }


def _worker(
    fake: _FakeDecider,
    *,
    timeout_s: float = 1.0,
) -> PolicyWorker:
    policy = DeciderPolicy(fake, catalog_revision="catalog-1")
    worker = PolicyWorker(policy, inference_timeout_s=timeout_s)
    return worker


def test_submit_with_fewer_than_two_candidates_skips_worker() -> None:
    fake = _FakeDecider(response=_good_response())
    worker = _worker(fake)

    turn = worker.submit(_snapshot(), _directive(), (_candidates()[0],))
    result = worker.poll(turn)

    assert turn == 0
    assert fake.calls == 0
    assert result.status is PolicyTurnStatus.UNAVAILABLE
    assert result.reason == "decider_requires_at_least_two_candidates"
    worker.shutdown()


def test_frozen_request_is_stable() -> None:
    fake = _FakeDecider(response=_good_response())
    worker = _worker(fake)
    snap, directive, candidates = _snapshot(), _directive(), _candidates()

    first = worker.submit(snap, directive, candidates)
    ready = _wait_ready(worker, first)
    second = worker.submit(snap, directive, candidates)
    again = _wait_ready(worker, second)

    assert ready.decision is not None and again.decision is not None
    assert ready.decision.candidate_id == again.decision.candidate_id == "left"
    assert ready.decision.probabilities == pytest.approx(again.decision.probabilities)
    assert fake.calls == 2
    worker.shutdown()


def test_candidate_reorder_is_position_insensitive() -> None:
    fake = _FakeDecider(response=_good_response("left"))
    worker = _worker(fake)
    snap, directive = _snapshot(), _directive()
    forward = (
        CandidateAction("right", "key:right", "Move toward the open path."),
        CandidateAction("left", "key:left", "Move away from the hazard."),
    )
    reverse = tuple(reversed(forward))

    t1 = worker.submit(snap, directive, forward)
    r1 = _wait_ready(worker, t1)
    t2 = worker.submit(snap, directive, reverse)
    r2 = _wait_ready(worker, t2)

    assert r1.decision and r2.decision
    assert r1.decision.candidate_id == r2.decision.candidate_id == "left"
    assert r1.decision.probabilities == pytest.approx(r2.decision.probabilities, rel=1e-6, abs=1e-6)
    worker.shutdown()


@pytest.mark.parametrize(
    "response,reason_substr",
    [
        ({"answers": {}}, "choice"),
        ({"answers": {"action": {"type": "choice", "choice": "left"}}}, "probabilities"),
        (
            {
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "left",
                        "confidence": 0.7,
                        "probabilities": {"left": 0.7, "wrong": 0.3},
                    }
                }
            },
            "match",
        ),
        (
            {
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "left",
                        "confidence": math.nan,
                        "probabilities": {"left": math.nan, "right": 0.0},
                    }
                }
            },
            "finite",
        ),
    ],
)
def test_malformed_responses_are_unavailable(
    response: dict[str, object],
    reason_substr: str,
) -> None:
    fake = _FakeDecider(response=response)
    worker = _worker(fake)
    turn = worker.submit(_snapshot(), _directive(), _candidates())
    result = _wait_terminal(worker, turn)

    assert result.status is PolicyTurnStatus.UNAVAILABLE
    assert reason_substr in result.reason.lower()
    worker.shutdown()


def test_timeout_marks_turn_unavailable() -> None:
    fake = _FakeDecider(response=_good_response(), delay_s=0.2)
    worker = _worker(fake, timeout_s=0.05)
    t0 = 1000.0
    turn = worker.submit(_snapshot(), _directive(), _candidates(), timeout_s=0.05, now=t0)
    pending = worker.poll(turn, now=t0 + 0.01)
    assert pending.status is PolicyTurnStatus.PENDING
    timed_out = worker.poll(turn, now=t0 + 0.2)
    assert timed_out.status is PolicyTurnStatus.UNAVAILABLE
    assert timed_out.reason == "inference_deadline"
    worker.shutdown()


def test_late_result_after_superseded_turn_is_discarded() -> None:
    fake = _FakeDecider(response=_good_response())
    fake.block_next()
    worker = _worker(fake, timeout_s=5.0)
    first = worker.submit(_snapshot(), _directive(), _candidates())
    fake.wait_until_called()
    second = worker.submit(_snapshot(), _directive(), _candidates())
    fake.release()
    first_done = _wait_terminal(worker, first, timeout_s=3.0)
    second_done = _wait_ready(worker, second, timeout_s=3.0)

    assert first_done.status is PolicyTurnStatus.UNAVAILABLE
    assert first_done.reason == "superseded"
    assert second_done.status is PolicyTurnStatus.READY
    worker.shutdown()


def test_worker_failure_is_unavailable() -> None:
    fake = _FakeDecider(error=RuntimeError("CUDA out of memory"))
    worker = _worker(fake)
    turn = worker.submit(_snapshot(), _directive(), _candidates())
    result = _wait_terminal(worker, turn)

    assert result.status is PolicyTurnStatus.UNAVAILABLE
    assert result.reason == "worker_failed:RuntimeError"
    worker.shutdown()


def _wait_ready(
    worker: PolicyWorker,
    turn_id: int,
    *,
    timeout_s: float = 2.0,
) -> object:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = worker.poll(turn_id)
        if result.status is PolicyTurnStatus.READY:
            return result
        if result.status is PolicyTurnStatus.UNAVAILABLE:
            pytest.fail(f"unexpected unavailable: {result.reason}")
        time.sleep(0.01)
    pytest.fail("timed out waiting for ready policy turn")


def _wait_terminal(
    worker: PolicyWorker,
    turn_id: int,
    *,
    timeout_s: float = 2.0,
) -> object:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = worker.poll(turn_id)
        if result.status is not PolicyTurnStatus.PENDING:
            return result
        time.sleep(0.01)
    pytest.fail("timed out waiting for terminal policy turn")
