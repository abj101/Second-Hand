"""Wave 0 frozen native contract types."""

from __future__ import annotations

import pytest

from loop.adaptive.cli import apply_collect_operating_requests
from loop.adaptive.contracts import (
    CatalogCompileError,
    NativeCandidate,
    NativeObservation,
    NativeTrustedConstraints,
    ObservationQuality,
    OperatingState,
    OperatingTransition,
    TransitionFacts,
    after_preflight,
    catalog_revision_for,
    compile_native_catalog,
    decider_may_arm,
    transition_operating_state,
)
from loop.adaptive.schema import ActionSurface, StateSnapshot, empty_schema
from loop.adaptive.snapshot import StateSnapshotV2


def _constraints(**overrides: object) -> NativeTrustedConstraints:
    base = dict(
        risk="low",
        retry="deny",
        cooldown_s=0.5,
        max_plan_duration_s=1.0,
    )
    base.update(overrides)
    return NativeTrustedConstraints(**base)  # type: ignore[arg-type]


def _verified_snapshot() -> StateSnapshot:
    rev = empty_schema().revision.revision_id
    surface = ActionSurface(snapshot_id="s1", schema_revision_id=rev, action_ids=())
    return StateSnapshot(
        snapshot_id="s1",
        schema_revision_id=rev,
        mode_id=None,
        fields={},
        entities={},
        surface=surface,
    )


def test_native_observation_wraps_verified_snapshot_not_v2() -> None:
    snap = _verified_snapshot()
    obs = NativeObservation(
        episode_id="ep",
        episode_generation=1,
        capture_ts_monotonic=1.0,
        publication_ts_monotonic=1.1,
        observation_age_s=0.1,
        capture_integrity="ok",
        capture_provenance=("window_capture",),
        hwnd=100,
        pid=200,
        target_identity={"selector": "win"},
        snapshot_id=snap.snapshot_id,
        schema_revision_id=snap.schema_revision_id,
        quality=ObservationQuality.trusted,
        missing_required_fields=(),
        snapshot=snap,
    )
    assert isinstance(obs.snapshot, StateSnapshot)
    assert not isinstance(obs.snapshot, StateSnapshotV2)


def test_catalog_compile_fails_without_conservative_constraints() -> None:
    good = NativeCandidate("a1", "desc", _constraints())
    compile_native_catalog("schema-rev", (good,))

    with pytest.raises(CatalogCompileError, match="risk"):
        compile_native_catalog(
            "schema-rev",
            (NativeCandidate("a1", "desc", _constraints(risk="")),),
        )
    with pytest.raises(CatalogCompileError, match="max_plan_duration"):
        compile_native_catalog(
            "schema-rev",
            (NativeCandidate("a1", "desc", _constraints(max_plan_duration_s=0)),),
        )


def test_catalog_revision_is_content_hash_and_order_invariant() -> None:
    c1 = NativeCandidate("b", "two", _constraints())
    c2 = NativeCandidate("a", "one", _constraints())
    cat = compile_native_catalog("rev-1", (c1, c2))
    assert cat.candidates[0].action_id == "a"
    assert cat.revision == catalog_revision_for("rev-1", cat.candidates)
    again = compile_native_catalog("rev-1", (c2, c1))
    assert again.revision == cat.revision


def test_after_preflight_and_decider_may_arm() -> None:
    assert after_preflight(False, True) is OperatingState.Observe
    assert after_preflight(True, False) is OperatingState.Observe
    assert after_preflight(True, None) is OperatingState.Observe
    assert after_preflight(True, True) is OperatingState.Observe
    assert decider_may_arm(False) is False
    assert decider_may_arm(True) is True


def test_transition_boot_requires_manifests_and_release() -> None:
    facts = TransitionFacts(manifests_checked=False, inputs_released=True)
    assert (
        transition_operating_state(
            OperatingState.Boot, OperatingTransition.boot_complete, facts
        )
        is OperatingState.Boot
    )


