"""Wave 7 table-driven native fault injection (mocked time/worker/owner)."""

from __future__ import annotations

import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from loop.adaptive.candidates import (
    compile_catalog_from_surface,
    compile_mask,
    mask_digest,
)
from loop.adaptive.contracts import ObservationQuality, OperatingState
from loop.adaptive.controller import (
    Executor,
    GenerationStore,
    NativeShadowConfig,
    NativeShadowLoop,
    ReactConfig,
    run_episode,
)
from loop.adaptive.decider_policy import DeciderPolicy
from loop.adaptive.environment import MemoryEnvironment
from loop.adaptive.orchestrator import (
    OrchestratorConfig,
    SyncOrchestratorRunner,
    ensure_directive,
    maybe_screen_steer_wake,
    orchestrator_worker,
)
from loop.adaptive.policy import DeterministicPolicy
from loop.adaptive.policy_worker import PolicyWorker
from loop.adaptive.schema import (
    ActionInstance,
    ActionSpec,
    ActionSurface,
    EffectPredicate,
    PrimitiveStep,
    StateSnapshot,
    empty_schema,
)
from loop.adaptive.session import Session
from loop.adaptive.snapshot import MotionPerception
from loop.adaptive.telemetry import TelemetryStore

from tests.loop.test_decider_policy import _snapshot
from tests.loop.test_policy_worker import _FakeDecider, _wait_terminal, _worker


def _constraints() -> dict:
    return {
        "risk": "low",
        "retry": "deny",
        "cooldown_s": 0.5,
        "max_plan_duration_s": 1.0,
    }


def _two_action_schema():
    schema = empty_schema()
    specs = []
    for aid in ("probe_a", "probe_b"):
        specs.append(
            ActionSpec(
                action_id=aid,
                parameters=(),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate("score", "increased"),),
                constraints=_constraints(),
            )
        )
    return replace(schema, actions=tuple(specs))


def _snap(schema, action_ids: tuple[str, ...] = ("probe_a", "probe_b")) -> StateSnapshot:
    rev = schema.revision.revision_id
    surface = ActionSurface(
        snapshot_id="snap-fault",
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
        snapshot_id="snap-fault",
        schema_revision_id=rev,
        mode_id="running",
        fields={"score": 1},
        entities={},
        surface=surface,
    )


def _shadow_loop(tmp: Path) -> NativeShadowLoop:
    session = Session(tmp)
    session.accept_schema(_two_action_schema())
    session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
    worker = PolicyWorker(DeterministicPolicy(), inference_timeout_s=0.5)
    return NativeShadowLoop(
        session,
        worker,
        Executor(snapshot_max_age_s=0.5, decision_max_age_s=0.25),
        TelemetryStore(tmp / "react.jsonl", workspace=tmp),
        config=NativeShadowConfig(decision_deadline_s=0.25),
    )


def _acquisition_kwargs(sequence: int = 1) -> dict:
    """Explicit fake-clock acquisition contract for a fixture whose fresh
    action is valid: known capture bound, origin and a monotone sequence."""
    return {
        "capture_ts_monotonic": time.monotonic(),
        "capture_age_known": True,
        "acquisition_origin": "pre-grab-sync",
        "observation_sequence": sequence,
    }


@pytest.mark.parametrize(
    "fault,setup,expect",
    [
        (
            "stale_capture",
            {"integrity": "untrusted"},
            {"dispatch_accepted": False, "reason_prefix": "release_observe"},
        ),
    ],
)
def test_shadow_fault_table_surface(fault, setup, expect):
    del fault
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shadow = _shadow_loop(root)
        try:
            aids = setup.get("action_ids", ("probe_a", "probe_b"))
            snap = _snap(shadow._session.schema, tuple(aids))  # noqa: SLF001
            shadow.process_frame(
                snap,
                hwnd=1,
                pid=42,
                capture_integrity=str(setup.get("integrity", "trusted")),
                focused=bool(setup.get("focused", True)),
            )
        finally:
            shadow.close()
        row = shadow._telemetry.shadow_rows()[-1]  # noqa: SLF001
        assert row.dispatch_accepted is expect["dispatch_accepted"]
        assert row.dispatch_reason.startswith(expect["reason_prefix"])


