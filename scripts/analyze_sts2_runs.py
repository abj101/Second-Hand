"""Compare recent STS2 collect runs before/after perceive prompt changes."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from loop.agent.prompts import REASON_TURN_SYSTEM, SCENE_PERCEIVE_SYSTEM, prompt_id
from loop.eval.export import compare_turn_runs, load_steps, turn_run_metrics


def _old_prompt_id() -> str:
    text = SCENE_PERCEIVE_SYSTEM + REASON_TURN_SYSTEM
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def analyze_run(path: Path) -> dict | None:
    turns = path / "turns.jsonl"
    if not turns.exists():
        return None
    rows = [json.loads(line) for line in turns.read_text(encoding="utf-8").splitlines() if line.strip()]
    metrics = turn_run_metrics(path)
    pid = rows[0].get("prompt_id", "") if rows else ""
    arch = rows[0].get("architecture", "") if rows else ""

    perceive_screens: list[str] = []
    host_screens: list[str] = []
    bogus_controls: list[str] = []
    rescan_tools = 0
    noop = 0
    progress = 0
    tutorial_step2_combat = 0
    tutorial_step2_ok = 0
    cards_halluc = 0
    perceive_ms: list[float] = []
    reason_ms: list[float] = []

    for row in rows:
        m = row.get("metrics") or {}
        host_screens.append(str(m.get("screen") or ""))
        if row.get("perceive_ms") is not None:
            perceive_ms.append(float(row["perceive_ms"]))
        if row.get("reason_ms") is not None:
            reason_ms.append(float(row["reason_ms"]))

        pr = row.get("perceive_raw") or ""
        if pr:
            try:
                parsed = json.loads(pr)
                perceive_screens.append(str(parsed.get("screen") or ""))
                step = (parsed.get("tutorial") or {}).get("step")
                if step == 2 and parsed.get("screen") == "combat":
                    tutorial_step2_combat += 1
                if step == 2 and parsed.get("screen") == "tutorial":
                    tutorial_step2_ok += 1
                for ctrl in parsed.get("controls") or []:
                    lab = str(ctrl.get("label") or "")
                    if lab.lower() in {"cards", "card"}:
                        cards_halluc += 1
                    if step == 2 and lab.lower() in {
                        "strike",
                        "defend",
                        "end turn",
                        "jaw worm",
                        "got it",
                    }:
                        bogus_controls.append(lab)
            except json.JSONDecodeError:
                pass

        tool_trace = row.get("tool_trace") or []
        if any("screen_perceive" in str(item) for item in tool_trace):
            rescan_tools += 1
        if not (row.get("result") or {}).get("pixel_changed"):
            noop += 1
        if (row.get("score_delta") or 0) > 0:
            progress += 1

    screen_mismatch = sum(
        1
        for row in rows
        if row.get("perceive_raw")
        and row.get("scene")
        and "screen=combat" in str(row.get("scene"))
        and "tutorial=" in str(row.get("scene"))
    )

    return {
        "name": path.name,
        "steps": len(rows),
        "prompt_id": pid,
        "architecture": arch,
        "metrics": metrics,
        "host_screens": dict(Counter(host_screens)),
        "perceive_screens": dict(Counter(perceive_screens)),
        "noop_rate": noop / len(rows) if rows else 0.0,
        "progress_steps": progress,
        "rescan_tools": rescan_tools,
        "tutorial_step2_combat": tutorial_step2_combat,
        "tutorial_step2_ok": tutorial_step2_ok,
        "cards_halluc": cards_halluc,
        "bogus_step2_controls": dict(Counter(bogus_controls)),
        "final_score": rows[-1].get("cumulative_score", 0) if rows else 0,
        "choices": dict(Counter(
            (row.get("metrics") or {}).get("choice", "")
            for row in rows
            if (row.get("metrics") or {}).get("choice")
        )),
        "mean_perceive_ms": sum(perceive_ms) / len(perceive_ms) if perceive_ms else None,
        "mean_reason_ms": sum(reason_ms) / len(reason_ms) if reason_ms else None,
        "scene_combat_while_tutorial": screen_mismatch,
    }


def main() -> None:
    new_pid = prompt_id(arm="no_skill", pace="turn", architecture="two_stage")
    old_pid = _old_prompt_id()
    print(f"new_prompt_id: {new_pid}")
    print(f"old_prompt_id: {old_pid}")

    runs_root = Path("runs")
    sts2 = sorted(
        [p for p in runs_root.iterdir() if p.name.endswith("_sts2_collect")],
        key=lambda p: p.name,
    )
    today = [p for p in sts2 if p.name.startswith("20260829")]
    selected = today[-10:] if len(today) >= 10 else today
    results = [r for p in selected if (r := analyze_run(p))]

    print("\n=== RUN SUMMARY (today, chronological) ===")
    for row in results:
        m = row["metrics"]
        if row["prompt_id"] == new_pid:
            tag = "NEW"
        elif row["prompt_id"] == old_pid:
            tag = "OLD"
        else:
            tag = "OTHER"
        print(
            f"{row['name']} [{tag}] "
            f"steps={row['steps']} "
            f"tutorial_clear={m['tutorial_clear']} "
            f"combat={m['combats_started']} "
            f"score={row['final_score']:.2f} "
            f"noop={row['noop_rate']:.0%} "
            f"rescans={row['rescan_tools']} "
            f"step2_combat={row['tutorial_step2_combat']} "
            f"step2_tutorial={row['tutorial_step2_ok']} "
            f"cards_ctrl={row['cards_halluc']}"
        )

    new_runs = [r for r in results if r["prompt_id"] == new_pid]
    old_runs = [r for r in results if r["prompt_id"] == old_pid]
    if new_runs and old_runs:
        latest = new_runs[-1]
        baseline = old_runs[-1]
        cmp = compare_turn_runs(runs_root / baseline["name"], runs_root / latest["name"])
        print("\n=== compare_turn_runs (last OLD vs latest NEW) ===")
        print(json.dumps(cmp, indent=2))

    if new_runs:
        print("\n=== LATEST NEW RUN DETAIL ===")
        latest_path = runs_root / new_runs[-1]["name"]
        for step in load_steps(latest_path)[:12]:
            m = step.metrics or {}
            scene = step.scene or ""
            choice = m.get("choice", "")
            delta = step.score_delta or 0
            print(
                f"  step {step.step_id}: host={m.get('screen')} "
                f"choice={choice!r} delta={delta} scene={scene[:100]}"
            )


if __name__ == "__main__":
    main()
