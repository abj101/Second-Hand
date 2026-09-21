"""Neutral evidence extraction helpers (no game ontology)."""

from __future__ import annotations

import uuid
import re
from typing import Iterable

import numpy as np
from PIL import Image

from loop.adaptive.schema import EvidenceRecord

ALLOWED_KINDS = frozenset(
    {
        "ocr",
        "region",
        "motion",
        "color",
        "track",
        "vlm_hypothesis",
        "interaction_delta",
        "observation",
        "promotion",
        "action",
    }
)

OcrToken = tuple[str, tuple[float, float, float, float]]  # text, norm bbox xyxy

_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_RATIO = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*/\s*([-+]?\d+(?:\.\d+)?)\s*$")


def parse_numeric_text(text: str) -> tuple[int | float, int | float | None] | None:
    """Parse a standalone scalar or ``current/maximum`` OCR token.

    This deliberately carries no domain vocabulary.  Ratios are common in
    dashboards, games, media controls, and instrument panels.
    """

    compact = text.strip().replace(",", "")
    ratio = _RATIO.fullmatch(compact)
    if ratio:
        return _as_number(ratio.group(1)), _as_number(ratio.group(2))
    if _NUMBER.fullmatch(compact):
        return _as_number(compact), None
    return None


def _as_number(value: str) -> int | float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def new_evidence_id() -> str:
    return str(uuid.uuid4())


def evidence_record(kind: str, payload: dict) -> EvidenceRecord:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported evidence kind: {kind}")
    return EvidenceRecord(evidence_id=new_evidence_id(), kind=kind, payload=dict(payload))


def _grid_size(w: int, h: int, *, max_side: int = 120) -> tuple[int, int]:
    scale = min(1.0, max_side / max(w, h, 1))
    return max(1, int(w * scale)), max(1, int(h * scale))


def _corner_bg(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    pts = np.array(
        [rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]], dtype=np.float32
    )
    return pts.mean(axis=0)


def _components(mask: list[list[bool]]) -> list[list[tuple[int, int]]]:
    rows, cols = len(mask), len(mask[0]) if mask else 0
    seen = [[False] * cols for _ in range(rows)]
    out: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(cols):
            if not mask[r][c] or seen[r][c]:
                continue
            stack = [(r, c)]
            seen[r][c] = True
            cells: list[tuple[int, int]] = []
            while stack:
                cr, cc = stack.pop()
                cells.append((cr, cc))
                for nr, nc in ((cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)):
                    if 0 <= nr < rows and 0 <= nc < cols and mask[nr][nc] and not seen[nr][nc]:
                        seen[nr][nc] = True
                        stack.append((nr, nc))
            if len(cells) >= 4:
                out.append(cells)
    return out


def _cells_to_bbox(
    cells: list[tuple[int, int]], gw: int, gh: int, w: int, h: int
) -> tuple[float, float, float, float]:
    rs = [r for r, _ in cells]
    cs = [c for _, c in cells]
    x0 = min(cs) / gw * w
    y0 = min(rs) / gh * h
    x1 = (max(cs) + 1) / gw * w
    y1 = (max(rs) + 1) / gh * h
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def detect_regions(image: Image.Image) -> list[tuple[float, float, float, float]]:
    rgb = image.convert("RGB")
    w, h = rgb.size
    if w < 4 or h < 4:
        return []
    gw, gh = _grid_size(w, h)
    small = np.array(rgb.resize((gw, gh), Image.Resampling.BILINEAR))
    bg = _corner_bg(small)
    diff = np.abs(small.astype(np.int16) - bg.astype(np.int16)).sum(axis=2)
    thr = max(35, float(np.percentile(diff, 75)) * 0.5)
    mask = (diff > thr).tolist()
    bboxes: list[tuple[float, float, float, float]] = []
    for cells in _components(mask):
        bboxes.append(_cells_to_bbox(cells, gw, gh, w, h))
    return bboxes


def motion_bbox(
    previous: Image.Image, current: Image.Image, *, threshold: float = 10.0
) -> tuple[float, float, float, float] | None:
    prev = previous.convert("L")
    curr = current.convert("L")
    w, h = prev.size
    gw, gh = _grid_size(w, h)
    a = np.array(prev.resize((gw, gh), Image.Resampling.BILINEAR), dtype=np.int16)
    b = np.array(curr.resize((gw, gh), Image.Resampling.BILINEAR), dtype=np.int16)
    diff = np.abs(a - b)
    if not (diff > threshold).any():
        return None
    ys, xs = np.where(diff > threshold)
    x0 = float(xs.min()) / gw * w
    y0 = float(ys.min()) / gh * h
    x1 = float(xs.max() + 1) / gw * w
    y1 = float(ys.max() + 1) / gh * h
    return (x0 / w, y0 / h, x1 / w, y1 / h)


