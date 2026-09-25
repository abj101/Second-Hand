"""Semantic confirmation component (fake gateway only)."""

from __future__ import annotations

import threading
import time

from PIL import Image

from loop.adaptive.controller import GenerationStore
from loop.adaptive.orchestrator import OrchestratorConfig, SyncOrchestratorRunner
from loop.adaptive.semantic_confirmation import (
    CandidateEvidence,
    SemanticConfirmer,
    SemanticJobStatus,
    SemanticWorker,
    brief_digest,
)
from loop.model.gateway import ModelCall
from loop.os.capture import EncodedFrame, encode_image


def _frame() -> EncodedFrame:
    return encode_image(Image.new("RGB", (64, 48), color=(40, 80, 120)))


def _cand(cid: str = "cand-a", appearance: str = "app-1") -> CandidateEvidence:
    return CandidateEvidence(cid, appearance, (0.1, 0.2, 0.3, 0.4))


def _ok_payload(
    *,
    target: str = "tgt-1",
    generation: str = "7",
    cid: str = "cand-a",
    cls: str = "control",
    rationale: str = "looks clickable",
) -> dict:
    return {
        "target_identity": target,
        "screen_generation": generation,
        "classifications": [
            {"candidate_id": cid, "class": cls, "rationale": rationale},
        ],
    }


class FakeGateway:
    def __init__(self, script: list[ModelCall | Exception]) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def complete_json(self, **kwargs) -> ModelCall:
        self.calls.append(kwargs)
        if not self._script:
            raise RuntimeError("unexpected gateway call")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _status(result, cid: str = "cand-a"):
    return next(s for s in result.candidates if s.candidate_id == cid)


def test_two_distinct_observations_confirm_same_class():
    gw = FakeGateway(
        [
            ModelCall("", _ok_payload(), 12.0, "fake-model", 10, 5),
            ModelCall("", _ok_payload(), 11.0, "fake-model", 10, 4),
        ]
    )
    c = SemanticConfirmer(gw, model="fake-model")
    frame = _frame()
    cand = _cand()
    r1 = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r1).status == "withheld"
    r2 = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-2",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r2).status == "confirmed"
    assert _status(r2).semantic_class == "control"
    assert len(gw.calls) == 2


def test_repeated_same_observation_id_does_not_confirm():
    gw = FakeGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    c = SemanticConfirmer(gw, model="fake-model")
    frame = _frame()
    cand = _cand()
    c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[cand],
        frame=frame,
    )
    r2 = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r2).status == "withheld"
    assert len(gw.calls) == 1


def test_rejects_partial_invalid_duplicate_and_extra_action_field():
    frame = _frame()
    cand = _cand()
    partial = {
        "target_identity": "tgt-1",
        "screen_generation": "7",
        "classifications": [],
    }
    bad_class = _ok_payload(cls="menu")
    duplicate = {
        "target_identity": "tgt-1",
        "screen_generation": "7",
        "classifications": [
            {"candidate_id": "cand-a", "class": "control", "rationale": "a"},
            {"candidate_id": "cand-a", "class": "unknown", "rationale": "b"},
        ],
    }
    extra_action = {
        "target_identity": "tgt-1",
        "screen_generation": "7",
        "classifications": [
            {
                "candidate_id": "cand-a",
                "class": "control",
                "rationale": "a",
                "click": True,
            },
        ],
    }
    unknown_id = {
        "target_identity": "tgt-1",
        "screen_generation": "7",
        "classifications": [
            {"candidate_id": "other", "class": "control", "rationale": "a"},
        ],
    }
    for parsed in (partial, bad_class, duplicate, extra_action, unknown_id):
        gw = FakeGateway([ModelCall("", parsed, 1.0, "fake-model", 2, 2)])
        c = SemanticConfirmer(gw, model="fake-model")
        r = c.observe(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-x",
            candidates=[cand],
            frame=frame,
        )
        assert _status(r).status == "unresolved"


