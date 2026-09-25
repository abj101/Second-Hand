"""Wave 3 native adapter, catalog compile, and dispatch gate tests."""

from __future__ import annotations

import json
import random
import time
from dataclasses import replace

import pytest

from loop.adaptive.candidates import (
    ActionCatalog,
    CandidateAction,
    Directive,
    MaskGateContext,
    compile_catalog_from_surface,
    compile_mask,
    mask_digest,
    select_if_unambiguous,
)
from loop.adaptive.contracts import ObservationQuality
from loop.adaptive.decider_policy import build_decider_questions, build_decider_state
from loop.adaptive.executor import Executor, NativeDispatchContext
from loop.adaptive.policy import (
    DeterministicPolicy,
    ReactiveState,
    project_reactive_for_model,
    reactive_state_from_native,
    reactive_state_from_v2,
)
from loop.adaptive.schema import SceneGraph, SceneNode
from loop.adaptive.schema import (
    ActionInstance,
    ActionSpec,
    ActionSurface,
    EffectPredicate,
    ParamSpec,
    PrimitiveStep,
    StateSnapshot,
    empty_schema,
)
from loop.adaptive.snapshot import (
    EntityView,
    Provenanced,
    StateSnapshotV2,
    assert_no_privileged,
    build_native_observation,
)


def _constraints() -> dict:
    return {
        "risk": "low",
        "retry": "deny",
        "cooldown_s": 0.5,
        "max_plan_duration_s": 1.0,
    }


def _schema_with_actions(*action_ids: str):
    schema = empty_schema()
    actions = []
    for aid in action_ids:
        actions.append(
            ActionSpec(
                action_id=aid,
                parameters=(),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate("score", "increased"),),
                constraints=_constraints(),
            )
        )
    from dataclasses import replace as dc_replace

    return dc_replace(schema, actions=tuple(actions))


def _verified_snapshot(schema, action_ids: tuple[str, ...]) -> StateSnapshot:
    rev = schema.revision.revision_id
    surface = ActionSurface(
        snapshot_id="snap-native",
        schema_revision_id=rev,
        action_ids=action_ids,
        actions=tuple(
            ActionInstance(
                action_id=aid,
                parameters=(),
                domains=(),
                expected_effects=(EffectPredicate("score", "increased"),),
            )
            for aid in action_ids
        ),
    )
    return StateSnapshot(
        snapshot_id="snap-native",
        schema_revision_id=rev,
        mode_id="running",
        fields={"score": 1},
        entities={},
        surface=surface,
    )


def _observation(**overrides: object):
    schema = _schema_with_actions("click_a", "click_b")
    snap = _verified_snapshot(schema, ("click_a", "click_b"))
    captured = time.monotonic()
    base = dict(
        snapshot=snap,
        episode_id="ep1",
        episode_generation=3,
        capture_ts_monotonic=captured - 0.01,
        publication_ts_monotonic=captured,
        capture_integrity="ok",
        capture_provenance=("window_capture",),
        hwnd=42,
        pid=99,
        target_identity={"selector": "win"},
        quality=ObservationQuality.trusted,
        capture_age_known=True,
        acquisition_origin="pre-grab-sync",
        observation_sequence=1,
    )
    base.update(overrides)
    return build_native_observation(**base)  # type: ignore[arg-type]


def _v2_from_obs(obs) -> StateSnapshotV2:
    return StateSnapshotV2(
        snapshot_id=obs.snapshot_id,
        schema_revision_id=obs.schema_revision_id,
        episode_id=obs.episode_id,
        episode_generation=obs.episode_generation,
        step=0,
        frame_digest="hidden",
        frame_history_digests=(),
        captured_at=time.monotonic(),
        observation_age_s=obs.observation_age_s,
        mode=obs.snapshot.mode_id,
        fields={},
        entities=(),
        relations=(),
        recent_events=(),
        confidence=1.0,
    )


def _native_context(obs, catalog, directive, mask):
    return NativeDispatchContext(
        observation=obs,
        mask=mask,
        expected_mask_digest=mask_digest(mask),
        policy_revision="policy-v1",
        surface_action_ids=frozenset(obs.snapshot.surface.action_ids),
    )