def test_focus_loss_release_lands_halted():
    from loop.adaptive.experiment import CiFakeInputOwner

    owner = CiFakeInputOwner()
    owner.enable_actuation()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        worker = PolicyWorker(DeterministicPolicy(), inference_timeout_s=0.5)
        shadow = NativeShadowLoop(
            session,
            worker,
            Executor(snapshot_max_age_s=0.5, decision_max_age_s=0.25),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(decision_deadline_s=0.25),
            owner=owner,
            operating_state=OperatingState.ArmedReact,
        )
        try:
            snap = _snap(session.schema, ("probe_a",))
            shadow.process_frame(
                snap,
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
                focused=False,
                **_acquisition_kwargs(1),
            )
        finally:
            shadow.close()
        row = shadow._telemetry.shadow_rows()[-1]  # noqa: SLF001
        assert not row.dispatch_accepted
        assert row.dispatch_reason.startswith("unfocused")
        assert shadow.operating_state is OperatingState.Halted
        assert any(r[0] == "release_all" for r in owner.releases)


def _probe_response(choice: str = "probe_a") -> dict[str, object]:
    other = "probe_b" if choice == "probe_a" else "probe_a"
    return {
        "answers": {
            "action": {
                "type": "choice",
                "choice": choice,
                "confidence": 0.7,
                "probabilities": {choice: 0.7, other: 0.3},
            }
        }
    }


def test_slow_inference_marks_unavailable_no_dispatch():
    fake = _FakeDecider(delay_s=0.35, response=_probe_response("probe_a"))
    worker = _worker(fake, timeout_s=0.05)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = NativeShadowLoop(
            session,
            worker,
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(decision_deadline_s=0.05),
        )
        try:
            shadow.process_frame(
                _snap(session.schema),
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
            )
            time.sleep(0.4)
            shadow.process_frame(
                _snap(session.schema),
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
            )
        finally:
            shadow.close()
        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        assert any(r.decision_source == "unavailable" for r in rows)
        assert shadow.metrics()["shadow_dispatch_accepted"] == 0


def test_malformed_inference_unavailable():
    fake = _FakeDecider(response={"answers": {"action": {"type": "choice", "choice": "nope"}}})
    policy = DeciderPolicy(fake, catalog_revision="c1")
    worker = PolicyWorker(policy, inference_timeout_s=1.0)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = NativeShadowLoop(
            session,
            worker,
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
        )
        try:
            snap = _snap(session.schema)
            shadow.process_frame(
                snap,
                hwnd=1,
                pid=42,
                capture_integrity="trusted",
            )
            for _ in range(3):
                shadow.process_frame(
                    snap,
                    hwnd=1,
                    pid=42,
                    capture_integrity="trusted",
                )
        finally:
            shadow.close()
        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        assert any(r.decision_source == "unavailable" for r in rows)
        bad = [r for r in rows if r.decision_source == "unavailable"]
        assert bad and all(not r.dispatch_accepted for r in bad)


def test_directive_hard_expiry_release_lands_observe():
    from loop.adaptive.experiment import CiFakeInputOwner

    fake = _FakeDecider(delay_s=0.2, response=_probe_response("probe_a"))
    worker = _worker(fake, timeout_s=2.0)
    owner = CiFakeInputOwner()
    owner.enable_actuation()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = NativeShadowLoop(
            session,
            worker,
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(directive_ttl_s=0.01, decision_deadline_s=2.0),
            owner=owner,
            operating_state=OperatingState.ShadowReact,
        )
        snap = _snap(session.schema)
        try:
            shadow.process_frame(
                snap, hwnd=1, pid=42, capture_integrity="trusted", **_acquisition_kwargs(1)
            )
            time.sleep(0.02)
            shadow.process_frame(
                snap, hwnd=1, pid=42, capture_integrity="trusted", **_acquisition_kwargs(2)
            )
            turn_id = shadow._telemetry.shadow_rows()[-1].policy_turn_id  # noqa: SLF001
            _wait_terminal(worker, turn_id, timeout_s=2.0)
            shadow.process_frame(
                snap, hwnd=1, pid=42, capture_integrity="trusted", **_acquisition_kwargs(3)
            )
        finally:
            shadow.close()
            worker.shutdown()
        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        assert any(r.dispatch_reason == "directive_expired" for r in rows)
        assert shadow.operating_state is OperatingState.Observe
        assert any(r[0] == "release_all" for r in owner.releases)


