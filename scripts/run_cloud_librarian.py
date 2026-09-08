"""Frozen-cloud librarian: collect + curate skills, never train weights.

Same VLM for collect chords and skill synth. Skip export-sft / train-sft / train-rl.
Number is analyze p_on − p_off after a probe collect on the skill bank.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "gemma4:31b-cloud"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run(argv: list[str]) -> int:
    cmd = [sys.executable, "-u", "-m", "loop", *argv]
    print(f"\n=== {' '.join(cmd)} ===", flush=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env)
    print(f"exit={proc.returncode}", flush=True)
    return int(proc.returncode)


def _ollama_has(model: str) -> bool:
    bin_path = shutil.which("ollama")
    if not bin_path:
        return False
    proc = subprocess.run([bin_path, "list"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return False
    tags = {line.split()[0] for line in proc.stdout.splitlines()[1:] if line.split()}
    return model in tags or f"{model}:latest" in tags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloud VLM librarian experiment (no weight training)")
    parser.add_argument("--fixture", default="elden", choices=("elden", "fake"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--baseline-lives", type=int, default=10)
    parser.add_argument("--author-lives", type=int, default=10)
    parser.add_argument("--probe-lives", type=int, default=20)
    parser.add_argument("--max-steps", type=int, default=32)
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--skill-off-frac", type=float, default=0.5)
    parser.add_argument("--countdown", type=int, default=5)
    parser.add_argument("--skip-perceive", action="store_true")
    parser.add_argument("--no-dino", action="store_true")
    args = parser.parse_args(argv)

    dest = ROOT / "runs" / f"{_stamp()}_{args.fixture}_cloud_librarian"
    dest.mkdir(parents=True, exist_ok=True)
    log_path = dest / "experiment.log"
    report: dict = {
        "fixture": args.fixture,
        "model": args.model,
        "train_weights": False,
        "baseline_lives": args.baseline_lives,
        "author_lives": args.author_lives,
        "probe_lives": args.probe_lives,
        "steps": [],
        "ok": False,
        "log": str(log_path),
    }
    print(f"report_dir={dest}", flush=True)
    log_path.write_text(json.dumps({k: v for k, v in report.items() if k != "steps"}, indent=2) + "\n", encoding="utf-8")
    (dest / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not _ollama_has(args.model):
        print(f"missing Ollama tag {args.model!r} — pull or ollama signin first", file=sys.stderr)
        report["reason"] = "model missing"
        (dest / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return 2

    common = ["--fixture", args.fixture, "--model", args.model]
    collect_tail = [
        "--max-steps",
        str(args.max_steps),
        "--timeout-s",
        str(args.timeout_s),
        "--countdown",
        str(args.countdown),
        "--no-learn",
        "--skill-off-frac",
        str(args.skill_off_frac),
    ]
    if args.no_dino:
        collect_tail.append("--no-dino")

    def fail(reason: str, code: int) -> int:
        report["reason"] = reason
        (dest / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return code

    def step(name: str, argv: list[str]) -> int:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {name} {' '.join(argv)} ===\n")
        code = _run(argv)
        report["steps"].append({"name": name, "argv": argv, "exit": code})
        (dest / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return code

    if not args.skip_perceive:
        code = step("perceive", ["perceive", *common])
        if code != 0:
            return fail("perceive failed", code)

    if args.baseline_lives > 0:
        code = step(
            "collect_no_skill",
            ["collect", *common, "--arm", "no_skill", "--lives", str(args.baseline_lives), *collect_tail],
        )
        if code != 0:
            return fail("baseline collect failed", code)

    if args.author_lives > 0:
        code = step(
            "collect_ratchet_author",
            ["collect", *common, "--arm", "ratchet", "--lives", str(args.author_lives), *collect_tail],
        )
        if code != 0:
            return fail("author collect failed", code)

    code = step("curate_preview", ["curate", *common, "--preview"])
    if code != 0:
        return fail("curate preview failed", code)

    code = step("curate_commit", ["curate", *common])
    if code != 0:
        return fail("curate commit failed", code)

    if args.probe_lives > 0:
        code = step(
            "collect_ratchet_probe",
            ["collect", *common, "--arm", "ratchet", "--lives", str(args.probe_lives), *collect_tail],
        )
        if code != 0:
            return fail("probe collect failed", code)

    code = step("analyze", ["analyze", "--fixture", args.fixture])
    if code != 0:
        return fail("analyze failed", code)

    step("status", ["status", "--fixture", args.fixture])
    report["ok"] = True
    report["reason"] = "ok"
    (dest / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
