"""Generic benchmark react path (memory env, no desktop input)."""

from __future__ import annotations

import ast
import asyncio
import random
import re
import time
from dataclasses import replace
from pathlib import Path

import pytest

from loop.adaptive.candidates import (
    ActionCatalog,
    ActionMask,
    CandidateAction,
    Directive,
    compile_mask,
    select_if_unambiguous,
)
from loop.adaptive.controller import (
    GenerationStore,
    ReactConfig,
    orchestrator_worker,
    reset_barrier,
    run_episode,
)
from loop.adaptive.environment import MemoryEnvironment
from loop.adaptive.executor import Executor
from loop.adaptive.policy import (
    DecisionError,
    DeterministicPolicy,
    RandomPolicy,
    StructuredDecision,
    validate_decision,
)
from loop.adaptive.snapshot import MotionPerception, assert_no_privileged, to_verified_snapshot
from loop.adaptive.telemetry import TelemetryStore

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTIVE_DIR = REPO_ROOT / "loop" / "adaptive"
_BANNED = re.compile(
    r"mariohtml5|nintendo|enjine|infinite_mario", re.I
)


def test_memory_environment_reset_and_step():
    async def _run():
        env = MemoryEnvironment(max_steps=4)
        r0 = await env.reset(7, "ep1", 1)
        assert r0.score_delta == 0.0
        assert r0.reward == 0.0
        assert r0.terminated is False
        assert r0.truncated is False
        assert r0.termination_reason is None
        assert "secret" in r0.privileged_info

        r1 = await env.step("advance")
        assert r1.score == 1.0
        assert r1.score_delta == 1.0
        assert r1.reward == 1.0

        r2 = await env.step("wait")
        assert r2.score == 1.0
        assert r2.score_delta == 0.0

    asyncio.run(_run())


def test_motion_perception_no_privileged_leak():
    async def _run():
        env = MemoryEnvironment()
        mp = MotionPerception()
        obs = await env.reset(1, "e", 0)
        snap = mp.qualify(obs)
        assert_no_privileged(snap)
        obs2 = await env.step("advance")
        snap2 = mp.qualify(obs2)
        assert_no_privileged(snap2)

    asyncio.run(_run())


def test_compile_mask_and_unambiguous():
    catalog = ActionCatalog(
        revision="c1",
        candidates=(
            CandidateAction("wait", "w", "wait", available=True),
            CandidateAction("advance", "a", "go", available=False),
            CandidateAction("retreat", "r", "back", available=True),
        ),
    )
    directive = Directive(1, 0, "x", 30.0, time.monotonic(), forbidden_ids=("retreat",))
    mask = compile_mask(catalog, directive)
    assert mask.allowed_ids == ("wait",)
    assert select_if_unambiguous(mask) == "wait"

    empty = compile_mask(
        catalog,
        Directive(1, 0, "x", 30.0, time.monotonic(), forbidden_ids=("wait", "retreat")),
    )
    assert empty.allowed_ids == ()
    assert select_if_unambiguous(empty) is None


def test_deterministic_and_random_policy():
    from loop.adaptive.snapshot import StateSnapshotV2, Provenanced

    snap = StateSnapshotV2(
        snapshot_id="s",
        schema_revision_id="r",
        episode_id="e",
        episode_generation=2,
        step=0,
        frame_digest="d",
        frame_history_digests=(),
        captured_at=0.0,
        observation_age_s=0.0,
        mode=None,
        fields={},
        entities=(),
        relations=(),
        recent_events=(),
        confidence=1.0,
    )
    directive = Directive(1, 2, "i", 30.0, time.monotonic())
    cands = (
        CandidateAction("wait", "w", ""),
        CandidateAction("advance", "a", ""),
    )
    det = DeterministicPolicy("advance", catalog_revision="c1")
    d = det.score(snap, directive, cands)
    assert d.candidate_id == "advance"

    rng = random.Random(0)
    rp = RandomPolicy(rng, catalog_revision="c1")
    picks = [rp.score(snap, directive, cands).candidate_id for _ in range(20)]
    rng2 = random.Random(0)
    rp2 = RandomPolicy(rng2, catalog_revision="c1")
    picks2 = [rp2.score(snap, directive, cands).candidate_id for _ in range(20)]
    assert picks == picks2


