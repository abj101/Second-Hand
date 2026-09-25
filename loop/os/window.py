from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from dataclasses import replace
from typing import Any

from PIL import Image, ImageStat

from loop.os.capture import EncodedFrame, FrameEncoder
from loop.os.geometry import (
    ContentBox,
    ScreenGeometry,
    content_box_for,
    geometry_from_image,
    is_top_left_scaled_capture,
    perceive_longest_side,
)
from loop.os.profile import (
    CaptureProfile,
    GrabMeta,
    SettleResult,
    apply_crop,
    mean_absdiff,
    poll_until_settled,
    roi_thumb,
)

SW_RESTORE = 9
SW_SHOW = 5
WM_CLOSE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
gdi32.GetDIBits.restype = ctypes.c_int

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
_BLACK_LUMA = 5.0
_IDENTITY_BOX: ContentBox = (0.0, 0.0, 1.0, 1.0)

# Acquisition-time origins recorded on CaptureProvenance.  Only a correctly
# converted backend frame time or a timestamp taken immediately before a fresh
# synchronous grab bounds the true frame age.  A ``buffered-unknown`` frame
# (asynchronous callback whose buffering we cannot audit) never qualifies an
# observation for actuation.
ACQ_ORIGIN_BACKEND_FRAME_TIME = "backend-frame-time"
ACQ_ORIGIN_PRE_GRAB_SYNC = "pre-grab-sync"
ACQ_ORIGIN_BUFFERED_UNKNOWN = "buffered-unknown"
ACQ_ORIGIN_UNKNOWN = "unknown"


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

_dpi_aware = False
_sct: object | None = None


def ensure_dpi_aware() -> None:
    global _dpi_aware
    if _dpi_aware:
        return
    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            _dpi_aware = True
    except (AttributeError, OSError):
        pass


ensure_dpi_aware()


@dataclass
class WindowTarget:
    hwnd: int
    title: str
    left: int
    top: int
    width: int
    height: int

    def bbox(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def _client_target(hwnd: int, title: str) -> WindowTarget:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError(f"GetClientRect failed for hwnd={hwnd}")
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise RuntimeError(f"ClientToScreen failed for hwnd={hwnd}")
    return WindowTarget(
        hwnd=int(hwnd),
        title=title,
        left=int(origin.x),
        top=int(origin.y),
        width=int(rect.right - rect.left),
        height=int(rect.bottom - rect.top),
    )


def list_windows(title_substr: str) -> list[WindowTarget]:
    found: list[WindowTarget] = []
    needle = title_substr.lower()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if needle not in title.lower():
            return True
        found.append(_client_target(hwnd, title))
        return True

    user32.EnumWindows(_enum, 0)
    return found


def find_window(
    title_substr: str,
    *,
    prefer_hwnd: int | None = None,
    exclude_hwnds: set[int] | None = None,
) -> WindowTarget | None:
    matches = list_windows(title_substr)
    if prefer_hwnd is not None:
        for target in matches:
            if target.hwnd == prefer_hwnd:
                return target
    if exclude_hwnds:
        matches = [target for target in matches if target.hwnd not in exclude_hwnds]
    if not matches:
        return None
    fg = int(user32.GetForegroundWindow())
    for target in matches:
        if target.hwnd == fg:
            return target
    return matches[0]


def require_window(
    title_substr: str,
    *,
    prefer_hwnd: int | None = None,
    exclude_hwnds: set[int] | None = None,
) -> WindowTarget:
    target = find_window(title_substr, prefer_hwnd=prefer_hwnd, exclude_hwnds=exclude_hwnds)
    if target is None:
        raise RuntimeError(
            f"No visible window with title containing {title_substr!r}. "
            "Ensure the application is running and its window is visible."
        )
    return target


def wait_for_window(
    title_substr: str,
    timeout_s: float = 15.0,
    *,
    exclude_hwnds: set[int] | None = None,
) -> WindowTarget:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        found = find_window(title_substr, exclude_hwnds=exclude_hwnds)
        if found is not None and found.width > 0 and found.height > 0:
            return found
        time.sleep(0.2)
    return require_window(title_substr, exclude_hwnds=exclude_hwnds)


def defocus_to_console() -> None:
    """Activate the console so TTY prompts are not swallowed by the game."""
    hwnd = int(kernel32.GetConsoleWindow() or 0)
    if hwnd:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)


