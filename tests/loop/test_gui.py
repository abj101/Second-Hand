from __future__ import annotations

from PIL import Image

from loop.os.gui import configure_gui, detect_gui_boxes, detect_gui_marks, gui_status, weight_path


def test_gui_off_detects_nothing() -> None:
    assert configure_gui(enabled=False) == "gui=off"
    assert gui_status() == "gui=off"
    image = Image.new("RGB", (64, 64), (12, 12, 12))
    assert detect_gui_boxes(image) == []
    assert detect_gui_marks(image) == []


def test_gui_missing_cache_is_unavailable() -> None:
    dest = weight_path("gpa")
    status = configure_gui(enabled=True, download=False, model="gpa")
    if dest.is_file():
        assert status.startswith("gui=gpa") or status.startswith("gui=unavailable")
        return
    assert status.startswith("gui=unavailable")
    assert detect_gui_boxes(Image.new("RGB", (32, 32), (0, 0, 0))) == []
