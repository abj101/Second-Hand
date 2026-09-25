"""Request-scoped Choose context through PolicyWorker (no model weights)."""

from __future__ import annotations

import json
import threading
import time

import pytest
from PIL import Image

from loop.adaptive.candidates import ActionCatalog, ActionMask, CandidateAction, Directive
from loop.adaptive.decider_policy import build_decider_state
from loop.adaptive.policy import (
    DecisionError,
    ReactiveField,
    ReactiveState,
    StructuredDecision,
    reactive_episode_meta,
    validate_decision,
)
from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
from loop.adaptive.qwen_policy import QwenReactionPolicy, choice_from_text, render_choice_prompt
_COORD_FORBIDDEN = frozenset({"x", "y", "bbox", "coordinate", "coordinates"})


def _reactive(
    *,
    snapshot_id: str = "snap-a",
    episode_generation: int = 0,
) -> ReactiveState:
    return ReactiveState(
        snapshot_id=snapshot_id,
        schema_revision_id="schema-1",
        episode_id="ep-1",
        episode_generation=episode_generation,
        mode="menu",
        confidence=1.0,
        fields={"score": ReactiveField(1, 1.0, False)},
        entities=(),
        relations=(),
    )


def _directive(*, revision: int = 1, episode_generation: int = 0) -> Directive:
    return Directive(
        revision=revision,
        episode_generation=episode_generation,
        intent="choose",
        ttl_s=30.0,
        published_at=0.0,
    )


def _pair() -> tuple[CandidateAction, CandidateAction]:
    return (
        CandidateAction("act-a", "act-a", "Option A"),
        CandidateAction("act-b", "act-b", "Option B"),
    )


def _catalog(revision: str, candidates: tuple[CandidateAction, ...] | None = None) -> ActionCatalog:
    return ActionCatalog(revision, candidates or _pair())


def _mask(*ids: str) -> ActionMask:
    return ActionMask(ids, {})


class _BlockingFakePolicy:
    """Returns the policy object's live catalog_revision (simulates Qwen bug)."""

    def __init__(self, *, catalog_revision: str = "catalog-a", choice: str = "act-a") -> None:
        self.catalog_revision = catalog_revision
        self.calls = 0
        self._choice = choice
        self._started = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._error: BaseException | None = None

    def block_next(self) -> None:
        self._started.clear()
        self._resume.clear()

    def wait_until_called(self, timeout: float = 2.0) -> None:
        if not self._started.wait(timeout):
            raise TimeoutError("policy.score was not invoked")

    def release(self) -> None:
        self._resume.set()

    def score(
        self,
        snapshot: ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision:
        self.calls += 1
        self._started.set()
        self._resume.wait(timeout=5.0)
        if self._error is not None:
            raise self._error
        snap_id, episode_gen = reactive_episode_meta(snapshot)
        probs = {c.action_id: (1.0 if c.action_id == self._choice else 0.0) for c in candidates}
        return StructuredDecision(
            candidate_id=self._choice,
            confidence=1.0,
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=episode_gen,
            snapshot_id=snap_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )


def _assert_no_coordinate_keys(obj: object, *, path: str = "") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            lowered = str(key).lower()
            assert lowered not in _COORD_FORBIDDEN, f"coordinate leak at {path}.{key!r}"
            _assert_no_coordinate_keys(value, path=f"{path}.{key}")
        return
    if isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            _assert_no_coordinate_keys(value, path=f"{path}[{index}]")


def _wait_ready(worker: PolicyWorker, turn_id: int, *, timeout_s: float = 3.0) -> object:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = worker.poll(turn_id)
        if result.status is PolicyTurnStatus.READY:
            return result
        if result.status is PolicyTurnStatus.UNAVAILABLE:
            pytest.fail(f"unexpected unavailable: {result.reason}")
        time.sleep(0.01)
    pytest.fail("timed out waiting for ready")


def test_sequential_catalogs_validate_against_their_own_revision() -> None:
    policy = _BlockingFakePolicy(catalog_revision="will-be-overwritten")
    worker = PolicyWorker(policy)
    snap, directive, candidates = _reactive(), _directive(), _pair()

    turn_a = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-a",
    )
    result_a = _wait_ready(worker, turn_a)
    assert result_a.decision is not None
    validate_decision(
        result_a.decision,
        _mask("act-a", "act-b"),
        _catalog("catalog-a"),
        snap,
        directive,
    )

    turn_b = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-b",
    )
    result_b = _wait_ready(worker, turn_b)
    assert result_b.decision is not None
    validate_decision(
        result_b.decision,
        _mask("act-a", "act-b"),
        _catalog("catalog-b"),
        snap,
        directive,
    )
    assert policy.calls == 2
    worker.shutdown()


