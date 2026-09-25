"""Native keyboard/input owner unit tests (no real Win32 by default)."""

from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loop.adaptive.contracts import ActuationLease, ExecutionReceipt
from loop.adaptive.schema import (
    ActionInvocation,
    ActionSpec,
    EffectPredicate,
    EntityTypeSpec,
    FieldSpec,
    ParamSpec,
    PrimitiveSchemaError,
    PrimitiveStep,
    empty_schema,
)
from loop.adaptive.arm import _bind_lease_context
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, SCALAR_FIELD_ID, NeutralWorld
from loop.os.input_owner import NativeInputOwner
from loop.os.keyboard import Keyboard
from loop.os.wininput import KEYEVENTF_KEYUP, key_event
from loop.os.window import WindowTarget


def _record_send():
    events: list = []

    def _send(*inputs):
        events.extend(inputs)

    return events, _send


def test_keyboard_tap_down_before_up():
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    kb.tap("Space", duration_ms=1)
    assert len(events) == 2
    assert events[0].ki.dwFlags & KEYEVENTF_KEYUP == 0
    assert events[1].ki.dwFlags & KEYEVENTF_KEYUP


def test_keyboard_duplicate_down_suppressed():
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    scan, ext = 0x39, False
    kb._down(scan, ext)
    kb._held.add("Space")
    kb.tap("Space", duration_ms=1)
    assert len(events) == 1


def test_keyboard_hold_schedules_release():
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    kb.hold("KeyA", duration_ms=30)
    assert any(e.ki.dwFlags & KEYEVENTF_KEYUP == 0 for e in events)
    time.sleep(0.05)
    assert any(e.ki.dwFlags & KEYEVENTF_KEYUP for e in events)


def test_release_clock_fires_while_scored_plan_worker_blocked():
    """Held input releases on the armed deadline while the plan thread is stuck after hold."""
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    mouse = MagicMock()
    target = WindowTarget(hwnd=1, title="t", left=0, top=0, width=100, height=100)
    owner = NativeInputOwner(target, mouse=mouse, keyboard=kb)
    owner.enable_actuation()
    hold_ms = 80
    steps = (
        PrimitiveStep("key_hold", (), {"key": "KeyW", "duration_ms": hold_ms}),
        PrimitiveStep("sleep", (), {"duration_s": 5.0}),
    )
    def _blocked_plan() -> None:
        with patch.object(owner, "check_target", return_value=(True, "ok")):
            owner.run_plan(steps, {}, scene=None)

    worker = threading.Thread(target=_blocked_plan, daemon=True)
    worker.start()
    time.sleep(hold_ms / 1000.0 + 0.05)
    ups = [e for e in events if e.ki.dwFlags & KEYEVENTF_KEYUP]
    assert ups, "release clock must drop held keys while plan thread is blocked"
    owner.release_all("test_cleanup")
    worker.join(timeout=2.0)


def test_release_deadline_not_moved_by_later_input_down():
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    target = WindowTarget(hwnd=1, title="t", left=0, top=0, width=100, height=100)
    owner = NativeInputOwner(target, mouse=MagicMock(), keyboard=kb)
    owner.enable_actuation()
    first_ms = 80
    t0 = time.monotonic()
    with patch.object(owner, "check_target", return_value=(True, "ok")):
        owner.run_plan(
            (PrimitiveStep("key_hold", (), {"key": "KeyW", "duration_ms": first_ms}),),
            {},
            scene=None,
        )
        owner.run_plan(
            (PrimitiveStep("key_hold", (), {"key": "KeyA", "duration_ms": 2000}),),
            {},
            scene=None,
        )
        time.sleep(first_ms / 1000.0 + 0.05)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, "later hold must not extend an already-armed release deadline"
    ups = [e for e in events if e.ki.dwFlags & KEYEVENTF_KEYUP]
    assert len(ups) >= 2


def test_keyboard_release_all_idempotent():
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    kb.hold("KeyA", duration_ms=5000)
    kb.release_all()
    kb.release_all()
    ups = [e for e in events if e.ki.dwFlags & KEYEVENTF_KEYUP]
    assert ups


def test_keyboard_startup_safe_ups():
    events, send = _record_send()
    kb = Keyboard(send_input_fn=send)
    kb.safe_startup_release(frozenset({"Space"}))
    assert events and all(e.ki.dwFlags & KEYEVENTF_KEYUP for e in events)