def test_conflict_stays_unresolved_until_new_cycle():
    gw = FakeGateway(
        [
            ModelCall("", _ok_payload(cls="control"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(cls="decoration"), 1.0, "fake-model", 1, 1),
        ]
    )
    c = SemanticConfirmer(gw, model="fake-model")
    frame = _frame()
    cand = _cand()
    c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[cand],
        frame=frame,
    )
    r_conflict = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-2",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r_conflict).status == "unresolved"
    c.start_new_confirmation_cycle()
    gw._script.extend(
        [
            ModelCall("", _ok_payload(cls="unknown"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(cls="unknown"), 1.0, "fake-model", 1, 1),
        ]
    )
    c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-3",
        candidates=[cand],
        frame=frame,
    )
    r_ok = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-4",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r_ok).status == "confirmed"
    assert _status(r_ok).semantic_class == "unknown"


def test_timeout_and_exception_return_unavailable_without_raise():
    frame = _frame()
    cand = _cand()
    for err in (TimeoutError("slow"), RuntimeError("boom")):
        gw = FakeGateway([err])
        c = SemanticConfirmer(gw, model="fake-model")
        r = c.observe(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[cand],
            frame=frame,
        )
        assert _status(r).status == "unavailable"


def test_none_gateway_is_unavailable_with_zero_calls():
    c = SemanticConfirmer(None, model="fake-model")
    r = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[_cand()],
        frame=_frame(),
    )
    assert _status(r).status == "unavailable"
    assert c.telemetry == ()


def test_stale_target_or_generation_does_not_reuse_confirmation():
    gw = FakeGateway(
        [
            ModelCall("", _ok_payload(target="tgt-1"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(target="tgt-1"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(target="tgt-2"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(target="tgt-2"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(target="tgt-1", generation="8"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(target="tgt-1", generation="8"), 1.0, "fake-model", 1, 1),
        ]
    )
    c = SemanticConfirmer(gw, model="fake-model")
    frame = _frame()
    cand = _cand()
    c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[cand],
        frame=frame,
    )
    r1 = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-2",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r1).status == "confirmed"
    c.observe(
        target_identity="tgt-2",
        screen_generation=7,
        observation_id="obs-3",
        candidates=[cand],
        frame=frame,
    )
    r_other = c.observe(
        target_identity="tgt-2",
        screen_generation=7,
        observation_id="obs-4",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r_other).status == "confirmed"
    c.observe(
        target_identity="tgt-1",
        screen_generation=8,
        observation_id="obs-5",
        candidates=[cand],
        frame=frame,
    )
    r_gen = c.observe(
        target_identity="tgt-1",
        screen_generation=8,
        observation_id="obs-6",
        candidates=[cand],
        frame=frame,
    )
    assert _status(r_gen).status == "confirmed"
    assert len(gw.calls) == 6


def test_confirmed_identical_frames_make_zero_further_calls():
    gw = FakeGateway(
        [
            ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1),
        ]
    )
    c = SemanticConfirmer(gw, model="fake-model")
    frame = _frame()
    cand = _cand()
    for oid in ("obs-1", "obs-2", "obs-3", "obs-4"):
        c.observe(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id=oid,
            candidates=[cand],
            frame=frame,
        )
    assert len(gw.calls) == 2


def test_third_attempt_is_exhausted_without_call():
    gw = FakeGateway(
        [
            ModelCall("", {"bad": True}, 1.0, "fake-model", 1, 1),
            ModelCall("", {"bad": True}, 1.0, "fake-model", 1, 1),
        ]
    )
    c = SemanticConfirmer(gw, model="fake-model")
    frame = _frame()
    cand = _cand()
    for oid in ("obs-1", "obs-2", "obs-3"):
        r = c.observe(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id=oid,
            candidates=[cand],
            frame=frame,
        )
    assert _status(r).status == "exhausted"
    assert len(gw.calls) == 2


def test_telemetry_records_model_latency_outcome_and_tokens():
    gw = FakeGateway([ModelCall("", _ok_payload(), 42.5, "fake-model", 99, 7)])
    c = SemanticConfirmer(gw, model="fake-model")
    r = c.observe(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[_cand()],
        frame=_frame(),
    )
    assert len(r.telemetry) == 1
    row = r.telemetry[0]
    assert row.model == "fake-model"
    assert row.latency_ms == 42.5
    assert row.prompt_tokens == 99
    assert row.output_tokens == 7
    assert row.outcome == "success"
    assert row.observation_id == "obs-1"
    assert c.telemetry[0].outcome == "success"


# ---------------------------------------------------------------------------
# F1: bounded worker lifecycle / result contract
# ---------------------------------------------------------------------------


class BlockingGateway:
    """Gateway that holds every call until ``release``; records real call count."""

    def __init__(self, script: list[ModelCall | Exception]) -> None:
        self._script = list(script)
        self._lock = threading.Lock()
        self.calls = 0
        self.recorded: list[dict] = []
        self.gate = threading.Event()

    def complete_json(self, **kwargs) -> ModelCall:
        with self._lock:
            self.calls += 1
            self.recorded.append(kwargs)
        self.gate.wait(timeout=5.0)
        if not self._script:
            raise RuntimeError("unexpected gateway call")
        return self._script.pop(0)

    def release(self) -> None:
        self.gate.set()


def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.01) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def _wait_ready(worker: SemanticWorker, job, *, timeout: float = 3.0):
    _wait_for(
        lambda: worker.poll(job.job_id).status == SemanticJobStatus.READY, timeout=timeout
    )
    return worker.poll(job.job_id)


def test_worker_runs_one_inference_and_keeps_only_the_latest_pending_job():
    gw = BlockingGateway(
        [
            ModelCall("", _ok_payload(cid="cand-a"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(cid="cand-c"), 1.0, "fake-model", 1, 1),
        ]
    )
    worker = SemanticWorker(SemanticConfirmer(gw, model="fake-model"))
    try:
        job_a = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand("cand-a")],
            frame=_frame(),
        )
        _wait_for(lambda: gw.calls == 1)  # A is now blocked in the provider
        job_b = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-2",
            candidates=[_cand("cand-b")],
            frame=_frame(),
        )
        job_c = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-3",
            candidates=[_cand("cand-c")],
            frame=_frame(),
        )
        time.sleep(0.1)
        assert gw.calls == 1, "second inference must wait for the in-flight one"

        gw.release()
        result_c = _wait_ready(worker, job_c)
        assert result_c.status == SemanticJobStatus.READY
        assert [s.candidate_id for s in result_c.candidates] == ["cand-c"]
        assert gw.calls == 2, "the dropped middle job must never run"
        assert worker.poll(job_b.job_id).reason == "superseded"
        assert worker.poll(job_a.job_id).status == SemanticJobStatus.UNAVAILABLE  # latest-value
    finally:
        gw.release()
        worker.shutdown(wait=True, timeout_s=2.0)


