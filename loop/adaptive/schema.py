"""Adaptive schema types and revision I/O (game-agnostic)."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SchemaRevision:
    revision_id: str
    parent_id: str | None
    created_at: str  # ISO UTC
    payload_hash: str


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str  # "entity" | "point" | "scalar" | "enum" | "void"
    required: bool = True


_SCHEMA_PRIMITIVE_OPS = frozenset({"click", "move", "key_tap", "key_hold", "sleep"})
_TRUSTED_MOUSE_BUTTONS = frozenset({"left", "right", "middle"})
_MAX_SLEEP_S = 5.0
_MAX_KEY_TAP_MS = 500
_MAX_KEY_HOLD_MS = 2000


class PrimitiveSchemaError(ValueError):
    """Invalid primitive op or params at schema compile time."""


def _validate_primitive_params(op: str, params: dict) -> dict:
    if not isinstance(params, dict):
        raise PrimitiveSchemaError("params must be a mapping")
    unknown = set(params) - _allowed_param_keys(op)
    if unknown:
        raise PrimitiveSchemaError(f"unknown params for {op!r}: {sorted(unknown)}")
    if op == "click":
        button = params.get("button", "left")
        if button not in _TRUSTED_MOUSE_BUTTONS:
            raise PrimitiveSchemaError(f"click button not trusted: {button!r}")
        return {"button": str(button)}
    if op == "move":
        if params:
            raise PrimitiveSchemaError("move accepts no params")
        return {}
    if op == "sleep":
        duration_s = float(params.get("duration_s", 0.0))
        if duration_s < 0 or duration_s > _MAX_SLEEP_S:
            raise PrimitiveSchemaError(f"sleep duration_s out of range: {duration_s}")
        return {"duration_s": duration_s}
    if op == "key_tap":
        key = params.get("key")
        if not isinstance(key, str) or not key.strip():
            raise PrimitiveSchemaError("key_tap requires non-empty key")
        from loop.os.keyboard import is_allowlisted_key

        if not is_allowlisted_key(key):
            raise PrimitiveSchemaError(f"key_tap key not allowlisted: {key!r}")
        duration_ms = params.get("duration_ms", 50)
        if not isinstance(duration_ms, (int, float)):
            raise PrimitiveSchemaError("key_tap duration_ms must be numeric")
        duration_ms = int(duration_ms)
        if duration_ms <= 0 or duration_ms > _MAX_KEY_TAP_MS:
            raise PrimitiveSchemaError(f"key_tap duration_ms out of range: {duration_ms}")
        return {"key": key, "duration_ms": duration_ms}
    if op == "key_hold":
        key = params.get("key")
        if not isinstance(key, str) or not key.strip():
            raise PrimitiveSchemaError("key_hold requires non-empty key")
        from loop.os.keyboard import is_allowlisted_key

        if not is_allowlisted_key(key):
            raise PrimitiveSchemaError(f"key_hold key not allowlisted: {key!r}")
        if "duration_ms" not in params:
            raise PrimitiveSchemaError("key_hold requires duration_ms")
        duration_ms = params["duration_ms"]
        if not isinstance(duration_ms, (int, float)):
            raise PrimitiveSchemaError("key_hold duration_ms must be numeric")
        duration_ms = int(duration_ms)
        if duration_ms <= 0 or duration_ms > _MAX_KEY_HOLD_MS:
            raise PrimitiveSchemaError(f"key_hold duration_ms out of range: {duration_ms}")
        return {"key": key, "duration_ms": duration_ms}
    raise PrimitiveSchemaError(f"unsupported primitive op: {op!r}")


def _allowed_param_keys(op: str) -> frozenset[str]:
    if op == "click":
        return frozenset({"button"})
    if op == "move":
        return frozenset()
    if op == "sleep":
        return frozenset({"duration_s"})
    if op == "key_tap":
        return frozenset({"key", "duration_ms"})
    if op == "key_hold":
        return frozenset({"key", "duration_ms"})
    return frozenset()


def validate_primitive_step(step: "PrimitiveStep") -> None:
    if step.op not in _SCHEMA_PRIMITIVE_OPS:
        raise PrimitiveSchemaError(f"unsupported primitive op: {step.op!r}")
    if not isinstance(step.bindings, tuple) or any(not isinstance(b, str) for b in step.bindings):
        raise PrimitiveSchemaError("bindings must be a tuple of strings")
    _validate_primitive_params(step.op, dict(step.params))


@dataclass(frozen=True)
class PrimitiveStep:
    op: str  # "click" | "move" | "key_tap" | "key_hold" | "sleep"
    bindings: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params))
        validate_primitive_step(self)


@dataclass(frozen=True)
class EffectPredicate:
    field_id: str
    op: str  # "changed" | "equals" | "increased" | "decreased" | "set" | "appeared" | "disappeared"
    entity_id: str | None = None
    value: object | None = None
    binding: str | None = None  # resolve entity_id from invocation.bindings


@dataclass(frozen=True)
class ActionSpec:
    action_id: str
    parameters: tuple[ParamSpec, ...]
    preconditions: tuple[str, ...]
    primitive_plan: tuple[PrimitiveStep, ...]
    expected_effects: tuple[EffectPredicate, ...]
    selector: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if re.search(r"track_\d+", self.action_id):
            raise ValueError(f"track_N action ids are not allowed: {self.action_id!r}")


@dataclass(frozen=True)
class FieldSpec:
    field_id: str
    value_type: str  # "number" | "text" | "enum" | "bool"
    sensor_id: str = ""
    normalizer: str = ""
    selector: dict = field(default_factory=dict)
    decoder: str = ""


@dataclass(frozen=True)
class EntityTypeSpec:
    type_id: str
    field_ids: tuple[str, ...]
    classifier: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ModeSpec:
    mode_id: str
    activation: dict = field(default_factory=dict)
    transitions: tuple = ()


@dataclass(frozen=True)
class SensorRecipe:
    sensor_id: str
    kind: str  # "ocr" | "region" | "motion" | "color" | "track" | "vlm"
    config: dict = field(default_factory=dict)
    roi: tuple[float, float, float, float] | None = None
    fallback_sensor_id: str = ""
    min_confidence: float = 0.0


@dataclass(frozen=True)
class PromotionPolicy:
    min_agreeing_reads: int = 3
    min_correlated_deltas: int = 2
    require_replay: bool = True


@dataclass(frozen=True)
class AdaptiveSchema:
    revision: SchemaRevision
    fields: tuple[FieldSpec, ...]
    entity_types: tuple[EntityTypeSpec, ...]
    modes: tuple[ModeSpec, ...]
    sensors: tuple[SensorRecipe, ...]
    actions: tuple[ActionSpec, ...]
    policy: PromotionPolicy = PromotionPolicy()


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    kind: str
    payload: dict


class EvidenceLedger:
    """Append-only ledger; optional JSONL persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._records: list[EvidenceRecord] = []
        if self._path is not None and self._path.is_file():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                self._records.append(
                    EvidenceRecord(
                        evidence_id=str(obj["evidence_id"]),
                        kind=str(obj["kind"]),
                        payload=dict(obj["payload"]),
                    )
                )

    def append(self, record: EvidenceRecord) -> None:
        self._records.append(record)
        if self._path is None:
            return
        line = json.dumps(
            {
                "evidence_id": record.evidence_id,
                "kind": record.kind,
                "payload": record.payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def append_event(self, kind: str, payload: dict) -> EvidenceRecord:
        from loop.adaptive.evidence import ALLOWED_KINDS, evidence_record, new_evidence_id

        if kind in ALLOWED_KINDS:
            record = evidence_record(kind, payload)
        else:
            record = EvidenceRecord(
                evidence_id=new_evidence_id(), kind=kind, payload=dict(payload)
            )
        self.append(record)
        return record

    def all(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)


@dataclass(frozen=True)
class SceneNode:
    node_id: str
    bbox: tuple[float, float, float, float]
    evidence_ids: tuple[str, ...]
    attributes: dict


@dataclass(frozen=True)
class Relation:
    rel_id: str
    kind: str
    src: str
    dst: str


@dataclass(frozen=True)
class SceneGraph:
    snapshot_id: str
    nodes: tuple[SceneNode, ...]
    relations: tuple[Relation, ...] = ()


@dataclass(frozen=True)
class BindingDomain:
    name: str
    kind: str
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionInstance:
    action_id: str
    parameters: tuple[ParamSpec, ...]
    domains: tuple[BindingDomain, ...]
    expected_effects: tuple[EffectPredicate, ...]
    bound_node_id: str | None = None


@dataclass(frozen=True)
class ActionSurface:
    snapshot_id: str
    schema_revision_id: str
    action_ids: tuple[str, ...]
    actions: tuple[ActionInstance, ...] = ()


@dataclass(frozen=True)
class StateSnapshot:
    snapshot_id: str
    schema_revision_id: str
    mode_id: str | None
    fields: dict
    entities: dict
    surface: ActionSurface


@dataclass(frozen=True)
class ActionInvocation:
    action_id: str
    bindings: dict
    snapshot_id: str
    schema_revision_id: str = ""


def _sort_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sort_value(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_sort_value(v) for v in value]
    if isinstance(value, tuple):
        return [_sort_value(v) for v in value]
    return value


def _to_plain(obj: Any) -> Any:
    if is_dataclass(obj):
        payload = {f.name: _to_plain(getattr(obj, f.name)) for f in fields(obj)}
        if isinstance(obj, PrimitiveStep) and not payload.get("params"):
            payload.pop("params", None)
        return _sort_value(payload)
    if isinstance(obj, tuple):
        return [_to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return _sort_value({k: _to_plain(v) for k, v in obj.items()})
    return obj


def schema_payload(schema: AdaptiveSchema) -> dict:
    return _to_plain(schema)


def hash_payload(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(blob.encode("utf-8")).hexdigest()


def _stamp_revision(
    schema: AdaptiveSchema,
    *,
    revision_id: str,
    parent_id: str | None,
    created_at: str,
) -> AdaptiveSchema:
    draft = replace(
        schema,
        revision=SchemaRevision(revision_id, parent_id, created_at, ""),
    )
    payload = schema_payload(draft)
    digest = hash_payload(payload)
    return replace(
        draft,
        revision=SchemaRevision(revision_id, parent_id, created_at, digest),
    )


def empty_schema() -> AdaptiveSchema:
    rid = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    base = AdaptiveSchema(
        revision=SchemaRevision(rid, None, ts, ""),
        fields=(),
        entity_types=(),
        modes=(),
        sensors=(),
        actions=(),
    )
    return _stamp_revision(base, revision_id=rid, parent_id=None, created_at=ts)


def with_revision(schema: AdaptiveSchema, parent_id: str | None) -> AdaptiveSchema:
    rid = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    parent = parent_id if parent_id is not None else schema.revision.revision_id
    copied = replace(schema, revision=SchemaRevision(rid, parent, ts, ""))
    return _stamp_revision(copied, revision_id=rid, parent_id=parent, created_at=ts)


HEAD_FILENAME = "HEAD.json"


def revision_files(dir: Path) -> list[Path]:
    return sorted(p for p in dir.glob("*.json") if p.name != HEAD_FILENAME)


def _atomic_replace(dest: Path, text: str) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)


def write_head(dir: Path, revision_id: str) -> Path:
    dest = dir / HEAD_FILENAME
    _atomic_replace(dest, json.dumps({"revision_id": revision_id}, sort_keys=True))
    return dest


def load_head(dir: Path) -> AdaptiveSchema:
    head = json.loads((dir / HEAD_FILENAME).read_text(encoding="utf-8"))
    rid = str(head["revision_id"])
    return load_revision(dir / f"{rid}.json")


def atomic_write_revision(dir: Path, schema: AdaptiveSchema) -> Path:
    dir.mkdir(parents=True, exist_ok=True)
    dest = dir / f"{schema.revision.revision_id}.json"
    if dest.exists():
        raise FileExistsError(dest)
    payload = schema_payload(schema)
    _atomic_replace(dest, json.dumps(payload, sort_keys=True, indent=2))
    write_head(dir, schema.revision.revision_id)
    return dest


def _from_dict(cls: type, data: dict) -> Any:
    if cls is AdaptiveSchema:
        return AdaptiveSchema(
            revision=_from_dict(SchemaRevision, data["revision"]),
            fields=tuple(_from_dict(FieldSpec, x) for x in data["fields"]),
            entity_types=tuple(_from_dict(EntityTypeSpec, x) for x in data["entity_types"]),
            modes=tuple(_from_dict(ModeSpec, x) for x in data["modes"]),
            sensors=tuple(_from_dict(SensorRecipe, x) for x in data["sensors"]),
            actions=tuple(_from_dict(ActionSpec, x) for x in data["actions"]),
            policy=_from_dict(PromotionPolicy, data.get("policy", {})),
        )
    if cls is SchemaRevision:
        return SchemaRevision(**data)
    if cls is FieldSpec:
        d = dict(data)
        d.setdefault("sensor_id", "")
        d.setdefault("normalizer", "")
        d.setdefault("selector", {})
        d.setdefault("decoder", "")
        return FieldSpec(**d)
    if cls is EntityTypeSpec:
        d = dict(data)
        d["field_ids"] = tuple(d["field_ids"])
        d.setdefault("classifier", {})
        return EntityTypeSpec(**d)
    if cls is ModeSpec:
        d = dict(data)
        d.setdefault("activation", {})
        trans = d.setdefault("transitions", ())
        if isinstance(trans, list):
            d["transitions"] = tuple(trans)
        return ModeSpec(**d)
    if cls is SensorRecipe:
        d = dict(data)
        d.setdefault("config", {})
        roi = d.get("roi")
        if isinstance(roi, list):
            d["roi"] = tuple(roi)
        d.setdefault("fallback_sensor_id", "")
        d.setdefault("min_confidence", 0.0)
        return SensorRecipe(**d)
    if cls is PromotionPolicy:
        return PromotionPolicy(**data)
    if cls is ActionSpec:
        d = dict(data)
        d.setdefault("selector", {})
        d.setdefault("constraints", {})
        return ActionSpec(
            action_id=d["action_id"],
            parameters=tuple(_from_dict(ParamSpec, x) for x in d["parameters"]),
            preconditions=tuple(d["preconditions"]),
            primitive_plan=tuple(_from_dict(PrimitiveStep, x) for x in d["primitive_plan"]),
            expected_effects=tuple(_from_dict(EffectPredicate, x) for x in d["expected_effects"]),
            selector=d["selector"],
            constraints=d["constraints"],
        )
    if cls is ParamSpec:
        return ParamSpec(**data)
    if cls is PrimitiveStep:
        d = dict(data)
        d["bindings"] = tuple(d.get("bindings", ()))
        d.setdefault("params", {})
        return PrimitiveStep(**d)
    if cls is EffectPredicate:
        d = dict(data)
        d.setdefault("entity_id", None)
        d.setdefault("value", None)
        d.setdefault("binding", None)
        return EffectPredicate(**d)
    raise TypeError(f"unsupported type {cls}")


def load_revision(path: Path) -> AdaptiveSchema:
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = _from_dict(AdaptiveSchema, data)
    stored = schema.revision.payload_hash
    blank = replace(schema, revision=replace(schema.revision, payload_hash=""))
    if hash_payload(schema_payload(blank)) != stored:
        raise ValueError("payload_hash mismatch")
    parent_id = schema.revision.parent_id
    if parent_id:
        parent = path.parent / f"{parent_id}.json"
        if not parent.exists():
            raise ValueError(f"missing parent revision {parent_id}")
    return schema


def eval_precondition(expr: str, snapshot: StateSnapshot) -> bool:
    expr = expr.strip()
    if expr.startswith("mode=="):
        want = expr[6:].strip()
        return snapshot.mode_id == want
    if "==" in expr:
        left, right = expr.split("==", 1)
        fid = left.strip()
        want_raw = right.strip()
        if fid not in snapshot.fields:
            return False
        val = snapshot.fields[fid]
        if isinstance(val, bool):
            want = want_raw.lower() == "true"
        elif isinstance(val, (int, float)):
            want = type(val)(want_raw)
        else:
            want = want_raw
        return val == want
    return False