def focus_window(target: WindowTarget) -> bool:
    """Force the game client to the foreground. Windows often blocks SetForegroundWindow."""
    hwnd = int(target.hwnd)
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)
    try:
        user32.AllowSetForegroundWindow(-1)
    except OSError:
        pass

    def _here() -> bool:
        return int(user32.GetForegroundWindow()) == hwnd

    if _here():
        return True

    for attempt in range(4):
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE,
        )
        user32.SetWindowPos(
            hwnd,
            HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )
        user32.BringWindowToTop(hwnd)
        foreground = int(user32.GetForegroundWindow() or 0)
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        current_thread = kernel32.GetCurrentThreadId()
        attached_fg = False
        attached_cur = False
        if foreground and foreground != hwnd:
            attached_fg = bool(user32.AttachThreadInput(foreground_thread, target_thread, True))
        attached_cur = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            if attempt >= 1:
                user32.keybd_event(VK_MENU, 0, 0, 0)
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached_cur:
                user32.AttachThreadInput(current_thread, target_thread, False)
            if attached_fg:
                user32.AttachThreadInput(foreground_thread, target_thread, False)
        time.sleep(0.05)
        if _here():
            return True
    return _here()


def focus_window_and_settle(
    target: WindowTarget, *, timeout_s: float = 0.5, poll_s: float = 0.02
) -> bool:
    """Focus exactly ``target.hwnd`` and wait until that handle is foreground.

    This is intentionally a one-shot acquisition helper.  Capture loops must use
    :func:`is_foreground` afterwards and fail closed; they must not call this
    helper to recover focus in the middle of a run.
    """

    if not focus_window(target):
        return False
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if is_foreground(target):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.001, float(poll_s)))


def is_foreground(target: WindowTarget) -> bool:
    return int(user32.GetForegroundWindow()) == target.hwnd


