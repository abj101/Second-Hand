"""Quick post-mortem for an sts2 collect run."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def action_name(action: dict) -> str:
    if action.get("name"):
        return str(action["name"])
    mark = action.get("mark")
    if mark is not None:
        return f"mk_{mark}"
    cx, cy = action.get("click_x"), action.get("click_y")
    if cx is not None and cy is not None:
        return f"pt_{float(cx):.2f}_{float(cy):.2f}"
    return "?"


def infer_phase(thought: str) -> str:
    t = thought.lower()
    if any(k in t for k in ("you died", "death", "defeated")):
        return "death"
    if any(k in t for k in ("relic", "burning blood", "choose a relic")):
        return "relic"
    if any(k in t for k in ("tutorial", "combat basics", "learn the controls")):
        return "tutorial"
    if any(k in t for k in ("strike", "enemy", "block", "energy", "end turn", "play card")):
        return "combat"
    if any(k in t for k in ("map", "node", "path", "floor", "ascend")):
        return "map"
    if any(k in t for k in ("profile", "singleplayer", "main menu", "save profile")):
        return "menu"
    if any(k in t for k in ("merchant", "shop")):
        return "shop"
    if any(k in t for k in ("rest", "campfire")):
        return "rest"
    if any(k in t for k in ("event", "?", "choice")):
        return "event"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    path = args.run_dir / "turns.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    actions = Counter()
    phases = Counter()
    errors = Counter()
    pixel_yes = 0
    max_streak = cur = 0
    hp_seen: list[tuple[int, object, object]] = []
    mad_buckets = Counter()

    for row in rows:
        actions[action_name(row.get("action") or {})] += 1
        phases[infer_phase(row.get("thought") or "")] += 1
        result = row.get("result") or {}
        if result.get("error"):
            errors[str(result["error"])[:100]] += 1
        if result.get("pixel_changed"):
            pixel_yes += 1
            cur = 0
        else:
            cur += 1
            max_streak = max(max_streak, cur)
        mad = result.get("pixel_mad")
        if mad is not None:
            if mad < 0.01:
                mad_buckets["<0.01"] += 1
            elif mad < 0.05:
                mad_buckets["0.01-0.05"] += 1
            else:
                mad_buckets[">=0.05"] += 1
        metrics = row.get("metrics") or {}
        if metrics.get("hp") is not None:
            hp_seen.append((row["step_id"], metrics.get("hp"), metrics.get("hp_max")))

    n = len(rows)
    print(f"steps={n}")
    print(f"pixel_changed={pixel_yes} ({pixel_yes/n:.1%})")
    print(f"max_no_change_streak={max_streak}")
    print(f"mad_buckets={dict(mad_buckets)}")
    print(f"phases_from_thoughts={dict(phases.most_common())}")
    print(f"top_actions={actions.most_common(12)}")
    if errors:
        print(f"errors={dict(errors.most_common(5))}")
    print(f"hp_readings={len(hp_seen)}")

    # show phase timeline (compress consecutive)
    timeline: list[tuple[str, int, int]] = []
    for row in rows:
        phase = infer_phase(row.get("thought") or "")
        if timeline and timeline[-1][0] == phase:
            timeline[-1] = (phase, timeline[-1][1], row["step_id"])
        else:
            timeline.append((phase, row["step_id"], row["step_id"]))
    print("phase_timeline:")
    for phase, a, b in timeline:
        span = f"{a}" if a == b else f"{a}-{b}"
        print(f"  {span:>10}  {phase}")

  # last 30 steps detail
    print("last_30:")
    for row in rows[-30:]:
        act = action_name(row.get("action") or {})
        res = row.get("result") or {}
        thought = re.sub(r"\s+", " ", (row.get("thought") or ""))[:90]
        print(
            f"  {row['step_id']:4d}  {act:16s}  px={int(bool(res.get('pixel_changed')))}  "
            f"mad={res.get('pixel_mad', 0):.3f}  {thought}"
        )
    print("pixel_change_events:")
    for row in rows:
        if not (row.get("result") or {}).get("pixel_changed"):
            continue
        act = action_name(row.get("action") or {})
        sent = (row.get("result") or {}).get("executed") or []
        mad = (row.get("result") or {}).get("pixel_mad")
        thought = re.sub(r"\s+", " ", (row.get("thought") or ""))[:70]
        print(f"  {row['step_id']:4d}  {act:16s}  sent={sent}  mad={mad:.3f}  {thought}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