def test_worker_coalesces_identical_unchanged_requests():
    gw = BlockingGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    worker = SemanticWorker(SemanticConfirmer(gw, model="fake-model"))
    try:
        first = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand()],
            frame=_frame(),
        )
        _wait_for(lambda: gw.calls == 1)
        again = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand()],
            frame=_frame(),
        )
        assert again.job_id == first.job_id, "identical request must coalesce, not enqueue"
        assert worker.coalesced_submissions == 1
        gw.release()
        _wait_ready(worker, first)
        assert gw.calls == 1

        finished_replay = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand()],
            frame=_frame(),
        )
        assert finished_replay.job_id == first.job_id, "finished identical request is not re-run"
        assert gw.calls == 1
    finally:
        gw.release()
        worker.shutdown(wait=True, timeout_s=2.0)


def test_blocked_semantic_provider_does_not_stall_directive_publication():
    gw = BlockingGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    worker = SemanticWorker(SemanticConfirmer(gw, model="fake-model"))
    store = GenerationStore()
    runner = SyncOrchestratorRunner(
        store, episode_generation=1, config=OrchestratorConfig()
    )
    try:
        worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand()],
            frame=_frame(),
        )
        _wait_for(lambda: gw.calls == 1)  # provider is blocked
        before = store.latest().revision
        runner.notify("schema_change")
        _wait_for(lambda: store.latest().revision > before)
        assert worker.is_running  # still blocked in the provider

        started = time.monotonic()
        stopped = worker.shutdown(wait=True, timeout_s=0.1)
        assert time.monotonic() - started < 1.0, "shutdown must not wait for translation"
        assert stopped is False, "a blocked provider must not make shutdown appear complete"
    finally:
        gw.release()
        runner.close()
        worker.shutdown(wait=False)


