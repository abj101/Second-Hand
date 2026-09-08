"""Legacy Phase 1 runbook: preflight Ollama, then See → Understand → Act on 2048."""

from __future__ import annotations

import argparse
import subprocess
import sys

from avatar.config import load_config, project_root
from avatar.experiments.act import main as act_main
from avatar.experiments.see import main as see_main
from avatar.experiments.understand import main as understand_main
from avatar.models import configured_models

STAGES = ("see", "understand", "act")


def preflight(models: list[str] | None) -> int:
    root = project_root()
    cmd = [sys.executable, str(root / "scripts" / "pull_local_vlms.py"), "--check"]
    if models:
        for model_id in models:
            cmd.extend(["--model", model_id])
    print("=== Preflight: Ollama + model weights ===")
    proc = subprocess.run(cmd, cwd=root)
    return proc.returncode


def _experiment_argv(args: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    if args.headed:
        argv.append("--headed")
    if args.skip_models:
        argv.append("--skip-models")
    if args.model:
        for model_id in args.model:
            argv.extend(["--model", model_id])
    return argv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase 1 runbook: preflight, then See → Understand → Act on 2048",
    )
    parser.add_argument("--headed", action="store_true", help="visible Chromium during capture")
    parser.add_argument("--skip-models", action="store_true", help="capture/logging only; no VLM calls")
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        metavar="ID",
        help="only this configured model (repeatable; also honors AVATAR_MODEL)",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=STAGES,
        dest="stages",
        help="run only these stages (default: all)",
    )
    parser.add_argument("--preflight-only", action="store_true", help="check Ollama/models and exit")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--level", choices=["L1", "L2"], default="L1", help="Act harness level")
    parser.add_argument("--n", type=int, default=None, help="Act micro-task repeats (default: config n_micro)")
    args = parser.parse_args(argv)

    cfg = load_config()
    stages = args.stages or list(STAGES)
    models = configured_models(cfg, only=args.model)
    print("Project root:", project_root())
    print("Stages:", ", ".join(stages))
    if args.skip_models:
        print("VLM calls: skipped")
    elif models:
        print("Models:", [m for m, _ in models])
    else:
        print("Models: none selected (use --model or AVATAR_MODEL)")

    if not args.skip_preflight:
        rc = preflight(args.model)
        if rc != 0:
            print("Preflight failed. Run: python scripts/pull_local_vlms.py")
            return rc

    if args.preflight_only:
        return 0

    exp_argv = _experiment_argv(args)

    if "see" in stages:
        print("\n=== See (L0) ===")
        rc = see_main(exp_argv)
        if rc != 0:
            return rc

    if "understand" in stages:
        print("\n=== Understand (L0) ===")
        rc = understand_main(exp_argv)
        if rc != 0:
            return rc

    if "act" in stages:
        print(f"\n=== Act ({args.level}) ===")
        act_argv = [*exp_argv, "--level", args.level]
        if args.n is not None:
            act_argv.extend(["--n", str(args.n)])
        rc = act_main(act_argv)
        if rc != 0:
            return rc

    print("\n=== Phase 1 runbook complete ===")
    print("Runs logged under runs/")
    if not args.skip_models:
        print("Then: run_commercial.py, run_analyze.py, run_tetris.py as needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
