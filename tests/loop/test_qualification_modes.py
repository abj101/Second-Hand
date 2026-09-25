"""Qualification stages, metrics, and Choose context integration (offline)."""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.cli import (
    _perception_collect,
    _shadow_policy_worker,
    cmd_collect,
)
from loop.adaptive.controller import (
    Executor,
    NativeShadowConfig,
    NativeShadowLoop,
    registry_offer_diagnostics,
)
from loop.adaptive.policy import (
    DecisionError,
    DeterministicPolicy,
    ReactiveField,
    ReactiveState,
    StructuredDecision,
    validate_decision,
)
from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
from loop.adaptive.schema import (
    ActionInstance,
    ActionSurface,
    SceneGraph,
    SceneNode,
    StateSnapshot,
    empty_schema,
)
from loop.adaptive.session import Session
from loop.adaptive.telemetry import (
    TelemetryStore,
    qualification_metrics,
    qualification_passed,
)
from loop.adaptive.world import NeutralWorld
from loop.os.window import CaptureProvenance, WindowTarget


def _dummy_target() -> WindowTarget:
    return WindowTarget(
        hwnd=1, title="dummy", left=0, top=0, width=320, height=240
    )


def _patch_live_world(monkeypatch) -> NeutralWorld:
    world = NeutralWorld()
    world.reset(0)
    target = _dummy_target()
    monkeypatch.setattr("loop.os.window.list_windows", lambda _title: [target])
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _t: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _t: True)

    def _grab(_target):
        return world.observe()[0].convert("RGB")

    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda target: (
            _grab(target),
            CaptureProvenance(
                source="hwnd-imagegrab",
                client_size=(320, 240),
                content_box=(0.0, 0.0, 1.0, 1.0),
                integrity="trusted",
                reason="test",
            ),
        ),
    )
    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", lambda _im: world.ocr_tokens())
    return world


def _graph(*rows: tuple[str, str, dict]) -> SceneGraph:
    nodes = []
    for i, (rid, label, extra) in enumerate(rows):
        attrs = {"registry_id": rid, "label": label, **extra}
        nodes.append(
            SceneNode(
                f"node-{rid}",
                (0.1 + i * 0.05, 0.1, 0.2 + i * 0.05, 0.2),
                (),
                attrs,
            )
        )
    return SceneGraph("frame-1", tuple(nodes))


def _snap(schema, *, action_ids: tuple[str, ...]) -> StateSnapshot:
    rev = schema.revision.revision_id
    surface = ActionSurface(
        snapshot_id="snap-q",
        schema_revision_id=rev,
        action_ids=action_ids,
        actions=tuple(
            ActionInstance(
                action_id=aid,
                parameters=(),
                domains=(),
                expected_effects=(),
            )
            for aid in action_ids
        ),
    )
    return StateSnapshot(
        snapshot_id="snap-q",
        schema_revision_id=rev,
        mode_id="menu",
        fields={},
        entities={},
        surface=surface,
    )


class _CountingAsyncPolicy:
    off_thread = True
    catalog_revision = ""

    def __init__(self, *, choice: str = "reg-a") -> None:
        self.calls = 0
        self._choice = choice

    def score(self, snapshot, directive, candidates):
        self.calls += 1
        probs = {c.action_id: (1.0 if c.action_id == self._choice else 0.0) for c in candidates}
        return StructuredDecision(
            candidate_id=self._choice,
            confidence=1.0,
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=0,
            snapshot_id=getattr(snapshot, "snapshot_id", "snap-q"),
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )


def test_perception_stage_constructs_no_reaction_workers(monkeypatch):
    _patch_live_world(monkeypatch)
    constructed: list[object] = []
    real_init = PolicyWorker.__init__

    def _track_init(self, policy, **kwargs):
        constructed.append(policy)
        return real_init(self, policy, **kwargs)

    monkeypatch.setattr(PolicyWorker, "__init__", _track_init)
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", MagicMock())

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp), window_selector="dummy")
        rc, summary = _perception_collect(session, "dummy", 3)
        assert rc != 0
        assert summary["stage"] == "perception"
        assert constructed == []
        assert summary["gates"]["qwen_load"]["pass"] is False
        assert summary["gates"]["stability"]["reason"] == "trusted_frames"


