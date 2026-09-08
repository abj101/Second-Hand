from __future__ import annotations

import math
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, ImageStat

from loop.os.profile import mean_absdiff, roi_thumb

Grab = Callable[[], Image.Image]

# Elden prompt pill is a thin near-black centered bar with bright glyphs.
# Detect on the 2× contrast inset (not a 64×24 full-frame grid — short
# "Open" pills vanish there). Bar *shape* (dark rows + glyph chips).
_PROMPT_DARK_MAX = 110
_PROMPT_BRIGHT_MIN = 90
_PROMPT_TEXT_MIN = 130
_PROMPT_DARK_FRAC = 0.40
_PROMPT_MIN_BRIGHT = 4
_PROMPT_MIN_TEXT = 1
_PROMPT_MIN_ROWS = 2
_PROMPT_MAX_ROWS = 8
_PROMPT_CENTER = (0.10, 0.90)
_PROMPT_GRID = (256, 48)
# High-contrast row OR — translucent pills on mid-luma stone.
_PROMPT_ROW_STD_MIN = 18.0
_PROMPT_CONTRAST_ROWS = 2
_PROMPT_CONTRAST_BRIGHT = 4
# Screen y below this is roof/sky; walking there should still go forward, not pitch up.
_SKY_Y = 0.30
_STICK_VEC: dict[str, tuple[float, float]] = {
    "n": (0.0, 1.0),
    "ne": (0.7071, 0.7071),
    "e": (1.0, 0.0),
    "se": (0.7071, -0.7071),
    "s": (0.0, -1.0),
    "sw": (-0.7071, -0.7071),
    "w": (-1.0, 0.0),
    "nw": (-0.7071, 0.7071),
}


def ui_band(image: Image.Image) -> Image.Image:
    """Lower-screen strip including the bottom edge. Pickup/tutorial copy lives here."""
    w, h = image.size
    if w < 8 or h < 8:
        return image
    top = int(h * 0.70)
    left = int(w * 0.12)
    right = int(w * 0.88)
    return image.crop((left, top, max(left + 1, right), h))


def _prompt_band(image: Image.Image) -> Image.Image:
    """Lower third through the bottom edge — skips torso, keeps pickup/prompt copy."""
    w, h = image.size
    if w < 8 or h < 8:
        return image
    left = int(w * 0.22)
    right = max(left + 1, int(w * 0.78))
    top = int(h * 0.78)
    return image.crop((left, top, right, h))


def prompt_inset(image: Image.Image) -> Image.Image:
    """2× zoomed lower-center crop so the thin prompt pill stays readable after JPEG."""
    band = _prompt_band(image).convert("RGB")
    bw, bh = band.size
    if bw < 4 or bh < 2:
        return band
    zoomed = band.resize((max(1, bw * 2), max(1, bh * 2)), Image.Resampling.LANCZOS)
    return ImageEnhance.Contrast(zoomed).enhance(1.35)


_LOCK_MARKERS = (
    "carrying",
    "only to those",
    "required",
    "need",
    "locked",
    "closed",
)


_OCR_MISSING = False
_PROMPT_OCR_VERBS = (
    "open",
    "talk",
    "need",
    "pick",
    "examine",
    "read",
    "search",
    "rest",
    "sit",
    "pull",
    "use",
    "item",
    "message",
    "gesture",
    "closed",
)
_OCR_WHITELIST = (
    "--psm {psm} -c tessedit_char_whitelist="
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 '?.,:-"
)


_TESSERACT_WIN = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _bind_tesseract() -> Any | None:
    """Import pytesseract and point it at the Windows installer if PATH is stale."""
    try:
        import pytesseract
    except ImportError:
        return None
    try:
        pytesseract.get_tesseract_version()
        return pytesseract
    except Exception:  # noqa: BLE001 — try the default install path next
        if sys.platform == "win32" and _TESSERACT_WIN.exists():
            pytesseract.pytesseract.tesseract_cmd = str(_TESSERACT_WIN)
            try:
                pytesseract.get_tesseract_version()
                return pytesseract
            except Exception:  # noqa: BLE001
                return None
        return None


