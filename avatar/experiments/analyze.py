from __future__ import annotations

import argparse
import json
from pathlib import Path

from avatar.config import load_config
from avatar.constructor.skills import SkillLibrary
from avatar.eval.logger import iter_jsonl, new_run_dir
from avatar.eval.reflect import reflect_session
from avatar.eval.scoring import harness_lift_table, plot_attempt_success, write_markdown_table
from avatar.experiments.common import add_model_flags, selected_models
from avatar.models import make_gateway
from avatar.prompts.library import load_manual


def _summarize(jsonl: Path, limit: int = 40) -> str:
    lines = []
    for i, row in enumerate(iter_jsonl(jsonl)):
        if i >= limit:
            break
        action = row.get("action") or {}
        result = row.get("result") or {}
        lines.append(
            f"{row.get('stage')} {row.get('harness_level')} model={row.get('model')} "
            f"goal={action.get('goal') or action.get('next_move')} "
            f"moved={result.get('moved')} verdict-related={result}"
        )
    return "\n".join(lines) or "empty trace"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze experiment: L4 reflection + manual boost + plots")
    parser.add_argument("--traces", type=Path, required=True, help="turns.jsonl from an Act/commercial run")
    parser.add_argument("--skills", type=Path, default=None)
    parser.add_argument("--manual", action="store_true", help="run the late manual-boost prompt mode")
    add_model_flags(parser)
    args = parser.parse_args(argv)

    cfg = load_config()
    run_dir = new_run_dir("analyze", "l4")
    print(f"Analyze run dir: {run_dir}", flush=True)
    print(f"Traces: {args.traces}", flush=True)

    rows = list(iter_jsonl(args.traces))
    score_rows = []
    for row in rows:
        if "success" in (row.get("result") or {}) or "moved" in (row.get("result") or {}):
            score_rows.append(
                {
                    "harness_level": row.get("harness_level"),
                    "model": row.get("model"),
                    "attempt": (row.get("result") or {}).get("attempt") or 1,
                    "success": (row.get("result") or {}).get("moved")
                    or (row.get("result") or {}).get("success"),
                }
            )
    table = harness_lift_table(score_rows)
    write_markdown_table(table, run_dir / "harness_lift.md")
    plot_attempt_success(score_rows, run_dir / "attempt_success.png")
    print(f"Wrote {run_dir / 'harness_lift.md'} and attempt_success.png", flush=True)

    if args.skills and args.skills.exists():
        stats = SkillLibrary.load(args.skills).stats()
        (run_dir / "skill_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")

    prompt_mode = "manual" if args.manual else "pixels"
    manual = load_manual("2048") if args.manual else ""
    summary = _summarize(args.traces)
    (run_dir / "trace_summary.txt").write_text(summary, encoding="utf-8")

    reports = []
    if not args.skip_models:
        model_rows = selected_models(cfg, args)
        print(f"Models: {[model for model, _ in model_rows]}", flush=True)
        for model_id, provider in model_rows:
            print(f"Reflecting with {model_id}...", flush=True)
            gateway = make_gateway(cfg, model_id, provider)
            try:
                parsed, call = reflect_session(
                    gateway,
                    fixture="2048",
                    trace_summary=summary,
                    prompt_mode=prompt_mode,
                    manual=manual,
                )
            finally:
                gateway.unload()
            reports.append(
                {
                    "model": model_id,
                    "latency_ms": call.latency_ms,
                    "cost_usd": call.cost_usd,
                    "reflection": parsed.model_dump(),
                }
            )
            print(
                f"  {model_id}: {parsed.verdict} ({call.latency_ms:.0f}ms)",
                flush=True,
            )
    (run_dir / "qa_reports.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"Analyze experiment written to {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
