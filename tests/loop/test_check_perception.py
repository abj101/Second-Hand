"""Mocked checks for scripts/check_perception.py. No GPU, weights, network or input."""

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from PIL import Image

from loop.adaptive.policy import ReactiveField, StructuredDecision
from scripts import check_perception


class _FakeEngine:
    """Stand-in for the RapidOCR engine: records the single full-frame call."""

    def __init__(self, txts, scores, providers=("CPUExecutionProvider",)):
        self.txts = txts
        self.scores = scores
        self.calls = []
        session = SimpleNamespace(get_providers=lambda: list(providers))
        self.text_det = SimpleNamespace(session=SimpleNamespace(session=session))
        self.text_cls = SimpleNamespace(session=SimpleNamespace(session=session))
        self.text_rec = SimpleNamespace(session=SimpleNamespace(session=session))

    def __call__(self, array, *, use_cls=True):
        self.calls.append({"shape": array.shape, "use_cls": use_cls})
        boxes = [[0, 0, 10, 10] for _ in self.txts]
        return SimpleNamespace(txts=list(self.txts), boxes=boxes, scores=list(self.scores))


class _FakeDetector:
    def __init__(self, proposals, *, predict_device=0, status="ok", last_infer_ms=12.5):
        self.status = status
        self.weights_path = "C:/fake/gpa-gui/model.pt"
        self.load_ms = 5.0
        self.last_infer_ms = last_infer_ms
        self._predict_device = predict_device
        self._proposals = proposals

    def propose(self, image, *, frame_id=""):
        return [dict(row, frame_id=frame_id) for row in self._proposals]


class _FakePolicy:
    def __init__(self, choice):
        self.choice = choice
        self.seen = []

    def score(self, snapshot, directive, candidates):
        self.seen.append((snapshot, directive, candidates))
        return StructuredDecision(
            candidate_id=self.choice,
            confidence=1.0,
            probabilities={
                candidate.action_id: (1.0 if candidate.action_id == self.choice else 0.0)
                for candidate in candidates
            },
            decided_at=0.0,
            episode_generation=0,
            snapshot_id="smoke-frame",
            directive_revision=1,
            catalog_revision="",
        )


def _png(tmp_path):
    path = tmp_path / "frame.png"
    Image.new("RGB", (64, 48), color=(12, 34, 56)).save(path)
    return path


def _proposal_row(confidence=0.8):
    return {
        "bbox": (0.1, 0.1, 0.2, 0.2),
        "role": "ui",
        "source": "gpa-gui",
        "class_hint": "button",
        "confidence": confidence,
    }


def test_missing_frame_fails_explicitly(tmp_path, capsys):
    with pytest.raises(check_perception.SmokeError, match="missing frame"):
        check_perception.run(str(tmp_path / "absent.png"))
    assert check_perception.main(["--frame", str(tmp_path / "absent.png")]) == 1
    assert json.loads(capsys.readouterr().err)["ok"] is False


def test_unreadable_frame_fails_explicitly(tmp_path):
    path = tmp_path / "frame.png"
    path.write_text("not an image")
    with pytest.raises(check_perception.SmokeError, match="not a readable image"):
        check_perception.run(str(path))


def test_cpu_only_torch_fails_explicitly(monkeypatch, tmp_path):
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setattr(check_perception, "_require", lambda name, hint: fake_torch)
    with pytest.raises(check_perception.SmokeError, match="CUDA is unavailable"):
        check_perception.cuda_info()


def test_gpa_cpu_fallback_is_reported_as_failure():
    detector = _FakeDetector([_proposal_row()], predict_device="cpu")
    with pytest.raises(check_perception.SmokeError, match="CPU"):
        check_perception.gpa_infer(detector, Image.new("RGB", (64, 48)))


def test_gpa_inference_reports_device_and_nms_config():
    detector = _FakeDetector([_proposal_row(), _proposal_row(0.4)], predict_device=0)
    out = check_perception.gpa_infer(detector, Image.new("RGB", (64, 48)))
    assert out["predict_device"] == 0
    assert out["proposals_post_nms"] == 2
    # conf/iou/imgsz actually handed to ultralytics, i.e. the NMS configuration
    assert out["predict"]["iou"] == 0.7
    assert out["predict"]["conf"] == 0.15
    assert out["predict"]["imgsz"] == 640


def test_gpa_missing_weights_fails_explicitly(monkeypatch, tmp_path):
    missing = tmp_path / "model.pt"
    monkeypatch.setattr(check_perception, "gpa_weights_path", lambda: missing)
    with pytest.raises(check_perception.SmokeError, match="missing GPA weights"):
        check_perception.load_gpa()


def test_gpa_unavailable_detector_fails_explicitly(monkeypatch, tmp_path):
    weights = tmp_path / "model.pt"
    weights.write_bytes(b"stub")
    monkeypatch.setattr(check_perception, "gpa_weights_path", lambda: weights)
    monkeypatch.setattr(check_perception, "_require", lambda name, hint: None)
    monkeypatch.setattr(
        "loop.adaptive.gui_yolo.GpaGuiDetector",
        lambda: SimpleNamespace(status="unavailable", weights_path=str(weights)),
    )
    with pytest.raises(check_perception.SmokeError, match="failed to load"):
        check_perception.load_gpa()


def test_missing_bundled_ocr_models_fail_explicitly(monkeypatch, tmp_path):
    monkeypatch.setattr(check_perception, "ocr_models_dir", lambda: tmp_path)
    monkeypatch.setattr(check_perception, "_require", lambda name, hint: None)
    with pytest.raises(check_perception.SmokeError) as excinfo:
        check_perception.load_ocr()
    message = str(excinfo.value)
    assert "never downloads" in message
    for name in check_perception.OCR_MODELS:
        assert name in message