def test_shadow_stage_sends_no_native_input(monkeypatch):
    _patch_live_world(monkeypatch)
    mouse_execute = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse_execute)

    with tempfile.TemporaryDirectory() as tmp:
        rc = cmd_collect(
            [
                "--workspace",
                str(tmp),
                "--window",
                "dummy",
                "--steps",
                "2",
                "--stage",
                "shadow",
            ]
        )
        assert rc != 0
        summary = json.loads((Path(tmp) / "summary.json").read_text(encoding="utf-8"))
        assert summary["gates"]["qwen_load"]["pass"] is False
        mouse_execute.assert_not_called()


class _BlockingAsyncPolicy(_CountingAsyncPolicy):
    def __init__(self) -> None:
        super().__init__()
        import threading

        self._started = threading.Event()
        self._resume = threading.Event()
        self._resume.set()

    def block_next(self) -> None:
        self._started.clear()
        self._resume.clear()

    def score(self, snapshot, directive, candidates):
        self.calls += 1
        self._started.set()
        self._resume.wait(timeout=2.0)
        probs = {
            c.action_id: (1.0 if c.action_id == self._choice else 0.0) for c in candidates
        }
        return StructuredDecision(
            candidate_id=self._choice,
            confidence=1.0,
            probabilities=probs,
            decided_at=time.monotonic(),
            episode_generation=0,
            snapshot_id=getattr(snapshot, "snapshot_id", "snap-q"),
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )


def test_zero_candidates_no_submit_one_deterministic_two_one_score():
    session = Session(Path(tempfile.mkdtemp()))
    policy = _BlockingAsyncPolicy()
    worker = PolicyWorker(policy, inference_timeout_s=2.0)
    telemetry = TelemetryStore(session.workspace / "react.jsonl", workspace=session.workspace)
    loop = NativeShadowLoop(
        session,
        worker,
        Executor(),
        telemetry,
        config=NativeShadowConfig(decision_deadline_s=0.5),
    )
    graph0 = SceneGraph("f0", ())
    snap0 = _snap(empty_schema(), action_ids=())
    loop.process_frame(
        snap0,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        scene_graph=graph0,
    )
    assert policy.calls == 0

    graph1 = _graph(("reg-a", "One", {}))
    snap1 = _snap(empty_schema(), action_ids=("reg-a",))
    loop.process_frame(
        snap1,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        scene_graph=graph1,
    )
    assert policy.calls == 0

    graph2 = _graph(("reg-a", "A", {}), ("reg-b", "B", {}))
    snap2 = _snap(empty_schema(), action_ids=("reg-a", "reg-b"))
    policy.block_next()
    loop.process_frame(
        snap2,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        scene_graph=graph2,
    )
    assert policy._started.wait(2.0)
    assert policy.calls == 1
    loop.process_frame(
        snap2,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        scene_graph=graph2,
    )
    assert policy.calls == 1
    policy._resume.set()
    loop.process_frame(
        snap2,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        scene_graph=graph2,
    )
    assert policy.calls == 1
    loop.close()


def test_qwen_requested_load_failure_nonzero_and_summary_not_qwen(monkeypatch):
    def _boom(**_kwargs):
        raise RuntimeError("weights missing")

    monkeypatch.setattr(
        "loop.adaptive.qwen_policy.QwenReactionPolicy.from_pretrained",
        _boom,
    )
    _worker, _executor, backend = _shadow_policy_worker(
        inference_timeout_s=1.0,
        preflight_ok=True,
        qwen_requested=True,
    )
    assert _worker is None
    assert backend["load_status"] == "failed"
    assert backend["backend"] != "qwen"


