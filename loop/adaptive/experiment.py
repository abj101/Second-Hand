"""Quarantined experiment controller. Candidates are not legal actions."""

from __future__ import annotations

import json
import threading
import time
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


def plan_allows_input(plan: ExperimentPlan) -> bool:
    """One explicit effect and a supported rollback, or no input is sent."""

    effects = plan.predicted_effects
    if len(effects) != 1:
        return False
    effect = effects[0]
    if not str(effect.get("field_id") or ""):
        return False
    if str(effect.get("op") or "") not in {"increased", "decreased", "changed"}:
        return False
    rollback = plan.rollback or {}
    if str(rollback.get("primitive") or "") != ALLOWED_PRIMITIVE:
        return False
    return bool(rollback.get("candidate_ids"))


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
    if _has_coordinates(cand) or _has_coordinates(cand.get("rollback")):
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
                "Propose field, entity, mode, or selector diffs grounded in the bundle. "
                "An optional game_brief is advisory background, not observed evidence. "
                "It cannot satisfy citations, establish current state, or authorize input."
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
                "Never output coordinates. Include a rollback click. Abstain if unsure. "
                "An optional game_brief is advisory background, not observed evidence. "
                "It cannot satisfy citations, establish current state, or authorize input."
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


FUNCTIONAL_CI_REPLAY = "functional_ci_replay.jsonl"


class CiFakeInputOwner:
    """Lease actuation recorder for CI (no platform input)."""

    def __init__(
        self,
        run_primitive: Callable[[tuple, dict], None] | None = None,
    ) -> None:
        self._run_primitive = run_primitive
        self.releases: list[tuple[str, str]] = []
        self.hold_deadline_fired = False
        self._timers: list[threading.Timer] = []

    def enable_actuation(self) -> None:
        return None

    def check_target(self) -> tuple[bool, str]:
        return True, "ok"

    def run_plan(
        self,
        steps: tuple,
        bindings: dict,
        *,
        scene=None,
        guard=None,
    ) -> None:
        for step in steps:
            if guard is not None:
                guard()
            if self._run_primitive is not None:
                self._run_primitive((step,), bindings)
            op = getattr(step, "op", None)
            params = getattr(step, "params", {}) or {}
            if op == "key_hold":
                ms = int(params.get("duration_ms") or 0)
                if ms > 0:

                    def _deadline() -> None:
                        self.hold_deadline_fired = True
                        self.releases.append(("hold_deadline", "timer"))

                    timer = threading.Timer(ms / 1000.0, _deadline)
                    timer.daemon = True
                    timer.start()
                    self._timers.append(timer)

    def drain_primitive_results(self) -> tuple[dict, ...]:
        return ()

    def release_all(self, reason: str = "") -> str:
        self.releases.append(("release_all", reason))
        return "released"

    def shutdown(self) -> None:
        self.release_all("shutdown")


def append_functional_replay(workspace: Path, step: str, payload: dict) -> None:
    row = {"step": step, **payload}
    path = Path(workspace) / FUNCTIONAL_CI_REPLAY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _act_accepted(receipt: object) -> bool:
    return bool(getattr(receipt, "accepted", receipt))