def test_ocr_models_dir_missing_rapidocr_is_explicit(monkeypatch):
    def missing(name, hint):
        raise check_perception.SmokeError(f"missing dependency {name!r} ({hint})")

    monkeypatch.setattr(check_perception, "_require", missing)
    with pytest.raises(check_perception.SmokeError, match="missing dependency 'rapidocr'"):
        check_perception.ocr_models_dir()


def test_qwen_loader_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(check_perception, "_require", lambda name, hint: None)

    def boom(cls, **kwargs):
        raise OSError("offline mode is enabled and this file is not in the cache")

    monkeypatch.setattr("loop.adaptive.qwen_policy.QwenReactionPolicy.from_pretrained", boom)
    with pytest.raises(check_perception.SmokeError) as excinfo:
        check_perception.load_qwen()
    message = str(excinfo.value)
    assert "2fc06364715b967f1860aea9cf38778875588b17" in message
    assert "never downloads" in message


def test_ocr_read_makes_one_full_frame_call_and_applies_the_production_threshold():
    engine = _FakeEngine(txts=["Settings", "Play"], scores=[0.99, 0.4])
    out = check_perception.ocr_read(engine, Image.new("RGB", (64, 48)))
    assert out["calls"] == 1
    assert engine.calls == [{"shape": (48, 64, 3), "use_cls": False}]
    assert out["use_cls"] is False
    assert out["raw_tokens"] == 2
    assert out["tokens"] == 1  # 0.4 is below the production 0.9 floor
    assert out["min_score"] == 0.9
    assert out["top_tokens"][0] == {"text": "Settings", "score": 0.99}


def test_ocr_providers_come_from_the_onnx_sessions():
    engine = _FakeEngine(txts=[], scores=[], providers=("CPUExecutionProvider",))
    assert check_perception.ocr_providers(engine) == ["CPUExecutionProvider"]
    assert check_perception.ocr_providers(object()) == []


def test_qwen_decision_offers_multiple_actions_and_carries_frame_evidence():
    policy = _FakePolicy("act_defend")
    out = check_perception.qwen_decision(policy, gpa_proposals=7, ocr_tokens=3)
    assert out["offer_count"] == 3
    assert out["choice"] == "act_defend"
    assert out["calibrated"] is False
    assert sum(out["probabilities"].values()) == 1.0
    snapshot = policy.seen[0][0]
    assert snapshot.fields["gpa_proposals"] == ReactiveField(7, 1.0, False)
    assert snapshot.fields["ocr_tokens"] == ReactiveField(3, 1.0, False)
    assert len(policy.seen[0][2]) == 3


def test_qwen_decision_rejects_a_choice_that_was_not_offered():
    with pytest.raises(check_perception.SmokeError, match="not among the offered"):
        check_perception.qwen_decision(_FakePolicy("act_bogus"), gpa_proposals=1, ocr_tokens=1)


def test_run_reports_offline_devices_and_stage_evidence(monkeypatch, tmp_path):
    frame = _png(tmp_path)
    engine = _FakeEngine(txts=["Settings", "Play"], scores=[0.99, 0.4])
    detector = _FakeDetector([_proposal_row(), _proposal_row(0.3)], predict_device=0)
    policy = _FakePolicy("act_defend")

    monkeypatch.setattr(
        check_perception,
        "cuda_info",
        lambda: {"kind": "cuda", "index": 0, "name": "RTX 4060", "torch": "2.14.0+cu130"},
    )
    monkeypatch.setattr(
        check_perception, "whole_device_vram", lambda: {"available": True, "free_percent": 42.0}
    )
    monkeypatch.setattr(check_perception, "cuda_memory", lambda: {"available": True, "allocated_mib": 1.0})
    monkeypatch.setattr(check_perception, "reset_peak_memory", lambda: None)
    monkeypatch.setattr(check_perception, "cpu_usage", lambda **kwargs: {"system_percent": 5.0})
    monkeypatch.setattr(
        check_perception,
        "load_gpa",
        lambda: (detector, {"weights": "w", "ultralytics": "8.4.138", "status": "ok", "load_ms": 1.0}),
    )
    monkeypatch.setattr(
        check_perception,
        "load_ocr",
        lambda: (engine, {"rapidocr": "3.9.2", "onnxruntime": "1.30.0", "load_ms": 2.0}),
    )
    monkeypatch.setattr(
        check_perception,
        "load_qwen",
        lambda: (policy, {"model_id": "Qwen/Qwen3.5-0.8B", "revision": "rev", "load_ms": 3.0}),
    )

    report = check_perception.run(str(frame))

    assert report["ok"] is True
    assert json.dumps(report, default=str)  # the report must be serialisable evidence
    assert report["frame"]["digest"] == sha256(Image.open(frame).convert("RGB").tobytes()).hexdigest()
    assert report["offline_env"]["HF_HUB_OFFLINE"] == "1"
    assert report["offline_env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert report["stages"]["gpa"]["inference"]["predict_device"] == 0
    assert report["stages"]["gpa"]["inference"]["proposals_post_nms"] == 2
    assert report["stages"]["ocr"]["inference"]["tokens"] == 1
    decision = report["stages"]["qwen"]["decision"]
    assert decision["choice"] == "act_defend"
    # GPA/OCR evidence must reach the Qwen decision in the same process
    assert decision["gpa_proposals"] == 2
    assert decision["ocr_tokens"] == 1


def test_main_prints_the_report_and_returns_zero(monkeypatch, capsys):
    monkeypatch.setattr(check_perception, "run", lambda frame: {"ok": True, "frame": frame})
    assert check_perception.main(["--frame", "frame.png"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_main_requires_a_frame():
    with pytest.raises(SystemExit):
        check_perception.main([])
