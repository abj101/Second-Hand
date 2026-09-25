"""YOLO-World adapter with a stubbed detector. Real weights are a gpu test."""

from __future__ import annotations

import pytest
from PIL import Image

from loop.adaptive.yolo_world import YoloWorldTracker

BOX = (0.10, 0.10, 0.40, 0.40)


def test_empty_prompts_return_no_hits_without_loading():
    tracker = YoloWorldTracker([])
    assert tracker.status == "ok"
    assert tracker.update(Image.new("RGB", (16, 16), 0)) == ()


def test_failed_load_is_unavailable():
    def boom(_prompts):
        raise RuntimeError("weights missing")

    tracker = YoloWorldTracker(["character"], loader=boom)
    image = Image.new("RGB", (16, 16), 0)
    assert tracker.status == "unavailable"
    assert tracker.update(image) == ()


def test_stubbed_detector_keeps_id_and_blanks_low_confidence():
    calls = {"n": 0}

    def detector(_image):
        calls["n"] += 1
        label = "character" if calls["n"] == 1 else "ignored"
        conf = 0.9 if calls["n"] == 1 else 0.05
        return [
            {"bbox": BOX, "label": label, "conf": conf, "track_id": "7"},
            {"bbox": (0.60, 0.60, 0.90, 0.90), "label": "coin", "conf": 0.1},
        ]

    tracker = YoloWorldTracker(["character", "coin"], detector=detector)
    image = Image.new("RGB", (32, 32), (3, 3, 3))
    first = tracker.update(image)
    assert tracker.associator == "stub"
    assert {hit.track_id for hit in first} == {"7", "trk_1"}
    by_id = {hit.track_id: hit for hit in first}
    assert by_id["7"].detector_label == "character"
    assert by_id["7"].state == "new"
    assert by_id["trk_1"].detector_label == ""
    second = tracker.update(image)
    kept = next(hit for hit in second if hit.track_id == "7")
    assert kept.state == "continuing"
    assert kept.detector_label == "character"


@pytest.mark.gpu
def test_yolo_world_loads_small_weights_when_present():
    tracker = YoloWorldTracker(["character"])
    if tracker.status != "ok":
        pytest.skip("yolo-world weights unavailable")
    assert tracker.associator in {"botsort", "bytetrack"}
    hits = tracker.update(Image.new("RGB", (64, 64), (30, 30, 30)))
    assert isinstance(hits, tuple)
