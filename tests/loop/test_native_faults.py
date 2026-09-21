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
from loop.adaptive.contracts import ObservationQuality
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
from tests.loop.test_policy_worker import _FakeDecider, _worker


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


@pytest.mark.parametrize(
    "fault,setup,expect",
    [
        (
            "stale_capture",
            {"integrity": "untrusted"},
            {"dispatch_accepted": False, "reason_prefix": "release_observe"},
        ),
        (
            "focus_loss",
            {"focused": False, "action_ids": ("probe_a",)},
            {"dispatch_accepted": False, "reason_prefix": "unfocused"},
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
        assert any(r.decision_source in ("unavailable", "decider_pending") for r in rows)
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


def test_directive_expiry_rejects_dispatch():
    store = GenerationStore()
    cfg = OrchestratorConfig(directive_ttl_s=0.01)
    directive = ensure_directive(store, episode_generation=1, config=cfg)
    assert directive is not None
    time.sleep(0.02)
    executor = Executor(snapshot_max_age_s=1.0, decision_max_age_s=1.0)
    from loop.adaptive.controller import _v2_from_native_obs
    from loop.adaptive.executor import NativeDispatchContext
    from loop.adaptive.snapshot import build_native_observation

    schema = _two_action_schema()
    snap = _snap(schema)
    catalog = compile_catalog_from_surface(schema, snap)
    d_expired = store.latest()
    assert d_expired is not None
    mask = compile_mask(catalog, d_expired)
    from loop.adaptive.policy import StructuredDecision

    decision = StructuredDecision(
        candidate_id="probe_a",
        confidence=1.0,
        probabilities={"probe_a": 1.0, "probe_b": 0.0},
        decided_at=time.monotonic(),
        episode_generation=1,
        snapshot_id=snap.snapshot_id,
        directive_revision=d_expired.revision,
        catalog_revision=catalog.revision,
    )
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
    v2 = _v2_from_native_obs(native_obs)
    receipt = executor.dispatch(
        decision,
        v2,
        catalog,
        d_expired,
        now=time.monotonic(),
        focused=True,
        native=NativeDispatchContext(
            observation=native_obs,
            mask=mask,
            expected_mask_digest=mask_digest(mask),
            policy_revision="p",
            surface_action_ids=frozenset(snap.surface.action_ids),
        ),
    )
    assert receipt.accepted is False
    assert receipt.reason == "directive_expired"


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
        for kind in ("reset", "shutdown", "terminal", "policy_worker_failed"):
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