def test_primitive_schema_unknown_op():
    with pytest.raises(PrimitiveSchemaError, match="unsupported"):
        PrimitiveStep("key_down", ())


def test_primitive_schema_unknown_param():
    with pytest.raises(PrimitiveSchemaError, match="unknown params"):
        PrimitiveStep("click", ("target",), {"coords": (0, 0)})


def test_primitive_step_backward_compat_hash():
    from loop.adaptive.schema import _to_plain

    a = PrimitiveStep("click", ("target",))
    b = PrimitiveStep("click", ("target",), {})
    assert a.params == {}
    assert a.op == b.op
    assert "params" not in _to_plain(a)
    assert _to_plain(a) == _to_plain(b)


def _lease_for(session: Session, before, inv: ActionInvocation) -> ActuationLease:
    return ActuationLease(
        episode_generation=0,
        snapshot_id=before.snapshot_id,
        schema_revision_id=before.schema_revision_id,
        catalog_revision="cat",
        directive_revision=0,
        policy_revision="pol",
        hwnd=1,
        pid=1,
        target_identity={"selector": "t", "exe": "e.exe", "window_class": "C"},
        expires_at_monotonic=time.monotonic() + 60.0,
        selected_action_id=inv.action_id,
        hard_mask_digest="mask",
    )


def test_session_lease_expired_rejects():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        schema = replace(
            empty_schema(),
            fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
            entity_types=(EntityTypeSpec("type_unit", ()),),
            actions=(
                ActionSpec(
                    action_id="act_click",
                    parameters=(ParamSpec("target", "entity"),),
                    preconditions=(),
                    primitive_plan=(PrimitiveStep("sleep", ()),),
                    expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "increased"),),
                ),
            ),
        )
        session.set_schema(schema)
        session.bind_target({"selector": "t", "exe": "e.exe", "window_class": "C", "hwnd": 1, "pid": 1})
        world = NeutralWorld()
        before = session.observe(world.reset(0))
        inv = ActionInvocation(
            "act_click",
            {"target": CONTROL_NODE_ID},
            before.snapshot_id,
            before.schema_revision_id,
        )
        lease = _lease_for(session, before, inv)
        lease = replace(lease, expires_at_monotonic=time.monotonic() - 1.0)
        _bind_lease_context(session, lease)
        owner = MagicMock()
        owner.check_target.return_value = (True, "ok")
        owner.drain_primitive_results.return_value = ()
        owner.release_all.return_value = "released"
        receipt = session.act(
            inv,
            lambda s, b: None,
            lambda: world.observe()[1],
            before=before,
            lease=lease,
            owner=owner,
            get_scene=lambda: world.observe()[1],
        )
        assert receipt.accepted is False
        assert "expired" in receipt.reason


def test_session_lease_focus_loss_via_owner():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        schema = replace(
            empty_schema(),
            fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
            entity_types=(EntityTypeSpec("type_unit", ()),),
            actions=(
                ActionSpec(
                    action_id="act_click",
                    parameters=(ParamSpec("target", "entity"),),
                    preconditions=(),
                    primitive_plan=(PrimitiveStep("sleep", ()),),
                    expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "changed"),),
                ),
            ),
        )
        session.set_schema(schema)
        session.bind_target({"selector": "t", "exe": "e.exe", "window_class": "C", "hwnd": 1, "pid": 1})
        world = NeutralWorld()
        before = session.observe(world.reset(0))
        inv = ActionInvocation(
            "act_click",
            {"target": CONTROL_NODE_ID},
            before.snapshot_id,
            before.schema_revision_id,
        )
        lease = _lease_for(session, before, inv)
        _bind_lease_context(session, lease)
        owner = MagicMock()
        owner.check_target.return_value = (False, "target not foreground")
        owner.run_plan.side_effect = AssertionError("should not run")
        owner.drain_primitive_results.return_value = ()
        owner.release_all.return_value = "released"
        receipt = session.act(
            inv,
            lambda s, b: None,
            lambda: world.observe()[1],
            before=before,
            lease=lease,
            owner=owner,
            pre_dispatch=lambda: (False, "untrusted capture"),
            get_scene=lambda: world.observe()[1],
        )
        assert receipt.accepted is False
        assert "untrusted" in receipt.reason


@pytest.mark.native_input
def test_instrumented_session_act_native_path():
    pytest.skip("instrumented window harness not wired in CI; run locally with -m native_input")


