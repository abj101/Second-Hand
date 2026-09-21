"""Native keyboard/input owner unit tests (no real Win32 by default)."""

from __future__ import annotations

import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loop.adaptive.contracts import ActuationLease
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
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, SCALAR_FIELD_ID, NeutralWorld
from loop.os.keyboard import Keyboard
from loop.os.wininput import KEYEVENTF_KEYUP, key_event


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
