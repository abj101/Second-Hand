"""Wave 2: promoted native actions carry conservative constraints."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from loop.adaptive.candidates import native_constraints_from_spec
from loop.adaptive.contracts import CatalogCompileError
from loop.adaptive.experiment import ExperimentController, admit_plan, execute_trial
from loop.adaptive.orchestrate import (
    PROMOTED_NATIVE_CONSTRAINTS,
    apply_action_revision,
    compile_schema,
)
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.schema import (
    ActionSpec,
    EffectPredicate,
    ParamSpec,
    PrimitiveStep,
    empty_schema,
    load_head,
)
from loop.adaptive.session import Session
from loop.adaptive.world import CONTROL_NODE_ID, NeutralWorld
from tests.loop.test_adaptive_experiment import (
    _control_and_field,
    _observe_world,
)


def test_empty_schema_has_no_actions():
    schema = empty_schema()
    assert schema.actions == ()
    assert compile_schema(schema).actions == ()


def test_promoted_click_stamps_conservative_constraints(tmp_path: Path):
    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    graphs = []
    recs = None
    for _ in range(4):
        recs, graph, _ = _observe_world(world, assembler, session)
        graphs.append(graph)
    _control, field_id, ids = _control_and_field(graphs[-1])
    plan = admit_plan(
        schema_parsed={
            "diffs": [
                {
                    "kind": "field",
                    "suggested_id": field_id,
                    "candidate_ids": ids[:1],
                    "payload": {
                        "field_id": field_id,
                        "value_type": "number",
                        "label": field_id,
                    },
                }
            ]
        },
        experiment_parsed={
            "abstain": False,
            "candidate": {
                "primitive": "click",
                "candidate_ids": ids[:1],
                "predicted_effects": [{"field_id": field_id, "op": "increased"}],
                "rollback": {"primitive": "click", "candidate_ids": ids[:1]},
            },
        },
        evidence=recs,
        schema=session.schema,
        scenes=graphs[-3:],
        target_identity={"selector": "synth"},
    )
    assert plan is not None
    ctrl = ExperimentController(session, min_frames=4)
    ctrl.accept_plan(plan)

    def click(node_id: str) -> None:
        box = next(n.bbox for n in graph.nodes if n.node_id == node_id)
        world.click_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def rollback(_node_id: str) -> None:
        world.undo_click(CONTROL_NODE_ID)

    def after():
        _r, g2, s2 = _observe_world(world, assembler, session)
        graphs.append(g2)
        return g2, s2

    _r, graph, snap = _observe_world(world, assembler, session)
    for idx in (1, 2):
        trial = execute_trial(
            plan=plan,
            index=idx,
            graph=graph,
            before=snap,
            click=click,
            observe_after=after,
            rollback=rollback,
            provenance={"integrity": "trusted"},
            live=False,
            target=None,
            identity=None,
        )
        ctrl.record_trial(trial)
        _r, graph, snap = _observe_world(world, assembler, session)

    schema = ctrl.promote()
    assert len(schema.actions) == 1
    act = schema.actions[0]
    assert act.constraints == dict(PROMOTED_NATIVE_CONSTRAINTS)
    assert act.selector
    assert act.expected_effects
    assert len(ctrl.trials) == 2
    assert ctrl.trials[0].observed_deltas == ctrl.trials[1].observed_deltas

    compiled = compile_schema(schema)
    native_constraints_from_spec(compiled.actions[0])

    session2 = Session(tmp_path)
    reloaded = load_head(session2.schema_dir)
    assert reloaded.actions[0].constraints == dict(PROMOTED_NATIVE_CONSTRAINTS)


def test_compile_schema_rejects_native_action_without_constraints():
    schema = empty_schema()
    bad = ActionSpec(
        action_id="act_score_increased",
        parameters=(ParamSpec("target", "entity"),),
        preconditions=(),
        primitive_plan=(PrimitiveStep("click", ("target",)),),
        expected_effects=(EffectPredicate("score", "increased"),),
        selector={"label": "score"},
        constraints={},
    )
    schema = replace(schema, actions=(bad,))
    with pytest.raises(CatalogCompileError, match="missing native constraints"):
        compile_schema(schema)


def test_merge_rejects_invalid_native_constraints():
    with pytest.raises(CatalogCompileError):
        apply_action_revision(
            empty_schema(),
            action_payload={
                "clickable_region": "target",
                "field_id": "alpha",
                "op": "increased",
                "constraints": {
                    "risk": "low",
                    "retry": "deny",
                    "cooldown_s": 0.5,
                    "max_plan_duration_s": 0,
                },
            },
        )
