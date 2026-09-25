"""Phase 4: provisional offer projection and one-click lease planning."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from loop.adaptive.candidates import (
    MaskGateContext,
    compile_catalog_from_surface,
    compile_mask,
    mask_digest,
)
from loop.adaptive.contracts import ObservationQuality
from loop.adaptive.controller import GenerationStore
from loop.adaptive.controller import Executor, NativeShadowConfig, NativeShadowLoop
from loop.adaptive.orchestrator import OrchestratorConfig, ensure_directive
from loop.adaptive.runtime import project
from loop.adaptive.schema import SceneGraph, SceneNode, empty_schema
from loop.adaptive.policy import DeterministicPolicy
from loop.adaptive.policy_worker import PolicyWorker
from loop.adaptive.session import Session
from loop.adaptive.telemetry import TelemetryStore


def _graph(*rows: tuple[str, str]) -> SceneGraph:
    return SceneGraph(
        "frame-1",
        tuple(
            SceneNode(
                f"node-{rid}",
                (0.1 + i * 0.2, 0.1, 0.2 + i * 0.2, 0.2),
                (),
                {"registry_id": rid, "label": label},
            )
            for i, (rid, label) in enumerate(rows)
        ),
    )


def test_registry_offers_are_legal_and_compile_to_one_click_plan() -> None:
    graph = _graph(("reg-a", "Audio"), ("reg-b", "Video"))
    snap = project(graph, empty_schema())
    assert snap.surface.action_ids == ("reg-a", "reg-b")
    offers = tuple(
        type("Offer", (), {"offer_id": rid, "label": label, "node_id": f"node-{rid}"})
        for rid, label in (("reg-a", "Audio"), ("reg-b", "Video"))
    )
    catalog = compile_catalog_from_surface(empty_schema(), snap, provisional_offers=offers)
    assert [c.description for c in catalog.candidates] == ["Audio", "Video"]
    plan = catalog.transient_plan_for("reg-a")
    assert plan is not None
    assert [step.op for step in plan.primitive_plan] == ["click"]
    assert plan.node_id == "node-reg-a"
    assert plan.constraints.retry == "deny"


def test_mask_preserves_valid_projected_offer() -> None:
    graph = _graph(("reg-a", "Audio"))
    snap = project(graph, empty_schema())
    offer = type("Offer", (), {"offer_id": "reg-a", "label": "Audio", "node_id": "node-reg-a"})
    catalog = compile_catalog_from_surface(empty_schema(), snap, provisional_offers=(offer,))
    directive = ensure_directive(GenerationStore(), episode_generation=0, config=OrchestratorConfig())
    assert directive is not None
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(snap.surface.action_ids),
        ),
    )
    assert mask.allowed_ids == ("reg-a",)
    assert mask_digest(mask)


def test_direct_experiment_route_is_disabled_and_never_sends_input() -> None:
    class Owner:
        def __init__(self) -> None:
            self.plans = []
            self.releases = []

        def run_plan(self, steps, bindings, *, scene) -> None:
            self.plans.append((steps, bindings, scene))

        def release_all(self, reason: str) -> str:
            self.releases.append(reason)
            return "released"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        graph = _graph(("reg-a", "Audio"))
        snap = project(graph, session.schema)
        owner = Owner()
        loop = NativeShadowLoop(
            session,
            PolicyWorker(DeterministicPolicy(), inference_timeout_s=1.0),
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(actuate_transient_offers=True),
            owner=owner,
        )
        try:
            for sequence in (1, 2):
                loop.process_frame(
                    snap,
                    hwnd=1,
                    pid=1,
                    capture_integrity="trusted",
                    scene_graph=graph,
                    capture_ts_monotonic=time.monotonic(),
                    capture_age_known=True,
                    acquisition_origin="pre-grab-sync",
                    observation_sequence=sequence,
                )
        finally:
            loop.close()

        # H1: the direct route is disabled outright.  The loop never hands a
        # transient plan to the input owner, it refuses once per offer, and it
        # releases the owner instead of acting.
        assert owner.plans == []
        metrics = loop.metrics()
        assert metrics["transient_actuations"] == 0
        assert metrics["transient_actuations_disabled"] == 1
        assert "direct_actuation_disabled" in owner.releases
        disabled = [
            record
            for record in session.ledger.all()
            if record.kind == "direct_actuation_disabled"
        ]
        assert len(disabled) == 1
        assert disabled[0].payload["action_id"] == "reg-a"
        assert disabled[0].payload["registry_id"] == "reg-a"
        # A disabled refusal is not negative evidence about the target.
        assert all(record.kind != "negative_evidence" for record in session.ledger.all())
        # The hypothetical lease and its receipt are still produced.
        rows = loop._telemetry.shadow_rows()  # noqa: SLF001
        assert rows
        assert rows[-1].dispatch_accepted is True


def test_transient_actuation_requires_known_acquisition() -> None:
    class Owner:
        def __init__(self) -> None:
            self.plans = []

        def run_plan(self, steps, bindings, *, scene) -> None:
            self.plans.append((steps, bindings, scene))

        def release_all(self, _reason: str) -> str:
            return "released"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        graph = _graph(("reg-a", "Audio"))
        snap = project(graph, session.schema)
        owner = Owner()
        loop = NativeShadowLoop(
            session,
            PolicyWorker(DeterministicPolicy(), inference_timeout_s=1.0),
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(actuate_transient_offers=True),
            owner=owner,
        )
        try:
            for _ in range(2):
                # No acquisition contract: unknown age must never run a plan,
                # even with transient actuation enabled.
                loop.process_frame(
                    snap,
                    hwnd=1,
                    pid=1,
                    capture_integrity="trusted",
                    scene_graph=graph,
                )
        finally:
            loop.close()

        assert owner.plans == []
        assert loop.metrics()["transient_actuations"] == 0
        # The acquisition gate rejects before the disabled direct route is even
        # reached, so the refusal counter stays at zero here.
        assert loop.metrics()["transient_actuations_disabled"] == 0
        rows = loop._telemetry.shadow_rows()  # noqa: SLF001
        assert all(
            r.dispatch_reason == "unverified_acquisition"
            for r in rows
            if not r.dispatch_accepted
        )
