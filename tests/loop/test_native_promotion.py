"""Wave 2: promoted native actions carry conservative constraints."""

from __future__ import annotations

import time
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


def _functional_world_observe(session, world, assembler):
    scene_holder: list = [None]
    sequence = [0]

    def observe():
        from tests.loop.test_adaptive_experiment import _observe_world

        recs, graph, snap = _observe_world(world, assembler, session)
        scene_holder[0] = graph
        sequence[0] += 1
        observe.provenance = {
            "integrity": "trusted",
            "hwnd": 1,
            "pid": 42,
            "source": "ci_capture",
            # Explicit acquisition: functional-CI fakes state the capture bound,
            # origin and a monotonically increasing sequence instead of
            # synthesizing age from publication time.
            "capture_ts_monotonic": time.monotonic(),
            "capture_age_known": True,
            "acquisition_origin": "pre-grab-sync",
            "observation_sequence": sequence[0],
        }
        return recs, graph, snap, scene_holder

    return observe


def _promote_one_reversible(session, world, assembler) -> None:
    from tests.loop.test_adaptive_experiment import (
        _control_and_field,
        _observe_world,
    )

    from loop.adaptive.experiment import ExperimentController, admit_plan, execute_trial

    graphs = []
    recs = ()
    for _ in range(4):
        recs, graph, _snap = _observe_world(world, assembler, session)
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
        target_identity={"selector": "synth", "hwnd": 1, "pid": 42},
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

    _recs, graph, snap = _observe_world(world, assembler, session)
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
        _recs, graph, snap = _observe_world(world, assembler, session)
    ctrl.promote()
    for node in graph.nodes:
        if node.node_id.startswith("track_") and "numeric" not in node.attributes:
            assembler.note_clickable(node.node_id)
            break


def _add_second_promoted_action(session) -> None:
    from loop.adaptive.orchestrate import apply_action_revision, compile_schema

    field_id = session.schema.fields[0].field_id if session.schema.fields else "score"
    schema = apply_action_revision(
        session.schema,
        field_payload={"field_id": field_id, "value_type": "number", "label": field_id},
        action_payload={
            "action_id": f"act_{field_id}_alt_increased",
            "field_id": field_id,
            "op": "increased",
            "clickable_region": "target",
            "selector": {"label": field_id},
        },
        store_dir=session.schema_dir,
    )
    session.accept_schema(compile_schema(schema))


def test_functional_ci_loop_one_candidate(tmp_path: Path):
    from loop.adaptive.cli import _make_runner
    from loop.adaptive.experiment import run_functional_ci_loop
    from loop.adaptive.perceive import SceneAssembler
    from loop.adaptive.session import Session
    from loop.adaptive.world import NeutralWorld

    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    observe = _functional_world_observe(session, world, assembler)
    scene_holder: list = [None]

    def runner(steps, bindings):
        graph = scene_holder[0] or observe()[1]
        for st in steps:
            if st.op == "click":
                target = bindings.get("target")
                box = next(n.bbox for n in graph.nodes if n.node_id == target)
                world.click_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            else:
                _make_runner(world, scene_holder)( (st,), bindings)

    def promote_and_prime(s):
        _promote_one_reversible(s, world, assembler)
        scene_holder[0] = observe()[1]

    summary = run_functional_ci_loop(
        session,
        observe=observe,
        runner=runner,
        promote=promote_and_prime,
        target_identity={
            "selector": "synth",
            "hwnd": 1,
            "pid": 42,
            "exe": "test.exe",
            "window_class": "Test",
        },
    )
    acc = summary["acceptance"]
    assert acc["1_bind_rejects_replacement"]
    assert acc["2_empty_schema"]
    assert acc["3_promoted_reversible"]
    assert acc["4_opaque_catalog_ids"]
    assert acc["5_shadow_no_act"]
    assert acc["6_rejections"]
    assert acc["7_leased_act"]
    assert acc["8_release_while_scoring"]
    assert acc["9_effect_and_provenance"]
    assert acc["10_replay_chain"]
    assert acc["deterministic_not_labeled_decider"]
    assert summary["decision_source"] == "DeterministicPolicy"


