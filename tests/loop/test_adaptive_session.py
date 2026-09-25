"""Adaptive session workspace tests."""

from __future__ import annotations

import tempfile
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from loop.adaptive.arm import _bind_lease_context
from loop.adaptive.runtime import normalized_click_point
from loop.adaptive.schema import (
    ActionInvocation,
    ActionSpec,
    EffectPredicate,
    EntityTypeSpec,
    FieldSpec,
    ParamSpec,
    PrimitiveStep,
    empty_schema,
)
from loop.adaptive.contracts import (
    EFFECT_STATUS_NOT_EXECUTED,
    EFFECT_STATUS_NO_EFFECT,
    ActuationLease,
    ExecutionReceipt,
)
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, SCALAR_FIELD_ID, NeutralWorld


def _neutral_schema():
    base = empty_schema()
    return replace(
        base,
        fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
        entity_types=(EntityTypeSpec("type_unit", ()),),
        actions=(
            ActionSpec(
                action_id="act_click",
                parameters=(ParamSpec("target", "entity"),),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "increased"),),
            ),
        ),
    )


def _runner(world: NeutralWorld):
    def run(steps, bindings):
        for st in steps:
            if st.op == "click":
                world.click(str(bindings["target"]))

    return run


def test_session_observe_and_act():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.set_schema(_neutral_schema())
        world = NeutralWorld()
        graph = world.reset(0)
        before = session.observe(graph)
        assert "act_click" in before.surface.action_ids

        inv = ActionInvocation(
            "act_click",
            {"target": CONTROL_NODE_ID},
            before.snapshot_id,
            before.schema_revision_id,
        )
        receipt = session.act(
            inv, _runner(world), lambda: world.observe()[1], before=before
        )
        assert isinstance(receipt, ExecutionReceipt)
        assert receipt.accepted is False
        assert receipt.reason == "lease required"
        after = session.observe(world.observe()[1])
        assert after.fields[SCALAR_FIELD_ID] == 0


def test_bind_target_rejects_different_process_identity():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        session.bind_target({"selector": "alpha", "exe": "one.exe", "window_class": "A", "hwnd": 1, "pid": 1})
        with pytest.raises(ValueError, match="identity mismatch"):
            session.bind_target({"selector": "alpha", "exe": "two.exe", "window_class": "A", "hwnd": 2, "pid": 2})
        again = Session(Path(tmp))
        again.bind_target({"selector": "alpha", "exe": "one.exe", "window_class": "A", "hwnd": 99, "pid": 99})


def test_session_noop_runner_fails_verify():
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        session.set_schema(_neutral_schema())
        world = NeutralWorld()
        graph = world.reset(0)
        before = session.observe(graph)
        cx, cy = normalized_click_point(graph, CONTROL_NODE_ID)
        inv = ActionInvocation(
            "act_click",
            {"target": CONTROL_NODE_ID, "x": cx, "y": cy},
            before.snapshot_id,
            before.schema_revision_id,
        )
        receipt = session.act(
            inv, lambda steps, bindings: None, lambda: world.observe()[1], before=before
        )
        assert isinstance(receipt, ExecutionReceipt)
        assert receipt.accepted is False
        assert receipt.reason == "lease required"


def _leased_act(tmp_path: Path, *, lease: bool = True):
    session = Session(tmp_path)
    session.set_schema(_neutral_schema())
    session.bind_target({"selector": "t", "exe": "e.exe", "window_class": "C", "hwnd": 1, "pid": 1})
    world = NeutralWorld()
    before = session.observe(world.reset(0))
    inv = ActionInvocation(
        "act_click",
        {"target": CONTROL_NODE_ID},
        before.snapshot_id,
        before.schema_revision_id,
    )
    owner = MagicMock()
    owner.check_target.return_value = (True, "ok")
    owner.drain_primitive_results.return_value = ()
    owner.release_all.return_value = "released"
    if not lease:
        return session, world, before, inv, owner, None
    actuation_lease = ActuationLease(
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
    _bind_lease_context(session, actuation_lease)
    return session, world, before, inv, owner, actuation_lease


def test_act_records_effect_status_separately_from_acceptance(tmp_path: Path):
    session, world, before, inv, owner, lease = _leased_act(tmp_path)
    receipt = session.act(
        inv,
        lambda _s, _b: None,
        lambda: world.observe()[1],
        before=before,
        lease=lease,
        owner=owner,
    )
    assert isinstance(receipt, ExecutionReceipt)
    # Execution accepted, but one unchanged after-frame is an observed no-effect,
    # which must never read as success.
    assert receipt.accepted is True
    assert receipt.expected_effect.status == EFFECT_STATUS_NO_EFFECT
    assert receipt.expected_effect.verified is False
    assert receipt.expected_effect.reason == "expected effect not observed"
    evidence = receipt.expected_effect.evidence
    assert len(evidence) == 1
    assert evidence[0]["trusted"] is True
    assert evidence[0]["before_snapshot_id"] == before.snapshot_id


def test_rejected_act_is_not_executed_not_a_no_effect(tmp_path: Path):
    session, world, before, inv, owner, _lease = _leased_act(tmp_path, lease=False)
    receipt = session.act(
        inv,
        lambda _s, _b: None,
        lambda: world.observe()[1],
        before=before,
        owner=owner,
    )
    assert isinstance(receipt, ExecutionReceipt)
    assert receipt.accepted is False
    assert receipt.expected_effect.status == EFFECT_STATUS_NOT_EXECUTED
    assert receipt.expected_effect.status != EFFECT_STATUS_NO_EFFECT
    assert receipt.expected_effect.verified is False
    owner.run_plan.assert_not_called()
