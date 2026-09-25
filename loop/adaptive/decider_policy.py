"""Local Decider policy with a redacted SystemOne request contract."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, Protocol

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.policy import DecisionError, ReactiveState, StructuredDecision, coerce_reactive
from loop.adaptive.snapshot import StateSnapshotV2, assert_no_privileged


DECIDER_SOURCE_REVISION = "c4daaac28af9fea95d627015cffa2dd5a5926ee6"
DECIDER_08B_MODEL_ID = "Mapika/decider-0.8b"
DECIDER_08B_MODEL_REVISION = "1ea54127d3bd52f6d753d9257b32a6380b873907"
DECIDER_2B_MODEL_ID = "Mapika/decider-2b"
DECIDER_2B_MODEL_REVISION = "b37f7e1ba3fbc9238004cf531fabbee2619973fd"

_QUESTION = "Which currently valid action best advances the directive from this observation?"


def scoring_policy_source(policy: object) -> str:
    """Telemetry label for the policy object that scored (never a fallback alias)."""
    return type(policy).__name__


class SystemOne(Protocol):
    def system_one(
        self,
        state: object,
        questions: dict[str, object],
        *,
        independent: bool,
        layout: str,
    ) -> Mapping[str, object]: ...


def build_decider_state(
    snapshot: StateSnapshotV2 | ReactiveState,
    directive: Directive,
) -> dict[str, object]:
    """Return model-visible semantics, excluding control and dispatch metadata."""

    reactive = coerce_reactive(snapshot)
    observation: dict[str, object] = {
        "mode": reactive.mode if reactive.mode is not None else "unknown",
        "confidence": round(reactive.confidence, 4),
        "fields": {
            name: {
                "value": field.value,
                "confidence": round(field.confidence, 4),
                "unknown": field.unknown,
            }
            for name, field in sorted(reactive.fields.items())
        },
        "entities": [
            {
                "id": entity.stable_id,
                "type": entity.type,
                "confidence": round(entity.confidence, 4),
            }
            for entity in sorted(reactive.entities, key=lambda item: item.stable_id)
        ],
        "relations": [
            {
                "source": relation.src,
                "relation": relation.kind,
                "target": relation.dst,
                "confidence": round(relation.confidence, 4),
            }
            for relation in sorted(
                reactive.relations,
                key=lambda item: (item.src, item.kind, item.dst),
            )
        ],
    }
    if reactive.prior_verified_outcome is not None:
        observation["prior_verified_outcome"] = dict(reactive.prior_verified_outcome)
    state: dict[str, object] = {
        "directive": {"intent": directive.intent, "revision": directive.revision},
        "observation": observation,
    }
    assert_no_privileged(state)
    return state


def build_decider_questions(
    candidates: tuple[CandidateAction, ...],
) -> tuple[tuple[CandidateAction, ...], dict[str, object]]:
    ordered = tuple(sorted(candidates, key=lambda item: item.action_id))
    if not 2 <= len(ordered) <= 255:
        raise DecisionError("Decider requires 2..255 candidates")
    ids = [candidate.action_id for candidate in ordered]
    if len(ids) != len(set(ids)) or any(not action_id for action_id in ids):
        raise DecisionError("candidate IDs must be non-empty and unique")
    if any(not candidate.description.strip() for candidate in ordered):
        raise DecisionError("candidate descriptions must be non-empty")
    return ordered, {
        "action": {
            "type": "choice",
            "instructions": _QUESTION,
            "criteria": {
                candidate.action_id: candidate.description for candidate in ordered
            },
        }
    }


def _finite_probability(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise DecisionError(f"{name} must be finite and in [0, 1]")
    return result


def validate_system_one_answer(
    response: Mapping[str, object],
    candidate_ids: tuple[str, ...],
) -> tuple[str, float, dict[str, float]]:
    answers = response.get("answers")
    if not isinstance(answers, Mapping):
        raise DecisionError("Decider response is missing answers")
    answer = answers.get("action")
    if not isinstance(answer, Mapping) or answer.get("type") != "choice":
        raise DecisionError("Decider action answer must be a choice")

    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in candidate_ids:
        raise DecisionError("Decider choice was not offered")
    raw_probabilities = answer.get("probabilities")
    if not isinstance(raw_probabilities, Mapping):
        raise DecisionError("Decider probabilities are missing")
    if set(raw_probabilities) != set(candidate_ids):
        raise DecisionError("Decider probability keys do not match candidates")

    probabilities = {
        action_id: _finite_probability(
            raw_probabilities[action_id], name=f"probability[{action_id!r}]"
        )
        for action_id in candidate_ids
    }
    tolerance = max(1e-3, len(candidate_ids) * 5e-5 + 1e-6)
    total = sum(probabilities.values())
    if abs(total - 1.0) > tolerance:
        raise DecisionError("Decider probabilities do not sum to one")

    confidence = _finite_probability(answer.get("confidence"), name="confidence")
    if abs(confidence - probabilities[choice]) > tolerance:
        raise DecisionError("Decider confidence does not match its choice")
    maximum = max(probabilities.values())
    if maximum - probabilities[choice] > tolerance:
        raise DecisionError("Decider choice is not a maximum-probability candidate")
    if "certainty" in answer:
        _finite_probability(answer["certainty"], name="certainty")

    normalized = {key: value / total for key, value in probabilities.items()}
    return choice, normalized[choice], normalized


class DeciderPolicy:
    """Synchronous scoring unit intended to run inside the policy worker."""

    def __init__(self, model: SystemOne, *, catalog_revision: str = "") -> None:
        self._model = model
        self.catalog_revision = catalog_revision

    @classmethod
    def from_huggingface(
        cls,
        *,
        model_id: str = DECIDER_08B_MODEL_ID,
        revision: str = DECIDER_08B_MODEL_REVISION,
        catalog_revision: str = "",
        use_graphs: bool | None = None,
    ) -> "DeciderPolicy":
        """Materialize an immutable model revision, then load it once on CUDA."""

        try:
            import torch
            from decider.infer import Decider
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - exercised by the optional GPU profile
            raise RuntimeError(
                "Install the native Decider profile with scripts/setup_native_decider.ps1"
            ) from exc
        if not torch.cuda.is_available():
            raise RuntimeError("Decider requires the native CUDA PyTorch profile")
        model_path = snapshot_download(model_id, revision=revision)
        model = Decider(
            model_path,
            device="cuda",
            dtype=torch.bfloat16,
            use_graphs=use_graphs,
        )
        return cls(model, catalog_revision=catalog_revision)

    def score(
        self,
        snapshot: StateSnapshotV2 | ReactiveState,
        directive: Directive,
        candidates: tuple[CandidateAction, ...],
    ) -> StructuredDecision:
        ordered, questions = build_decider_questions(candidates)
        reactive = coerce_reactive(snapshot)
        state = build_decider_state(reactive, directive)
        response = self._model.system_one(
            state,
            questions,
            independent=True,
            layout="state_first",
        )
        if not isinstance(response, Mapping):
            raise DecisionError("Decider response must be an object")
        candidate_ids = tuple(candidate.action_id for candidate in ordered)
        choice, confidence, probabilities = validate_system_one_answer(
            response, candidate_ids
        )
        return StructuredDecision(
            candidate_id=choice,
            confidence=confidence,
            probabilities=probabilities,
            decided_at=time.monotonic(),
            episode_generation=reactive.episode_generation,
            snapshot_id=reactive.snapshot_id,
            directive_revision=directive.revision,
            catalog_revision=self.catalog_revision,
        )
