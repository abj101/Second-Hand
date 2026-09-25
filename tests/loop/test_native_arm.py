"""Wave 6 armed collect: leased Session.act, mocked owner (no Win32)."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from loop.adaptive.arm import ArmConfig, append_negative_evidence, next_offers, run_armed_collect
from loop.adaptive.contracts import PostActionObservationPolicy
from loop.adaptive.schema import ActionInvocation, SceneGraph, SceneNode
from loop.adaptive.telemetry import NativeShadowRow, TelemetryStore
from loop.adaptive.cli import cmd_collect
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.schema import (
    ActionInstance,
    ActionSpec,
    ActionSurface,
    EffectPredicate,
    FieldSpec,
    PrimitiveStep,
    StateSnapshot,
    empty_schema,
)
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, SCALAR_FIELD_ID, NeutralWorld
from loop.os.window import CaptureProvenance, WindowTarget


def _constraints() -> dict:
    return {
        "risk": "low",
        "retry": "deny",
        "cooldown_s": 0.0,
        "max_plan_duration_s": 1.0,
    }


def _click_schema():
    schema = empty_schema()
    schema = replace(
        schema,
        fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
        actions=(
            ActionSpec(
                action_id="act_click",
                parameters=(),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "increased"),),
                constraints=_constraints(),
            ),
        ),
    )
    return schema


def _snap(schema, *, sid: str = "snap-a") -> StateSnapshot:
    rev = schema.revision.revision_id
    return StateSnapshot(
        snapshot_id=sid,
        schema_revision_id=rev,
        mode_id="running",
        fields={"score": 0},
        entities={},
        surface=ActionSurface(
            snapshot_id=sid,
            schema_revision_id=rev,
            action_ids=("act_click",),
            actions=(
                ActionInstance(
                    action_id="act_click",
                    parameters=(),
                    domains=(),
                    bound_node_id=CONTROL_NODE_ID,
                    expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "increased"),),
                ),
            ),
        ),
    )


def _world_observe(session: Session, world: NeutralWorld, assembler: SceneAssembler):
    scene_holder: list = [None]
    sequence = [0]

    def observe():
        image, graph, _truth = world.observe()
        recs, graph = assembler.update(
            image.convert("RGB"),
            ocr=lambda _im: world.ocr_tokens(),
            ledger=session.ledger,
        )
        scene_holder[0] = graph
        snap = session.observe(graph)
        sequence[0] += 1
        observe.provenance = {
            "integrity": "trusted",
            "source": "test",
            "hwnd": 1,
            "capture_ts_monotonic": time.monotonic(),
            "capture_age_known": True,
            "acquisition_origin": "pre-grab-sync",
            "observation_sequence": sequence[0],
        }
        return recs, graph, snap, scene_holder

    return observe


def _mock_owner_factory(world: NeutralWorld, *, run_plan=None):
    def factory(_target):
        owner = MagicMock()
        owner.enable_actuation = MagicMock()
        owner.release_all = MagicMock(return_value="released")
        owner.drain_primitive_results = MagicMock(return_value=())
        owner.check_target = MagicMock(return_value=(True, "ok"))
        if run_plan is None:

            def _run_plan(steps, bindings, scene=None, guard=None):
                for step in steps:
                    if guard is not None and step.op in ("click", "key_tap", "key_hold"):
                        guard()
                    if step.op == "click":
                        world.click(str(bindings.get("target") or CONTROL_NODE_ID))
                    elif step.op == "sleep":
                        world.noop()

            owner.run_plan = MagicMock(side_effect=_run_plan)
        else:
            owner.run_plan = run_plan
        return owner

    return factory


def test_arm_without_promoted_action_fails_closed(monkeypatch, tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    dummy = WindowTarget(hwnd=1, title="dummy", left=0, top=0, width=320, height=240)
    monkeypatch.setattr("loop.os.window.list_windows", lambda _t: [dummy])
    monkeypatch.setattr("loop.os.window.focus_window_and_settle", lambda _target: True)
    monkeypatch.setattr("loop.os.window.is_foreground", lambda _target: True)
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda _target: (
            world.observe()[0].convert("RGB"),
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
    owner = MagicMock()
    owner.enable_actuation = MagicMock()
    owner.release_all = MagicMock(return_value="released")
    owner.drain_primitive_results = MagicMock(return_value=())
    monkeypatch.setattr(
        "loop.adaptive.arm.create_owner",
        lambda _target: owner,
    )
    mouse_execute = MagicMock()
    monkeypatch.setattr("loop.os.mouse.Mouse.execute", mouse_execute)

    rc = cmd_collect(["--workspace", str(tmp_path), "--window", "dummy", "--arm", "--steps", "3"])
    assert rc == 2
    owner.run_plan.assert_not_called()
    mouse_execute.assert_not_called()
    assert owner.release_all.called


def test_one_candidate_uses_leased_run_plan_not_adhoc_mouse(tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target(
        {
            "selector": "dummy",
            "exe": "dummy.exe",
            "window_class": "Dummy",
            "hwnd": 1,
            "pid": 1,
        }
    )
    assembler = SceneAssembler()
    observe = _world_observe(session, world, assembler)
    # Prime graph so click primitive can resolve if needed
    observe()

    mouse_execute = MagicMock()
    import loop.os.mouse as mouse_mod

    original_execute = mouse_mod.Mouse.execute
    mouse_mod.Mouse.execute = mouse_execute

    owner = _mock_owner_factory(world)(None)
    try:
        rc, summary = run_armed_collect(
            session,
            WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
            observe,
            n_steps=3,
            owner_factory=lambda _t: owner,
            is_focused=lambda: True,
        )
    finally:
        mouse_mod.Mouse.execute = original_execute

    assert rc == 0, summary
    owner.run_plan.assert_called()
    lines = (tmp_path / "arm_executions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines
    row = json.loads(lines[0])
    assert row["dispatch_accepted"] is True
    assert row["lease"]
    assert row["execution"]["accepted"] is True
    mouse_execute.assert_not_called()


def test_focus_loss_zero_executions_release_all(tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target({"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"})
    observe = _world_observe(session, world, SceneAssembler())
    observe()

    owner = MagicMock()
    owner.enable_actuation = MagicMock()
    owner.release_all = MagicMock(return_value="released")
    owner.drain_primitive_results = MagicMock(return_value=())
    owner.check_target = MagicMock(return_value=(True, "ok"))
    owner.run_plan = MagicMock(side_effect=AssertionError("must not run"))

    rc, summary = run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        observe,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: False,
    )
    assert rc == 2
    owner.run_plan.assert_not_called()
    assert owner.release_all.call_count >= 1


def test_failed_expected_effect_no_retry(tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target({"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"})
    observe = _world_observe(session, world, SceneAssembler())
    observe()

    owner = MagicMock()
    owner.enable_actuation = MagicMock()
    owner.release_all = MagicMock(return_value="released")
    owner.drain_primitive_results = MagicMock(return_value=())
    owner.check_target = MagicMock(return_value=(True, "ok"))
    owner.run_plan = MagicMock()  # no world change -> verify fails

    rc, _summary = run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        observe,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: True,
    )
    assert rc == 2
    assert owner.run_plan.call_count == 1
    lines = (tmp_path / "arm_executions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["execution"]["expected_effect_verified"] is False


def test_stale_candidate_not_on_surface_no_run_plan(tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target({"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"})
    assembler = SceneAssembler()
    observe = _world_observe(session, world, assembler)
    observe()

    owner = MagicMock()
    owner.enable_actuation = MagicMock()
    owner.release_all = MagicMock(return_value="released")
    owner.drain_primitive_results = MagicMock(return_value=())
    owner.check_target = MagicMock(return_value=(True, "ok"))
    owner.run_plan = MagicMock(side_effect=AssertionError("must not run"))

    call_n = {"n": 0}
    real_observe = observe

    def observe_empty_surface():
        call_n["n"] += 1
        recs, graph, snap, holder = real_observe()
        if call_n["n"] >= 7:
            empty = replace(snap, surface=replace(snap.surface, action_ids=(), actions=()))
            return recs, graph, empty, holder
        return recs, graph, snap, holder

    rc, _ = run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        observe_empty_surface,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: True,
    )
    assert rc == 2
    owner.run_plan.assert_not_called()


def test_release_all_on_stop_path(tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target({"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"})
    observe = _world_observe(session, world, SceneAssembler())
    observe()

    owner = MagicMock()
    owner.enable_actuation = MagicMock()
    owner.release_all = MagicMock(return_value="released")
    owner.drain_primitive_results = MagicMock(return_value=())
    owner.check_target = MagicMock(return_value=(True, "ok"))
    owner.run_plan = MagicMock()

    run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        observe,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: True,
    )
    reasons = [c.args[0] for c in owner.release_all.call_args_list]
    assert "armed_finally" in reasons


def test_phase6_shadow_training_log_and_failed_effect_offers(tmp_path: Path):
    workspace = tmp_path / "ws"
    telemetry = TelemetryStore(workspace / "react.jsonl", workspace=workspace)
    digest = "frame-digest-1"
    frame_bytes = b"\x01\x02shadow-frame"
    offer_ids = ("reg_audio", "reg_video")
    telemetry.append_shadow(
        NativeShadowRow(
            episode_id="ep",
            episode_generation=0,
            frame_index=1,
            schema_revision_id="sch",
            snapshot_id="snap",
            observation_age_s=0.0,
            capture_integrity="trusted",
            hwnd=1,
            pid=1,
            focused=True,
            directive_revision=1,
            catalog_revision="cat",
            mask_digest="mask",
            mask_allowed_ids=(),
            decision_source="observe",
            selected_action_id=None,
            deterministic_action_id=None,
            dispatch_accepted=False,
            dispatch_reason="no_candidates",
            primitive_plan_digest=None,
            policy_turn_id=0,
            latency_policy_s=0.0,
            latency_e2e_s=0.0,
            frame_digest=digest,
            offer_ids=offer_ids,
        ),
        frame=frame_bytes,
    )
    frame_path = workspace / "frames" / f"{digest}.bin"
    assert frame_path.read_bytes() == frame_bytes
    logged = telemetry.shadow_rows()[0]
    assert logged.frame_digest == digest
    assert logged.offer_ids == offer_ids

    session = Session(tmp_path)
    graph = SceneGraph(
        snapshot_id="menu",
        nodes=(
            SceneNode(
                "track_audio",
                (0.1, 0.1, 0.4, 0.4),
                (),
                {"registry_id": "reg_audio", "label": "Audio"},
            ),
            SceneNode(
                "track_video",
                (0.1, 0.5, 0.4, 0.8),
                (),
                {"registry_id": "reg_video", "label": "Video"},
            ),
        ),
    )
    schema = _click_schema()
    session.set_schema(schema)
    snap = _snap(schema)
    snap = replace(
        snap,
        surface=replace(
            snap.surface,
            actions=(
                replace(
                    snap.surface.actions[0],
                    bound_node_id="track_audio",
                ),
            ),
        ),
    )
    inv = ActionInvocation(
        "act_click",
        {"target": "track_audio"},
        snap.snapshot_id,
        schema_revision_id=snap.schema_revision_id,
    )
    append_negative_evidence(
        session,
        inv,
        snap,
        graph,
        reason="effect_missing",
        stage=2,
    )
    neg = [r for r in session.ledger.all() if r.kind == "negative_evidence"]
    assert len(neg) == 1
    assert neg[0].payload["registry_id"] == "reg_audio"
    offers = next_offers(graph, session.ledger)
    assert {o.offer_id for o in offers} == {"reg_video"}


def _arm_session(tmp_path: Path):
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target(
        {"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"}
    )
    observe = _world_observe(session, world, SceneAssembler())
    observe()
    return session, observe


def _compile_and_dispatch(
    session: Session,
    observe,
    *,
    capture_ts: float | None,
    age_known: bool,
    origin: str,
    sequence: int | None,
    now: float | None = None,
):
    from loop.adaptive.arm import ArmConfig, _compile_frame, _dispatch_decision
    from loop.adaptive.controller import GenerationStore
    from loop.adaptive.executor import Executor
    from loop.adaptive.policy import StructuredDecision

    cfg = ArmConfig()
    recs, graph, snap, holder = observe()
    native_obs, catalog, directive, mask = _compile_frame(
        session,
        snap,
        episode_id="ep-b3a",
        episode_generation=cfg.episode_generation,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        capture_provenance=("test",),
        directive_store=GenerationStore(),
        config=cfg,
        capture_ts_monotonic=capture_ts,
        capture_age_known=age_known,
        acquisition_origin=origin,
        observation_sequence=sequence,
    )
    # Dispatch follows publication even when the test fixed capture time earlier.
    now = max(time.monotonic(), now or 0.0)
    candidate = snap.surface.action_ids[0]
    decision = StructuredDecision(
        candidate_id=candidate,
        confidence=1.0,
        probabilities={candidate: 1.0},
        decided_at=now,
        episode_generation=cfg.episode_generation,
        snapshot_id=snap.snapshot_id,
        directive_revision=directive.revision,
        catalog_revision=catalog.revision,
    )
    receipt = _dispatch_decision(
        session,
        decision,
        native_obs,
        catalog,
        directive,
        mask,
        executor=Executor(
            snapshot_max_age_s=cfg.snapshot_max_age_s,
            decision_max_age_s=cfg.decision_deadline_s,
        ),
        config=cfg,
        focused=True,
        now=now,
    )
    return native_obs, receipt


def test_armed_compile_frame_preserves_observe_acquisition(tmp_path: Path):
    from loop.adaptive.arm import ArmConfig, _compile_frame, acquisition_from_observe
    from loop.adaptive.controller import GenerationStore

    session, observe = _arm_session(tmp_path)
    recs, graph, snap, holder = observe()
    native_obs, _catalog, _directive, _mask = _compile_frame(
        session,
        snap,
        episode_id="ep",
        episode_generation=0,
        hwnd=1,
        pid=1,
        capture_integrity="trusted",
        capture_provenance=("test",),
        directive_store=GenerationStore(),
        config=ArmConfig(),
        **acquisition_from_observe(observe),
    )
    prov = observe.provenance
    assert native_obs.capture_age_known is True
    assert native_obs.acquisition_origin == "pre-grab-sync"
    assert native_obs.observation_sequence == prov["observation_sequence"]
    assert native_obs.capture_ts_monotonic == prov["capture_ts_monotonic"]


def test_armed_unknown_acquisition_rejects_before_input(tmp_path: Path):
    session, observe = _arm_session(tmp_path)
    native_obs, receipt = _compile_and_dispatch(
        session,
        observe,
        capture_ts=None,
        age_known=False,
        origin="buffered-unknown",
        sequence=None,
    )
    assert native_obs.capture_age_known is False
    assert native_obs.capture_ts_monotonic == 0.0
    assert receipt.accepted is False
    assert receipt.lease is None
    assert receipt.reason == "unverified_acquisition"


def test_armed_delayed_perception_rejects_stale_at_half_second(tmp_path: Path):
    session, observe = _arm_session(tmp_path)
    now = time.monotonic()
    native_obs, receipt = _compile_and_dispatch(
        session,
        observe,
        capture_ts=now - 1.2,
        age_known=True,
        origin="pre-grab-sync",
        sequence=1,
        now=now,
    )
    assert native_obs.capture_age_known is True
    assert native_obs.observation_age_s >= 1.2 - 1e-6
    assert receipt.accepted is False
    assert receipt.reason == "stale_snapshot"


def test_armed_fresh_acquisition_leases_once(tmp_path: Path):
    session, observe = _arm_session(tmp_path)
    now = time.monotonic()
    _native_obs, receipt = _compile_and_dispatch(
        session,
        observe,
        capture_ts=now - 0.01,
        age_known=True,
        origin="pre-grab-sync",
        sequence=1,
        now=now,
    )
    assert receipt.accepted is True
    assert receipt.lease is not None


def test_armed_unknown_wgc_provenance_rejects_without_input(tmp_path: Path):
    session, observe = _arm_session(tmp_path)

    def buffered_observe():
        recs, graph, snap, holder = observe()
        observe.provenance = {
            "integrity": "trusted",
            "source": "hwnd-wgc",
            "hwnd": 1,
            "capture_ts_monotonic": None,
            "capture_age_known": False,
            "acquisition_origin": "buffered-unknown",
            "observation_sequence": 1,
        }
        return recs, graph, snap, holder

    owner = MagicMock()
    owner.enable_actuation = MagicMock()
    owner.release_all = MagicMock(return_value="released")
    owner.drain_primitive_results = MagicMock(return_value=())
    owner.check_target = MagicMock(return_value=(True, "ok"))
    owner.run_plan = MagicMock(side_effect=AssertionError("must not run"))

    rc, _summary = run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        buffered_observe,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: True,
    )
    assert rc == 2
    owner.run_plan.assert_not_called()
    lines = (
        (tmp_path / "arm_executions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    )
    assert lines
    row = json.loads(lines[0])
    assert row["dispatch_accepted"] is False
    assert row["dispatch_reason"] == "unverified_acquisition"


class _SettlingClock:
    """Monotonic shim so the declared settling window never sleeps in real time."""

    def __init__(self, start: float = 500.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(float(seconds), 0.001)


SETTLING_POLICY = PostActionObservationPolicy(
    max_window_s=0.5, cadence_s=0.1, max_frames=6, required_negative_frames=2
)


def test_armed_declared_window_verifies_a_settling_effect(tmp_path: Path, monkeypatch):
    """A delayed animation verifies inside the declared window instead of failing once."""
    monkeypatch.setattr("loop.adaptive.session.time", _SettlingClock())
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target({"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"})
    observe = _world_observe(session, world, SceneAssembler())
    observe()

    state = {"acted": False, "after": 0}

    def run_plan(_steps, _bindings, scene=None, guard=None):
        if guard is not None:
            guard()
        # The input lands now; the UI state settles only on the next capture pair.
        state["acted"] = True

    def settling_observe():
        if state["acted"]:
            state["after"] += 1
            if state["after"] % 2 == 0:
                world.click(CONTROL_NODE_ID)
        result = observe()
        settling_observe.provenance = observe.provenance  # type: ignore[attr-defined]
        return result

    owner = _mock_owner_factory(world, run_plan=MagicMock(side_effect=run_plan))(None)
    rc, summary = run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        settling_observe,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: True,
        config=ArmConfig(post_action=SETTLING_POLICY),
    )
    assert rc == 0, summary
    rows = [
        json.loads(line)
        for line in (tmp_path / "arm_executions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ]
    assert rows
    for row in rows:
        execution = row["execution"]
        assert execution["accepted"] is True
        assert execution["expected_effect_status"] == "verified"
        assert execution["expected_effect_verified"] is True
        assert execution["expected_effect_evidence"]


def test_armed_unobservable_outcome_keeps_no_negative_evidence(tmp_path: Path, monkeypatch):
    """Failed post-action perception must not be recorded as a real no-effect."""
    monkeypatch.setattr("loop.adaptive.session.time", _SettlingClock())
    world = NeutralWorld()
    world.reset(0)
    session = Session(tmp_path)
    session.set_schema(_click_schema())
    session.bind_target({"selector": "d", "hwnd": 1, "pid": 1, "exe": "e.exe", "window_class": "C"})
    observe = _world_observe(session, world, SceneAssembler())
    observe()

    state = {"acted": False, "after": 0}

    def run_plan(_steps, _bindings, scene=None, guard=None):
        if guard is not None:
            guard()
        state["acted"] = True

    def degraded_observe():
        recs, graph, snap, holder = observe()
        provenance = dict(observe.provenance)
        if state["acted"]:
            state["after"] += 1
            provenance["integrity"] = "degraded"
        degraded_observe.provenance = provenance  # type: ignore[attr-defined]
        return recs, graph, snap, holder

    owner = _mock_owner_factory(world, run_plan=MagicMock(side_effect=run_plan))(None)
    rc, _summary = run_armed_collect(
        session,
        WindowTarget(hwnd=1, title="d", left=0, top=0, width=320, height=240),
        degraded_observe,
        n_steps=3,
        owner_factory=lambda _t: owner,
        is_focused=lambda: True,
        config=ArmConfig(post_action=SETTLING_POLICY),
    )
    assert rc == 2
    rows = [
        json.loads(line)
        for line in (tmp_path / "arm_executions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ]
    execution = rows[0]["execution"]
    assert execution["accepted"] is True
    assert execution["expected_effect_status"] == "unobservable"
    assert execution["expected_effect_verified"] is False
    negative = [r for r in session.ledger.all() if r.kind == "negative_evidence"]
    assert negative == []
