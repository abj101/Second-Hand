from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_live(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_LIVE_ROOT", str(tmp_path))
    monkeypatch.setenv("AVATAR_OCR", "off")
    from loop.os.gui import configure_gui

    configure_gui(enabled=False)


@pytest.fixture(autouse=True)
def reset_ocr_context() -> None:
    from loop.os import ocr

    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="", uncertain=False)
    yield
    ocr._ENGINE = None
    ocr._TRIED_RAPID = False
    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="", uncertain=False)