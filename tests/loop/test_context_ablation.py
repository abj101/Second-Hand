from __future__ import annotations

from pathlib import Path

from loop.eval.context_ablation import (
    compare_context_runs,
    summarize_context_run,
    summarize_mixed_run,
)
from loop.log.schema import EpisodeLog, Observation, StepLog, StepResult
from loop.log.store import EpisodeStore


def _write_run(
    path: Path,
    *,
    mode: str,
    success: bool,
    tokens: int,
    noop: bool,
    episode_id: str = "ep1",
    llm1_tokens: int = 0,
    utilization: float | None = None,
) -> None:
    store = EpisodeStore(path)
    store.write_episode(
        EpisodeLog(
            session_id="s",
            episode_id=episode_id,
            fixture="sts2",
            steps=1,
            return_value=1.0 if success else 0.0,
            terminal="win" if success else "fail",
            audit_flags=[f"context:{mode}"],
        )
    )
    llm1 = {
        "prompt_tokens": llm1_tokens,
        "output_tokens": 4,
        "latency_ms": 20.0,
        "num_ctx": 8192,
        "utilization": utilization,
    }
    act = {
        "prompt_tokens": tokens,
        "output_tokens": 10,
        "latency_ms": 8.0,
        "num_ctx": 2048,
        "utilization": None,
    }
    store.write_step(
        StepLog(
            session_id="s",
            episode_id=episode_id,
            step_id=1,
            ts="2026-09-09T00:00:00Z",
            fixture="sts2",
            observation=Observation(screenshot_ref="x.jpg"),
            metrics={
                "context_mode": mode,
                "false_card_transactions": 0 if success else 2,
                "llm1": llm1,
                "act": act,
            },
            obs_to_action_ms=100.0 if mode == "cold" else 80.0,
            reason_call={"prompt_tokens": tokens, "output_tokens": 10},
            llm1_called_this_turn=True,
            reasoner_called_this_turn=True,
            result=StepResult(pixel_changed=not noop),
        )
    )


def test_context_ablation_compare_reports_deltas(tmp_path: Path) -> None:
    cold = tmp_path / "cold"
    threaded = tmp_path / "threaded"
    _write_run(cold, mode="cold", success=False, tokens=200, noop=True)
    _write_run(threaded, mode="threaded", success=True, tokens=120, noop=False)
    report = compare_context_runs(cold, threaded)
    assert summarize_context_run(cold)["win_rate"] == 0.0
    assert report["threaded"]["win_rate"] == 1.0
    assert report["delta"]["win_rate"] == 1.0
    assert report["delta"]["prompt_tokens"] == -80
    assert report["delta"]["llm_calls"] == 0
    assert report["delta"]["noop_steps"] == -1
    assert "opaque" in report["caveat"]
    assert report["cold"]["exploratory"] is True
    assert report["cold"]["act"]["prompt_tokens"] == 200
    assert report["cold"]["llm1"]["calls"] == 1


def test_mixed_run_splits_arms_and_warns_on_utilization(tmp_path: Path) -> None:
    run = tmp_path / "mixed"
    _write_run(
        run, mode="cold", success=False, tokens=200, noop=True,
        episode_id="c1", llm1_tokens=100, utilization=0.91,
    )
    _write_run(
        run, mode="threaded", success=True, tokens=120, noop=False,
        episode_id="t1", llm1_tokens=80, utilization=0.2,
    )
    report = summarize_mixed_run(run)
    assert report["cold"]["episodes"] == 1
    assert report["threaded"]["episodes"] == 1
    assert report["delta"]["win_rate"] == 1.0
    assert report["delta"]["prompt_tokens"] == -100
    assert report["delta"]["llm1_prompt_tokens"] == -20
    assert report["cold"]["utilization_warnings"] == ["75%", "90%"]
    assert report["threaded"]["utilization_warnings"] == []
    assert report["exploratory"] is True
    assert "unmatched starts" in report["caveat"]
    assert "Ollama-visible" in report["caveat"]


def test_mixed_run_reports_incomplete_arm(tmp_path: Path) -> None:
    run = tmp_path / "partial"
    _write_run(run, mode="cold", success=True, tokens=50, noop=False, episode_id="c1")
    report = summarize_mixed_run(run)
    assert report["cold"]["episodes"] == 1
    assert report["threaded"]["episodes"] == 0
    assert report["threaded"]["conclusive"] == 0
    assert report["delta"]["win_rate"] is None
    assert report["exploratory"] is True