def test_bootstrap_directive_refresh_stays_actionable_in_register():
    from loop.adaptive.experiment import CiFakeInputOwner

    owner = CiFakeInputOwner()
    owner.enable_actuation()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        worker = PolicyWorker(DeterministicPolicy(), inference_timeout_s=0.5)
        shadow = NativeShadowLoop(
            session,
            worker,
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(directive_ttl_s=0.01, decision_deadline_s=2.0),
            owner=owner,
            operating_state=OperatingState.ShadowReact,
        )
        snap = _snap(session.schema, ("probe_a",))
        try:
            shadow.process_frame(
                snap, hwnd=1, pid=42, capture_integrity="trusted", **_acquisition_kwargs(1)
            )
            before_rev = shadow._telemetry.shadow_rows()[-1].directive_revision  # noqa: SLF001
            time.sleep(0.02)
            shadow.process_frame(
                snap, hwnd=1, pid=42, capture_integrity="trusted", **_acquisition_kwargs(2)
            )
        finally:
            shadow.close()
            worker.shutdown()
        rows = shadow._telemetry.shadow_rows()  # noqa: SLF001
        after_rev = rows[-1].directive_revision
        assert after_rev > before_rev
        assert shadow.operating_state is OperatingState.ShadowReact
        assert owner.releases == [("release_all", "controller_close")]
        assert not any(r.dispatch_reason == "directive_expired" for r in rows)
        assert any(r.dispatch_accepted for r in rows)


def test_orchestrator_stall_does_not_block_reaction():
    store = GenerationStore()
    blocked = threading.Event()
    blocked.clear()

    class StalledRunner(SyncOrchestratorRunner):
        def _run(self) -> None:
            blocked.wait(timeout=5.0)

    runner = StalledRunner(
        store,
        episode_generation=0,
        config=OrchestratorConfig(),
    )
    try:
        t0 = time.monotonic()
        directive = ensure_directive(store, episode_generation=0, config=OrchestratorConfig())
        elapsed = time.monotonic() - t0
        assert directive is not None
        assert elapsed < 0.05
    finally:
        blocked.set()
        runner.close()


def test_benchmark_run_episode_release_all_on_timeout(tmp_path):
    import asyncio

    class SlowPolicy(DeterministicPolicy):
        def score(self, snapshot, directive, candidates):
            time.sleep(0.2)
            return super().score(snapshot, directive, candidates)

    async def _run():
        env = MemoryEnvironment(action_ids=("noop", "advance"), max_steps=3)
        executor = Executor()
        ds = GenerationStore()
        report = await run_episode(
            env,
            MotionPerception(),
            SlowPolicy("advance", catalog_revision="memory-catalog-v1"),
            executor,
            TelemetryStore(tmp_path / "t.jsonl"),
            ds,
            GenerationStore(),
            seed=1,
            episode_id="e",
            episode_generation=1,
            config=ReactConfig(max_steps=1, decision_deadline_s=0.05),
        )
        assert executor.release_count >= 1
        assert report["invalidation_failures"] >= 0

    asyncio.run(_run())


