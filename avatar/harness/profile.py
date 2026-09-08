from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from PIL import Image, ImageChops, ImageStat

from avatar.harness.capture import LongestSide
from avatar.schema import CaptureSettings

# Normalized client crop: left, top, width, height in [0, 1].
CropRect = tuple[float, float, float, float]


@dataclass(frozen=True)
class CapturePreset:
    crop: CropRect | None = None
    longest_side: LongestSide = 768
    jpeg_quality: int = 85


@dataclass
class CaptureProfile:
    """Per-fixture pixel budget, vision crop, settle, and scout knobs.

    Crop is vision-only. Mouse/gamepad stay in full client coordinates.
    """

    jpeg_quality: int = 85
    fmt: str = "jpeg"
    diff_size: int = 64
    diff_threshold: float = 0.02
    scout_every: int = 8
    scout_longest_side: LongestSide = 384
    settle_timeout_ms: int = 400
    settle_stable_ms: int = 50
    settle_poll_ms: int = 16
    force_every: int = 8
    skip_unchanged: bool = True
    layout_mode: str = "cv"
    layout_pad: tuple[float, float, float, float] = (0.04, 0.04, 0.04, 0.04)
    layout_sides: tuple[int, ...] = (256, 384, 512, 768)
    layout_iou_refresh: float = 0.7
    yaml_gameplay: CapturePreset | None = None
    last_layout: Any = None
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
        if self.yaml_gameplay is None:
            self.yaml_gameplay = self.presets.get("gameplay")

    @property
    def current(self) -> CapturePreset:
        return self.presets[self.active]

    def select(self, screen_kind: str) -> CapturePreset:
        if screen_kind in self.presets:
            self.active = screen_kind
        return self.current

    def apply_gameplay_layout(self, plan: Any) -> None:
        """Write a diagnosed crop + longest_side onto the gameplay preset only."""
        prev = self.presets.get("gameplay") or CapturePreset(jpeg_quality=self.jpeg_quality)
        jpeg = int(getattr(plan, "jpeg_quality", None) or prev.jpeg_quality)
        self.presets["gameplay"] = CapturePreset(
            crop=getattr(plan, "crop", prev.crop),
            longest_side=getattr(plan, "longest_side", prev.longest_side),
            jpeg_quality=jpeg,
        )
        self.last_layout = plan

    @classmethod
    def from_mapping(cls, global_cap: dict[str, Any], local: dict[str, Any]) -> CaptureProfile:
        default_side = _as_side(
            local.get("longest_side", global_cap.get("default_longest_side", 768)),
            768,
        )
        if isinstance(global_cap.get("longest_side"), list) and "longest_side" not in local:
            default_side = _as_side(global_cap.get("default_longest_side"), 768)
        default_jpeg = _as_int(
            local.get("jpeg_quality", global_cap.get("default_jpeg_quality", 85)),
            85,
        )
        if isinstance(global_cap.get("jpeg_quality"), list) and "jpeg_quality" not in local:
            default_jpeg = _as_int(global_cap.get("default_jpeg_quality"), 85)

        fmt = str(_merged(global_cap, local, "format", "jpeg"))
        profile = cls(
            jpeg_quality=default_jpeg,
            fmt=fmt,
            diff_size=_as_int(_merged(global_cap, local, "diff_size", 64), 64),
            diff_threshold=_as_float(_merged(global_cap, local, "diff_threshold", 0.02), 0.02),
            scout_every=_as_int(_merged(global_cap, local, "scout_every", 8), 8),
            scout_longest_side=_as_side(_merged(global_cap, local, "scout_longest_side", 384), 384),
            settle_timeout_ms=_as_int(_merged(global_cap, local, "settle_timeout_ms", 400), 400),
            settle_stable_ms=_as_int(_merged(global_cap, local, "settle_stable_ms", 50), 50),
            settle_poll_ms=_as_int(_merged(global_cap, local, "settle_poll_ms", 16), 16),
            force_every=_as_int(_merged(global_cap, local, "force_every", 8), 8),
            skip_unchanged=bool(_merged(global_cap, local, "skip_unchanged", True)),
            layout_mode=_as_layout_mode(_pick(global_cap, local, "layout", "cv")),
            layout_pad=_as_pad(_pick(global_cap, local, "layout_pad", (0.04, 0.04, 0.04, 0.04))),
            layout_sides=_as_sides(_pick(global_cap, local, "layout_sides", (256, 384, 512, 768))),
            layout_iou_refresh=_as_float(_pick(global_cap, local, "layout_iou_refresh", 0.7), 0.7),
        )
        default_crop = _as_crop(local.get("crop"))
        preset_raw = local.get("presets") if isinstance(local.get("presets"), dict) else {}
        gameplay_raw = preset_raw.get("gameplay") if isinstance(preset_raw.get("gameplay"), dict) else {}
        ui_raw = preset_raw.get("ui") if isinstance(preset_raw.get("ui"), dict) else {}

        def _preset(raw: dict[str, Any], fallback_crop: CropRect | None) -> CapturePreset:
            crop = _as_crop(raw["crop"]) if "crop" in raw else fallback_crop
            return CapturePreset(
                crop=crop,
                longest_side=_as_side(raw.get("longest_side"), default_side),
                jpeg_quality=_as_int(raw.get("jpeg_quality"), default_jpeg),
            )

        gameplay = _preset(gameplay_raw, default_crop)
        if ui_raw:
            ui = _preset(ui_raw, None)
        else:
            ui = CapturePreset(crop=None, longest_side=768, jpeg_quality=default_jpeg)
        profile.presets = {"gameplay": gameplay, "ui": ui}
        profile.yaml_gameplay = gameplay
        return profile


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
    layout_source: str | None = None


