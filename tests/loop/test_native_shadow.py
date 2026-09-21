"""Wave 5 native shadow loop: hypothetical dispatch, no actuation."""

from __future__ import annotations

import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loop.adaptive.candidates import Directive
from loop.adaptive.controller import Executor, NativeShadowConfig, NativeShadowLoop
from loop.adaptive.policy import DeterministicPolicy
from loop.adaptive.policy_worker import PolicyWorker
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


def _loop(session: Session, tmp: Path) -> NativeShadowLoop:
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


def test_two_candidates_uses_worker_and_logs_digest():
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
                focused=True,
            )
        finally:
            shadow.close()

        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        assert rows
        last = rows[-1]
        assert last.decision_source in ("decider", "decider_pending")
        if last.decision_source == "decider":
            assert last.primitive_plan_digest
            assert last.deterministic_action_id is not None


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

    def _grab(_target):
        return world.observe()[0].convert("RGB")

    monkeypatch.setattr(
        "loop.os.window.grab_client_capture",
        lambda target: (
            _grab(target),
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

    with tempfile.TemporaryDirectory() as tmp:
        rc = cmd_collect(["--workspace", tmp, "--window", "dummy", "--steps", "2"])
        assert rc == 0
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
