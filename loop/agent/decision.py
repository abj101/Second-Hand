"""Typed, text-only ACT decisions for the STS2 combat loop.

This module deliberately knows nothing about marks, pixels, or MCP tools.  A
caller supplies a fresh :class:`TurnSnapshot`, gets a stable-ID intent back,
and resolves it against its own current-frame executor.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Protocol, TypeAlias

from loop.model.gateway import ModelCall, VlmGateway

ACT_PRIMARY_MODEL = "gemma4:31b-cloud"
ACT_FALLBACK_MODEL = "qwen3.5:4b-instruct-q4_K_M"
ACT_PRIMARY_TIMEOUT_S = 8.0
ACT_FALLBACK_TIMEOUT_S = 15.0
ACT_MAX_TOKENS = 96
ACT_NUM_CTX = 2048
ACT_PROMPT_ID = "sts2-action-intent-v2"


@dataclass(frozen=True)
class BoundingBox:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class CardSnapshot:
    card_id: str
    name: str | None
    bbox: BoundingBox
    playable: bool
    description: str | None = None
    card_type: str | None = None
    cost: int | None = None


@dataclass(frozen=True)
class TargetSnapshot:
    target_id: str
    bbox: BoundingBox
    name: str | None = None
    hp: int | None = None
    intent: str | None = None
    identity_confidence: float | None = None

    def __post_init__(self) -> None:
        conf = self.identity_confidence
        if conf is None:
            return
        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
            object.__setattr__(self, "identity_confidence", None)
            return
        number = float(conf)
        if 0.0 <= number <= 1.0:
            object.__setattr__(self, "identity_confidence", number)
        else:
            object.__setattr__(self, "identity_confidence", None)


@dataclass(frozen=True)
class ControlSnapshot:
    control_id: str
    label: str
    bbox: BoundingBox
    role: str = "button"


@dataclass(frozen=True)
class TurnSnapshot:
    snapshot_id: str
    screen: str
    turn: str | None
    hp: int | None
    block: int | None
    energy: int | None
    cards: tuple[CardSnapshot, ...] = ()
    targets: tuple[TargetSnapshot, ...] = ()
    controls: tuple[ControlSnapshot, ...] = ()
    legal_intents: tuple[str, ...] | None = None
    last_execution: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        # frozen dataclasses prevent attribute reassignment; normalize their
        # containers too so a producer cannot mutate a snapshot in place.
        object.__setattr__(self, "cards", tuple(self.cards))
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "controls", tuple(self.controls))
        if self.legal_intents is not None:
            object.__setattr__(self, "legal_intents", tuple(dict.fromkeys(self.legal_intents)))
        if self.last_execution is not None:
            object.__setattr__(self, "last_execution", MappingProxyType(dict(self.last_execution)))

    @classmethod
    def from_action_surface(
        cls,
        surface: Any,
        *,
        turn: str | None = None,
        hp: int | None = None,
        block: int | None = None,
        energy: int | None = None,
        last_execution: Mapping[str, Any] | None = None,
    ) -> TurnSnapshot:
        """Convert the canonical current-frame surface into ACT's typed view."""
        cards: list[CardSnapshot] = []
        targets: list[TargetSnapshot] = []
        controls: list[ControlSnapshot] = []
        for obj in tuple(getattr(surface, "objects", ()) or ()):
            bbox = BoundingBox(float(obj.x0), float(obj.y0), float(obj.x1), float(obj.y1))
            role = str(getattr(obj, "role", "") or "").strip().lower()
            object_id = str(getattr(obj, "object_id", "") or "").strip()
            label = str(getattr(obj, "label", "") or "").strip()
            if not object_id:
                continue
            if role == "card":
                cards.append(CardSnapshot(object_id, label or None, bbox, playable=True))
            elif role == "enemy":
                targets.append(TargetSnapshot(object_id, bbox, name=label or None))
            elif role in {"button", "control", "nav_prev", "nav_next"}:
                controls.append(ControlSnapshot(object_id, label or object_id, bbox, role=role))
        context = getattr(surface, "context", None)
        return cls(
            snapshot_id=str(getattr(surface, "snapshot_id", "") or ""),
            screen=str(getattr(context, "screen", "other") or "other"),
            turn=turn,
            hp=hp,
            block=block,
            energy=energy,
            cards=tuple(cards),
            targets=tuple(targets),
            controls=tuple(controls),
            legal_intents=tuple(getattr(surface, "legal_intents", ()) or ()),
            last_execution=last_execution,
        )


@dataclass(frozen=True)
class PlayCardIntent:
    kind: Literal["play_card"] = "play_card"
    card_id: str = ""
    target_id: str | None = None


