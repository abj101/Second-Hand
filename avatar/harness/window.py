from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from avatar.harness.capture import EncodedFrame, FrameEncoder, encode_image
from avatar.harness.profile import (
    CapturePreset,
    CaptureProfile,
    GrabMeta,
    SettleResult,
    apply_crop,
    mean_absdiff,
    poll_until_settled,
    roi_thumb,
    use_cached_jpeg,
)

# Windows constants
SW_RESTORE = 9
WM_CLOSE = 0x0010
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_dpi_aware = False
_sct: object | None = None


def ensure_dpi_aware() -> None:
    """Enable per-monitor DPI awareness (v2) once per process."""
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
    """Visible top-level windows whose titles contain *title_substr* (Z-order)."""
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


def close_windows(title_substr: str, *, timeout_s: float = 3.0) -> int:
    """Ask matching top-level windows to close. Returns how many we tried to close."""
    targets = list_windows(title_substr)
    for target in targets:
        user32.PostMessageW(target.hwnd, WM_CLOSE, 0, 0)
    if not targets:
        return 0
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        leftover = list_windows(title_substr)
        if not leftover:
            break
        time.sleep(0.15)
    still = list_windows(title_substr)
    for target in still:
        user32.PostMessageW(target.hwnd, WM_CLOSE, 0, 0)
    return len(targets)


def find_window(
    title_substr: str,
    *,
    prefer_hwnd: int | None = None,
    exclude_hwnds: set[int] | None = None,
) -> WindowTarget | None:
    """Find a visible top-level window whose title contains *title_substr*."""
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


def focus_window(target: WindowTarget) -> None:
    hwnd = target.hwnd
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    if user32.SetForegroundWindow(hwnd):
        return
    if user32.GetForegroundWindow() == hwnd:
        return

    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()

    user32.AttachThreadInput(foreground_thread, target_thread, True)
    user32.AttachThreadInput(current_thread, target_thread, True)
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(foreground_thread, target_thread, False)
        user32.AttachThreadInput(current_thread, target_thread, False)


def is_foreground(target: WindowTarget) -> bool:
    return int(user32.GetForegroundWindow()) == target.hwnd


def client_to_screen(target: WindowTarget, nx: float, ny: float) -> tuple[int, int]:
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    client_x = int(nx * target.width)
    client_y = int(ny * target.height)
    point = wintypes.POINT(client_x, client_y)
    user32.ClientToScreen(target.hwnd, ctypes.byref(point))
    return int(point.x), int(point.y)


def _get_sct():
    global _sct
    if _sct is None:
        import mss

        _sct = mss.mss()
    return _sct


