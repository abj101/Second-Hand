from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_live(tmp_path, monkeypatch):
    monkeypatch.setenv("AVATAR_LIVE_ROOT", str(tmp_path))


@pytest.fixture(autouse=True)
def reset_ocr_context() -> None:
    from loop.os import ocr

    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="", uncertain=False)
    yield
    ocr.clear_ocr_region_cache()
    ocr.set_ocr_context(screen="", uncertain=False)
