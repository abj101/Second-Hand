"""GPA GUI adapter with stubbed detector; real weights are gpu-only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from loop.adaptive.gui_yolo import GpaGuiDetector, filter_proposals, validate_bbox

BOX = (0.10, 0.20, 0.30, 0.50)


def test_stub_detector_emits_frozen_contract():
    def detector(_image):
        return [{"bbox": BOX, "class_hint": "button", "conf": 0.91}]

    det = GpaGuiDetector(detector=detector)
    props = det.propose(Image.new("RGB", (64, 64), 0), frame_id="f1")
    assert det.status == "ok"
    assert len(props) == 1
    row = props[0]
    assert set(row) == {
        "bbox",
        "role",
        "source",
        "class_hint",
        "confidence",
        "frame_id",
    }
    assert row["role"] == "ui"
    assert row["source"] == "gpa-gui"
    assert row["class_hint"] == "button"
    assert row["frame_id"] == "f1"
    diag = det.diagnostics
    assert diag["available"] is True and diag["reason"] is None
    assert diag["raw"] == 1 and diag["accepted"] == 1
    assert diag["rejected"] == {"malformed": 0, "off_frame": 0, "full_screen": 0}
    assert diag["backend"] == "stub"
    assert diag["device"] is None and diag["fallback"] is False


def test_failed_load_is_unavailable_without_invented_hits():
    def boom(_path):
        raise RuntimeError("weights missing")

    det = GpaGuiDetector(loader=boom)
    props = det.propose(Image.new("RGB", (16, 16), 0))
    assert det.status == "unavailable"
    assert props == []
    diag = det.diagnostics
    assert diag["available"] is False
    assert diag["reason"]
    assert diag["raw"] is None and diag["accepted"] is None
    assert diag["rejected"] is None and diag["merged"] is None


def test_missing_weights_file_is_unavailable():
    det = GpaGuiDetector(weights_path=Path("data/loop/weights/gpa-gui/missing-model.pt"))
    assert det.status == "unavailable"
    assert det.propose(Image.new("RGB", (8, 8), 0)) == []


def test_rejects_malformed_off_frame_and_full_screen():
    raw = [
        {"bbox": "nope", "conf": 0.5, "class_hint": "icon"},
        {"bbox": (1.2, 0.1, 1.3, 0.2), "conf": 0.5, "class_hint": "icon"},
        {"bbox": (0.0, 0.0, 1.0, 1.0), "conf": 0.5, "class_hint": "icon"},
        {"bbox": BOX, "conf": 0.5, "class_hint": "icon"},
    ]
    props, diag = filter_proposals(raw, frame_id="x", role="ui", source="gpa-gui")
    assert len(props) == 1
    assert diag["raw"] == 4
    assert diag["accepted"] == 1
    assert diag["rejected"] == {"malformed": 1, "off_frame": 1, "full_screen": 1}
    assert diag["merged"] is None


def test_validate_bbox_clamps_finite_boxes():
    bbox, reason = validate_bbox(BOX)
    assert reason is None
    assert bbox == BOX


def test_cuda_nms_fallback_is_cached(monkeypatch):
    monkeypatch.setattr("loop.adaptive.gui_yolo._cuda", lambda: True)

    class Model:
        def __init__(self):
            self.devices = []

        def predict(self, _image, *, device, **_kw):
            self.devices.append(device)
            if device == 0:
                raise NotImplementedError("CPU torchvision NMS")
            return []

    model = Model()
    det = GpaGuiDetector(loader=lambda _path: model)
    image = Image.new("RGB", (16, 16), 0)
    assert det.propose(image) == []
    assert det.propose(image) == []
    assert model.devices == [0, "cpu", "cpu"]
    assert det.diagnostics["device"] == "cpu"
    assert det.diagnostics["fallback"] is True
    assert det.diagnostics["backend"] == "gpa-gui"
    assert det.diagnostics["available"] is True


def test_counters_are_per_frame_not_cumulative():
    frames = [
        [{"bbox": BOX, "conf": 0.5}, {"bbox": "nope", "conf": 0.4}],
        [{"bbox": BOX, "conf": 0.5}],
    ]
    det = GpaGuiDetector(detector=lambda _image: frames.pop(0))
    image = Image.new("RGB", (16, 16), 0)
    det.propose(image, frame_id="f1")
    assert det.diagnostics["raw"] == 2
    assert det.diagnostics["rejected"] == {
        "malformed": 1,
        "off_frame": 0,
        "full_screen": 0,
    }
    det.propose(image, frame_id="f2")
    assert det.diagnostics["raw"] == 1 and det.diagnostics["accepted"] == 1
    assert det.diagnostics["rejected"] == {
        "malformed": 0,
        "off_frame": 0,
        "full_screen": 0,
    }


def test_empty_detector_result_is_measured_zero_not_unavailable():
    det = GpaGuiDetector(detector=lambda _image: [])
    assert det.propose(Image.new("RGB", (8, 8), 0)) == []
    diag = det.diagnostics
    assert diag["available"] is True and diag["reason"] is None
    assert diag["raw"] == 0 and diag["accepted"] == 0
    assert diag["rejected"] == {"malformed": 0, "off_frame": 0, "full_screen": 0}


def test_inference_failure_clears_previous_frame_counters_and_boxes():
    state = {"fail": False}

    def detector(_image):
        if state["fail"]:
            raise RuntimeError("cuda oom")
        return [{"bbox": BOX, "conf": 0.9}, {"bbox": "nope", "conf": 0.1}]

    det = GpaGuiDetector(detector=detector)
    image = Image.new("RGB", (16, 16), 0)
    assert det.propose(image, frame_id="ok") != []
    assert det.diagnostics["accepted"] == 1
    state["fail"] = True
    assert det.propose(image, frame_id="bad") == []
    assert det.status == "unavailable"
    assert det.diagnostics["available"] is False
    assert det.diagnostics["raw"] is None
    # A dead provider must not resurrect boxes or old counts on later calls.
    assert det.propose(image, frame_id="bad2") == []
    assert det.diagnostics["raw"] is None


@pytest.mark.gpu
def test_gpa_gui_loads_cached_weights_when_present():
    det = GpaGuiDetector()
    if det.status != "ok":
        pytest.skip("gpa-gui weights unavailable")
    image = Image.new("RGB", (1261, 720), (30, 30, 30))
    props = det.propose(image, frame_id="gpu-smoke")
    assert isinstance(props, list)
    assert det.last_infer_ms >= 0.0
    assert det.model_path.endswith("model.pt")


@pytest.mark.gpu
def test_combat_frame_bin_when_annotation_fixture_present():
    root = Path(__file__).resolve().parents[2]
    ann = root / ".tmp" / "fusion_eval" / "annotations.json"
    if not ann.is_file():
        pytest.skip("combat annotation fixture absent")
    digest = "2a3e4f6ff3b03305"
    rows = json.loads(ann.read_text(encoding="utf-8"))
    if digest not in rows.get("frames", {}):
        pytest.skip("combat frame not in annotations")
    frame_path = root / ".tmp" / "sts2-full-20260922-105336" / "frames" / f"{digest}.bin"
    if not frame_path.is_file():
        pytest.skip("combat frame bin absent")
    det = GpaGuiDetector()
    if det.status != "ok":
        pytest.skip("gpa-gui weights unavailable")
    image = Image.frombytes("RGB", (1261, 720), frame_path.read_bytes())
    props = det.propose(image, frame_id=digest)
    assert isinstance(props, list)
