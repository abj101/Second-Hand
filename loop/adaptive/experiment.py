"""Quarantined experiment controller. Candidates are not legal actions."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Sequence
from uuid import uuid4

from loop.adaptive.orchestrate import (
    PROMPT_VERSION,
    SchemaDiff,
    _has_coordinates,
    apply_action_revision,
    filter_model_diffs,
    selector_from_evidence,
)
from loop.adaptive.runtime import resolve_selector
from loop.adaptive.schema import (
    AdaptiveSchema,
    EvidenceRecord,
    SceneGraph,
    StateSnapshot,
)
from loop.adaptive.session import Session

STATES = (
    "observe",
    "propose",
    "admit",
    "arm",
    "trial_1",
    "rollback_1",
    "trial_2",
    "rollback_2",
    "promote",
    "postcheck",
)
ALLOWED_PRIMITIVE = "click"
JOURNAL_NAME = "experiment.jsonl"
CACHE_DIRNAME = "proposal_cache"
TRACK_STABILITY_MIN = 0.95
FIELD_CONTINUITY_MIN = 0.95
SCHEMA_SCOUT_VERSION = f"{PROMPT_VERSION}:schema"
EXPERIMENT_SCOUT_VERSION = f"{PROMPT_VERSION}:experiment"

ScoutFn = Callable[[dict], dict]
ClickFn = Callable[[str], None]


@dataclass(frozen=True)
class ExperimentPlan:
    plan_id: str
    evidence_ids: tuple[str, ...]
    selector: dict
    primitive: str
    predicted_effects: tuple[dict, ...]
    rollback: dict
    target_identity: dict
    schema_revision_id: str
    field_id: str = ""
    op: str = "increased"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExperimentPlan":
        return cls(
            plan_id=str(data["plan_id"]),
            evidence_ids=tuple(data.get("evidence_ids") or ()),
            selector=dict(data.get("selector") or {}),
            primitive=str(data.get("primitive") or ALLOWED_PRIMITIVE),
            predicted_effects=tuple(data.get("predicted_effects") or ()),
            rollback=dict(data.get("rollback") or {}),
            target_identity=dict(data.get("target_identity") or {}),
            schema_revision_id=str(data.get("schema_revision_id") or ""),
            field_id=str(data.get("field_id") or ""),
            op=str(data.get("op") or "increased"),
        )


@dataclass(frozen=True)
class ProbeTrial:
    trial_id: str
    plan_id: str
    index: int
    before_hash: str
    after_hash: str
    binding: dict
    observed_deltas: dict
    rollback_ok: bool
    capture_provenance: dict
    model_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ProbeTrial":
        return cls(
            trial_id=str(data["trial_id"]),
            plan_id=str(data["plan_id"]),
            index=int(data["index"]),
            before_hash=str(data.get("before_hash") or ""),
            after_hash=str(data.get("after_hash") or ""),
            binding=dict(data.get("binding") or {}),
            observed_deltas=dict(data.get("observed_deltas") or {}),
            rollback_ok=bool(data.get("rollback_ok")),
            capture_provenance=dict(data.get("capture_provenance") or {}),
            model_meta=dict(data.get("model_meta") or {}),
        )


def stability_ok(metrics: dict, *, min_frames: int) -> bool:
    if int(metrics.get("frames") or 0) < min_frames:
        return False
    if float(metrics.get("track_stability") or 0) < TRACK_STABILITY_MIN:
        return False
    if float(metrics.get("field_continuity") or 0) < FIELD_CONTINUITY_MIN:
        return False
    return True


def observation_fingerprint(snap: StateSnapshot, graph: SceneGraph) -> str:
    body: dict[str, Any] = {"mode": snap.mode_id, "fields": snap.fields}
    if not snap.fields:
        body["nums"] = {
            str(n.attributes.get("label") or n.node_id): n.attributes.get("numeric")
            for n in graph.nodes
            if n.attributes.get("numeric") is not None
        }
    blob = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return sha256(blob.encode()).hexdigest()


def _read_field(snap: StateSnapshot, graph: SceneGraph | None, field_id: str) -> object:
    if field_id in snap.fields:
        return snap.fields[field_id]
    if graph is None:
        return None
    want = field_id.casefold()
    for node in graph.nodes:
        label = str(node.attributes.get("label") or "")
        if label and label.casefold() == want and "numeric" in node.attributes:
            return node.attributes["numeric"]
        if field_id in node.attributes:
            return node.attributes[field_id]
    return None


def typed_delta(
    before: StateSnapshot,
    after: StateSnapshot,
    field_id: str,
    *,
    before_graph: SceneGraph | None = None,
    after_graph: SceneGraph | None = None,
) -> dict | None:
    b = _read_field(before, before_graph, field_id)
    a = _read_field(after, after_graph, field_id)
    if not isinstance(b, (int, float)) or not isinstance(a, (int, float)):
        return None
    if a == b:
        return {"field_id": field_id, "op": "noop", "delta": 0}
    op = "increased" if a > b else "decreased"
    return {"field_id": field_id, "op": op, "delta": a - b}


def _cache_key(digest: str, revision: str, prompt_version: str, role: str) -> str:
    blob = f"{digest}|{revision}|{prompt_version}|{role}"
    return sha256(blob.encode()).hexdigest()


def _read_cache(dir: Path, key: str) -> dict | None:
    path = dir / f"{key}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(dir: Path, key: str, payload: dict) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    dest = dir / f"{key}.json"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(dest)


SCHEMA_JSON = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "diffs": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "suggested_id": {"type": "string"},
                    "candidate_ids": {"type": "array", "items": {"type": "string"}},
                    "payload": {"type": "object"},
                },
                "required": ["kind", "suggested_id", "candidate_ids", "payload"],
            },
        }
    },
    "required": ["diffs"],
}

EXPERIMENT_JSON = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "abstain": {"type": "boolean"},
        "candidate": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "primitive": {"type": "string"},
                "candidate_ids": {"type": "array", "items": {"type": "string"}},
                "predicted_effects": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "field_id": {"type": "string"},
                            "op": {"type": "string"},
                        },
                        "required": ["field_id", "op"],
                    },
                },
                "rollback": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "primitive": {"type": "string"},
                        "candidate_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["primitive"],
                },
            },
            "required": ["primitive", "candidate_ids", "predicted_effects", "rollback"],
        },
    },
    "required": ["abstain", "candidate"],
}


def run_scouts(
    bundle: dict,
    *,
    cache_dir: Path,
    schema_scout: ScoutFn | None = None,
    experiment_scout: ScoutFn | None = None,
) -> tuple[dict, dict]:
    def _run(role: str, fn: ScoutFn | None, version: str) -> dict:
        if fn is None:
            return {}
        key = _cache_key(
            str(bundle.get("digest") or ""),
            str(bundle.get("schema_revision_id") or ""),
            version,
            role,
        )
        hit = _read_cache(cache_dir, key)
        if hit is not None:
            return hit
        parsed = fn(bundle)
        if not isinstance(parsed, dict):
            parsed = {}
        _write_cache(cache_dir, key, parsed)
        return parsed

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_schema = pool.submit(_run, "schema", schema_scout, SCHEMA_SCOUT_VERSION)
        f_exp = pool.submit(_run, "experiment", experiment_scout, EXPERIMENT_SCOUT_VERSION)
        return f_schema.result(), f_exp.result()


def _diffs_from_scout(parsed: dict, valid_ids: set[str]) -> tuple[SchemaDiff, ...]:
    raw: list[SchemaDiff] = []
    for item in parsed.get("diffs") or ():
        if not isinstance(item, dict):
            continue
        ids = tuple(str(x) for x in (item.get("candidate_ids") or ()))
        raw.append(
            SchemaDiff(
                str(item.get("kind") or ""),
                str(item.get("suggested_id") or ""),
                ids,
                dict(item.get("payload") or {}),
            )
        )
    return filter_model_diffs(raw, valid_ids)


def admit_plan(
    *,
    schema_parsed: dict,
    experiment_parsed: dict,
    evidence: Sequence[EvidenceRecord],
    schema: AdaptiveSchema,
    scenes: Sequence[SceneGraph],
    target_identity: dict,
) -> ExperimentPlan | None:
    valid = {r.evidence_id for r in evidence}
    by_id = {r.evidence_id: r for r in evidence}
    diffs = _diffs_from_scout(schema_parsed, valid)
    if experiment_parsed.get("abstain") or not experiment_parsed.get("candidate"):
        return None
    cand = experiment_parsed["candidate"]
    if not isinstance(cand, dict):
        return None
    primitive = str(cand.get("primitive") or "")
    if primitive != ALLOWED_PRIMITIVE:
        return None
    ids = tuple(str(x) for x in (cand.get("candidate_ids") or ()))
    if not ids or any(i not in valid for i in ids):
        return None
    if _has_coordinates(cand):
        return None
    effects = tuple(dict(x) for x in (cand.get("predicted_effects") or ()) if isinstance(x, dict))
    if len(effects) != 1:
        return None
    field_id = str(effects[0].get("field_id") or "")
    op = str(effects[0].get("op") or "")
    if not field_id or op not in {"increased", "decreased", "changed"}:
        return None
    schema_fields = {d.suggested_id for d in diffs if d.kind == "field"} | {
        f.field_id for f in schema.fields
    }
    if field_id not in schema_fields:
        return None
    rollback = dict(cand.get("rollback") or {})
    if str(rollback.get("primitive") or "") != ALLOWED_PRIMITIVE:
        return None
    rb_ids = tuple(str(x) for x in (rollback.get("candidate_ids") or ids))
    if any(i not in valid for i in rb_ids):
        return None
    selector = selector_from_evidence(by_id, ids)
    if not selector:
        return None
    if len(scenes) < 3:
        return None
    bound: set[tuple[str, ...]] = set()
    for scene in scenes[-3:]:
        hits = resolve_selector(scene, selector)
        if len(hits) != 1:
            return None
        bound.add(hits)
    if len(bound) != 1:
        return None
    return ExperimentPlan(
        plan_id=str(uuid4()),
        evidence_ids=ids,
        selector=selector,
        primitive=ALLOWED_PRIMITIVE,
        predicted_effects=effects,
        rollback={"primitive": ALLOWED_PRIMITIVE, "candidate_ids": list(rb_ids)},
        target_identity=dict(target_identity),
        schema_revision_id=schema.revision.revision_id,
        field_id=field_id,
        op=op,
    )


def preflight(
    *,
    graph: SceneGraph,
    selector: dict,
    provenance: dict | None,
    identity: dict | None,
    target: object | None,
    live: bool,
) -> str:
    if live:
        if provenance is None or provenance.get("integrity") != "trusted":
            raise RuntimeError("untrusted capture")
        hwnd = getattr(target, "hwnd", None)
        if identity is not None and hwnd is not None and int(hwnd) != int(identity.get("hwnd") or 0):
            raise RuntimeError("hwnd identity mismatch")
        if target is not None:
            from loop.os.window import is_foreground

            if not is_foreground(target):  # type: ignore[arg-type]
                raise RuntimeError("target not foreground")
    hits = resolve_selector(graph, selector)
    if len(hits) != 1:
        raise RuntimeError("selector is not unique")
    return hits[0]


class ExperimentJournal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.records: list[dict] = []
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self.records.append(json.loads(line))

    def append(self, kind: str, payload: dict) -> None:
        rec = {"kind": kind, **payload}
        self.records.append(rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
            f.flush()

    def last_state(self) -> str:
        for rec in reversed(self.records):
            if rec.get("kind") == "state":
                return str(rec.get("state") or "observe")
        return "observe"

    def last_plan(self) -> ExperimentPlan | None:
        for rec in reversed(self.records):
            if rec.get("kind") == "plan":
                return ExperimentPlan.from_dict(rec)
        return None

    def trials(self) -> list[ProbeTrial]:
        return [ProbeTrial.from_dict(r) for r in self.records if r.get("kind") == "trial"]


class ExperimentController:
    def __init__(self, session: Session, *, min_frames: int = 30) -> None:
        self.session = session
        self.min_frames = min_frames
        self.journal = ExperimentJournal(session.workspace / JOURNAL_NAME)
        self.state = self.journal.last_state()
        self.plan = self.journal.last_plan()
        self.trials = self.journal.trials()

    def set_state(self, state: str) -> None:
        if state not in STATES:
            raise ValueError(state)
        self.state = state
        self.journal.append("state", {"state": state})

    def accept_plan(self, plan: ExperimentPlan | None) -> None:
        self.plan = plan
        if plan is None:
            self.set_state("observe")
            return
        self.journal.append("plan", plan.to_dict())
        self.set_state("admit")

    def record_trial(self, trial: ProbeTrial) -> None:
        self.trials.append(trial)
        self.journal.append("trial", trial.to_dict())

    def promotion_ready(self) -> bool:
        if self.plan is None or len(self.trials) < 2:
            return False
        a, b = self.trials[-2], self.trials[-1]
        if not a.rollback_ok or not b.rollback_ok:
            return False
        if a.observed_deltas.get("op") == "noop" or b.observed_deltas.get("op") == "noop":
            return False
        return a.observed_deltas == b.observed_deltas

    def promote(self) -> AdaptiveSchema:
        if not self.promotion_ready() or self.plan is None:
            raise ValueError("promotion predicate failed")
        plan = self.plan
        field_payload = {"field_id": plan.field_id, "value_type": "number"}
        if "label" in plan.selector:
            field_payload["label"] = plan.selector["label"]
        action_payload = {
            "action_id": f"act_{plan.field_id}_{plan.op}",
            "field_id": plan.field_id,
            "op": plan.op,
            "clickable_region": "target",
            "selector": {k: v for k, v in plan.selector.items() if k != "bbox"} or plan.selector,
        }
        schema = apply_action_revision(
            self.session.schema,
            field_payload=field_payload,
            action_payload=action_payload,
            store_dir=self.session.schema_dir,
        )
        self.session.accept_schema(schema)
        self.set_state("promote")
        return schema


def make_gateway_scouts(_cache_dir: Path) -> tuple[ScoutFn, ScoutFn] | None:
    from loop.model.gateway import ProposalGateway
    from loop.os.capture import encode_image

    gw = ProposalGateway.from_env()
    if gw is None:
        return None

    def _schema(bundle: dict, image=None) -> dict:
        call = gw.complete_json(
            system=(
                "Cite evidence_ids only. Never output coordinates or raw click points. "
                "Propose field, entity, mode, or selector diffs grounded in the bundle."
            ),
            user=json.dumps(bundle, sort_keys=True, default=str),
            json_schema=SCHEMA_JSON,
            schema_name="schema_scout",
            frames=[encode_image(image)] if image is not None else None,
        )
        return call.parsed or {}

    def _experiment(bundle: dict, image=None) -> dict:
        call = gw.complete_json(
            system=(
                "Propose at most one low-risk reversible left-click. Cite evidence_ids only. "
                "Never output coordinates. Include a rollback click. Abstain if unsure."
            ),
            user=json.dumps(bundle, sort_keys=True, default=str),
            json_schema=EXPERIMENT_JSON,
            schema_name="experiment_scout",
            frames=[encode_image(image)] if image is not None else None,
        )
        return call.parsed or {}

    return _schema, _experiment


def execute_trial(
    *,
    plan: ExperimentPlan,
    index: int,
    graph: SceneGraph,
    before: StateSnapshot,
    click: ClickFn,
    observe_after: Callable[[], tuple[SceneGraph, StateSnapshot]],
    rollback: ClickFn,
    provenance: dict,
    live: bool,
    target: object | None,
    identity: dict | None,
) -> ProbeTrial:
    node_id = preflight(
        graph=graph,
        selector=plan.selector,
        provenance=provenance,
        identity=identity,
        target=target,
        live=live,
    )
    before_hash = observation_fingerprint(before, graph)
    click(node_id)
    graph_after, after = observe_after()
    delta = typed_delta(
        before, after, plan.field_id, before_graph=graph, after_graph=graph_after
    )
    if delta is None or delta.get("op") != plan.op:
        raise RuntimeError("predicted effect missing")
    rb_node = preflight(
        graph=graph_after,
        selector=plan.selector,
        provenance=provenance,
        identity=identity,
        target=target,
        live=live,
    )
    rollback(rb_node)
    graph_restored, restored = observe_after()
    rollback_ok = observation_fingerprint(restored, graph_restored) == before_hash
    after_hash = observation_fingerprint(after, graph_after)
    return ProbeTrial(
        trial_id=str(uuid4()),
        plan_id=plan.plan_id,
        index=index,
        before_hash=before_hash,
        after_hash=after_hash,
        binding={"target": node_id},
        observed_deltas=delta,
        rollback_ok=rollback_ok,
        capture_provenance=dict(provenance),
        model_meta={"prompt_version": PROMPT_VERSION},
    )