def test_stale_catalog_revision_rejected_on_apply():
    session = Session(Path(tempfile.mkdtemp()))
    worker = PolicyWorker(_CountingAsyncPolicy(), inference_timeout_s=1.0)
    loop = NativeShadowLoop(
        session,
        worker,
        Executor(),
        TelemetryStore(session.workspace / "react.jsonl"),
        config=NativeShadowConfig(decision_deadline_s=0.5),
    )
    from loop.adaptive.candidates import ActionCatalog, ActionMask
    from loop.adaptive.controller import _PendingTurn

    catalog = ActionCatalog(
        "catalog-live",
        (
            CandidateAction("reg-a", "reg-a", "A"),
            CandidateAction("reg-b", "reg-b", "B"),
        ),
    )
    directive = Directive(
        revision=1,
        episode_generation=0,
        intent="x",
        ttl_s=30.0,
        published_at=0.0,
    )
    snap = _snap(empty_schema(), action_ids=("reg-a", "reg-b"))
    from loop.adaptive.snapshot import build_native_observation

    from loop.adaptive.contracts import ObservationQuality

    native_obs = build_native_observation(
        snap,
        episode_id="ep",
        episode_generation=0,
        capture_ts_monotonic=0.0,
        publication_ts_monotonic=0.0,
        capture_integrity="trusted",
        capture_provenance=("test",),
        quality=ObservationQuality.trusted,
        hwnd=1,
        pid=1,
        target_identity={},
    )
    mask = ActionMask(("reg-a", "reg-b"), {})
    pending = _PendingTurn(
        turn_id=1,
        observation=native_obs,
        catalog=catalog,
        directive=directive,
        mask=mask,
        filtered=catalog.candidates,
        deterministic_id=None,
        submitted_at=0.0,
    )
    bad = StructuredDecision(
        candidate_id="reg-a",
        confidence=1.0,
        probabilities={"reg-a": 1.0, "reg-b": 0.0},
        decided_at=0.0,
        episode_generation=0,
        snapshot_id="snap-q",
        directive_revision=1,
        catalog_revision="catalog-stale",
    )
    source, selected, accepted, reason, _digest = loop._apply_worker_decision(
        pending, bad, focused=True, now=1.0
    )
    assert source == "unavailable"
    assert not accepted
    assert "catalog_revision mismatch" in reason
    loop.close()


def test_qualification_metrics_hand_calculated():
    frames = [
        {
            "track_ids": ["t1"],
            "ocr_node_ids": ("ocr_a",),
            "registry_ids": ["r1"],
            "eligible_offer_ids": ["r1"],
            "field_values": {"score": 1},
            "ambiguity_count": 0,
            "exclusion_reasons": {},
            "mode_id": "m",
            "revision_id": "rev1",
            "surface_action_ids": ["a1"],
        },
        {
            "track_ids": ["t1", "t2"],
            "ocr_node_ids": ("ocr_a",),
            "registry_ids": ["r1"],
            "eligible_offer_ids": ["r1"],
            "field_values": {"score": 2},
            "ambiguity_count": 1,
            "exclusion_reasons": {"ambiguous": 1},
            "mode_id": "m",
            "revision_id": "rev1",
            "surface_action_ids": ["a1"],
        },
        {
            "track_ids": ["t2"],
            "ocr_node_ids": ("ocr_a",),
            "registry_ids": ["r1", "r2"],
            "eligible_offer_ids": ["r1"],
            "field_values": {"score": None},
            "ambiguity_count": 0,
            "exclusion_reasons": {},
            "mode_id": "m",
            "revision_id": "rev1",
            "surface_action_ids": ["a1"],
        },
        {
            "track_ids": ["t9"],
            "ocr_node_ids": ("ocr_b",),
            "registry_ids": ["r9"],
            "eligible_offer_ids": [],
            "field_values": {},
            "ambiguity_count": 0,
            "exclusion_reasons": {},
            "mode_id": "m",
            "revision_id": "rev1",
            "surface_action_ids": [],
        },
    ]
    metrics = qualification_metrics(frames, schema_field_ids=("score",), warmup=3)
    assert metrics["raw_region_track_stability"] < 1.0
    assert metrics["track_stability"] == metrics["raw_region_track_stability"]
    assert metrics["field_continuity"] == metrics["ocr_node_set_continuity"]["whole_run"]
    assert metrics["ocr_node_set_continuity"]["post_warmup"] == 1.0
    empty_schema_metrics = qualification_metrics(frames[:1], schema_field_ids=(), warmup=3)
    block = empty_schema_metrics["promoted_field_coverage"]["whole_run"]
    assert block["status"] == "not_applicable"
    assert block["denominator"] == 0
    assert metrics["required_target_coverage"]["whole_run"]["status"] == "unavailable"


