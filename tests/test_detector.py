from __future__ import annotations

from types import SimpleNamespace

from avatar.gsm.rule.detector import DetectedBox, FakeDetector, OpenVocabDetector, default_detector


def _skip_weight_load(self: OpenVocabDetector) -> None:
    self._impl = None
    self.family = "unavailable"


def test_fake_detector_still_works() -> None:
    det = FakeDetector()
    boxes = det.detect(None, ["T tetromino"])
    assert boxes
    assert all(b.label == "T tetromino" for b in boxes)
    assert det.detect(None, ["cat"]) == []


def test_open_vocab_stubbed_impl_returns_boxes(monkeypatch) -> None:
    monkeypatch.setattr(OpenVocabDetector, "_try_load", _skip_weight_load)
    expected = [DetectedBox(label="T tetromino", x=0.2, y=0.1, w=0.1, h=0.1, score=0.9)]

    class _Stub:
        def detect(self, frame, labels, **_kw):
            _ = frame, labels
            return list(expected)

    det = OpenVocabDetector(device="cpu")
    det._impl = _Stub()
    det.family = "grounding-dino"
    assert det.detect(None, ["T tetromino"]) == expected
    assert det.family == "grounding-dino"


def test_open_vocab_forwards_thresholds(monkeypatch) -> None:
    monkeypatch.setattr(OpenVocabDetector, "_try_load", _skip_weight_load)
    seen: dict[str, float | None] = {}

    class _Stub:
        def detect(self, frame, labels, *, box_threshold=None, text_threshold=None):
            _ = frame, labels
            seen["box"] = box_threshold
            seen["text"] = text_threshold
            return []

    det = OpenVocabDetector(device="cpu")
    det._impl = _Stub()
    det.family = "grounding-dino"
    det.detect(None, ["Open"], box_threshold=0.18, text_threshold=0.15)
    assert seen["box"] == 0.18
    assert seen["text"] == 0.15


def test_unavailable_open_vocab_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(OpenVocabDetector, "_try_load", _skip_weight_load)
    det = OpenVocabDetector(device="cpu")
    det._impl = None
    det.family = "unavailable"
    assert det.detect(None, ["T tetromino"]) == []
    assert det.family == "unavailable"


def test_default_detector_grounding_dino_is_open_vocab(monkeypatch) -> None:
    monkeypatch.setattr(OpenVocabDetector, "_try_load", _skip_weight_load)
    spec = SimpleNamespace(detector_family="grounding-dino", fixture="tetris", model_id="", detector_device="cpu")
    det = default_detector(spec)
    assert det.family == "pixel-blob"
    assert not isinstance(det, OpenVocabDetector)
    assert not isinstance(det, FakeDetector)


def test_default_detector_dino_success_wraps_pixel_blob(monkeypatch) -> None:
    from avatar.gsm.rule.detector import FallbackDetector, PixelBlobDetector

    def _fake_load(self: OpenVocabDetector) -> None:
        self._impl = object()
        self.family = "grounding-dino"

    monkeypatch.setattr(OpenVocabDetector, "_try_load", _fake_load)
    spec = SimpleNamespace(detector_family="grounding-dino", fixture="tetris", model_id="", detector_device="cpu")
    det = default_detector(spec)
    assert isinstance(det, FallbackDetector)
    assert det.family == "grounding-dino"
    assert isinstance(det.fallback, PixelBlobDetector)


def test_open_vocab_records_load_error(monkeypatch, caplog) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("missing torch")

    monkeypatch.setattr(
        "transformers.AutoProcessor.from_pretrained",
        _boom,
    )
    with caplog.at_level("WARNING", logger="avatar.gsm.detector"):
        det = OpenVocabDetector(device="cpu")
    assert det.family == "unavailable"
    assert "missing torch" in det.last_error
    assert any("unavailable" in rec.message.lower() for rec in caplog.records)


def test_attach_live_runtime_keeps_extracted_for_dino(monkeypatch) -> None:
    from types import SimpleNamespace

    from avatar.gsm.registry import GsmRegistry
    from avatar.gsm.runtime import attach_live_runtime
    from avatar.schema import GsmSpec

    spec = GsmSpec(name="place_piece", kind="rule", enabled=True, state_source="extracted")
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = spec
    fake_rt = SimpleNamespace(detector=SimpleNamespace(family="grounding-dino"))
    monkeypatch.setattr("avatar.gsm.runtime.make_runtime", lambda *_a, **_k: fake_rt)
    attach_live_runtime(gsms)
    assert spec.state_source == "extracted"
    assert spec.detector_family == "grounding-dino"


def test_attach_live_runtime_downgrades_blob_extracted(monkeypatch) -> None:
    from types import SimpleNamespace

    from avatar.gsm.registry import GsmRegistry
    from avatar.gsm.runtime import attach_live_runtime
    from avatar.schema import GsmSpec

    spec = GsmSpec(name="place_piece", kind="rule", enabled=True, state_source="extracted")
    gsms = GsmRegistry("tetris")
    gsms.specs["place_piece"] = spec
    fake_rt = SimpleNamespace(detector=SimpleNamespace(family="pixel-blob"))
    monkeypatch.setattr("avatar.gsm.runtime.make_runtime", lambda *_a, **_k: fake_rt)
    attach_live_runtime(gsms)
    assert spec.state_source == "pixels"
    assert spec.detector_family == "pixel-blob"