def _leased_act_setup(session, world, *, run_side_effect=None):
    schema = replace(
        empty_schema(),
        fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
        entity_types=(EntityTypeSpec("type_unit", ()),),
        actions=(
            ActionSpec(
                action_id="act_click",
                parameters=(ParamSpec("target", "entity"),),
                preconditions=(),
                primitive_plan=(PrimitiveStep("sleep", ()),),
                expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "increased"),),
            ),
        ),
    )
    session.set_schema(schema)
    session.bind_target({"selector": "t", "exe": "e.exe", "window_class": "C", "hwnd": 1, "pid": 1})
    before = session.observe(world.reset(0))
    inv = ActionInvocation(
        "act_click",
        {"target": CONTROL_NODE_ID},
        before.snapshot_id,
        before.schema_revision_id,
    )
    lease = _lease_for(session, before, inv)
    _bind_lease_context(session, lease)
    owner = MagicMock()
    owner.check_target.return_value = (True, "ok")
    owner.drain_primitive_results.return_value = ()
    owner.release_all.return_value = "released"
    if run_side_effect is not None:
        owner.run_plan.side_effect = run_side_effect
    runner = MagicMock(side_effect=AssertionError("runner must not run"))
    return before, inv, lease, owner, runner, world


def test_session_act_without_lease_rejects_no_input():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        world = NeutralWorld()
        before, inv, _lease, owner, runner, world = _leased_act_setup(session, world)
        receipt = session.act(
            inv,
            runner,
            lambda: world.observe()[1],
            before=before,
            owner=owner,
        )
        assert isinstance(receipt, ExecutionReceipt)
        assert receipt.accepted is False
        assert "lease" in receipt.reason
        runner.assert_not_called()
        owner.run_plan.assert_not_called()


def test_session_mask_digest_stale_rejects():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        world = NeutralWorld()
        before, inv, lease, owner, runner, world = _leased_act_setup(
            session, world, run_side_effect=AssertionError("should not run")
        )
        session._hard_mask_digest = "stale-digest"  # noqa: SLF001
        receipt = session.act(
            inv,
            runner,
            lambda: world.observe()[1],
            before=before,
            lease=lease,
            owner=owner,
            get_scene=lambda: world.observe()[1],
        )
        assert receipt.accepted is False
        assert "mask digest" in receipt.reason


def test_session_lease_missing_catalog_revision_rejects():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        world = NeutralWorld()
        before, inv, lease, owner, runner, world = _leased_act_setup(
            session, world, run_side_effect=AssertionError("should not run")
        )
        lease = replace(lease, catalog_revision="")
        _bind_lease_context(session, lease)
        receipt = session.act(
            inv,
            runner,
            lambda: world.observe()[1],
            before=before,
            lease=lease,
            owner=owner,
        )
        assert receipt.accepted is False
        assert "catalog_revision" in receipt.reason


def test_session_lease_context_missing_rejects():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        world = NeutralWorld()
        before, inv, lease, owner, runner, world = _leased_act_setup(session, world)
        del session._catalog_revision  # noqa: SLF001
        receipt = session.act(
            inv,
            runner,
            lambda: world.observe()[1],
            before=before,
            lease=lease,
            owner=owner,
        )
        assert receipt.accepted is False
        assert "lease context" in receipt.reason


def test_session_after_capture_from_fresh_observe_not_bound_target():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        world = NeutralWorld()
        before, inv, lease, owner, runner, world = _leased_act_setup(session, world)
        provenance = {"hwnd": 9001, "pid": 4242, "source": "fresh_capture", "integrity": "trusted"}

        def observe_fn():
            return world.observe()

        observe_fn.provenance = provenance  # type: ignore[attr-defined]

        def after_capture():
            return (
                int(provenance["hwnd"]),
                int(provenance["pid"]),
                (str(provenance["source"]),),
            )

        receipt = session.act(
            inv,
            runner,
            lambda: world.observe()[1],
            before=before,
            lease=lease,
            owner=owner,
            get_scene=lambda: world.observe()[1],
            after_capture=after_capture,
        )
        assert receipt.after_hwnd == 9001
        assert receipt.after_pid == 4242
        assert receipt.after_capture_provenance == ("fresh_capture",)
        assert receipt.after_hwnd != int(session.target["hwnd"])
