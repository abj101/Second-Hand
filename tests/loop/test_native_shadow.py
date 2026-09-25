"""Wave 5 native shadow loop: hypothetical dispatch, no actuation."""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loop.adaptive.candidates import Directive
from loop.adaptive.controller import Executor, NativeShadowConfig, NativeShadowLoop
from loop.adaptive.decider_policy import DeciderPolicy
from loop.adaptive.policy import DeterministicPolicy
from loop.adaptive.policy_worker import PolicyWorker

from tests.loop.test_policy_worker import _FakeDecider, _good_response
from loop.adaptive.schema import (
    ActionInstance,
    ActionSpec,
    ActionSurface,
    EffectPredicate,
    PrimitiveStep,
    StateSnapshot,
    empty_schema,
)
from loop.adaptive.session import Session
from loop.adaptive.telemetry import TelemetryStore


def _constraints() -> dict:
    return {
        "risk": "low",
        "retry": "deny",
        "cooldown_s": 0.5,
        "max_plan_duration_s": 1.0,
    }


def _promoted_schema():
    schema = empty_schema()
    spec = ActionSpec(
        action_id="probe_click",
        parameters=(),
        preconditions=(),
        primitive_plan=(PrimitiveStep("click", ("target",)),),
        expected_effects=(EffectPredicate("score", "increased"),),
        constraints=_constraints(),
    )
    return replace(schema, actions=(spec,))


def _snap(schema, *, action_ids: tuple[str, ...] = ("probe_click",)) -> StateSnapshot:
    rev = schema.revision.revision_id
    surface = ActionSurface(
        snapshot_id="snap-shadow",
        schema_revision_id=rev,
        action_ids=action_ids,
        actions=(
            ActionInstance(
                action_id=action_ids[0],
                parameters=(),
                domains=(),
                expected_effects=(EffectPredicate("score", "increased"),),
            ),
        ),
    )
    return StateSnapshot(
        snapshot_id="snap-shadow",
        schema_revision_id=rev,
        mode_id="running",
        fields={"score": 1},
        entities={},
        surface=surface,
    )


def _loop(
    session: Session,
    tmp: Path,
    *,
    policy: object | None = None,
) -> NativeShadowLoop:
    if policy is None:
        policy = DeterministicPolicy()
    worker = PolicyWorker(policy, inference_timeout_s=1.0)
    telemetry = TelemetryStore(tmp / "react.jsonl", workspace=tmp)
    return NativeShadowLoop(
        session,
        worker,
        Executor(),
        telemetry,
        config=NativeShadowConfig(decision_deadline_s=0.5),
    )


def test_one_candidate_hypothetical_dispatch_no_act():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_promoted_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(session, root)
        try:
            snap = _snap(session.schema)
            shadow.process_frame(
                snap,
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
                focused=True,
                capture_ts_monotonic=time.monotonic(),
                capture_age_known=True,
                acquisition_origin="pre-grab-sync",
                observation_sequence=1,
            )
        finally:
            shadow.close()

        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        assert len(rows) == 1
        row = rows[0]
        assert row.decision_source == "deterministic"
        assert row.dispatch_accepted is True
        assert row.dispatch_reason == "ok"
        assert row.primitive_plan_digest
        assert len(row.primitive_plan_digest) == 64


def _probe_pair_response(choice: str = "probe_click") -> dict[str, object]:
    other = "probe_click_b" if choice == "probe_click" else "probe_click"
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