def apply_crop(image: Image.Image, crop: Sequence[float] | None) -> Image.Image:
    """Crop *image* by a normalized rect. None or invalid rect → identity."""
    if crop is None:
        return image
    if len(crop) != 4:
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
    """Mean absolute difference in [0, 1] on luma. Resizes *b* to *a* if needed."""
    left = a.convert("L")
    right = b.convert("L")
    if right.size != left.size:
        right = right.resize(left.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(left, right)
    return float(ImageStat.Stat(diff).mean[0]) / 255.0


def roi_changed(prev: Image.Image, curr: Image.Image, threshold: float) -> bool:
    return mean_absdiff(prev, curr) > threshold


def use_cached_jpeg(
    *,
    skip_unchanged: bool,
    changed: bool,
    has_last: bool,
    fresh: bool = False,
) -> bool:
    """Paused-mode JPEG skip only. Realtime/GSM must pass fresh=True."""
    if fresh or not skip_unchanged or changed or not has_last:
        return False
    return True


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
    """Poll ROI thumbs until a change then a stable window, or timeout.

    No rising edge within *timeout_ms* → changed=False (caller must still plan).
    """
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


def next_skip_streak(changed: bool, streak: int, force_every: int) -> tuple[bool, int]:
    """Realtime skip: (skip_inference, new_streak).

    Unchanged frames skip until *force_every* consecutive skips, then force a call.
    A change or a forced call resets the streak. force_every<=0 disables skipping.
    """
    if force_every <= 0:
        return False, 0
    if changed:
        return False, 0
    new_streak = streak + 1
    if new_streak >= force_every:
        return False, 0
    return True, new_streak


def settings_from_frame(
    frame: Any,
    profile: CaptureProfile,
    *,
    scout: bool = False,
    settle_ms: float | None = None,
    skipped_inference: bool = False,
) -> CaptureSettings:
    crop = profile.current.crop
    mime = getattr(frame, "mime", "image/jpeg")
    layout = getattr(profile, "last_layout", None)
    source = None if layout is None else getattr(layout, "source", None)
    return CaptureSettings(
        longest_side=frame.longest_side,
        jpeg_quality=frame.jpeg_quality or 85,
        format="jpeg" if "jpeg" in mime else mime,
        width=frame.width,
        height=frame.height,
        bytes=frame.nbytes,
        crop=list(crop) if crop is not None else None,
        preset=profile.active,
        scout=scout,
        settle_ms=settle_ms,
        skipped_inference=skipped_inference,
        layout_source=source,
    )


def profile_from_config(cfg: dict[str, Any], fixture: str) -> CaptureProfile:
    global_cap = cfg.get("capture") or {}
    fixture_cfg = (cfg.get("fixtures") or {}).get(fixture) or {}
    local = fixture_cfg.get("capture") or {}
    return CaptureProfile.from_mapping(global_cap, local)


def _as_side(value: Any, default: LongestSide) -> LongestSide:
    if value == "native":
        return "native"
    if isinstance(value, int) and value > 0:
        return value
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _as_crop(value: Any) -> CropRect | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    return None


def _merged(global_cap: dict[str, Any], local: dict[str, Any], key: str, default: Any) -> Any:
    if key in local:
        return local[key]
    if key in global_cap:
        value = global_cap[key]
        if isinstance(value, list):
            return default
        return value
    return default


def _pick(global_cap: dict[str, Any], local: dict[str, Any], key: str, default: Any) -> Any:
    if key in local:
        return local[key]
    if key in global_cap:
        return global_cap[key]
    return default


def _as_layout_mode(value: Any) -> str:
    text = str(value or "cv").strip().lower()
    return text if text in {"cv", "yaml"} else "cv"


def _as_pad(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    return (0.04, 0.04, 0.04, 0.04)


def _as_sides(value: Any) -> tuple[int, ...]:
    if isinstance(value, (list, tuple)) and value:
        sides = tuple(int(v) for v in value if isinstance(v, int) and not isinstance(v, bool) and v > 0)
        if sides:
            return sides
    return (256, 384, 512, 768)


__all__ = [
    "CapturePreset",
    "CaptureProfile",
    "CropRect",
    "GrabMeta",
    "SettleResult",
    "apply_crop",
    "mean_absdiff",
    "next_skip_streak",
    "poll_until_settled",
    "profile_from_config",
    "roi_changed",
    "roi_thumb",
    "settings_from_frame",
]
