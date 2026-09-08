from __future__ import annotations

import json
from pathlib import Path

from loop.eval.grpo import gspo_config_or_reason, train_grpo


def test_train_grpo_refuses_live_window(tmp_path: Path) -> None:
    frames = tmp_path / "frozen_frames.jsonl"
    frames.write_text(
        json.dumps(
            {
                "obs": "vlm.jpg",
                "metrics": {"screen": "title", "aim_marks": "1@500,700:Continue"},
                "prompt_id": "abc",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = train_grpo(frames, live=True)
    assert report["ok"] is False
    assert "live" in str(report.get("reason") or "").lower()


def test_train_grpo_dry_run_scores_frozen_bank(tmp_path: Path) -> None:
    frames = tmp_path / "frozen_frames.jsonl"
    frames.write_text(
        json.dumps(
            {
                "obs": "vlm.jpg",
                "metrics": {
                    "screen": "title",
                    "aim_marks": "1@500,700:Continue 9@500,180:logo",
                },
                "prompt_id": "abc",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = train_grpo(frames, execute=False, live=False)
    assert report["ok"] is True
    assert report["executed"] is False
    assert report["frames"] == 1
    assert report["sample"]


def test_gspo_config_or_reason_fails_on_type_error() -> None:
    class FakeGRPOConfig:
        def __init__(self, **kwargs):
            if "loss_type" in kwargs or "importance_sampling_level" in kwargs:
                raise TypeError("unexpected keyword argument")

    config, reason = gspo_config_or_reason(FakeGRPOConfig, {"output_dir": "/tmp"})
    assert config is None
    assert reason is not None
    assert "GSPO kwargs unsupported" in reason
    assert "TypeError" in reason or "unexpected keyword" in reason


def test_reward_sandbox_inequalities_aligned() -> None:
    from loop.reward.frozen_score import sandbox_frozen_score
    from loop.reward.sts import sandbox_turn_reward

    ok_frozen, reason_frozen = sandbox_frozen_score()
    assert ok_frozen, reason_frozen
    ok_turn, reason_turn = sandbox_turn_reward()
    assert ok_turn, reason_turn