def test_validate_decision_rejects():
    from loop.adaptive.snapshot import StateSnapshotV2

    snap = StateSnapshotV2(
        snapshot_id="s",
        schema_revision_id="r",
        episode_id="e",
        episode_generation=1,
        step=0,
        frame_digest="d",
        frame_history_digests=(),
        captured_at=0.0,
        observation_age_s=0.0,
        mode=None,
        fields={},
        entities=(),
        relations=(),
        recent_events=(),
        confidence=1.0,
    )
    catalog = ActionCatalog("c1", (CandidateAction("wait", "w", ""),))
    directive = Directive(1, 1, "i", 30.0, time.monotonic())
    mask = ActionMask(("wait",), {})
    bad = StructuredDecision(
        "missing",
        1.0,
        {"missing": 1.0},
        time.monotonic(),
        1,
        "s",
        1,
        "c1",
    )
    with pytest.raises(DecisionError):
        validate_decision(bad, mask, catalog, snap, directive)

    gen_bad = replace(bad, candidate_id="wait", episode_generation=99)
    with pytest.raises(DecisionError):
        validate_decision(gen_bad, mask, catalog, snap, directive)


def test_executor_gates_and_release():
    from loop.adaptive.snapshot import StateSnapshotV2

    ex = Executor(snapshot_max_age_s=0.5, decision_max_age_s=0.25)
    env = MemoryEnvironment()
    snap = StateSnapshotV2(
        snapshot_id="s",
        schema_revision_id="r",
        episode_id="e",
        episode_generation=1,
        step=0,
        frame_digest="d",
        frame_history_digests=(),
        captured_at=time.monotonic(),
        observation_age_s=0.0,
        mode=None,
        fields={},
        entities=(),
        relations=(),
        recent_events=(),
        confidence=1.0,
    )
    catalog = ActionCatalog("c1", (CandidateAction("wait", "w", ""),))
    directive = Directive(1, 1, "i", 30.0, time.monotonic())
    now = time.monotonic()
    decision = StructuredDecision(
        "wait", 1.0, {"wait": 1.0}, now, 1, "s", 1, "c1"
    )
    ok = ex.dispatch(decision, snap, catalog, directive, now=now)
    assert ok.accepted and ex.held == "wait"

    stale = replace(decision, decided_at=now - 1.0)
    bad = ex.dispatch(stale, snap, catalog, directive, now=now)
    assert bad.reason == "stale_decision" and bad.watchdog == "release"

    gen = replace(decision, episode_generation=2, decided_at=now)
    bad2 = ex.dispatch(gen, snap, catalog, directive, now=now)
    assert bad2.reason == "generation_mismatch"

    unf = ex.dispatch(decision, snap, catalog, directive, now=now, focused=False)
    assert unf.reason == "unfocused"

    term = ex.dispatch(decision, snap, catalog, directive, now=now, terminal=True)
    assert term.reason == "terminal"

    stale_snap = replace(snap, observation_age_s=1.0)
    aged = ex.dispatch(decision, stale_snap, catalog, directive, now=now)
    assert aged.reason == "stale_snapshot"

    n0 = ex.release_count
    ex.release_all(env)
    assert ex.held is None and ex.release_count == n0 + 1 and env.released


def test_reset_barrier_wipes_prior_directive():
    async def _run():
        env = MemoryEnvironment()
        mp = MotionPerception()
        ex = Executor()
        ds = GenerationStore()
        dec = GenerationStore()
        ds.publish(0, Directive(1, 0, "old", 30.0, time.monotonic()))
        await reset_barrier(
            env, mp, ex, ds, dec, seed=1, episode_id="e", episode_generation=1
        )
        assert ds.latest() is None

    asyncio.run(_run())


def test_run_episode_telemetry_and_dispatch_parity(tmp_path):
    async def _run():
        env = MemoryEnvironment(max_steps=3)
        mp = MotionPerception()
        pol = DeterministicPolicy("advance", catalog_revision="memory-catalog-v1")
        ex = Executor()
        tel = TelemetryStore(tmp_path / "rows.jsonl")
        ds = GenerationStore()
        dec = GenerationStore()
        report = await run_episode(
            env,
            mp,
            pol,
            ex,
            tel,
            ds,
            dec,
            seed=3,
            episode_id="ep",
            episode_generation=1,
            config=ReactConfig(max_steps=3),
        )
        assert report["steps"] >= 1
        for row in tel.rows():
            if row.executed_action_id is not None:
                assert row.dispatched_action_id == row.executed_action_id

    asyncio.run(_run())


def test_to_verified_snapshot_empty_surface():
    from loop.adaptive.snapshot import StateSnapshotV2

    v2 = StateSnapshotV2(
        snapshot_id="s",
        schema_revision_id="r",
        episode_id="e",
        episode_generation=0,
        step=0,
        frame_digest="d",
        frame_history_digests=(),
        captured_at=0.0,
        observation_age_s=0.0,
        mode=None,
        fields={},
        entities=(),
        relations=(),
        recent_events=(),
        confidence=0.5,
    )
    v = to_verified_snapshot(v2)
    assert v.surface.action_ids == ()


