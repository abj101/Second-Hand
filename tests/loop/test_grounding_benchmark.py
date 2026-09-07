from pathlib import Path

from loop.eval.grounding_benchmark import (
    gui_detector_health,
    run_grounding_benchmark,
    run_production_proposal_benchmark,
    score_legend_false_promotions,
)
from loop.os.gui import configure_gui


def test_diagnosed_runs_meet_grounding_gate(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    labels = root / "loop/fixtures/sts2_screens/grounding_labels.jsonl"
    monkeypatch.setenv("AVATAR_OCR", "auto")
    report = run_grounding_benchmark(labels)
    assert report["path"] == "oracle_assisted"
    assert report["oracle_enemy_bboxes"] is True
    assert report["forced_empty_gui"] is True
    if report["missing"]:
        # Packaged test distributions need not contain local run artifacts.
        return
    assert report["recall"] >= .95
    assert report["precision"] >= .95
    assert report["role_accuracy"] == 1.0
    assert report["click_containment"] == 1.0


def test_production_path_does_not_leak_truth(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[2]
    labels = root / "loop/fixtures/sts2_screens/proposal_replay.jsonl"

    def _boom(*_args, **_kwargs):
        raise AssertionError("expected objects must not be stamped onto production marks")

    monkeypatch.setattr("loop.eval.grounding_benchmark.apply_enemy_bboxes", _boom)
    report = run_production_proposal_benchmark(labels)
    assert report["path"] == "production"
    assert report["oracle_enemy_bboxes"] is False
    assert report["forced_empty_gui"] is False
    assert report["missing"] == 0
    assert report["transfer_gate"] == "pending"
    assert len(report["rows"]) >= 12
    assert report["legend_clean"] is True
    assert report["legend_false_promotions"] == 0
    rows = {row["id"]: row for row in report["rows"]}
    for row in report["rows"]:
        assert row.get("observation_id")
        assert "screen" in row
        assert "raw" in row and "ocr" in row["raw"] and "gui" in row["raw"] and "cv" in row["raw"]
        assert "fused" in row and "policy_input" in row
        assert "fusion_audit" in row
        assert "detector" in row
        assert row["legend_false_promotions"]["clean"] is True
    starvation = rows["sts2_map_legend_starvation"]
    assert starvation["detector"]["kind"] == "injected"
    assert starvation["hits"] >= 5
    assert starvation["click_containment"] >= 0.8
    sources = {item["source"] for item in starvation["fused"]}
    assert "cv" in sources
    assert any(abs(item["x"] - 0.42) < 0.03 for item in starvation["fused"])
    transfer = rows["unfamiliar_left_chrome_right_nodes"]
    assert transfer["hits"] == 3
    assert any(item["x"] >= 0.60 for item in transfer["fused"])
    assert rows["noop_abstain_miss"]["abstained"] is True
    assert rows["noop_empty_frame"]["expected"] == 0
    assert rows["cap_pressure_four"]["fusion_audit"]["cap"] == 4
    for item in starvation["fused"]:
        if item["label"] in {"", "icon", "node_0"}:
            assert item["role"] == ""
            assert item["confidence"] == 0.0


def test_score_legend_false_promotions_flags_chrome_with_role() -> None:
    from loop.os.aim import AimMark

    clean = [
        AimMark(id=1, x=0.5, y=0.5, x0=0.4, y0=0.4, x1=0.6, y1=0.6, label="icon"),
        AimMark(id=2, x=0.5, y=0.7, x0=0.4, y0=0.6, x1=0.6, y1=0.8, label="Continue"),
    ]
    assert score_legend_false_promotions(clean)["clean"] is True
    dirty = [
        AimMark(
            id=3,
            x=0.9,
            y=0.6,
            x0=0.85,
            y0=0.55,
            x1=0.95,
            y1=0.65,
            label="Enemy",
            role="enemy",
            confidence=0.9,
        )
    ]
    scored = score_legend_false_promotions(dirty)
    assert scored["clean"] is False
    assert scored["count"] == 1
    assert scored["violations"][0]["label"] == "Enemy"


def test_gui_detector_health_distinguishes_off_unavailable_and_zero() -> None:
    configure_gui(enabled=False)
    off = gui_detector_health(box_count=0)
    assert off["kind"] == "off"
    injected = gui_detector_health(box_count=3, injected=True)
    assert injected["kind"] == "injected"
    assert injected["box_count"] == 3
    status = configure_gui(enabled=True, download=False, model="gpa")
    live = gui_detector_health(box_count=0)
    if status.startswith("gui=unavailable"):
        assert live["kind"] == "unavailable"
    elif status == "gui=off":
        assert live["kind"] == "off"
    else:
        assert live["kind"] == "healthy_zero"
    configure_gui(enabled=False)
