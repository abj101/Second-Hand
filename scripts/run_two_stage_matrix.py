"""Run the concise frozen STS2 LLM1 × LLM2 selection matrix."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loop.config import DEFAULT_CLOUD_MODEL, project_root
from loop.eval.two_stage_matrix import MatrixCase, default_cases, run_matrix, write_report

# The local 4B must use the project's instruct alias; the bare tag is
# thinking-only with ``think: false`` in this JSON benchmark.
DEFAULT_VISION = [DEFAULT_CLOUD_MODEL]
DEFAULT_REASONERS = ["qwen3.5:9b-q4_K_M"]
QWEN35_INSTRUCT = "qwen3.5:4b-instruct-q4_K_M"


def _installed_tags(ollama: str) -> set[str]:
    result = subprocess.run([ollama, "list"], capture_output=True, text=True, check=False)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "ollama list failed; is the Ollama service running?")
    return {line.split()[0] for line in result.stdout.splitlines()[1:] if line.split()}


def _pull(models: list[str]) -> None:
    ollama = shutil.which("ollama")
    if not ollama:
        raise SystemExit("ollama is not on PATH. Install it from https://ollama.com/download")
    installed = _installed_tags(ollama)
    for model in models:
        if model.endswith("cloud") or model in installed:
            continue
        if model == QWEN35_INSTRUCT:
            # This is a local Modelfile alias, not an Ollama library tag.
            setup = Path(__file__).resolve().parent / "pull_local_vlms.py"
            subprocess.run([sys.executable, str(setup), "--model", model], check=True)
        else:
            subprocess.run([ollama, "pull", model], check=True)
        installed = _installed_tags(ollama)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ungraded frozen-screen LLM1 × reasoner trace collection")
    parser.add_argument("--vision", action="append", default=None, help="LLM1 tag; repeat to override defaults")
    parser.add_argument("--reasoner", action="append", default=None, help="LLM2 tag; repeat to override defaults")
    parser.add_argument("--frame", action="append", default=None, help="fixture filename; repeat to select frames")
    parser.add_argument("--limit", type=int, default=3, help="maximum discovered frames (default: 3)")
    parser.add_argument("--vision-num-ctx", type=int, default=8192, help="LLM1 context; 8192 admits Qwen3-VL")
    parser.add_argument("--reason-num-ctx", type=int, default=4096, help="LLM2 context")
    parser.add_argument("--pull-missing", action="store_true", help="run ollama pull for local tags before testing")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    vision = args.vision or DEFAULT_VISION
    reasoners = args.reasoner or DEFAULT_REASONERS
    if args.pull_missing:
        _pull(list(dict.fromkeys([*vision, *reasoners])))
    dest = args.output or (project_root() / "runs" / f"{datetime.now():%Y%m%d_%H%M%S}_two_stage_matrix.json")
    cases = (
        [MatrixCase(file=str(Path(name))) for name in args.frame]
        if args.frame
        else default_cases(limit=args.limit)
    )
    report = run_matrix(
        vision_models=vision,
        reasoner_models=reasoners,
        cases=cases,
        vision_num_ctx=args.vision_num_ctx,
        reason_num_ctx=args.reason_num_ctx,
    )
    write_report(report, dest)
    print(
        json.dumps(
            {"records": len(report["records"]), "cases": report["cases"], "report": str(dest)},
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
