"""Spatial track ids. No game names, clicks, or schema writes."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from loop.adaptive.evidence import iou

BBox = tuple[float, float, float, float]
OCR_TRACK_IOU = 0.3
RETURN_WINDOW = 30
LABEL_CONFIDENCE = 0.25

# Appearance descriptor: 8x8 mean RGB cells, i.e. APPEARANCE_GRID**2 * 3 bytes.
APPEARANCE_GRID = 8
# Mean per-channel difference (0-255) between two descriptors that still counts
# as the same object. A SHA digest has no usable distance: one noisy pixel broke
# a return match completely.
APPEARANCE_MAX_DISTANCE = 40.0
# Area-ratio bounds between the remembered and current box (hover scaling).
SCALE_TOLERANCE = 3.0
# Centre distance that still counts as the same place when IoU has collapsed.
RETURN_RADIUS = 0.25
# A candidate must beat its runner-up by this much, otherwise the row abstains.
IOU_MARGIN = 0.15
RETURN_MARGIN = 8.0
# Hard bound on remembered-but-absent identities.
MAX_LOST = 64


@dataclass(frozen=True)
class TrackHit:
    track_id: str
    bbox: BBox
    appearance: bytes
    detector_label: str
    confidence: float
    state: str  # new | continuing | returned
    role: str = "unknown"
    source: str = ""
    class_hint: str = ""


class TrackMemory:
    """new / continuing / returned over detector boxes.

    Identity is evidence, not a digest: role, scale plausibility, tolerant
    appearance distance, geometry and the bounded absence window must all agree
    before a remembered id is re-used. Ambiguous evidence abstains and creates a
    new id instead of transferring an arbitrary identity.
    """

    def __init__(self, window: int = RETURN_WINDOW, *, max_lost: int = MAX_LOST) -> None:
        self.window = window
        self.max_lost = max_lost
        self.live: dict[str, tuple[BBox, bytes, str]] = {}
        self._live_role: dict[str, str] = {}
        self.lost: list[tuple[str, bytes, str, int, BBox]] = []
        self._lost_role: dict[str, str] = {}
        self._next = 0

    def clear_live(self) -> None:
        for tid, (bbox, app, label) in self.live.items():
            self.lost.append((tid, app, label, 0, bbox))
            self._lost_role[tid] = self._live_role.get(tid, "unknown")
        self.live = {}
        self._live_role = {}
        self._trim_lost()

    def reset(self) -> None:
        """Forget geometry but never reissue an id in the same authority scope."""

        self.live = {}
        self._live_role = {}
        self.lost = []
        self._lost_role = {}
        # `_next` is deliberately not reset: a reset screen must not hand an old
        # actionable id to a different object.

    def _trim_lost(self) -> None:
        if self.max_lost > 0:
            self.lost = self.lost[-self.max_lost :]

    @staticmethod
    def _plausible(
        remembered: tuple[BBox, bytes, str],
        remembered_role: str,
        bbox: BBox,
        app: bytes,
        role: str,
    ) -> bool:
        """Role, scale and tolerant appearance must agree before an id is re-used."""

        prev_bbox, prev_app, _label = remembered
        if not _roles_match(remembered_role, role):
            return False
        if not _scale_ok(prev_bbox, bbox):
            return False
        return appearance_distance(prev_app, app) <= APPEARANCE_MAX_DISTANCE

    def reconcile(
        self,
        raw: list[tuple[BBox, str, float, bytes, str | None] | tuple[BBox, str, float, bytes, str | None, str]],
    ) -> tuple[TrackHit, ...]:
        self.lost = [
            (tid, app, label, age + 1, bbox)
            for tid, app, label, age, bbox in self.lost
            if age + 1 <= self.window
        ]
        prev = self.live
        prev_role = dict(self._live_role)
        lost = {tid: (app, label, bbox) for tid, app, label, _age, bbox in self.lost}
        lost_role = dict(self._lost_role)
        used_prev: set[str] = set()
        used_lost: set[str] = set()
        assigned: list[tuple[str, str, BBox, bytes, str, float, str]] = []
        claimed: set[int] = set()
        rows: list[tuple[BBox, str, float, bytes, str | None, str]] = []

        for item in raw:
            bbox, label, conf, app, pref = item[0], item[1], item[2], item[3], item[4]
            role = item[5] if len(item) > 5 else "unknown"
            rows.append((bbox, label, conf, app, pref, role))

        # 1. A detector-supplied id is the strongest evidence, but a changed
        #    object on the old box still fails appearance, role or scale.
        for index, (bbox, label, conf, app, pref, role) in enumerate(rows):
            if not pref:
                continue
            if (
                pref in prev
                and pref not in used_prev
                and self._plausible(prev[pref], prev_role.get(pref, "unknown"), bbox, app, role)
            ):
                used_prev.add(pref)
                claimed.add(index)
                assigned.append((pref, "continuing", bbox, app, _keep(label, prev[pref][2]), conf, role))
            elif (
                pref in lost
                and pref not in used_lost
                and self._plausible(lost[pref], lost_role.get(pref, "unknown"), bbox, app, role)
            ):
                used_lost.add(pref)
                claimed.add(index)
                assigned.append((pref, "returned", bbox, app, _keep(label, lost[pref][1]), conf, role))

        pending = [index for index in range(len(rows)) if index not in claimed]

        # 2. Continuation before return: a live box is stronger evidence, and
        #    the appearance distance keeps content, not position, in control.
        continuing: list[tuple[int, str, float]] = []
        for index in pending:
            bbox, _label, _conf, app, _pref, role = rows[index]
            for tid, (prev_bbox, prev_app, _prev_label) in prev.items():
                if tid in used_prev:
                    continue
                if not _roles_match(prev_role.get(tid, "unknown"), role):
                    continue
                if not _scale_ok(prev_bbox, bbox):
                    continue
                if appearance_distance(prev_app, app) > APPEARANCE_MAX_DISTANCE:
                    continue
                score = iou(prev_bbox, bbox)
                if score < OCR_TRACK_IOU:
                    continue
                continuing.append((index, tid, score))
        for index, tid in _assign_one_to_one(continuing, margin=IOU_MARGIN):
            used_prev.add(tid)
            claimed.add(index)
            bbox, label, conf, app, _pref, role = rows[index]
            assigned.append((tid, "continuing", bbox, app, _keep(label, prev[tid][2]), conf, role))

        # 3. Returns need the same role, a plausible scale/place and a tolerant
        #    appearance match; the closest descriptor wins or the row abstains.
        returning: list[tuple[int, str, float]] = []
        for index in [item for item in pending if item not in claimed]:
            bbox, _label, _conf, app, _pref, role = rows[index]
            for tid, (lost_app, _lost_label, lost_bbox) in lost.items():
                if tid in used_lost or tid in used_prev:
                    continue
                if not _roles_match(lost_role.get(tid, "unknown"), role):
                    continue
                if not _scale_ok(lost_bbox, bbox) or not _nearby(lost_bbox, bbox):
                    continue
                distance = appearance_distance(lost_app, app)
                if distance > APPEARANCE_MAX_DISTANCE:
                    continue
                returning.append((index, tid, -distance))
        for index, tid in _assign_one_to_one(returning, margin=RETURN_MARGIN):
            used_lost.add(tid)
            claimed.add(index)
            bbox, label, conf, app, _pref, role = rows[index]
            assigned.append((tid, "returned", bbox, app, _keep(label, lost[tid][1]), conf, role))

        # 4. Everything else is a new identity. `_next` advances even when the
        #    detector supplied the id, so generated ids stay deterministic.
        for index in [item for item in range(len(rows)) if item not in claimed]:
            bbox, label, conf, app, pref, role = rows[index]
            # A supplied id that is still remembered belongs to that other
            # object; reissuing it here would put one id on two identities.
            remembered = bool(pref) and (pref in prev or pref in lost)
            tid = pref if pref and not remembered else f"trk_{self._next}"
            self._next += 1
            assigned.append((tid, "new", bbox, app, label, conf, role))

        self.lost = [
            (tid, app, label, age, bbox)
            for tid, app, label, age, bbox in self.lost
            if tid not in used_lost and tid not in used_prev
        ]
        self._lost_role = {tid: lost_role[tid] for tid, *_ in self.lost if tid in lost_role}
        for tid, (bbox, app, label) in prev.items():
            if tid not in used_prev and tid not in used_lost:
                self.lost.append((tid, app, label, 0, bbox))
                self._lost_role[tid] = prev_role.get(tid, "unknown")
        self._trim_lost()

        self.live = {}
        self._live_role = {}
        hits: list[TrackHit] = []
        seen: set[str] = set()
        for tid, state, bbox, app, label, conf, role in assigned:
            if tid in seen:
                continue
            seen.add(tid)
            self.live[tid] = (bbox, app, label)
            self._live_role[tid] = role
            hits.append(TrackHit(tid, bbox, app, label, conf, state, role=role))
        return tuple(hits)


def _keep(new_label: str, old_label: str) -> str:
    """Labels stay mutable: a fresh non-empty label replaces the remembered one."""

    return new_label or old_label


def _roles_match(left: str, right: str) -> bool:
    if left == right:
        return True
    return left == "unknown" or right == "unknown"


def _area(bbox: BBox) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _center_distance(left: BBox, right: BBox) -> float:
    left_x, left_y = (left[0] + left[2]) * 0.5, (left[1] + left[3]) * 0.5
    right_x, right_y = (right[0] + right[2]) * 0.5, (right[1] + right[3]) * 0.5
    return ((left_x - right_x) ** 2 + (left_y - right_y) ** 2) ** 0.5


def _scale_ok(prev: BBox, bbox: BBox) -> bool:
    prev_area, area = _area(prev), _area(bbox)
    if prev_area <= 0.0 or area <= 0.0:
        return False
    ratio = area / prev_area
    return (1.0 / SCALE_TOLERANCE) <= ratio <= SCALE_TOLERANCE


def _nearby(prev: BBox, bbox: BBox) -> bool:
    if iou(prev, bbox) >= OCR_TRACK_IOU:
        return True
    return _center_distance(prev, bbox) <= RETURN_RADIUS


def appearance_distance(left: bytes, right: bytes) -> float:
    """Mean per-channel distance between two crop descriptors, inf if incomparable."""

    if not left or len(left) != len(right):
        return float("inf")
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)


def _assign_one_to_one(
    candidates: list[tuple[int, str, float]], *, margin: float
) -> list[tuple[int, str]]:
    """Deterministic, ambiguity-aware one-to-one assignment.

    `candidates` are (row index, track id, quality); higher quality is a better
    match. A row is assigned only when its best candidate beats the runner-up by
    `margin`, so equal-looking options abstain instead of handing over an
    arbitrary identity. Rows are visited best-evidence-first, so the strongest
    match claims a contested track.

    # ponytail: greedy with an ambiguity guard, not a global assignment solver.
    # Upgrade path: use a standard assignment library if reviewed reorder or
    # crossing sequences still swap ids.
    """

    by_row: dict[int, list[tuple[float, str]]] = {}
    for row, track_id, quality in candidates:
        by_row.setdefault(row, []).append((quality, track_id))
    ranked_rows = sorted(
        by_row,
        key=lambda row: (-max(quality for quality, _tid in by_row[row]), row),
    )
    used: set[str] = set()
    pairs: list[tuple[int, str]] = []
    for row in ranked_rows:
        ranked = sorted(by_row[row], key=lambda item: (-item[0], item[1]))
        best_quality, best_track = ranked[0]
        if best_track in used:
            continue
        if len(ranked) > 1 and best_quality - ranked[1][0] < margin:
            continue
        used.add(best_track)
        pairs.append((row, best_track))
    return pairs


def crop_appearance(image: Image.Image, bbox: BBox) -> bytes:
    """Compact tolerant descriptor: scale-normalised 8x8 mean RGB cells.

    The crop is resized to the grid, so hover scaling still matches, and the raw
    cell bytes support `appearance_distance` instead of sha256's all-or-nothing
    equality. A hash may still key an exact cache; it is not a distance feature.
    """

    w, h = image.size
    x0, y0, x1, y1 = bbox
    px0, py0 = max(0, int(x0 * w)), max(0, int(y0 * h))
    px1 = min(w, max(px0 + 1, int(x1 * w)))
    py1 = min(h, max(py0 + 1, int(y1 * h)))
    crop = image.crop((px0, py0, px1, py1)).convert("RGB")
    return crop.resize((APPEARANCE_GRID, APPEARANCE_GRID)).tobytes()


def clip_bbox(bbox: BBox) -> BBox | None:
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(v))) for v in bbox)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def detections_to_raw(
    image: Image.Image,
    detections: list[dict],
    *,
    label_conf: float = LABEL_CONFIDENCE,
) -> list[tuple[BBox, str, float, bytes, str | None]]:
    raw = []
    for det in detections:
        bbox = clip_bbox(tuple(det["bbox"]))  # type: ignore[arg-type]
        if bbox is None:
            continue
        conf = float(det.get("conf") or 0.0)
        label = str(det.get("label") or "")
        if conf < label_conf:
            label = ""
        app = det.get("appearance")
        if not isinstance(app, (bytes, bytearray)):
            app = crop_appearance(image, bbox)
        pref = det.get("track_id")
        raw.append((bbox, label, conf, bytes(app), None if pref is None else str(pref)))
    return raw


def bind_ocr_to_tracks(
    hits: tuple[TrackHit, ...],
    tokens: list[tuple[str, BBox]],
) -> dict[int, str]:
    """Token index → track id when it overlaps exactly one hit at or above IoU 0.3."""

    bound: dict[int, str] = {}
    for index, (_text, box) in enumerate(tokens):
        owners = [hit.track_id for hit in hits if iou(box, hit.bbox) >= OCR_TRACK_IOU]
        if len(owners) == 1:
            bound[index] = owners[0]
    return bound


class ScriptedTracker:
    """Test double. Each update consumes one scripted frame of boxes."""

    def __init__(self, frames: list[list[tuple]]) -> None:
        self._frames = frames
        self._i = 0
        self._mem = TrackMemory()
        self.status = "ok"
        self.associator = "scripted"
        self.load_ms = 0.0

    def clear_live(self) -> None:
        self._mem.clear_live()

    def reset(self) -> None:
        self._mem.reset()
        self._i = 0

    def update(self, image: Image.Image) -> tuple[TrackHit, ...]:
        if self.status != "ok":
            return ()
        frame = self._frames[self._i] if self._i < len(self._frames) else []
        self._i += 1
        raw = []
        for item in frame:
            bbox, label, conf = item[0], item[1], float(item[2])
            app = item[3] if len(item) > 3 and item[3] is not None else crop_appearance(image, bbox)
            pref = item[4] if len(item) > 4 else None
            raw.append((tuple(bbox), str(label), conf, bytes(app), None if pref is None else str(pref)))
        return self._mem.reconcile(raw)


def open_tracker(name: str, *, prompts: list[str] | None = None):
    """None keeps today's sensor. Weights load only for a named arm."""

    if name in ("", "none"):
        return None
    prompts = list(prompts or [])
    if name == "yolo-world":
        from loop.adaptive.yolo_world import YoloWorldTracker

        return YoloWorldTracker(prompts)
    if name == "sam2":
        from loop.adaptive.sam2_tracker import Sam2Tracker

        return Sam2Tracker(prompts)
    if name in ("fused", "fused-gpa", "fused-base"):
        from loop.adaptive.fused_perception import FusedPerceptionTracker

        return FusedPerceptionTracker(name, prompts=prompts)
    raise ValueError(f"unknown tracker: {name}")