def test_functional_ci_loop_two_candidates_decider(tmp_path: Path):
    from loop.adaptive.cli import _make_runner
    from loop.adaptive.experiment import run_functional_ci_loop
    from loop.adaptive.perceive import SceneAssembler
    from loop.adaptive.session import Session
    from loop.adaptive.world import NeutralWorld

    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    observe = _functional_world_observe(session, world, assembler)
    scene_holder: list = [None]

    def runner(steps, bindings):
        graph = scene_holder[0] or observe()[1]
        for st in steps:
            if st.op == "click":
                target = bindings.get("target")
                box = next(n.bbox for n in graph.nodes if n.node_id == target)
                world.click_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            else:
                _make_runner(world, scene_holder)((st,), bindings)

    def promote_and_prime(s):
        _promote_one_reversible(s, world, assembler)
        scene_holder[0] = observe()[1]

    class _PairDecider:
        calls = 0

        def system_one(self, state, questions, *, independent, layout):
            _PairDecider.calls += 1
            a0 = session.schema.actions[0].action_id
            a1 = session.schema.actions[1].action_id
            return {
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": a0,
                        "confidence": 0.65,
                        "probabilities": {a0: 0.65, a1: 0.35},
                    }
                }
            }

    fake = _PairDecider()
    summary = run_functional_ci_loop(
        session,
        observe=observe,
        runner=runner,
        promote=promote_and_prime,
        second_action=_add_second_promoted_action,
        decider=fake,
        target_identity={
            "selector": "synth",
            "hwnd": 1,
            "pid": 42,
            "exe": "test.exe",
            "window_class": "Test",
        },
    )
    acc = summary["acceptance"]
    assert acc["two_candidate_decider_source"]
    assert _PairDecider.calls >= 1
    assert summary["shadow_decision_source"] in ("DeciderPolicy", "DeciderPolicy_pending")
    assert summary["decision_source"] != "Decider"


def test_functional_ci_loop_rebound_between_score_and_act(tmp_path: Path):
    import time
    from dataclasses import replace

    from loop.adaptive.arm import ArmConfig, _compile_frame, _dispatch_decision
    from loop.adaptive.controller import Executor, GenerationStore
    from loop.adaptive.experiment import CiFakeInputOwner, run_functional_ci_loop
    from loop.adaptive.perceive import SceneAssembler
    from loop.adaptive.policy import StructuredDecision
    from loop.adaptive.schema import SceneGraph, SceneNode
    from loop.adaptive.session import Session
    from loop.adaptive.world import NeutralWorld

    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    observe = _functional_world_observe(session, world, assembler)

    def probe_rebound(sess, owner: CiFakeInputOwner) -> bool:
        from loop.adaptive.arm import acquisition_from_observe

        recs, graph, snap, holder = observe()
        cfg = ArmConfig()
        store = GenerationStore()
        native_obs, catalog, directive, mask = _compile_frame(
            sess,
            snap,
            episode_id="rebound-ep",
            episode_generation=cfg.episode_generation,
            hwnd=1,
            pid=42,
            capture_integrity="trusted",
            capture_provenance=("ci",),
            directive_store=store,
            config=cfg,
            **acquisition_from_observe(observe),
        )
        now = time.monotonic()
        decision = StructuredDecision(
            candidate_id=snap.surface.action_ids[0],
            confidence=1.0,
            probabilities={snap.surface.action_ids[0]: 1.0},
            decided_at=now,
            episode_generation=cfg.episode_generation,
            snapshot_id="snap-lease",
            directive_revision=directive.revision,
            catalog_revision=catalog.revision,
            bound_entity_id="rebound-node",
            scene_id="snap-lease",
        )
        leased = SceneGraph(
            snapshot_id="snap-lease",
            nodes=(
                SceneNode(
                    node_id="rebound-node",
                    bbox=(0.1, 0.1, 0.2, 0.2),
                    attributes={"clickable": True},
                    evidence_ids=(),
                ),
            ),
        )
        moved = SceneGraph(
            snapshot_id="snap-latest",
            nodes=(
                SceneNode(
                    node_id="rebound-node",
                    bbox=(0.7, 0.7, 0.8, 0.8),
                    attributes={"clickable": True},
                    evidence_ids=(),
                ),
            ),
        )
        holder[0] = moved
        newer_snap = replace(snap, snapshot_id="snap-latest")
        newer_obs = replace(
            native_obs,
            snapshot_id="snap-latest",
            snapshot=newer_snap,
        )
        from loop.adaptive.controller import _v2_from_native_obs
        from loop.adaptive.executor import NativeDispatchContext
        from loop.adaptive.candidates import mask_digest

        ctx = NativeDispatchContext(
            observation=newer_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision=cfg.policy_revision,
            surface_action_ids=frozenset(snap.surface.action_ids),
            leased_scene=leased,
            current_scene=moved,
        )
        ex = Executor(
            snapshot_max_age_s=cfg.snapshot_max_age_s,
            decision_max_age_s=cfg.decision_deadline_s,
        )
        receipt = ex.dispatch(
            decision,
            replace(_v2_from_native_obs(newer_obs), snapshot_id="snap-latest"),
            catalog,
            directive,
            now=time.monotonic(),
            native=ctx,
        )
        owner.release_all("rebound_probe")
        return receipt.reason == "rebound" and not receipt.accepted

    from loop.adaptive.cli import _make_runner

    scene_holder: list = [None]

    scene_holder: list = [None]

    def runner(steps, bindings):
        graph = scene_holder[0] or observe()[1]
        for st in steps:
            if st.op == "click":
                target = bindings.get("target")
                box = next(n.bbox for n in graph.nodes if n.node_id == target)
                world.click_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    def promote_and_prime(s):
        _promote_one_reversible(s, world, assembler)
        scene_holder[0] = observe()[1]

    summary = run_functional_ci_loop(
        session,
        observe=observe,
        runner=runner,
        promote=promote_and_prime,
        target_identity={
            "selector": "synth",
            "hwnd": 1,
            "pid": 42,
            "exe": "test.exe",
            "window_class": "Test",
        },
        probe_rebound_act=probe_rebound,
    )
    assert summary["acceptance"]["rebound_before_act"]


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