def color_features(
    image: Image.Image, bbox: tuple[float, float, float, float]
) -> dict:
    rgb = image.convert("RGB")
    w, h = rgb.size
    x0, y0, x1, y1 = bbox
    px0 = int(max(0, min(w - 1, x0 * w)))
    py0 = int(max(0, min(h - 1, y0 * h)))
    px1 = int(max(px0 + 1, min(w, x1 * w)))
    py1 = int(max(py0 + 1, min(h, y1 * h)))
    crop = np.array(rgb.crop((px0, py0, px1, py1)), dtype=np.float32)
    if crop.size == 0:
        return {"mean_rgb": [0.0, 0.0, 0.0], "aspect_ratio": 1.0}
    mean = crop.reshape(-1, 3).mean(axis=0)
    bw, bh = max(1, px1 - px0), max(1, py1 - py0)
    return {
        "mean_rgb": [round(float(c), 2) for c in mean],
        "aspect_ratio": round(bw / bh, 4),
        "bbox": list(bbox),
    }


def iou(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _bbox_center(bb: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)


def _feature_label(feat: dict | None) -> str:
    if not feat:
        return ""
    return str(feat.get("label") or "").strip().casefold()


def _color_sim(a: dict | None, b: dict | None) -> float:
    if not a or not b:
        return 0.0
    ra, rb = a.get("mean_rgb"), b.get("mean_rgb")
    if not isinstance(ra, (list, tuple)) or not isinstance(rb, (list, tuple)):
        return 0.0
    if len(ra) < 3 or len(rb) < 3:
        return 0.0
    dist = sum(abs(float(ra[i]) - float(rb[i])) for i in range(3)) / 3.0
    return max(0.0, 1.0 - dist / 40.0)


def match_score(
    prev: tuple[float, float, float, float],
    curr: tuple[float, float, float, float],
    prev_feat: dict | None = None,
    curr_feat: dict | None = None,
) -> float:
    geo = iou(prev, curr)
    pc, cc = _bbox_center(prev), _bbox_center(curr)
    dist = ((pc[0] - cc[0]) ** 2 + (pc[1] - cc[1]) ** 2) ** 0.5
    near = max(0.0, 1.0 - dist / 0.2)
    pl, cl = _feature_label(prev_feat), _feature_label(curr_feat)
    label = 1.0 if pl and cl and pl == cl else 0.0
    color = _color_sim(prev_feat, curr_feat)
    return 0.5 * geo + 0.25 * label + 0.15 * color + 0.10 * near


def assign_track_ids(
    prev_regions: list[tuple[float, float, float, float]],
    curr_regions: list[tuple[float, float, float, float]],
    *,
    min_iou: float = 0.15,
    prev_ids: list[int] | None = None,
    prev_features: list[dict] | None = None,
    curr_features: list[dict] | None = None,
) -> list[tuple[int, tuple[float, float, float, float]]]:
    """Mutual-best matching. Order of `curr_regions` does not change identities."""

    ids = prev_ids if prev_ids is not None else list(range(len(prev_regions)))
    n, m = len(curr_regions), len(prev_regions)
    if n == 0:
        return []
    if m == 0:
        return list(enumerate(curr_regions))

    # ponytail: O(n²) pairwise; Hungarian if region count grows past ~100
    scores = [
        [
            match_score(
                prev_regions[j],
                curr_regions[i],
                prev_features[j] if prev_features else None,
                curr_features[i] if curr_features else None,
            )
            for j in range(m)
        ]
        for i in range(n)
    ]
    curr_best = [max(range(m), key=lambda j, i=i: (scores[i][j], -j)) for i in range(n)]
    prev_best = [max(range(n), key=lambda i, j=j: (scores[i][j], -i)) for j in range(m)]

    pairs: list[tuple[float, int, int]] = []
    for i, j in enumerate(curr_best):
        if prev_best[j] != i:
            continue
        geo = iou(prev_regions[j], curr_regions[i])
        pl = _feature_label(prev_features[j] if prev_features else None)
        cl = _feature_label(curr_features[i] if curr_features else None)
        label_hit = bool(pl and cl and pl == cl)
        if geo >= min_iou or (label_hit and geo >= 0.05) or (label_hit and scores[i][j] >= 0.25):
            pairs.append((scores[i][j], i, j))
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_curr: set[int] = set()
    used_prev: set[int] = set()
    assigned: dict[int, int] = {}
    for _, i, j in pairs:
        if i in used_curr or j in used_prev:
            continue
        used_curr.add(i)
        used_prev.add(j)
        assigned[i] = ids[j]

    next_id = (max(ids) + 1) if ids else 0
    out: list[tuple[int, tuple[float, float, float, float]]] = []
    for i, curr in enumerate(curr_regions):
        if i in assigned:
            out.append((assigned[i], curr))
        else:
            out.append((next_id, curr))
            next_id += 1
    return out


def run_ocr(image: Image.Image, ocr: Iterable[OcrToken] | None) -> list[EvidenceRecord]:
    if ocr is None:
        return []
    if callable(ocr):
        tokens = list(ocr(image))
    else:
        tokens = list(ocr)
    out: list[EvidenceRecord] = []
    for item in tokens:
        if len(item) != 2:
            continue
        text, bbox = item
        payload = {"text": str(text), "bbox": [float(x) for x in bbox]}
        numeric = parse_numeric_text(str(text))
        if numeric is not None:
            payload["number"] = numeric[0]
            if numeric[1] is not None:
                payload["maximum"] = numeric[1]
        out.append(evidence_record("ocr", payload))
    return out
