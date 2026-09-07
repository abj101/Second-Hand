"""Phase 3 cost/quality report from frozen proposal + effect replay fixtures.

Combines production proposal fusion metrics with offline effect-sequence scoring.
No live timings are invented; latency fields are omitted or marked unmeasured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loop.config import project_root
from loop.eval.effect_benchmark import run_effect_benchmark
from loop.eval.grounding_benchmark import run_production_proposal_benchmark

_TIMING_KEYS = ("p50_ms", "p95_ms", "latency_ms", "latency_p50", "latency_p95")


def default_proposal_replay_path() -> Path:
    return project_root() / "loop" / "fixtures" / "sts2_screens" / "proposal_replay.jsonl"


def default_effect_replay_path() -> Path:
    return project_root() / "loop" / "fixtures" / "sts2_screens" / "effect_replay.jsonl"


def _timing_from_reports(*reports: dict[str, Any]) -> dict[str, Any] | str:
    """Return p50/p95 when present on upstream reports; otherwise unmeasured."""
    out: dict[str, Any] = {}
    for report in reports:
        for key in _TIMING_KEYS:
            if key in report and report[key] is not None:
                out[key] = report[key]
    return out if out else "unmeasured"


def build_perception_cost_report(
    *,
    proposal_path: Path | None = None,
    effect_path: Path | None = None,
) -> dict[str, Any]:
    """Run proposal + effect replay benchmarks and merge into one quality dict."""
    proposal = run_production_proposal_benchmark(proposal_path or default_proposal_replay_path())
    effect = run_effect_benchmark(effect_path)

    report: dict[str, Any] = {
        "recall": proposal["recall"],
        "precision": proposal["precision"],
        "click_containment": proposal["click_containment"],
        "legend_clean": proposal["legend_clean"],
        "cycle_repeats": effect["cycle_repeats"],
        "unresolved_recommended": effect["unresolved_recommended"],
        "transfer_gate": "pending",
        "fusion_audit": {
            "gui_kept": [
                ((row.get("fusion_audit") or {}).get("raw_counts") or {}).get("gui_kept")
                for row in proposal.get("rows") or []
            ],
            "kept_sources": [((row.get("fusion_audit") or {}).get("kept_sources")) for row in proposal.get("rows") or []],
            "cv_reserve": [((row.get("fusion_audit") or {}).get("cv_reserve")) for row in proposal.get("rows") or []],
        },
        "legend_false_promotions": proposal.get("legend_false_promotions", 0),
        "perceive_ms": {"p50": "unmeasured", "p95": "unmeasured"},
        "ocr_ms": {"p50": "unmeasured", "p95": "unmeasured"},
        "think_ms": {"p50": "unmeasured", "p95": "unmeasured"},
        "llm1": {"fresh": "unmeasured", "cached": "unmeasured"},
    }

    timing = _timing_from_reports(proposal, effect)
    if timing != "unmeasured":
        report.update(timing)
    else:
        report["timing"] = timing

    return report