def test_functional_ci_rejects_unknown_capture_provenance(tmp_path: Path):
    from loop.adaptive.experiment import run_functional_ci_loop
    from loop.adaptive.perceive import SceneAssembler
    from loop.adaptive.session import Session
    from loop.adaptive.world import NeutralWorld

    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    inner = _functional_world_observe(session, world, assembler)

    def observe():
        recs, graph, snap, holder = inner()
        # A real route with no verified frame time: age stays unknown.
        inner.provenance = {
            "integrity": "trusted",
            "hwnd": 1,
            "pid": 42,
            "source": "hwnd-wgc",
        }
        return recs, graph, snap, holder

    def runner(_steps, _bindings):
        raise AssertionError("no input expected")

    def promote_and_prime(sess):
        _promote_one_reversible(sess, world, assembler)

    with pytest.raises(RuntimeError, match="unverified_acquisition"):
        run_functional_ci_loop(
            session,
            observe=observe,
            runner=runner,
            promote=promote_and_prime,
            target_identity={
                "selector": "synth",
                "hwnd": 1,
                "pid": 42,
                "exe": "test.exe",
                "window_class": "Test",
            },
        )


def test_functional_ci_carries_truthful_acquisition_into_armed_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import loop.adaptive.arm as arm_mod
    from loop.adaptive.cli import _make_runner
    from loop.adaptive.experiment import run_functional_ci_loop
    from loop.adaptive.perceive import SceneAssembler
    from loop.adaptive.session import Session
    from loop.adaptive.world import NeutralWorld

    session = Session(tmp_path)
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    observe = _functional_world_observe(session, world, assembler)

    compiled: list[dict] = []
    real_compile = arm_mod._compile_frame

    def spy_compile(*args, **kwargs):
        compiled.append(dict(kwargs))
        return real_compile(*args, **kwargs)

    monkeypatch.setattr(arm_mod, "_compile_frame", spy_compile)

    after: list[dict] = []
    real_capture = arm_mod._capture_from_observe

    def spy_capture(observe_fn):
        after.append(dict(getattr(observe_fn, "provenance", None) or {}))
        return real_capture(observe_fn)

    monkeypatch.setattr(arm_mod, "_capture_from_observe", spy_capture)

    scene_holder: list = [None]

    def runner(steps, bindings):
        graph = scene_holder[0] or observe()[1]
        for st in steps:
            if st.op == "click":
                target = bindings.get("target")
                box = next(n.bbox for n in graph.nodes if n.node_id == target)
                world.click_at((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
            else:
                _make_runner(world, scene_holder)((st,), bindings)

    def promote_and_prime(sess):
        _promote_one_reversible(sess, world, assembler)
        scene_holder[0] = observe()[1]

    run_functional_ci_loop(
        session,
        observe=observe,
        runner=runner,
        promote=promote_and_prime,
        target_identity={
            "selector": "synth",
            "hwnd": 1,
            "pid": 42,
            "exe": "test.exe",
            "window_class": "Test",
        },
    )

    assert len(compiled) == 1
    act = compiled[0]
    assert act["capture_age_known"] is True
    assert act["acquisition_origin"] == "pre-grab-sync"
    assert act["capture_ts_monotonic"] is not None
    assert isinstance(act["observation_sequence"], int)

    # The after-capture is a fresh capture strictly later than the action frame.
    assert after and str(after[0].get("source", "")).startswith("ci_after_")
    assert after[0]["capture_age_known"] is True
    assert after[0]["capture_ts_monotonic"] >= act["capture_ts_monotonic"]
    assert after[0]["observation_sequence"] > act["observation_sequence"]