def test_two_candidates_uses_worker_and_logs_digest():
    fake = _FakeDecider(response=_probe_pair_response("probe_click"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        schema = _promoted_schema()
        schema = replace(
            schema,
            actions=(
                schema.actions[0],
                replace(schema.actions[0], action_id="probe_click_b"),
            ),
        )
        session = Session(root)
        session.accept_schema(schema)
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(
            session,
            root,
            policy=DeciderPolicy(fake, catalog_revision="catalog-1"),
        )
        try:
            snap = _snap(
                session.schema,
                action_ids=("probe_click", "probe_click_b"),
            )
            snap = replace(
                snap,
                surface=replace(
                    snap.surface,
                    action_ids=("probe_click", "probe_click_b"),
                    actions=(
                        snap.surface.actions[0],
                        replace(snap.surface.actions[0], action_id="probe_click_b"),
                    ),
                ),
            )
            shadow.process_frame(
                snap,
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
                focused=True,
            )
        finally:
            shadow.close()

        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        assert rows
        last = rows[-1]
        assert last.decision_source in ("DeciderPolicy", "DeciderPolicy_pending")
        assert fake.calls == 1
        if last.decision_source == "DeciderPolicy":
            assert last.primitive_plan_digest
            assert last.deterministic_action_id is not None


def test_zero_candidates_never_calls_decider():
    fake = _FakeDecider(response=_good_response())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_promoted_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(
            session,
            root,
            policy=DeciderPolicy(fake, catalog_revision="catalog-1"),
        )
        try:
            shadow.process_frame(
                _snap(session.schema),
                hwnd=1,
                pid=42,
                capture_integrity="untrusted",
            )
        finally:
            shadow.close()
        assert fake.calls == 0


def test_one_candidate_never_calls_decider():
    fake = _FakeDecider(response=_good_response())
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        schema = _promoted_schema()
        schema = replace(
            schema,
            actions=(
                schema.actions[0],
                replace(schema.actions[0], action_id="probe_click_b"),
            ),
        )
        session = Session(root)
        session.accept_schema(schema)
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(
            session,
            root,
            policy=DeciderPolicy(fake, catalog_revision="catalog-1"),
        )
        try:
            shadow.process_frame(
                _snap(session.schema, action_ids=("probe_click",)),
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
            )
        finally:
            shadow.close()
        assert fake.calls == 0
        row = shadow._telemetry.shadow_rows()[-1]  # noqa: SLF001
        assert row.decision_source == "deterministic"


def test_two_candidates_deterministic_policy_source_not_decider():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        schema = _promoted_schema()
        schema = replace(
            schema,
            actions=(
                schema.actions[0],
                replace(schema.actions[0], action_id="probe_click_b"),
            ),
        )
        session = Session(root)
        session.accept_schema(schema)
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(session, root)
        try:
            snap = _snap(
                session.schema,
                action_ids=("probe_click", "probe_click_b"),
            )
            snap = replace(
                snap,
                surface=replace(
                    snap.surface,
                    action_ids=("probe_click", "probe_click_b"),
                    actions=(
                        snap.surface.actions[0],
                        replace(snap.surface.actions[0], action_id="probe_click_b"),
                    ),
                ),
            )
            shadow.process_frame(
                snap,
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
            )
        finally:
            shadow.close()
        row = shadow._telemetry.shadow_rows()[-1]  # noqa: SLF001
        assert row.decision_source == "DeterministicPolicy"
        assert row.decision_source != "DeciderPolicy"


@pytest.fixture
def dummy_target():
    from loop.os.window import WindowTarget

    return WindowTarget(
        hwnd=1, title="dummy", left=0, top=0, width=320, height=240
    )


def test_cli_shadow_with_promoted_action_never_actuates(monkeypatch, dummy_target):
    from loop.adaptive.cli import cmd_collect
    from loop.adaptive.world import NeutralWorld
    from loop.os.window import CaptureProvenance

    world = NeutralWorld()
    world.reset(0)
    monkeypatch.setattr(
        "loop.os.window.list_windows",
        lambda _title: [dummy_target],
    )
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _target: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)

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
                acquisition_ts_monotonic=time.monotonic(),
                acquisition_origin="pre-grab-sync",
                acquisition_age_known=True,
            ),
        ),
    )
    monkeypatch.setattr("loop.adaptive.cli._live_ocr_tokens", lambda _im: world.ocr_tokens())
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _t: True)
    monkeypatch.setattr(
        "loop.os.window.target_identity",
        lambda target, selector="": {
            "selector": selector or "dummy",
            "hwnd": int(target.hwnd),
            "pid": 42,
            "exe": "test.exe",
        },
    )

    session_act = MagicMock()
    monkeypatch.setattr("loop.adaptive.session.Session.act", session_act)
    mouse_execute = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse_execute)
    run_plan = MagicMock()
    monkeypatch.setattr("loop.os.input_owner.NativeInputOwner.run_plan", run_plan)

    real_session_init = Session.__init__

    def _session_with_action(self, workspace, window_selector=None):
        real_session_init(self, workspace, window_selector=window_selector)
        self.accept_schema(_promoted_schema())

    monkeypatch.setattr(Session, "__init__", _session_with_action)

    orig_observe_factory = None

    def _patched_factory(session, target, assembler):
        observe = orig_observe_factory(session, target, assembler)

        def wrapped():
            recs, graph, snap, holder = observe()
            if not snap.surface.action_ids:
                snap = _snap(session.schema)
            wrapped.provenance = getattr(observe, "provenance", None)
            return recs, graph, snap, holder

        wrapped.provenance = None
        return wrapped

    import loop.adaptive.cli as cli_mod

    orig_observe_factory = cli_mod._live_observe_factory
    monkeypatch.setattr(cli_mod, "_live_observe_factory", _patched_factory)
    monkeypatch.setattr(
        cli_mod,
        "_shadow_policy_worker",
        lambda **_kwargs: (
            PolicyWorker(DeterministicPolicy(), inference_timeout_s=1.0),
            Executor(),
            {"backend": "deterministic", "load_status": "not_requested", "qwen_requested": False},
        ),
    )

    with tempfile.TemporaryDirectory() as tmp:
        rc = cmd_collect(["--workspace", tmp, "--window", "dummy", "--steps", "2"])
        assert rc != 0
        summary = json.loads(Path(tmp).joinpath("summary.json").read_text(encoding="utf-8"))
        assert summary["mode"] == "shadow"
        session_act.assert_not_called()
        mouse_execute.assert_not_called()
        run_plan.assert_not_called()
        shadow_path = Path(tmp) / "shadow_react.jsonl"
        assert shadow_path.is_file()
        lines = shadow_path.read_text(encoding="utf-8").strip().splitlines()
        assert lines
        row = json.loads(lines[-1])
        assert row.get("dispatch_accepted") is True or row.get("decision_source") == "observe"


