"""Role-aware proposal merge, one identity per object, OCR text attachment."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PIL import Image

from loop.adaptive.evidence import iou
from loop.adaptive.tracker import (
    BBox,
    OCR_TRACK_IOU,
    TrackHit,
    TrackMemory,
    clip_bbox,
    crop_appearance,
)

MERGE_IOU = 0.7
FULL_SCREEN_AREA = 0.92
# A token belongs to the box that holds it: this much of the token must fall
# inside that box. Adjacent/fanned cards overlap, so a neighbour that merely
# clips the same token must not claim it.
OCR_CONTAINMENT_RATIO = 0.92
# Ownership evidence, strongest first: contained > covers the box centre > plain
# IoU overlap. A tie at the strongest tier abstains instead of picking one.
_OCR_TIER_CONTAINED = 3
_OCR_TIER_CENTER = 2
_OCR_TIER_OVERLAP = 1
UI_GEOM_SOURCES = frozenset({"gpa", "gpa-gui", "gui_yolo", "card", "card-split", "card_split"})
WORLD_GEOM_SOURCES = frozenset({"dino", "grounding-dino", "grounding_dino", "yolo-world"})


@dataclass(frozen=True)
class FrameProposal:
    bbox: BBox
    role: str  # ui | world | unknown
    source: str
    class_hint: str
    confidence: float
    frame_id: str | int = 0


@dataclass
class _Fused:
    bbox: BBox
    role: str
    class_hint: str
    label: str
    confidence: float
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FusionDiagnostics:
    """One frame's counters. Never accumulates the previous frame's totals."""

    rejections: dict[str, int] = field(default_factory=dict)
    merges: int = 0
    live_sources: dict[str, list[str]] = field(default_factory=dict)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    ocr_attached: int = 0
    ocr_orphans: list[dict[str, Any]] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1


def _area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _reject_reason(raw_bbox: BBox, clipped: BBox | None, role: str) -> str | None:
    if any(v < -1e-6 or v > 1.0 + 1e-6 for v in raw_bbox):
        return "off_frame"
    if clipped is None:
        return "malformed"
    if _area(clipped) >= FULL_SCREEN_AREA:
        return "full_screen"
    if role not in ("ui", "world", "unknown"):
        return "bad_role"
    return None


def _intersection_area(left: BBox, right: BBox) -> float:
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0])) * max(
        0.0, min(left[3], right[3]) - max(left[1], right[1])
    )


def _point_inside(bbox: BBox, x: float, y: float) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


def _ocr_tier(token: BBox, box: BBox) -> int:
    """Strongest ownership evidence a token has on one box; 0 means no claim."""

    token_area = _area(token)
    if token_area <= 0.0:
        return 0
    if _intersection_area(token, box) / token_area >= OCR_CONTAINMENT_RATIO:
        return _OCR_TIER_CONTAINED
    cx, cy = (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5
    if _point_inside(token, cx, cy):
        return _OCR_TIER_CENTER
    return _OCR_TIER_OVERLAP if iou(token, box) >= OCR_TRACK_IOU else 0


def bind_ocr_to_hits(
    hits: tuple[TrackHit, ...],
    tokens: list[tuple[str, BBox]],
    diag: FusionDiagnostics,
) -> dict[str, list[str]]:
    """Attach each OCR token to at most one hit, or report it as an orphan.

    A token is written onto a track only when exactly one hit provides the
    strongest ownership evidence, so a title inside a fanned card is not stolen
    by the neighbour it also overlaps, and an equally-shared token abstains
    instead of going to the first equal-looking track. Orphans keep their text
    and box for the caller to emit as diagnostic evidence; blank text carries
    no label and is ignored.
    """

    bound: dict[str, list[str]] = {}
    for text, box in tokens:
        if not text.strip():
            continue
        tiers = [(hit.track_id, _ocr_tier(box, hit.bbox)) for hit in hits]
        best = max((tier for _tid, tier in tiers), default=0)
        owners = sorted(tid for tid, tier in tiers if tier and tier == best)
        if len(owners) == 1:
            bound.setdefault(owners[0], []).append(text)
            diag.ocr_attached += 1
            continue
        reason = "ambiguous" if best else "unmatched"
        diag.reject(f"ocr_{reason}")
        diag.ocr_orphans.append(
            {
                "reason": reason,
                "text": text,
                "bbox": [float(v) for v in box],
                "candidates": owners,
            }
        )
    return bound


def _geom_rank(role: str, source: str) -> int:
    src = source.lower().replace("_", "-")
    if role == "ui" and (src in UI_GEOM_SOURCES or "gpa" in src or "card" in src):
        return 3
    if role == "world" and any(s in src for s in WORLD_GEOM_SOURCES):
        return 3
    if role == "ui" and "dino" in src:
        return 1
    return 2


def filter_proposals(
    proposals: list[FrameProposal],
    diag: FusionDiagnostics,
    *,
    junk_seen: set[str],
) -> list[FrameProposal]:
    kept: list[FrameProposal] = []
    for prop in proposals:
        clipped = clip_bbox(prop.bbox)
        reason = _reject_reason(prop.bbox, clipped, prop.role)
        if reason:
            diag.reject(reason)
            key = f"{reason}:{prop.bbox}:{prop.role}:{prop.source}"
            if key in junk_seen:
                diag.reject("repeat_junk")
            junk_seen.add(key)
            diag.exclusions.append(
                {
                    "reason": reason,
                    "source": prop.source,
                    "role": prop.role,
                    "confidence": prop.confidence,
                }
            )
            continue
        kept.append(
            FrameProposal(
                clipped,  # type: ignore[arg-type]
                prop.role,
                prop.source,
                prop.class_hint,
                prop.confidence,
                prop.frame_id,
            )
        )
    return kept


def merge_proposals(proposals: list[FrameProposal], diag: FusionDiagnostics) -> list[_Fused]:
    """High-IoU same-role duplicates only; UI geometry wins over DINO hint."""

    clusters: list[list[FrameProposal]] = []
    for prop in sorted(proposals, key=lambda p: (-_geom_rank(p.role, p.source), -p.confidence)):
        placed = False
        for group in clusters:
            if group[0].role != prop.role:
                continue
            rep = max(group, key=lambda p: (_geom_rank(p.role, p.source), p.confidence))
            if iou(rep.bbox, prop.bbox) < MERGE_IOU:
                continue
            placed = True
            group.append(prop)
            diag.merges += 1
            break
        if not placed:
            clusters.append([prop])

    fused: list[_Fused] = []
    for group in clusters:
        geom = max(group, key=lambda p: (_geom_rank(p.role, p.source), p.confidence))
        hints = [p.class_hint for p in group if p.class_hint]
        hint = next((h for h in hints if h), geom.class_hint)
        evidence = [
            {
                "source": p.source,
                "role": p.role,
                "confidence": p.confidence,
                "class_hint": p.class_hint,
            }
            for p in group
        ]
        label = hint or geom.class_hint
        fused.append(
            _Fused(
                geom.bbox,
                geom.role,
                hint,
                label,
                geom.confidence,
                evidence,
            )
        )
    return fused


class ProposalFusion:
    def __init__(self, *, window: int = 30) -> None:
        self.memory = TrackMemory(window=window)
        self.diagnostics = FusionDiagnostics()
        self._junk_seen: set[str] = set()

    def reset(self) -> None:
        self.memory.reset()
        self._junk_seen.clear()
        self.diagnostics = FusionDiagnostics()

    def clear_live(self) -> None:
        self.memory.clear_live()

    def fuse(
        self,
        image: Image.Image,
        proposals: list[FrameProposal],
        *,
        ocr: list[tuple[str, BBox]] | None = None,
    ) -> tuple[TrackHit, ...]:
        diag = FusionDiagnostics()
        kept = filter_proposals(proposals, diag, junk_seen=self._junk_seen)
        merged = merge_proposals(kept, diag)
        raw: list[tuple] = []
        meta_by_geometry: dict[tuple[BBox, str], _Fused] = {}
        for fus in merged:
            app = crop_appearance(image, fus.bbox)
            raw.append((fus.bbox, fus.label, fus.confidence, app, None, fus.role))
            # TrackMemory can return continuing tracks before newly created
            # tracks, so its hit order is not necessarily the proposal order.
            # Merged same-role proposals have unique geometry (overlapping
            # duplicates are clustered), making current bbox + role the
            # correspondence key without changing TrackMemory's contract.
            meta_by_geometry[(fus.bbox, fus.role)] = fus

        hits = self.memory.reconcile(raw)
        texts = bind_ocr_to_hits(hits, list(ocr or []), diag)

        out: list[TrackHit] = []
        diag.live_sources = {}
        for hit in hits:
            fus = meta_by_geometry[(hit.bbox, hit.role)]
            ocr_label = " ".join(t.strip() for t in texts.get(hit.track_id, []) if t.strip())
            label = ocr_label or hit.detector_label
            if hit.track_id in self.memory.live:
                bbox, app, _old = self.memory.live[hit.track_id]
                role = self.memory._live_role.get(hit.track_id, fus.role)
                self.memory.live[hit.track_id] = (bbox, app, label)
                self.memory._live_role[hit.track_id] = role
            sources = [str(e["source"]) for e in fus.evidence]
            diag.live_sources[hit.track_id] = sources
            out.append(
                TrackHit(
                    hit.track_id,
                    hit.bbox,
                    hit.appearance,
                    label,
                    hit.confidence,
                    hit.state,
                    role=fus.role,
                    source=",".join(sources),
                    class_hint=fus.class_hint,
                )
            )
        # Per-frame counters: carrying the previous frame's rejections forward
        # made a clean frame report stale rejections.
        self.diagnostics = diag
        return tuple(out)
