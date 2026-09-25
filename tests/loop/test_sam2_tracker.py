"""SAM 2 adapter with a stubbed proposer. Real weights are a gpu test."""

from __future__ import annotations

import pytest
from PIL import Image

from loop.adaptive import sam2_tracker as sam2_mod
from loop.adaptive.sam2_tracker import Sam2Tracker

BOX = (0.20, 0.20, 0.45, 0.55)


def test_unavailable_without_cuda(monkeypatch):
    monkeypatch.setattr(sam2_mod, "sam2_available", lambda: False)
    tracker = Sam2Tracker(["character"])
    assert tracker.status == "unavailable"
    assert tracker.associator == "none"
    assert tracker.update(Image.new("RGB", (8, 8), 0)) == ()


def test_stubbed_proposer_propagates_and_keeps_label():
    seen = {"n": 0}

    def proposer(_image):
        seen["n"] += 1
        if seen["n"] == 2:
            return []
        return [{"bbox": BOX, "label": "enemy", "conf": 0.91, "track_id": "should-drop"}]

    def propagator(_image, boxes):
        if seen["n"] == 2:
            return []
        return [dict(row) for row in boxes]

    tracker = Sam2Tracker(["enemy"], proposer=proposer, propagator=propagator)
    image = Image.new("RGB", (40, 40), (9, 9, 9))
    first = tracker.update(image)
    assert len(first) == 1
    assert first[0].state == "new"
    assert first[0].detector_label == "enemy"
    assert first[0].track_id != "should-drop"
    assert tracker.update(image) == ()
    returned = tracker.update(image)
    assert len(returned) == 1
    assert returned[0].state == "returned"
    assert returned[0].track_id == first[0].track_id
    assert returned[0].detector_label == "enemy"


@pytest.mark.gpu
def test_sam2_loads_small_checkpoint_on_cuda():
    if not sam2_mod.sam2_available():
        pytest.skip("CUDA required")
    tracker = Sam2Tracker(["character"])
    if tracker.status != "ok":
        pytest.skip("sam2 weights unavailable")
    assert tracker.associator == "sam2"
    hits = tracker.update(Image.new("RGB", (64, 64), (20, 40, 20)))
    assert isinstance(hits, tuple)