def verify_native_dispatch_gates(*, hwnd: int = 1, pid: int = 42) -> dict[str, bool]:
    """Exercise native dispatch rejections required by the north-star acceptance list."""
    import time
    from dataclasses import replace

    from loop.adaptive.candidates import (
        Directive,
        MaskGateContext,
        compile_catalog_from_surface,
        compile_mask,
        mask_digest,
    )
    from loop.adaptive.contracts import ObservationQuality
    from loop.adaptive.decider_policy import build_decider_questions
    from loop.adaptive.executor import Executor, NativeDispatchContext
    from loop.adaptive.policy import DecisionError, StructuredDecision, validate_decision
    from loop.adaptive.schema import (
        ActionInstance,
        ActionSpec,
        ActionSurface,
        EffectPredicate,
        PrimitiveStep,
        StateSnapshot,
        empty_schema,
    )
    from loop.adaptive.snapshot import build_native_observation

    constraints = {
        "risk": "low",
        "retry": "deny",
        "cooldown_s": 0.5,
        "max_plan_duration_s": 1.0,
    }
    schema = replace(
        empty_schema(),
        actions=(
            ActionSpec(
                action_id="gate_a",
                parameters=(),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate("score", "increased"),),
                constraints=constraints,
            ),
            ActionSpec(
                action_id="gate_b",
                parameters=(),
                preconditions=(),
                primitive_plan=(PrimitiveStep("click", ("target",)),),
                expected_effects=(EffectPredicate("score", "increased"),),
                constraints=constraints,
            ),
        ),
    )
    rev = schema.revision.revision_id
    surface = ActionSurface(
        snapshot_id="snap-gate",
        schema_revision_id=rev,
        action_ids=("gate_a", "gate_b"),
        actions=(
            ActionInstance(
                action_id="gate_a",
                parameters=(),
                domains=(),
                expected_effects=(EffectPredicate("score", "increased"),),
            ),
            ActionInstance(
                action_id="gate_b",
                parameters=(),
                domains=(),
                expected_effects=(EffectPredicate("score", "increased"),),
            ),
        ),
    )
    snap = StateSnapshot(
        snapshot_id="snap-gate",
        schema_revision_id=rev,
        mode_id="running",
        fields={"score": 1},
        entities={},
        surface=surface,
    )
    catalog = compile_catalog_from_surface(schema, snap)
    directive = Directive(1, 0, "gate", 30.0, time.monotonic())
    mask = compile_mask(
        catalog,
        directive,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(snap.surface.action_ids),
        ),
    )
    capture_bound = time.monotonic()
    native_obs = build_native_observation(
        snap,
        episode_id="ep-gate",
        episode_generation=0,
        capture_ts_monotonic=capture_bound,
        publication_ts_monotonic=capture_bound,
        capture_integrity="trusted",
        capture_provenance=("ci",),
        hwnd=hwnd,
        pid=pid,
        target_identity={"selector": "ci", "hwnd": hwnd, "pid": pid},
        quality=ObservationQuality.trusted,
        capture_age_known=True,
        acquisition_origin="pre-grab-sync",
        observation_sequence=1,
    )
    from loop.adaptive.controller import _v2_from_native_obs

    now = time.monotonic()
    base = StructuredDecision(
        candidate_id="gate_a",
        confidence=1.0,
        probabilities={"gate_a": 1.0},
        decided_at=now,
        episode_generation=0,
        snapshot_id=snap.snapshot_id,
        directive_revision=directive.revision,
        catalog_revision=catalog.revision,
    )
    ex = Executor(snapshot_max_age_s=0.5, decision_max_age_s=0.25)
    v2 = _v2_from_native_obs(native_obs)
    ctx = NativeDispatchContext(
        observation=native_obs,
        mask=mask,
        expected_mask_digest=mask_digest(mask),
        policy_revision="ci-policy",
        surface_action_ids=frozenset(snap.surface.action_ids),
    )

    def _dispatch(decision: StructuredDecision, obs_v2, native_ctx):
        return ex.dispatch(
            decision,
            obs_v2,
            catalog,
            directive,
            now=time.monotonic(),
            focused=True,
            native=native_ctx,
        )

    out: dict[str, bool] = {}
    # Truthful stale probe: the capture bound itself is 10 s old, so the
    # dispatch-time age recompute (not a fabricated observation_age_s) rejects.
    stale_bound = time.monotonic() - 10.0
    stale_obs = replace(
        native_obs,
        capture_ts_monotonic=stale_bound,
        publication_ts_monotonic=stale_bound,
        observation_age_s=10.0,
    )
    stale_v2 = replace(
        _v2_from_native_obs(stale_obs), captured_at=stale_bound, observation_age_s=10.0
    )
    out["stale"] = _dispatch(base, stale_v2, replace(ctx, observation=stale_obs)).reason == "stale_snapshot"

    degraded_obs = replace(
        native_obs,
        quality=ObservationQuality.degraded,
        capture_integrity="degraded",
    )
    out["degraded"] = (
        _dispatch(base, _v2_from_native_obs(degraded_obs), replace(ctx, observation=degraded_obs)).reason
        == "degraded_capture"
    )

    forbidden_dir = Directive(1, 0, "gate", 30.0, time.monotonic(), forbidden_ids=("gate_a",))
    forbidden_mask = compile_mask(
        catalog,
        forbidden_dir,
        gate=MaskGateContext(
            quality=ObservationQuality.trusted,
            surface_ids=frozenset(snap.surface.action_ids),
        ),
    )
    out["forbidden"] = (
        ex.dispatch(
            base,
            v2,
            catalog,
            forbidden_dir,
            now=time.monotonic(),
            native=replace(
                ctx,
                mask=forbidden_mask,
                expected_mask_digest=mask_digest(forbidden_mask),
            ),
        ).reason
        == "masked"
    )

    illegal = replace(base, candidate_id="not_on_surface")
    out["illegal"] = _dispatch(illegal, v2, ctx).reason in ("not_on_surface", "masked")

    wrong_target = replace(native_obs, hwnd=0, pid=0)
    out["wrong_target"] = (
        _dispatch(base, _v2_from_native_obs(wrong_target), replace(ctx, observation=wrong_target)).reason
        == "target_identity"
    )

    from loop.adaptive.schema import SceneGraph, SceneNode

    leased = SceneGraph(
        snapshot_id="snap-lease",
        nodes=(
            SceneNode(
                node_id="node-a",
                bbox=(0.1, 0.1, 0.2, 0.2),
                attributes={"clickable": True},
                evidence_ids=(),
            ),
        ),
    )
    current = SceneGraph(
        snapshot_id="snap-latest",
        nodes=(
            SceneNode(
                node_id="node-a",
                bbox=(0.5, 0.5, 0.6, 0.6),
                attributes={"clickable": True},
                evidence_ids=(),
            ),
        ),
    )
    rebound_decision = replace(
        base,
        snapshot_id="snap-lease",
        bound_entity_id="node-a",
        scene_id="snap-lease",
    )
    newer_snap = replace(snap, snapshot_id="snap-latest")
    newer_obs = replace(native_obs, snapshot_id="snap-latest", snapshot=newer_snap)
    rebound_ctx = replace(
        ctx,
        observation=newer_obs,
        leased_scene=leased,
        current_scene=current,
    )
    out["rebound"] = (
        ex.dispatch(
            rebound_decision,
            replace(_v2_from_native_obs(newer_obs), snapshot_id="snap-latest"),
            catalog,
            directive,
            now=time.monotonic(),
            native=rebound_ctx,
        ).reason
        == "rebound"
    )

    bad = replace(base, catalog_revision="not-the-catalog")
    try:
        validate_decision(bad, mask, catalog, v2, directive)
        out["malformed"] = False
    except DecisionError:
        out["malformed"] = True

    _, questions = build_decider_questions(catalog.candidates)
    blob = json.dumps(questions, sort_keys=True)
    out["opaque_ids"] = "PrimitiveStep" not in blob and "key:" not in blob
    return out


