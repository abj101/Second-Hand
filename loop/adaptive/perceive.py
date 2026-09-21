"""Game-agnostic neutral perception → evidence records."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from PIL import Image

from loop.adaptive import evidence as ev
from loop.adaptive.schema import EvidenceLedger, EvidenceRecord, SceneGraph, SceneNode

OcrFn = Callable[[Image.Image], Iterable[tuple[str, tuple[float, float, float, float]]]]
VlmFn = Callable[[Image.Image], str]

_OCR_IOU = 0.05
_COLOR_IOU = 0.3


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _norm_bbox(payload: dict) -> tuple[float, float, float, float]:
    raw = payload.get("bbox")
    if raw is None:
        raise KeyError("bbox")
    return tuple(float(x) for x in raw)


def _parse_numeric(text: str, payload: dict) -> int | float | None:
    if "number" in payload:
        num = payload["number"]
        if isinstance(num, (int, float)):
            return num
    standalone = ev.parse_numeric_text(text)
    if standalone is not None:
        return standalone[0]
    if ":" not in text:
        return None
    tail = text.split(":", 1)[1].strip()
    if not tail:
        return None
    try:
        if tail.isdigit() or (tail.startswith("-") and tail[1:].isdigit()):
            return int(tail)
        return float(tail)
    except ValueError:
        return None


def _ocr_stem(payload: dict) -> str:
    text = str(payload.get("text", "")).strip()
    if not text:
        return ""
    return text.split(":", 1)[0].strip()


def _region_features(
    bbox: tuple[float, float, float, float],
    ocr_recs: list[EvidenceRecord],
    color_recs: list[EvidenceRecord],
) -> dict:
    best_ocr: EvidenceRecord | None = None
    best_ocr_iou = 0.0
    for rec in ocr_recs:
        try:
            ob = _norm_bbox(rec.payload)
        except (KeyError, TypeError, ValueError):
            continue
        score = ev.iou(bbox, ob)
        if score >= _OCR_IOU and score > best_ocr_iou:
            best_ocr_iou = score
            best_ocr = rec
    best_color: EvidenceRecord | None = None
    best_color_iou = 0.0
    for rec in color_recs:
        try:
            cb = _norm_bbox(rec.payload)
        except (KeyError, TypeError, ValueError):
            continue
        score = ev.iou(bbox, cb)
        if score >= _COLOR_IOU and score > best_color_iou:
            best_color_iou = score
            best_color = rec
    feat: dict = {}
    if best_ocr is not None:
        stem = _ocr_stem(best_ocr.payload)
        if stem:
            feat["label"] = stem
    if best_color is not None:
        rgb = best_color.payload.get("mean_rgb")
        if rgb is not None:
            feat["mean_rgb"] = rgb
    return feat


def _track_payload(
    track_id: int,
    bbox: tuple[float, float, float, float],
    feat: dict,
) -> dict:
    payload: dict = {"track_id": int(track_id), "bbox": [float(x) for x in bbox]}
    if feat.get("label"):
        payload["label"] = feat["label"]
    if feat.get("mean_rgb") is not None:
        payload["mean_rgb"] = feat["mean_rgb"]
    return payload


def perceive(
    image: Image.Image,
    *,
    previous: Image.Image | None = None,
    prev_tracks: list[tuple[int, tuple[float, float, float, float]]] | None = None,
    prev_features: list[dict] | None = None,
    ocr: OcrFn | Iterable[tuple[str, tuple[float, float, float, float]]] | None = None,
    vlm: VlmFn | None = None,
    ledger: EvidenceLedger | None = None,
) -> tuple[EvidenceRecord, ...]:
    if not isinstance(image, Image.Image):
        raise TypeError("image must be PIL.Image.Image")
    records: list[EvidenceRecord] = []

    records.extend(ev.run_ocr(image, ocr))

    regions = ev.detect_regions(image)
    for bbox in regions:
        records.append(ev.evidence_record("region", {"bbox": [float(x) for x in bbox]}))
        records.append(ev.evidence_record("color", ev.color_features(image, bbox)))

    ocr_recs = [r for r in records if r.kind == "ocr"]
    color_recs = [r for r in records if r.kind == "color"]
    curr_features = [_region_features(bbox, ocr_recs, color_recs) for bbox in regions]

    if previous is not None:
        mb = ev.motion_bbox(previous, image)
        if mb is not None:
            records.append(ev.evidence_record("motion", {"bbox": [float(x) for x in mb]}))

        if prev_tracks is not None:
            prev_regions = [b for _, b in prev_tracks]
            prev_ids = [i for i, _ in prev_tracks]
            matched = ev.assign_track_ids(
                prev_regions,
                regions,
                prev_ids=prev_ids,
                prev_features=prev_features,
                curr_features=curr_features,
            )
        else:
            prev_regions = ev.detect_regions(previous)
            matched = ev.assign_track_ids(prev_regions, regions, curr_features=curr_features)
        feat_by_index = curr_features
        for i, (track_id, bbox) in enumerate(matched):
            feat = feat_by_index[i] if i < len(feat_by_index) else {}
            records.append(ev.evidence_record("track", _track_payload(track_id, bbox, feat)))
    else:
        for track_id, bbox in enumerate(regions):
            records.append(
                ev.evidence_record("track", _track_payload(track_id, bbox, curr_features[track_id]))
            )

    if vlm is not None:
        raw = vlm(image)
        if raw:
            records.append(ev.evidence_record("vlm_hypothesis", {"text": str(raw)}))

    if ledger is not None:
        for rec in records:
            ledger.append(rec)

    return tuple(records)


def assemble_scene(
    records: Iterable[EvidenceRecord],
    *,
    snapshot_id: str,
    clickable_ids: set[int] | None = None,
) -> SceneGraph:
    known_clickable = clickable_ids or set()
    ocr_recs = [r for r in records if r.kind == "ocr"]
    color_recs = [r for r in records if r.kind == "color"]
    track_recs = [r for r in records if r.kind == "track"]

    nodes: list[SceneNode] = []
    for tr in track_recs:
        track_id = int(tr.payload["track_id"])
        bbox = _norm_bbox(tr.payload)
        ev_ids: list[str] = [tr.evidence_id]

        best_ocr: EvidenceRecord | None = None
        best_ocr_iou = 0.0
        for ocr_rec in ocr_recs:
            ob = _norm_bbox(ocr_rec.payload)
            score = ev.iou(bbox, ob)
            if score >= _OCR_IOU and score > best_ocr_iou:
                best_ocr_iou = score
                best_ocr = ocr_rec
                ev_ids.append(ocr_rec.evidence_id)

        best_color: EvidenceRecord | None = None
        best_color_iou = 0.0
        for color_rec in color_recs:
            cb = _norm_bbox(color_rec.payload)
            score = ev.iou(bbox, cb)
            if score >= _COLOR_IOU and score > best_color_iou:
                best_color_iou = score
                best_color = color_rec
                ev_ids.append(color_rec.evidence_id)

        attrs: dict = {}
        if best_color is not None:
            cp = best_color.payload
            if "mean_rgb" in cp:
                attrs["mean_rgb"] = cp["mean_rgb"]
            if "aspect_ratio" in cp:
                attrs["aspect_ratio"] = cp["aspect_ratio"]

        numeric: int | float | None = None
        label_stem = ""
        if best_ocr is not None:
            text = str(best_ocr.payload.get("text", ""))
            label_stem = text.split(":", 1)[0].strip()
            numeric = _parse_numeric(text, best_ocr.payload)
            if label_stem and numeric is None:
                attrs["label"] = label_stem
            elif ":" in text and label_stem:
                attrs["label"] = label_stem

        if numeric is not None:
            attrs["numeric"] = numeric
            if label_stem:
                attrs[label_stem] = numeric
        elif track_id in known_clickable:
            attrs["clickable"] = True

        nodes.append(
            SceneNode(
                node_id=f"track_{track_id}",
                bbox=bbox,
                evidence_ids=tuple(dict.fromkeys(ev_ids)),
                attributes=attrs,
            )
        )

    # OCR tokens are first-class scene nodes.  Region segmentation is useful
    # for visual objects, but small text often sits inside a much larger
    # component (or no component at all).  Keeping the token node preserves
    # its exact spatial selector and prevents state from disappearing merely
    # because segmentation changed.
    for ocr_rec in ocr_recs:
        bbox = _norm_bbox(ocr_rec.payload)
        text = str(ocr_rec.payload.get("text", ""))
        numeric = _parse_numeric(text, ocr_rec.payload)
        attrs: dict = {"text": text}
        if numeric is not None:
            attrs["numeric"] = numeric
            if ocr_rec.payload.get("maximum") is not None:
                attrs["maximum"] = ocr_rec.payload["maximum"]
        else:
            label = text.split(":", 1)[0].strip()
            if label:
                attrs["label"] = label
        coords = "_".join(f"{round(v * 100):03d}" for v in bbox)
        cluster_id = ocr_rec.payload.get("cluster_id")
        node_id = f"ocr_c{int(cluster_id)}" if cluster_id is not None else f"ocr_{coords}"
        nodes.append(
            SceneNode(
                node_id=node_id,
                bbox=bbox,
                evidence_ids=(ocr_rec.evidence_id,),
                attributes=attrs,
            )
        )

    return SceneGraph(snapshot_id=snapshot_id, nodes=tuple(nodes), relations=())


def derive_interactions(
    before: SceneGraph,
    after: SceneGraph,
    clicked_node_id: str,
) -> tuple[EvidenceRecord, ...]:
    def _by_label(graph: SceneGraph) -> dict[str, SceneNode]:
        out: dict[str, SceneNode] = {}
        for node in graph.nodes:
            label = node.attributes.get("label")
            if label is not None:
                out[str(label)] = node
        return out

    before_by = _by_label(before)
    after_by = _by_label(after)
    records: list[EvidenceRecord] = []

    for label in before_by.keys() & after_by.keys():
        bnode, anode = before_by[label], after_by[label]
        bval = bnode.attributes.get("numeric")
        if bval is None:
            bval = bnode.attributes.get(label)
        aval = anode.attributes.get("numeric")
        if aval is None:
            aval = anode.attributes.get(label)
        if bval is None or aval is None or bval == aval:
            continue
        if not isinstance(bval, (int, float)) or not isinstance(aval, (int, float)):
            continue
        op = "increased" if aval > bval else "decreased"
        field_id = _slug(label)
        records.append(
            ev.evidence_record(
                "interaction_delta",
                {
                    "field_id": field_id,
                    "clickable_region": clicked_node_id,
                    "action_id": f"act_{_slug(clicked_node_id)}",
                    "op": op,
                    "delta": aval - bval,
                },
            )
        )
    return tuple(records)


@dataclass
class _OcrCluster:
    cluster_id: int
    bbox: tuple[float, float, float, float]
    identity: str
    n: int


def _ocr_identity(payload: dict) -> str:
    stem = _ocr_stem(payload)
    if payload.get("number") is not None:
        return f"label:{stem.casefold()}" if stem else "numeric"
    return f"text:{stem.casefold()}" if stem else "text"


def _centroid_dist(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    ac, bc = ev._bbox_center(a), ev._bbox_center(b)
    return ((ac[0] - bc[0]) ** 2 + (ac[1] - bc[1]) ** 2) ** 0.5


class SceneAssembler:
    """Stateful wrapper: perceive → assemble_scene with frame-to-frame tracks."""

    def __init__(self) -> None:
        self._previous: Image.Image | None = None
        self._tracks: list[tuple[int, tuple[float, float, float, float], dict]] = []
        self._clickable_ids: set[int] = set()
        self._ocr_clusters: list[_OcrCluster] = []
        self._next_cluster_id = 0

    def note_clickable(self, node_id: str) -> None:
        m = re.match(r"track_(\d+)", node_id)
        if m:
            self._clickable_ids.add(int(m.group(1)))

    def _bind_ocr_clusters(
        self, records: tuple[EvidenceRecord, ...]
    ) -> tuple[EvidenceRecord, ...]:
        out: list[EvidenceRecord] = []
        for rec in records:
            if rec.kind != "ocr":
                out.append(rec)
                continue
            try:
                bbox = _norm_bbox(rec.payload)
            except (KeyError, TypeError, ValueError):
                out.append(rec)
                continue
            identity = _ocr_identity(rec.payload)
            best_i, best_score = -1, -1.0
            for i, cluster in enumerate(self._ocr_clusters):
                if cluster.identity != identity:
                    continue
                geo = ev.iou(cluster.bbox, bbox)
                near = _centroid_dist(cluster.bbox, bbox)
                if geo < 0.1 and near > 0.08:
                    continue
                score = geo + max(0.0, 1.0 - near / 0.08)
                if score > best_score:
                    best_score = score
                    best_i = i
            if best_i < 0:
                cluster = _OcrCluster(self._next_cluster_id, bbox, identity, 1)
                self._ocr_clusters.append(cluster)
                self._next_cluster_id += 1
            else:
                cluster = self._ocr_clusters[best_i]
                n = cluster.n + 1
                mixed = tuple(
                    (cluster.bbox[k] * cluster.n + bbox[k]) / n for k in range(4)
                )
                cluster = _OcrCluster(cluster.cluster_id, mixed, identity, n)
                self._ocr_clusters[best_i] = cluster
            payload = dict(rec.payload)
            payload["cluster_id"] = cluster.cluster_id
            out.append(replace(rec, payload=payload))
        return tuple(out)

    def update(
        self,
        image: Image.Image,
        *,
        ocr: OcrFn | Iterable[tuple[str, tuple[float, float, float, float]]] | None = None,
        vlm: VlmFn | None = None,
        ledger: EvidenceLedger | None = None,
    ) -> tuple[tuple[EvidenceRecord, ...], SceneGraph]:
        if not isinstance(image, Image.Image):
            raise TypeError("image must be PIL.Image.Image")
        snapshot_id = hashlib.sha256(image.tobytes()).hexdigest()[:16]
        prev_tracks = [(i, b) for i, b, _f in self._tracks] or None
        prev_features = [f for _i, _b, f in self._tracks] or None
        records = perceive(
            image,
            previous=self._previous,
            prev_tracks=prev_tracks,
            prev_features=prev_features,
            ocr=ocr,
            vlm=vlm,
            ledger=None,
        )
        records = self._bind_ocr_clusters(records)
        if ledger is not None:
            for rec in records:
                ledger.append(rec)
        scene = assemble_scene(
            records,
            snapshot_id=snapshot_id,
            clickable_ids=self._clickable_ids,
        )
        self._tracks = [
            (
                int(r.payload["track_id"]),
                _norm_bbox(r.payload),
                {
                    "label": r.payload.get("label") or None,
                    "mean_rgb": r.payload.get("mean_rgb"),
                },
            )
            for r in records
            if r.kind == "track"
        ]
        self._previous = image.copy()
        return records, scene
