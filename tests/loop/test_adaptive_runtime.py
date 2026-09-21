"""Adaptive runtime authority tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from loop.adaptive.runtime import (
    InvocationError,
    invoke,
    normalized_click_point,
    project,
    select_invocation,
    step,
    verify,
)
from loop.adaptive.orchestrate import _slug_id
from loop.adaptive.schema import (
    ActionInvocation,
    ActionSpec,
    ActionSurface,
    EffectPredicate,
    EntityTypeSpec,
    FieldSpec,
    ModeSpec,
    ParamSpec,
    PrimitiveStep,
    SceneGraph,
    StateSnapshot,
    empty_schema,
)
from loop.adaptive.world import (
    CONTROL_NODE_ID,
    DISPLAY_NODE_ID,
    ENTITY_NODE_ID,
    ENTITY_TYPE_ID,
    SCALAR_FIELD_ID,
    NeutralWorld,
)


def _neutral_schema():
    base = empty_schema()
    return replace(
        base,
        fields=(FieldSpec(SCALAR_FIELD_ID, "number"),),
        entity_types=(EntityTypeSpec(ENTITY_TYPE_ID, ()),),
        actions=(
            ActionSpec(
                action_id="act_click",
                parameters=(ParamSpec("target", "entity"),),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate(SCALAR_FIELD_ID, "increased"),),
            ),
            ActionSpec(
                action_id="act_noop",
                parameters=(),
                preconditions=(),
                primitive_plan=(PrimitiveStep("sleep", ()),),
                expected_effects=(),
            ),
        ),
    )


def _inv(snap, action_id: str, bindings: dict | None = None) -> ActionInvocation:
    return ActionInvocation(
        action_id,
        bindings or {},
        snap.snapshot_id,
        schema_revision_id=snap.schema_revision_id,
    )


def _runner(world: NeutralWorld):
    def run(steps, bindings):
        for st in steps:
            if st.op == "click":
                world.click(str(bindings["target"]))
            elif st.op == "sleep":
                world.noop()

    return run


def test_legal_actions_only_from_surface():
    world = NeutralWorld()
    schema = _neutral_schema()
    graph = world.reset(3)
    snap = project(graph, schema)
    assert "act_noop" not in snap.surface.action_ids
    assert snap.surface.action_ids == ("act_click",)
    bindings = {"target": CONTROL_NODE_ID}
    for action_id in snap.surface.action_ids:
        invoke(snap, _inv(snap, action_id, bindings))
    with pytest.raises(InvocationError, match="not legal"):
        invoke(snap, _inv(snap, "act_unknown"))


def test_snapshot_id_mismatch_raises():
    world = NeutralWorld()
    schema = _neutral_schema()
    snap = project(world.reset(1), schema)
    with pytest.raises(InvocationError, match="snapshot_id mismatch"):
        invoke(
            snap,
            ActionInvocation(
                "act_click",
                {"target": CONTROL_NODE_ID},
                "wrong_id",
                schema_revision_id=snap.schema_revision_id,
            ),
        )


def test_schema_revision_id_mismatch_raises():
    world = NeutralWorld()
    schema = _neutral_schema()
    snap = project(world.reset(1), schema)
    with pytest.raises(InvocationError, match="schema_revision_id mismatch"):
        invoke(
            snap,
            ActionInvocation(
                "act_click",
                {"target": CONTROL_NODE_ID},
                snap.snapshot_id,
                schema_revision_id="wrong-rev",
            ),
        )


def test_missing_binding_raises():
    world = NeutralWorld()
    schema = _neutral_schema()
    snap = project(world.reset(1), schema)
    with pytest.raises(InvocationError, match="missing binding"):
        invoke(snap, _inv(snap, "act_click", {}))


def test_target_not_in_domain_raises():
    world = NeutralWorld()
    schema = _neutral_schema()
    snap = project(world.reset(1), schema)
    with pytest.raises(InvocationError, match="not in domain"):
        invoke(snap, _inv(snap, "act_click", {"target": DISPLAY_NODE_ID}))


def test_noop_verify_false():
    world = NeutralWorld()
    schema = _neutral_schema()
    graph = world.reset(5)
    before = project(graph, schema)
    world.noop()
    after = project(world.observe()[1], schema)
    spec = schema.actions[1]
    assert verify(before, after, spec) is False


def test_verify_disappeared_entity():
    rev = empty_schema().revision.revision_id
    surf = ActionSurface("", rev, ())
    before = StateSnapshot(
        "s1",
        rev,
        None,
        {},
        {"ent_a": {"type_id": "t"}},
        surf,
    )
    after = StateSnapshot("s2", rev, None, {}, {}, surf)
    spec = ActionSpec(
        action_id="vanish",
        parameters=(),
        preconditions=(),
        primitive_plan=(),
        expected_effects=(EffectPredicate("field_x", "disappeared", entity_id="ent_a"),),
    )
    assert verify(before, after, spec) is True


def test_verify_appeared_entity():
    rev = empty_schema().revision.revision_id
    surf = ActionSurface("", rev, ())
    before = StateSnapshot("s1", rev, None, {}, {}, surf)
    after = StateSnapshot(
        "s2",
        rev,
        None,
        {},
        {"ent_b": {"type_id": "t"}},
        surf,
    )
    spec = ActionSpec(
        action_id="spawn",
        parameters=(),
        preconditions=(),
        primitive_plan=(),
        expected_effects=(EffectPredicate("field_x", "appeared", entity_id="ent_b"),),
    )
    assert verify(before, after, spec) is True


def test_click_increases_scalar_verify_true():
    world = NeutralWorld()
    schema = _neutral_schema()
    graph = world.reset(0)
    before = project(graph, schema)
    inv = _inv(before, "act_click", {"target": CONTROL_NODE_ID})
    after, ok = step(world, schema, inv, _runner(world))
    assert ok is True
    assert after.fields[SCALAR_FIELD_ID] == 1


def test_rename_invariance_stable_entity_and_field_ids():
    schema = _neutral_schema()
    w1, w2 = NeutralWorld(), NeutralWorld()
    g1, g2 = w1.reset(1), w2.reset(99)
    s1, s2 = project(g1, schema), project(g2, schema)
    assert ENTITY_NODE_ID in s1.entities and ENTITY_NODE_ID in s2.entities
    assert s1.entities[ENTITY_NODE_ID]["type_id"] == ENTITY_TYPE_ID
    assert s1.fields[SCALAR_FIELD_ID] == s2.fields[SCALAR_FIELD_ID] == 0
    assert s1.entities.keys() == s2.entities.keys()


def test_layout_scale_bindings_use_normalized_bbox():
    base = NeutralWorld()
    scaled = NeutralWorld(layout_scale=1.5, origin=(0.05, 0.05))
    g0 = base.reset(10)
    g1 = scaled.reset(10)
    cx0, cy0 = normalized_click_point(g0, CONTROL_NODE_ID)
    cx1, cy1 = normalized_click_point(g1, CONTROL_NODE_ID)
    assert (cx0, cy0) == (cx1, cy1)
    assert 0.0 <= cx0 <= 1.0 and 0.0 <= cy0 <= 1.0


def test_replay_identical_snapshot_fields_and_actions():
    schema = _neutral_schema()
    world = NeutralWorld()
    graph = world.reset(42)
    a = project(graph, schema)
    b = project(graph, schema)
    assert a.fields == b.fields
    assert a.entities == b.entities
    assert a.surface.action_ids == b.surface.action_ids


def test_pixel_scene_classifier_entities_and_mode_default():
    pytest.importorskip("PIL")
    from loop.adaptive.perceive import SceneAssembler

    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    pixels, _, truth = world.observe()
    assert pixels is not None
    _, scene = assembler.update(
        pixels.convert("RGB"),
        ocr=lambda im: world.ocr_tokens(),
    )
    display_label = truth["labels"]["display"]
    field_id = _slug_id(display_label)
    schema = replace(
        empty_schema(),
        fields=(FieldSpec(field_id, "number"),),
        entity_types=(
            EntityTypeSpec(
                "type_unit",
                (),
                {"has_label": True, "has_numeric": False},
            ),
        ),
        modes=(ModeSpec("mode_default", activation={"min_fields": 1}),),
    )
    snap = project(scene, schema)
    assert len(snap.entities) >= 1
    assert snap.mode_id == "mode_default"
    assert field_id in snap.fields


def test_mode_does_not_fall_back_when_activation_is_false():
    schema = replace(
        empty_schema(),
        modes=(ModeSpec("needs_a_field", activation={"min_fields": 1}),),
    )
    snap = project(SceneGraph("empty", ()), schema)
    assert snap.mode_id is None


def test_select_invocation_rejects_ambiguous_target():
    world = NeutralWorld()
    schema = _neutral_schema()
    snap = project(world.reset(1), schema)
    assert select_invocation(snap) is None


def test_select_invocation_uses_unique_selected_target():
    world = NeutralWorld()
    schema = _neutral_schema()
    graph = world.reset(1)
    control = next(n for n in graph.nodes if n.node_id == CONTROL_NODE_ID)
    schema = replace(
        schema,
        actions=(
            replace(schema.actions[0], selector={"bbox": list(control.bbox)}),
            schema.actions[1],
        ),
    )
    snap = project(graph, schema)
    inv = select_invocation(snap)
    assert inv is not None
    assert inv.action_id == "act_click"
    assert inv.bindings["target"] == CONTROL_NODE_ID
    assert inv.snapshot_id == snap.snapshot_id
    assert inv.schema_revision_id == snap.schema_revision_id
    target = next(d for d in snap.surface.actions[0].domains if d.name == "target")
    assert inv.bindings["target"] in target.candidates


def test_select_invocation_empty_surface_returns_none():
    rev = empty_schema().revision.revision_id
    surf = ActionSurface("s0", rev, (), ())
    snap = StateSnapshot("s0", rev, None, {}, {}, surf)
    assert select_invocation(snap) is None


def test_action_selector_label_narrows_entity_domain():
    base = _neutral_schema()
    world = NeutralWorld()
    graph = world.reset(7)
    _, _, truth = world.observe()
    control_label = truth["labels"]["control"]
    schema = replace(
        base,
        actions=(
            replace(
                base.actions[0],
                selector={"label": control_label},
            ),
            base.actions[1],
        ),
    )
    snap = project(graph, schema)
    inst = next(a for a in snap.surface.actions if a.action_id == "act_click")
    target = next(d for d in inst.domains if d.name == "target")
    assert inst.bound_node_id == CONTROL_NODE_ID
    assert target.candidates == (CONTROL_NODE_ID,)
    assert ENTITY_NODE_ID not in target.candidates
