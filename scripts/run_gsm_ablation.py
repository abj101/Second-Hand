"""G5: same RL task, extracted vs pixels state_source. Not a runtime rewrite."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from avatar.eval.logger import new_run_dir
from avatar.eval.scoring import gsm_lift_table, write_markdown_table
from avatar.gsm.rl.env import FakeGsmEnv
from avatar.gsm.rl.loop import train_analyze_optimize
from avatar.schema import GsmSpec


def _spec(state_source: str) -> GsmSpec:
    return GsmSpec(
        name="place_piece",
        kind="rl",
        fixture="tetris",
        recognize="active tetromino needs placement before lock",
        enabled=False,
        state_source=state_source,  # type: ignore[arg-type]
        action_space=["shift_left", "shift_right", "rotate", "hard_drop"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pixels vs extracted RL GSM ablation.")
    parser.add_argument("--episodes", type=int, default=6)
    args = parser.parse_args(argv)
    run_dir = new_run_dir("gsm_ablation", "tetris-rt")
    rows = []
    summary = {}
    for source in ("extracted", "pixels"):
        result = train_analyze_optimize(
            _spec(source),
            env=FakeGsmEnv(state_source=source, max_steps=8),  # type: ignore[arg-type]
            max_opt_iters=1,
            episodes=args.episodes,
            run_dir=run_dir / source,
        )
        summary[source] = {
            "sandbox_ok": result["sandbox_ok"],
            "opt_iters": result["opt_iters"],
            "mean_return": (
                sum(result["curves"][-1]) / len(result["curves"][-1]) if result["curves"] else 0.0
            ),
        }
        rows.append(
            {
                "harness_level": "GSM",
                "model": "dqn",
                "success": result["sandbox_ok"],
                "gsm": "rl",
                "state_source": source,
                "vlm_calls_per_episode": 0,
                "opt_iter": result["opt_iters"],
            }
        )
        print(f"{source}: sandbox_ok={result['sandbox_ok']} opt={result['opt_iters']}")
    write_markdown_table(gsm_lift_table(rows), run_dir / "harness_lift.md")
    (run_dir / "ablation.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
