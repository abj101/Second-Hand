from __future__ import annotations

import sys

import pytest

from loop.os.window import WindowTarget, _grab_mss_client

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 capture")


def test_client_bbox_is_not_a_monitor_dict() -> None:
    target = WindowTarget(hwnd=1, title="t", left=40, top=80, width=1440, height=810)
    bbox = target.bbox()
    assert bbox == {"left": 40, "top": 80, "width": 1440, "height": 810}
    assert "monitor" not in bbox


def test_mss_client_rejects_empty_rect() -> None:
    target = WindowTarget(hwnd=1, title="t", left=0, top=0, width=0, height=0)
    with pytest.raises(RuntimeError, match="empty"):
        _grab_mss_client(target)


def test_hwnd_grab_matches_client_size() -> None:
    tk = pytest.importorskip("tkinter")
    from loop.os.window import find_window, focus_window, grab_hwnd_client

    root = tk.Tk()
    root.title("AvatarGrabClientTest")
    root.geometry("320x200+64+64")
    root.update()
    root.deiconify()
    root.update_idletasks()
    root.update()
    try:
        target = find_window("AvatarGrabClientTest")
        assert target is not None
        focused = focus_window(target)
        assert isinstance(focused, bool)
        image = grab_hwnd_client(target)
        assert image is not None
        assert image.size[0] == target.width
        assert image.size[1] == target.height
        assert image.size[0] < 1200
        assert image.size[1] < 800
    finally:
        root.destroy()