@dataclass(frozen=True)
class EndTurnIntent:
    kind: Literal["end_turn"] = "end_turn"
    control_id: str = ""


@dataclass(frozen=True)
class ClickControlIntent:
    kind: Literal["click_control"] = "click_control"
    control_id: str = ""


@dataclass(frozen=True)
class InspectIntent:
    kind: Literal["inspect"] = "inspect"
    object_id: str = ""


ActionIntent: TypeAlias = PlayCardIntent | EndTurnIntent | ClickControlIntent | InspectIntent

ACTION_INTENT_SCHEMA: dict[str, Any] = {
    "oneOf": [
        {"type": "object", "additionalProperties": False, "properties": {
            "kind": {"const": "play_card"}, "card_id": {"type": "string", "minLength": 1},
            "target_id": {"type": ["string", "null"], "minLength": 1},
        }, "required": ["kind", "card_id"]},
        {"type": "object", "additionalProperties": False, "properties": {
            "kind": {"const": "end_turn"}, "control_id": {"type": "string", "minLength": 1},
        }, "required": ["kind", "control_id"]},
        {"type": "object", "additionalProperties": False, "properties": {
            "kind": {"const": "click_control"}, "control_id": {"type": "string", "minLength": 1},
        }, "required": ["kind", "control_id"]},
        {"type": "object", "additionalProperties": False, "properties": {
            "kind": {"const": "inspect"}, "object_id": {"type": "string", "minLength": 1},
        }, "required": ["kind", "object_id"]},
    ]
}

ACT_SYSTEM_PROMPT = "Return exactly one ActionIntent JSON object matching the supplied schema. No prose."


class ActionIntentError(ValueError):
    pass


def _semantic_key(value: str | None) -> str:
    return " ".join(str(value or "").casefold().replace("_", " ").replace("-", " ").split())


def _is_end_turn_control(control: ControlSnapshot) -> bool:
    return _semantic_key(control.role) == "end turn" or _semantic_key(control.label) == "end turn"


def legal_intents_for_snapshot(snapshot: TurnSnapshot) -> tuple[dict[str, Any], ...]:
    """Return the exact intent variants and stable IDs ACT may emit.

    This is deliberately derived from the immutable snapshot rather than from
    game-specific prose.  It is used both in the prompt and to validate the
    model response, so the advertised and enforced action surfaces cannot
    drift apart.
    """
    playable_cards = tuple(card.card_id for card in snapshot.cards if card.playable and card.card_id)
    target_ids = tuple(target.target_id for target in snapshot.targets if target.target_id)
    control_ids = tuple(control.control_id for control in snapshot.controls if control.control_id)
    end_turn_ids = tuple(
        control.control_id for control in snapshot.controls
        if control.control_id and _is_end_turn_control(control)
    )
    object_ids = tuple(dict.fromkeys((
        *(card.card_id for card in snapshot.cards if card.card_id),
        *(target.target_id for target in snapshot.targets if target.target_id),
        *control_ids,
    )))

    allowed = set(snapshot.legal_intents) if snapshot.legal_intents is not None else None
    legal: list[dict[str, Any]] = []
    if playable_cards and (allowed is None or "play_card" in allowed):
        legal.append({"kind": "play_card", "card_ids": playable_cards, "target_ids": target_ids})
    if end_turn_ids and (allowed is None or "end_turn" in allowed):
        legal.append({"kind": "end_turn", "control_ids": end_turn_ids})
    if control_ids and (allowed is None or "click_control" in allowed):
        legal.append({"kind": "click_control", "control_ids": control_ids})
    if object_ids and (allowed is None or "inspect" in allowed):
        legal.append({"kind": "inspect", "object_ids": object_ids})
    return tuple(legal)


def validate_action_intent(snapshot: TurnSnapshot, intent: ActionIntent) -> ActionIntent:
    """Reject an otherwise well-formed intent that is not legal on ``snapshot``."""
    cards = {card.card_id: card for card in snapshot.cards if card.card_id}
    targets = {target.target_id for target in snapshot.targets if target.target_id}
    controls = {control.control_id: control for control in snapshot.controls if control.control_id}

    if isinstance(intent, PlayCardIntent):
        card = cards.get(intent.card_id)
        if card is None:
            raise ActionIntentError("invalid-act-intent:unknown-card-id")
        if not card.playable:
            raise ActionIntentError("invalid-act-intent:card-not-playable")
        if intent.target_id is not None and intent.target_id not in targets:
            raise ActionIntentError("invalid-act-intent:unknown-target-id")
    elif isinstance(intent, EndTurnIntent):
        control = controls.get(intent.control_id)
        if control is None:
            raise ActionIntentError("invalid-act-intent:unknown-control-id")
        if not _is_end_turn_control(control):
            raise ActionIntentError("invalid-act-intent:not-end-turn-control")
    elif isinstance(intent, ClickControlIntent):
        if intent.control_id not in controls:
            raise ActionIntentError("invalid-act-intent:unknown-control-id")
    elif isinstance(intent, InspectIntent):
        if intent.object_id not in cards and intent.object_id not in targets and intent.object_id not in controls:
            raise ActionIntentError("invalid-act-intent:unknown-object-id")
    legal_kinds = {str(row["kind"]) for row in legal_intents_for_snapshot(snapshot)}
    if intent.kind not in legal_kinds:
        raise ActionIntentError(f"invalid-act-intent:unavailable-kind:{intent.kind}")
    return intent