def test_inflight_catalog_b_submit_supersedes_a_but_b_stays_clean() -> None:
    policy = _BlockingFakePolicy(catalog_revision="catalog-a")
    policy.block_next()
    worker = PolicyWorker(policy)
    snap, directive, candidates = _reactive(), _directive(), _pair()

    turn_a = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-a",
    )
    policy.wait_until_called()
    policy.catalog_revision = "catalog-b"

    turn_b = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-b",
    )
    policy.release()
    result_a = worker.poll(turn_a)
    result_b = _wait_ready(worker, turn_b)

    assert result_a.status is PolicyTurnStatus.UNAVAILABLE
    assert result_a.reason == "superseded"
    assert result_b.decision is not None
    assert result_b.decision.catalog_revision == "catalog-b"
    validate_decision(
        result_b.decision,
        _mask("act-a", "act-b"),
        _catalog("catalog-b"),
        snap,
        directive,
    )
    worker.shutdown()


def test_stamped_a_decision_rejects_current_catalog_b() -> None:
    """Job-scoped stamp: catalog A decision does not validate against catalog B."""
    policy = _BlockingFakePolicy(catalog_revision="catalog-a")
    worker = PolicyWorker(policy)
    snap, directive, candidates = _reactive(), _directive(), _pair()
    turn = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-a",
    )
    policy.catalog_revision = "catalog-b"
    result = _wait_ready(worker, turn)
    assert result.decision is not None
    assert result.decision.catalog_revision == "catalog-a"
    with pytest.raises(DecisionError, match="catalog_revision mismatch"):
        validate_decision(
            result.decision,
            _mask("act-a", "act-b"),
            _catalog("catalog-b"),
            snap,
            directive,
        )
    worker.shutdown()


def test_stale_snapshot_id_rejects_on_validation() -> None:
    policy = _BlockingFakePolicy()
    worker = PolicyWorker(policy)
    snap = _reactive(snapshot_id="snap-frozen")
    directive, candidates = _directive(), _pair()
    catalog = _catalog("catalog-x")

    turn = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-x",
        snapshot_id="snap-frozen",
    )
    result = _wait_ready(worker, turn)
    assert result.decision is not None
    with pytest.raises(DecisionError, match="snapshot_id mismatch"):
        validate_decision(
            result.decision,
            _mask("act-a", "act-b"),
            catalog,
            _reactive(snapshot_id="snap-current"),
            directive,
        )
    worker.shutdown()


def test_stale_directive_revision_rejects_on_validation() -> None:
    policy = _BlockingFakePolicy()
    worker = PolicyWorker(policy)
    snap, candidates = _reactive(), _pair()
    submit_directive = _directive(revision=1)
    current_directive = _directive(revision=2)
    catalog = _catalog("catalog-x")

    turn = worker.submit(
        snap,
        submit_directive,
        candidates,
        catalog_revision="catalog-x",
        directive_revision=1,
    )
    result = _wait_ready(worker, turn)
    assert result.decision is not None
    with pytest.raises(DecisionError, match="directive_revision mismatch"):
        validate_decision(
            result.decision,
            _mask("act-a", "act-b"),
            catalog,
            snap,
            current_directive,
        )
    worker.shutdown()


