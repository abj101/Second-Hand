"""Game-agnostic neutral perception → evidence records."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass, replace

from PIL import Image, ImageChops

from loop.adaptive import evidence as ev
from loop.adaptive.schema import EvidenceLedger, EvidenceRecord, SceneGraph, SceneNode

OcrFn = Callable[[Image.Image], Iterable[tuple[str, tuple[float, float, float, float]]]]
VlmFn = Callable[[Image.Image], str]

_OCR_IOU = 0.05
_COLOR_IOU = 0.3
_REGISTRY_LABEL = re.compile(r"^[A-Za-z]+(?: [A-Za-z]+)*$")
_APPEARANCE_TOL = 55.0


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
        raw = str(best_ocr.payload.get("text", "")).strip()
        stem = _ocr_stem(best_ocr.payload)
        if stem:
            feat["label"] = stem
        if raw:
            feat["text"] = raw
    if best_color is not None:
        rgb = best_color.payload.get("mean_rgb")
        if rgb is not None:
            feat["mean_rgb"] = rgb
    return feat


def _region_hash(image: Image.Image, bbox: tuple[float, float, float, float]) -> str:
    return hashlib.sha256(ev._crop_norm(image, bbox).tobytes()).hexdigest()


def _hard_screen_change(
    previous: Image.Image | None,
    current: Image.Image,
    previous_regions: list[tuple[float, float, float, float]],
    current_regions: list[tuple[float, float, float, float]],
) -> bool:
    """Identify a replacement frame before any frame-local cache is reused."""

    if previous is None or previous.size != current.size:
        return previous is not None
    if previous.tobytes() == current.tobytes():
        return False
    sample_size = (64, 64)
    before = previous.convert("RGB").resize(sample_size)
    after = current.convert("RGB").resize(sample_size)
    changed = ImageChops.difference(before, after).convert("L").histogram()
    if sum(changed[24:]) / (sample_size[0] * sample_size[1]) >= 0.55:
        return True
    # If no detected region survives spatially, the old cache cannot safely
    # own any token on this frame even when the replacement has a quiet
    # background.
    if previous_regions and current_regions:
        if max(
            (max((ev.iou(a, b) for b in current_regions), default=0.0) for a in previous_regions),
            default=0.0,
        ) < 0.05:
            return True
    return bool(previous_regions and not current_regions)


def _ocr_token_key(rec: EvidenceRecord) -> tuple[str, object]:
    """Return the content identity used to coalesce overlapping OCR reads."""

    text = " ".join(str(rec.payload.get("text", "")).split()).casefold()
    number = rec.payload.get("number")
    return text, number


def _dedupe_ocr_records(records: Iterable[EvidenceRecord]) -> list[EvidenceRecord]:
    """Keep one evidence record per visible token.

    A region detector may return overlapping regions, and the crop OCR path can
    consequently report the same token more than once.  The first record owns
    that token; later records are only duplicates when their content and box
    overlap.  Identical labels in separate locations remain distinct tokens.
    """

    unique: list[EvidenceRecord] = []
    for rec in records:
        if rec.kind != "ocr":
            continue
        try:
            bbox = _norm_bbox(rec.payload)
        except (KeyError, TypeError, ValueError):
            unique.append(rec)
            continue
        key = _ocr_token_key(rec)
        duplicate = False
        for prior in unique:
            if prior.kind != "ocr" or _ocr_token_key(prior) != key:
                continue
            try:
                prior_bbox = _norm_bbox(prior.payload)
            except (KeyError, TypeError, ValueError):
                continue
            if ev.iou(bbox, prior_bbox) >= 0.5:
                duplicate = True
                break
        if not duplicate:
            unique.append(rec)
    return unique


def _sync_region_cache(
    region_cache: dict[int, tuple[str, tuple[EvidenceRecord, ...], dict, dict]],
    image: Image.Image,
    matched: list[tuple[int, tuple[float, float, float, float]]],
    ocr_recs: list[EvidenceRecord],
    color_recs: list[EvidenceRecord],
    curr_features: list[dict],
) -> None:
    # Assign each token to exactly one tracked region.  Caching a token under
    # every overlapping region is what caused duplicate OCR scene nodes after
    # a cache hit.
    owned: dict[int, list[EvidenceRecord]] = {int(track_id): [] for track_id, _ in matched}
    for rec in _dedupe_ocr_records(ocr_recs):
        try:
            token_box = _norm_bbox(rec.payload)
        except (KeyError, TypeError, ValueError):
            continue
        candidates = [
            (ev.iou(token_box, bbox), int(track_id))
            for track_id, bbox in matched
            if ev.iou(token_box, bbox) >= _OCR_IOU
        ]
        if candidates:
            _score, owner = max(candidates, key=lambda item: item[0])
            owned[owner].append(rec)
    for i, (track_id, bbox) in enumerate(matched):
        h = _region_hash(image, bbox)
        ocr_part = tuple(owned.get(int(track_id), ()))
        color_payload = dict(color_recs[i].payload) if i < len(color_recs) else {}
        feat = dict(curr_features[i]) if i < len(curr_features) else {}
        region_cache[int(track_id)] = (h, ocr_part, color_payload, feat)


def _track_payload(
    track_id: int,
    bbox: tuple[float, float, float, float],
    feat: dict,
) -> dict:
    payload: dict = {"track_id": int(track_id), "bbox": [float(x) for x in bbox]}
    if feat.get("label"):
        payload["label"] = feat["label"]
    if feat.get("text"):
        payload["text"] = feat["text"]
    if feat.get("mean_rgb") is not None:
        payload["mean_rgb"] = feat["mean_rgb"]
    return payload


def _registry_phrase(payload: dict) -> str:
    raw = str(payload.get("text") or "").strip()
    if raw:
        if _registry_label_key(raw):
            return " ".join(raw.split())
        return ""
    label = str(payload.get("label") or "").strip()
    return " ".join(label.split()) if _registry_label_key(label) else ""


def _registry_label_key(label: str) -> str | None:
    text = " ".join(label.split())
    if len(text) < 2 or len(text) > 24 or not _REGISTRY_LABEL.fullmatch(text):
        return None
    return text.casefold()


def _mean_rgb(mean_rgb: object) -> tuple[float, float, float] | None:
    if not isinstance(mean_rgb, (list, tuple)) or len(mean_rgb) < 3:
        return None
    try:
        return tuple(float(mean_rgb[i]) for i in range(3))
    except (TypeError, ValueError):
        return None


def _appearance_distance(a: object, b: object) -> float:
    left, right = _mean_rgb(a), _mean_rgb(b)
    if left is None or right is None:
        return float("inf")
    return sum((x - y) ** 2 for x, y in zip(left, right)) ** 0.5


def _ocr_bbox_mean_rgb(
    image: Image.Image, bbox: tuple[float, float, float, float]
) -> tuple[float, float, float] | None:
    return _mean_rgb(ev.color_features(image, bbox).get("mean_rgb"))


def _registry_geometry_match(
    entry_bbox: tuple[float, float, float, float],
    bbox: tuple[float, float, float, float],
) -> bool:
    geo = ev.iou(entry_bbox, bbox)
    near = _centroid_dist(entry_bbox, bbox)
    return not (geo < 0.1 and near > 0.08)


def _registry_entry_matches(
    entry: dict,
    bbox: tuple[float, float, float, float],
    rgb: tuple[float, float, float],
) -> bool:
    if _appearance_distance(entry.get("rgb"), rgb) > _APPEARANCE_TOL:
        return False
    eb = entry.get("bbox")
    if eb is None:
        return True
    return _registry_geometry_match(tuple(float(x) for x in eb), bbox)


def perceive(
    image: Image.Image,
    *,
    previous: Image.Image | None = None,
    prev_tracks: list[tuple[int, tuple[float, float, float, float]]] | None = None,
    prev_features: list[dict] | None = None,
    ocr: OcrFn | Iterable[tuple[str, tuple[float, float, float, float]]] | None = None,
    vlm: VlmFn | None = None,
    ledger: EvidenceLedger | None = None,
    region_cache: dict[int, tuple[str, tuple[EvidenceRecord, ...], dict, dict]] | None = None,
    held_ocr: list[EvidenceRecord] | None = None,
) -> tuple[EvidenceRecord, ...]:
    if not isinstance(image, Image.Image):
        raise TypeError("image must be PIL.Image.Image")
    records: list[EvidenceRecord] = []

    regions = ev.detect_regions(image)
    ocr_is_callable = ocr is not None and callable(ocr)
    use_gate = region_cache is not None and ocr_is_callable and previous is not None

    ocr_recs: list[EvidenceRecord] = []
    color_recs: list[EvidenceRecord] = []
    curr_features: list[dict]

    if use_gate:
        assert ocr is not None and callable(ocr)
        if prev_tracks is not None:
            prev_regions = [b for _, b in prev_tracks]
            prev_ids = [i for i, _ in prev_tracks]
        else:
            prev_regions = ev.detect_regions(previous)
            prev_ids = list(range(len(prev_regions)))
        pre_match = ev.assign_track_ids(
            prev_regions,
            regions,
            prev_ids=prev_ids,
            # Features only align with stored tracks, not freshly detected
            # previous-frame regions.
            prev_features=prev_features if prev_tracks is not None else None,
            curr_features=[{}] * len(regions),
        )
        curr_features = []
        for i, bbox in enumerate(regions):
            track_id = pre_match[i][0]
            h = _region_hash(image, bbox)
            cached = region_cache.get(track_id)
            if cached is not None and cached[0] == h:
                ocr_recs.extend(cached[1])
                color_payload = dict(cached[2])
                color_payload["bbox"] = [float(x) for x in bbox]
                color_rec = ev.evidence_record("color", color_payload)
                color_recs.append(color_rec)
                curr_features.append(dict(cached[3]))
            else:
                new_ocr = ev.run_ocr_crop(image, bbox, ocr)
                # ponytail: an empty crop read is not a re-read; keep the previous tokens.
                # Upgrade path: split the region when a logo and a label share one box.
                if not new_ocr and cached is not None:
                    current_color = ev.color_features(image, bbox)
                    # The region hash already changed, so an empty crop read
                    # is not evidence that its old token remains visible.
                    # Retaining it here is precisely the cross-screen carryover
                    # that the frame cache contract forbids.
                    color_rec = ev.evidence_record("color", current_color)
                    color_recs.append(color_rec)
                    curr_features.append(_region_features(bbox, (), [color_rec]))
                else:
                    ocr_recs.extend(new_ocr)
                    color_payload = ev.color_features(image, bbox)
                    color_rec = ev.evidence_record("color", color_payload)
                    color_recs.append(color_rec)
                    curr_features.append(_region_features(bbox, new_ocr, [color_rec]))
            records.append(ev.evidence_record("region", {"bbox": [float(x) for x in bbox]}))
            records.append(color_recs[-1])
        # Cached OCR is frame-local to its owning region.  Never merge a
        # separate held-token list here: that made labels from a replaced
        # screen survive until an unrelated region happened to change.
        ocr_recs = _dedupe_ocr_records(ocr_recs)
        records = list(ocr_recs) + records
    else:
        ocr_recs = _dedupe_ocr_records(ev.run_ocr(image, ocr))
        records.extend(ocr_recs)
        for bbox in regions:
            records.append(ev.evidence_record("region", {"bbox": [float(x) for x in bbox]}))
            records.append(ev.evidence_record("color", ev.color_features(image, bbox)))
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

    if region_cache is not None and ocr_is_callable and regions:
        if previous is not None and prev_tracks is not None:
            prev_regions = [b for _, b in prev_tracks]
            prev_ids = [i for i, _ in prev_tracks]
            matched_for_cache = ev.assign_track_ids(
                prev_regions,
                regions,
                prev_ids=prev_ids,
                prev_features=prev_features,
                curr_features=curr_features,
            )
        elif previous is not None:
            prev_regions = ev.detect_regions(previous)
            matched_for_cache = ev.assign_track_ids(
                prev_regions, regions, curr_features=curr_features
            )
        else:
            matched_for_cache = [(i, b) for i, b in enumerate(regions)]
        _sync_region_cache(
            region_cache, image, matched_for_cache, ocr_recs, color_recs, curr_features
        )

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
        raw_id = tr.payload["track_id"]
        numeric_id = isinstance(raw_id, int) or (isinstance(raw_id, str) and str(raw_id).isdigit())
        track_id: int | str = int(raw_id) if numeric_id else str(raw_id)
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
        elif numeric_id and track_id in known_clickable:
            attrs["clickable"] = True

        reg_id = tr.payload.get("registry_id")
        if reg_id:
            attrs["registry_id"] = reg_id
            owner = tr.payload.get("registry_owner")
            if owner:
                attrs["registry_owner"] = owner
        if tr.payload.get("track_state"):
            attrs["track_state"] = tr.payload["track_state"]
        detector_label = str(tr.payload.get("detector_label") or "")
        if detector_label:
            attrs["detector_label"] = detector_label
        role = str(tr.payload.get("role") or "")
        if role:
            attrs["track_role"] = role
        source = str(tr.payload.get("source") or "")
        if source:
            attrs["track_source"] = source
        if "label" not in attrs:
            payload_label = str(tr.payload.get("label") or "").strip()
            if payload_label:
                attrs["label"] = payload_label

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
        reg_id = ocr_rec.payload.get("registry_id")
        if reg_id:
            attrs["registry_id"] = reg_id
            owner = ocr_rec.payload.get("registry_owner")
            if owner:
                attrs["registry_owner"] = owner
            reg_label = ocr_rec.payload.get("registry_label")
            if reg_label:
                attrs["registry_label"] = reg_label
            attrs["bbox"] = [float(x) for x in bbox]
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


def _apply_track_hits(
    records: tuple[EvidenceRecord, ...], hits: tuple
) -> tuple[EvidenceRecord, ...]:
    """Registry id is the track id. OCR text is a label, not a second id."""

    from loop.adaptive.tracker import bind_ocr_to_tracks

    positions: list[int] = []
    tokens: list[tuple[str, tuple[float, float, float, float]]] = []
    for index, rec in enumerate(records):
        if rec.kind != "ocr":
            continue
        try:
            box = _norm_bbox(rec.payload)
        except (KeyError, TypeError, ValueError):
            continue
        positions.append(index)
        tokens.append((str(rec.payload.get("text") or ""), box))
    binding = bind_ocr_to_tracks(hits, tokens)
    texts: dict[str, list[str]] = {}
    for token_index, track_id in binding.items():
        texts.setdefault(track_id, []).append(tokens[token_index][0])

    out: list[EvidenceRecord] = []
    for rec in records:
        if rec.kind == "track":
            continue
        if rec.kind == "ocr":
            payload = dict(rec.payload)
            for key in ("registry_id", "registry_owner", "registry_label"):
                payload.pop(key, None)
            out.append(replace(rec, payload=payload))
            continue
        out.append(rec)
    for hit in hits:
        label = next((text.strip() for text in texts.get(hit.track_id, []) if text.strip()), "")
        if not label:
            label = hit.detector_label
        out.append(
            ev.evidence_record(
                "track",
                {
                    "track_id": hit.track_id,
                    "bbox": [float(x) for x in hit.bbox],
                    "registry_id": hit.track_id,
                    "registry_owner": "track",
                    "label": label,
                    "detector_label": hit.detector_label,
                    "track_state": hit.state,
                    "confidence": hit.confidence,
                    "appearance": hit.appearance.hex(),
                    "role": getattr(hit, "role", "unknown"),
                    "source": getattr(hit, "source", ""),
                    "class_hint": getattr(hit, "class_hint", ""),
                },
            )
        )
    return tuple(out)


class SceneAssembler:
    """Stateful wrapper: perceive → assemble_scene with frame-to-frame tracks.

    Exact-frame reuse: when the caller supplies a fresh capture of the same
    target with the same dimensions and pixel bytes, the expensive OCR and
    tracker detector work of the previous update is reused.  The graph is still
    assembled fresh from those measurements every update, no ledger evidence is
    replayed, and the frame digest is only a measurement origin: it is never a
    screen generation, an acquisition sequence or a verification time.
    """

    def __init__(self, tracker=None) -> None:
        self._tracker = tracker
        self.tracker_status = "none" if tracker is None else str(getattr(tracker, "status", "unavailable"))
        self.screen_replaced = False
        self._previous: Image.Image | None = None
        self._tracks: list[tuple[int, tuple[float, float, float, float], dict]] = []
        self._clickable_ids: set[int] = set()
        self._ocr_clusters: list[_OcrCluster] = []
        self._next_cluster_id = 0
        self._region_cache: dict[int, tuple[str, tuple[EvidenceRecord, ...], dict, dict]] = {}
        # Durable ids are indexed by normalized label, then matched by a
        # tolerant appearance distance.  Two controls with the same label but
        # materially different appearance remain separate identities.
        self._registry: dict[str, list[dict]] = {}
        self._next_registry_id = 0
        # Exact-frame reuse state. Scoped to this assembler instance, which
        # callers create per (session, target); a changed frame, tracker status
        # or OCR/VLM provider replaces it instead of reusing stale work.
        self._frame_key: tuple[tuple[int, int], str, str, str] | None = None
        self._frame_ocr: object | None = None
        self._frame_vlm: object | None = None
        self._frame_records: tuple[EvidenceRecord, ...] | None = None
        self._frame_pixels: bytes | None = None
        # Per-update diagnostics: whether this update reused the previous
        # measurement, how often that happened, and which captured frame the
        # reused measurements came from. Verification time stays with callers.
        self.frame_reused = False
        self.cache_hits = 0
        self.measurement_digest: str | None = None

    def _commit_registry_entry(
        self,
        key_label: str,
        bbox: tuple[float, float, float, float],
        rgb: tuple[float, float, float],
        entry: dict | None,
    ) -> str:
        entries = self._registry.setdefault(key_label, [])
        if entry is None:
            entry = {"id": f"reg_{self._next_registry_id}", "bbox": bbox, "rgb": rgb}
            self._next_registry_id += 1
            entries.append(entry)
        else:
            entry["bbox"] = bbox
            old_rgb = entry.get("rgb", rgb)
            entry["rgb"] = tuple((old_rgb[i] * 3.0 + rgb[i]) / 4.0 for i in range(3))
        return str(entry["id"])

    def _assign_ocr_registry(
        self,
        candidates: list[tuple[int, str, str, tuple[float, float, float, float], tuple[float, float, float]]],
    ) -> dict[int, str | None]:
        """One-to-one OCR registry assignment; ambiguous duplicates stay unbound."""
        assignments: dict[int, str | None] = {idx: None for idx, *_ in candidates}
        withheld: set[int] = set()
        by_key: dict[str, list[tuple[int, str, str, tuple[float, float, float, float], tuple[float, float, float]]]] = {}
        for item in candidates:
            by_key.setdefault(item[1], []).append(item)

        for key_label, group in by_key.items():
            entries = self._registry.get(key_label, [])
            for entry in entries:
                matched = [
                    idx
                    for idx, _kl, _disp, bbox, rgb in group
                    if _registry_entry_matches(entry, bbox, rgb)
                ]
                if len(matched) >= 2:
                    withheld.update(matched)

        used_entry_ids: set[str] = set()
        ordered = sorted(
            candidates,
            key=lambda item: (
                item[1],
                ev._bbox_center(item[3])[0],
                ev._bbox_center(item[3])[1],
                item[0],
            ),
        )
        for idx, key_label, _disp, bbox, rgb in ordered:
            if idx in withheld:
                continue
            entries = self._registry.get(key_label, [])
            matching = [
                e
                for e in entries
                if e["id"] not in used_entry_ids and _registry_entry_matches(e, bbox, rgb)
            ]
            if not matching:
                rid = self._commit_registry_entry(key_label, bbox, rgb, None)
            else:
                entry = min(
                    matching,
                    key=lambda e: (_appearance_distance(e.get("rgb"), rgb), str(e["id"])),
                )
                rid = self._commit_registry_entry(key_label, bbox, rgb, entry)
            used_entry_ids.add(rid)
            assignments[idx] = rid
        return assignments

    def _apply_registry(
        self, records: tuple[EvidenceRecord, ...], image: Image.Image
    ) -> tuple[EvidenceRecord, ...]:
        ocr_candidates: list[
            tuple[int, str, str, tuple[float, float, float, float], tuple[float, float, float]]
        ] = []
        for i, rec in enumerate(records):
            if rec.kind != "ocr":
                continue
            phrase = _registry_phrase(rec.payload)
            if not phrase:
                continue
            key_label = _registry_label_key(phrase)
            if key_label is None:
                continue
            try:
                bbox = _norm_bbox(rec.payload)
            except (KeyError, TypeError, ValueError):
                continue
            rgb = _ocr_bbox_mean_rgb(image, bbox)
            if rgb is None:
                continue
            ocr_candidates.append((i, key_label, phrase, bbox, rgb))

        assigned = self._assign_ocr_registry(ocr_candidates)
        out: list[EvidenceRecord] = []
        for i, rec in enumerate(records):
            if rec.kind == "track":
                payload = dict(rec.payload)
                payload.pop("registry_id", None)
                out.append(replace(rec, payload=payload))
                continue
            if rec.kind == "ocr" and i in assigned and assigned[i]:
                payload = dict(rec.payload)
                key_label = _registry_label_key(_registry_phrase(payload) or "")
                payload["registry_id"] = assigned[i]
                payload["registry_owner"] = "ocr"
                if key_label is not None:
                    payload["registry_label"] = key_label
                out.append(replace(rec, payload=payload))
                continue
            if rec.kind == "ocr":
                payload = dict(rec.payload)
                for key in ("registry_id", "registry_owner", "registry_label"):
                    payload.pop(key, None)
                out.append(replace(rec, payload=payload))
                continue
            out.append(rec)
        return tuple(out)

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
        pixels = image.tobytes()
        digest = hashlib.sha256(pixels).hexdigest()
        snapshot_id = digest[:16]
        self.frame_reused = False
        # An explicit token list is caller-supplied evidence, so only a live OCR
        # callable (whose output is a function of the pixels) may be reused.
        cacheable_ocr = ocr is None or callable(ocr)
        current_regions = ev.detect_regions(image)
        previous_regions = ev.detect_regions(self._previous) if self._previous is not None else []
        hard_change = _hard_screen_change(
            self._previous,
            image,
            previous_regions,
            current_regions,
        )
        self.screen_replaced = bool(hard_change)
        if self._tracker is None:
            self.tracker_status = "none"
        else:
            self.tracker_status = str(getattr(self._tracker, "status", "unavailable"))
        use_tracker = self.tracker_status == "ok"
        frame_key = (image.size, image.mode, digest, self.tracker_status)
        if (
            cacheable_ocr
            and not hard_change
            and self._frame_records is not None
            and self._frame_key == frame_key
            and self._frame_pixels == pixels
            and self._frame_ocr is ocr
            and self._frame_vlm is vlm
        ):
            # Same pixels, same target, same providers: rebuild projectable
            # records and a fresh graph from the previous measurements, and
            # append nothing to the ledger, because a reused measurement is not
            # a second independent observation.
            records = tuple(
                replace(rec, payload=deepcopy(rec.payload)) for rec in self._frame_records
            )
            scene = assemble_scene(
                records, snapshot_id=snapshot_id, clickable_ids=self._clickable_ids
            )
            self.frame_reused = True
            self.cache_hits += 1
            self._previous = image.copy()
            return records, scene
        if hard_change:
            # Region OCR, track ids, token clusters, and clickable bindings are
            # frame-local.  Registry identities intentionally remain durable so
            # a returned label can reclaim its prior offer id.
            # Live tracks drop on a real replacement. Appearance memory may
            # still mark a later hit returned; it cannot invent a box.
            self._region_cache.clear()
            self._tracks.clear()
            self._ocr_clusters.clear()
            self._next_cluster_id = 0
            self._clickable_ids.clear()
            if use_tracker:
                self._tracker.clear_live()
        int_tracks = all(isinstance(i, int) for i, _b, _f in self._tracks)
        prev_tracks = (
            [(i, b) for i, b, _f in self._tracks] or None
            if not hard_change and not use_tracker and int_tracks
            else None
        )
        prev_features = (
            [f for _i, _b, f in self._tracks] or None
            if not hard_change and not use_tracker
            else None
        )
        records = perceive(
            image,
            previous=None if hard_change else self._previous,
            prev_tracks=prev_tracks,
            prev_features=prev_features,
            ocr=ocr,
            vlm=vlm,
            ledger=None,
            region_cache=(
                self._region_cache
                if ocr is not None
                and callable(ocr)
                and not getattr(self._tracker, "require_visual_ownership", False)
                else None
            ),
            held_ocr=None,
        )
        records = self._bind_ocr_clusters(records)
        if use_tracker:
            tokens: list[tuple[str, tuple[float, float, float, float]]] = []
            for rec in records:
                if rec.kind != "ocr":
                    continue
                try:
                    tokens.append((str(rec.payload.get("text") or ""), _norm_bbox(rec.payload)))
                except (KeyError, TypeError, ValueError):
                    continue
            try:
                hits = self._tracker.update(image, ocr=tokens)
            except TypeError:
                hits = self._tracker.update(image)
            records = _apply_track_hits(records, hits)
        elif self._tracker is not None and getattr(self._tracker, "require_visual_ownership", False):
            records = _apply_track_hits(records, ())
        else:
            records = self._apply_registry(records, image)
        if ledger is not None:
            for rec in records:
                ledger.append(rec)
        scene = assemble_scene(
            records,
            snapshot_id=snapshot_id,
            clickable_ids=self._clickable_ids,
        )
        stored = []
        for rec in records:
            if rec.kind != "track":
                continue
            raw = rec.payload["track_id"]
            try:
                key: int | str = int(raw)
            except (TypeError, ValueError):
                key = str(raw)
            stored.append(
                (
                    key,
                    _norm_bbox(rec.payload),
                    {
                        "label": rec.payload.get("label") or None,
                        "mean_rgb": rec.payload.get("mean_rgb"),
                    },
                )
            )
        self._tracks = stored
        self._previous = image.copy()
        # Cache a private copy, so nothing a caller or the ledger holds can
        # mutate the measurements a later exact-frame update reuses.
        self._frame_key = frame_key
        self._frame_ocr = ocr
        self._frame_vlm = vlm
        self._frame_records = tuple(
            replace(rec, payload=deepcopy(rec.payload)) for rec in records
        )
        self._frame_pixels = pixels
        self.measurement_digest = digest
        return records, scene