def _ready_result() -> tuple[SemanticWorker, object]:
    gw = FakeGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    worker = SemanticWorker(
        SemanticConfirmer(gw, model="fake-model", game_brief={"objective": "survive"})
    )
    job = worker.submit(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[_cand()],
        frame=_frame(),
        schema_revision="rev-1",
    )
    return worker, _wait_ready(worker, job)


def test_validate_result_accepts_unchanged_evidence_and_rejects_stale_fields():
    worker, result = _ready_result()
    try:
        assert result.status == SemanticJobStatus.READY
        assert result.origin_observation_id == "obs-1"
        assert result.evidence_digest
        assert result.brief_digest == brief_digest({"objective": "survive"})

        accepted = worker.validate_result(
            result,
            fresh_observation_id="obs-2",
            fresh_trusted=True,
            target_identity_key="tgt-1",
            screen_generation=7,
            schema_revision="rev-1",
            brief_digest=result.brief_digest,
        )
        assert accepted.status == SemanticJobStatus.READY
        assert accepted.origin_observation_id == "obs-1"
        assert accepted.validation_observation_id == "obs-2"

        stale = (
            ({"target_identity_key": "tgt-2"}, "target_identity_changed"),
            ({"screen_generation": 8}, "screen_generation_changed"),
            ({"schema_revision": "rev-2"}, "schema_revision_changed"),
            ({"brief_digest": "deadbeef"}, "brief_changed"),
            ({"relevant_evidence_changed": True}, "relevant_evidence_changed"),
        )
        for extra, reason in stale:
            rejected = worker.validate_result(
                result, fresh_observation_id="obs-3", fresh_trusted=True, **extra
            )
            assert rejected.status == SemanticJobStatus.UNAVAILABLE
            assert rejected.reason == reason
        untrusted = worker.validate_result(
            result, fresh_observation_id="obs-3", fresh_trusted=False
        )
        assert untrusted.reason == "untrusted_validation_observation"
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_validation_requires_a_distinct_fresh_observation_and_no_replay():
    worker, result = _ready_result()
    try:
        same = worker.validate_result(
            result, fresh_observation_id=result.origin_observation_id, fresh_trusted=True
        )
        assert same.status == SemanticJobStatus.UNAVAILABLE
        assert same.reason == "validation_must_be_distinct_observation"

        first = worker.validate_result(result, fresh_observation_id="obs-2", fresh_trusted=True)
        assert first.validation_observation_id == "obs-2"
        replay = worker.validate_result(first, fresh_observation_id="obs-3", fresh_trusted=True)
        assert replay.status == SemanticJobStatus.UNAVAILABLE
        assert replay.reason == "already_validated"
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_required_second_confirmation_completes_on_a_new_observation_without_replay():
    gw = FakeGateway(
        [
            ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1),
        ]
    )
    worker = SemanticWorker(SemanticConfirmer(gw, model="fake-model"))
    try:
        frame = _frame()
        cand = _cand()
        first = _wait_ready(
            worker,
            worker.submit(
                target_identity="tgt-1",
                screen_generation=7,
                observation_id="obs-1",
                candidates=[cand],
                frame=frame,
            ),
        )
        assert _status(first).status == "withheld"
        assert len(gw.calls) == 1

        replay = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[cand],
            frame=frame,
        )
        assert replay.job_id == first.job_id
        assert len(gw.calls) == 1, "the first answer must not be replayed as new proof"

        second = _wait_ready(
            worker,
            worker.submit(
                target_identity="tgt-1",
                screen_generation=7,
                observation_id="obs-2",
                candidates=[cand],
                frame=frame,
            ),
        )
        assert _status(second).status == "confirmed"
        assert len(gw.calls) == 2
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_invalid_reply_produces_no_confirmation_or_schema_mutation():
    bad = ModelCall(
        "",
        {
            "target_identity": "tgt-1",
            "screen_generation": "7",
            "classifications": [
                {"candidate_id": "cand-a", "class": "menu", "rationale": "nope"}
            ],
        },
        1.0,
        "fake-model",
        1,
        1,
    )
    gw = FakeGateway([bad])
    worker = SemanticWorker(SemanticConfirmer(gw, model="fake-model"))
    try:
        result = _wait_ready(
            worker,
            worker.submit(
                target_identity="tgt-1",
                screen_generation=7,
                observation_id="obs-1",
                candidates=[_cand()],
                frame=_frame(),
            ),
        )
        assert result.status == SemanticJobStatus.READY
        assert _status(result).status == "unresolved"
        assert result.telemetry and result.telemetry[0].outcome == "rejected"
        assert not hasattr(worker, "apply_result"), "workers must not apply results"
        assert not hasattr(worker, "publish"), "workers must not publish directives"
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_poll_reports_unknown_and_expired_jobs_without_blocking():
    gw = BlockingGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    worker = SemanticWorker(SemanticConfirmer(gw, model="fake-model"), timeout_s=0.05)
    try:
        assert worker.poll(0).status == SemanticJobStatus.UNAVAILABLE
        assert worker.poll(0).reason == "unknown_job"
        job = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand()],
            frame=_frame(),
            timeout_s=0.05,
        )
        _wait_for(lambda: gw.calls == 1)
        time.sleep(0.15)
        expired = worker.poll(job.job_id)
        assert expired.status == SemanticJobStatus.UNAVAILABLE
        assert expired.reason == "semantic_deadline"
        assert expired.origin_observation_id == "obs-1"
    finally:
        gw.release()
        worker.shutdown(wait=True, timeout_s=2.0)
    assert worker.poll(999).reason == "unknown_job"