def test_screen_signature_wake_bumps_directive_revision():
    from loop.adaptive.schema import SceneGraph, SceneNode

    store = GenerationStore()
    runner = SyncOrchestratorRunner(
        store,
        episode_generation=1,
        config=OrchestratorConfig(),
    )
    try:
        time.sleep(0.06)
        assert store.latest() is not None

        prev_sig: frozenset[str] | None = None
        prev_unkeyed: int | None = None

        def _apply(graph: SceneGraph, **wake_kw) -> int:
            nonlocal prev_sig, prev_unkeyed
            prev_sig, prev_unkeyed, ev = maybe_screen_steer_wake(
                graph,
                previous_signature=prev_sig,
                previous_unkeyed=prev_unkeyed,
                **wake_kw,
            )
            if ev is not None:
                kind = str(ev.pop("kind"))
                runner.notify(kind, **ev)
                time.sleep(0.06)
            latest = store.latest()
            assert latest is not None
            return latest.revision

        graph_reg0 = SceneGraph(
            snapshot_id="s0",
            nodes=(
                SceneNode(
                    "track_0",
                    (0.1, 0.1, 0.2, 0.2),
                    (),
                    {"registry_id": "reg_0", "label": "Play"},
                ),
            ),
        )
        rev_after_first = _apply(graph_reg0)
        animated = SceneGraph(
            snapshot_id="s0-anim",
            nodes=(
                SceneNode(
                    "track_0",
                    (0.5, 0.5, 0.6, 0.6),
                    (),
                    {"registry_id": "reg_0", "label": "Play"},
                ),
            ),
        )
        rev_after_anim = _apply(animated)
        assert rev_after_anim == rev_after_first

        graph_two = SceneGraph(
            snapshot_id="s1",
            nodes=(
                SceneNode(
                    "track_0",
                    (0.1, 0.1, 0.2, 0.2),
                    (),
                    {"registry_id": "reg_0", "label": "Play"},
                ),
                SceneNode(
                    "track_1",
                    (0.3, 0.1, 0.4, 0.2),
                    (),
                    {"registry_id": "reg_1", "label": "Quit"},
                ),
            ),
        )
        rev_after_new_id = _apply(graph_two)
        assert rev_after_new_id == rev_after_anim + 1

        prev_sig, prev_unkeyed, ev = maybe_screen_steer_wake(
            graph_two,
            previous_signature=prev_sig,
            previous_unkeyed=prev_unkeyed,
            negative_evidence_recorded=True,
            forbidden_ids=("reg_0",),
        )
        assert ev is not None
        runner.notify(str(ev.pop("kind")), **ev)
        time.sleep(0.06)
        rev_after_neg = store.latest().revision
        assert rev_after_neg == rev_after_new_id + 1

        runner.notify(
            "screen_signature",
            forbidden_ids=("not_on_screen", "reg_0"),
            surface_action_ids=("reg_0", "reg_1"),
        )
        time.sleep(0.06)
        assert store.latest().forbidden_ids == ("reg_0",)
    finally:
        runner.close()


def test_steer_wake_exposes_rule_policy_and_coalesces_signature():
    from loop.adaptive.schema import SceneGraph, SceneNode

    graph = SceneGraph(
        snapshot_id="stable",
        nodes=(
            SceneNode(
                "track_0",
                (0.1, 0.1, 0.2, 0.2),
                (),
                {"registry_id": "reg_0", "label": "Play"},
            ),
        ),
    )
    previous, unkeyed, wake = maybe_screen_steer_wake(
        graph,
        previous_signature=None,
        previous_unkeyed=None,
    )
    assert wake is not None
    assert wake["policy_id"] == "rule_v1"
    assert wake["steer_wake"] is True
    assert "registry_signature_changed" in wake["wake_reason"]

    same_previous, same_unkeyed, no_wake = maybe_screen_steer_wake(
        graph,
        previous_signature=previous,
        previous_unkeyed=unkeyed,
    )
    assert same_previous == previous
    assert same_unkeyed == unkeyed
    assert no_wake is None

    store = GenerationStore()
    runner = SyncOrchestratorRunner(store, episode_generation=1, config=OrchestratorConfig())
    try:
        time.sleep(0.05)
        runner.notify("screen_signature", **{k: v for k, v in wake.items() if k != "kind"})
        time.sleep(0.05)
        first_revision = store.latest().revision
        runner.notify("screen_signature", **{k: v for k, v in wake.items() if k != "kind"})
        time.sleep(0.05)
        assert store.latest().revision == first_revision
        assert runner.steer_wake_telemetry[-1]["policy_id"] == "rule_v1"
    finally:
        runner.close()


def test_orchestrator_worker_survives_event_burst():
    import asyncio

    async def _run():
        store = GenerationStore()
        events: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()
        task = asyncio.create_task(
            orchestrator_worker(
                events,
                store,
                episode_generation=1,
                config=OrchestratorConfig(),
                stop=stop,
            )
        )
        for kind in ("schema_change", "quality_loss", "directive_expired", "repeated_no_effect"):
            await events.put({"kind": kind})
        await asyncio.sleep(0.05)
        latest = store.latest()
        assert latest is not None
        assert latest.revision >= 2
        stop.set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


