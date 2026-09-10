from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

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


def is_foreground(target: WindowTarget) -> bool:
    return int(user32.GetForegroundWindow()) == target.hwnd


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
) -> tuple[Image.Image, CaptureProvenance]:
    """Prefer a healthy HWND frame; fall back to MSS for black or top-left-scaled HWND."""
    hwnd_state, hwnd_reason, hwnd_box = classify_frame_integrity(hwnd_image)
    mss_state, mss_reason, mss_box = classify_frame_integrity(mss_image)
    mss_ok = (
        mss_image is not None
        and mss_state == "trusted"
        and _size_matches(mss_image, client_size)
    )
    if hwnd_image is not None and hwnd_state == "trusted":
        return hwnd_image, CaptureProvenance(
            source="hwnd",
            client_size=client_size,
            content_box=hwnd_box,
            integrity="trusted",
            reason=hwnd_reason,
            hwnd_box=hwnd_box,
            mss_box=mss_box if mss_image is not None else None,
        )
    if hwnd_image is None or hwnd_state == "empty":
        if mss_ok and mss_image is not None:
            return mss_image, CaptureProvenance(
                source="mss-client",
                client_size=client_size,
                content_box=mss_box,
                integrity="trusted",
                reason=f"hwnd-{hwnd_reason}",
                hwnd_box=hwnd_box if hwnd_image is not None else None,
                mss_box=mss_box,
            )
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
    # hwnd is suspect (top-left scaled PrintWindow)
    if mss_ok and mss_image is not None:
        return mss_image, CaptureProvenance(
            source="mss-client-integrity",
            client_size=client_size,
            content_box=mss_box,
            integrity="trusted",
            reason="hwnd-top-left-scaled",
            hwnd_box=hwnd_box,
            mss_box=mss_box,
        )
    fallback = hwnd_image if hwnd_image is not None else mss_image
    if fallback is None:
        raise RuntimeError("Client grab is empty; restore the windowed game.")
    return fallback, CaptureProvenance(
        source="uncertain",
        client_size=client_size,
        content_box=content_box_for(fallback),
        integrity="uncertain",
        reason=f"hwnd-{hwnd_reason};mss-{mss_reason}",
        hwnd_box=hwnd_box,
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
    """Client of this HWND only. Prefer PrintWindow; reject top-left-scaled bitmaps."""
    target = _sync_client(target)
    client_size = (int(target.width), int(target.height))
    hwnd_image = grab_hwnd_client(target)
    hwnd_state, _, _ = classify_frame_integrity(hwnd_image)
    mss_image = None
    if hwnd_image is None or hwnd_state != "trusted":
        try:
            mss_image = _grab_mss_client(target, sct)
        except RuntimeError:
            mss_image = None
    return select_client_capture(hwnd_image, mss_image, client_size=client_size)


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
            image, provenance = grab_client_capture(self._target, self._sct)
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
