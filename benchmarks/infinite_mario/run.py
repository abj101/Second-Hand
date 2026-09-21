"""CLI report runner: deterministic/random baselines via adaptive react loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from pathlib import Path

from loop.adaptive.controller import (
    GenerationStore,
    ReactConfig,
    orchestrator_worker,
    run_episode,
)
from loop.adaptive.executor import Executor
from loop.adaptive.policy import DeterministicPolicy, RandomPolicy
from loop.adaptive.telemetry import TelemetryStore, percentile

from benchmarks.infinite_mario.adapter import MarioEnvironment
from benchmarks.infinite_mario.perceive import MarioPerception

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REVISION = "2d7a0280841e3244a299c9c640efc8cefc46d476"


def _cache_dir(repo_root: Path) -> Path:
    return repo_root / ".cache" / "benchmarks" / "playjev" / DEFAULT_REVISION


def _load_seeds(repo_root: Path, split: str) -> list[int]:
    if split == "test" and os.environ.get("AVATAR_ALLOW_TEST_SEEDS") != "1":
        raise SystemExit(
            "refusing test split without AVATAR_ALLOW_TEST_SEEDS=1 "
            "(test seeds are reserved for final evaluation)"
        )
    path = repo_root / "benchmarks" / "infinite_mario" / "seed-manifests" / f"{split}.json"
    if not path.is_file():
        raise SystemExit(f"missing seed manifest: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["seeds"])


async def _run_one(
    env: MarioEnvironment,
    *,
    seed: int,
    policy_name: str,
    max_steps: int,
    workspace: Path,
) -> dict:
    perception = MarioPerception()
    executor = Executor()
    telemetry = TelemetryStore(workspace / "telemetry.jsonl", workspace=workspace)
    directive_store = GenerationStore()
    decision_store = GenerationStore()
    events: asyncio.Queue = asyncio.Queue()
    stop = asyncio.Event()
    episode_id = uuid.uuid4().hex
    episode_generation = 1
    cfg = ReactConfig(max_steps=max_steps)

    if policy_name == "random":
        policy = RandomPolicy(random.Random(seed), catalog_revision=env.actions().revision)
    else:
        policy = DeterministicPolicy(
            preferred_id="right run jump",
            catalog_revision=env.actions().revision,
        )

    worker = asyncio.create_task(
        orchestrator_worker(
            events,
            directive_store,
            episode_generation=episode_generation,
            config=cfg,
            stop=stop,
        )
    )
    try:
        summary = await run_episode(
            env,
            perception,
            policy,
            executor,
            telemetry,
            directive_store,
            decision_store,
            seed=seed,
            episode_id=episode_id,
            episode_generation=episode_generation,
            config=cfg,
            events=events,
        )
    finally:
        stop.set()
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass

    rows = telemetry.rows()
    e2e = [r.latency_e2e_s for r in rows]
    return {
        "seed": seed,
        "policy": policy_name,
        "score": summary.get("score"),
        "termination_reason": summary.get("termination_reason"),
        "episode_length": summary.get("steps"),
        "virtual_ticks": summary.get("ticks"),
        "watchdog_releases": summary.get("watchdog_releases"),
        "invalidation_failures": summary.get("invalidation_failures"),
        "latency_perception_p50": summary.get("latency_perception_p50"),
        "latency_perception_p95": summary.get("latency_perception_p95"),
        "latency_policy_p50": summary.get("latency_policy_p50"),
        "latency_policy_p95": summary.get("latency_policy_p95"),
        "latency_e2e_p50": summary.get("latency_e2e_p50", percentile(e2e, 50)),
        "latency_e2e_p95": summary.get("latency_e2e_p95", percentile(e2e, 95)),
        "environment_revision": summary.get("environment_revision") or "infinite-mario-v1",
        "model_choice": None,
        "vram_mb": None,
    }


async def run_report(
    *,
    repo_root: Path,
    split: str,
    policy_name: str,
    max_steps: int,
    workspace: Path,
    seed_override: int | None = None,
    max_episodes: int = 1,
) -> dict:
    cache = _cache_dir(repo_root)
    if not cache.is_dir():
        raise FileNotFoundError(
            f"PlayJev cache missing at {cache}. Run: python -m loop fetch-playjev"
        )

    seeds = _load_seeds(repo_root, split)
    if seed_override is not None:
        seeds = [seed_override]
    seeds = seeds[:max_episodes]

    env = MarioEnvironment(cache, headless=True)
    report: dict = {
        "fingerprint": {},
        "split": split,
        "model_choice": None,
        "vram_mb": None,
        "episodes": [],
    }
    try:
        await env.start()
        report["fingerprint"] = env.fingerprint()
        if policy_name == "baselines":
            for seed in seeds:
                det = await _run_one(
                    env, seed=seed, policy_name="deterministic", max_steps=max_steps, workspace=workspace
                )
                rnd = await _run_one(
                    env, seed=seed, policy_name="random", max_steps=max_steps, workspace=workspace
                )
                report["episodes"].append({"seed": seed, "deterministic": det, "random": rnd})
        else:
            for seed in seeds:
                ep = await _run_one(
                    env,
                    seed=seed,
                    policy_name=policy_name,
                    max_steps=max_steps,
                    workspace=workspace,
                )
                report["episodes"].append(ep)
    finally:
        await env.close()

    if report["episodes"]:
        first = report["episodes"][0]
        if policy_name == "baselines":
            det = first["deterministic"]
            report["score"] = det.get("score")
            report["termination_reason"] = det.get("termination_reason")
            report["episode_length"] = det.get("episode_length")
            report["virtual_ticks"] = det.get("virtual_ticks")
            report["watchdog_releases"] = det.get("watchdog_releases")
            report["invalidation_failures"] = det.get("invalidation_failures")
            report["latency_perception_p50"] = det.get("latency_perception_p50")
            report["latency_perception_p95"] = det.get("latency_perception_p95")
            report["latency_policy_p50"] = det.get("latency_policy_p50")
            report["latency_policy_p95"] = det.get("latency_policy_p95")
            report["latency_e2e_p50"] = det.get("latency_e2e_p50")
            report["latency_e2e_p95"] = det.get("latency_e2e_p95")
        else:
            report["score"] = first.get("score")
            report["termination_reason"] = first.get("termination_reason")
            report["episode_length"] = first.get("episode_length")
            report["virtual_ticks"] = first.get("virtual_ticks")
            report["watchdog_releases"] = first.get("watchdog_releases")
            report["invalidation_failures"] = first.get("invalidation_failures")
            report["latency_perception_p50"] = first.get("latency_perception_p50")
            report["latency_perception_p95"] = first.get("latency_perception_p95")
            report["latency_policy_p50"] = first.get("latency_policy_p50")
            report["latency_policy_p95"] = first.get("latency_policy_p95")
            report["latency_e2e_p50"] = first.get("latency_e2e_p50")
            report["latency_e2e_p95"] = first.get("latency_e2e_p95")
        report["policy"] = policy_name
        report["environment_fingerprint"] = report["fingerprint"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Infinite Mario react baseline report")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--split", default="train", choices=("train", "val", "test"))
    parser.add_argument(
        "--policy",
        default="deterministic",
        choices=("deterministic", "random", "baselines"),
    )
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=None, help="override manifest seed")
    args = parser.parse_args(argv)

    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    repo_root = REPO_ROOT

    report = asyncio.run(
        run_report(
            repo_root=repo_root,
            split=args.split,
            policy_name=args.policy,
            max_steps=args.max_steps,
            workspace=workspace,
            seed_override=args.seed,
        )
    )
    out = workspace / "report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