class _FixedClock:
    """Deterministic monotonic clock for capture-to-publication delay tests."""

    def __init__(self, start: float) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _FakeTime:
    def __init__(self, clock: _FixedClock) -> None:
        self.monotonic = clock


def test_process_frame_passes_explicit_acquisition_fields(monkeypatch):
    from loop.adaptive import controller as controller_mod

    seen: list[dict] = []
    real_build = controller_mod.build_native_observation

    def _record(*args, **kwargs):
        seen.append(dict(kwargs))
        return real_build(*args, **kwargs)

    monkeypatch.setattr(controller_mod, "build_native_observation", _record)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_promoted_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(session, root)
        try:
            shadow.process_frame(
                _snap(session.schema),
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
                capture_ts_monotonic=12.5,
                capture_age_known=True,
                acquisition_origin="pre-grab-sync",
                observation_sequence=7,
            )
        finally:
            shadow.close()

    assert seen
    assert seen[-1]["capture_ts_monotonic"] == 12.5
    assert seen[-1]["capture_age_known"] is True
    assert seen[-1]["acquisition_origin"] == "pre-grab-sync"
    assert seen[-1]["observation_sequence"] == 7


def test_perception_delay_bounds_age_and_fails_authority(monkeypatch):
    from loop.adaptive import controller as controller_mod

    clock = _FixedClock(1000.0)
    monkeypatch.setattr(controller_mod, "time", _FakeTime(clock))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_promoted_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = _loop(session, root)  # Executor default snapshot_max_age_s=0.5
        capture_ts = clock()
        clock.advance(1.2)  # OCR/perception delay between capture and publication
        try:
            shadow.process_frame(
                _snap(session.schema),
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
                capture_ts_monotonic=capture_ts,
                capture_age_known=True,
                acquisition_origin="pre-grab-sync",
                observation_sequence=1,
            )
        finally:
            shadow.close()

        row = shadow._telemetry.shadow_rows()[-1]  # noqa: SLF001
        assert row.observation_age_s >= 1.2 - 1e-9
        assert row.dispatch_accepted is False
        assert row.dispatch_reason == "stale_snapshot"