def test_compile_catalog_from_surface_hashes_and_never_exposes_plan() -> None:
    schema = _schema_with_actions("b_act", "a_act")
    snap = _verified_snapshot(schema, ("a_act", "b_act"))
    catalog = compile_catalog_from_surface(schema, snap)
    assert [c.action_id for c in catalog.candidates] == ["a_act", "b_act"]
    assert all("click" not in c.description.lower() for c in catalog.candidates)
    rendered = json.dumps(
        {
            "revision": catalog.revision,
            "candidates": [
                {"id": c.action_id, "description": c.description} for c in catalog.candidates
            ],
        }
    )
    assert "primitive" not in rendered
    assert "PrimitiveStep" not in rendered


def test_compile_mask_zero_candidates_no_noop_invention() -> None:
    schema = _schema_with_actions("only")
    snap = _verified_snapshot(schema, ())
    catalog = compile_catalog_from_surface(schema, snap)
    directive = Directive(1, 0, "bootstrap", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(surface_ids=frozenset(snap.surface.action_ids)),
    )
    assert mask.allowed_ids == ()
    assert "noop" not in mask.blocked
    assert select_if_unambiguous(mask) is None


def test_reactive_adapters_redacted() -> None:
    obs = _observation()
    native = reactive_state_from_native(obs)
    v2 = StateSnapshotV2(
        snapshot_id=obs.snapshot_id,
        schema_revision_id=obs.schema_revision_id,
        episode_id=obs.episode_id,
        episode_generation=obs.episode_generation,
        step=1,
        frame_digest="must-not-leak",
        frame_history_digests=("secret-digest",),
        captured_at=0.0,
        observation_age_s=0.01,
        mode="running",
        fields={"hp": Provenanced(3, 0.8, False, ("motion",))},
        entities=(EntityView("player", "ent-1", {"x": Provenanced(9, 1.0, False, ())}, 0.9),),
        relations=(),
        recent_events=({"verified": True, "action_id": "click_a", "hwnd": 999},),
        confidence=0.85,
    )
    bench = reactive_state_from_v2(v2)
    for state in (native, bench):
        assert_no_privileged(state)
        payload = build_decider_state(state, Directive(1, obs.episode_generation, "go", 30.0, 0.0))
        assert_no_privileged(payload)
        blob = repr(payload)
        assert "must-not-leak" not in blob
        assert "hwnd" not in blob
        assert "999" not in blob or "999" not in json.dumps(payload)
        assert "click" not in blob.lower() or "click_a" in blob


def test_decider_request_has_no_command_or_primitive_leaks() -> None:
    obs = _observation()
    catalog = compile_catalog_from_surface(
        _schema_with_actions("click_a", "click_b"),
        obs.snapshot,
    )
    _, questions = build_decider_questions(catalog.candidates)
    rendered = repr(questions)
    assert "key:" not in rendered
    assert "PrimitiveStep" not in rendered


