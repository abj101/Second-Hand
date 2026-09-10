"""Summarize cold vs threaded STS2 collect runs from existing jsonl."""

from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from loop.log.schema import EpisodeLog
from loop.log.store import EpisodeStore

_CAVEAT = "same-model visible-state continuity, not opaque reasoning-state parity"
_MIXED_CAVEAT = (
    "unmatched starts; STS2 has no snapshot restore. "
    "same-model Ollama-visible state, not opaque reasoning-state parity"
)


def _episode_mode(episode: EpisodeLog, steps: list[dict[str, Any]]) -> str:
    for flag in episode.audit_flags:
        if flag in {"context:cold", "context:threaded"}:
            return flag.split(":", 1)[1]
    seen = [
        str((row.get("metrics") or {}).get("context_mode") or "")
        for row in steps
        if row.get("episode_id") == episode.episode_id
    ]
    if "threaded" in seen:
        return "threaded"
    return "cold"


def _stage_blob(row: dict[str, Any], name: str) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    stage = metrics.get(name)
    if isinstance(stage, dict):
        return stage
    if name == "act":
        call = row.get("reason_call")
        return call if isinstance(call, dict) else {}
    return {}


def _stage_stats(steps: list[dict[str, Any]], *, called_key: str, stage: str) -> dict[str, Any]:
    calls = 0
    prompt_tokens = 0
    output_tokens = 0
    latencies: list[float] = []
    utils: list[float] = []
    for row in steps:
        if row.get(called_key):
            calls += 1
        blob = _stage_blob(row, stage)
        prompt = blob.get("prompt_tokens")
        output = blob.get("output_tokens")
        latency = blob.get("latency_ms")
        utilization = blob.get("utilization")
        if isinstance(prompt, int):
            prompt_tokens += prompt
        if isinstance(output, int):
            output_tokens += output
        if isinstance(latency, (int, float)):
            latencies.append(float(latency))
        if isinstance(utilization, (int, float)):
            utils.append(float(utilization))
    return {
        "calls": calls,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "median_latency_ms": median(latencies) if latencies else None,
        "max_utilization": max(utils) if utils else None,
        "utilization_warnings": _util_warnings(utils),
    }


def _util_warnings(utils: list[float]) -> list[str]:
    flags: list[str] = []
    if any(value >= 0.75 for value in utils):
        flags.append("75%")
    if any(value >= 0.90 for value in utils):
        flags.append("90%")
    return flags


def summarize_context_run(run_dir: Path, *, mode: str | None = None) -> dict[str, Any]:
    store = EpisodeStore(run_dir)
    episodes = [EpisodeLog.model_validate(row) for row in store.read_episodes()]
    steps = store.read_steps()
    if mode is not None:
        episodes = [ep for ep in episodes if _episode_mode(ep, steps) == mode]
        ids = {ep.episode_id for ep in episodes}
        steps = [row for row in steps if row.get("episode_id") in ids]
    conclusive = [ep for ep in episodes if ep.outcome != "inconclusive"]
    n = len(conclusive)
    wins = sum(1 for ep in conclusive if ep.success)
    returns = [float(ep.return_value) for ep in conclusive]
    llm1 = _stage_stats(steps, called_key="llm1_called_this_turn", stage="llm1")
    act = _stage_stats(steps, called_key="reasoner_called_this_turn", stage="act")
    latencies: list[float] = []
    softlocks = 0
    false_by_episode: dict[str, int] = {}
    noops = 0
    context_mode = mode or "cold"
    utils: list[float] = []
    for row in steps:
        latency = row.get("obs_to_action_ms")
        if isinstance(latency, (int, float)):
            latencies.append(float(latency))
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        if mode is None and metrics.get("context_mode") == "threaded":
            context_mode = "threaded"
        episode_id = str(row.get("episode_id") or "")
        if isinstance(metrics.get("false_card_transactions"), int):
            false_by_episode[episode_id] = int(metrics["false_card_transactions"])
        for name in ("llm1", "act"):
            utilization = _stage_blob(row, name).get("utilization")
            if isinstance(utilization, (int, float)):
                utils.append(float(utilization))
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        if result.get("error") == "decision-softlock":
            softlocks += 1
        if result.get("pixel_changed") is False:
            noops += 1
    return {
        "run_dir": str(run_dir),
        "context_mode": context_mode,
        "episodes": len(episodes),
        "conclusive": n,
        "wins": wins,
        "win_rate": (wins / n) if n else None,
        "mean_return": (sum(returns) / n) if n else None,
        "llm_calls": int(llm1["calls"]) + int(act["calls"]),
        "prompt_tokens": int(llm1["prompt_tokens"]) + int(act["prompt_tokens"]),
        "output_tokens": int(llm1["output_tokens"]) + int(act["output_tokens"]),
        "median_obs_to_action_ms": median(latencies) if latencies else None,
        "llm1": llm1,
        "act": act,
        "decision_softlocks": softlocks,
        "false_card_transactions": sum(false_by_episode.values()),
        "noop_steps": noops,
        "utilization_warnings": _util_warnings(utils),
        "exploratory": n < 20,
    }