def _schema_for_snapshot(snapshot: TurnSnapshot) -> dict[str, Any]:
    legal = {str(row["kind"]): row for row in legal_intents_for_snapshot(snapshot)}
    variants: list[dict[str, Any]] = []
    for original in ACTION_INTENT_SCHEMA["oneOf"]:
        kind = str(original.get("properties", {}).get("kind", {}).get("const", ""))
        row = legal.get(kind)
        if row is None:
            continue
        variant = json.loads(json.dumps(original))
        props = variant["properties"]
        if kind == "play_card":
            props["card_id"] = {"type": "string", "enum": list(row["card_ids"])}
            if row["target_ids"]:
                props["target_id"] = {"type": ["string", "null"], "enum": [*row["target_ids"], None]}
        elif kind in {"end_turn", "click_control"}:
            props["control_id"] = {"type": "string", "enum": list(row["control_ids"])}
        elif kind == "inspect":
            props["object_id"] = {"type": "string", "enum": list(row["object_ids"])}
        variants.append(variant)
    return {"oneOf": variants}


def action_intent_from_dict(value: dict[str, Any] | None) -> ActionIntent:
    """Parse strictly; rejected keys prevent legacy coordinates leaking into ACT."""
    if not isinstance(value, dict):
        raise ActionIntentError("malformed-json")
    kind = value.get("kind")
    if kind == "play_card":
        _only(value, {"kind", "card_id", "target_id"})
        card_id = _required_string(value, "card_id")
        target_id = value.get("target_id")
        if target_id is not None and not isinstance(target_id, str):
            raise ActionIntentError("invalid-target-id")
        if isinstance(target_id, str) and not target_id.strip():
            raise ActionIntentError("invalid-target-id")
        return PlayCardIntent(card_id=card_id, target_id=target_id)
    if kind in {"end_turn", "click_control"}:
        _only(value, {"kind", "control_id"})
        control_id = _required_string(value, "control_id")
        return EndTurnIntent(control_id=control_id) if kind == "end_turn" else ClickControlIntent(control_id=control_id)
    if kind == "inspect":
        _only(value, {"kind", "object_id"})
        return InspectIntent(object_id=_required_string(value, "object_id"))
    raise ActionIntentError("unknown-action-kind")


def compact_act_prompt(snapshot: TurnSnapshot) -> str:
    """Emit only decision-relevant typed state; bounding boxes never reach ACT."""
    payload: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id,
        "screen": snapshot.screen,
        "turn": snapshot.turn,
        "player": {"hp": snapshot.hp, "block": snapshot.block, "energy": snapshot.energy},
        "cards": [{"card_id": c.card_id, "name": c.name, "playable": c.playable,
                   "description": c.description, "type": c.card_type, "cost": c.cost} for c in snapshot.cards],
        "targets": [{"target_id": t.target_id, "name": t.name, "hp": t.hp,
                     "intent": t.intent, "identity_confidence": t.identity_confidence} for t in snapshot.targets],
        "controls": [{"control_id": c.control_id, "label": c.label, "role": c.role} for c in snapshot.controls],
        "legal_intents": [dict(row) for row in legal_intents_for_snapshot(snapshot)],
        "last_execution": dict(snapshot.last_execution) if snapshot.last_execution is not None else None,
        "response_schema": _schema_for_snapshot(snapshot),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _bbox_payload(bbox: BoundingBox) -> dict[str, float]:
    return {"x0": float(bbox.x0), "y0": float(bbox.y0), "x1": float(bbox.x1), "y1": float(bbox.y1)}


def turn_snapshot_for_log(snapshot: TurnSnapshot) -> dict[str, Any]:
    """Compact ACT payload plus bboxes for hygiene / replay. Never sent to ACT."""
    payload = json.loads(compact_act_prompt(snapshot))
    payload.pop("response_schema", None)
    cards = {card.card_id: card for card in snapshot.cards}
    for row in payload.get("cards") or []:
        if not isinstance(row, dict):
            continue
        card = cards.get(str(row.get("card_id") or ""))
        if card is not None:
            row["bbox"] = _bbox_payload(card.bbox)
    targets = {target.target_id: target for target in snapshot.targets}
    for row in payload.get("targets") or []:
        if not isinstance(row, dict):
            continue
        target = targets.get(str(row.get("target_id") or ""))
        if target is not None:
            row["bbox"] = _bbox_payload(target.bbox)
    return payload