@pytest.mark.parametrize(
    "mutator,reason",
    [
        (lambda ctx: replace(ctx, expected_mask_digest="deadbeef"), "mask_mismatch"),
        (lambda ctx: replace(ctx, recovery_locked=True), "recovery_locked"),
        (
            lambda ctx: replace(
                ctx,
                observation=replace(
                    ctx.observation,
                    quality=ObservationQuality.untrusted,
                ),
            ),
            "untrusted_capture",
        ),
        (
            lambda ctx: replace(
                ctx,
                surface_action_ids=frozenset(),
            ),
            "not_on_surface",
        ),
    ],
)
def test_native_dispatch_rejects_without_lease(mutator, reason: str) -> None:
    obs = _observation()
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    ctx = _native_context(obs, catalog, directive, mask)
    ctx = mutator(ctx)
    ex = Executor()
    now = time.monotonic()
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        obs.snapshot_id,
        directive.revision,
        catalog.revision,
    )
    snap = _v2_from_obs(obs)
    receipt = ex.dispatch(
        decision,
        snap,
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == reason
    assert receipt.lease is None


def test_native_dispatch_fuzz_never_leases_on_stale_or_mismatch() -> None:
    rng = random.Random(0)
    ex = Executor(snapshot_max_age_s=0.2, decision_max_age_s=0.15)
    reasons = {
        "stale_snapshot",
        "stale_decision",
        "snapshot_id_mismatch",
        "generation_mismatch",
        "catalog_mismatch",
        "directive_mismatch",
        "masked",
    }
    for _ in range(40):
        obs = _observation(
            episode_generation=rng.randint(1, 5),
            publication_ts_monotonic=100.0 + rng.random() * 0.1,
        )
        schema = _schema_with_actions("click_a", "click_b")
        catalog = compile_catalog_from_surface(schema, obs.snapshot)
        directive = Directive(
            rng.randint(1, 3),
            obs.episode_generation,
            "fuzz",
            30.0,
            time.monotonic(),
        )
        mask = compile_mask(
            catalog,
            directive,
            gate=MaskGateContext(
                quality=ObservationQuality.trusted,
                surface_ids=frozenset(obs.snapshot.surface.action_ids),
            ),
        )
        ctx = _native_context(obs, catalog, directive, mask)
        now = time.monotonic()
        from loop.adaptive.policy import StructuredDecision

        decision = StructuredDecision(
            rng.choice(["click_a", "click_b"]),
            1.0,
            {"click_a": 0.5, "click_b": 0.5},
            now - rng.choice([0.0, 0.5, 1.0]),
            obs.episode_generation + rng.choice([0, 0, 1]),
            obs.snapshot_id if rng.random() > 0.2 else "wrong",
            directive.revision,
            catalog.revision if rng.random() > 0.2 else "bad-rev",
        )
        snap = replace(
            _v2_from_obs(obs),
            observation_age_s=rng.choice([0.01, 0.5, 2.0]),
            episode_generation=obs.episode_generation,
        )
        receipt = ex.dispatch(
            decision,
            snap,
            catalog,
            directive,
            now=now,
            native=ctx,
        )
        if receipt.accepted:
            assert receipt.lease is not None
            assert receipt.lease.selected_action_id == receipt.action_id
        else:
            assert receipt.lease is None
            if receipt.reason in reasons:
                continue
            assert receipt.reason in {
                "directive_expired",
                "unavailable",
                "not_on_surface",
                "mask_mismatch",
                "untrusted_capture",
                "degraded_capture",
                "recovery_locked",
                "missing_required_fields",
                "target_identity",
                "schema_mismatch",
                    "rebound",
                    "unverified_acquisition",
            }


def test_native_dispatch_rejects_empty_policy_revision() -> None:
    obs = _observation()
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    ctx = replace(_native_context(obs, catalog, directive, mask), policy_revision="")
    ex = Executor()
    now = time.monotonic()
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        obs.snapshot_id,
        directive.revision,
        catalog.revision,
    )
    receipt = ex.dispatch(
        decision,
        _v2_from_obs(obs),
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == "policy_revision missing"
    assert receipt.lease is None


def test_arm_stages_four_five_use_deterministic_policy_source() -> None:
    from pathlib import Path

    arm_text = (Path(__file__).resolve().parents[2] / "loop/adaptive/arm.py").read_text(
        encoding="utf-8"
    )
    start = arm_text.index("# Stages 4–5")
    end = arm_text.index("# Stage 6", start)
    block = arm_text[start:end]
    assert 'decision_source="decider"' not in block
    assert "decision_source=decision_source" in block
    assert "DeterministicPolicy" in block


def test_native_dispatch_issues_lease_when_valid() -> None:
    obs = _observation()
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    ctx = _native_context(obs, catalog, directive, mask)
    ex = Executor()
    now = time.monotonic()
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        obs.snapshot_id,
        directive.revision,
        catalog.revision,
    )
    snap = _v2_from_obs(obs)
    receipt = ex.dispatch(
        decision,
        snap,
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert receipt.accepted and receipt.lease is not None
    lease = receipt.lease
    assert lease.snapshot_id == obs.snapshot_id
    assert lease.hard_mask_digest == mask_digest(mask)
    assert lease.policy_revision == "policy-v1"
    assert lease.hwnd == obs.hwnd and lease.pid == obs.pid


def test_directive_forbid_phantom_does_not_add_candidate() -> None:
    schema = _schema_with_actions("click_a", "click_b")
    snap = _verified_snapshot(schema, ("click_a", "click_b"))
    catalog = compile_catalog_from_surface(schema, snap)
    gate = MaskGateContext(surface_ids=frozenset(snap.surface.action_ids))
    open_mask = compile_mask(catalog, Directive(1, 0, "go", 30.0, time.monotonic()), gate=gate)
    forbid_b = compile_mask(
        catalog,
        Directive(1, 0, "go", 30.0, time.monotonic(), forbidden_ids=("click_b",)),
        gate=gate,
    )
    forbid_b_and_phantom = compile_mask(
        catalog,
        Directive(
            1,
            0,
            "go",
            30.0,
            time.monotonic(),
            forbidden_ids=("click_b", "phantom_action"),
        ),
        gate=gate,
    )
    assert open_mask.allowed_ids == ("click_a", "click_b")
    assert forbid_b.allowed_ids == ("click_a",)
    assert forbid_b_and_phantom.allowed_ids == forbid_b.allowed_ids


def test_degraded_and_untrusted_masks_are_empty() -> None:
    schema = _schema_with_actions("click_a")
    snap = _verified_snapshot(schema, ("click_a",))
    catalog = compile_catalog_from_surface(schema, snap)
    directive = Directive(1, 0, "go", 30.0, time.monotonic())
    gate = MaskGateContext(
        quality=ObservationQuality.trusted,
        surface_ids=frozenset(snap.surface.action_ids),
    )
    trusted = compile_mask(catalog, directive, gate=gate)
    assert trusted.allowed_ids == ("click_a",)
    degraded = compile_mask(
        catalog,
        directive,
        gate=replace(gate, quality=ObservationQuality.degraded),
    )
    assert degraded.allowed_ids == ()
    untrusted = compile_mask(
        catalog,
        directive,
        gate=replace(gate, quality=ObservationQuality.untrusted),
    )
    assert untrusted.allowed_ids == ()


def test_degraded_capture_rejects_dispatch_without_lease() -> None:
    obs = _observation(quality=ObservationQuality.degraded)
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.degraded,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    assert mask.allowed_ids == ()
    ctx = _native_context(obs, catalog, directive, mask)
    ex = Executor()
    now = time.monotonic()
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        obs.snapshot_id,
        directive.revision,
        catalog.revision,
    )
    receipt = ex.dispatch(
        decision,
        _v2_from_obs(obs),
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == "degraded_capture"
    assert receipt.lease is None


def _scene(snapshot_id: str, node_id: str, bbox: tuple[float, float, float, float]) -> SceneGraph:
    return SceneGraph(
        snapshot_id=snapshot_id,
        nodes=(SceneNode(node_id, bbox, (), {}),),
    )


def test_dispatch_accepts_newer_snapshot_when_binding_stable() -> None:
    obs = _observation()
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    leased = _scene("snap-decision", "node-a", (0.1, 0.1, 0.2, 0.2))
    current = _scene("snap-latest", "node-a", (0.11, 0.11, 0.21, 0.21))
    newer_snap = replace(obs.snapshot, snapshot_id="snap-latest")
    newer_obs = replace(obs, snapshot_id="snap-latest", snapshot=newer_snap)
    ctx = replace(
        _native_context(newer_obs, catalog, directive, mask),
        leased_scene=leased,
        current_scene=current,
    )
    ex = Executor()
    now = time.monotonic()
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        "snap-decision",
        directive.revision,
        catalog.revision,
        bound_entity_id="node-a",
        scene_id="snap-decision",
    )
    receipt = ex.dispatch(
        decision,
        replace(_v2_from_obs(newer_obs), snapshot_id="snap-latest"),
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert receipt.accepted
    assert receipt.lease is not None
    assert receipt.lease.target_identity["leased_scene_id"] == "snap-decision"
    assert receipt.lease.snapshot_id == "snap-decision"


def test_dispatch_rejects_rebound_when_binding_moved() -> None:
    obs = _observation()
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    leased = _scene("snap-decision", "node-a", (0.1, 0.1, 0.2, 0.2))
    current = _scene("snap-latest", "node-a", (0.5, 0.5, 0.6, 0.6))
    newer_snap = replace(obs.snapshot, snapshot_id="snap-latest")
    newer_obs = replace(obs, snapshot_id="snap-latest", snapshot=newer_snap)
    ctx = replace(
        _native_context(newer_obs, catalog, directive, mask),
        leased_scene=leased,
        current_scene=current,
    )
    ex = Executor()
    now = time.monotonic()
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        "snap-decision",
        directive.revision,
        catalog.revision,
        bound_entity_id="node-a",
        scene_id="snap-decision",
    )
    receipt = ex.dispatch(
        decision,
        replace(_v2_from_obs(newer_obs), snapshot_id="snap-latest"),
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == "rebound"
    assert receipt.lease is None


def test_actuation_scene_ignores_latest_snapshot() -> None:
    from loop.adaptive.snapshot import actuation_scene

    frozen = _scene("scene-frozen", "n1", (0.0, 0.0, 0.1, 0.1))
    latest = _scene("scene-new", "n2", (0.9, 0.9, 1.0, 1.0))
    resolved = actuation_scene("scene-frozen", frozen, latest)
    assert resolved.snapshot_id == "scene-frozen"
    assert resolved.nodes[0].node_id == "n1"


def test_attention_redacts_model_projection_only() -> None:
    from loop.adaptive.policy import ReactiveField

    obs = _observation()
    reactive = ReactiveState(
        snapshot_id=obs.snapshot_id,
        schema_revision_id=obs.schema_revision_id,
        episode_id=obs.episode_id,
        episode_generation=obs.episode_generation,
        mode="running",
        confidence=1.0,
        fields={
            "score": ReactiveField(1, 1.0, False),
            "hp": ReactiveField(3, 1.0, False),
        },
        entities=(),
        relations=(),
    )
    directive = Directive(1, obs.episode_generation, "go", 30.0, 0.0, attention=("score",))
    projected = project_reactive_for_model(reactive, directive)
    assert set(projected.fields) == {"score"}
    assert set(reactive.fields) == {"score", "hp"}


def test_policy_accepts_reactive_state() -> None:
    obs = _observation()
    reactive = reactive_state_from_native(obs)
    assert isinstance(reactive, ReactiveState)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    cands = (
        CandidateAction("click_a", "click_a", "desc a"),
        CandidateAction("click_b", "click_b", "desc b"),
    )
    decision = DeterministicPolicy("click_b", catalog_revision="x").score(
        reactive, directive, cands
    )
    assert decision.candidate_id == "click_b"
    assert decision.snapshot_id == obs.snapshot_id


def _dispatch_ready(obs):
    schema = _schema_with_actions("click_a", "click_b")
    catalog = compile_catalog_from_surface(schema, obs.snapshot)
    directive = Directive(1, obs.episode_generation, "i", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(obs.snapshot.surface.action_ids),
        ),
    )
    return catalog, directive, mask