def _offer_graph(*rows: tuple[str, str]):
    from loop.adaptive.schema import SceneGraph, SceneNode

    return SceneGraph(
        "frame-1",
        tuple(
            SceneNode(
                f"node-{rid}",
                (0.1 + i * 0.2, 0.1, 0.2 + i * 0.2, 0.2),
                (),
                {"registry_id": rid, "label": label},
            )
            for i, (rid, label) in enumerate(rows)
        ),
    )


class _RecordingOwner:
    def __init__(self) -> None:
        self.plans = []

    def run_plan(self, steps, bindings, *, scene) -> None:
        self.plans.append((steps, bindings, scene))

    def release_all(self, _reason: str) -> str:
        return "released"


def _offer_loop(session: Session, root: Path, owner: _RecordingOwner) -> NativeShadowLoop:
    return NativeShadowLoop(
        session,
        PolicyWorker(DeterministicPolicy(), inference_timeout_s=1.0),
        Executor(),
        TelemetryStore(root / "react.jsonl", workspace=root),
        config=NativeShadowConfig(actuate_transient_offers=True),
        owner=owner,
    )


def test_unknown_capture_age_never_actuates_transient_offer():
    from loop.adaptive.runtime import project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        graph = _offer_graph(("reg-a", "Audio"))
        snap = project(graph, session.schema)
        owner = _RecordingOwner()
        loop = _offer_loop(session, root, owner)
        try:
            loop.process_frame(
                snap,
                hwnd=1,
                pid=1,
                capture_integrity="trusted",
                scene_graph=graph,
                capture_ts_monotonic=0.0,
                capture_age_known=False,
                acquisition_origin="buffered-unknown",
                observation_sequence=1,
            )
        finally:
            loop.close()

        assert owner.plans == []
        assert loop.metrics()["transient_actuations"] == 0
        # Unknown age is rejected by the acquisition gate before the disabled
        # direct route is reached, so nothing is recorded as refused.
        assert loop.metrics()["transient_actuations_disabled"] == 0


def test_known_capture_age_never_actuates_transient_offer():
    from loop.adaptive.runtime import project

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        graph = _offer_graph(("reg-a", "Audio"))
        snap = project(graph, session.schema)
        owner = _RecordingOwner()
        loop = _offer_loop(session, root, owner)
        try:
            for _ in range(2):
                loop.process_frame(
                    snap,
                    hwnd=1,
                    pid=1,
                    capture_integrity="trusted",
                    scene_graph=graph,
                    capture_ts_monotonic=time.monotonic(),
                    capture_age_known=True,
                    acquisition_origin="pre-grab-sync",
                    observation_sequence=1,
                )
        finally:
            loop.close()

        # H1: even a fully eligible offer is refused because the direct route
        # cannot prove a frozen lease plus a newer revalidation capture.
        assert owner.plans == []
        metrics = loop.metrics()
        assert metrics["transient_actuations"] == 0
        assert metrics["transient_actuations_disabled"] == 1


