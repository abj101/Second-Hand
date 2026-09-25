"""Adaptive schema orchestration: hypothesize → validate → promote."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Callable, Iterable
from typing import Mapping, Sequence

from loop.adaptive.evidence import iou
from loop.adaptive.schema import (
    ActionSpec,
    AdaptiveSchema,
    EffectPredicate,
    EntityTypeSpec,
    EvidenceRecord,
    FieldSpec,
    ModeSpec,
    ParamSpec,
    PrimitiveStep,
    PromotionPolicy,
    SceneGraph,
    SensorRecipe,
    atomic_write_revision,
    hash_payload,
    load_revision,
    schema_payload,
    with_revision,
)

PROMPT_VERSION = "composer-2.5-v1"
_COORD_KEYS = frozenset({"x", "y", "bbox", "xy", "screen_xy", "px", "py", "nx", "ny"})

# Conservative native constraints stamped on every promoted click action (Wave 2).
PROMOTED_NATIVE_CONSTRAINTS: dict[str, object] = {
    "risk": "low",
    "retry": "deny",
    "cooldown_s": 0.5,
    "max_plan_duration_s": 1.0,
}

_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_OCR_LABEL_LINE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)[:\s]+(-?\d+(?:\.\d+)?)$",
)
_TRACK_REGION_RE = re.compile(r"^track_(\d+)$")
_EPHEMERAL_ACTION_ID = re.compile(r"track_\d+")
_SELECTOR_OCR_IOU = 0.05
_SELECTOR_COLOR_IOU = 0.3

# revision_id → seed file (read-only); promote never writes here.
_SEED_SOURCES: dict[str, Path] = {}


@dataclass(frozen=True)
class SchemaDiff:
    kind: str
    suggested_id: str
    candidate_ids: tuple[str, ...]
    payload: dict


@dataclass(frozen=True)
class Proposal:
    kind: str  # field | entity_type | mode | action | effect | relation
    suggested_id: str
    payload: dict


@dataclass(frozen=True)
class Trace:
    before_fields: dict
    after_fields: dict
    before_evidence: tuple[EvidenceRecord, ...] = ()
    after_evidence: tuple[EvidenceRecord, ...] = ()
    action_id: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Validation:
    ok: bool
    reasons: tuple[str, ...] = ()


def load_seed(path: Path) -> AdaptiveSchema:
    schema = load_revision(path)
    _SEED_SOURCES[schema.revision.revision_id] = path.resolve()
    return schema


def _has_coordinates(obj: object) -> bool:
    if isinstance(obj, dict):
        if _COORD_KEYS & set(obj):
            return True
        return any(_has_coordinates(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return any(_has_coordinates(v) for v in obj)
    return False


def filter_model_diffs(
    diffs: Iterable[SchemaDiff],
    valid_ids: set[str],
) -> tuple[SchemaDiff, ...]:
    """Reject fabricated evidence IDs, empty citations, and model-supplied coordinates."""

    out: list[SchemaDiff] = []
    for diff in diffs:
        if not diff.candidate_ids:
            continue
        if any(cid not in valid_ids for cid in diff.candidate_ids):
            continue
        if _has_coordinates(diff.payload):
            continue
        out.append(diff)
    return tuple(out)


def make_evidence_bundle(
    *,
    image_hash: str,
    scene: SceneGraph,
    schema: AdaptiveSchema,
    evidence: Sequence[EvidenceRecord],
    stability: dict,
    game_brief: Mapping[str, object] | None = None,
    game_brief_provenance: Mapping[str, str] | None = None,
) -> dict:
    nodes = [
        {
            "node_id": n.node_id,
            "evidence_ids": list(n.evidence_ids),
            "attributes": {
                k: n.attributes[k]
                for k in ("label", "numeric", "text", "clickable", "mean_rgb")
                if k in n.attributes
            },
        }
        for n in scene.nodes
    ]
    payload = {
        "prompt_version": PROMPT_VERSION,
        "image_hash": image_hash,
        "schema_revision_id": schema.revision.revision_id,
        "stability": dict(stability),
        "evidence_ids": [r.evidence_id for r in evidence],
        "scene": {"snapshot_id": scene.snapshot_id, "nodes": nodes},
        "schema_head": {
            "fields": [f.field_id for f in schema.fields],
            "actions": [a.action_id for a in schema.actions],
            "modes": [m.mode_id for m in schema.modes],
        },
    }
    if game_brief is not None:
        payload["game_brief"] = dict(game_brief)
        if game_brief_provenance is not None:
            payload["game_brief_provenance"] = dict(game_brief_provenance)
    payload["digest"] = hash_payload(payload)
    return payload


def selector_from_evidence(
    evidence_by_id: dict[str, EvidenceRecord],
    candidate_ids: Sequence[str],
) -> dict:
    selector: dict = {}
    for eid in candidate_ids:
        rec = evidence_by_id.get(str(eid))
        if rec is None:
            continue
        p = rec.payload
        if rec.kind == "ocr":
            text = str(p.get("text", ""))
            stem = text.split(":", 1)[0].strip()
            if stem and p.get("number") is None:
                selector.setdefault("label", stem)
        if rec.kind == "color" and "mean_rgb" in p:
            selector.setdefault("mean_rgb", p["mean_rgb"])
        if rec.kind == "track" and p.get("label"):
            selector.setdefault("label", p["label"])
        bb = _bbox_from_payload(p)
        if bb is not None and "bbox" not in selector:
            selector["bbox"] = [round(float(x), 4) for x in bb]
    if "label" in selector:
        selector.pop("bbox", None)
    return selector


def apply_action_revision(
    schema: AdaptiveSchema,
    *,
    field_payload: dict | None = None,
    action_payload: dict,
    store_dir: Path | None = None,
) -> AdaptiveSchema:
    updated = schema
    if field_payload:
        updated = _merge_field(updated, field_payload)
    updated = _merge_action(updated, action_payload)
    if _semantic_schema(updated) == _semantic_schema(schema):
        return schema
    new_schema = with_revision(updated, parent_id=schema.revision.revision_id)
    if store_dir is not None:
        atomic_write_revision(store_dir, new_schema)
    return new_schema


def _slug_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", text.strip()).lower()


def _spatial_field_id(bbox: tuple[float, float, float, float]) -> str:
    """Stable, ontology-free id for an unlabeled value at a screen anchor."""

    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    return f"field_{round(cx * 1000):03d}_{round(cy * 1000):03d}"


def _stable_action_id(raw: str | None, field_id: str, op: str) -> str:
    if raw is None or _EPHEMERAL_ACTION_ID.search(str(raw)):
        return f"act_{_slug_id(field_id)}_{op}"
    return str(raw)


def _bbox_from_payload(payload: dict) -> tuple[float, float, float, float] | None:
    raw = payload.get("bbox")
    if raw is None:
        return None
    try:
        return tuple(float(x) for x in raw)
    except (TypeError, ValueError):
        return None


def _click_track_bbox(
    evidence: Sequence[EvidenceRecord],
    region: object,
) -> tuple[float, float, float, float] | None:
    m = _TRACK_REGION_RE.match(str(region))
    if not m:
        return None
    want = int(m.group(1))
    for rec in evidence:
        if rec.kind != "track":
            continue
        if rec.payload.get("track_id") == want:
            return _bbox_from_payload(rec.payload)
    return None


def _selector_from_evidence(
    evidence: Sequence[EvidenceRecord],
    region: object,
) -> dict:
    track_bbox = _click_track_bbox(evidence, region)
    if track_bbox is None:
        return {"clickable_region": str(region)} if region is not None else {}

    selector: dict = {}
    best_ocr: EvidenceRecord | None = None
    best_ocr_score = 0.0
    best_color: EvidenceRecord | None = None
    best_color_score = 0.0
    for rec in evidence:
        bb = _bbox_from_payload(rec.payload)
        if bb is None:
            continue
        score = iou(track_bbox, bb)
        if rec.kind == "ocr" and score >= _SELECTOR_OCR_IOU and score > best_ocr_score:
            best_ocr_score = score
            best_ocr = rec
        elif rec.kind == "color" and score >= _SELECTOR_COLOR_IOU and score > best_color_score:
            best_color_score = score
            best_color = rec

    if best_ocr is not None:
        text = str(best_ocr.payload.get("text", ""))
        label = text.split(":", 1)[0].strip()
        if label:
            selector["label"] = label
    if best_color is not None:
        cp = best_color.payload
        if "mean_rgb" in cp:
            selector["mean_rgb"] = cp["mean_rgb"]
        if "aspect_ratio" in cp:
            selector["aspect_ratio"] = cp["aspect_ratio"]
    selector["bbox"] = [round(float(x), 4) for x in track_bbox]
    return selector


def _action_candidate_ids(
    evidence: Sequence[EvidenceRecord],
    region: object,
) -> list[str]:
    ids: list[str] = [str(region)]
    track_bbox = _click_track_bbox(evidence, region)
    if track_bbox is None:
        return ids
    for rec in evidence:
        bb = _bbox_from_payload(rec.payload)
        if bb is None:
            continue
        score = iou(track_bbox, bb)
        if rec.kind == "ocr" and score >= _SELECTOR_OCR_IOU:
            ids.append(rec.evidence_id)
        elif rec.kind == "color" and score >= _SELECTOR_COLOR_IOU:
            ids.append(rec.evidence_id)
    seen: set[str] = set()
    out: list[str] = []
    for x in ids:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def enumerate_candidates(
    evidence: Sequence[EvidenceRecord],
) -> dict[str, EvidenceRecord]:
    out: dict[str, EvidenceRecord] = {}
    for rec in evidence:
        out[rec.evidence_id] = rec
        if rec.kind == "track":
            tid = rec.payload.get("track_id")
            if tid is not None:
                out[f"track_{tid}"] = rec
    return out


def orchestrate(
    evidence: Sequence[EvidenceRecord],
    schema: AdaptiveSchema,
    *,
    proposer: Callable[
        [dict[str, EvidenceRecord], AdaptiveSchema],
        Iterable[SchemaDiff],
    ]
    | None = None,
    from_model: bool = False,
) -> tuple[Proposal, ...]:
    if proposer is None:
        return hypothesize(evidence, schema)
    candidates = enumerate_candidates(evidence)
    valid = set(candidates)
    raw = tuple(proposer(candidates, schema))
    diffs = filter_model_diffs(raw, valid) if from_model else raw
    proposals: list[Proposal] = []
    for diff in diffs:
        if not diff.candidate_ids:
            continue
        if any(cid not in valid for cid in diff.candidate_ids):
            continue
        payload = {**diff.payload, "candidate_ids": list(diff.candidate_ids)}
        proposals.append(Proposal(diff.kind, diff.suggested_id, payload))
    return tuple(proposals)


def _semantic_schema(schema: AdaptiveSchema) -> dict:
    payload = schema_payload(schema)
    payload.pop("revision", None)
    return payload


def _trace_action_matches(trace_action_id: str | None, proposal_action_id: str) -> bool:
    if trace_action_id is None:
        return True
    if trace_action_id == proposal_action_id:
        return True
    if _EPHEMERAL_ACTION_ID.search(trace_action_id):
        return True
    return False


def hypothesize(
    evidence: Sequence[EvidenceRecord],
    schema: AdaptiveSchema,
) -> tuple[Proposal, ...]:
    out: list[Proposal] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, sid: str, payload: dict) -> None:
        key = (kind, sid)
        if key in seen:
            return
        seen.add(key)
        out.append(Proposal(kind, sid, payload))

    for rec in evidence:
        p = rec.payload
        kind = rec.kind

        if kind == "ocr":
            text = str(p.get("text", ""))
            label: str | None = None
            if p.get("number") is not None:
                m = _OCR_LABEL_LINE.match(text)
                label = m.group(1) if m else (text if _ID_RE.match(text) else None)
            else:
                m = _OCR_LABEL_LINE.match(text)
                if m:
                    label = m.group(1)
            bbox = _bbox_from_payload(p)
            if label or (p.get("number") is not None and bbox is not None):
                cluster_id = p.get("cluster_id")
                if label:
                    fid = _slug_id(label)
                elif cluster_id is not None:
                    fid = f"field_c{int(cluster_id):03d}"
                else:
                    fid = _spatial_field_id(bbox)
                if label is None and bbox is not None and cluster_id is None:
                    for existing in schema.fields:
                        existing_bbox = _bbox_from_payload(existing.selector)
                        if existing_bbox is not None and iou(bbox, existing_bbox) >= 0.3:
                            fid = existing.field_id
                            break
                field_payload: dict = {
                    "field_id": fid,
                    "value_type": "number",
                    "candidate_ids": [rec.evidence_id],
                }
                if label:
                    field_payload["label"] = label
                if bbox is not None:
                    field_payload["bbox"] = [round(float(x), 4) for x in bbox]
                add("field", fid, field_payload)

        if kind == "interaction_delta":
            field_id = str(p.get("field_id", ""))
            region = p.get("clickable_region")
            if field_id and region is not None:
                op = str(p.get("op", "increased"))
                raw_aid = p.get("action_id")
                aid = _stable_action_id(
                    str(raw_aid) if raw_aid is not None else None,
                    field_id,
                    op,
                )
                selector = _selector_from_evidence(evidence, region)
                add(
                    "action",
                    aid,
                    {
                        "action_id": aid,
                        "clickable_region": region,
                        "field_id": field_id,
                        "delta": p.get("delta"),
                        "op": op,
                        "selector": selector,
                        "candidate_ids": _action_candidate_ids(evidence, region),
                    },
                )
                eid = f"effect_{field_id}_changed"
                add(
                    "effect",
                    eid,
                    {
                        "field_id": field_id,
                        "op": p.get("op", "increased"),
                        "action_id": aid,
                    },
                )

    track_recs = [r for r in evidence if r.kind == "track"]
    if len(track_recs) >= 2:
        add(
            "entity_type",
            "type_unit",
            {
                "type_id": "type_unit",
                "field_ids": (),
                "classifier": {"has_label": True, "has_numeric": False},
            },
        )
        add(
            "mode",
            "mode_default",
            {"mode_id": "mode_default", "activation": {"min_fields": 1}},
        )

    return tuple(out)


@dataclass(frozen=True)
class ProvisionalOffer:
    offer_id: str
    label: str
    node_id: str


MAX_OFFER_AREA_FRACTION = 0.25


@dataclass(frozen=True)
class OfferDecision:
    candidate_id: str
    node_id: str
    label: str
    status: str  # eligible | withheld | rejected
    reason: str


_OFFER_STATE_FORBIDDEN_KEYS = frozenset(
    {"bbox", "box", "x", "y", "coordinates", "point", "primitive"}
)


def registry_label_set(graph: SceneGraph) -> frozenset[str]:
    labels: set[str] = set()
    for node in graph.nodes:
        if not node.attributes.get("registry_id"):
            continue
        label = str(node.attributes.get("label") or "").strip()
        if label:
            labels.add(label.casefold())
    return frozenset(labels)


def _bbox_area_fraction(bbox: tuple[float, ...]) -> float | None:
    if len(bbox) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    if x0 < 0.0 or y0 < 0.0 or x1 > 1.0 or y1 > 1.0:
        return None
    w, h = x1 - x0, y1 - y0
    if w <= 0.0 or h <= 0.0:
        return None
    return w * h


def _semantic_eligibility(attrs: dict) -> tuple[str, str] | None:
    """Return (status, reason) when semantic confirmation is required."""

    status = str(attrs.get("semantic_status") or "").strip().lower()
    klass = str(attrs.get("semantic_class") or "").strip().lower()
    if status == "confirmed" and klass == "control":
        return None
    if status == "confirmed" and klass == "decoration":
        return ("withheld", "semantic_decoration")
    if status == "confirmed" and klass == "unknown":
        return ("withheld", "semantic_unknown")
    if status in ("", "missing"):
        return ("withheld", "semantic_missing")
    if status == "unresolved":
        return ("withheld", "semantic_unresolved")
    if status == "unavailable":
        return ("withheld", "semantic_unavailable")
    if status == "exhausted":
        return ("withheld", "semantic_exhausted")
    return ("withheld", "semantic_unconfirmed")


def offer_decisions(
    graph: SceneGraph,
    forbidden: frozenset[str] | set[str] | None = None,
    *,
    max_area_fraction: float = MAX_OFFER_AREA_FRACTION,
    semantic: str = "off",
) -> tuple[OfferDecision, ...]:
    blocked = forbidden or frozenset()
    semantic_mode = str(semantic or "off").strip().lower()
    candidates: list[tuple[object, str, str, str]] = []
    id_counts: dict[str, int] = {}
    for node in graph.nodes:
        rid = node.attributes.get("registry_id")
        if not rid:
            continue
        label = str(node.attributes.get("label") or "").strip()
        if not label:
            continue
        offer_id = str(rid)
        candidates.append((node, offer_id, label, node.node_id))
        id_counts[offer_id] = id_counts.get(offer_id, 0) + 1

    decisions: list[OfferDecision] = []
    for node, offer_id, label, node_id in candidates:
        attrs = node.attributes
        owner = attrs.get("registry_owner")
        if owner is not None and str(owner) not in {"ocr", "track"}:
            decisions.append(
                OfferDecision(offer_id, node_id, label, "rejected", "registry_owner_not_ocr")
            )
            continue
        if str(attrs.get("track_role") or "") == "world":
            decisions.append(
                OfferDecision(offer_id, node_id, label, "rejected", "world_target_not_control")
            )
            continue
        source = str(attrs.get("track_source") or "")
        if "gpa-gui" in source and "card-split" not in source:
            if label.casefold() in {"icon", "image", "object"}:
                decisions.append(
                    OfferDecision(offer_id, node_id, label, "withheld", "unclassified_gui_icon")
                )
                continue
            if attrs.get("numeric") is not None:
                decisions.append(
                    OfferDecision(offer_id, node_id, label, "withheld", "gui_value_not_control")
                )
                continue
            if re.search(r"\bv\d+(?:\.\d+)+", label, re.IGNORECASE):
                decisions.append(
                    OfferDecision(offer_id, node_id, label, "withheld", "gui_version_not_control")
                )
                continue
        area = _bbox_area_fraction(tuple(node.bbox))
        if area is None:
            decisions.append(OfferDecision(offer_id, node_id, label, "rejected", "bbox_invalid"))
            continue
        if area > max_area_fraction:
            decisions.append(OfferDecision(offer_id, node_id, label, "rejected", "oversized"))
            continue
        if id_counts.get(offer_id, 0) > 1:
            decisions.append(
                OfferDecision(offer_id, node_id, label, "rejected", "ambiguous_registry_id")
            )
            continue
        if offer_id in blocked:
            decisions.append(OfferDecision(offer_id, node_id, label, "withheld", "forbidden"))
            continue
        if semantic_mode == "required":
            withheld = _semantic_eligibility(attrs)
            if withheld is not None:
                status, reason = withheld
                decisions.append(OfferDecision(offer_id, node_id, label, status, reason))
                continue
        decisions.append(OfferDecision(offer_id, node_id, label, "eligible", "eligible"))

    decisions.sort(key=lambda item: (item.candidate_id, item.node_id))
    return tuple(decisions)


def registry_offers(
    graph: SceneGraph,
    forbidden: frozenset[str] | set[str] | None = None,
    *,
    max_area_fraction: float = MAX_OFFER_AREA_FRACTION,
    semantic: str = "off",
) -> tuple[ProvisionalOffer, ...]:
    offers: list[ProvisionalOffer] = []
    for decision in offer_decisions(
        graph,
        forbidden,
        max_area_fraction=max_area_fraction,
        semantic=semantic,
    ):
        if decision.status != "eligible":
            continue
        offers.append(
            ProvisionalOffer(decision.candidate_id, decision.label, decision.node_id)
        )
    offers.sort(key=lambda item: item.offer_id)
    return tuple(offers)


def offer_model_state(
    offers: Sequence[ProvisionalOffer],
    snapshot: object,
) -> dict[str, object]:
    fields = getattr(snapshot, "fields", None) or {}
    state: dict[str, object] = {
        "offer_ids": [item.offer_id for item in offers],
        "offer_labels": [item.label for item in offers],
        "fields": dict(fields),
    }
    _assert_no_offer_coordinate_keys(state)
    return state


def _assert_no_offer_coordinate_keys(obj: object) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key) in _OFFER_STATE_FORBIDDEN_KEYS:
                raise ValueError(f"offer state must not expose {key!r}")
            _assert_no_offer_coordinate_keys(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _assert_no_offer_coordinate_keys(value)


def forbid_unrestored_registry_id(
    registry_id: str,
    *,
    window_gone: bool,
    labels_returned: bool,
    forbidden: set[str],
    ledger: object,
) -> bool:
    if labels_returned and not window_gone:
        return False
    forbidden.add(registry_id)
    append = getattr(ledger, "append_event", None)
    if append is not None:
        append(
            "negative_evidence",
            {
                "registry_id": registry_id,
                "window_gone": window_gone,
                "labels_returned": labels_returned,
            },
        )
    return True


def _trace_sequence(world_or_trace: object) -> tuple[Trace, ...] | None:
    if isinstance(world_or_trace, (str, bytes)):
        return None
    if not isinstance(world_or_trace, Sequence):
        return None
    traces: list[Trace] = []
    for item in world_or_trace:
        if not isinstance(item, Trace):
            return None
        traces.append(item)
    return tuple(traces)


def probe(proposal: Proposal, world_or_trace: object) -> ProbeResult:
    traces = _trace_sequence(world_or_trace)
    if traces is None:
        return ProbeResult(ok=False, detail="no traces")

    if proposal.kind == "field":
        if not traces:
            return ProbeResult(ok=False, detail="empty traces")
        return ProbeResult(ok=True)

    if proposal.kind in ("action", "effect"):
        if not traces:
            return ProbeResult(ok=False, detail="empty traces")
        if all(_trace_noop(t) for t in traces):
            return ProbeResult(ok=False, detail="noop traces")
        return ProbeResult(ok=True)

    if proposal.kind in ("entity_type", "mode", "relation"):
        if not traces:
            return ProbeResult(ok=False, detail="empty traces")
        return ProbeResult(ok=True)

    return ProbeResult(ok=False, detail=f"unknown proposal kind {proposal.kind}")


def _trace_noop(trace: Trace) -> bool:
    return trace.before_fields == trace.after_fields


def _numeric_delta(before: dict, after: dict, field_id: str) -> float | None:
    if field_id not in before or field_id not in after:
        return None
    b, a = before[field_id], after[field_id]
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return float(a) - float(b)
    return None


def _replay_ok(trace: Trace, field_id: str | None) -> bool:
    if trace.before_fields == trace.after_fields:
        return False
    if field_id and _numeric_delta(trace.before_fields, trace.after_fields, field_id) is None:
        if trace.before_fields.get(field_id) == trace.after_fields.get(field_id):
            return False
    return True


def _ocr_matches_label(ev: EvidenceRecord, label: str) -> bool:
    if ev.kind != "ocr":
        return False
    text = str(ev.payload.get("text", ""))
    lab = str(label)
    return text == lab or text.startswith(f"{lab}:") or text.startswith(f"{lab} ")


def _ocr_matches_bbox(ev: EvidenceRecord, bbox: object) -> bool:
    if ev.kind != "ocr" or ev.payload.get("number") is None:
        return False
    actual = _bbox_from_payload(ev.payload)
    if actual is None:
        return False
    try:
        wanted = tuple(float(x) for x in bbox)
    except (TypeError, ValueError):
        return False
    return iou(actual, wanted) >= 0.5


def validate(
    proposal: Proposal,
    traces: Sequence[Trace],
    schema: AdaptiveSchema,
    policy: PromotionPolicy,
) -> Validation:
    del schema
    reasons: list[str] = []

    if proposal.kind == "field":
        label = proposal.payload.get("label")
        bbox = proposal.payload.get("bbox")
        reads = sum(
            1
            for t in traces
            for ev in (*t.before_evidence, *t.after_evidence)
            if (
                _ocr_matches_label(ev, str(label))
                if label is not None
                else _ocr_matches_bbox(ev, bbox)
            )
        )
        if reads < policy.min_agreeing_reads:
            reasons.append(f"field reads {reads} < {policy.min_agreeing_reads}")
        return Validation(ok=not reasons, reasons=tuple(reasons))

    if proposal.kind in ("action", "effect"):
        field_id = str(proposal.payload.get("field_id", ""))
        action_id = proposal.payload.get("action_id")
        want_op = proposal.payload.get("op", "increased")

        correlated = 0
        agreeing = 0
        deltas: list[float] = []

        for trace in traces:
            if _trace_noop(trace):
                continue
            if action_id is not None and not _trace_action_matches(
                trace.action_id,
                str(action_id),
            ):
                continue
            if _entity_only_disappearance(trace, field_id):
                continue

            delta = _numeric_delta(trace.before_fields, trace.after_fields, field_id)
            if delta is None:
                continue
            if delta == 0:
                continue

            deltas.append(delta)
            if want_op == "increased" and delta > 0:
                correlated += 1
            elif want_op == "decreased" and delta < 0:
                correlated += 1
            elif want_op == "changed":
                correlated += 1

            if policy.require_replay and not _replay_ok(trace, field_id):
                reasons.append("replay failed")
            agreeing += 1

        if any(d > 0 for d in deltas) and any(d < 0 for d in deltas):
            reasons.append("contradictory field deltas")

        noop_only = len(traces) > 0 and all(_trace_noop(t) for t in traces)
        if noop_only:
            reasons.append("no-op traces cannot validate action/effect")

        if agreeing < policy.min_agreeing_reads:
            reasons.append(
                f"agreeing reads {agreeing} < {policy.min_agreeing_reads}",
            )
        if correlated < policy.min_correlated_deltas:
            reasons.append(
                f"correlated deltas {correlated} < {policy.min_correlated_deltas}",
            )

        return Validation(ok=not reasons, reasons=tuple(reasons))

    if proposal.kind in ("entity_type", "mode", "relation"):
        if not traces:
            return Validation(ok=False, reasons=("empty traces",))
        return Validation(ok=True, reasons=())

    reasons.append(f"unknown proposal kind {proposal.kind}")
    return Validation(ok=False, reasons=tuple(reasons))


def _entity_only_disappearance(trace: Trace, field_id: str) -> bool:
    """True when entities vanish but the target field did not change."""
    before_keys = set(trace.before_fields)
    after_keys = set(trace.after_fields)
    if not before_keys - after_keys:
        return False
    if field_id in before_keys and field_id in after_keys:
        if trace.before_fields[field_id] != trace.after_fields[field_id]:
            return False
    return field_id not in after_keys or trace.before_fields.get(field_id) == trace.after_fields.get(
        field_id,
    )


def _merge_field(schema: AdaptiveSchema, payload: dict) -> AdaptiveSchema:
    fid = str(payload.get("field_id", ""))
    if any(f.field_id == fid for f in schema.fields):
        return schema
    selector: dict = {}
    if payload.get("label"):
        selector["label"] = payload["label"]
    if payload.get("bbox") is not None:
        selector["bbox"] = payload["bbox"]
    spec = FieldSpec(
        fid,
        str(payload.get("value_type", "number")),
        selector=selector,
    )
    return replace(schema, fields=schema.fields + (spec,))


def _merge_entity_type(schema: AdaptiveSchema, payload: dict) -> AdaptiveSchema:
    tid = str(payload["type_id"])
    if any(e.type_id == tid for e in schema.entity_types):
        return schema
    fids = tuple(payload.get("field_ids", ()))
    classifier = dict(payload.get("classifier") or ())
    return replace(
        schema,
        entity_types=schema.entity_types + (EntityTypeSpec(tid, fids, classifier),),
    )


def _merge_mode(schema: AdaptiveSchema, payload: dict) -> AdaptiveSchema:
    mid = str(payload["mode_id"])
    if any(m.mode_id == mid for m in schema.modes):
        return schema
    activation = dict(payload.get("activation") or ())
    return replace(schema, modes=schema.modes + (ModeSpec(mid, activation=activation),))


def _validate_native_constraints_dict(constraints: dict) -> None:
    from loop.adaptive import contracts as native_contracts
    from loop.adaptive.candidates import native_constraints_from_spec

    trusted = native_constraints_from_spec(
        ActionSpec(
            action_id="_constraint_probe",
            parameters=(),
            preconditions=(),
            primitive_plan=(PrimitiveStep("click", ("target",)),),
            expected_effects=(),
            constraints=constraints,
        )
    )
    native_contracts._validate_constraints(trusted)


def _resolve_promoted_native_constraints(payload: dict) -> dict:
    merged = dict(PROMOTED_NATIVE_CONSTRAINTS)
    extra = payload.get("constraints")
    if extra:
        if not isinstance(extra, dict):
            raise ValueError("constraints must be a mapping")
        merged.update(extra)
    _validate_native_constraints_dict(merged)
    return merged


def _merge_action(schema: AdaptiveSchema, payload: dict) -> AdaptiveSchema:
    field_id = str(payload.get("field_id", ""))
    op = str(payload.get("op", "increased"))
    raw_aid = payload.get("action_id")
    aid = _stable_action_id(
        str(raw_aid) if raw_aid is not None else None,
        field_id,
        op,
    )
    if any(a.action_id == aid for a in schema.actions):
        return schema
    region = payload.get("clickable_region")
    selector = dict(payload.get("selector") or {})
    has_target = region is not None and str(region) != ""
    effects: tuple[EffectPredicate, ...] = ()
    if field_id:
        effects = (EffectPredicate(field_id, op),)
    constraints: dict = {}
    if has_target:
        constraints = _resolve_promoted_native_constraints(payload)
    spec = ActionSpec(
        action_id=aid,
        parameters=(ParamSpec("target", "entity"),) if has_target else (),
        preconditions=(),
        primitive_plan=((PrimitiveStep("click", ("target",)),) if has_target else ()),
        expected_effects=effects,
        selector=selector,
        constraints=constraints,
    )
    return replace(schema, actions=schema.actions + (spec,))


def _merge_effect(schema: AdaptiveSchema, payload: dict) -> AdaptiveSchema:
    raw_aid = payload.get("action_id")
    field_id = str(payload.get("field_id", ""))
    op = str(payload.get("op", "changed"))
    if not raw_aid or not field_id:
        return schema
    aid = _stable_action_id(str(raw_aid), field_id, op)
    actions = list(schema.actions)
    for i, act in enumerate(actions):
        if act.action_id != aid:
            continue
        pred = EffectPredicate(field_id, op)
        if pred in act.expected_effects:
            return schema
        actions[i] = replace(
            act,
            expected_effects=act.expected_effects + (pred,),
        )
        return replace(schema, actions=tuple(actions))
    return _merge_action(
        schema,
        {
            "action_id": aid,
            "field_id": field_id,
            "op": op,
            "clickable_region": payload.get("region", ""),
            "selector": payload.get("selector") or {},
        },
    )


def promote(
    schema: AdaptiveSchema,
    proposal: Proposal,
    traces: Sequence[Trace],
    policy: PromotionPolicy,
    *,
    store_dir: Path | None = None,
    probe_target: object | None = None,
) -> AdaptiveSchema:
    validation = validate(proposal, traces, schema, policy)
    if not validation.ok:
        raise ValueError("; ".join(validation.reasons) or "validate failed")
    probe_result = probe(proposal, probe_target if probe_target is not None else traces)
    if not probe_result.ok:
        raise ValueError(probe_result.detail or "probe failed")

    updated = schema
    if proposal.kind == "field":
        updated = _merge_field(schema, proposal.payload)
    elif proposal.kind == "entity_type":
        updated = _merge_entity_type(schema, proposal.payload)
    elif proposal.kind == "mode":
        updated = _merge_mode(schema, proposal.payload)
    elif proposal.kind == "action":
        updated = _merge_action(schema, proposal.payload)
    elif proposal.kind == "effect":
        updated = _merge_effect(schema, proposal.payload)
    elif proposal.kind == "relation":
        updated = schema
    else:
        updated = schema

    if _semantic_schema(updated) == _semantic_schema(schema):
        return schema

    parent = schema.revision.revision_id
    new_schema = with_revision(updated, parent_id=parent)

    if store_dir is not None:
        seed_path = _SEED_SOURCES.get(schema.revision.revision_id)
        if seed_path is not None and seed_path.parent.resolve() == store_dir.resolve():
            raise ValueError("store_dir must not be the seed directory")
        atomic_write_revision(store_dir, new_schema)

    return new_schema


def _detector_kind_for_field(value_type: str) -> str:
    if value_type == "number":
        return "ocr_int_pair"
    if value_type == "text":
        return "ocr_text"
    return "pixel_color_frac"


def _validate_compiled_native_actions(schema: AdaptiveSchema) -> None:
    from loop.adaptive.schema import validate_primitive_step

    for action in schema.actions:
        if not action.primitive_plan:
            continue
        for step in action.primitive_plan:
            validate_primitive_step(step)
        _validate_native_constraints_dict(dict(action.constraints))


def compile_schema(schema: AdaptiveSchema) -> AdaptiveSchema:
    _validate_compiled_native_actions(schema)
    by_id = {s.sensor_id: s for s in schema.sensors}
    new_fields: list[FieldSpec] = []
    new_sensors: list[SensorRecipe] = list(schema.sensors)
    changed = False
    for field in schema.fields:
        sid = field.sensor_id or f"sensor_{field.field_id}"
        if not field.sensor_id:
            field = replace(field, sensor_id=sid)
            changed = True
        new_fields.append(field)
        if sid in by_id:
            continue
        kind = "ocr" if field.value_type in ("number", "text") else "region"
        raw_bbox = field.selector.get("bbox") if field.selector else None
        roi = tuple(raw_bbox) if raw_bbox else None
        recipe = SensorRecipe(
            sid,
            kind,
            config={
                "field_id": field.field_id,
                "detector_kind": _detector_kind_for_field(field.value_type),
            },
            roi=roi,
        )
        by_id[sid] = recipe
        new_sensors.append(recipe)
        changed = True
    if not changed:
        return schema
    merged = replace(schema, fields=tuple(new_fields), sensors=tuple(new_sensors))
    return with_revision(merged, parent_id=schema.revision.revision_id)


def clear_seed_registry_for_tests() -> None:
    """Test-only: reset seed path tracking."""
    _SEED_SOURCES.clear()