def _decision(obs, catalog, directive, now: float):
    from loop.adaptive.policy import StructuredDecision

    return StructuredDecision(
        "click_a",
        1.0,
        {"click_a": 1.0},
        now,
        obs.episode_generation,
        obs.snapshot_id,
        directive.revision,
        catalog.revision,
    )


def test_native_dispatch_rejects_unknown_capture_age() -> None:
    obs = replace(
        _observation(),
        capture_ts_monotonic=0.0,
        capture_age_known=False,
        acquisition_origin="buffered-unknown",
        observation_sequence=None,
    )
    catalog, directive, mask = _dispatch_ready(obs)
    ctx = _native_context(obs, catalog, directive, mask)
    now = time.monotonic()
    receipt = Executor().dispatch(
        _decision(obs, catalog, directive, now),
        _v2_from_obs(obs),
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == "unverified_acquisition"
    assert receipt.lease is None


def test_native_dispatch_rejects_missing_observation_sequence() -> None:
    obs = replace(_observation(), observation_sequence=None)
    catalog, directive, mask = _dispatch_ready(obs)
    ctx = _native_context(obs, catalog, directive, mask)
    now = time.monotonic()
    receipt = Executor().dispatch(
        _decision(obs, catalog, directive, now),
        _v2_from_obs(obs),
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == "unverified_acquisition"


def test_native_dispatch_rejects_future_or_nonfinite_acquisition() -> None:
    now = time.monotonic()
    for capture, publication in ((now + 1, now + 1), (float("nan"), now)):
        obs = replace(
            _observation(),
            capture_ts_monotonic=capture,
            publication_ts_monotonic=publication,
        )
        catalog, directive, mask = _dispatch_ready(obs)
        receipt = Executor().dispatch(
            _decision(obs, catalog, directive, now),
            _v2_from_obs(obs),
            catalog,
            directive,
            now=now,
            native=_native_context(obs, catalog, directive, mask),
        )
        assert not receipt.accepted
        assert receipt.reason == "unverified_acquisition"


def test_native_dispatch_recomputes_age_at_dispatch_not_publication() -> None:
    # Perception produced a small publication age, but dispatch happens 1.2 s
    # after the real capture: the delayed decision must not rejuvenate it.
    now = time.monotonic()
    obs = replace(
        _observation(),
        capture_ts_monotonic=now - 1.2,
        publication_ts_monotonic=now - 1.19,
    )
    snap = replace(_v2_from_obs(obs), observation_age_s=0.01, captured_at=now - 1.2)
    catalog, directive, mask = _dispatch_ready(obs)
    ctx = _native_context(obs, catalog, directive, mask)
    receipt = Executor().dispatch(
        _decision(obs, catalog, directive, now),
        snap,
        catalog,
        directive,
        now=now,
        native=ctx,
    )
    assert not receipt.accepted
    assert receipt.reason == "stale_snapshot"
    assert receipt.lease is None