def test_stale_episode_generation_rejects_on_validation() -> None:
    policy = _BlockingFakePolicy()
    worker = PolicyWorker(policy)
    snap = _reactive(episode_generation=0)
    directive, candidates = _directive(episode_generation=0), _pair()
    catalog = _catalog("catalog-x")

    turn = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-x",
        episode_generation=0,
    )
    result = _wait_ready(worker, turn)
    assert result.decision is not None
    with pytest.raises(DecisionError, match="episode_generation mismatch"):
        validate_decision(
            result.decision,
            _mask("act-a", "act-b"),
            catalog,
            _reactive(episode_generation=9),
            directive,
        )
    worker.shutdown()


def test_policy_catalog_mutation_before_score_returns_job_revision() -> None:
    policy = _BlockingFakePolicy(catalog_revision="catalog-a")
    policy.block_next()
    worker = PolicyWorker(policy)
    snap, directive, candidates = _reactive(), _directive(), _pair()

    turn = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="catalog-a",
    )
    policy.wait_until_called()
    policy.catalog_revision = "catalog-b"
    policy.release()
    result = _wait_ready(worker, turn)
    assert result.decision is not None
    assert result.decision.catalog_revision == "catalog-a"
    worker.shutdown()


def test_job_image_isolated_from_later_submit_crop() -> None:
    policy = QwenReactionPolicy(None, None)
    seen: list[Image.Image | None] = []
    in_generate = threading.Event()
    release = threading.Event()
    release.set()

    def _generate(_prompt: str, *, image: Image.Image | None = None) -> str:
        in_generate.set()
        release.wait(timeout=5.0)
        seen.append(image)
        return "act-a"

    policy._generate = _generate  # type: ignore[method-assign]
    worker = PolicyWorker(policy)

    image_a = Image.new("RGB", (4, 4), color=(255, 0, 0))
    image_b = Image.new("RGB", (4, 4), color=(0, 0, 255))
    snap, directive, candidates = _reactive(), _directive(), _pair()

    release.clear()
    in_generate.clear()
    turn_a = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="cat",
        job_image=image_a,
    )
    in_generate.wait(timeout=2.0)
    policy._crop = image_b
    turn_b = worker.submit(
        snap,
        directive,
        candidates,
        catalog_revision="cat",
        job_image=image_b,
    )
    release.set()
    assert worker.poll(turn_a).reason == "superseded"
    _wait_ready(worker, turn_b)

    assert len(seen) == 2
    assert seen[0] is not None and seen[0].getpixel((0, 0)) == (255, 0, 0)
    assert seen[1] is not None and seen[1].getpixel((0, 0)) == (0, 0, 255)
    worker.shutdown()


def test_reaction_state_has_no_coordinate_fields() -> None:
    snap, directive, candidates = _reactive(), _directive(), _pair()
    state = build_decider_state(snap, directive)
    _assert_no_coordinate_keys(state)
    prompt = render_choice_prompt(state, candidates)
    _assert_no_coordinate_keys(json.loads(prompt.splitlines()[0]))


def test_choice_from_text_accepts_only_offered_ids() -> None:
    assert choice_from_text("act-b", ("act-a", "act-b")) == "act-b"
    with pytest.raises(DecisionError, match="not offered"):
        choice_from_text("act-z", ("act-a", "act-b"))


def test_score_called_once_per_job_second_poll_is_silent() -> None:
    policy = _BlockingFakePolicy()
    worker = PolicyWorker(policy)
    snap, directive, candidates = _reactive(), _directive(), _pair()
    turn = worker.submit(snap, directive, candidates, catalog_revision="c1")
    result = _wait_ready(worker, turn)
    assert result.decision is not None
    assert policy.calls == 1
    again = worker.poll(turn)
    assert again.status is PolicyTurnStatus.READY
    assert policy.calls == 1
    worker.shutdown()


def test_score_exception_surfaces_type_in_unavailable_reason() -> None:
    policy = _BlockingFakePolicy()
    policy._error = RuntimeError("boom")
    worker = PolicyWorker(policy)
    turn = worker.submit(_reactive(), _directive(), _pair(), catalog_revision="c1")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        result = worker.poll(turn)
        if result.status is not PolicyTurnStatus.PENDING:
            break
        time.sleep(0.01)
    assert result.status is PolicyTurnStatus.UNAVAILABLE
    assert "RuntimeError" in result.reason
    worker.shutdown()
