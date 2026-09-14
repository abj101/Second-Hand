from __future__ import annotations

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
