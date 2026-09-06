"""CPU OCR for STS2 HUD and aim labels. RapidOCR (ONNX) first, Tesseract fallback.

Keep this off CUDA so Ollama can own the 4060 during collect. Elden prompt-inset
OCR stays in `saliency.read_prompt_text` (invert + PSM).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from loop.os.profile import roi_thumb

_LOG = logging.getLogger("loop.ocr")
_RAPID_LOG = logging.getLogger("RapidOCR")

_MIN_SCORE = 0.45
_OCR_LONGEST = 960
_BLANK_MAX = 24
_CROP_HASH_SIZE = 32
_LOCK = threading.Lock()
_ENGINE: Any = None
_ENGINE_ERROR = ""
_TRIED_RAPID = False
_LAST_STATS: dict[str, Any] = {}
_DEBUG_DIR: Path | None = None
_DUMP_COUNTER = 0
_DUMP_LOCK = threading.Lock()
_READ_TAG = "full"
_OCR_SCREEN = ""
_OCR_UNCERTAIN = False
_REGION_CACHE: dict[tuple[str, str, str], list[OcrWord]] = {}
_REGION_CACHE_SCREEN = ""
_CACHE_TOTAL_HITS = 0
_CACHE_TOTAL_MISSES = 0

# HUD, hand, board, overlay, and corner controls — full frame only on uncertain transitions.
_OCR_REGIONS: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("hud", (0.0, 0.0, 1.0, 0.12)),
    ("board", (0.0, 0.12, 1.0, 0.70)),
    ("hand", (0.18, 0.70, 0.82, 1.0)),
    ("overlay", (0.28, 0.60, 0.72, 0.94)),
    ("controls", (0.74, 0.0, 1.0, 0.20)),
)


@dataclass(frozen=True)
class OcrWord:
    text: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def x(self) -> float:
        return (self.x0 + self.x1) * 0.5

    @property
    def y(self) -> float:
        return (self.y0 + self.y1) * 0.5


def _pref() -> str:
    return os.environ.get("AVATAR_OCR", "auto").strip().lower() or "auto"


def _debug_enabled() -> bool:
    return os.environ.get("AVATAR_OCR_DEBUG", "").strip().lower() in {"1", "true", "yes"}


class _SuppressEmptyDetectionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "detection result is empty" not in record.getMessage().lower()


@contextmanager
def _rapid_quiet():
    filt = _SuppressEmptyDetectionFilter()
    loggers = [logging.getLogger("RapidOCR"), logging.getLogger("rapidocr")]
    for log in loggers:
        log.addFilter(filt)
    try:
        yield
    finally:
        for log in loggers:
            log.removeFilter(filt)


def set_debug_dir(path: Path | str | None) -> None:
    """Collect loop sets run_dir/ocr_debug once per episode."""
    global _DEBUG_DIR, _DUMP_COUNTER
    if path is None:
        _DEBUG_DIR = None
        return
    _DEBUG_DIR = Path(path)
    _DUMP_COUNTER = 0


def set_read_tag(tag: str) -> None:
    """Optional tag for debug dumps: full, roi, prepared, etc."""
    global _READ_TAG
    _READ_TAG = (tag or "full").strip() or "full"


def set_ocr_context(*, screen: str = "", uncertain: bool = False) -> None:
    """Screen-aware region OCR; screen change clears the crop cache."""
    global _OCR_SCREEN, _OCR_UNCERTAIN, _REGION_CACHE, _REGION_CACHE_SCREEN
    screen_key = (screen or "").strip().lower()
    if screen_key != _REGION_CACHE_SCREEN:
        _REGION_CACHE.clear()
        _REGION_CACHE_SCREEN = screen_key
    _OCR_SCREEN = screen_key
    _OCR_UNCERTAIN = bool(uncertain)


def clear_ocr_region_cache() -> None:
    """Drop cached region OCR (e.g. on perception reset)."""
    global _REGION_CACHE, _REGION_CACHE_SCREEN
    _REGION_CACHE.clear()
    _REGION_CACHE_SCREEN = ""


def ocr_cache_stats() -> dict[str, Any]:
    """Cumulative region-cache hit/miss counters."""
    return {"hits": _CACHE_TOTAL_HITS, "misses": _CACHE_TOTAL_MISSES}


def warm_up_rapid_engine() -> bool:
    """Construct RapidOCR once before timed turns (no OCR work)."""
    return _rapid_engine() is not None


def engine_name() -> str:
    pref = _pref()
    if pref in {"off", "none"}:
        return "off"
    if pref != "tesseract" and _rapid_engine() is not None:
        return "rapidocr"
    if pref != "rapidocr" and _tesseract() is not None:
        return "tesseract"
    return "off"


def available() -> bool:
    return engine_name() != "off"


def last_read_stats() -> dict[str, Any]:
    """Diagnostics from the most recent read_words call (engine, blank, counts)."""
    return dict(_LAST_STATS)


def frame_is_blank(image: Image.Image) -> bool:
    """True when the frame is nearly black — OCR empty is expected, not a bug."""
    if image is None:
        return True
    arr = np.asarray(image.convert("RGB"), dtype=np.float32)
    if arr.size == 0:
        return True
    return float(arr.max()) <= _BLANK_MAX


def _resolve_debug_dir() -> Path | None:
    if not _debug_enabled():
        return None
    if _DEBUG_DIR is not None:
        return _DEBUG_DIR
    env = os.environ.get("AVATAR_OCR_DEBUG_DIR", "").strip()
    if env:
        return Path(env)
    return Path("ocr_debug")


def _dump_image(image: Image.Image, tag: str) -> str | None:
    out_dir = _resolve_debug_dir()
    if out_dir is None:
        return None
    global _DUMP_COUNTER
    with _DUMP_LOCK:
        idx = _DUMP_COUNTER
        _DUMP_COUNTER += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{idx:04d}_{tag}.png"
    try:
        image.save(path)
        return str(path)
    except OSError:
        return None


def _coerce_rgb_uint8(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.ndim != 3 or arr.shape[2] not in {3, 4}:
        raise ValueError(f"expected HxWx3/4 array, got shape {arr.shape}")
    if arr.shape[2] == 4:
        arr = arr[:, :, :3]
    if arr.dtype != np.uint8:
        if arr.dtype in (np.float32, np.float64) and float(arr.max()) <= 1.0:
            arr = (arr * 255.0).clip(0, 255).astype(np.uint8)
        else:
            arr = arr.clip(0, 255).astype(np.uint8)
    return np.ascontiguousarray(arr)


def read_words(
    image: Image.Image,
    *,
    tag: str | None = None,
    force_full: bool = False,
) -> list[OcrWord]:
    """Word boxes in normalized client coords. Empty when OCR is off or fails."""
    if force_full or _OCR_UNCERTAIN or not _OCR_SCREEN:
        return _read_words_full(image, tag=tag, ocr_mode="full")
    return _read_words_regions(image, tag=tag)


def _read_words_full(
    image: Image.Image,
    *,
    tag: str | None = None,
    ocr_mode: str = "full",
) -> list[OcrWord]:
    """Full-frame OCR path (uncertain transitions and ROI sub-crops)."""
    global _LAST_STATS, _READ_TAG
    if tag is not None:
        _READ_TAG = tag.strip() or "full"
    if image is None:
        _LAST_STATS = {"engine": "none", "blank": True, "words": 0, "ocr_mode": ocr_mode}
        return []
    src_mode = getattr(image, "mode", "RGB")
    rgb = image.convert("RGB")
    w, h = rgb.size
    if w < 8 or h < 8:
        _LAST_STATS = {
            "engine": engine_name(),
            "blank": True,
            "words": 0,
            "size": (w, h),
            "ocr_mode": ocr_mode,
        }
        return []
    blank = frame_is_blank(rgb)
    raw_dump = _dump_image(rgb, f"{_READ_TAG}_raw") if _debug_enabled() else None
    prepared = _prepare_rgb(rgb)
    prep_dump = _dump_image(prepared, f"{_READ_TAG}_prepared") if _debug_enabled() else None
    name = engine_name()
    words: list[OcrWord] = []
    rapid_stats: dict[str, Any] = {}
    if name == "rapidocr":
        words, rapid_stats = _rapid_words(prepared, raw_rgb=rgb)
        if not words and not blank and _tesseract() is not None:
            words = _tess_words(prepared)
            if words:
                name = "tesseract-fallback"
    elif name == "tesseract":
        words = _tess_words(prepared)
    arr = np.asarray(rgb, dtype=np.float32)
    prep_arr = np.asarray(prepared, dtype=np.float32)
    _LAST_STATS = {
        "engine": name,
        "blank": blank,
        "words": len(words),
        "size": (w, h),
        "mode": src_mode,
        "mean": float(arr.mean()),
        "max": float(arr.max()),
        "prepared_mean": float(prep_arr.mean()) if prep_arr.size else 0.0,
        "prepared_max": float(prep_arr.max()) if prep_arr.size else 0.0,
        "dump_raw": raw_dump,
        "dump_prepared": prep_dump,
        "ocr_mode": ocr_mode,
        "cache_hits": 0,
        "cache_misses": 0,
        **rapid_stats,
    }
    return words


def _read_words_regions(image: Image.Image, *, tag: str | None = None) -> list[OcrWord]:
    """Region OCR with crop-hash cache; merges HUD/hand/overlay/control bands."""
    global _LAST_STATS, _CACHE_TOTAL_HITS, _CACHE_TOTAL_MISSES
    t0 = time.perf_counter()
    hits = 0
    misses = 0
    region_ms: dict[str, float] = {}
    merged: list[OcrWord] = []
    seen: set[tuple[str, float, float]] = set()

    for name, box in _OCR_REGIONS:
        t_region = time.perf_counter()
        crop_hash = _crop_hash(image, box)
        key = (_OCR_SCREEN, name, crop_hash)
        if key in _REGION_CACHE:
            hits += 1
            region_words = list(_REGION_CACHE[key])
        else:
            misses += 1
            region_words = read_words_in_roi(image, box, force_full=True, tag=f"{tag or 'full'}-{name}")
            _REGION_CACHE[key] = list(region_words)
        region_ms[name] = (time.perf_counter() - t_region) * 1000.0
        for word in region_words:
            dedupe = (word.text, round(word.x, 4), round(word.y, 4))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            merged.append(word)

    _CACHE_TOTAL_HITS += hits
    _CACHE_TOTAL_MISSES += misses
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    _LAST_STATS = {
        "engine": engine_name(),
        "blank": frame_is_blank(image),
        "words": len(merged),
        "size": image.size,
        "ocr_mode": "regions",
        "cache_hits": hits,
        "cache_misses": misses,
        "region_ms": region_ms,
        "ocr_ms": elapsed_ms,
        "screen": _OCR_SCREEN,
    }
    return merged


def _crop_hash(image: Image.Image, box: tuple[float, float, float, float]) -> str:
    w, h = image.size
    x0, y0, x1, y1 = box
    crop = image.crop(
        (
            int(max(0.0, min(1.0, x0)) * w),
            int(max(0.0, min(1.0, y0)) * h),
            int(max(0.0, min(1.0, x1)) * w),
            int(max(0.0, min(1.0, y1)) * h),
        )
    )
    thumb = roi_thumb(crop, _CROP_HASH_SIZE)
    return hashlib.md5(thumb.tobytes()).hexdigest()


def read_words_in_roi(
    image: Image.Image,
    box: tuple[float, float, float, float],
    *,
    tag: str | None = None,
    force_full: bool = True,
) -> list[OcrWord]:
    """OCR a normalized ROI; word coords are mapped back to full-frame space."""
    if image is None:
        return []
    rgb = image.convert("RGB")
    w, h = rgb.size
    if w < 8 or h < 8:
        return []
    x0, y0, x1, y1 = box
    crop = rgb.crop(
        (
            int(max(0.0, min(1.0, x0)) * w),
            int(max(0.0, min(1.0, y0)) * h),
            int(max(0.0, min(1.0, x1)) * w),
            int(max(0.0, min(1.0, y1)) * h),
        )
    )
    if crop.size[0] < 8 or crop.size[1] < 8:
        return []
    prev_tag = _READ_TAG
    set_read_tag(tag or "roi")
    words = read_words(crop, force_full=force_full)
    set_read_tag(prev_tag)
    fx0, fy0 = float(box[0]), float(box[1])
    fw, fh = float(box[2] - box[0]), float(box[3] - box[1])
    mapped: list[OcrWord] = []
    for word in words:
        mapped.append(
            OcrWord(
                text=word.text,
                score=word.score,
                x0=fx0 + word.x0 * fw,
                y0=fy0 + word.y0 * fh,
                x1=fx0 + word.x1 * fw,
                y1=fy0 + word.y1 * fh,
            )
        )
    return mapped


def read_text(image: Image.Image) -> str:
    """Joined OCR string for screen/HUD parsers."""
    words = read_words(image)
    if words:
        return " ".join(w.text for w in words if w.text)
    return ""


def _rapid_engine() -> Any | None:
    global _ENGINE, _ENGINE_ERROR, _TRIED_RAPID
    pref = _pref()
    if pref in {"off", "none", "tesseract"}:
        return None
    with _LOCK:
        if _TRIED_RAPID:
            return _ENGINE
        _TRIED_RAPID = True
        try:
            from rapidocr import RapidOCR
        except Exception as exc:  # noqa: BLE001 — optional extra
            _ENGINE_ERROR = f"{type(exc).__name__}: {exc}"
            return None
        _RAPID_LOG.setLevel(logging.ERROR)
        empty_filter = _SuppressEmptyDetectionFilter()
        for name in ("RapidOCR", "rapidocr", "rapidocr.main"):
            log = logging.getLogger(name)
            log.setLevel(logging.ERROR)
            log.addFilter(empty_filter)
        try:
            _ENGINE = RapidOCR()
        except Exception as exc:  # noqa: BLE001
            _ENGINE_ERROR = f"{type(exc).__name__}: {exc}"
            _ENGINE = None
            return None
        return _ENGINE


def _prepare_rgb(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    w, h = rgb.size
    longest = max(w, h)
    if longest < _OCR_LONGEST:
        scale = _OCR_LONGEST / longest
        rgb = rgb.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    rgb = ImageOps.autocontrast(rgb, cutoff=1)
    return ImageEnhance.Contrast(rgb).enhance(1.15)


def _parse_rapid_output(out: Any) -> tuple[list[Any], list[Any] | None, list[Any] | None]:
    if out is None:
        return [], None, None
    txts = getattr(out, "txts", None)
    boxes = getattr(out, "boxes", None)
    scores = getattr(out, "scores", None)
    if txts is not None and boxes is not None:
        return list(txts), boxes, scores
    if isinstance(out, tuple) and len(out) >= 2:
        maybe_boxes, maybe_txts = out[0], out[1]
        if maybe_txts and maybe_boxes is not None:
            extra_scores = out[2] if len(out) > 2 else None
            return list(maybe_txts), maybe_boxes, extra_scores
    return [], None, None


def _rapid_words(
    image: Image.Image,
    *,
    raw_rgb: Image.Image | None = None,
) -> tuple[list[OcrWord], dict[str, Any]]:
    stats: dict[str, Any] = {}
    engine = _rapid_engine()
    if engine is None:
        return [], stats
    if raw_rgb is not None:
        raw_arr = np.asarray(raw_rgb.convert("RGB"))
        stats["raw_mean"] = float(raw_arr.mean()) if raw_arr.size else 0.0
        stats["raw_max"] = float(raw_arr.max()) if raw_arr.size else 0.0
        stats["raw_mode"] = getattr(raw_rgb, "mode", "RGB")
    try:
        arr = _coerce_rgb_uint8(np.asarray(image.convert("RGB")))
    except ValueError as exc:
        stats["dtype_error"] = str(exc)
        return [], stats
    h, w = arr.shape[:2]
    stats["dtype"] = str(arr.dtype)
    stats["shape"] = (h, w, arr.shape[2])
    stats["prepared_dtype"] = str(arr.dtype)
    stats["prepared_shape"] = tuple(arr.shape)
    try:
        with _rapid_quiet():
            out = engine(arr, use_cls=False)
    except Exception as exc:  # noqa: BLE001 — HUD OCR must never abort collect
        _LOG.debug("rapidocr failed: %s", exc)
        stats["rapid_error"] = str(exc)
        return [], stats
    txts, boxes, scores = _parse_rapid_output(out)
    if not txts or boxes is None:
        return [], stats
    words: list[OcrWord] = []
    for i, raw in enumerate(txts):
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        try:
            score = float(scores[i]) if scores is not None else 1.0
        except (TypeError, ValueError, IndexError):
            score = 1.0
        if 0.0 <= score <= 1.0:
            conf = score
        else:
            conf = score / 100.0
        if conf < _MIN_SCORE:
            continue
        x0, y0, x1, y1 = _norm_box(boxes[i], w, h)
        words.append(OcrWord(text=text, score=conf, x0=x0, y0=y0, x1=x1, y1=y1))
    return words, stats


def _norm_box(box: Any, width: int, height: int) -> tuple[float, float, float, float]:
    arr = np.asarray(box, dtype=np.float32).reshape(-1)
    if arr.size >= 8:
        xs = arr[0::2][:4]
        ys = arr[1::2][:4]
        x0, x1 = float(xs.min()), float(xs.max())
        y0, y1 = float(ys.min()), float(ys.max())
    elif arr.size >= 4:
        x0, y0, x1, y1 = (float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3]))
    else:
        return 0.0, 0.0, 0.0, 0.0
    w = max(float(width), 1.0)
    h = max(float(height), 1.0)
    return (
        max(0.0, min(1.0, x0 / w)),
        max(0.0, min(1.0, y0 / h)),
        max(0.0, min(1.0, x1 / w)),
        max(0.0, min(1.0, y1 / h)),
    )


def _tesseract() -> Any | None:
    from loop.os.saliency import _bind_tesseract

    return _bind_tesseract()


def _tess_words(image: Image.Image) -> list[OcrWord]:
    tess = _tesseract()
    if tess is None:
        return []
    gray = image.convert("L")
    w, h = gray.size
    longest = max(w, h)
    scale = 1.0
    if longest > 960:
        scale = 960 / longest
        gray = gray.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.BILINEAR,
        )
        w2, h2 = gray.size
    else:
        w2, h2 = w, h
    try:
        data = tess.image_to_data(gray, output_type=tess.Output.DICT)
    except Exception:  # noqa: BLE001
        return []
    n = len(data.get("text") or [])
    words: list[OcrWord] = []
    for i in range(n):
        text = " ".join(str(data["text"][i] or "").split())
        if not text:
            continue
        try:
            conf = float(data["conf"][i]) / 100.0
        except (TypeError, ValueError):
            continue
        if conf < _MIN_SCORE:
            continue
        bw, bh = int(data["width"][i]), int(data["height"][i])
        if bw < 6 or bh < 6:
            continue
        x, y = int(data["left"][i]), int(data["top"][i])
        inv = 1.0 / scale if scale else 1.0
        words.append(
            OcrWord(
                text=text,
                score=conf,
                x0=max(0.0, min(1.0, (x * inv) / w)),
                y0=max(0.0, min(1.0, (y * inv) / h)),
                x1=max(0.0, min(1.0, ((x + bw) * inv) / w)),
                y1=max(0.0, min(1.0, ((y + bh) * inv) / h)),
            )
        )
    return words
