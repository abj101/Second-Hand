from __future__ import annotations

import logging

import numpy as np
import pytest
from PIL import Image


def test_ocr_off_via_env(monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "off")
    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="", uncertain=False)
    assert ocr.engine_name() == "off"
    assert ocr.available() is False
    assert ocr.read_words(Image.new("RGB", (64, 64), (20, 20, 20))) == []
    assert ocr.read_text(Image.new("RGB", (64, 64), (20, 20, 20))) == ""


def test_norm_box_quad_and_xyxy() -> None:
    from loop.os.ocr import _norm_box

    x0, y0, x1, y1 = _norm_box([10, 20, 90, 20, 90, 60, 10, 60], 100, 100)
    assert abs(x0 - 0.10) < 1e-6
    assert abs(y0 - 0.20) < 1e-6
    assert abs(x1 - 0.90) < 1e-6
    assert abs(y1 - 0.60) < 1e-6
    x0, y0, x1, y1 = _norm_box(np.array([10.0, 20.0, 90.0, 60.0]), 100, 200)
    assert abs(x0 - 0.10) < 1e-6
    assert abs(y1 - 0.30) < 1e-6


def test_frame_is_blank() -> None:
    from loop.os.ocr import frame_is_blank

    assert frame_is_blank(Image.new("RGB", (64, 64), (0, 0, 0)))
    assert not frame_is_blank(Image.new("RGB", (64, 64), (120, 100, 80)))


def test_read_words_in_roi_maps_coords() -> None:
    from loop.os.ocr import read_words_in_roi

    img = Image.new("RGB", (200, 100), (240, 230, 210))
    # bottom half only — full frame OCR may still find nothing on blank top
    words = read_words_in_roi(img, (0.0, 0.5, 1.0, 1.0))
    assert isinstance(words, list)


def test_coerce_rgb_uint8_from_float() -> None:
    from loop.os.ocr import _coerce_rgb_uint8

    arr = np.zeros((8, 8, 3), dtype=np.float32)
    arr[:, :, 0] = 0.5
    out = _coerce_rgb_uint8(arr)
    assert out.dtype == np.uint8
    assert out.shape == (8, 8, 3)
    assert int(out[0, 0, 0]) == 127


def test_ocr_debug_dump_writes_png(tmp_path, monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR_DEBUG", "1")
    monkeypatch.setenv("AVATAR_OCR", "off")
    ocr.set_debug_dir(tmp_path / "ocr_debug")
    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    img = Image.new("RGB", (64, 64), (120, 100, 80))
    ocr.read_words(img, tag="test")
    stats = ocr.last_read_stats()
    assert stats.get("dump_raw")
    assert (tmp_path / "ocr_debug" / "0000_test_raw.png").exists()
    assert (tmp_path / "ocr_debug" / "0001_test_prepared.png").exists()


def test_ocr_probe_script_import() -> None:
    import scripts.ocr_probe as probe

    assert callable(probe.main)


def test_rapid_engine_constructed_once(monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "auto")
    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    mock_engine = MagicMock()
    with patch("rapidocr.RapidOCR", return_value=mock_engine) as ctor:
        assert ocr._rapid_engine() is mock_engine
        assert ocr._rapid_engine() is mock_engine
        assert ctor.call_count == 1


def test_empty_ocr_returns_without_rapid_warning(caplog, monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "auto")
    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    ocr.set_ocr_context(screen="combat", uncertain=True)
    caplog.set_level(logging.WARNING)
    img = Image.new("RGB", (960, 540), (128, 128, 128))
    words = ocr.read_words(img)
    assert words == []
    rapid_warnings = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING and "rapidocr" in r.name.lower()
    ]
    assert not any("empty" in r.getMessage().lower() for r in rapid_warnings)


def test_identical_crop_hits_region_cache(monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "off")
    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="combat", uncertain=False)
    img = Image.new("RGB", (960, 540), (90, 90, 90))
    ocr.read_words(img)
    stats1 = ocr.last_read_stats()
    ocr.read_words(img)
    stats2 = ocr.last_read_stats()
    assert stats1.get("ocr_mode") == "regions"
    assert stats2.get("cache_hits", 0) >= len(ocr._OCR_REGIONS)
    assert stats2.get("cache_misses", 0) == 0


def test_changed_crop_misses_region_cache(monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "off")
    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="combat", uncertain=False)
    base = Image.new("RGB", (960, 540), (90, 90, 90))
    ocr.read_words(base)
    changed = base.copy()
    px = changed.load()
    for x in range(100, 300):
        for y in range(400, 500):
            px[x, y] = (20, 200, 20)
    ocr.read_words(changed)
    stats = ocr.last_read_stats()
    assert stats.get("cache_misses", 0) >= 1


def test_uncertain_transition_uses_full_frame(monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "off")
    ocr.set_ocr_context(screen="combat", uncertain=True)
    img = Image.new("RGB", (960, 540), (90, 90, 90))
    ocr.read_words(img)
    assert ocr.last_read_stats().get("ocr_mode") == "full"


def test_warm_up_does_not_run_ocr(monkeypatch) -> None:
    from unittest.mock import MagicMock, patch

    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "auto")
    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    mock_engine = MagicMock()
    with patch("rapidocr.RapidOCR", return_value=mock_engine):
        assert ocr.warm_up_rapid_engine() is True
        mock_engine.assert_not_called()
