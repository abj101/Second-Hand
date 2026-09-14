"""Episode-boundary semantic-memory consolidation.

Only compact, structured evidence reaches the model.  Raw perception/reasoning
text and screenshots remain in the lossless run log.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence

from loop.log.schema import EpisodeLog, StepLog
from loop.memory.knowledge import EvidenceRef, KnowledgeHit, KnowledgeStore, MemoryCandidate


MEMORY_TYPES = ("mechanic", "strategy", "procedural_skill", "fact", "failure_lesson")
EVIDENCE_KINDS = (
    "action_effect", "screen_transition", "terminal", "sensor_verified_tutorial", "failure",
)

CONSOLIDATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string", "enum": list(MEMORY_TYPES)},
                    "canonical_text": {"type": "string", "minLength": 1},
                    "retrieval_text": {"type": "string", "minLength": 1},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "step_id": {"type": "integer", "minimum": 0},
                                "evidence_kind": {"type": "string", "enum": list(EVIDENCE_KINDS)},
                                "relation": {"type": "string", "enum": ["support", "contradiction"]},
                            },
                            "required": ["step_id", "evidence_kind", "relation"],
                        },
                    },
                    "related_memory_id": {"type": ["string", "null"]},
                    "relation": {"type": "string", "enum": ["support", "contradiction"]},
                },
                "required": [
                    "type", "canonical_text", "retrieval_text", "entities", "tags",
                    "confidence", "evidence_refs", "related_memory_id", "relation",
                ],
            },
        }
    },
    "required": ["candidates"],
}

CONSOLIDATION_SYSTEM = (
    "Extract only reusable game knowledge directly supported by the supplied structured evidence. "
    "Return JSON matching the schema. Return an empty candidates list when nothing is reusable. "
    "Never infer objects, controls, rules, or outcomes not present in evidence."
)


class ConsolidationGateway(Protocol):
    def complete_json(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class ConsolidationResult:
    candidates: int = 0
    rejections: int = 0
    inserts: int = 0
    merges: int = 0
    contradictions: int = 0
    embeddings_generated: int = 0
    embeddings_avoided: int = 0
    skipped: bool = False
    error: str | None = None

    def telemetry(self) -> dict[str, Any]:
        return asdict(self)


class EpisodeConsolidator:
    """Call the reasoner and store knowledge at most once per episode."""

    def __init__(self, gateway: ConsolidationGateway, store: KnowledgeStore) -> None:
        self.gateway = gateway
        self.store = store
        self._seen: set[tuple[str, str]] = set()

    def consolidate(
        self,
        *,
        run_ref: str,
        episode: EpisodeLog,
        steps: Sequence[StepLog],
        nearby: Sequence[KnowledgeHit] = (),
    ) -> ConsolidationResult:
        key = (episode.session_id, episode.episode_id)
        if key in self._seen:
            return ConsolidationResult(skipped=True)
        self._seen.add(key)
        try:
            evidence = build_evidence_package(run_ref=run_ref, episode=episode, steps=steps)
            if not evidence:
                return ConsolidationResult()
            supplied = [_hit_payload(hit) for hit in nearby[:3]]
            call = self.gateway.complete_json(
                system=CONSOLIDATION_SYSTEM,
                user=json.dumps(
                    {"episode": {"terminal": episode.terminal, "outcome": episode.outcome},
                     "evidence": evidence, "nearby_memories": supplied},
                    ensure_ascii=False, separators=(",", ":"),
                ),
                json_schema=CONSOLIDATION_SCHEMA,
                max_tokens=1200,
            )
            raw = getattr(call, "parsed", None)
            raw_candidates = raw.get("candidates") if isinstance(raw, Mapping) else None
            if not isinstance(raw_candidates, list):
                raise ValueError("consolidator returned no candidates array")
            candidates, rejected = _validated_candidates(
                raw_candidates, run_ref=run_ref, episode=episode, evidence=evidence, nearby=nearby,
            )
            report = self.store.remember_candidates(candidates) if candidates else None
            return ConsolidationResult(
                candidates=len(candidates), rejections=rejected,
                inserts=report.inserts if report else 0,
                merges=report.merges if report else 0,
                contradictions=report.contradictions if report else 0,
                embeddings_generated=report.embeddings_generated if report else 0,
                embeddings_avoided=report.embeddings_avoided if report else 0,
            )
        except Exception as exc:  # Episode data is already durable; learning is best-effort.
            return ConsolidationResult(error=f"{type(exc).__name__}:{str(exc)[:300]}")


def consolidate_episode(
    *, gateway: ConsolidationGateway, store: KnowledgeStore, run_ref: str,
    episode: EpisodeLog, steps: Sequence[StepLog], nearby: Sequence[KnowledgeHit] = (),
) -> ConsolidationResult:
    """One-shot convenience API; long-lived callers should reuse EpisodeConsolidator."""
    return EpisodeConsolidator(gateway, store).consolidate(
        run_ref=run_ref, episode=episode, steps=steps, nearby=nearby,
    )


_WEAK_EFFECTS = frozenset({"pixel_changed", "moved"})


def _control_ids(snapshot: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    for row in snapshot.get("controls") or ():
        if not isinstance(row, Mapping):
            continue
        value = str(row.get("control_id") or row.get("label") or "").strip()
        if value and value not in out:
            out.append(value)
    return out


def _overlays(snapshot: Mapping[str, Any]) -> list[str]:
    return [str(item).strip() for item in (snapshot.get("overlays") or ()) if str(item).strip()]


def _material_effect(step: StepLog, before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    if step.result.view_changed or step.result.prompt_cleared:
        return True
    transaction = step.result.card_transaction
    if transaction is not None and transaction.effect_verified:
        return True
    effects = [str(x) for x in step.result.observed_effects if str(x).strip() and str(x) not in _WEAK_EFFECTS]
    if effects:
        return True
    if before and after:
        if str(before.get("screen") or "") != str(after.get("screen") or ""):
            return True
        if _overlays(before) != _overlays(after):
            return True
        if _control_ids(before) != _control_ids(after):
            return True
    return False


def build_evidence_package(
    *, run_ref: str, episode: EpisodeLog, steps: Sequence[StepLog],
) -> list[dict[str, Any]]:
    """Select grounded evidence; intentionally omit raw prompts, thoughts, and images."""
    rows: list[dict[str, Any]] = []
    prior_screen = ""
    for index, step in enumerate(steps):
        screen = str(step.turn_snapshot.get("screen") or step.scene or "").strip().casefold()
        entities = _entities(step.turn_snapshot)
        common = {"step_id": step.step_id, "screen": screen, "entities": entities}
        effects = [
            str(x) for x in step.result.observed_effects
            if str(x).strip() and str(x) not in _WEAK_EFFECTS
        ]
        after = steps[index + 1].turn_snapshot if index + 1 < len(steps) else {}
        if step.result.execution_disposition == "dispatched" and _material_effect(
            step, step.turn_snapshot, after,
        ):
            rows.append({
                **common, "evidence_kind": "action_effect",
                "action": dict(step.intent) if step.intent else (step.action.name or step.action.derive_name()),
                "effects": effects,
                "before": {
                    "screen": str(step.turn_snapshot.get("screen") or ""),
                    "overlays": _overlays(step.turn_snapshot),
                    "controls": _control_ids(step.turn_snapshot),
                },
                "after": {
                    "screen": str(after.get("screen") or ""),
                    "overlays": _overlays(after),
                    "controls": _control_ids(after),
                } if after else {},
                "verification": {
                    "prompt_cleared": step.result.prompt_cleared,
                    "view_changed": step.result.view_changed,
                    "card_effect_verified": bool(
                        step.result.card_transaction and step.result.card_transaction.effect_verified
                    ),
                },
            })
        if prior_screen and screen and screen != prior_screen:
            rows.append({**common, "evidence_kind": "screen_transition", "from": prior_screen, "to": screen})
        if screen:
            prior_screen = screen
        if (
            screen == "tutorial" and step.prompt_on is True and step.prompt_text.strip()
            and str(step.prompt_source or "").strip()
        ):
            rows.append({
                **common, "evidence_kind": "sensor_verified_tutorial",
                "text": " ".join(step.prompt_text.split()),
            })
        source = classify_failure_source(step)
        if source != "unknown":
            rows.append({
                **common, "evidence_kind": "failure", "failure_source": source,
                "execution_disposition": step.result.execution_disposition,
                "error": str(step.result.error or "")[:200], "terminal": step.terminal,
            })
        if step.terminal != "alive":
            rows.append({**common, "evidence_kind": "terminal", "terminal": step.terminal})
    if steps and not any(row["evidence_kind"] == "terminal" for row in rows):
        last = steps[-1]
        rows.append({
            "step_id": last.step_id, "screen": str(last.turn_snapshot.get("screen") or last.scene or ""),
            "entities": _entities(last.turn_snapshot), "evidence_kind": "terminal",
            "terminal": episode.terminal,
        })
    return rows


def classify_failure_source(step: StepLog) -> str:
    """Attribute failure only from structured state, never reasoner prose."""
    if step.failed_while_thinking:
        return "environment"
    if step.epistemic_blockers or step.llm1_final_schema_valid is False:
        return "perception"
    route = f"{step.route_before_discovery} {step.route_after_discovery}".casefold()
    if any(word in route for word in ("unresolved", "ungrounded", "no_target", "blocked_ground")):
        return "grounding"
    transaction = step.result.card_transaction
    transaction_failed = transaction is not None and transaction.outcome not in {None, "success", "auto_resolved"}
    if step.result.execution_disposition in {"rejected_pre_dispatch", "partially_dispatched"} or step.result.error or transaction_failed:
        return "execution"
    if (
        step.result.execution_disposition == "dispatched" and step.terminal == "fail"
        and _legal_logged_intent(step.intent, step.turn_snapshot)
    ):
        return "policy"
    if step.terminal == "fail" or step.result.stall:
        return "environment"
    return "unknown"


def _validated_candidates(
    values: Sequence[Any], *, run_ref: str, episode: EpisodeLog,
    evidence: Sequence[Mapping[str, Any]], nearby: Sequence[KnowledgeHit],
) -> tuple[list[MemoryCandidate], int]:
    available = {(int(row["step_id"]), str(row["evidence_kind"])): row for row in evidence}
    nearby_by_id = {hit.memory_id: hit for hit in nearby[:3]}
    accepted: list[MemoryCandidate] = []
    rejected = 0
    for value in values:
        try:
            if not isinstance(value, Mapping) or value.get("type") not in MEMORY_TYPES:
                raise ValueError
            refs: list[EvidenceRef] = []
            source_rows: list[Mapping[str, Any]] = []
            for raw_ref in value.get("evidence_refs") or ():
                pair = (int(raw_ref["step_id"]), str(raw_ref["evidence_kind"]))
                row = available.get(pair)
                relation = str(raw_ref.get("relation") or "support")
                if row is None or relation not in {"support", "contradiction"}:
                    raise ValueError
                source_rows.append(row)
                refs.append(EvidenceRef(
                    run_ref=run_ref, session_id=episode.session_id, episode_id=episode.episode_id,
                    step_id=pair[0], evidence_kind=pair[1], relation=relation,
                ))
            if not refs:
                raise ValueError
            entities = tuple(dict.fromkeys(" ".join(str(x).split()) for x in value.get("entities") or () if str(x).strip()))
            grounded_entities = {str(x).casefold() for row in source_rows for x in row.get("entities") or ()}
            if any(entity.casefold() not in grounded_entities for entity in entities):
                raise ValueError
            memory_type = str(value["type"])
            sources = {str(row.get("failure_source")) for row in source_rows if row.get("evidence_kind") == "failure"}
            if memory_type in {"strategy", "procedural_skill", "failure_lesson"} and (
                sources and sources != {"policy"}
            ):
                raise ValueError
            if memory_type == "failure_lesson" and "policy" not in sources:
                raise ValueError
            related_id = str(value.get("related_memory_id") or "") or None
            related = nearby_by_id.get(related_id) if related_id else None
            relation = str(value.get("relation") or "support")
            if relation not in {"support", "contradiction"}:
                raise ValueError
            if relation == "contradiction" and related is None:
                raise ValueError
            if related_id and (
                related is None or related.type != memory_type
                or (entities and related.entities and not {x.casefold() for x in entities} & {x.casefold() for x in related.entities})
            ):
                raise ValueError
            accepted.append(MemoryCandidate(
                type=memory_type, canonical_text=str(value["canonical_text"]).strip(),
                retrieval_text=str(value["retrieval_text"]).strip(), entities=entities,
                tags=tuple(dict.fromkeys(str(x).strip() for x in value.get("tags") or () if str(x).strip())),
                confidence=float(value.get("confidence", 0.5)), evidence_refs=tuple(refs),
                failure_source="policy" if memory_type == "failure_lesson" else None,
                related_memory_id=related_id, relation=relation,
            ))
        except (KeyError, TypeError, ValueError):
            rejected += 1
    return accepted, rejected


def _entities(snapshot: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    for key in ("cards", "targets", "controls"):
        for row in snapshot.get(key) or ():
            if not isinstance(row, Mapping):
                continue
            for field in ("name", "label", "control_id"):
                value = str(row.get(field) or "").strip()
                if value and value.casefold() not in {item.casefold() for item in found}:
                    found.append(value)
                    break
    for overlay in snapshot.get("overlays") or ():
        value = str(overlay or "").strip()
        if value and value.casefold() not in {item.casefold() for item in found}:
            found.append(value)
    return found


def _legal_logged_intent(intent: Mapping[str, Any], snapshot: Mapping[str, Any]) -> bool:
    kind = str(intent.get("kind") or "")
    cards = {str(row.get("card_id")) for row in snapshot.get("cards") or () if isinstance(row, Mapping)}
    targets = {str(row.get("target_id")) for row in snapshot.get("targets") or () if isinstance(row, Mapping)}
    controls = {str(row.get("control_id")) for row in snapshot.get("controls") or () if isinstance(row, Mapping)}
    if kind == "play_card":
        return str(intent.get("card_id")) in cards and (
            intent.get("target_id") is None or str(intent.get("target_id")) in targets
        )
    if kind in {"end_turn", "click_control"}:
        return str(intent.get("control_id")) in controls
    if kind == "inspect":
        return str(intent.get("object_id")) in cards | targets | controls
    return False


def _hit_payload(hit: KnowledgeHit) -> dict[str, Any]:
    return {
        "memory_id": hit.memory_id, "type": hit.type, "text": hit.text,
        "entities": list(hit.entities),
    }
