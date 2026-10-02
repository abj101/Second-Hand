"""Assign teach/eval splits to the curated corpus: whole runs go to one side, eval is stratified by screen.

usage: python scripts/split_samples.py SAMPLES_DIR [--eval-runs-frac 0.3] [--per-screen 8]
Reads manifest.jsonl + screens.jsonl, writes splits.json:
  {"<game>": {"teach_runs": [...], "eval_runs": [...], "eval_key": [ids], "teach_pool": [ids]}}
eval_key is the small stratified set meant for a blind answer key; every eval-run frame not in it stays unused,
so teaching never sees an eval encounter. Fixture frames always go to eval (they are the legacy regression set).
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def main() -> None:
    a = sys.argv[1:]
    root = Path(a[0])
    frac = float(a[a.index("--eval-runs-frac") + 1]) if "--eval-runs-frac" in a else 0.3
    per = int(a[a.index("--per-screen") + 1]) if "--per-screen" in a else 8
    rows = {r["id"]: r for r in map(json.loads, open(root / "manifest.jsonl", encoding="utf-8"))}
    for t in map(json.loads, open(root / "screens.jsonl", encoding="utf-8")):
        rows[t["id"]]["screen"] = t["screen"] or "untagged"
    rng = random.Random(0)
    out = {}
    for game in sorted({r["game"] for r in rows.values()}):
        rs = [r for r in rows.values() if r["game"] == game]
        runs = sorted({r["run"] for r in rs} - {"fixture"})
        rng.shuffle(runs)
        eval_runs = set(runs[: max(1, round(len(runs) * frac))]) | {"fixture"}
        by_screen = defaultdict(list)
        for r in rs:
            if r["run"] in eval_runs:
                by_screen[r["screen"]].append(r)
        key = []
        for screen, items in sorted(by_screen.items()):
            fixtures = [r for r in items if r["run"] == "fixture"]
            rest = [r for r in items if r["run"] != "fixture"]
            rng.shuffle(rest)
            # Spread picks over runs so one long encounter cannot fill a screen's quota.
            seen, picked = set(), []
            for r in rest:
                if r["run"] not in seen:
                    seen.add(r["run"]); picked.append(r)
            picked += [r for r in rest if r not in picked]
            key += [r["id"] for r in fixtures] + [r["id"] for r in picked[: max(0, per - len(fixtures))]]
        out[game] = {
            "teach_runs": sorted(set(runs) - eval_runs),
            "eval_runs": sorted(eval_runs),
            "eval_key": sorted(key),
            "teach_pool": sorted(r["id"] for r in rs if r["run"] not in eval_runs),
            "screens": {s: sum(1 for r in rs if r["screen"] == s) for s in sorted({r["screen"] for r in rs})},
        }
        print(game, "teach_pool", len(out[game]["teach_pool"]), "eval_key", len(key), out[game]["screens"])
    (root / "splits.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