def test_policy_timeout_releases_watchdog(tmp_path):
    async def _run():
        env = MemoryEnvironment(max_steps=2)
        mp = MotionPerception()

        class SlowPolicy(DeterministicPolicy):
            def score(self, snapshot, directive, candidates):
                time.sleep(0.35)
                return super().score(snapshot, directive, candidates)

        pol = SlowPolicy("wait", catalog_revision="memory-catalog-v1")
        ex = Executor()
        tel = TelemetryStore(tmp_path / "t.jsonl")
        ds = GenerationStore()
        dec = GenerationStore()
        report = await run_episode(
            env,
            mp,
            pol,
            ex,
            tel,
            ds,
            dec,
            seed=1,
            episode_id="e",
            episode_generation=1,
            config=ReactConfig(decision_deadline_s=0.05, max_steps=2),
        )
        assert report["watchdog_releases"] >= 1

    asyncio.run(_run())


def _forbidden_tokens_in_source(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return _BANNED.findall(text)


def test_adaptive_tree_has_no_game_vendor_tokens():
    hits: list[str] = []
    for path in sorted(ADAPTIVE_DIR.rglob("*.py")):
        for m in _forbidden_tokens_in_source(path):
            hits.append(f"{path.relative_to(REPO_ROOT)}:{m}")
    assert not hits


def test_run_episode_max_steps_sets_reason(tmp_path):
    async def _run():
        env = MemoryEnvironment(max_steps=50)
        report = await run_episode(
            env,
            MotionPerception(),
            DeterministicPolicy("advance", catalog_revision="memory-catalog-v1"),
            Executor(),
            TelemetryStore(tmp_path / "t.jsonl"),
            GenerationStore(),
            GenerationStore(),
            seed=1,
            episode_id="e",
            episode_generation=1,
            config=ReactConfig(max_steps=2),
        )
        assert report["steps"] == 2
        assert report["terminated"] is False
        assert report["truncated"] is True
        assert report["termination_reason"] == "max_steps"

    asyncio.run(_run())


def test_orchestrator_worker_keeps_directive(tmp_path):
    async def _run():
        env = MemoryEnvironment(max_steps=4)
        ds = GenerationStore()
        events: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()
        cfg = ReactConfig(max_steps=2)
        worker = asyncio.create_task(
            orchestrator_worker(
                events,
                ds,
                episode_generation=1,
                config=cfg,
                stop=stop,
            )
        )
        try:
            report = await run_episode(
                env,
                MotionPerception(),
                DeterministicPolicy("advance", catalog_revision="memory-catalog-v1"),
                Executor(),
                TelemetryStore(tmp_path / "t.jsonl"),
                ds,
                GenerationStore(),
                seed=2,
                episode_id="e",
                episode_generation=1,
                config=cfg,
                events=events,
            )
            assert ds.latest() is not None
            assert report["steps"] >= 1
        finally:
            stop.set()
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


def test_qualify_same_step_is_idempotent():
    async def _run():
        env = MemoryEnvironment()
        mp = MotionPerception()
        obs = await env.reset(1, "e", 1)
        a = mp.qualify(obs)
        b = mp.qualify(obs)
        assert a.snapshot_id == b.snapshot_id
        obs2 = await env.step("advance")
        c = mp.qualify(obs2)
        d = mp.qualify(obs2)
        assert c.snapshot_id == d.snapshot_id
        assert c.snapshot_id != a.snapshot_id

    asyncio.run(_run())


def test_run_episode_timeout_uses_noop(tmp_path):
    async def _run():
        env = MemoryEnvironment(action_ids=("noop", "advance"), max_steps=3)
        mp = MotionPerception()

        class SlowPolicy(DeterministicPolicy):
            def score(self, snapshot, directive, candidates):
                time.sleep(0.2)
                return super().score(snapshot, directive, candidates)

        pol = SlowPolicy("advance", catalog_revision="memory-catalog-v1")
        ex = Executor()
        tel = TelemetryStore(tmp_path / "t.jsonl")
        report = await run_episode(
            env,
            mp,
            pol,
            ex,
            tel,
            GenerationStore(),
            GenerationStore(),
            seed=1,
            episode_id="e",
            episode_generation=1,
            config=ReactConfig(decision_deadline_s=0.01, max_steps=2),
        )
        assert report["steps"] >= 1
        assert report["watchdog_releases"] >= 1
        assert any(row.executed_action_id == "noop" for row in tel.rows())

    asyncio.run(_run())


def test_executor_and_controller_ast_no_session_imports():
    banned = ("Session", "session.act", "loop.adaptive.session", "loop.os.mouse")
    for name in ("executor.py", "controller.py"):
        path = ADAPTIVE_DIR / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
        blob = ast.unparse(tree)
        for token in banned:
            assert token not in blob, f"{name} mentions {token}"