def _process_exe(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        query.restype = wintypes.BOOL
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if query(handle, 0, buf, ctypes.byref(size)):
            path = buf.value.replace("/", "\\")
            return path.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def target_identity(target: WindowTarget, *, selector: str = "") -> dict:
    """Stable process/window identity. hwnd/pid bind the current instance only."""

    pid = wintypes.DWORD(0)
    try:
        user32.GetWindowThreadProcessId(int(target.hwnd), ctypes.byref(pid))
    except OSError:
        pid = wintypes.DWORD(0)
    class_buf = ctypes.create_unicode_buffer(256)
    try:
        user32.GetClassNameW(int(target.hwnd), class_buf, 256)
        window_class = class_buf.value
    except OSError:
        window_class = ""
    return {
        "selector": str(selector),
        "exe": _process_exe(int(pid.value)),
        "window_class": window_class,
        "hwnd": int(target.hwnd),
        "pid": int(pid.value),
        "title": str(target.title),
        "client_size": [int(target.width), int(target.height)],
    }


def foreground_title() -> str:
    hwnd = int(user32.GetForegroundWindow())
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def client_frac_to_px(nx: float, ny: float, width: int, height: int) -> tuple[int, int]:
    """Map [0,1] onto the last pixel of the client so 0.5 is the true center."""
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    return (
        int(round(nx * max(width - 1, 0))),
        int(round(ny * max(height - 1, 0))),
    )


def client_to_screen(target: WindowTarget, nx: float, ny: float) -> tuple[int, int]:
    client_x, client_y = client_frac_to_px(nx, ny, int(target.width), int(target.height))
    point = wintypes.POINT(client_x, client_y)
    user32.ClientToScreen(target.hwnd, ctypes.byref(point))
    return int(point.x), int(point.y)


def _luma_mean(image: Image.Image) -> float:
    return float(ImageStat.Stat(image.convert("L")).mean[0])


@dataclass(frozen=True)
class CaptureProvenance:
    """How a raw client grab was sourced and whether it may bind click geometry."""

    source: str
    client_size: tuple[int, int]
    content_box: ContentBox
    integrity: str
    reason: str
    hwnd_box: ContentBox | None = None
    mss_box: ContentBox | None = None
    # The target instance is part of the evidence, not inferred from the
    # caller.  These remain optional for benchmark/unit fixtures that construct
    # provenance directly.
    pinned_hwnd: int | None = None
    identity_before: dict[str, Any] | None = None
    identity_after: dict[str, Any] | None = None
    foreground_before: int | None = None
    foreground_after: int | None = None
    # Truthful acquisition bound.  ``acquisition_ts_monotonic`` is only set
    # when it actually bounds the frame age; ``acquisition_age_known`` is the
    # authority bit callers must check before any actuation.  Unknown age is
    # usable for observation and fails closed for input.
    acquisition_ts_monotonic: float | None = None
    acquisition_origin: str = ACQ_ORIGIN_UNKNOWN
    acquisition_age_known: bool = False
    # When the frame was requested.  Diagnostic only: content may predate it,
    # so it is not an acquisition bound and must not be used as one.
    grab_requested_ts_monotonic: float | None = None


def classify_frame_integrity(image: Image.Image | None) -> tuple[str, str, ContentBox]:
    """Classify a raw client bitmap: trusted, suspect (top-left scaled), or empty."""
    if image is None:
        return "empty", "no-frame", _IDENTITY_BOX
    if min(image.size) <= 0:
        return "empty", "empty-size", _IDENTITY_BOX
    box = content_box_for(image)
    if _luma_mean(image) <= _BLACK_LUMA:
        return "empty", "black", box
    if is_top_left_scaled_capture(image):
        return "suspect", "top-left-scaled", box
    return "trusted", "ok", box


def _size_matches(image: Image.Image, client_size: tuple[int, int], *, slack: int = 2) -> bool:
    return abs(image.size[0] - client_size[0]) <= slack and abs(image.size[1] - client_size[1]) <= slack


def select_client_capture(
    hwnd_image: Image.Image | None,
    mss_image: Image.Image | None,
    *,
    client_size: tuple[int, int],
    hwnd_source: str = "hwnd-imagegrab",
) -> tuple[Image.Image, CaptureProvenance]:
    """Select an HWND frame; MSS is diagnostic-only and never trusted.

    Desktop-coordinate capture can contain another window, compositor damage,
    or stale pixels even when its dimensions match the client.  It may be
    returned to aid diagnostics, but its integrity is deliberately uncertain.
    """
    hwnd_state, hwnd_reason, hwnd_box = classify_frame_integrity(hwnd_image)
    mss_state, mss_reason, mss_box = classify_frame_integrity(mss_image)
    if hwnd_image is not None and hwnd_state == "trusted":
        return hwnd_image, CaptureProvenance(
            source=hwnd_source,
            client_size=client_size,
            content_box=hwnd_box,
            integrity="trusted",
            reason=hwnd_reason,
            hwnd_box=hwnd_box,
            mss_box=mss_box if mss_image is not None else None,
        )
    # A desktop grab may still be useful for an operator diagnostic, but never
    # upgrades a frame to trusted and never becomes click geometry.
    fallback = hwnd_image if hwnd_image is not None else mss_image
    if fallback is None:
        raise RuntimeError("Client grab is empty; restore the windowed game.")
    return fallback, CaptureProvenance(
        source="uncertain",
        client_size=client_size,
        content_box=content_box_for(fallback),
        integrity="uncertain",
        reason=f"hwnd-{hwnd_reason};mss-{mss_reason}",
        hwnd_box=hwnd_box if hwnd_image is not None else None,
        mss_box=mss_box if mss_image is not None else None,
    )


def _sync_client(target: WindowTarget) -> WindowTarget:
    """Re-read client rect from hwnd so windowed resize never becomes a monitor grab."""
    if not target.hwnd or not user32.IsWindow(target.hwnd):
        return target
    return _client_target(target.hwnd, target.title)


def grab_hwnd_client(target: WindowTarget) -> Image.Image | None:
    """Copy this HWND's client pixels (not the desktop, not overlapping windows)."""
    width, height = int(target.width), int(target.height)
    if width <= 0 or height <= 0:
        return None
    hwnd = int(target.hwnd)
    hdc_win = user32.GetDC(hwnd)
    if not hdc_win:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, width, height)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        flags = PW_CLIENTONLY | PW_RENDERFULLCONTENT
        ok = bool(user32.PrintWindow(hwnd, hdc_mem, flags))
        if not ok:
            ok = bool(gdi32.BitBlt(hdc_mem, 0, 0, width, height, hdc_win, 0, 0, SRCCOPY))
        if not ok:
            return None
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if copied == 0:
            return None
        return Image.frombuffer("RGB", (width, height), bytes(buf), "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd, hdc_win)


def _capture_window_rect(hwnd: int) -> wintypes.RECT | None:
    """Return the compositor-visible bounds used by HWND capture APIs."""

    rect = wintypes.RECT()
    try:
        dwmapi = ctypes.windll.dwmapi
        get_attribute = dwmapi.DwmGetWindowAttribute
        get_attribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        get_attribute.restype = ctypes.c_long
        # DWMWA_EXTENDED_FRAME_BOUNDS excludes invisible resize borders and
        # matches the surface dimensions returned by WGC/ImageGrab.
        if get_attribute(
            int(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0:
            return rect
    except (AttributeError, OSError):
        pass
    if user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return rect
    return None


def grab_hwnd_imagegrab(target: WindowTarget) -> Image.Image | None:
    """Capture one HWND with Pillow's native Windows ImageGrab window mode.

    This is an HWND-bound backend available in the existing dependency set. It
    is deliberately named after the API used; it is not a claim that the
    optional Windows.Graphics.Capture WinRT API is installed or active.
    Unlike MSS this path does not read desktop coordinates.
    """

    if not target.hwnd or not user32.IsWindow(int(target.hwnd)):
        return None
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab(window=int(target.hwnd))
    except (AttributeError, OSError, RuntimeError):
        return None
    if image is None:
        return None
    image = image.convert("RGB")
    wanted = (int(target.width), int(target.height))
    if image.size == wanted:
        return image

    # Window capture may include non-client chrome.  Crop using the exact
    # screen-space client origin relative to the captured window rectangle.
    window_rect = _capture_window_rect(int(target.hwnd))
    if window_rect is None:
        return None
    left = int(target.left) - int(window_rect.left)
    top = int(target.top) - int(window_rect.top)
    right = left + wanted[0]
    bottom = top + wanted[1]
    if left < 0 or top < 0 or right > image.width or bottom > image.height:
        return None
    cropped = image.crop((left, top, right, bottom))
    return cropped if cropped.size == wanted else None


def _wgc_frame_image(frame: Any) -> Image.Image | None:
    """Copy one ``windows-capture`` BGRA frame while its native mapping is live."""
    try:
        import numpy as np

        pixels = np.asarray(getattr(frame, "frame_buffer"))
        height = int(getattr(frame, "height", pixels.shape[0]))
        width = int(getattr(frame, "width", pixels.shape[1]))
        if height <= 0 or width <= 0 or pixels.ndim != 3 or pixels.shape[2] < 3:
            return None
        pixels = pixels[:height, :width]
        if pixels.dtype != np.uint8:
            pixels = pixels.astype(np.uint8, copy=False)
        # Windows Graphics Capture exposes BGRA8.  Drop alpha and copy so the
        # image no longer depends on the callback's mapped-frame lifetime.
        rgb = pixels[..., 2::-1]
        return Image.fromarray(np.ascontiguousarray(rgb), mode="RGB").copy()
    except Exception:
        return None


def _stop_wgc(control: Any, timeout_s: float) -> None:
    """Stop a free-threaded capture without allowing cleanup to block capture."""
    if control is None:
        return
    try:
        control.stop()
    except Exception:
        return
    wait = getattr(control, "wait", None)
    if not callable(wait):
        return
    # The package's wait() has no timeout.  Keep it off the capture caller's
    # thread so a broken native session cannot turn one-frame capture into an
    # unbounded operation.  Normal sessions finish immediately after stop().
    waiter = threading.Thread(target=wait, name="wgc-stop", daemon=True)
    waiter.start()
    waiter.join(timeout=max(0.01, min(0.25, float(timeout_s))))


def grab_hwnd_wgc(target: WindowTarget, *, timeout_s: float = 0.75) -> Image.Image | None:
    """Capture one exact HWND through Windows.Graphics.Capture.

    ``windows-capture`` exposes an event-driven API, so this is intentionally a
    bounded one-frame adapter around ``start_free_threaded``.  The native frame
    is copied in the callback, then the session is stopped and cleaned up.  A
    missing package, unsupported Windows build, native error, timeout, or bad
    frame all fail closed and let the HWND-bound fallbacks run.
    """
    if not target.hwnd or int(target.width) <= 0 or int(target.height) <= 0:
        return None
    timeout_s = max(0.01, float(timeout_s))
    try:
        from windows_capture import WindowsCapture
    except (ImportError, OSError, RuntimeError):
        return None

    arrived = threading.Event()
    result: list[Image.Image] = []
    callback_lock = threading.Lock()
    control: Any = None
    completed = False

    def on_frame_arrived(frame: Any, capture_control: Any) -> None:
        nonlocal control
        image = _wgc_frame_image(frame)
        with callback_lock:
            if arrived.is_set():
                return
            if image is not None:
                result.append(image)
            arrived.set()
        # Stop from inside the callback as recommended by windows-capture.  A
        # second stop in the caller is harmless and handles timeout/close.
        control = capture_control
        try:
            capture_control.stop()
        except Exception:
            pass

    def on_closed() -> None:
        arrived.set()

    try:
        capture = WindowsCapture(
            cursor_capture=False,
            draw_border=False,
            window_hwnd=int(target.hwnd),
        )
        capture.event(on_frame_arrived)
        capture.event(on_closed)
        control = capture.start_free_threaded()
        completed = arrived.wait(timeout_s)
    except Exception:
        return None
    finally:
        _stop_wgc(control, timeout_s)

    if not completed:
        return None
    image = result[0] if result else None
    wanted = (int(target.width), int(target.height))
    if image is None:
        return None
    if image.size == wanted:
        return image

    # WGC captures the complete top-level window, including non-client chrome.
    # Convert the pinned client origin into window-relative coordinates so the
    # trusted frame uses the same client-only geometry as input binding.
    window_rect = _capture_window_rect(int(target.hwnd))
    if window_rect is None:
        return None
    left = int(target.left) - int(window_rect.left)
    top = int(target.top) - int(window_rect.top)
    right = left + wanted[0]
    bottom = top + wanted[1]
    if left < 0 or top < 0 or right > image.width or bottom > image.height:
        return None
    cropped = image.crop((left, top, right, bottom))
    return cropped if cropped.size == wanted else None


def _grab_mss_client(target: WindowTarget, sct=None) -> Image.Image:
    """Fallback: screen pixels inside the client rect only — never a full monitor."""
    if sct is None:
        import mss

        sct = mss.mss()
    bbox = target.bbox()
    if bbox["width"] <= 0 or bbox["height"] <= 0:
        raise RuntimeError("Game client rect is empty; restore the windowed game.")
    shot = sct.grab(
        {
            "left": int(bbox["left"]),
            "top": int(bbox["top"]),
            "width": int(bbox["width"]),
            "height": int(bbox["height"]),
        }
    )
    return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)


def grab_client(target: WindowTarget, sct=None) -> Image.Image:
    image, _prov = grab_client_capture(target, sct)
    return image


def grab_client_capture(target: WindowTarget, sct=None) -> tuple[Image.Image, CaptureProvenance]:
    """Capture this exact HWND and retain provenance for the pinned instance."""
    target = _sync_client(target)
    client_size = (int(target.width), int(target.height))
    foreground_before = int(user32.GetForegroundWindow() or 0)
    identity_before = target_identity(target)
    # Graphics Capture is the first trusted source: it binds directly to the
    # pinned HWND and does not depend on desktop-coordinate pixels.  Its frame
    # arrives through an asynchronous native callback, so a timestamp taken
    # here cannot prove a previously buffered frame is fresh.  Record the
    # request time for diagnostics only and leave the age unknown.
    # ponytail: WGC age stays unknown until a pinned backend exposes a verified
    # frame time; re-enable actuation by converting that timestamp, not this one.
    hwnd_request_ts = time.monotonic()
    hwnd_image = grab_hwnd_wgc(target)
    hwnd_source = "hwnd-wgc"
    hwnd_acq_ts: float | None = None
    hwnd_acq_origin = ACQ_ORIGIN_BUFFERED_UNKNOWN
    hwnd_acq_known = False
    if classify_frame_integrity(hwnd_image)[0] != "trusted":
        hwnd_request_ts = time.monotonic()
        hwnd_image = grab_hwnd_imagegrab(target)
        hwnd_source = "hwnd-imagegrab"
        hwnd_acq_ts = hwnd_request_ts
        hwnd_acq_origin = ACQ_ORIGIN_PRE_GRAB_SYNC
        hwnd_acq_known = True
    if classify_frame_integrity(hwnd_image)[0] != "trusted":
        # PrintWindow remains a useful HWND-bound fallback for ordinary windows.
        hwnd_request_ts = time.monotonic()
        hwnd_image = grab_hwnd_client(target)
        hwnd_source = "hwnd-printwindow"
        hwnd_acq_ts = hwnd_request_ts
        hwnd_acq_origin = ACQ_ORIGIN_PRE_GRAB_SYNC
        hwnd_acq_known = True
    hwnd_state, _, _ = classify_frame_integrity(hwnd_image)
    mss_image = None
    mss_acq_ts: float | None = None
    mss_request_ts: float | None = None
    mss_acq_origin = ACQ_ORIGIN_UNKNOWN
    mss_acq_known = False
    if hwnd_image is None or hwnd_state != "trusted":
        mss_request_ts = time.monotonic()
        try:
            mss_image = _grab_mss_client(target, sct)
        except RuntimeError:
            mss_image = None
        mss_acq_ts = mss_request_ts
        mss_acq_origin = ACQ_ORIGIN_PRE_GRAB_SYNC
        mss_acq_known = True
    image, provenance = select_client_capture(
        hwnd_image,
        mss_image,
        client_size=client_size,
        hwnd_source=hwnd_source,
    )
    if mss_image is not None and image is mss_image:
        acq_ts, acq_origin, acq_known = mss_acq_ts, mss_acq_origin, mss_acq_known
        request_ts = mss_request_ts
    else:
        acq_ts, acq_origin, acq_known = hwnd_acq_ts, hwnd_acq_origin, hwnd_acq_known
        request_ts = hwnd_request_ts
    identity_after = target_identity(target)
    foreground_after = int(user32.GetForegroundWindow() or 0)
    # A frame is only trusted if the exact pinned HWND remained the foreground
    # instance throughout the capture.  The caller turns this into a hard
    # observation failure; no schema or ledger work happens first.
    exact_identity = (
        int(identity_before.get("hwnd") or 0) == int(target.hwnd)
        and int(identity_after.get("hwnd") or 0) == int(target.hwnd)
        and (
            not identity_before.get("pid")
            or not identity_after.get("pid")
            or int(identity_before.get("pid")) == int(identity_after.get("pid"))
        )
        and foreground_before == int(target.hwnd)
        and foreground_after == int(target.hwnd)
    )
    if provenance.integrity == "trusted" and not exact_identity:
        provenance = replace(
            provenance,
            integrity="uncertain",
            reason=f"{provenance.reason};focus-or-identity-changed",
        )
    provenance = replace(
        provenance,
        pinned_hwnd=int(target.hwnd),
        identity_before=identity_before,
        identity_after=identity_after,
        foreground_before=foreground_before,
        foreground_after=foreground_after,
        acquisition_ts_monotonic=acq_ts,
        acquisition_origin=acq_origin,
        acquisition_age_known=acq_known,
        grab_requested_ts_monotonic=request_ts,
    )
    return image, provenance


def grab_client_tracked(target: WindowTarget, sct=None) -> tuple[Image.Image, str]:
    """Client of this HWND only. Prefer PrintWindow so overlays on the desktop are not in the crop."""
    image, provenance = grab_client_capture(target, sct)
    return image, provenance.source


class WindowCapture:
    def __init__(
        self,
        title_substr: str,
        *,
        encoder: FrameEncoder | None = None,
        skip_unchanged: bool | None = None,
        profile: CaptureProfile | None = None,
        hwnd: int | None = None,
    ) -> None:
        import mss

        self._title_substr = title_substr
        self._pinned_hwnd = hwnd
        self.profile = profile or CaptureProfile()
        self._encoder = encoder or FrameEncoder(
            longest_side=self.profile.current.longest_side,
            jpeg_quality=self.profile.current.jpeg_quality,
            fmt=self.profile.fmt,
        )
        self._skip_unchanged = (
            self.profile.skip_unchanged if skip_unchanged is None else skip_unchanged
        )
        self._sct = mss.mss()
        self._target = require_window(title_substr, prefer_hwnd=hwnd)
        self._pinned_hwnd = self._target.hwnd
        self._last_thumb: Image.Image | None = None
        self._last_frame: EncodedFrame | None = None
        self.last_meta = GrabMeta()
        self.last_settle: SettleResult | None = None
        self.last_source = "hwnd"
        self.last_provenance: CaptureProvenance | None = None
        self.last_geometry: ScreenGeometry | None = None
        self._grab_lock = threading.Lock()

    @property
    def target(self) -> WindowTarget:
        return self._target

    def refresh(self) -> WindowTarget:
        prev = (self._target.width, self._target.height)
        self._target = require_window(self._title_substr, prefer_hwnd=self._pinned_hwnd)
        self._pinned_hwnd = self._target.hwnd
        if (self._target.width, self._target.height) != prev:
            self._last_frame = None
            self._last_thumb = None
        return self._target

    def focus(self) -> None:
        focus_window(self._target)

    def is_foreground(self) -> bool:
        return is_foreground(self._target)

    def grab_image(self, *, full: bool = False) -> Image.Image:
        with self._grab_lock:
            if self._pinned_hwnd and user32.IsWindow(self._pinned_hwnd):
                self._target = _client_target(self._pinned_hwnd, self._target.title)
            else:
                self.refresh()
            if not is_foreground(self._target):
                raise RuntimeError(
                    f"target lost foreground hwnd={int(self._target.hwnd)}; refusing capture"
                )
            image, provenance = grab_client_capture(self._target, self._sct)
            if provenance.integrity != "trusted" or provenance.source not in {
                "hwnd-wgc",
                "hwnd-imagegrab",
                "hwnd-printwindow",
            }:
                raise RuntimeError(
                    f"untrusted capture: {provenance.source}/{provenance.reason}"
                )
            self.last_source = provenance.source
            self.last_provenance = provenance
            self._target = _sync_client(self._target)
            self.last_geometry = geometry_from_image(
                image,
                client=(self._target.width, self._target.height),
            )
            if full:
                return image
            return apply_crop(image, self.profile.current.crop)

    def grab(self, *, full: bool = False, fresh: bool = True) -> tuple[EncodedFrame, bool]:
        image = self.grab_image(full=full)
        thumb = roi_thumb(image, self.profile.diff_size)
        changed = True
        if self._last_thumb is not None:
            changed = mean_absdiff(self._last_thumb, thumb) > self.profile.diff_threshold
        side = perceive_longest_side(image, self.profile.current.longest_side)
        encoder = FrameEncoder(
            longest_side=side,
            jpeg_quality=self.profile.current.jpeg_quality,
            fmt=self.profile.fmt,
        )
        frame = encoder.encode(image)
        if not full:
            self._last_frame = frame
            self._last_thumb = thumb
        self.last_meta = GrabMeta(
            skipped_jpeg=False,
            changed=changed,
            preset=self.profile.active,
            crop=None if full else self.profile.current.crop,
            full=full,
        )
        return frame, False

    def wait_settled(self) -> SettleResult:
        result = poll_until_settled(
            lambda: roi_thumb(self.grab_image(full=False), self.profile.diff_size),
            threshold=self.profile.diff_threshold,
            timeout_ms=400,
            poll_ms=16,
            stable_ms=50,
        )
        self.last_settle = result
        return result