def ocr_available() -> bool:
    from loop.os.ocr import available

    return available()


def pick_prompt_ocr(raws: Sequence[str]) -> str:
    """Prefer a candidate that contains an interact verb over longer garbage."""
    best = ""
    best_score = -1
    for raw in raws:
        text = " ".join(str(raw or "").split())
        if not text:
            continue
        score = _ocr_score(text)
        if score > best_score:
            best_score = score
            best = text
    return best


def _ocr_score(text: str) -> int:
    blob = text.lower()
    hits = sum(1 for verb in _PROMPT_OCR_VERBS if verb in blob)
    letters = sum(ch.isalpha() for ch in text)
    return hits * 10 + min(letters, 24)


def _ocr_preps(gray: Image.Image) -> list[Image.Image]:
    contrast = ImageEnhance.Contrast(gray).enhance(1.8)
    inverted = ImageOps.invert(contrast)
    binary = contrast.point(lambda p: 255 if p > 150 else 0)
    return [inverted, ImageOps.invert(binary), contrast]


def read_prompt_text(image: Image.Image) -> str:
    """OCR the zoomed prompt inset. Optional pytesseract; empty if missing/fail.

    Elden copy is light-on-dark; invert + a couple of PSM modes beat a single
    grayscale pass. Stop early when a known interact verb appears.
    """
    global _OCR_MISSING
    pytesseract = _bind_tesseract()
    if pytesseract is None:
        _OCR_MISSING = True
        return ""
    try:
        inset = prompt_inset(image).convert("L")
        iw, ih = inset.size
        if iw >= 4 and ih >= 2:
            inset = inset.resize((iw * 2, ih * 2), Image.Resampling.LANCZOS)
        raws: list[str] = []
        for prep in _ocr_preps(inset):
            for psm in (7, 6):
                raw = pytesseract.image_to_string(
                    prep, config=_OCR_WHITELIST.format(psm=psm)
                )
                text = " ".join(str(raw or "").split())
                raws.append(text)
                if _ocr_score(text) >= 10:
                    return pick_prompt_ocr(raws)
    except Exception:  # noqa: BLE001 — OCR must never abort collect
        _OCR_MISSING = True
        return ""
    return pick_prompt_ocr(raws)


def prompt_looks_locked(text: str) -> bool:
    """True when prompt copy is a condition (need item), not Open/Talk."""
    blob = " ".join((text or "").lower().split())
    if not blob:
        return False
    return any(marker in blob for marker in _LOCK_MARKERS)


def stuck_prompt(
    *,
    prompt_on: bool,
    glyph: str | None,
    last_interrupt_glyph: str | None,
) -> bool:
    """Same face glyph still up after an interrupt tap — lock even with empty OCR."""
    if not prompt_on:
        return False
    now = (glyph or "").strip().lower()
    prev = (last_interrupt_glyph or "").strip().lower()
    return bool(now) and now == prev


def skip_interact_interrupt(
    *,
    prompt_text: str,
    prompt_on: bool,
    glyph: str | None,
    last_interrupt_glyph: str | None,
) -> bool:
    """Yield the VLM when copy is a lock, or the bar did not leave after an interrupt tap."""
    return prompt_looks_locked(prompt_text) or stuck_prompt(
        prompt_on=prompt_on,
        glyph=glyph,
        last_interrupt_glyph=last_interrupt_glyph,
    )


def update_last_interrupt_glyph(
    last: str | None,
    *,
    interrupt: bool,
    glyph: str | None,
    prompt_on: bool,
    moved: bool,
) -> str | None:
    """Keep the face we already tapped so the next tick can yield the VLM.

    Look/walk while the same bar is still up does not reset. A one-tick CV miss
    keeps the glyph so the bar coming back yields instead of mashing. Reset when
    the glyph changes, or when we leave the volume (moved after the bar is gone).
    """
    now = (glyph or "").strip().lower() or None
    prev = (last or "").strip().lower() or None
    if interrupt and now:
        return now
    if prompt_on:
        if now and prev and now != prev:
            return None
        return prev
    if moved:
        return None
    return prev


