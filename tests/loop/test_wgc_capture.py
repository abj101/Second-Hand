from __future__ import annotations

import sys
import threading
import types

import numpy as np
from PIL import Image

import loop.os.window as window
from loop.os.window import WindowTarget, grab_hwnd_wgc


class _FakeControl:
    def __init__(self) -> None:
        self.stops = 0
        self.done = threading.Event()

    def stop(self) -> None:
        self.stops += 1
        self.done.set()

    def wait(self) -> None:
        self.done.wait(1.0)


class _FakeFrame:
    width = 2
    height = 2

    def __init__(self) -> None:
        # BGRA: red, green, blue, white.
        self.frame_buffer = np.array(
            [
                [[0, 0, 255, 255], [0, 255, 0, 255]],
                [[255, 0, 0, 255], [255, 255, 255, 255]],
            ],
            dtype=np.uint8,
        )


def test_wgc_captures_one_frame_by_hwnd_and_stops(monkeypatch) -> None:
    control = _FakeControl()
    seen: dict[str, object] = {}

    class FakeWindowsCapture:
        def __init__(self, **kwargs) -> None:
            seen.update(kwargs)
            self.handlers = {}

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def start_free_threaded(self):
            def emit() -> None:
                self.handlers["on_frame_arrived"](_FakeFrame(), control)

            thread = threading.Thread(target=emit)
            thread.start()
            thread.join(1.0)
            return control

    monkeypatch.setitem(
        sys.modules,
        "windows_capture",
        types.SimpleNamespace(WindowsCapture=FakeWindowsCapture),
    )

    image = grab_hwnd_wgc(WindowTarget(77, "fake", 0, 0, 2, 2), timeout_s=0.2)

    assert image is not None
    assert image.size == (2, 2)
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((1, 0)) == (0, 255, 0)
    assert seen["window_hwnd"] == 77
    assert seen["cursor_capture"] is False
    assert control.stops >= 1


def test_wgc_returns_none_when_no_frame_arrives(monkeypatch) -> None:
    class FakeWindowsCapture:
        def __init__(self, **kwargs) -> None:
            del kwargs
            self.handlers = {}

        def event(self, handler):
            self.handlers[handler.__name__] = handler
            return handler

        def start_free_threaded(self):
            control = _FakeControl()
            return control

    monkeypatch.setitem(
        sys.modules,
        "windows_capture",
        types.SimpleNamespace(WindowsCapture=FakeWindowsCapture),
    )

    image = grab_hwnd_wgc(WindowTarget(88, "fake", 0, 0, 2, 2), timeout_s=0.01)

    assert image is None


def test_grab_client_capture_reports_wgc_as_trusted_source(monkeypatch) -> None:
    target = WindowTarget(99, "fake", 0, 0, 8, 6)
    image = Image.new("RGB", (8, 6), (180, 40, 20))
    identity = {"hwnd": 99, "pid": 123}

    monkeypatch.setattr(window, "_sync_client", lambda value: value)
    monkeypatch.setattr(window, "target_identity", lambda value: dict(identity))
    monkeypatch.setattr(window.user32, "GetForegroundWindow", lambda: 99)
    monkeypatch.setattr(window, "grab_hwnd_wgc", lambda value: image)
    monkeypatch.setattr(
        window,
        "grab_hwnd_imagegrab",
        lambda value: (_ for _ in ()).throw(AssertionError("WGC must be first")),
    )

    captured, provenance = window.grab_client_capture(target)

    assert captured is image
    assert provenance.source == "hwnd-wgc"
    assert provenance.integrity == "trusted"
    # WGC frames arrive through an asynchronous native callback, so a wrapper
    # timestamp cannot bound their age: record it as unknown, not as truth.
    assert provenance.acquisition_age_known is False
    assert provenance.acquisition_origin == window.ACQ_ORIGIN_BUFFERED_UNKNOWN
    assert provenance.acquisition_ts_monotonic is None
    assert provenance.grab_requested_ts_monotonic is not None
