from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import numpy as np
from PIL import Image, ImageStat

from loop.os.profile import apply_crop


def _rgb_close(pixel: tuple[int, int, int], target: Sequence[int], tolerance: int) -> bool:
    return all(abs(int(pixel[i]) - int(target[i])) <= tolerance for i in range(3))


def _rgb_close_arr(arr: np.ndarray, target: Sequence[int], tolerance: int) -> np.ndarray:
    diff = np.abs(arr[..., :3].astype(np.int16) - np.asarray(target, dtype=np.int16)[:3])
    return np.all(diff <= tolerance, axis=-1)


def _crop_region(image: Image.Image, crop: Sequence[float] | None) -> Image.Image:
    return apply_crop(image, crop).convert("RGB")


def _crop_pixels(image: Image.Image, crop: Sequence[float] | None) -> np.ndarray:
    return np.asarray(_crop_region(image, crop))


def _bar_edge_frac(image: Image.Image, spec: dict[str, Any] | None) -> float | None:
    """Scan a 1-px-tall row at the crop's vertical centre (fill vs empty)."""
    if not spec:
        return None
    crop = spec.get("crop")
    region = _crop_region(image, crop)
    arr = np.asarray(region)
    h, w = arr.shape[:2]
    if w < 2 or h < 1:
        return None
    row = max(0, min(h - 1, int(round(float(spec.get("bar_row") or 0.5) * (h - 1)))))
    fill = spec.get("fill_rgb") or [180, 20, 20]
    empty = spec.get("empty_rgb") or [40, 20, 20]
    tolerance = int(spec.get("tolerance") or 28)
    row_px = arr[row, :, :3]
    fill_m = _rgb_close_arr(row_px, fill, tolerance)
    empty_m = _rgb_close_arr(row_px, empty, tolerance)
    known = int(np.count_nonzero(fill_m | empty_m))
    fill_idx = np.flatnonzero(fill_m)
    last_fill_x = int(fill_idx[-1]) if fill_idx.size else -1
    if known < max(4, w // 8) and last_fill_x < 0:
        return None
    if last_fill_x >= 0:
        return float(last_fill_x + 1) / w
    empty_tight = max(8, tolerance // 2)
    empty_strict = int(np.count_nonzero(_rgb_close_arr(row_px, empty, empty_tight)))
    if empty_strict >= max(4, w // 8):
        return 0.0
    return None


def health_frac(image: Image.Image, spec: dict[str, Any] | None) -> float | None:
    return _bar_edge_frac(image, spec)


def stamina_frac(image: Image.Image, spec: dict[str, Any] | None) -> float | None:
    return _bar_edge_frac(image, spec)


def runes(image: Image.Image, spec: dict[str, Any] | None) -> int | None:
    """Count gold blobs in the rune crop. None when the crop is not gold-like."""
    if not spec:
        return None
    arr = np.asarray(_crop_region(image, spec.get("crop")))
    h, w = arr.shape[:2]
    gold = spec.get("gold_rgb") or [210, 170, 40]
    tolerance = int(spec.get("tolerance") or 40)
    min_blob = int(spec.get("min_blob") or 6)
    mask = _rgb_close_arr(arr, gold, tolerance)
    gold_n = int(np.count_nonzero(mask))
    if gold_n < min_blob:
        return None
    blobs = 0
    seen = np.zeros((h, w), dtype=bool)
    for y, x in zip(*np.nonzero(mask)):
        y, x = int(y), int(x)
        if seen[y, x]:
            continue
        stack = [(x, y)]
        size = 0
        seen[y, x] = True
        while stack:
            cx, cy = stack.pop()
            size += 1
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((nx, ny))
        if size >= min_blob:
            blobs += 1
    return blobs


def fail_screen(image: Image.Image, spec: dict[str, Any] | None) -> bool | None:
    if not spec:
        return None
    arr = _crop_pixels(image, spec.get("crop"))
    if arr.size == 0:
        return None
    min_frac = float(spec.get("min_red_frac") or 0.15)
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    reds = int(np.count_nonzero((r > 140) & (r > g * 2) & (r > b * 2)))
    return bool((reds / r.size) >= min_frac)


def win_screen(image: Image.Image, spec: dict[str, Any] | None) -> bool | None:
    if not spec:
        return None
    arr = _crop_pixels(image, spec.get("crop"))
    if arr.size == 0:
        return None
    min_frac = float(spec.get("min_gold_frac") or 0.2)
    r = arr[..., 0]
    g = arr[..., 1]
    b = arr[..., 2]
    golds = int(np.count_nonzero((r > 160) & (g > 120) & (b < 80)))
    return bool((golds / r.size) >= min_frac)


def mean_luma(image: Image.Image) -> float:
    gray = image.convert("L")
    return float(ImageStat.Stat(gray).mean[0])


def is_black_frame(image: Image.Image, *, luma_max: float = 5.0) -> bool:
    return mean_luma(image) <= luma_max


def score_hud(image: Image.Image, hud: dict[str, Any] | None) -> dict[str, Any]:
    hud = hud or {}
    health = health_frac(image, hud.get("health") if isinstance(hud.get("health"), dict) else None)
    stamina = stamina_frac(image, hud.get("stamina") if isinstance(hud.get("stamina"), dict) else None)
    rune_n = runes(image, hud.get("runes") if isinstance(hud.get("runes"), dict) else None)
    failed = fail_screen(image, hud.get("fail_screen") if isinstance(hud.get("fail_screen"), dict) else None)
    won = win_screen(image, hud.get("win_screen") if isinstance(hud.get("win_screen"), dict) else None)
    return {
        "health_frac": health,
        "stamina_frac": stamina,
        "runes": rune_n,
        "fail_screen": failed,
        "win_screen": won,
    }


def hud_visible(metrics: dict[str, Any] | None) -> bool:
    """True when vital bars are on screen. Cutscenes / hidden HUD stay False.

    A lone health=0.0 with no stamina bar is treated as absent (wrong window or
    empty crop), not a real empty health bar.
    """
    if not metrics:
        return False
    if metrics.get("fail_screen") is True:
        return True
    health = metrics.get("health_frac")
    stamina = metrics.get("stamina_frac")
    health_ok = isinstance(health, (int, float))
    stamina_ok = isinstance(stamina, (int, float))
    if health_ok and stamina_ok:
        return True
    if health_ok and float(health) > 0.02:
        return True
    return False


def shift_crop(crop: Sequence[float], dy: float) -> list[float]:
    x, y, w, h = (float(v) for v in crop[:4])
    return [x, max(0.0, min(0.95, y + dy)), w, h]


def score_hud_search(
    image: Image.Image,
    hud: dict[str, Any] | None,
    *,
    y_offsets: tuple[float, ...] = (-0.04, -0.02, 0.02, 0.04),
) -> tuple[dict[str, Any], float]:
    scored = score_hud(image, hud)
    if hud_visible(scored):
        return scored, 0.0
    hud = hud or {}
    best = scored
    best_dy = 0.0
    found_visible = False
    for dy in y_offsets:
        shifted: dict[str, Any] = dict(hud)
        health = hud.get("health")
        stamina = hud.get("stamina")
        if isinstance(health, dict):
            health = dict(health)
            crop = health.get("crop")
            if isinstance(crop, (list, tuple)) and len(crop) >= 4:
                health["crop"] = shift_crop(crop, dy)
            shifted["health"] = health
        if isinstance(stamina, dict):
            stamina = dict(stamina)
            crop = stamina.get("crop")
            if isinstance(crop, (list, tuple)) and len(crop) >= 4:
                stamina["crop"] = shift_crop(crop, dy)
            shifted["stamina"] = stamina
        metrics = score_hud(image, shifted)
        if not hud_visible(metrics):
            continue
        health_ok = isinstance(metrics.get("health_frac"), (int, float))
        stamina_ok = isinstance(metrics.get("stamina_frac"), (int, float))
        if health_ok and stamina_ok:
            return metrics, float(dy)
        if not found_visible:
            best = metrics
            best_dy = float(dy)
            found_visible = True
    return best, best_dy


@dataclass
class HudWaitResult:
    image: Image.Image
    metrics: dict[str, Any]
    visible: bool
    black: bool
    attempts: int
    waited_s: float


def wait_for_hud(
    grab: Callable[[], Image.Image],
    score: Callable[[Image.Image], dict[str, Any]],
    *,
    timeout_s: float = 90.0,
    poll_s: float = 0.5,
    on_tick: Callable[[HudWaitResult], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> HudWaitResult:
    """Poll grabs until HUD bars appear, a black frame, timeout, or stop."""
    t0 = time.perf_counter()
    attempts = 0
    image = grab()
    while True:
        attempts += 1
        black = is_black_frame(image)
        metrics = {} if black else score(image)
        visible = (not black) and hud_visible(metrics)
        waited = time.perf_counter() - t0
        result = HudWaitResult(
            image=image,
            metrics=metrics,
            visible=visible,
            black=black,
            attempts=attempts,
            waited_s=waited,
        )
        if on_tick is not None:
            on_tick(result)
        if visible or black:
            return result
        if should_stop is not None and should_stop():
            return result
        if timeout_s <= 0 or waited >= timeout_s:
            return result
        time.sleep(max(0.05, poll_s))
        image = grab()


class HudPoller:
    """Background HUD scorer. Does not block play; None fields stay valid."""

    def __init__(
        self,
        grab: Callable[[], Image.Image],
        score: Callable[[Image.Image], dict[str, Any]],
        *,
        interval_s: float = 1.0,
        on_update: Callable[[dict[str, Any], bool], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> None:
        self._grab = grab
        self._score = score
        self.interval_s = interval_s
        self._on_update = on_update
        self._should_stop = should_stop
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.metrics: dict[str, Any] = {}
        self.visible = False

    def start(self) -> None:
        if self.interval_s <= 0 or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hud-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None

    def snapshot(self) -> tuple[dict[str, Any], bool]:
        with self._lock:
            return dict(self.metrics), self.visible

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._should_stop is not None and self._should_stop():
                break
            try:
                metrics = self._score(self._grab())
            except Exception:  # noqa: BLE001 — poller must not kill collect
                metrics = {}
            visible = hud_visible(metrics)
            with self._lock:
                self.metrics = metrics
                self.visible = visible
            if self._on_update is not None:
                try:
                    self._on_update(metrics, visible)
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(max(0.2, self.interval_s))