def _stable_frame(offer: str | None = "r1") -> dict:
    offers = [] if offer is None else [offer]
    return {
        "track_ids": ["t1"] if offer else [],
        "ocr_node_ids": ("ocr_a",) if offer else (),
        "registry_ids": ["r1"] if offer else [],
        "eligible_offer_ids": offers,
        "field_values": {"score": 1} if offer else {},
        "ambiguity_count": 0,
        "exclusion_reasons": {},
        "mode_id": "m",
        "revision_id": "rev1",
        "surface_action_ids": ["a1"] if offer else [],
    }


def test_empty_stable_eligible_surface_does_not_admit_useful_perception():
    frames = [_stable_frame(None) for _ in range(33)]
    metrics = qualification_metrics(frames, warmup=3)
    assert metrics["eligible_surface_stability"]["post_warmup"] is None
    assert metrics["eligible_surface_stability"]["post_warmup_nonempty_frames"] == 0
    ok, reason = qualification_passed(
        metrics,
        trusted_frames=30,
        identities=[{"hwnd": 1} for _ in frames],
    )
    assert ok is False
    assert reason == "empty_stable_surface"


def test_transition_empty_frames_do_not_break_useful_surface_stability():
    frames = [_stable_frame("r1") for _ in range(3)]
    frames.extend(
        _stable_frame("r1") if i % 2 == 0 else _stable_frame(None) for i in range(30)
    )
    metrics = qualification_metrics(frames, warmup=3)
    assert metrics["eligible_surface_stability"]["post_warmup"] == 1.0
    assert metrics["eligible_surface_stability"]["post_warmup_empty_frames"] > 0
    assert metrics["eligible_surface_stability"]["post_warmup_nonempty_frames"] > 0
    ok, reason = qualification_passed(
        metrics,
        trusted_frames=30,
        identities=[{"hwnd": 1} for _ in frames],
    )
    assert (ok, reason) == (True, "ok")


def test_required_target_coverage_miss_fails_when_labels_exist():
    frames = [_stable_frame("r1") for _ in range(33)]
    metrics = qualification_metrics(
        frames, warmup=3, required_target_ids=("r1", "missing")
    )
    ok, reason = qualification_passed(
        metrics,
        trusted_frames=30,
        identities=[{"hwnd": 1} for _ in frames],
    )
    assert ok is False
    assert reason == "required_target_coverage"


def test_semantic_required_withholds_unknown_candidates():
    graph = _graph(
        ("reg-a", "Ok", {"semantic_status": "confirmed", "semantic_class": "control"}),
        ("reg-b", "Maybe", {"semantic_status": "unresolved", "semantic_class": "unknown"}),
    )
    session = Session(Path(tempfile.mkdtemp()))
    offers, exclusions, _amb = registry_offer_diagnostics(
        graph, session.ledger, semantic_required=True
    )
    ids = {item.offer_id for item in offers}
    assert ids == {"reg-a"}
    assert exclusions.get("semantic_unresolved", 0) >= 1


def test_worker_submit_validates_catalog_context():
    policy = _CountingAsyncPolicy()
    worker = PolicyWorker(policy, inference_timeout_s=1.0)
    reactive = ReactiveState(
        snapshot_id="snap-a",
        schema_revision_id="s1",
        episode_id="ep",
        episode_generation=0,
        mode="menu",
        confidence=1.0,
        fields={"x": ReactiveField(1, 1.0, False)},
        entities=(),
        relations=(),
    )
    directive = Directive(
        revision=1,
        episode_generation=0,
        intent="choose",
        ttl_s=30.0,
        published_at=0.0,
    )
    candidates = (
        CandidateAction("reg-a", "reg-a", "A"),
        CandidateAction("reg-b", "reg-b", "B"),
    )
    from loop.adaptive.candidates import ActionCatalog, ActionMask

    turn = worker.submit(
        reactive,
        directive,
        candidates,
        catalog_revision="cat-a",
    )
    deadline = time.monotonic() + 2.0
    result = None
    while time.monotonic() < deadline:
        result = worker.poll(turn)
        if result.status is not PolicyTurnStatus.PENDING:
            break
        time.sleep(0.01)
    assert result is not None and result.decision is not None
    with pytest.raises(DecisionError, match="catalog_revision mismatch"):
        validate_decision(
            replace(result.decision, catalog_revision="cat-b"),
            ActionMask(("reg-a", "reg-b"), {}),
            ActionCatalog("cat-a", candidates),
            reactive,
            directive,
        )
    worker.shutdown()
