import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent.model import parse_json
from agent.perceive import normalize
from e1_perceive import score_frame


def test_parse_json_tolerates_fences_and_prose():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure: {"a": 2} hope that helps') == {"a": 2}
    assert parse_json("no json here") is None


def test_normalize_scales_clamps_and_drops_bad_points():
    out = normalize({"screen": "map", "affordances": [
        {"verb": "Click", "target": "node", "at": [500, 1200]},
        {"verb": "click", "target": "bad", "at": [1]},
        {"verb": "drag", "target": "card", "at": [100, 900], "to": [700, 300]},
    ]})
    assert out["affordances"][0] == {"verb": "click", "target": "node", "at": [0.5, 1.0], "why": ""}
    assert out["screen"] == "other"
    assert len(out["affordances"]) == 2 and out["affordances"][1]["to"] == [0.7, 0.3]
    assert normalize(None)["affordances"] == []

def test_screen_labels_follow_the_enum():
    from agent.perceive import SCHEMA
    labels = ("world", "dialogue", "menu", "keyboard", "battle", "title", "other")
    assert SCHEMA["properties"]["screen"]["enum"] == list(labels)
    assert [normalize({"screen": label})["screen"] for label in labels] == list(labels)
    assert normalize({"screen": "top-down room view"})["screen"] == "other"


def test_normalize_splits_string_text_into_lines():
    assert normalize({"text": "MOM: Right.\nAll boys leave"})["text"] == ["MOM: Right.", "All boys leave"]


def test_score_frame_recall_and_precision():
    label = {"size": [1000, 1000], "objects": [
        {"category": "button", "bbox": [100, 100, 200, 200]},
        {"category": "card", "bbox": [500, 500, 600, 700]},
        {"category": "ignore", "bbox": [800, 800, 900, 900]},
        {"category": "health_bar", "bbox": [0, 900, 100, 950]},
    ]}
    pts = [{"at": [0.15, 0.15]}, {"at": [0.85, 0.85]}, {"at": [0.05, 0.92]}]
    s = score_frame(pts, label)
    assert (s["targets"], s["hit"], s["points"], s["good"]) == (2, 1, 3, 2)
    assert s["missed"] == ["card"]


def test_crop_box_stays_inside_frame():
    from agent.locate import crop_box
    assert crop_box((1280, 720), [0.99, 0.01]) == (960, 0, 1280, 320)
    x0, y0, x1, y1 = crop_box((1280, 720), [0.5, 0.5])
    assert (x1 - x0, y1 - y0) == (320, 320) and x0 == 480


def test_snap_to_text_uses_nearest_matching_box_only():
    from agent.locate import snap_to_text
    tokens = [("Proceed", [0.80, 0.80, 0.90, 0.84]), ("Proceed", [0.10, 0.10, 0.20, 0.14]), ("Skip", [0.6, 0.6, 0.7, 0.64])]
    assert snap_to_text("proceed button", [0.83, 0.79], tokens) == pytest.approx([0.85, 0.82])
    assert snap_to_text("Settings", [0.5, 0.5], tokens) == [0.5, 0.5]
    assert snap_to_text("Skip", [0.1, 0.9], tokens) == [0.1, 0.9]  # matching text, but too far away
