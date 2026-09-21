"""Adaptive session workspace tests."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

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
        ok = session.act(
            inv, _runner(world), lambda: world.observe()[1], before=before
        )
        assert ok is True
        after = session.observe(world.observe()[1])
        assert after.fields[SCALAR_FIELD_ID] == 1


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
        ok = session.act(
            inv, lambda steps, bindings: None, lambda: world.observe()[1], before=before
        )
        assert ok is False
