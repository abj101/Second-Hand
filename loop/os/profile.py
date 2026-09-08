from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

from PIL import Image, ImageChops, ImageStat

LongestSide = int | Literal["native"]
CropRect = tuple[float, float, float, float]


@dataclass(frozen=True)
class CapturePreset:
    crop: CropRect | None = None
    longest_side: LongestSide = 768
    jpeg_quality: int = 85


@dataclass
class CaptureProfile:
    """Pixel budget and vision crop. Crop is vision-only; pad stays full-client."""

    jpeg_quality: int = 85
    fmt: str = "jpeg"
    diff_size: int = 64
    diff_threshold: float = 0.02
    skip_unchanged: bool = False
    presets: dict[str, CapturePreset] = field(default_factory=dict)
    active: str = "gameplay"

    def __post_init__(self) -> None:
        if not self.presets:
            self.presets = {
                "gameplay": CapturePreset(longest_side=768, jpeg_quality=self.jpeg_quality),
                "ui": CapturePreset(crop=None, longest_side=768, jpeg_quality=self.jpeg_quality),
            }
        if self.active not in self.presets:
            self.active = next(iter(self.presets))

    @property
    def current(self) -> CapturePreset:
        return self.presets[self.active]


@dataclass(frozen=True)
class SettleResult:
    elapsed_ms: float
    changed: bool
    polls: int


@dataclass
class GrabMeta:
    skipped_jpeg: bool = False
    changed: bool = True
    preset: str = "gameplay"
    crop: CropRect | None = None
    full: bool = False


def apply_crop(image: Image.Image, crop: Sequence[float] | None) -> Image.Image:
    if crop is None or len(crop) != 4:
        return image
    left, top, width, height = (float(v) for v in crop)
    w, h = image.size
    x0 = int(round(max(0.0, min(1.0, left)) * w))
    y0 = int(round(max(0.0, min(1.0, top)) * h))
    x1 = int(round(max(0.0, min(1.0, left + width)) * w))
    y1 = int(round(max(0.0, min(1.0, top + height)) * h))
    if x1 <= x0 or y1 <= y0:
        return image
    return image.crop((x0, y0, x1, y1))


def roi_thumb(image: Image.Image, size: int = 64) -> Image.Image:
    return image.convert("L").resize((max(1, size), max(1, size)), Image.Resampling.LANCZOS)


def mean_absdiff(a: Image.Image, b: Image.Image) -> float:
    left = a.convert("L")
    right = b.convert("L")
    if right.size != left.size:
        right = right.resize(left.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(left, right)
    return float(ImageStat.Stat(diff).mean[0]) / 255.0


def poll_until_settled(
    grab_thumb: Callable[[], Image.Image],
    *,
    threshold: float,
    timeout_ms: int,
    poll_ms: int,
    stable_ms: int,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> SettleResult:
    t0 = clock()
    prev = grab_thumb()
    deadline = t0 + max(0, timeout_ms) / 1000.0
    poll_s = max(0.0, poll_ms) / 1000.0
    stable_s = max(0.0, stable_ms) / 1000.0
    saw_change = False
    stable_since: float | None = None
    polls = 0
    while clock() < deadline:
        sleep(poll_s)
        curr = grab_thumb()
        polls += 1
        if mean_absdiff(prev, curr) > threshold:
            saw_change = True
            stable_since = None
            prev = curr
            continue
        if saw_change:
            now = clock()
            if stable_since is None:
                stable_since = now
            elif (now - stable_since) >= stable_s:
                break
    return SettleResult(elapsed_ms=(clock() - t0) * 1000.0, changed=saw_change, polls=polls)


def wait_until_quiet(
    grab_thumb: Callable[[], Image.Image],
    *,
    threshold: float,
    timeout_ms: int,
    poll_ms: int,
    quiet_ms: int,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
    should_stop: Callable[[], bool] | None = None,
) -> SettleResult:
    """Poll thumbs until they stay still for quiet_ms. Does not require a prior change.

    Cheap gate before JPEG/VLM: grab_thumb should be a tiny luma resize, not an encoded frame.
    Timeout returns the last state even if motion never stopped (idle particles).
    """
    t0 = clock()
    prev = grab_thumb()
    deadline = t0 + max(0, timeout_ms) / 1000.0
    poll_s = max(0.0, poll_ms) / 1000.0
    quiet_s = max(0.0, quiet_ms) / 1000.0
    saw_change = False
    quiet_since = t0
    polls = 0
    if timeout_ms <= 0 or quiet_s <= 0:
        return SettleResult(elapsed_ms=(clock() - t0) * 1000.0, changed=False, polls=0)
    while clock() < deadline:
        if should_stop is not None and should_stop():
            break
        sleep(poll_s)
        curr = grab_thumb()
        polls += 1
        if mean_absdiff(prev, curr) > threshold:
            saw_change = True
            quiet_since = None
            prev = curr
            continue
        now = clock()
        if quiet_since is None:
            quiet_since = now
        elif (now - quiet_since) >= quiet_s:
            break
    return SettleResult(elapsed_ms=(clock() - t0) * 1000.0, changed=saw_change, polls=polls)


__all__ = [
    "CapturePreset",
    "CaptureProfile",
    "CropRect",
    "GrabMeta",
    "SettleResult",
    "apply_crop",
    "mean_absdiff",
    "poll_until_settled",
    "roi_thumb",
    "wait_until_quiet",
]