class _Gateway(Protocol):
    def complete_text(self, **kwargs: Any) -> ModelCall: ...


GatewayFactory: TypeAlias = Callable[..., _Gateway]


@dataclass(frozen=True)
class ActDecision:
    snapshot_id: str
    intent: ActionIntent
    backend: str
    fallback_reason: str | None
    latency_ms: float
    metrics: dict[str, Any] = field(default_factory=dict)


class ActReasonerError(RuntimeError):
    def __init__(self, message: str, *, primary_error: str, fallback_error: str | None = None) -> None:
        super().__init__(message)
        self.primary_error = primary_error
        self.fallback_error = fallback_error


class ActReasoner:
    """One text-only primary call, then exactly one local fallback on failure."""
    def __init__(
        self, *, gateway_factory: GatewayFactory = VlmGateway,
        primary_model: str = ACT_PRIMARY_MODEL, fallback_model: str = ACT_FALLBACK_MODEL,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.primary_model = primary_model
        self.fallback_model = fallback_model

    def decide(self, snapshot: TurnSnapshot) -> ActDecision:
        if not legal_intents_for_snapshot(snapshot):
            # This is a harness/perception state, not a model decision.  In
            # particular, do not spend either the primary or fallback call on
            # a prompt that has no valid response.
            raise ActReasonerError(
                "no-legal-intents", primary_error="no-legal-intents"
            )
        prompt = compact_act_prompt(snapshot)
        try:
            return self._call(snapshot, prompt, self.primary_model, ACT_PRIMARY_TIMEOUT_S, None)
        except Exception as primary_exc:  # The fallback boundary intentionally includes HTTP and parsing failures.
            reason = _failure_reason(primary_exc)
        try:
            return self._call(snapshot, prompt, self.fallback_model, ACT_FALLBACK_TIMEOUT_S, reason)
        except Exception as fallback_exc:
            raise ActReasonerError(
                (
                    "invalid-act-intent"
                    if _failure_reason(fallback_exc).startswith("invalid-act-intent:")
                    else "ACT primary and fallback failed"
                ), primary_error=reason,
                fallback_error=_failure_reason(fallback_exc),
            ) from fallback_exc

    def _call(self, snapshot: TurnSnapshot, prompt: str, model: str, timeout_s: float,
              fallback_reason: str | None) -> ActDecision:
        gateway = self.gateway_factory(
            model=model, temperature=0.1, max_tokens=ACT_MAX_TOKENS, num_ctx=ACT_NUM_CTX,
        )
        call = gateway.complete_text(
            system=ACT_SYSTEM_PROMPT, user=prompt, json_mode=True,
            max_tokens=ACT_MAX_TOKENS, timeout_s=timeout_s,
        )
        intent = validate_action_intent(snapshot, action_intent_from_dict(call.parsed))
        metrics = dict(getattr(call, "metrics", {}) or {})
        for key in (
            "prompt_tokens", "output_tokens", "load_duration_ns",
            "prompt_eval_duration_ns", "eval_duration_ns", "total_duration_ns",
        ):
            metrics.setdefault(key, getattr(call, key, None))
        metrics.update({
            "backend": model, "fallback_reason": fallback_reason,
            "image_supplied": bool(getattr(call, "image_supplied", False)),
            "prompt_chars": len(prompt), "max_tokens": ACT_MAX_TOKENS,
            "num_ctx": ACT_NUM_CTX, "temperature": 0.1,
        })
        return ActDecision(snapshot.snapshot_id, intent, model, fallback_reason, call.latency_ms, metrics)


def decide_act(snapshot: TurnSnapshot, **kwargs: Any) -> ActDecision:
    """Convenience entry point for a single typed ACT decision."""
    return ActReasoner(**kwargs).decide(snapshot)


def _only(value: dict[str, Any], allowed: set[str]) -> None:
    extra = set(value) - allowed
    if extra:
        raise ActionIntentError("unexpected-fields:" + ",".join(sorted(extra)))


def _required_string(value: dict[str, Any], key: str) -> str:
    answer = value.get(key)
    if not isinstance(answer, str) or not answer.strip():
        raise ActionIntentError(f"invalid-{key}")
    return answer


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, ActionIntentError):
        return str(exc)
    if isinstance(exc, TimeoutError):
        return "timeout"
    return f"{type(exc).__name__}:{str(exc)[:200]}"
