from __future__ import annotations

from loop.agent.perceive import SceneDescription
from loop.eval.perceive_benchmark import load_labels, offline_score_labels, score_perceive


def test_score_perceive_v2_shape() -> None:
    label = {
        "file": "tutorial_combat_basics_1of3.jpg",
        "screen": "tutorial",
        "must_include": ["Strike", "End Turn"],
        "must_not_include": ["Singleplayer"],
    }
    parsed = {
        "screen": "tutorial",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 1, "total": 3},
        "controls": [{"label": "Strike", "role": "card"}, {"label": "End Turn", "role": "button"}],
    }
    desc = SceneDescription.from_parsed(parsed)
    ok, detail = score_perceive(label=label, desc=desc, parsed=parsed, raw_text="{}")
    assert ok
    assert detail["antipatterns"] == []


def test_score_perceive_rejects_hp_in_controls() -> None:
    label = {"screen": "tutorial", "must_include": [], "must_not_include": []}
    parsed = {
        "screen": "tutorial",
        "combat": {"enemies": ["46/46"], "hand": []},
    }
    desc = SceneDescription.from_parsed(parsed)
    ok, detail = score_perceive(label=label, desc=desc, parsed=parsed, raw_text="")
    assert not ok
    assert detail["antipatterns"]


def test_offline_fixture_labels_exist() -> None:
    from pathlib import Path

    labels = load_labels()
    assert len(labels) >= 4
    image_labels = [
        row for row in labels
        if Path(str(row.get("file") or "")).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    ]
    report = offline_score_labels("sts2")
    assert len(report["ocr_rows"]) == len(image_labels)