@pytest.mark.soak
def test_shadow_soak_thirty_minutes_placeholder():
    pytest.skip("opt-in: 30-minute concurrent shadow soak on live target")


def test_untrusted_capture_skips_policy_worker_submit():
    submits: list[int] = []
    worker = PolicyWorker(DeterministicPolicy(), inference_timeout_s=0.5)
    real_submit = worker.submit

    def _count_submit(*args, **kwargs):
        submits.append(1)
        return real_submit(*args, **kwargs)

    worker.submit = _count_submit  # type: ignore[method-assign]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shadow = _shadow_loop(root)
        try:
            shadow.process_frame(
                _snap(shadow._session.schema),  # noqa: SLF001
                hwnd=1,
                pid=42,
                capture_integrity="untrusted",
            )
        finally:
            shadow.close()
        assert submits == []
        row = shadow._telemetry.shadow_rows()[-1]  # noqa: SLF001
        assert row.dispatch_reason.startswith("release_observe")
        assert row.decision_source == "observe"


def test_dispatch_rebound_rejects_without_lease():
    from loop.adaptive.controller import _v2_from_native_obs
    from loop.adaptive.executor import NativeDispatchContext
    from loop.adaptive.policy import StructuredDecision
    from loop.adaptive.schema import SceneGraph, SceneNode
    from loop.adaptive.snapshot import build_native_observation

    schema = _two_action_schema()
    snap = _snap(schema)
    catalog = compile_catalog_from_surface(schema, snap)
    directive = ensure_directive(GenerationStore(), episode_generation=1, config=OrchestratorConfig())
    assert directive is not None
    mask = compile_mask(catalog, directive)
    native_obs = build_native_observation(
        snap,
        episode_id="e",
        episode_generation=1,
        capture_ts_monotonic=time.monotonic(),
        publication_ts_monotonic=time.monotonic(),
        capture_integrity="trusted",
        capture_provenance=("test",),
        quality=ObservationQuality.trusted,
        hwnd=1,
        pid=42,
        target_identity={"hwnd": 1},
    )
    leased = SceneGraph(
        snapshot_id="snap-lease",
        nodes=(
            SceneNode("node-a", (0.1, 0.1, 0.2, 0.2), {"clickable": True}, ()),
        ),
    )
    current = SceneGraph(
        snapshot_id="snap-latest",
        nodes=(
            SceneNode("node-a", (0.5, 0.5, 0.6, 0.6), {"clickable": True}, ()),
        ),
    )
    newer_snap = replace(snap, snapshot_id="snap-latest")
    newer_obs = replace(native_obs, snapshot_id="snap-latest", snapshot=newer_snap)
    decision = StructuredDecision(
        candidate_id="probe_a",
        confidence=1.0,
        probabilities={"probe_a": 1.0, "probe_b": 0.0},
        decided_at=time.monotonic(),
        episode_generation=1,
        snapshot_id="snap-lease",
        directive_revision=directive.revision,
        catalog_revision=catalog.revision,
        bound_entity_id="node-a",
        scene_id="snap-lease",
    )
    receipt = Executor().dispatch(
        decision,
        replace(_v2_from_native_obs(newer_obs), snapshot_id="snap-latest"),
        catalog,
        directive,
        now=time.monotonic(),
        native=NativeDispatchContext(
            observation=newer_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision="p",
            surface_action_ids=frozenset(snap.surface.action_ids),
            leased_scene=leased,
            current_scene=current,
        ),
    )
    assert not receipt.accepted
    assert receipt.reason == "rebound"
    assert receipt.lease is None