# ---------------------------------------------------------------------------
# F2: serialized worker-owned confirmer control
# ---------------------------------------------------------------------------


def _ready_worker(game_brief=None):
    gw = FakeGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    confirmer = SemanticConfirmer(gw, model="fake-model", game_brief=game_brief)
    worker = SemanticWorker(confirmer)
    job = worker.submit(
        target_identity="tgt-1",
        screen_generation=7,
        observation_id="obs-1",
        candidates=[_cand()],
        frame=_frame(),
    )
    return worker, confirmer, _wait_ready(worker, job)


def test_worker_controls_are_nonblocking_while_the_provider_is_blocked():
    gw = BlockingGateway([ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1)])
    confirmer = SemanticConfirmer(gw, model="fake-model", game_brief={"objective": "a"})
    worker = SemanticWorker(confirmer)
    try:
        worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand()],
            frame=_frame(),
        )
        _wait_for(lambda: gw.calls == 1)  # provider is blocked
        started = time.monotonic()
        assert worker.set_game_brief({"objective": "b"}) is True
        assert worker.clear_pending() is True
        assert worker.invalidate(["cand-a"]) is True
        assert worker.start_new_confirmation_cycle() is True
        assert time.monotonic() - started < 0.5, "controls must not wait for the provider"
        assert worker.is_running
        # queued controls are ordered, not applied, while inference is in flight
        assert confirmer.game_brief == {"objective": "a"}
    finally:
        gw.release()
        worker.shutdown(wait=True, timeout_s=2.0)


def test_worker_owns_brief_digest_and_applies_control_before_later_inference():
    gw = BlockingGateway(
        [
            ModelCall("", _ok_payload(cid="cand-a"), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(cid="cand-b"), 1.0, "fake-model", 1, 1),
        ]
    )
    confirmer = SemanticConfirmer(gw, model="fake-model", game_brief={"objective": "a"})
    worker = SemanticWorker(confirmer)
    try:
        first = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-1",
            candidates=[_cand("cand-a")],
            frame=_frame(),
        )
        assert first.brief_digest == brief_digest({"objective": "a"})
        _wait_for(lambda: gw.calls == 1)  # first inference is in flight

        worker.set_game_brief({"objective": "b"})
        assert confirmer.game_brief == {"objective": "a"}, "caller must not mutate the confirmer"
        second = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-2",
            candidates=[_cand("cand-b")],
            frame=_frame(),
        )
        assert second.brief_digest == brief_digest({"objective": "b"})
        assert second.brief_digest != first.brief_digest

        gw.release()
        _wait_ready(worker, second)
        assert confirmer.game_brief == {"objective": "b"}
        assert '"objective":"b"' in gw.recorded[1]["user"], "control must apply first"
        assert worker.poll(first.job_id).reason == "superseded"
    finally:
        gw.release()
        worker.shutdown(wait=True, timeout_s=2.0)


