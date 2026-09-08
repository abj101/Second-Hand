"""Analyze turn latency from recent collect runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

RUNS = [
    "runs/20260902T040749Z_sts2_collect",  # baseline (pre-fix, reached combat)
    "runs/20260902T045636Z_sts2_collect",  # F12 @ step 6
    "runs/20260902T050005Z_sts2_collect",  # F12 @ step 6
]


def summarize(run_path: Path) -> None:
    p = run_path / "turns.jsonl"
    if not p.exists():
        print(f"=== {run_path.name} MISSING ===\n")
        return
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"=== {run_path.name}  steps={len(rows)} ===")

    fields = [
        "perceive_ms",
        "think_ms",
        "reason_ms",
        "ocr_ms",
        "grab_ms",
        "obs_to_action_ms",
        "after_ms",
    ]
    for f in fields:
        vals = [r[f] for r in rows if isinstance(r.get(f), (int, float))]
        if vals:
            print(
                f"  {f:18s} avg={sum(vals)/len(vals):6.0f}ms  "
                f"min={min(vals):6.0f}  max={max(vals):6.0f}"
            )

    combat = [r for r in rows if (r.get("metrics") or {}).get("screen") == "combat"]
    illegal = sum(1 for r in rows if (r.get("metrics") or {}).get("illegal_chord"))
    blocked = sum(
        1
        for r in rows
        if not (r.get("result") or {}).get("executed")
        and (r.get("result") or {}).get("pixel_changed") is False
    )
    tool_steps = sum(1 for r in rows if r.get("tool_trace"))
    retries = sum(1 for r in rows if (r.get("metrics") or {}).get("perceive_retry_reason"))
    print(
        f"  combat={len(combat)}  illegal_chord={illegal}  no_pixel={blocked}  "
        f"tool_rounds={tool_steps}  perceive_retries={retries}"
    )
    print()
    for r in rows:
        m = r.get("metrics") or {}
        res = r.get("result") or {}
        choice = str(m.get("choice") or "")[:20]
        err = res.get("error") or ("illegal" if m.get("illegal_chord") else "")
        tools = len(r.get("tool_trace") or [])
        retry = (m.get("perceive_retry_reason") or "")[:40]
        print(
            f"  step {r['step_id']:2d}  {str(m.get('screen','?')):9s}  "
            f"perceive={r.get('perceive_ms',0):6.0f}  reason={r.get('reason_ms') or 0:6.0f}  "
            f"think={r.get('think_ms',0):6.0f}  obs2act={r.get('obs_to_action_ms',0):6.0f}  "
            f"choice={choice:20s}  exec={res.get('executed')}  "
            f"tools={tools}  err={err[:30]}  retry={retry}"
        )
    print()


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    for rel in RUNS:
        summarize(root / rel)


if __name__ == "__main__":
    main()