def _delta(left: dict[str, Any], right: dict[str, Any], key: str) -> float | None:
    a, b = left.get(key), right.get(key)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(b) - float(a)
    return None


def compare_context_runs(cold_dir: Path, threaded_dir: Path) -> dict[str, Any]:
    cold = summarize_context_run(run_dir=cold_dir)
    threaded = summarize_context_run(run_dir=threaded_dir)
    return _comparison(cold, threaded, caveat=_CAVEAT)


def summarize_mixed_run(run_dir: Path) -> dict[str, Any]:
    cold = summarize_context_run(run_dir, mode="cold")
    threaded = summarize_context_run(run_dir, mode="threaded")
    return _comparison(cold, threaded, caveat=_MIXED_CAVEAT)


def _comparison(cold: dict[str, Any], threaded: dict[str, Any], *, caveat: str) -> dict[str, Any]:
    def stage_delta(stage: str, key: str) -> float | None:
        left = cold.get(stage) if isinstance(cold.get(stage), dict) else {}
        right = threaded.get(stage) if isinstance(threaded.get(stage), dict) else {}
        return _delta(left, right, key)

    return {
        "cold": cold,
        "threaded": threaded,
        "delta": {
            "win_rate": _delta(cold, threaded, "win_rate"),
            "mean_return": _delta(cold, threaded, "mean_return"),
            "llm_calls": _delta(cold, threaded, "llm_calls"),
            "prompt_tokens": _delta(cold, threaded, "prompt_tokens"),
            "output_tokens": _delta(cold, threaded, "output_tokens"),
            "median_obs_to_action_ms": _delta(cold, threaded, "median_obs_to_action_ms"),
            "decision_softlocks": _delta(cold, threaded, "decision_softlocks"),
            "false_card_transactions": _delta(cold, threaded, "false_card_transactions"),
            "noop_steps": _delta(cold, threaded, "noop_steps"),
            "llm1_prompt_tokens": stage_delta("llm1", "prompt_tokens"),
            "act_prompt_tokens": stage_delta("act", "prompt_tokens"),
            "llm1_median_latency_ms": stage_delta("llm1", "median_latency_ms"),
            "act_median_latency_ms": stage_delta("act", "median_latency_ms"),
        },
        "exploratory": bool(cold.get("exploratory") or threaded.get("exploratory")),
        "caveat": caveat,
    }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) == 2:
        print(json.dumps(summarize_mixed_run(Path(sys.argv[1])), indent=2))
    elif len(sys.argv) == 3:
        print(json.dumps(compare_context_runs(Path(sys.argv[1]), Path(sys.argv[2])), indent=2))
    else:
        raise SystemExit(
            "usage: python -m loop.eval.context_ablation RUN_DIR | COLD_RUN_DIR THREADED_RUN_DIR"
        )
