"""Grounding DINO adapter with stubbed detector. Real weights are a gpu test."""

from __future__ import annotations

import pytest
from PIL import Image

from loop.adaptive.grounding_dino import (
    CACHED_TINY_SHA256,
    GroundingDinoTracker,
)

BOX = (0.10, 0.10, 0.40, 0.40)
GIANT = (0.01, 0.01, 0.99, 0.99)


def _proposal(**overrides):
    row = {
        "bbox": BOX,
        "role": "world",
        "source": "grounding-dino",
        "class_hint": "person",
        "confidence": 0.9,
        "frame_id": "f1",
    }
    row.update(overrides)
    return row


def test_empty_prompts_do_not_load():
    tracker = GroundingDinoTracker([])
    assert tracker.status == "ok"
    assert tracker.model_id_loaded is None
    assert tracker.load_ms == 0.0
    assert tracker.propose(Image.new("RGB", (16, 16), 0)) == []


def test_failed_loader_is_unavailable():
    def boom(_model_id, _prompts):
        raise RuntimeError("weights missing")

    tracker = GroundingDinoTracker(["person"], loader=boom)
    image = Image.new("RGB", (16, 16), 0)
    assert tracker.status == "unavailable"
    assert tracker.model_id_loaded is None
    assert tracker.propose(image) == []


def test_unavailable_never_fakes_zero_hit_success():
    tracker = GroundingDinoTracker(
        ["person"],
        detector=lambda _img: [{"bbox": BOX, "label": "person", "conf": 0.9}],
    )
    tracker.status = "unavailable"
    assert tracker.propose(Image.new("RGB", (16, 16), 0)) == []


def test_stubbed_detector_returns_world_proposals():
    def detector(_image):
        return [{"bbox": BOX, "label": "person", "conf": 0.82}]

    tracker = GroundingDinoTracker(["person"], detector=detector)
    out = tracker.propose(Image.new("RGB", (32, 32), 0), frame_id="frame-a")
    assert len(out) == 1
    assert out[0] == _proposal(confidence=0.82, frame_id="frame-a")


def test_base_variant_does_not_replace_tiny():
    stub = lambda _model_id, _prompts: (None, None, None)  # noqa: E731
    tiny = GroundingDinoTracker(["person"], variant="tiny", loader=stub)
    base = GroundingDinoTracker(["person"], variant="base", loader=stub)
    assert tiny.model_id == "IDEA-Research/grounding-dino-tiny"
    assert base.model_id == "IDEA-Research/grounding-dino-base"
    assert tiny.source == "grounding-dino"
    assert base.source == "grounding-dino-base"


def test_fullscreen_box_rejected_and_counted():
    def detector(_image):
        return [
            {"bbox": BOX, "label": "person", "conf": 0.9},
            {"bbox": GIANT, "label": "screen", "conf": 0.99},
        ]

    tracker = GroundingDinoTracker(["person"], detector=detector)
    out = tracker.propose(Image.new("RGB", (64, 64), 0))
    assert len(out) == 1
    assert out[0]["bbox"] == BOX
    assert tracker.diagnostics["rejected_fullscreen"] == 1


def test_malformed_and_off_frame_rejections():
    def detector(_image):
        return [
            {"bbox": "bad", "label": "x", "conf": 0.5},
            {"bbox": (0.2, 0.2, 1.2, 0.5), "label": "x", "conf": 0.5},
        ]

    tracker = GroundingDinoTracker(["person"], detector=detector)
    assert tracker.propose(Image.new("RGB", (16, 16), 0)) == []
    assert tracker.diagnostics["rejected_malformed"] == 1
    assert tracker.diagnostics["rejected_off_frame"] == 1


@pytest.mark.gpu
def test_grounding_dino_tiny_loads_cached_weights_when_present():
    tracker = GroundingDinoTracker(["person"])
    if tracker.status != "ok":
        pytest.skip("grounding-dino tiny weights unavailable")
    assert tracker.model_id_loaded == "IDEA-Research/grounding-dino-tiny"
    assert tracker.variant == "tiny"
    hits = tracker.propose(Image.new("RGB", (64, 64), (30, 30, 30)), frame_id="gpu")
    assert isinstance(hits, list)
    assert CACHED_TINY_SHA256  # pinned expected snapshot for qualification runs
