from __future__ import annotations

from pathlib import Path

from loop.eval.perception_cost import (
    build_perception_cost_report,
    default_effect_replay_path,
    default_proposal_replay_path,
)


def test_combined_report_reads_fixture_jsonl_and_keeps_transfer_gate_pending() -> None:
    root = Path(__file__).resolve().parents[2]
    proposal = root / "loop/fixtures/sts2_screens/proposal_replay.jsonl"
    effect = root / "loop/fixtures/sts2_screens/effect_replay.jsonl"
    assert proposal.exists()
    assert effect.exists()
    assert default_proposal_replay_path() == proposal
    assert default_effect_replay_path() == effect

    report = build_perception_cost_report(proposal_path=proposal, effect_path=effect)

    assert report["transfer_gate"] == "pending"
    assert report["timing"] == "unmeasured"
    assert "p50_ms" not in report
    assert "p95_ms" not in report

    assert 0.0 <= report["recall"] <= 1.0
    assert 0.0 <= report["precision"] <= 1.0
    assert 0.0 <= report["click_containment"] <= 1.0
    assert report["legend_clean"] is True
    assert report.get("legend_false_promotions", 0) == 0
    assert "fusion_audit" in report
    assert "gui_kept" in report["fusion_audit"]
    assert "kept_sources" in report["fusion_audit"]
    assert "cv_reserve" in report["fusion_audit"]
    assert report["perceive_ms"]["p50"] == "unmeasured"
    assert report["llm1"]["fresh"] == "unmeasured"
    assert report["cycle_repeats"] >= 1
    assert report["unresolved_recommended"] >= 1
