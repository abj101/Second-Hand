"""Wave 0 frozen native contract types."""

from __future__ import annotations

import pytest

from loop.adaptive.contracts import (
    CatalogCompileError,
    NativeCandidate,
    NativeObservation,
    NativeTrustedConstraints,
    ObservationQuality,
    OperatingState,
    after_preflight,
    catalog_revision_for,
    compile_native_catalog,
    decider_may_arm,
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