def test_queued_control_is_applied_before_the_next_inference_when_idle():
    gw = FakeGateway(
        [
            ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1),
            ModelCall("", _ok_payload(), 1.0, "fake-model", 1, 1),
        ]
    )
    confirmer = SemanticConfirmer(gw, model="fake-model", game_brief={"objective": "a"})
    worker = SemanticWorker(confirmer)
    try:
        _wait_ready(
            worker,
            worker.submit(
                target_identity="tgt-1",
                screen_generation=7,
                observation_id="obs-1",
                candidates=[_cand()],
                frame=_frame(),
            ),
        )
        worker.set_game_brief({"objective": "b"})
        _wait_for(lambda: confirmer.game_brief == {"objective": "b"})
        job = worker.submit(
            target_identity="tgt-1",
            screen_generation=7,
            observation_id="obs-2",
            candidates=[_cand()],
            frame=_frame(),
        )
        assert job.brief_digest == brief_digest({"objective": "b"})
        _wait_ready(worker, job)
        assert '"objective":"b"' in gw.calls[1]["user"]
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_brief_change_marks_old_result_unavailable():
    worker, confirmer, result = _ready_worker({"objective": "a"})
    try:
        assert result.status == SemanticJobStatus.READY
        worker.set_game_brief({"objective": "b"})
        _wait_for(lambda: worker.poll(result.job_id).reason == "brief_changed")
        stale = worker.poll(result.job_id)
        assert stale.status == SemanticJobStatus.UNAVAILABLE
        assert stale.origin_observation_id == "obs-1"
        assert confirmer.game_brief == {"objective": "b"}
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_unchanged_brief_control_leaves_result_available():
    worker, _, result = _ready_worker({"objective": "a"})
    try:
        worker.set_game_brief({"objective": "a"})
        _wait_for(lambda: worker.poll(result.job_id).status == SemanticJobStatus.READY)
        time.sleep(0.05)
        assert worker.poll(result.job_id).status == SemanticJobStatus.READY
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_invalidate_marks_only_matching_old_result_unavailable():
    worker, _, result = _ready_worker()
    try:
        worker.invalidate(["cand-b"])
        _wait_for(lambda: worker.poll(result.job_id).status == SemanticJobStatus.READY)
        time.sleep(0.05)
        assert worker.poll(result.job_id).status == SemanticJobStatus.READY
        worker.invalidate(["cand-a"])
        _wait_for(lambda: worker.poll(result.job_id).reason == "candidate_invalidated")
        assert worker.poll(result.job_id).status == SemanticJobStatus.UNAVAILABLE
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_clear_pending_and_new_cycle_reset_make_old_results_unavailable():
    worker, _, result = _ready_worker()
    try:
        worker.clear_pending()
        _wait_for(lambda: worker.poll(result.job_id).reason == "semantic_reset")
        assert worker.poll(result.job_id).status == SemanticJobStatus.UNAVAILABLE
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)

    worker, _, result = _ready_worker()
    try:
        worker.start_new_confirmation_cycle()
        _wait_for(lambda: worker.poll(result.job_id).reason == "confirmation_cycle_reset")
        assert worker.poll(result.job_id).status == SemanticJobStatus.UNAVAILABLE
    finally:
        worker.shutdown(wait=True, timeout_s=2.0)


def test_controls_after_shutdown_are_rejected_without_touching_the_confirmer():
    gw = FakeGateway([])
    confirmer = SemanticConfirmer(gw, model="fake-model", game_brief={"objective": "a"})
    worker = SemanticWorker(confirmer)
    worker.shutdown(wait=True, timeout_s=2.0)
    assert worker.set_game_brief({"objective": "b"}) is False
    assert worker.clear_pending() is False
    assert worker.invalidate(["cand-a"]) is False
    assert worker.start_new_confirmation_cycle() is False
    assert confirmer.game_brief == {"objective": "a"}
    assert gw.calls == []