def test_transition_rejects_observe_to_armed_jump() -> None:
    facts = TransitionFacts(
        react_gates_pass=True,
        operator_arms=True,
        surface_has_promoted_action=True,
    )
    assert (
        transition_operating_state(
            OperatingState.Observe, OperatingTransition.operator_arm_react, facts
        )
        is OperatingState.Observe
    )


def test_transition_shadow_to_armed_requires_gates_and_operator() -> None:
    base = TransitionFacts(
        react_gates_pass=True,
        operator_arms=True,
    )
    assert (
        transition_operating_state(
            OperatingState.ShadowReact, OperatingTransition.operator_arm_react, base
        )
        is OperatingState.ArmedReact
    )
    no_arm = TransitionFacts(react_gates_pass=True, operator_arms=False)
    assert (
        transition_operating_state(
            OperatingState.ShadowReact, OperatingTransition.operator_arm_react, no_arm
        )
        is OperatingState.ShadowReact
    )


def test_halted_recovery_requires_explicit_release_and_generation() -> None:
    ok = TransitionFacts(
        explicit_recovery=True,
        inputs_released=True,
        generation_incremented=True,
    )
    assert (
        transition_operating_state(
            OperatingState.Halted, OperatingTransition.recover_from_halt, ok
        )
        is OperatingState.Observe
    )
    partial = TransitionFacts(
        explicit_recovery=True,
        inputs_released=True,
        generation_incremented=False,
    )
    assert (
        transition_operating_state(
            OperatingState.Halted, OperatingTransition.recover_from_halt, partial
        )
        is OperatingState.Halted
    )


def test_failed_preflight_blocks_cli_arm_transition() -> None:
    state = apply_collect_operating_requests(
        OperatingState.Observe,
        experiment=False,
        arm=True,
        recover=False,
        observation_stable=True,
        surface_has_promoted_action=True,
        preflight_ok=False,
        target_bound=True,
    )
    assert state is OperatingState.ShadowReact


def test_cli_arm_from_observe_without_surface_stays_observe() -> None:
    state = apply_collect_operating_requests(
        OperatingState.Observe,
        experiment=False,
        arm=True,
        recover=False,
        observation_stable=True,
        surface_has_promoted_action=False,
        preflight_ok=None,
        target_bound=True,
    )
    assert state is OperatingState.Observe


def _observation_with(**overrides: object) -> NativeObservation:
    snap = _verified_snapshot()
    base = dict(
        episode_id="ep",
        episode_generation=1,
        capture_ts_monotonic=1.0,
        publication_ts_monotonic=1.1,
        observation_age_s=0.1,
        capture_integrity="ok",
        capture_provenance=("window_capture",),
        hwnd=100,
        pid=200,
        target_identity={"selector": "win"},
        snapshot_id=snap.snapshot_id,
        schema_revision_id=snap.schema_revision_id,
        quality=ObservationQuality.trusted,
        missing_required_fields=(),
        snapshot=snap,
    )
    base.update(overrides)
    return NativeObservation(**base)  # type: ignore[arg-type]


def test_acquisition_eligibility_fails_closed_on_defaults() -> None:
    obs = _observation_with()
    assert obs.capture_age_known is False
    assert obs.acquisition_origin == "unknown"
    assert obs.observation_sequence is None
    assert obs.acquisition_is_actuation_eligible() is False


def test_acquisition_eligibility_requires_bound_age_sequence_and_trust() -> None:
    fresh = _observation_with(
        capture_age_known=True,
        acquisition_origin="pre-grab-sync",
        observation_sequence=1,
    )
    assert fresh.acquisition_is_actuation_eligible() is True
    assert (
        _observation_with(capture_age_known=True, observation_sequence=None)
        .acquisition_is_actuation_eligible()
        is False
    )
    assert (
        _observation_with(observation_sequence=1).acquisition_is_actuation_eligible()
        is False
    )
    assert (
        _observation_with(
            capture_age_known=True,
            observation_sequence=1,
            quality=ObservationQuality.untrusted,
        ).acquisition_is_actuation_eligible()
        is False
    )
