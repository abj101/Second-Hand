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
    a = np.asarray(img)
    _, counts = np.unique(a.reshape(-1, a.shape[-1]), axis=0, return_counts=True)
    return bool(counts.max() >= 0.95 * a.shape[0] * a.shape[1])


def changed(a: Image.Image, b: Image.Image, thresh: int = 16) -> float:
    a, b = np.asarray(a).astype(np.int16), np.asarray(b).astype(np.int16)
    return float(np.mean(np.max(np.abs(a - b), axis=-1) > thresh))


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