def grab_client(target: WindowTarget, sct=None) -> Image.Image:
    """Grab the client area as an RGB PIL image."""
    if sct is None:
        sct = _get_sct()
    bbox = target.bbox()
    shot = sct.grab(bbox)
    return Image.frombytes("RGB", (shot.width, shot.height), shot.rgb)


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
        self._encoder = encoder or self._encoder_for(full=False)
        self._skip_unchanged = (
            self.profile.skip_unchanged if skip_unchanged is None else skip_unchanged
        )
        self._sct = mss.mss()
        self._target = require_window(title_substr, prefer_hwnd=hwnd)
        self._pinned_hwnd = self._target.hwnd
        self._last_thumb: Image.Image | None = None
        self._last_frame: EncodedFrame | None = None
        self.skip_streak = 0
        self.last_meta = GrabMeta()
        self.last_settle: SettleResult | None = None
        self.layout_plan = None
        self._layout_ready = False

    def _encoder_for(self, *, full: bool) -> FrameEncoder:
        preset = self.profile.current
        if full:
            return FrameEncoder(
                longest_side=self.profile.scout_longest_side,
                jpeg_quality=self.profile.jpeg_quality,
                fmt=self.profile.fmt,
            )
        return FrameEncoder(
            longest_side=preset.longest_side,
            jpeg_quality=preset.jpeg_quality,
            fmt=self.profile.fmt,
        )

    def select_preset(self, screen_kind: str) -> None:
        prev = self.profile.active
        self.profile.select(screen_kind)
        if self.profile.active != prev:
            self._encoder = self._encoder_for(full=False)
            self._last_frame = None
            self._last_thumb = None

    def _fallback_preset(self) -> CapturePreset:
        return (
            self.profile.yaml_gameplay
            or self.profile.presets.get("gameplay")
            or CapturePreset(jpeg_quality=self.profile.jpeg_quality)
        )

    def diagnose_and_apply(
        self,
        image: Image.Image | None = None,
        *,
        scout_dir: Path | None = None,
        scout_name: str = "scout",
    ):
        """Invent (or restore YAML) gameplay crop from a full-window grab."""
        from avatar.harness.layout import LayoutPlan, diagnose_layout

        if image is None:
            image = self.grab_image(full=True)
        fallback = self._fallback_preset()
        if self.profile.layout_mode != "cv":
            side = fallback.longest_side
            plan = LayoutPlan(
                crop=fallback.crop,
                longest_side=int(side) if isinstance(side, int) else 512,
                jpeg_quality=fallback.jpeg_quality,
                screen_kind="gameplay",
                source="yaml",
            )
        else:
            plan = diagnose_layout(
                image,
                fallback=fallback,
                pad=self.profile.layout_pad,
                sides=self.profile.layout_sides,
            )
        self.profile.apply_gameplay_layout(plan)
        self.layout_plan = plan
        self._layout_ready = True
        if scout_dir is not None:
            from avatar.harness.scout import persist_layout_scout

            persist_layout_scout(
                Path(scout_dir),
                image,
                plan,
                self.profile,
                name=scout_name,
            )
        if self.profile.active == "gameplay":
            self._encoder = self._encoder_for(full=False)
            self._last_frame = None
            self._last_thumb = None
        return plan

    def refresh_layout(self, image: Image.Image | None = None) -> bool:
        """Re-run CV; adopt the new box when IoU vs current gameplay crop is low."""
        from avatar.harness.layout import crop_iou, diagnose_layout

        if self.profile.layout_mode != "cv":
            return False
        if image is None:
            image = self.grab_image(full=True)
        fallback = self._fallback_preset()
        plan = diagnose_layout(
            image,
            fallback=fallback,
            pad=self.profile.layout_pad,
            sides=self.profile.layout_sides,
        )
        prev = self.profile.presets.get("gameplay")
        prev_crop = None if prev is None else prev.crop
        if prev_crop is not None and crop_iou(prev_crop, plan.crop) >= self.profile.layout_iou_refresh:
            return False
        self.profile.apply_gameplay_layout(plan)
        self.layout_plan = plan
        self._layout_ready = True
        if self.profile.active == "gameplay":
            self._encoder = self._encoder_for(full=False)
            self._last_frame = None
            self._last_thumb = None
        return True

    @property
    def layout_ready(self) -> bool:
        return self._layout_ready

    @property
    def target(self) -> WindowTarget:
        return self._target

    def refresh(self) -> WindowTarget:
        prev = (self._target.width, self._target.height)
        self._target = require_window(self._title_substr, prefer_hwnd=self._pinned_hwnd)
        self._pinned_hwnd = self._target.hwnd
        if (self._target.width, self._target.height) != prev:
            self._layout_ready = False
            self._last_frame = None
            self._last_thumb = None
        return self._target

    def focus(self) -> None:
        focus_window(self._target)

    def is_foreground(self) -> bool:
        return is_foreground(self._target)

    def grab_image(self, *, full: bool = False) -> Image.Image:
        """Client grab. Crop is vision-only; pass full=True to skip it."""
        prev = (self._target.width, self._target.height)
        if self._pinned_hwnd:
            try:
                self._target = _client_target(self._pinned_hwnd, self._target.title)
            except RuntimeError:
                self.refresh()
        else:
            self.refresh()
        if (self._target.width, self._target.height) != prev:
            self._layout_ready = False
            self._last_frame = None
            self._last_thumb = None
        image = grab_client(self._target, self._sct)
        if full:
            return image
        return apply_crop(image, self.profile.current.crop)

    def grab_roi_thumb(self) -> Image.Image:
        return roi_thumb(self.grab_image(full=False), self.profile.diff_size)

    def grab_scout(self) -> EncodedFrame:
        """Uncropped client at scout_longest_side. Does not update skip cache."""
        image = self.grab_image(full=True)
        return encode_image(
            image,
            longest_side=self.profile.scout_longest_side,
            jpeg_quality=self.profile.jpeg_quality,
            fmt=self.profile.fmt,
        )

    def grab(self, *, full: bool = False, fresh: bool = False) -> tuple[EncodedFrame, bool]:
        image = self.grab_image(full=full)
        thumb = roi_thumb(image, self.profile.diff_size)
        changed = True
        if self._last_thumb is not None:
            changed = mean_absdiff(self._last_thumb, thumb) > self.profile.diff_threshold
        skipped_jpeg = False
        if use_cached_jpeg(
            skip_unchanged=self._skip_unchanged,
            changed=changed,
            has_last=self._last_frame is not None,
            fresh=fresh or full,
        ):
            cached = self._last_frame
            assert cached is not None
            frame = cached
            skipped_jpeg = True
        else:
            encoder = self._encoder_for(full=full)
            frame = encoder.encode(image)
            if not full:
                self._last_frame = frame
                self._encoder = encoder
        if not full:
            self._last_thumb = thumb
        self.last_meta = GrabMeta(
            skipped_jpeg=skipped_jpeg,
            changed=changed,
            preset=self.profile.active,
            crop=None if full else self.profile.current.crop,
            full=full,
            layout_source=None if self.layout_plan is None else getattr(self.layout_plan, "source", None),
        )
        return frame, skipped_jpeg

    def wait_settled(self) -> SettleResult:
        result = poll_until_settled(
            self.grab_roi_thumb,
            threshold=self.profile.diff_threshold,
            timeout_ms=self.profile.settle_timeout_ms,
            poll_ms=self.profile.settle_poll_ms,
            stable_ms=self.profile.settle_stable_ms,
        )
        self.last_settle = result
        if result.changed:
            self._last_frame = None
            self._last_thumb = None
        return result
