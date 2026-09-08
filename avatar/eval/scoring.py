from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def harness_lift_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """success rate by harness_level x model."""
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        level = str(row.get("harness_level", ""))
        model = str(row.get("model", ""))
        success = int(bool(row.get("success")))
        buckets[(level, model)].append(success)
    table: dict[str, dict[str, float]] = {}
    for (level, model), vals in buckets.items():
        table.setdefault(level, {})[model] = sum(vals) / len(vals) if vals else 0.0
    return table


def plot_attempt_success(rows: list[dict[str, Any]], out: Path) -> Path:
    by_attempt: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        attempt = int(row.get("attempt") or 0)
        by_attempt[attempt].append(int(bool(row.get("success"))))
    xs = sorted(k for k in by_attempt if k > 0)
    ys = [sum(by_attempt[x]) / len(by_attempt[x]) for x in xs]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.plot(xs, ys, marker="o")
    ax.set_xlabel("Attempt #")
    ax.set_ylabel("Success rate")
    ax.set_ylim(0, 1.05)
    ax.set_title("Attempt → success (OmniGameArena-style)")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def plot_skill_reuse(stats: dict[str, Any], out: Path) -> Path:
    labels = ["spawned", "merged", "rewritten", "rejected", "invokes"]
    vals = [float(stats.get(k, 0)) for k in labels]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.bar(labels, vals)
    ax.set_title("Constructor outcomes (fix GameSense 4.5.3)")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return out


def gsm_lift_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Success rate and mean VLM calls by harness_level x gsm/state_source tag."""
    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    vlm: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        level = str(row.get("harness_level", ""))
        tag = f"{row.get('gsm', 'off')}/{row.get('state_source', 'n/a')}"
        buckets[(level, tag)].append(int(bool(row.get("success"))))
        if row.get("vlm_calls_per_episode") is not None:
            vlm[(level, tag)].append(float(row["vlm_calls_per_episode"]))
    table: dict[str, dict[str, float]] = {}
    for (level, tag), vals in buckets.items():
        table.setdefault(level, {})[tag] = sum(vals) / len(vals) if vals else 0.0
        if vlm[(level, tag)]:
            calls = vlm[(level, tag)]
            table[level][f"{tag}:vlm"] = sum(calls) / len(calls)
    return table


def write_markdown_table(table: dict[str, dict[str, float]], path: Path) -> None:
    models = sorted({m for row in table.values() for m in row})
    lines = ["| level | " + " | ".join(models) + " |", "|---|---" + "|---" * max(0, len(models) - 1) + " |"]
    if models:
        lines[1] = "|---|" + "|".join(["---"] * len(models)) + "|"
    for level in sorted(table):
        cells = []
        for model in models:
            val = table[level].get(model)
            cells.append("—" if val is None else f"{val:.2f}")
        lines.append(f"| {level} | " + " | ".join(cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def gsm_rows_from_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten turns.jsonl into gsm_lift_table rows (one row per logged cycle)."""
    rows: list[dict[str, Any]] = []
    for rec in turns:
        result = rec.get("result") or {}
        verdict = result.get("verdict")
        invoked = bool(result.get("gsm_name"))
        rows.append(
            {
                "harness_level": rec.get("harness_level"),
                "model": rec.get("model"),
                "success": verdict in {"success", "partial"} if verdict else bool(result.get("executed")),
                "gsm": result.get("gsm") or ("off" if not invoked else result.get("gsm_kind") or "rule"),
                "state_source": result.get("state_source") or result.get("gsm_state_source") or "n/a",
                "vlm_calls_per_episode": 0 if invoked else (0 if result.get("skipped_inference") else 1),
                "gsm_ticks": result.get("gsm_ticks") or 0,
                "opt_iter": result.get("opt_iter") or 0,
            }
        )
    return rows
