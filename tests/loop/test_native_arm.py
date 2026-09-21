"""Wave 6 armed collect: leased Session.act, mocked owner (no Win32)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from loop.adaptive.arm import run_armed_collect
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

    def observe():
        image, graph, _truth = world.observe()
        recs, graph = assembler.update(
            image.convert("RGB"),
            ocr=lambda _im: world.ocr_tokens(),
            ledger=session.ledger,
        )
        scene_holder[0] = graph
        snap = session.observe(graph)
        observe.provenance = {
            "integrity": "trusted",
            "source": "test",
            "hwnd": 1,
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
    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda _target: (
            world.observe()[0].convert("RGB"),
            CaptureProvenance(
                source="test",
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
