from collections import deque
from dataclasses import dataclass
import hashlib

import numpy as np
from PIL import Image


@dataclass
class Settle:
    frame: Image.Image
    settled: bool
    waited: int
    blank_seen: bool

def _blank(img: Image.Image) -> bool:
    a = np.asarray(img).astype(np.uint32)  # one int per pixel: a flat sort, not a row-wise one (~10 ms -> <1 ms per frame)
    v = (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2] if a.ndim == 3 else a
    return bool(np.unique(v.ravel(), return_counts=True)[1].max() >= 0.95 * a.shape[0] * a.shape[1])


def changed(a: Image.Image, b: Image.Image, thresh: int = 16) -> float:
    a, b = np.asarray(a).astype(np.int16), np.asarray(b).astype(np.int16)
    return float(np.mean(np.max(np.abs(a - b), axis=-1) > thresh))


def _residual(frames: list[Image.Image], shifts: list[tuple[int, int]]):
    if len(frames) < 2 or len(shifts) != len(frames): return None, 0, 0, False
    first = np.asarray(frames[0]).astype(np.int16); h, w = first.shape[:2]
    heat = np.zeros((h, w), dtype=np.uint16)
    for frame, (dx, dy) in zip(frames[1:], shifts[1:]):
        image = np.asarray(frame).astype(np.int16)
        sx, sy = w - abs(dx), h - abs(dy)
        if sx <= 0 or sy <= 0: continue
        a = first[max(-dy, 0):max(-dy, 0) + sy, max(-dx, 0):max(-dx, 0) + sx]
        b = image[max(dy, 0):max(dy, 0) + sy, max(dx, 0):max(dx, 0) + sx]
        changed_pixels = np.max(np.abs(a - b), axis=-1) > 16
        heat[max(-dy, 0):max(-dy, 0) + sy, max(-dx, 0):max(-dx, 0) + sx] += changed_pixels
    total = int(heat.sum())
    if not total: return None, 0, 0, False
    # ponytail: infer one tile from this move's largest cumulative camera shift; callers already calibrate tile_px.
    tile = max(1, max(max(abs(x), abs(y)) for x, y in shifts))
    ys, xs = np.nonzero(heat)
    threshold = max(1, int(heat.max() * .2))
    ys, xs = np.nonzero(heat >= threshold)
    if not len(xs): return None, tile, 0, False
    raw = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    limit = max(1, int(1.5 * tile)); summed = np.pad(heat.cumsum(0, dtype=np.int64).cumsum(1, dtype=np.int64), ((1, 0), (1, 0)))
    best, best_weight = None, 0
    for y0 in range(h):
        y1 = min(h, y0 + limit)
        for x0 in range(w):
            x1 = min(w, x0 + limit)
            weight = int(summed[y1, x1] - summed[y0, x1] - summed[y1, x0] + summed[y0, x0])
            if weight > best_weight: best, best_weight = (x0, y0, x1, y1), weight
    coverage = best_weight / total
    if coverage < .8: return raw, tile, coverage, False
    x0, y0, x1, y1 = best
    ys, xs = np.nonzero(heat[y0:y1, x0:x1])
    return (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1), tile, coverage, True


def residual_box(frames: list[Image.Image], shifts: list[tuple[int, int]]) -> tuple[int, int, int, int] | None:
    box = _residual(frames, shifts)[0]
    if box is None: return None
    dx, dy = (round(v / 2) for v in shifts[-1])
    return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy


def residual(frames: list[Image.Image], shifts: list[tuple[int, int]]) -> tuple[float, float] | None:
    box, _tile, _coverage, accepted = _residual(frames, shifts)
    if box is None or not accepted: return None
    x0, y0, x1, y1 = box; dx, dy = shifts[-1]; w, h = frames[0].size
    return ((x0 + x1 + dx) / (2 * w), (y0 + y1 + dy) / (2 * h))


def wait_settled(env, *, quiet: int = 24, max_frames: int = 600) -> Settle:
    quiet = max(1, quiet)
    frame = env.screenshot()
    last = frame if not _blank(frame) else None
    blank_seen = last is None
    hashes = deque([hashlib.sha256(frame.tobytes()).digest()], maxlen=quiet)
    consecutive = waited = 0
    for waited in range(1, max_frames + 1):
        env.tick()
        frame = env.screenshot()
        is_blank = _blank(frame)
        blank_seen = blank_seen or is_blank
        if not is_blank:
            last = frame
        key = hashlib.sha256(frame.tobytes()).digest()
        is_quiet = key in hashes and not is_blank
        consecutive = consecutive + 1 if is_quiet else 0
        hashes.append(key)
        if consecutive >= quiet:
            return Settle(frame, True, waited, blank_seen)
    return Settle(last if last is not None else frame, False, waited, blank_seen)

def scroll(a: Image.Image, b: Image.Image, max_shift: int = 4) -> tuple[int, int] | None:
    a, b = np.asarray(a), np.asarray(b)
    h, w = a.shape[:2]
    scores = []
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            sx, sy = w - abs(dx), h - abs(dy)
            aa = a[max(-dy, 0):max(-dy, 0) + sy, max(-dx, 0):max(-dx, 0) + sx]
            bb = b[max(dy, 0):max(dy, 0) + sy, max(dx, 0):max(dx, 0) + sx]
            scores.append((float(np.mean(np.abs(aa.astype(np.int16) - bb.astype(np.int16)))), dx, dy))
    scores.sort()
    # ponytail: 1.0 MAE unit is the ambiguity margin; raise if noisy captures need stricter matching.
    if len(scores) < 2 or scores[1][0] - scores[0][0] < 1.0:
        return None
    return scores[0][1], scores[0][2]

def for_model(img: Image.Image, long_side: int = 640) -> Image.Image:
    w, h = img.size
    if max(h, w) >= long_side:
        return img
    nw, nh = round(w * long_side / max(h, w)), round(h * long_side / max(h, w))
    return img.resize((nw, nh), Image.Resampling.NEAREST)

def snap(at: list[float], size: tuple[int, int], tile_px: int, phase: tuple[int, int] = (0, 0)) -> list[float]:
    w, h = size
    x, y = at[0] * w, at[1] * h
    x = phase[0] + round((x - phase[0] - tile_px / 2) / tile_px) * tile_px + tile_px / 2
    y = phase[1] + round((y - phase[1] - tile_px / 2) / tile_px) * tile_px + tile_px / 2
    return [min(1.0, max(0.0, x / w)), min(1.0, max(0.0, y / h))]

def cut(a: Image.Image, b: Image.Image, blank_seen: bool = False) -> bool:
    return blank_seen or (changed(a, b) > 0.5 and scroll(a, b) is None)
