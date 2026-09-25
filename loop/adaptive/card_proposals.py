"""Split a coarse hand region into individual card proposals."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PIL import Image, ImageDraw

from loop.adaptive.gui_yolo import filter_proposals
from loop.adaptive.tracker import clip_bbox

BBox = tuple[float, float, float, float]
_SOURCE = "card-split"
_MIN_SEG_FRAC = 0.045
_CENTER_EPS = 1e-4
# Frozen offer contract (matching_rules.json MAX_OFFER_AREA_FRACTION): geometry larger
# than this screen fraction is counted as an oversized/false offer, so the splitter
# must not emit it. Cards are ~0.12 x 0.22; the measured development fan hand is 0.13.
MAX_CARD_AREA_FRACTION = 0.25


def propose_hand_cards(
    image: Image.Image,
    hand_bbox: BBox,
    ocr_tokens: Sequence[tuple[str, BBox]] | None = None,
    *,
    frame_id: str = "",
    confidence: float = 0.85,
) -> list[dict]:
    hand = clip_bbox(hand_bbox)
    if hand is None:
        return []
    hx0, hy0, hx1, hy1 = hand
    if _area(hand) >= 0.95:
        return []

    crop = image.crop(_px_box(image.size, hand))
    gray = np.asarray(crop.convert("L"), dtype=np.float32)
    segments = _visual_segments(gray)
    tokens = _tokens_in_hand(ocr_tokens or (), hand)
    anchors = _title_anchors(tokens, hand)

    if anchors is None:
        return []
    if len(anchors) >= 2:
        # Fanned cards have no dark gaps. The top text in each horizontal
        # column marks a distinct visible card; lower text is card detail.
        boxes = _ocr_column_boxes(anchors, hand)
    elif len(segments) >= 2:
        boxes = _segments_to_boxes(segments, gray.shape[1], hand)
    else:
        return []

    if len(anchors) < 2:
        boxes = _refine_with_ocr(boxes, tokens, hand)
    boxes = _drop_inseparable(boxes, gray, hand)
    boxes = _unique_centers(boxes)
    # A column may be handed a whole decorative panel or a screen-spanning region:
    # oversized geometry is withheld rather than offered as a card.
    boxes = [box for box in boxes if _area(box) <= MAX_CARD_AREA_FRACTION]
    if len(boxes) < 1:
        return []

    raw = [
        {"bbox": bbox, "class_hint": "playing card", "confidence": confidence}
        for bbox in boxes
    ]
    proposals, _diag = filter_proposals(
        raw, frame_id=frame_id, role="ui", source=_SOURCE
    )
    return proposals


def _area(bbox: BBox) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _px_box(size: tuple[int, int], bbox: BBox) -> tuple[int, int, int, int]:
    w, h = size
    x0, y0, x1, y1 = bbox
    px0 = int(x0 * w)
    py0 = int(y0 * h)
    px1 = max(px0 + 1, int(x1 * w))
    py1 = max(py0 + 1, int(y1 * h))
    return px0, py0, min(w, px1), min(h, py1)


def _smooth(values: np.ndarray, radius: int = 2) -> np.ndarray:
    if values.size == 0:
        return values
    pad = np.pad(values, (radius, radius), mode="edge")
    kernel = np.ones(radius * 2 + 1, dtype=np.float32)
    kernel /= kernel.sum()
    return np.convolve(pad, kernel, mode="valid")


def _visual_segments(gray: np.ndarray) -> list[tuple[int, int]]:
    h, w = gray.shape
    if w < 8 or h < 8:
        return []
    col = _smooth(gray.mean(axis=0), 2)
    floor = float(np.percentile(col, 15))
    ceiling = float(np.percentile(col, 85))
    span = ceiling - floor
    if span < 4.0:
        return []
    min_seg = max(3, int(w * _MIN_SEG_FRAC))
    is_gap = col < floor + span * 0.28
    segments: list[tuple[int, int]] = []
    start: int | None = None
    for i in range(w):
        if not is_gap[i]:
            if start is None:
                start = i
            continue
        if start is not None and i - start >= min_seg:
            segments.append((start, i))
        start = None
    if start is not None and w - start >= min_seg:
        segments.append((start, w))
    return segments


def _segments_to_boxes(
    segments: list[tuple[int, int]], crop_w: int, hand: BBox
) -> list[BBox]:
    hx0, hy0, hx1, hy1 = hand
    span = hx1 - hx0
    return [
        (hx0 + (s / crop_w) * span, hy0, hx0 + (e / crop_w) * span, hy1)
        for s, e in segments
    ]


def _tokens_in_hand(
    tokens: Sequence[tuple[str, BBox]], hand: BBox
) -> list[tuple[str, BBox, float]]:
    hx0, hy0, hx1, hy1 = hand
    kept: list[tuple[str, BBox, float]] = []
    for text, bbox in tokens:
        cx = (bbox[0] + bbox[2]) * 0.5
        cy = (bbox[1] + bbox[3]) * 0.5
        if hx0 <= cx <= hx1 and hy0 <= cy <= hy1:
            kept.append((text, bbox, cx))
    return sorted(kept, key=lambda row: row[2])


def _title_anchors(
    tokens: list[tuple[str, BBox, float]], hand: BBox
) -> list[tuple[str, BBox, float]] | None:
    """Choose the upper text in each card column; withhold ambiguous peers."""

    if not tokens:
        return []
    column_radius = max(0.025, (hand[2] - hand[0]) * 0.08)
    columns: list[list[tuple[str, BBox, float]]] = []
    for token in tokens:
        text, _box, cx = token
        if not text.strip() or text.strip().isdecimal():
            continue
        if columns and abs(cx - sum(t[2] for t in columns[-1]) / len(columns[-1])) <= column_radius:
            columns[-1].append(token)
        else:
            columns.append([token])
    anchors: list[tuple[str, BBox, float]] = []
    for column in columns:
        ordered = sorted(column, key=lambda t: (t[1][1] + t[1][3]) * 0.5)
        if len(ordered) > 1:
            first_y = (ordered[0][1][1] + ordered[0][1][3]) * 0.5
            second_y = (ordered[1][1][1] + ordered[1][1][3]) * 0.5
            if second_y - first_y < 0.04:
                return None
        anchors.append(ordered[0])
    return anchors


def _ocr_column_boxes(tokens: list[tuple[str, BBox, float]], hand: BBox) -> list[BBox]:
    hx0, hy0, hx1, hy1 = hand
    if len(tokens) < 2:
        return []
    xs = [t[2] for t in tokens]
    edges = [hx0]
    for a, b in zip(xs, xs[1:]):
        edges.append((a + b) * 0.5)
    edges.append(hx1)
    boxes: list[BBox] = []
    for i in range(len(tokens)):
        x0 = max(hx0, edges[i])
        x1 = min(hx1, edges[i + 1])
        if x1 - x0 > 0.01:
            boxes.append((x0, hy0, x1, hy1))
    return boxes


def _refine_with_ocr(
    boxes: list[BBox], tokens: list[tuple[str, BBox, float]], hand: BBox
) -> list[BBox]:
    if not boxes or not tokens:
        return boxes
    hx0, hy0, hx1, hy1 = hand
    by_token: dict[int, list[int]] = {}
    for ti, (_text, _bbox, cx) in enumerate(tokens):
        owners = [
            bi
            for bi, box in enumerate(boxes)
            if box[0] - 1e-6 <= cx <= box[2] + 1e-6
        ]
        if len(owners) == 1:
            by_token.setdefault(owners[0], []).append(ti)
    if any(len(v) > 1 for v in by_token.values()):
        return []
    if len(by_token) == len(tokens) and len(by_token) == len(boxes):
        return boxes
    if len(tokens) == len(boxes):
        refined: list[BBox] = []
        for (_text, _bbox, cx), box in zip(tokens, boxes):
            half = (box[2] - box[0]) * 0.5
            x0 = max(hx0, cx - half)
            x1 = min(hx1, cx + half)
            refined.append((x0, hy0, x1, hy1))
        return refined
    return boxes


def _drop_inseparable(boxes: list[BBox], gray: np.ndarray, hand: BBox) -> list[BBox]:
    if len(boxes) <= 1:
        if len(boxes) == 1 and (hand[2] - hand[0]) > 0.22:
            col = gray.mean(axis=0)
            if float(col.std()) < 6.0:
                return []
        return boxes
    return boxes


def _unique_centers(boxes: list[BBox]) -> list[BBox]:
    out: list[BBox] = []
    centers: list[tuple[float, float]] = []
    for box in boxes:
        cx = (box[0] + box[2]) * 0.5
        cy = (box[1] + box[3]) * 0.5
        if any(abs(cx - px) < _CENTER_EPS and abs(cy - py) < _CENTER_EPS for px, py in centers):
            continue
        centers.append((cx, cy))
        out.append(box)
    return out


def synth_hand_image(
    *,
    size: tuple[int, int] = (320, 180),
    hand: BBox = (0.1, 0.55, 0.9, 0.95),
    card_count: int = 5,
    gap_px: int = 6,
    overlap_px: int = 0,
    hover_index: int | None = None,
    hover_scale: float = 1.2,
    solid: bool = False,
) -> Image.Image:
    """Synthetic hand layout for tests only (no game art)."""

    image = Image.new("RGB", size, (20, 24, 28))
    draw = ImageDraw.Draw(image)
    w, h = size
    px = _px_box(size, hand)
    if solid:
        draw.rectangle((px[0] + 4, px[1] + 4, px[2] - 4, px[3] - 4), fill=(180, 40, 40))
        return image
    inner_w = px[2] - px[0] - 8
    card_w = max(12, (inner_w - gap_px * (card_count - 1)) // card_count)
    x = px[0] + 4
    for i in range(card_count):
        cw = card_w
        if hover_index == i:
            cw = int(card_w * hover_scale)
        rect = (x, px[1] + 6, x + cw, px[3] - 6)
        color = (120 + i * 18, 150, 190 + i * 10)
        draw.rectangle(rect, fill=color)
        draw.text((rect[0] + 4, rect[1] + 4), f"C{i + 1}", fill=(240, 240, 240))
        step = cw - overlap_px + gap_px
        x += max(step, 1)
        if i + 1 < card_count and gap_px > 0:
            draw.rectangle((x - gap_px, px[1] + 4, x, px[3] - 4), fill=(20, 24, 28))
    return image
