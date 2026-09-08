from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Literal, Sequence

from PIL import Image, ImageFilter, ImageOps

from avatar.harness.capture import LongestSide
from avatar.harness.profile import CapturePreset, CropRect

ANALYZE_SIDE = 128
FG_THRESHOLD = 22
MIN_BLOB_PX = 12
MIN_AREA_FRAC = 0.12
MAX_AREA_FRAC = 0.95
MAX_FG_FRAC = 0.85
CORNER_SPREAD = 40
HUD_GAP_FRAC = 0.85
HUD_OVERLAP_FRAC = 0.20
HUD_HEADER_X_SLACK_FRAC = 0.25
MIN_SATELLITE_AREA_FRAC = 0.008
HUD_RESCAN_SIDE = 256
DEFAULT_PAD = (0.04, 0.04, 0.04, 0.04)
DEFAULT_SIDES = (256, 384, 512, 768)
Blob = tuple[int, int, int, int, int]  # x0, y0, x1, y1, area


@dataclass(frozen=True)
class LayoutPlan:
    crop: CropRect | None
    longest_side: int
    jpeg_quality: int
    screen_kind: str
    source: Literal["cv", "yaml", "ui"]


def crop_iou(a: CropRect | None, b: CropRect | None) -> float:
    """IoU of two normalized CropRects. Missing rects → 0."""
    aa = _xyxy(a)
    bb = _xyxy(b)
    if aa is None or bb is None:
        return 0.0
    ix0 = max(aa[0], bb[0])
    iy0 = max(aa[1], bb[1])
    ix1 = min(aa[2], bb[2])
    iy1 = min(aa[3], bb[3])
    iw = max(0.0, ix1 - ix0)
    ih = max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = (aa[2] - aa[0]) * (aa[3] - aa[1])
    area_b = (bb[2] - bb[0]) * (bb[3] - bb[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def choose_longest_side(
    native_longest: int,
    sides: Sequence[int],
    cap: int,
) -> int:
    """Smallest ladder step that can hold *native_longest* without downscale, else max under cap."""
    ladder = sorted({int(s) for s in sides if int(s) > 0} | ({cap} if cap > 0 else set()))
    if not ladder:
        ladder = list(DEFAULT_SIDES)
    cap_i = cap if cap > 0 else max(ladder)
    allowed = [s for s in ladder if s <= cap_i] or [min(ladder)]
    native = max(1, native_longest)
    no_downscale = [s for s in allowed if s >= native]
    if no_downscale:
        return min(no_downscale)
    under = [s for s in allowed if s <= native]
    return max(under) if under else min(allowed)


def pad_crop(crop: CropRect, pad: Sequence[float]) -> CropRect:
    left_p, top_p, right_p, bottom_p = _as_pad(pad)
    x0, y0, x1, y1 = crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3]
    x0 = max(0.0, x0 - left_p)
    y0 = max(0.0, y0 - top_p)
    x1 = min(1.0, x1 + right_p)
    y1 = min(1.0, y1 + bottom_p)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def floor_crop_y0(crop: CropRect, yaml_crop: CropRect | None) -> CropRect:
    """Raise CV y0 to the YAML gameplay floor so title/score chrome stays out."""
    if yaml_crop is None or len(yaml_crop) != 4:
        return crop
    x0, y0, w, h = crop
    yy0 = float(yaml_crop[1])
    if y0 >= yy0:
        return crop
    bottom = y0 + h
    return (x0, yy0, w, max(0.0, bottom - yy0))


def diagnose_layout(
    image: Image.Image,
    *,
    fallback: CapturePreset,
    pad: Sequence[float] = DEFAULT_PAD,
    sides: Sequence[int] = DEFAULT_SIDES,
) -> LayoutPlan:
    """Invent a gameplay crop from a full-window grab. Fixture-agnostic; YAML is fallback.

    Playfield is the largest compact blob. HUD (score, next, hold) attaches when it
    shares an axis with the playfield and sits in a playfield-relative ring above
    or beside it. Footer copy below the board is excluded.
    """
    jpeg = int(fallback.jpeg_quality)
    cap_side = _cap_side(fallback.longest_side)
    yaml_plan = LayoutPlan(
        crop=fallback.crop,
        longest_side=_yaml_side(fallback.longest_side, sides, cap_side),
        jpeg_quality=jpeg,
        screen_kind="gameplay",
        source="yaml",
    )
    rgb = image.convert("RGB")
    native_w, native_h = rgb.size
    if native_w < 8 or native_h < 8:
        return yaml_plan

    thumb = ImageOps.contain(rgb, (ANALYZE_SIDE, ANALYZE_SIDE), Image.Resampling.BILINEAR)
    tw, th = thumb.size
    binary = _foreground_mask(thumb)
    fg_frac = _white_fraction(binary)
    if fg_frac > MAX_FG_FRAC:
        return yaml_plan
    opened = binary.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    blobs = _blobs(opened)
    playfield = _pick_playfield(blobs, tw, th)
    if playfield is None:
        return yaml_plan

    sx = native_w / tw
    sy = native_h / th
    native_pf = _scale_blob(playfield, sx, sy)
    native_blobs = [_scale_blob(b, sx, sy) for b in blobs]
    union = _union_aligned_hud(native_pf, native_blobs, native_w, native_h)
    union = _extend_with_ring_scan(rgb, native_pf, union)
    nx0 = union[0] / native_w
    ny0 = union[1] / native_h
    nx1 = (union[2] + 1) / native_w
    ny1 = (union[3] + 1) / native_h
    raw = (nx0, ny0, max(0.0, nx1 - nx0), max(0.0, ny1 - ny0))
    crop = floor_crop_y0(pad_crop(raw, pad), fallback.crop)
    area = crop[2] * crop[3]
    if area < MIN_AREA_FRAC or area > MAX_AREA_FRAC:
        return yaml_plan

    native_longest = int(round(max(crop[2] * native_w, crop[3] * native_h)))
    return LayoutPlan(
        crop=crop,
        longest_side=choose_longest_side(native_longest, sides, cap_side),
        jpeg_quality=jpeg,
        screen_kind="gameplay",
        source="cv",
    )


def _cap_side(value: LongestSide) -> int:
    return int(value) if isinstance(value, int) and value > 0 else max(DEFAULT_SIDES)


def _yaml_side(value: LongestSide, sides: Sequence[int], cap: int) -> int:
    if isinstance(value, int) and value > 0:
        return choose_longest_side(value, sides, cap)
    return choose_longest_side(cap, sides, cap)


def _as_pad(pad: Sequence[float]) -> tuple[float, float, float, float]:
    if len(pad) != 4:
        return DEFAULT_PAD
    return (float(pad[0]), float(pad[1]), float(pad[2]), float(pad[3]))


def _xyxy(crop: CropRect | None) -> tuple[float, float, float, float] | None:
    if crop is None:
        return None
    x0, y0, w, h = crop
    if w <= 0 or h <= 0:
        return None
    return (x0, y0, x0 + w, y0 + h)


def _foreground_mask(thumb: Image.Image) -> Image.Image:
    luma = thumb.convert("L")
    bg = _background_luma(luma)
    pix = luma.load()
    w, h = luma.size
    out = Image.new("L", (w, h), 0)
    dest = out.load()
    for y in range(h):
        for x in range(w):
            if abs(int(pix[x, y]) - bg) >= FG_THRESHOLD:
                dest[x, y] = 255
    return out


def _background_luma(luma: Image.Image) -> int:
    """Corners if they agree (chrome); otherwise a 1px border. Works for light or dark UIs."""
    w, h = luma.size
    p = max(2, min(8, w // 4, h // 4))
    corner_vals: list[list[int]] = []
    for box in ((0, 0, p, p), (w - p, 0, w, p), (0, h - p, p, h), (w - p, h - p, w, h)):
        corner_vals.append(list(luma.crop(box).tobytes()))
    medians = [int(median(v)) for v in corner_vals if v]
    if medians and max(medians) - min(medians) <= CORNER_SPREAD:
        flat = [n for group in corner_vals for n in group]
        return int(median(flat)) if flat else 128
    border: list[int] = []
    pix = luma.load()
    for x in range(w):
        border.append(int(pix[x, 0]))
        border.append(int(pix[x, h - 1]))
    for y in range(h):
        border.append(int(pix[0, y]))
        border.append(int(pix[w - 1, y]))
    return int(median(border)) if border else 128


def _white_fraction(binary: Image.Image) -> float:
    hist = binary.histogram()
    white = hist[128] if len(hist) > 128 else 0
    white += sum(hist[129:]) if len(hist) > 129 else 0
    total = binary.size[0] * binary.size[1]
    return white / total if total else 1.0


def _blobs(binary: Image.Image) -> list[Blob]:
    w, h = binary.size
    pix = binary.load()
    seen = [[False] * w for _ in range(h)]
    blobs: list[Blob] = []
    for y in range(h):
        for x in range(w):
            if seen[y][x] or pix[x, y] < 128:
                continue
            q: deque[tuple[int, int]] = deque([(x, y)])
            seen[y][x] = True
            minx = maxx = x
            miny = maxy = y
            area = 0
            while q:
                cx, cy = q.popleft()
                area += 1
                minx = min(minx, cx)
                maxx = max(maxx, cx)
                miny = min(miny, cy)
                maxy = max(maxy, cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and pix[nx, ny] >= 128:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            if area >= MIN_BLOB_PX:
                blobs.append((minx, miny, maxx, maxy, area))
    return blobs


def _is_thin_strip(blob: Blob, tw: int, th: int) -> bool:
    x0, y0, x1, y1, _area = blob
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    if bw > 0.55 * tw and bh < 0.08 * th:
        return True
    if bh > 0.55 * th and bw < 0.08 * tw:
        return True
    return False


def _pick_playfield(blobs: list[Blob], tw: int, th: int) -> Blob | None:
    thumb_area = tw * th
    candidates = [
        b
        for b in blobs
        if not _is_thin_strip(b, tw, th) and b[4] < 0.90 * thumb_area
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda b: b[4])


def _scale_blob(blob: Blob, sx: float, sy: float) -> Blob:
    x0, y0, x1, y1, _area = blob
    nx0 = int(round(x0 * sx))
    ny0 = int(round(y0 * sy))
    nx1 = max(nx0, int(round((x1 + 1) * sx)) - 1)
    ny1 = max(ny0, int(round((y1 + 1) * sy)) - 1)
    return (nx0, ny0, nx1, ny1, max(1, (nx1 - nx0 + 1) * (ny1 - ny0 + 1)))


def _axis_overlap_frac(a0: int, a1: int, b0: int, b1: int) -> float:
    overlap = max(0, min(a1, b1) - max(a0, b0) + 1)
    smaller = min(a1 - a0 + 1, b1 - b0 + 1)
    return overlap / smaller if smaller > 0 else 0.0


def _sep_gap(a0: int, a1: int, b0: int, b1: int) -> int:
    """Gap between two inclusive 1D ranges; 0 if they touch or overlap."""
    if a1 < b0:
        return b0 - a1 - 1
    if b1 < a0:
        return a0 - b1 - 1
    return 0


def _hud_slot(playfield: Blob, blob: Blob) -> str | None:
    """Return 'header' or 'side' when blob is axis-aligned HUD; None for footer/other."""
    px0, py0, px1, py1, _ = playfield
    bx0, by0, bx1, by1, _ = blob
    if bx0 >= px0 and by0 >= py0 and bx1 <= px1 and by1 <= py1:
        return None
    pw = max(1, px1 - px0 + 1)
    ph = max(1, py1 - py0 + 1)
    x_ov = _axis_overlap_frac(px0, px1, bx0, bx1)
    y_ov = _axis_overlap_frac(py0, py1, by0, by1)
    bcx = (bx0 + bx1) / 2
    bcy = (by0 + by1) / 2
    if bcy > py1 and x_ov >= HUD_OVERLAP_FRAC:
        return None
    if bcy < py0:
        x_slack = HUD_HEADER_X_SLACK_FRAC * pw
        aligned_x = x_ov >= HUD_OVERLAP_FRAC or _sep_gap(px0, px1, bx0, bx1) <= x_slack
        if aligned_x:
            return "header" if _sep_gap(py0, py1, by0, by1) <= HUD_GAP_FRAC * ph else None
    if bcx < px0 and y_ov >= HUD_OVERLAP_FRAC:
        return "side" if _sep_gap(px0, px1, bx0, bx1) <= HUD_GAP_FRAC * pw else None
    if bcx > px1 and y_ov >= HUD_OVERLAP_FRAC:
        return "side" if _sep_gap(px0, px1, bx0, bx1) <= HUD_GAP_FRAC * pw else None
    return None


def _is_hud_satellite(playfield: Blob, blob: Blob, frame_w: int, frame_h: int) -> bool:
    """True when blob is header/side HUD aligned with the playfield, not footer copy."""
    if blob == playfield or _is_thin_strip(blob, frame_w, frame_h):
        return False
    _px0, _py0, _px1, _py1, parea = playfield
    min_area = max(MIN_BLOB_PX, int(parea * MIN_SATELLITE_AREA_FRAC))
    if blob[4] < min_area:
        return False
    return _hud_slot(playfield, blob) is not None


def _union_aligned_hud(
    playfield: Blob,
    blobs: list[Blob],
    frame_w: int,
    frame_h: int,
) -> list[int]:
    """Grow playfield by axis-aligned HUD (score above, next/hold beside). Skip footer."""
    union = [playfield[0], playfield[1], playfield[2], playfield[3]]
    for blob in blobs:
        if not _is_hud_satellite(playfield, blob, frame_w, frame_h):
            continue
        union[0] = min(union[0], blob[0])
        union[1] = min(union[1], blob[1])
        union[2] = max(union[2], blob[2])
        union[3] = max(union[3], blob[3])
    return union


def _detect_blobs(image: Image.Image, side: int) -> tuple[list[Blob], int, int]:
    thumb = ImageOps.contain(image.convert("RGB"), (side, side), Image.Resampling.BILINEAR)
    tw, th = thumb.size
    if tw < 8 or th < 8:
        return [], tw, th
    binary = _foreground_mask(thumb)
    opened = binary.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return _blobs(opened), tw, th


def _extend_with_ring_scan(image: Image.Image, playfield: Blob, union: list[int]) -> list[int]:
    """Re-scan header/side strips at higher res so small SCORE boxes survive the 128px thumb."""
    native_w, native_h = image.size
    px0, py0, px1, py1, _ = playfield
    pw = max(1, px1 - px0 + 1)
    ph = max(1, py1 - py0 + 1)
    gy = int(HUD_GAP_FRAC * ph)
    gx = int(HUD_GAP_FRAC * pw)
    hx = int(HUD_HEADER_X_SLACK_FRAC * pw)
    rings = (
        (max(0, px0 - hx), max(0, py0 - gy), min(native_w - 1, px1 + hx), max(0, py0 - 1)),
        (max(0, px0 - gx), max(0, py0), max(0, px0 - 1), min(native_h - 1, py1)),
        (min(native_w - 1, px1 + 1), max(0, py0), min(native_w - 1, px1 + gx), min(native_h - 1, py1)),
    )
    out = list(union)
    for cx0, cy0, cx1, cy1 in rings:
        if cx1 - cx0 < 8 or cy1 - cy0 < 8:
            continue
        crop = image.crop((cx0, cy0, cx1 + 1, cy1 + 1))
        blobs, stw, sth = _detect_blobs(crop, HUD_RESCAN_SIDE)
        sw, sh = crop.size
        for blob in blobs:
            if _is_thin_strip(blob, stw, sth) or blob[4] < MIN_BLOB_PX:
                continue
            bx0, by0, bx1, by1, _ = blob
            nx0 = cx0 + int(bx0 * sw / stw)
            ny0 = cy0 + int(by0 * sh / sth)
            nx1 = cx0 + max(int(bx0 * sw / stw), int((bx1 + 1) * sw / stw) - 1)
            ny1 = cy0 + max(int(by0 * sh / sth), int((by1 + 1) * sh / sth) - 1)
            native_blob = (nx0, ny0, nx1, ny1, max(1, (nx1 - nx0 + 1) * (ny1 - ny0 + 1)))
            if _hud_slot(playfield, native_blob) is None:
                continue
            out[0] = min(out[0], nx0)
            out[1] = min(out[1], ny0)
            out[2] = max(out[2], nx1)
            out[3] = max(out[3], ny1)
    return out
