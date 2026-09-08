"""Score See experiment turns.jsonl against backend oracle (automation helper)."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from avatar.config import project_root
from avatar.eval.logger import iter_jsonl


def norm_cell(v: object) -> int:
    if v is None or v == 0:
        return 0
    return int(v)  # type: ignore[arg-type]


def score_board(pred: list | None, oracle_grid: list) -> int:
    if not pred or not isinstance(pred, list) or len(pred) != 4:
        return 0
    correct = 0
    for r in range(4):
        row = pred[r] if r < len(pred) else []
        for c in range(4):
            pv = norm_cell(row[c] if c < len(row) else None)
            ov = norm_cell(oracle_grid[r][c])
            if pv == ov:
                correct += 1
    return correct


def status_ok(pred_status: str | None, oracle: dict) -> bool:
    over = oracle.get("over", False)
    ps = (pred_status or "").lower()
    if over:
        return "gameover" in ps or ps == "game over"
    return ps not in ("gameover", "game over", "unparseable")


def score_run(run_dir: Path) -> list[dict]:
    rows: list[dict] = []
    jsonl = run_dir / "turns.jsonl"
    if not jsonl.exists() or jsonl.stat().st_size == 0:
        return rows
    for row in iter_jsonl(jsonl):
        action = row.get("action") or {}
        oracle = (row.get("result") or {}).get("oracle") or {}
        cap = row.get("capture_settings") or {}
        stem = Path(str(row.get("screenshot_ref", ""))).stem
        state = stem.split("_")[0]
        rows.append(
            {
                "run": run_dir.name,
                "model": row.get("model"),
                "state": state,
                "ls": cap.get("longest_side"),
                "q": cap.get("jpeg_quality"),
                "tiles": score_board(action.get("board"), oracle.get("grid", [])),
                "score_ok": action.get("score") == oracle.get("score"),
                "status_ok": status_ok(action.get("status"), oracle),
                "latency_ms": row.get("latency_ms"),
                "bytes": cap.get("bytes"),
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict[str, dict[str, float]]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_model[str(row["model"])].append(row)
    out: dict[str, dict[str, float]] = {}
    for model, model_rows in sorted(by_model.items()):
        n = len(model_rows)
        out[model] = {
            "n": float(n),
            "tiles_avg": sum(r["tiles"] for r in model_rows) / n,
            "score_pct": sum(r["score_ok"] for r in model_rows) / n * 100,
            "status_pct": sum(r["status_ok"] for r in model_rows) / n * 100,
            "perfect_pct": sum(
                1 for r in model_rows if r["tiles"] == 16 and r["score_ok"] and r["status_ok"]
            )
            / n
            * 100,
            "latency_ms": sum(r["latency_ms"] or 0 for r in model_rows) / n,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Score See experiment against oracle")
    parser.add_argument("--run", type=Path, help="specific run dir under runs/")
    parser.add_argument("--json", action="store_true", help="emit JSON summary")
    args = parser.parse_args()

    runs_root = project_root() / "runs"
    if args.run:
        run_dirs = [args.run if args.run.is_absolute() else runs_root / args.run]
    else:
        run_dirs = sorted(runs_root.glob("*_2048_see"))

    all_rows: list[dict] = []
    for run_dir in run_dirs:
        all_rows.extend(score_run(run_dir))

    if not all_rows:
        print("No See turns with VLM data found.")
        return 1

    summary = summarize(all_rows)
    if args.json:
        print(json.dumps({"rows": len(all_rows), "by_model": summary}, indent=2))
        return 0

    print(f"Scored {len(all_rows)} turns across {len({r['run'] for r in all_rows})} runs\n")
    for model, stats in summary.items():
        print(
            f"{model}: n={int(stats['n'])} "
            f"tiles={stats['tiles_avg']:.1f}/16 "
            f"score={stats['score_pct']:.0f}% "
            f"status={stats['status_pct']:.0f}% "
            f"perfect={stats['perfect_pct']:.0f}% "
            f"lat={stats['latency_ms']:.0f}ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