def test_inflight_worker_crash_unavailable_release_stays_shadow():
    from loop.adaptive.experiment import CiFakeInputOwner
    from loop.adaptive.policy_worker import PolicyTurnStatus

    fake = _FakeDecider(error=RuntimeError("scorer dead"))
    worker = _worker(fake, timeout_s=2.0)
    owner = CiFakeInputOwner()
    owner.enable_actuation()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        session = Session(root)
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        shadow = NativeShadowLoop(
            session,
            worker,
            Executor(),
            TelemetryStore(root / "react.jsonl", workspace=root),
            config=NativeShadowConfig(decision_deadline_s=2.0),
            owner=owner,
            operating_state=OperatingState.ShadowReact,
        )
        try:
            snap = _snap(session.schema)
            shadow.process_frame(snap, hwnd=1, pid=42, capture_integrity="trusted")
            turn_id = shadow._telemetry.shadow_rows()[-1].policy_turn_id  # noqa: SLF001
            assert turn_id > 0
            result = _wait_terminal(worker, turn_id, timeout_s=2.0)
            assert result.status is PolicyTurnStatus.UNAVAILABLE
            assert str(result.reason).startswith("worker_failed")
            shadow.process_frame(snap, hwnd=1, pid=42, capture_integrity="trusted")
        finally:
            shadow.close()
            worker.shutdown()
        unavailable = [
            r
            for r in shadow._telemetry.shadow_rows()  # noqa: SLF001
            if r.decision_source == "unavailable"
        ]
        assert unavailable
        assert all(r.decision_source not in ("Decider", "DeciderPolicy") for r in unavailable)
        assert all(not r.dispatch_accepted for r in unavailable)
        assert any(
            str(r.dispatch_reason).startswith("worker_failed") for r in unavailable
        )
        assert shadow.operating_state in (
            OperatingState.ShadowReact,
            OperatingState.Observe,
        )
        assert any(r[0] == "release_all" for r in owner.releases)
        assert fake.calls >= 1


def test_mask_change_rejects_lease_on_act():
    from loop.adaptive.contracts import ActuationLease
    from loop.adaptive.runtime import InvocationError
    from loop.adaptive.schema import ActionInvocation

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        session.accept_schema(_two_action_schema())
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        snap = _snap(session.schema, ("probe_a",))
        session._episode_generation = 0  # noqa: SLF001
        session._catalog_revision = "cat"  # noqa: SLF001
        session._directive_revision = 1  # noqa: SLF001
        session._policy_revision = "p"  # noqa: SLF001
        session._hard_mask_digest = "expected-mask"  # noqa: SLF001
        lease = ActuationLease(
            episode_generation=0,
            snapshot_id=snap.snapshot_id,
            schema_revision_id=snap.schema_revision_id,
            catalog_revision="cat",
            directive_revision=1,
            policy_revision="p",
            hwnd=1,
            pid=42,
            target_identity={"hwnd": 1},
            expires_at_monotonic=time.monotonic() + 1.0,
            selected_action_id="probe_a",
            hard_mask_digest="other-mask",
        )
        inv = ActionInvocation(
            "probe_a",
            {"target": "t"},
            snap.snapshot_id,
            snap.schema_revision_id,
        )
        owner = type("O", (), {"release_all": lambda self, r: "released", "drain_primitive_results": lambda self: ()})()
        receipt = session.act(
            inv,
            lambda _s, _b: (_ for _ in ()).throw(InvocationError("should not run")),
            snap,
            before=snap,
            lease=lease,
            owner=owner,
        )
        assert not receipt.accepted
        assert receipt.reason == "mask digest stale"


def test_target_replacement_invalidates_decision():
    from loop.adaptive.controller import _v2_from_native_obs
    from loop.adaptive.executor import NativeDispatchContext
    from loop.adaptive.policy import StructuredDecision
    from loop.adaptive.snapshot import build_native_observation

    schema = _two_action_schema()
    snap = _snap(schema)
    catalog = compile_catalog_from_surface(schema, snap)
    directive = ensure_directive(GenerationStore(), episode_generation=2, config=OrchestratorConfig())
    assert directive is not None
    mask = compile_mask(catalog, directive)
    native_obs = build_native_observation(
        snap,
        episode_id="e",
        episode_generation=2,
        capture_ts_monotonic=time.monotonic(),
        publication_ts_monotonic=time.monotonic(),
        capture_integrity="trusted",
        capture_provenance=("test",),
        quality=ObservationQuality.trusted,
        hwnd=1,
        pid=42,
        target_identity={"hwnd": 1},
    )
    decision = StructuredDecision(
        candidate_id="probe_a",
        confidence=1.0,
        probabilities={"probe_a": 1.0, "probe_b": 0.0},
        decided_at=time.monotonic(),
        episode_generation=1,
        snapshot_id=snap.snapshot_id,
        directive_revision=directive.revision,
        catalog_revision=catalog.revision,
    )
    receipt = Executor().dispatch(
        decision,
        _v2_from_native_obs(native_obs),
        catalog,
        directive,
        now=time.monotonic(),
        native=NativeDispatchContext(
            observation=native_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision="p",
            surface_action_ids=frozenset(snap.surface.action_ids),
        ),
    )
    assert not receipt.accepted
    assert receipt.reason == "generation_mismatch"


