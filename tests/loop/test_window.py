from __future__ import annotations

import sys

import pytest

from loop.os.window import WindowTarget, _grab_mss_client

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Win32 capture")


def _printwindow_scaled(size: tuple[int, int], inner: tuple[int, int] | None = None):
    from PIL import Image

    inner = inner or (int(size[0] * 2 / 3), int(size[1] * 2 / 3))
    frame = Image.new("RGB", size, (0, 0, 0))
    frame.paste(Image.new("RGB", inner, (80, 40, 20)), (0, 0))
    return frame


def _centered_letterbox(size: tuple[int, int] = (400, 400)):
    from PIL import Image

    frame = Image.new("RGB", size, (0, 0, 0))
    frame.paste(Image.new("RGB", (size[0], size[1] // 2), (80, 40, 20)), (0, size[1] // 4))
    return frame


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


def test_select_client_capture_keeps_healthy_hwnd() -> None:
    from PIL import Image

    from loop.os.window import select_client_capture

    hwnd = Image.new("RGB", (200, 200), (40, 20, 20))
    mss = Image.new("RGB", (200, 200), (9, 9, 9))
    image, prov = select_client_capture(hwnd, mss, client_size=(200, 200))
    assert image is hwnd
    assert prov.source == "hwnd"
    assert prov.integrity == "trusted"


def test_select_client_capture_falls_back_on_black_hwnd() -> None:
    from PIL import Image

    from loop.os.window import select_client_capture

    hwnd = Image.new("RGB", (200, 200), (0, 0, 0))
    mss = Image.new("RGB", (200, 200), (40, 20, 20))
    image, prov = select_client_capture(hwnd, mss, client_size=(200, 200))
    assert image is mss
    assert prov.source == "mss-client"
    assert prov.integrity == "trusted"


def test_select_client_capture_rejects_top_left_scaled_hwnd() -> None:
    from PIL import Image

    from loop.os.window import select_client_capture

    hwnd = _printwindow_scaled((300, 300), (200, 200))
    mss = Image.new("RGB", (300, 300), (40, 20, 20))
    image, prov = select_client_capture(hwnd, mss, client_size=(300, 300))
    assert image is mss
    assert prov.source == "mss-client-integrity"
    assert prov.integrity == "trusted"
    assert prov.reason == "hwnd-top-left-scaled"


def test_select_client_capture_keeps_centered_letterbox_hwnd() -> None:
    from PIL import Image

    from loop.os.window import select_client_capture

    hwnd = _centered_letterbox((400, 400))
    mss = Image.new("RGB", (400, 400), (40, 20, 20))
    image, prov = select_client_capture(hwnd, mss, client_size=(400, 400))
    assert image is hwnd
    assert prov.source == "hwnd"
    assert prov.integrity == "trusted"


def test_select_client_capture_both_suspect_is_uncertain() -> None:
    from loop.os.window import select_client_capture

    hwnd = _printwindow_scaled((300, 300), (200, 200))
    mss = _printwindow_scaled((300, 300), (200, 200))
    image, prov = select_client_capture(hwnd, mss, client_size=(300, 300))
    assert prov.integrity == "uncertain"
    assert prov.source == "uncertain"
    assert image is hwnd


def test_grab_client_capture_monkeypatch_paths(monkeypatch) -> None:
    from PIL import Image

    from loop.os.window import WindowTarget, grab_client_capture, grab_client_tracked

    hwnd = _printwindow_scaled((300, 300), (200, 200))
    mss = Image.new("RGB", (300, 300), (40, 20, 20))
    monkeypatch.setattr("loop.os.window.grab_hwnd_client", lambda t: hwnd)
    monkeypatch.setattr("loop.os.window._grab_mss_client", lambda t, sct=None: mss)
    monkeypatch.setattr("loop.os.window._sync_client", lambda t: t)
    target = WindowTarget(hwnd=1, title="t", left=0, top=0, width=300, height=300)
    image, prov = grab_client_capture(target)
    assert image is mss
    assert prov.source == "mss-client-integrity"
    tracked, source = grab_client_tracked(target)
    assert tracked is mss
    assert source == "mss-client-integrity"