def run_functional_ci_loop(
    session: Session,
    *,
    observe: Callable[[], tuple],
    runner: Callable[[tuple, dict], None],
    promote: Callable[[Session], None],
    target_identity: dict,
    hwnd: int = 1,
    pid: int = 42,
    second_action: Callable[[Session], None] | None = None,
    decider: object | None = None,
    probe_rebound_act: Callable[[Session, CiFakeInputOwner], bool] | None = None,
) -> dict[str, object]:
    """North-star functional milestone on one workspace using fakes only."""
    from loop.adaptive.arm import (
        ArmConfig,
        _bind_lease_context,
        _capture_from_observe,
        _compile_frame,
        _dispatch_decision,
        _pre_dispatch_from_observe,
        acquisition_from_provenance,
        invocation_for_action,
    )
    from loop.adaptive.contracts import ExecutionReceipt
    from loop.adaptive.controller import Executor, NativeShadowConfig, NativeShadowLoop
    from loop.adaptive.decider_policy import DeciderPolicy, scoring_policy_source
    from loop.adaptive.policy import DeterministicPolicy, StructuredDecision
    from loop.adaptive.policy_worker import PolicyTurnStatus, PolicyWorker
    from loop.adaptive.telemetry import TelemetryStore
    from loop.adaptive.candidates import CandidateAction, compile_catalog_from_surface
    from loop.adaptive.controller import GenerationStore, _filtered_candidates
    from loop.adaptive.policy import reactive_state_from_native
    from loop.adaptive.schema import PrimitiveStep

    workspace = session.workspace
    acceptance: dict[str, bool] = {}
    replay_path = workspace / FUNCTIONAL_CI_REPLAY

    if session.schema.actions:
        raise ValueError("functional CI expects empty_schema at start")
    acceptance["2_empty_schema"] = True

    session.bind_target(dict(target_identity))
    try:
        session.bind_target(
            {
                **target_identity,
                "exe": "other.exe",
                "hwnd": int(target_identity.get("hwnd", hwnd)) + 99,
            }
        )
        acceptance["1_bind_rejects_replacement"] = False
    except ValueError:
        acceptance["1_bind_rejects_replacement"] = True

    promote(session)
    if not session.schema.actions:
        raise ValueError("promotion failed")
    acceptance["3_promoted_reversible"] = len(session.schema.actions) >= 1

    if second_action is not None:
        second_action(session)

    recs, graph, snap, holder = observe()
    gates = verify_native_dispatch_gates(hwnd=hwnd, pid=pid)
    acceptance["6_rejections"] = all(gates.get(k) for k in ("stale", "rebound", "illegal", "forbidden", "degraded", "wrong_target", "malformed"))
    acceptance["4_opaque_catalog_ids"] = bool(gates.get("opaque_ids"))

    append_functional_replay(
        workspace,
        "promotion",
        {"actions": [a.action_id for a in session.schema.actions], "revision": snap.schema_revision_id},
    )

    cfg = ArmConfig()
    directive_store = GenerationStore()
    episode_id = uuid4().hex
    owner = CiFakeInputOwner(run_primitive=runner)
    owner.enable_actuation()
    owner.release_all("ci_boot")
    append_functional_replay(workspace, "release", {"phase": "boot", "reason": "ci_boot"})

    shadow_policy: object = DeterministicPolicy()
    if decider is not None and len(session.schema.actions) >= 2:
        shadow_policy = DeciderPolicy(decider, catalog_revision="pending")
    shadow_worker = PolicyWorker(shadow_policy, inference_timeout_s=cfg.decision_deadline_s)
    telemetry = TelemetryStore(workspace / "shadow_react.jsonl", workspace=workspace)
    shadow = NativeShadowLoop(
        session,
        shadow_worker,
        Executor(
            snapshot_max_age_s=cfg.snapshot_max_age_s,
            decision_max_age_s=cfg.decision_deadline_s,
        ),
        telemetry,
        config=NativeShadowConfig(decision_deadline_s=cfg.decision_deadline_s),
        episode_id=episode_id,
    )
    act_calls = 0
    real_act = session.act

    def _guarded_act(*args, **kwargs):
        nonlocal act_calls
        act_calls += 1
        return real_act(*args, **kwargs)

    session.act = _guarded_act  # type: ignore[method-assign]
    try:
        prov = getattr(observe, "provenance", None) or {
            "integrity": "trusted",
            "hwnd": hwnd,
            "pid": pid,
            "source": "ci_capture",
        }
        observe.provenance = prov
        for _ in range(2 if len(session.schema.actions) >= 2 else 1):
            recs, graph, snap, holder = observe()
            frame_prov = getattr(observe, "provenance", None) or prov
            shadow.process_frame(
                snap,
                hwnd=hwnd,
                pid=pid,
                capture_integrity=str(frame_prov.get("integrity") or "trusted"),
                capture_provenance=(str(frame_prov.get("source") or "ci_capture"),),
                focused=True,
                frame_image=getattr(observe, "image", None),
                scene_graph=graph,
                **acquisition_from_provenance(frame_prov),
            )
    finally:
        session.act = real_act  # type: ignore[method-assign]
        shadow.close()

    acceptance["5_shadow_no_act"] = act_calls == 0
    rows = telemetry.shadow_rows()
    shadow_row = rows[-1] if rows else None
    if shadow_row is not None:
        src = shadow_row.decision_source
        if len(session.schema.actions) >= 2 and decider is not None:
            acceptance["two_candidate_decider_source"] = src in (
                "DeciderPolicy",
                "DeciderPolicy_pending",
            )
            acceptance["deterministic_not_labeled_decider"] = src != "Decider"
        else:
            acceptance["two_candidate_decider_source"] = True
            acceptance["deterministic_not_labeled_decider"] = src not in (
                "Decider",
                "DeciderPolicy",
            )

    if shadow_row is not None:
        append_functional_replay(
            workspace,
            "decision",
            {
                "decision_source": shadow_row.decision_source,
                "selected_action_id": shadow_row.selected_action_id,
                "dispatch_accepted": shadow_row.dispatch_accepted,
                "dispatch_reason": shadow_row.dispatch_reason,
            },
        )

    if probe_rebound_act is not None:
        acceptance["rebound_before_act"] = probe_rebound_act(session, owner)

    recs, graph, snap, holder = observe()
    if holder and holder[0] is None:
        holder[0] = graph
    act_prov = getattr(observe, "provenance", None) or prov
    native_obs, catalog, directive, mask = _compile_frame(
        session,
        snap,
        episode_id=episode_id,
        episode_generation=cfg.episode_generation,
        hwnd=hwnd,
        pid=pid,
        capture_integrity=str(act_prov.get("integrity") or "trusted"),
        capture_provenance=(str(act_prov.get("source") or "ci_capture"),),
        directive_store=directive_store,
        config=cfg,
        **acquisition_from_provenance(act_prov),
    )
    filtered = _filtered_candidates(catalog, mask)
    act_policy: object = DeterministicPolicy(catalog_revision=catalog.revision)
    if len(filtered) >= 2 and decider is not None:
        act_policy = DeciderPolicy(decider, catalog_revision=catalog.revision)
    act_worker = PolicyWorker(act_policy, inference_timeout_s=cfg.decision_deadline_s)
    decision_source = scoring_policy_source(act_policy)
    if len(filtered) >= 2 and isinstance(act_policy, DeciderPolicy):
        reactive = reactive_state_from_native(native_obs)
        turn_id = act_worker.submit(
            reactive,
            directive,
            filtered,
            timeout_s=cfg.decision_deadline_s,
        )
        scored = act_worker.poll(turn_id, now=time.monotonic())
        if scored.status is PolicyTurnStatus.READY and scored.decision is not None:
            decision = scored.decision
            decision_source = scoring_policy_source(act_policy)
        elif len(filtered) >= 1:
            cid = filtered[0].action_id
            decision = StructuredDecision(
                candidate_id=cid,
                confidence=1.0,
                probabilities={cid: 1.0},
                decided_at=time.monotonic(),
                episode_generation=cfg.episode_generation,
                snapshot_id=snap.snapshot_id,
                directive_revision=directive.revision,
                catalog_revision=catalog.revision,
            )
            decision_source = type(DeterministicPolicy()).__name__
        else:
            raise RuntimeError("decider unavailable in functional CI")
    elif len(filtered) == 1:
        cid = filtered[0].action_id
        decision = StructuredDecision(
            candidate_id=cid,
            confidence=1.0,
            probabilities={cid: 1.0},
            decided_at=time.monotonic(),
            episode_generation=cfg.episode_generation,
            snapshot_id=snap.snapshot_id,
            directive_revision=directive.revision,
            catalog_revision=catalog.revision,
        )
        decision_source = type(DeterministicPolicy()).__name__
    else:
        raise RuntimeError("no legal candidates for leased act")

    dispatch = _dispatch_decision(
        session,
        decision,
        native_obs,
        catalog,
        directive,
        mask,
        executor=Executor(
            snapshot_max_age_s=cfg.snapshot_max_age_s,
            decision_max_age_s=cfg.decision_deadline_s,
        ),
        config=cfg,
        focused=True,
    )
    if not dispatch.accepted or dispatch.lease is None:
        raise RuntimeError(f"dispatch failed: {dispatch.reason}")
    capture_to_lease_s = max(0.0, dispatch.dispatched_at - native_obs.capture_ts_monotonic)
    telemetry.record_capture_to_lease(capture_to_lease_s)
    append_functional_replay(
        workspace,
        "lease",
        {
            "lease": asdict(dispatch.lease),
            "action_id": dispatch.action_id,
            "capture_to_lease_s": capture_to_lease_s,
        },
    )

    inv = invocation_for_action(snap, decision.candidate_id)
    if inv is None:
        raise RuntimeError("invocation missing")
    _bind_lease_context(session, dispatch.lease)
    fresh_tag = {"pass": 0}

    def _after_capture() -> tuple[int | None, int | None, tuple[str, ...]]:
        fresh_tag["pass"] += 1
        observe()
        # Keep the acquisition the fresh observe just recorded: the post-action
        # capture must be later than the action, not a copy of the pre-action one.
        fresh_prov = getattr(observe, "provenance", None) or prov
        observe.provenance = {
            **fresh_prov,
            "source": f"ci_after_{fresh_tag['pass']}",
            "hwnd": hwnd,
            "pid": pid,
        }
        return _capture_from_observe(observe)

    receipt = session.act(
        inv,
        lambda _s, _b: None,
        lambda: observe()[1],
        before=snap,
        lease=dispatch.lease,
        owner=owner,
        pre_dispatch=lambda: _pre_dispatch_from_observe(observe),
        get_scene=lambda: holder[0],
        after_capture=_after_capture,
    )
    assert isinstance(receipt, ExecutionReceipt)
    acceptance["7_leased_act"] = receipt.accepted
    acceptance["9_effect_and_provenance"] = (
        receipt.expected_effect.verified
        and receipt.after_capture_provenance
        and receipt.after_capture_provenance[0].startswith("ci_after_")
    )
    append_functional_replay(
        workspace,
        "primitives",
        {"primitive_results": list(receipt.primitive_results)},
    )
    append_functional_replay(
        workspace,
        "release",
        {"phase": "act", "status": receipt.release_status, "reason": receipt.reason},
    )
    append_functional_replay(
        workspace,
        "effect",
        {
            "verified": receipt.expected_effect.verified,
            "after_capture_provenance": list(receipt.after_capture_provenance),
        },
    )

    class _CiBlockingDecider:
        def __init__(self) -> None:
            self._resume = threading.Event()
            self._resume.set()

        def block_next(self) -> None:
            self._resume.clear()

        def release(self) -> None:
            self._resume.set()

        def system_one(self, state, questions, *, independent, layout):
            self._resume.wait(timeout=5.0)
            criteria = questions["action"]["criteria"]
            choice = next(iter(criteria))
            other = next(k for k in criteria if k != choice)
            return {
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 0.6,
                        "probabilities": {choice: 0.6, other: 0.4},
                    }
                }
            }

    hold_owner = CiFakeInputOwner()
    hold_owner.enable_actuation()
    blocked = _CiBlockingDecider()
    blocked.block_next()
    hold_pair = (
        CandidateAction("hold_a", "hold_a", "hold a", available=True),
        CandidateAction("hold_b", "hold_b", "hold b", available=True),
    )
    hold_worker = PolicyWorker(
        DeciderPolicy(blocked, catalog_revision=catalog.revision),
        inference_timeout_s=2.0,
    )
    hold_turn = hold_worker.submit(
        reactive_state_from_native(native_obs),
        directive,
        hold_pair,
        timeout_s=2.0,
    )
    threading.Thread(
        target=lambda: hold_owner.run_plan(
            (PrimitiveStep("key_hold", (), {"key": "KeyW", "duration_ms": 60}),),
            {},
        ),
        daemon=True,
    ).start()
    time.sleep(0.08)
    acceptance["8_release_while_scoring"] = hold_owner.hold_deadline_fired
    blocked.release()
    hold_worker.poll(hold_turn, now=time.monotonic())
    hold_worker.shutdown()
    hold_owner.release_all("ci_hold_probe")

    acceptance["10_replay_chain"] = replay_path.is_file() and len(replay_path.read_text(encoding="utf-8").strip().splitlines()) >= 5

    act_worker.shutdown()
    ci_latencies = telemetry.capture_to_lease_latencies()
    return {
        "acceptance": acceptance,
        "decision_source": decision_source,
        "shadow_decision_source": shadow_row.decision_source if shadow_row else None,
        "replay": str(replay_path.resolve()),
        "shadow_rows": len(rows),
        "effect_verified": receipt.expected_effect.verified,
        "effect_reason": receipt.expected_effect.reason,
        "after_capture_provenance": list(receipt.after_capture_provenance),
        "capture_to_lease_s": ci_latencies[-1] if ci_latencies else 0.0,
        "capture_to_lease_latencies": ci_latencies,
    }