def test_release_failure_recovery_locks_session():
    from loop.adaptive.contracts import ActuationLease
    from loop.adaptive.schema import ActionInvocation, PrimitiveStep

    with tempfile.TemporaryDirectory() as tmp:
        session = Session(Path(tmp))
        schema = replace(
            _two_action_schema(),
            actions=(
                    replace(
                        _two_action_schema().actions[0],
                        primitive_plan=(PrimitiveStep("sleep", (), {"duration_s": 0.001}),),
                    ),
            ),
        )
        session.accept_schema(schema)
        session.bind_target({"selector": "dummy", "hwnd": 1, "pid": 42})
        snap = _snap(session.schema, ("probe_a",))
        digest = "mask-digest"
        session._episode_generation = 0  # noqa: SLF001
        session._catalog_revision = "cat"  # noqa: SLF001
        session._directive_revision = 1  # noqa: SLF001
        session._policy_revision = "p"  # noqa: SLF001
        session._hard_mask_digest = digest  # noqa: SLF001
        lease = ActuationLease(
            episode_generation=0,
            snapshot_id=snap.snapshot_id,
            schema_revision_id=snap.schema_revision_id,
            catalog_revision="cat",
            directive_revision=1,
            policy_revision="p",
            hwnd=1,
            pid=42,
            target_identity={"hwnd": 1},
            expires_at_monotonic=time.monotonic() + 1.0,
            selected_action_id="probe_a",
            hard_mask_digest=digest,
        )
        inv = ActionInvocation(
            "probe_a",
            {},
            snap.snapshot_id,
            snap.schema_revision_id,
        )

        class FailOwner:
            def check_target(self) -> tuple[bool, str]:
                return True, "ok"

            def run_plan(self, steps, bindings, *, scene=None, guard=None) -> None:
                if guard is not None:
                    guard()

            def release_all(self, reason: str) -> str:
                return f"failed:{reason}"

            def drain_primitive_results(self):
                return ()

        receipt = session.act(
            inv,
            lambda _s, _b: None,
            snap,
            before=snap,
            lease=lease,
            owner=FailOwner(),
        )
        assert not receipt.accepted or receipt.release_status.startswith("failed:")
        assert session.recovery_state() is not None
        assert session.recovery_state().get("reason") == "release_failure"


def test_shutdown_releases_held_input():
    from loop.adaptive.experiment import CiFakeInputOwner

    owner = CiFakeInputOwner()
    owner.enable_actuation()
    owner.release_all("hold")
    owner.shutdown()
    assert any(r[0] == "release_all" for r in owner.releases)


def test_functional_ci_records_capture_to_lease_latency(tmp_path: Path):
    from loop.adaptive.cli import _make_runner
    from loop.adaptive.experiment import run_functional_ci_loop
    from loop.adaptive.perceive import SceneAssembler
    from loop.adaptive.world import NeutralWorld

    from tests.loop.test_native_promotion import (
        _functional_world_observe,
        _promote_one_reversible,
    )

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

    def promote_and_prime(sess):
        _promote_one_reversible(sess, world, assembler)
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
    assert summary["capture_to_lease_s"] > 0.0
    assert summary["capture_to_lease_latencies"]


def test_memory_env_zero_illegal_executions_on_mismatch(tmp_path):
    import asyncio

    async def _run():
        env = MemoryEnvironment(action_ids=("advance",), max_steps=2)
        executor = Executor()
        report = await run_episode(
            env,
            MotionPerception(),
            DeterministicPolicy("advance", catalog_revision="memory-catalog-v1"),
            executor,
            TelemetryStore(tmp_path / "t.jsonl"),
            GenerationStore(),
            GenerationStore(),
            seed=1,
            episode_id="e",
            episode_generation=1,
            config=ReactConfig(max_steps=1),
            focused=False,
        )
        assert report["steps"] <= 1
        assert executor.release_count >= 1

    asyncio.run(_run())