def test_live_observe_assigns_fresh_acquisition_and_increasing_sequence(monkeypatch):
    from PIL import Image

    from loop.adaptive.cli import _live_observe_factory
    from loop.os.window import CaptureProvenance, WindowTarget

    target = WindowTarget(77, "target", 0, 0, 8, 6)
    calls = {"n": 0}

    class Session:
        ledger = type("Ledger", (), {"append_event": lambda *_a, **_k: None})()

        def observe(self, graph):
            return graph

    class Assembler:
        def update(self, image, **_kwargs):
            return (), type("Graph", (), {"snapshot_id": "fresh", "nodes": ()})()

    def _grab(_target):
        calls["n"] += 1
        ts = 1000.0 + calls["n"]
        return Image.new("RGB", (8, 6), "white"), CaptureProvenance(
            source="hwnd-imagegrab",
            client_size=(8, 6),
            content_box=(0.0, 0.0, 1.0, 1.0),
            integrity="trusted",
            reason="ok",
            acquisition_ts_monotonic=ts,
            acquisition_origin="pre-grab-sync",
            acquisition_age_known=True,
            grab_requested_ts_monotonic=ts,
        )

    monkeypatch.setattr("loop.os.window.is_foreground", lambda _t: True)
    monkeypatch.setattr("loop.os.window.target_identity", lambda _t: {"hwnd": 77, "pid": 9})
    monkeypatch.setattr("loop.os.window.grab_client_capture", _grab)
    observe = _live_observe_factory(Session(), target, Assembler())

    observe()
    first = dict(observe.acquisition)
    observe()
    second = dict(observe.acquisition)

    # Identical pixels, distinct fresh acquisition and increasing sequence.
    assert first["observation_sequence"] == 1
    assert second["observation_sequence"] == 2
    assert first["capture_ts_monotonic"] != second["capture_ts_monotonic"]
    assert first["capture_age_known"] is True
    assert first["acquisition_origin"] == "pre-grab-sync"


def _offer_pair_response(choice: str = "reg-a", other: str = "reg-b") -> dict[str, object]:
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


def test_deferred_decision_leases_its_own_frame_not_the_current_capture(monkeypatch):
    """H1: a decision made on frame A is revalidated against frame B.

    The lease holds a frozen copy of the frame the offer was bound to, and the
    current scene is the later capture.  The pre-H1 code passed one live graph
    as both, so the binding could never be rejected by a newer frame.
    """

    from loop.adaptive.decider_policy import DeciderPolicy
    from loop.adaptive.runtime import project

    graph_a = _offer_graph(("reg-a", "Audio"), ("reg-b", "Video"))
    graph_b = replace(
        _offer_graph(("reg-a", "Audio"), ("reg-b", "Video")),
        snapshot_id="frame-2",
    )
    dispatched: list[object] = []
    real_dispatch = Executor.dispatch

    def _spy(self, decision, snapshot, catalog, directive, **kwargs):
        dispatched.append(kwargs.get("native"))
        return real_dispatch(self, decision, snapshot, catalog, directive, **kwargs)

    monkeypatch.setattr(Executor, "dispatch", _spy)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        snap_a = project(graph_a, session.schema)
        snap_b = project(graph_b, session.schema)
        owner = _RecordingOwner()
        fake = _FakeDecider(delay_s=0.25, response=_offer_pair_response())
        loop = NativeShadowLoop(
            session,
            PolicyWorker(DeciderPolicy(fake), inference_timeout_s=1.0),
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(
                decision_deadline_s=2.0, actuate_transient_offers=True
            ),
            owner=owner,
        )
        try:
            loop.process_frame(
                snap_a,
                hwnd=1,
                pid=1,
                capture_integrity="trusted",
                scene_graph=graph_a,
                capture_ts_monotonic=time.monotonic(),
                capture_age_known=True,
                acquisition_origin="pre-grab-sync",
                observation_sequence=1,
            )
            fake.wait_until_called()
            time.sleep(0.4)  # the deferred inference is ready
            loop.process_frame(
                snap_b,
                hwnd=1,
                pid=1,
                capture_integrity="trusted",
                scene_graph=graph_b,
                capture_ts_monotonic=time.monotonic(),
                capture_age_known=True,
                acquisition_origin="pre-grab-sync",
                observation_sequence=2,
            )
        finally:
            loop.close()

        assert dispatched, "the deferred decision reached the dispatch gate"
        ctx = dispatched[0]
        assert ctx.leased_scene is not None
        assert ctx.current_scene is not None
        assert ctx.leased_scene is not ctx.current_scene
        assert ctx.leased_scene is not graph_a, "the lease holds a frozen copy"
        assert ctx.current_scene is graph_b
        assert ctx.leased_scene.snapshot_id == graph_a.snapshot_id
        assert ctx.current_scene.snapshot_id != ctx.leased_scene.snapshot_id
        assert {
            node.node_id: node.bbox for node in ctx.leased_scene.nodes
        } == {node.node_id: node.bbox for node in graph_a.nodes}
        # The lease frame may be rebuilt without disturbing the frozen copy.
        assert {
            node.node_id: node.bbox for node in graph_a.nodes
        } == {node.node_id: node.bbox for node in ctx.leased_scene.nodes}
        # And the disabled direct route still sends nothing from this path.
        assert owner.plans == []
        assert loop.metrics()["transient_actuations"] == 0