def compose_obs(image: Image.Image, *, boxes: Sequence[Any] | None = None) -> Image.Image:
    """Full frame (optional painted boxes) with a labeled PROMPT inset banner at the bottom."""
    if boxes:
        try:
            from loop.os.ground import paint_boxes

            base = paint_boxes(image, boxes)
        except Exception:  # noqa: BLE001 — overlay must not abort collect
            base = image.convert("RGB").copy()
    else:
        base = image.convert("RGB").copy()
    inset = prompt_inset(image)
    iw, ih = inset.size
    bw, bh = base.size
    if iw < 1 or ih < 1 or bw < 1:
        return base
    scale = min(1.0, bw / float(iw))
    if scale < 1.0:
        inset = inset.resize(
            (max(1, int(iw * scale)), max(1, int(ih * scale))),
            Image.Resampling.LANCZOS,
        )
        iw, ih = inset.size
    label_h = max(14, ih // 8)
    banner_h = ih + label_h + 4
    out = Image.new("RGB", (bw, bh + banner_h), (0, 0, 0))
    out.paste(base, (0, 0))
    draw = ImageDraw.Draw(out)
    draw.rectangle((0, bh, bw, bh + banner_h), fill=(8, 8, 8))
    draw.rectangle((0, bh, bw - 1, bh + banner_h - 1), outline=(255, 220, 0), width=2)
    try:
        font = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        font = None
    draw.text((6, bh + 1), "PROMPT", fill=(255, 220, 0), font=font)
    x0 = max(0, (bw - iw) // 2)
    out.paste(inset, (x0, bh + label_h + 2))
    return out


def ui_contrast(image: Image.Image) -> float:
    """Higher when the prompt band has bright glyphs on a dark bar."""
    band = ui_band(image).convert("L")
    stats = ImageStat.Stat(band)
    std = float(stats.stddev[0]) / 255.0
    mean = float(stats.mean[0]) / 255.0
    return std + 0.25 * mean


def _prompt_center_grid(image: Image.Image) -> Image.Image | None:
    """Scan the 2× inset. Do not crop _prompt_band again — that is already the inset."""
    inset = prompt_inset(image).convert("L")
    iw, ih = inset.size
    if iw < 8 or ih < 2:
        return None
    c0 = int(iw * _PROMPT_CENTER[0])
    c1 = max(c0 + 1, int(iw * _PROMPT_CENTER[1]))
    center = inset.crop((c0, 0, c1, ih))
    return center.resize(_PROMPT_GRID, Image.Resampling.BILINEAR)


def _center_row(row: list[int]) -> list[int]:
    n = len(row)
    c0 = int(n * 0.25)
    c1 = max(c0 + 1, int(n * 0.75))
    return row[c0:c1]


def _thin_run_with_text(
    flags: list[bool],
    text_flags: list[bool],
    *,
    min_rows: int,
    max_rows: int,
) -> bool:
    """Longest contrast run must be a thin pill and contain consecutive text rows.

    Tall torso/clothing blocks fail the length cap. Specular glints without
    a real glyph fail the text-run check.
    """
    run = 0
    best = (0, 0, 0)
    start = 0
    for i, flag in enumerate(flags):
        if flag:
            if run == 0:
                start = i
            run += 1
            if run > best[0]:
                best = (run, start, i + 1)
        else:
            run = 0
    if run > best[0]:
        best = (run, start, len(flags))
    length, y0, y1 = best
    if not (min_rows <= length <= max_rows):
        return False
    text_run = 0
    text_best = 0
    for y in range(y0, y1):
        if text_flags[y]:
            text_run += 1
            text_best = max(text_best, text_run)
        else:
            text_run = 0
    return text_best >= min_rows


def _dark_row_prompt(small: Image.Image) -> bool:
    pix = small.load()
    width, height = small.size
    flags: list[bool] = []
    text_flags: list[bool] = []
    for y in range(height):
        row = _center_row([int(pix[x, y]) for x in range(width)])
        n = len(row)
        dark = sum(1 for value in row if value <= _PROMPT_DARK_MAX)
        bright = sum(1 for value in row if value >= _PROMPT_BRIGHT_MIN)
        text = sum(1 for value in row if value >= _PROMPT_TEXT_MIN)
        flags.append((dark / n) >= _PROMPT_DARK_FRAC and bright >= _PROMPT_MIN_BRIGHT)
        text_flags.append(text >= _PROMPT_MIN_TEXT)
    return _thin_run_with_text(
        flags, text_flags, min_rows=_PROMPT_MIN_ROWS, max_rows=_PROMPT_MAX_ROWS
    )


def _contrast_strip_prompt(small: Image.Image) -> bool:
    """Translucent pill on mid-luma stone: high per-row std + bright chips."""
    pix = small.load()
    width, height = small.size
    flags: list[bool] = []
    text_flags: list[bool] = []
    for y in range(height):
        row = _center_row([int(pix[x, y]) for x in range(width)])
        n = len(row)
        mean = sum(row) / n
        var = sum((value - mean) ** 2 for value in row) / n
        std = var**0.5
        bright = sum(1 for value in row if value >= _PROMPT_BRIGHT_MIN)
        text = sum(1 for value in row if value >= _PROMPT_TEXT_MIN)
        flags.append(std >= _PROMPT_ROW_STD_MIN and bright >= _PROMPT_CONTRAST_BRIGHT)
        text_flags.append(text >= _PROMPT_MIN_TEXT)
    return _thin_run_with_text(
        flags,
        text_flags,
        min_rows=_PROMPT_CONTRAST_ROWS,
        max_rows=_PROMPT_MAX_ROWS,
    )


def prompt_visible(image: Image.Image) -> bool:
    """True when the zoomed PROMPT inset has a dark/high-contrast pill with glyphs.

    Mid-luma stone floors / message glows alone stay false so explore is not locked
    into face-button mash. Dark-row shape OR high-contrast strip both count.
    """
    small = _prompt_center_grid(image)
    if small is None:
        return False
    return _dark_row_prompt(small) or _contrast_strip_prompt(small)


def _pill_y_span(inset: Image.Image) -> tuple[int, int]:
    """Vertical span of the highest-std rows — the thin prompt pill, not a floor strip."""
    gray = inset.convert("L")
    width, height = gray.size
    pix = gray.load()
    x0 = int(width * 0.15)
    x1 = max(x0 + 1, int(width * 0.85))
    stds: list[float] = []
    brights: list[int] = []
    for y in range(height):
        row = [int(pix[x, y]) for x in range(x0, x1)]
        mean = sum(row) / len(row)
        var = sum((value - mean) ** 2 for value in row) / len(row)
        stds.append(var**0.5)
        brights.append(sum(1 for value in row if value >= _PROMPT_BRIGHT_MIN))
    scored = [(stds[y], y) for y in range(height) if brights[y] >= 2]
    if not scored:
        scored = [(stds[y], y) for y in range(height)]
    best_std, best_y = max(scored)
    floor = max(_PROMPT_ROW_STD_MIN, 0.45 * best_std)
    y0 = y1 = best_y
    while y0 > 0 and stds[y0 - 1] >= floor:
        y0 -= 1
    while y1 + 1 < height and stds[y1 + 1] >= floor:
        y1 += 1
    pad = max(2, (y1 - y0) // 3)
    return max(0, y0 - pad), min(height, y1 + 1 + pad)


def _face_from_rgb(r: int, g: int, b: int) -> str | None:
    chroma = max(r, g, b) - min(r, g, b)
    if chroma < 28:
        return None
    if g >= r and g >= b and g - min(r, b) >= 20:
        return "a"
    if r >= g and r >= b and r - min(g, b) >= 20 and b < 140:
        return "b"
    if b >= r and b >= g and b - min(r, g) >= 20:
        return "x"
    if r >= 150 and g >= 130 and b < 120 and r - b >= 35:
        return "y"
    return None


def prompt_glyph(image: Image.Image) -> str | None:
    """Cheap Xbox face-glyph hint from the left-center bright chip. None if unknown."""
    if not prompt_visible(image):
        return None
    inset = prompt_inset(image).convert("RGB")
    bw, bh = inset.size
    if bw < 8 or bh < 4:
        return None
    # Glyph chip sits just left of the centered verb, on the pill rows only.
    y0, y1 = _pill_y_span(inset)
    x0 = int(bw * 0.12)
    x1 = max(x0 + 1, int(bw * 0.58))
    chip = inset.crop((x0, y0, x1, y1))
    pix = chip.load()
    cw, ch = chip.size
    votes: dict[str, float] = {"a": 0.0, "b": 0.0, "x": 0.0, "y": 0.0}
    n_bright = 0
    for y in range(ch):
        for x in range(cw):
            r, g, b = pix[x, y][:3]
            luma = (r + g + b) / 3
            if luma < 90:
                continue
            n_bright += 1
            face = _face_from_rgb(r, g, b)
            if face is not None:
                chroma = max(r, g, b) - min(r, g, b)
                votes[face] += chroma
    if n_bright < 2:
        return None
    best = max(votes, key=lambda name: votes[name])
    if votes[best] <= 0:
        return None
    return best


def bright_patches(
    image: Image.Image,
    *,
    k: int = 3,
    grid: tuple[int, int] = (32, 18),
) -> list[tuple[float, float]]:
    """Normalized (x, y) of the brightest unmatched cells, UI band excluded."""
    gw, gh = grid
    luma = image.convert("L").resize((max(4, gw), max(4, gh)), Image.Resampling.BILINEAR)
    w, h = luma.size
    pix = luma.load()
    ignore_y = max(1, int(h * 0.75))
    cells: list[tuple[float, int, int]] = []
    acc: list[float] = []
    for y in range(ignore_y):
        for x in range(w):
            value = float(pix[x, y]) / 255.0
            acc.append(value)
            cells.append((value, x, y))
    if not acc:
        return []
    mean = sum(acc) / len(acc)
    var = sum((value - mean) ** 2 for value in acc) / len(acc)
    thresh = max(0.28, mean + 1.1 * (var**0.5))
    ranked = [row for row in cells if row[0] >= thresh]
    ranked.sort(reverse=True)
    taken: list[tuple[float, int, int]] = []
    for value, x, y in ranked:
        if any(abs(x - tx) + abs(y - ty) <= 2 for _, tx, ty in taken):
            continue
        taken.append((value, x, y))
        if len(taken) >= k:
            break
    return [((x + 0.5) / w, (y + 0.5) / h) for _, x, y in taken]


def gold_glows(
    image: Image.Image,
    *,
    k: int = 2,
    grid: tuple[int, int] = (32, 18),
) -> list[tuple[float, float]]:
    """Gold/yellow chroma blobs below HUD and above the prompt band.

    Backup when DINO misses a corpse loot trail. Skips sky and the interact strip.
    """
    import numpy as np

    gw, gh = grid
    small = image.convert("RGB").resize((max(4, gw), max(4, gh)), Image.Resampling.BILINEAR)
    arr = np.asarray(small)
    height, width = arr.shape[:2]
    y0 = max(1, int(height * 0.12))
    y1 = max(y0 + 1, int(height * 0.78))
    band = arr[y0:y1]
    red = band[..., 0].astype(np.int16)
    green = band[..., 1].astype(np.int16)
    blue = band[..., 2].astype(np.int16)
    chroma = np.maximum(np.maximum(red, green), blue) - np.minimum(np.minimum(red, green), blue)
    gold = (
        (red > 150)
        & (green > 105)
        & (blue < 110)
        & (red >= green)
        & ((red - blue) >= 35)
        & (chroma >= 36)
    )
    ys, xs = np.nonzero(gold)
    if len(xs) == 0:
        return []
    scored = list(zip((chroma[ys, xs]).tolist(), xs.tolist(), ys.tolist(), strict=False))
    scored.sort(reverse=True)
    taken: list[tuple[int, int]] = []
    for _, x, y in scored:
        if any(abs(x - tx) + abs(y - ty) <= 2 for tx, ty in taken):
            continue
        taken.append((x, y))
        if len(taken) >= k:
            break
    out: list[tuple[float, float]] = []
    for x, y in taken:
        out.append(((x + 0.5) / width, (y0 + y + 0.5) / height))
    return out


def nearest_stick_dir(dx: float, dy_up: float) -> str:
    """8-way name for a screen offset. dx right, dy_up toward the top of the frame."""
    mag = math.hypot(dx, dy_up)
    if mag < 1e-6:
        return "n"
    ux, uy = dx / mag, dy_up / mag
    best, best_dot = "n", -2.0
    for name, (sx, sy) in _STICK_VEC.items():
        sm = math.hypot(sx, sy) or 1.0
        dot = ux * (sx / sm) + uy * (sy / sm)
        if dot > best_dot:
            best, best_dot = name, dot
    return best


def walk_dir_for_point(x: float, y: float) -> str:
    """stick_l toward a normalized screen point. Sky lights still walk forward.

    Centered goals (door dead ahead) map to n, not e/w oscillation from tiny dx.
    """
    dx = x - 0.5
    if abs(dx) <= 0.18:
        return "n"
    dy_up = 0.52 - y
    if y < _SKY_Y:
        dy_up = max(dy_up, 0.35)
    dy_up = max(dy_up, 0.22)
    direction = nearest_stick_dir(dx, dy_up)
    if direction in {"s", "se", "sw"}:
        return "n"
    if direction == "e":
        return "ne"
    if direction == "w":
        return "nw"
    return direction


def look_dir_for_point(x: float, y: float) -> str:
    return nearest_stick_dir(x - 0.5, 0.5 - y)


def pick_walk_patch(patches: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Prefer a ground/mid patch over a roof light so the camera does not pitch into the sky."""
    if not patches:
        return None
    ground = [row for row in patches if row[1] >= _SKY_Y]
    pool = ground or list(patches)
    return min(pool, key=lambda row: (row[0] - 0.5) ** 2 + (row[1] - 0.55) ** 2)


def format_patches(patches: list[tuple[float, float]]) -> str:
    bits: list[str] = []
    for x, y in patches:
        kind = "sky" if y < _SKY_Y else "ahead"
        bits.append(f"{x:.2f},{y:.2f}→stick_l_{walk_dir_for_point(x, y)}({kind})")
    return " ".join(bits)


def motion_curve(frames: list[Image.Image]) -> list[float]:
    if not frames:
        return []
    base = roi_thumb(frames[0], 64)
    return [0.0, *[mean_absdiff(base, roi_thumb(frame, 64)) for frame in frames[1:]]]


def pick_peak(frames: list[Image.Image]) -> Image.Image:
    """Most-changed frame vs the first (jump, turn), not the highest UI contrast."""
    if not frames:
        raise ValueError("pick_peak needs at least one frame")
    mads = motion_curve(frames)
    idx = max(range(len(frames)), key=lambda i: mads[i])
    return frames[idx]


def contact_sheet(frames: list[Image.Image], *, n: int = 4, height: int = 140) -> Image.Image:
    """One image: evenly spaced burst stills. Avoids sending N separate VLM encodes."""
    if not frames:
        raise ValueError("contact_sheet needs at least one frame")
    picked = _even_frames(frames, n)
    tiles: list[Image.Image] = []
    for frame in picked:
        rgb = frame.convert("RGB")
        scale = height / max(1, rgb.size[1])
        width = max(1, int(rgb.size[0] * scale))
        tiles.append(rgb.resize((width, height), Image.Resampling.BILINEAR))
    gap = 2
    total_w = sum(tile.size[0] for tile in tiles) + gap * (len(tiles) - 1)
    sheet = Image.new("RGB", (total_w, height), (0, 0, 0))
    x = 0
    for tile in tiles:
        sheet.paste(tile, (x, 0))
        x += tile.size[0] + gap
    return sheet


def burst_caption(mads: list[float], *, prompt: bool, n: int) -> str:
    if not mads:
        curve = "0.00"
    else:
        pts = _even_values(mads, 4)
        curve = "→".join(f"{value:.2f}" for value in pts)
    return f"burst {n} frames, mad {curve}, after_prompt={'yes' if prompt else 'no'}"


def result_caption(
    *,
    chord: str,
    mad: float,
    pixel_changed: bool,
    prompt_before: bool,
    prompt_after: bool,
) -> str:
    """Text for the VLM: what the last chord actually did (not mid-motion tiles)."""
    name = (chord or "?").lower()
    face = name in {"a", "b", "x", "y"}
    if face:
        if prompt_before and not prompt_after:
            outcome = "prompt-cleared"
        elif prompt_after or prompt_before:
            # Jump/hop MAD must not read as travel success while the box stays up.
            outcome = "tap-failed/prompt-still"
        elif pixel_changed:
            outcome = "hop/anim-not-travel"
        else:
            outcome = "blocked/no-change"
    else:
        outcome = "moved" if pixel_changed else "blocked/no-change"
    return (
        f"last result: {name} → {outcome} mad={mad:.3f} "
        f"prompt_now={'yes' if prompt_after else 'no'}"
    )


def _even_frames(frames: list[Image.Image], n: int) -> list[Image.Image]:
    if len(frames) <= n:
        return list(frames)
    last = len(frames) - 1
    idxs = sorted({round(i * last / (n - 1)) for i in range(n)})
    return [frames[i] for i in idxs]


def _even_values(values: list[float], n: int) -> list[float]:
    if len(values) <= n:
        return list(values)
    last = len(values) - 1
    idxs = sorted({round(i * last / (n - 1)) for i in range(n)})
    return [values[i] for i in idxs]


@dataclass
class BurstResult:
    peak: Image.Image
    after: Image.Image
    frames: list[Image.Image] = field(default_factory=list)
    mads: list[float] = field(default_factory=list)
    sheet: Image.Image | None = None
    caption: str = ""


class PeakSampler:
    """Grab ~hz during a blocking chord, then a short trail. No extra VLM."""

    def __init__(self, grab: Grab, *, hz: float = 10.0, trail_s: float = 0.5) -> None:
        self._grab = grab
        self._interval = 1.0 / max(1.0, hz)
        self._trail_s = max(0.0, trail_s)
        self._frames: list[Image.Image] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._frames = [_safe_copy(self._grab)]
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def finish(self) -> BurstResult:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None
        deadline = time.perf_counter() + self._trail_s
        while time.perf_counter() < deadline:
            frame = _safe_copy(self._grab)
            if frame is not None:
                self._frames.append(frame)
            remain = deadline - time.perf_counter()
            if remain > 0:
                time.sleep(min(self._interval, remain))
        frames = [frame for frame in self._frames if frame is not None]
        if not frames:
            last = self._grab()
            frames = [last]
        mads = motion_curve(frames)
        peak = pick_peak(frames)
        after = frames[-1]
        sheet = contact_sheet(frames) if len(frames) >= 2 else after
        caption = burst_caption(mads, prompt=prompt_visible(after), n=len(frames))
        return BurstResult(peak=peak, after=after, frames=frames, mads=mads, sheet=sheet, caption=caption)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            frame = _safe_copy(self._grab)
            if frame is not None:
                self._frames.append(frame)


def _safe_copy(grab: Grab) -> Image.Image | None:
    try:
        return grab().copy()
    except Exception:
        return None
