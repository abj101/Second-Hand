"""RL GSM train-analyze-optimize (cap 3). --fake uses in-memory FakeGsmEnv (tests/sandbox)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from avatar.config import load_config
from avatar.eval.logger import new_run_dir
from avatar.eval.scoring import gsm_lift_table, write_markdown_table
from avatar.gsm.registry import GsmRegistry
from avatar.gsm.rl.env import FakeGsmEnv
from avatar.gsm.rl.loop import train_analyze_optimize
from avatar.gsm.sandbox import sandbox_reward
from avatar.schema import GsmSpec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a Tetris RL GSM with JSON rewards.")
    parser.add_argument("--state-source", default=None, choices=["extracted", "pixels"])
    parser.add_argument("--opt-iters", type=int, default=None)
    parser.add_argument("--episodes", type=int, default=8)
    parser.add_argument("--broken-reward", action="store_true")
    parser.add_argument(
        "--fake",
        action="store_true",
        help="tests-only FakeGsmEnv. Live training is python -m avatar.experiments.tetris_act --gsm --train.",
    )
    args = parser.parse_args(argv)
    cfg = load_config()
    gsm_cfg = cfg.get("gsm") or {}
    source = args.state_source or gsm_cfg.get("default_state_source") or "extracted"
    max_opt = args.opt_iters or int(gsm_cfg.get("max_opt_iters") or 3)
    spec = GsmSpec(
        name="place_piece",
        kind="rl",
        fixture="tetris",
        recognize="active tetromino needs placement before lock",
        end="piece locked or game over or timeout",
        enabled=False,
        state_source=source,  # type: ignore[arg-type]
        action_space=["shift_left", "shift_right", "rotate", "hard_drop"],
        reward_ref={"dodge_bonus": 100.0, "progress_scale": 0.0} if args.broken_reward else {},
    )
    if args.broken_reward:
        ok, reason = sandbox_reward(spec.reward_ref)
        print(f"sandbox_ok={ok} reason={reason}")
        return 0 if not ok else 1
    if not args.fake:
        print("Live RL train needs a window: python -m avatar.experiments.tetris_act --gsm --realtime --train")
        print("Pass --fake for the in-memory sandbox env.")
        return 2
    run_dir = new_run_dir("gsm_train", "tetris-rt")
    result = train_analyze_optimize(
        spec,
        env=FakeGsmEnv(state_source=source, max_steps=8),  # type: ignore[arg-type]
        max_opt_iters=max_opt,
        episodes=args.episodes,
        run_dir=run_dir,
    )
    print(
        f"opt_iters={result['opt_iters']} sandbox_ok={result['sandbox_ok']} "
        f"curves={run_dir / 'rl_curves.json'}"
    )
    rows = [
        {
            "harness_level": "GSM",
            "model": "dqn",
            "success": result["sandbox_ok"],
            "gsm": "rl",
            "state_source": source,
            "vlm_calls_per_episode": 0,
        }
    ]
    table = gsm_lift_table(rows)
    write_markdown_table(table, run_dir / "harness_lift.md")
    (run_dir / "train_summary.json").write_text(
        json.dumps(
            {
                "opt_iters": result["opt_iters"],
                "sandbox_ok": result["sandbox_ok"],
                "sandbox_reason": result["sandbox_reason"],
                "state_source": source,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _ = GsmRegistry.from_config(cfg, "tetris")
    return 0 if result["sandbox_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