def test_dispatch_refuses_one_graph_as_both_lease_and_revalidation():
    from loop.adaptive.arm import ArmConfig, _compile_frame
    from loop.adaptive.candidates import mask_digest
    from loop.adaptive.controller import GenerationStore, _v2_from_native_obs
    from loop.adaptive.executor import Executor as DispatchExecutor
    from loop.adaptive.executor import NativeDispatchContext
    from loop.adaptive.policy import StructuredDecision

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        session.accept_schema(_promoted_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        snap = _snap(session.schema)
        now = time.monotonic()
        native_obs, catalog, directive, mask = _compile_frame(
            session,
            snap,
            episode_id="ep",
            episode_generation=0,
            hwnd=1,
            pid=42,
            capture_integrity="trusted",
            capture_provenance=("test",),
            directive_store=GenerationStore(),
            config=ArmConfig(),
            capture_ts_monotonic=now,
            capture_age_known=True,
            acquisition_origin="pre-grab-sync",
            observation_sequence=1,
        )
        decision = StructuredDecision(
            candidate_id="probe_click",
            confidence=1.0,
            probabilities={"probe_click": 1.0},
            decided_at=time.monotonic(),
            episode_generation=0,
            snapshot_id="snap-lease",
            directive_revision=directive.revision,
            catalog_revision=catalog.revision,
            bound_entity_id="node-reg-a",
            scene_id="snap-lease",
        )
        graph = _offer_graph(("reg-a", "Audio"))
        shared = NativeDispatchContext(
            observation=native_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision="p",
            surface_action_ids=frozenset(native_obs.snapshot.surface.action_ids),
            leased_scene=graph,
            current_scene=graph,
        )
        observed = replace(_v2_from_native_obs(native_obs), snapshot_id="snap-latest")

        rejected = DispatchExecutor().dispatch(
            decision,
            observed,
            catalog,
            directive,
            now=time.monotonic(),
            native=shared,
        )
        assert not rejected.accepted
        assert rejected.reason == "rebound"
        assert rejected.lease is None

        # Two distinct captures of the same content still revalidate, so the
        # rejection above is caused by reusing one graph, not by the content.
        distinct = replace(
            shared,
            leased_scene=replace(graph),
            current_scene=replace(graph),
        )
        accepted = DispatchExecutor().dispatch(
            decision,
            observed,
            catalog,
            directive,
            now=time.monotonic(),
            native=distinct,
        )
        assert accepted.accepted
        assert accepted.lease is not None
        assert accepted.lease.target_identity["leased_scene_id"] == "snap-lease"
