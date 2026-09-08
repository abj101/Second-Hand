"""One-shot report for the latest sts2_collect run."""
from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path


def main() -> None:
    runs = sorted(
        [p for p in Path("runs").iterdir() if p.name.endswith("_sts2_collect")],
        key=lambda p: p.stat().st_mtime,
    )
    if not runs:
        print("no runs")
        return
    latest = runs[-1]
    rows = [
        json.loads(line)
        for line in latest.joinpath("turns.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    noops = sum(1 for r in rows if not (r.get("result") or {}).get("pixel_changed"))
    tools = [t for r in rows for t in (r.get("tool_trace") or [])]
    mem = sum("memory_" in t for t in tools)
    vision = sum(
        any(x in t for x in ("hud_read", "screen_perceive", "screen_list", "targeting_"))
        for t in tools
    )
    screens = Counter((r.get("metrics") or {}).get("screen") for r in rows)
    combat = "combat" in screens
    score = rows[-1].get("cumulative_score", 0) if rows else 0
    pid = rows[0].get("prompt_id", "") if rows else ""

    print(f"run={latest.name}")
    print(f"pid={pid}")
    print(f"steps={len(rows)} score={score:.2f} noop={noops}/{len(rows)}")
    print(f"screens={dict(screens)} combat_reached={combat}")
    print(f"tools={len(tools)} memory={mem} vision={vision}")
    if rows:
        p_ms = [r["perceive_ms"] for r in rows if r.get("perceive_ms")]
        r_ms = [r["reason_ms"] for r in rows if r.get("reason_ms")]
        if p_ms:
            print(f"latency_ms perceive={statistics.mean(p_ms):.0f} reason={statistics.mean(r_ms):.0f}")
    print("timeline:")
    for r in rows:
        m = r.get("metrics") or {}
        px = "ok" if (r.get("result") or {}).get("pixel_changed") else "noop"
        d = r.get("score_delta") or 0
        tc = len(r.get("tool_trace") or [])
        extra = f" tools={tc}" if tc else ""
        print(
            f"  {r['step_id']:2d} {str(m.get('screen') or '?'):8s} "
            f"{str(m.get('choice') or '-'):14s} +{d:.2f} {px}{extra}"
        )


if __name__ == "__main__":
    main()
