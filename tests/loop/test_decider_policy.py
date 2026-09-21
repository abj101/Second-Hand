from __future__ import annotations

import math

import pytest

from loop.adaptive.candidates import CandidateAction, Directive
from loop.adaptive.decider_policy import (
    DeciderPolicy,
    build_decider_questions,
    build_decider_state,
    validate_system_one_answer,
)
from loop.adaptive.policy import DecisionError
from loop.adaptive.snapshot import EntityView, Provenanced, StateSnapshotV2


def _snapshot() -> StateSnapshotV2:
    return StateSnapshotV2(
        snapshot_id="snapshot-1",
        schema_revision_id="schema-1",
        episode_id="episode-1",
        episode_generation=4,
        step=7,
        frame_digest="must-not-leak",
        frame_history_digests=("must-not-leak",),
        captured_at=10.0,
        observation_age_s=0.01,
        mode="running",
        fields={
            "grounded": Provenanced(True, 0.9, False, ("pixels",)),
        },
        entities=(EntityView("avatar", "entity-1", {}, 0.8),),
        relations=(),
        recent_events=(),
        confidence=0.85,
    )


def _directive() -> Directive:
    return Directive(
        revision=3,
        episode_generation=4,
        intent="progress while surviving",
        ttl_s=30.0,
        published_at=9.0,
    )


def _candidates() -> tuple[CandidateAction, ...]:
    return (
        CandidateAction("right", "key:right", "Move toward the open path."),
        CandidateAction("left", "key:left", "Move away from the hazard."),
    )


def test_request_is_canonical_and_redacted() -> None:
    state = build_decider_state(_snapshot(), _directive())
    ordered, questions = build_decider_questions(_candidates())

    assert [candidate.action_id for candidate in ordered] == ["left", "right"]
    assert list(questions["action"]["criteria"]) == ["left", "right"]
    rendered = repr((state, questions))
    assert "must-not-leak" not in rendered
    assert "key:left" not in rendered
    assert "key:right" not in rendered
    assert "snapshot-1" not in rendered
    assert state["directive"] == {"intent": "progress while surviving"}


class _FakeDecider:
    def __init__(self) -> None:
        self.request = None

    def system_one(self, state, questions, *, independent, layout):
        self.request = (state, questions, independent, layout)
        return {
            "model": "decider-v10",
            "answers": {
                "action": {
                    "type": "choice",
                    "choice": "left",
                    "confidence": 0.7000,
                    "certainty": 0.4,
                    "probabilities": {"left": 0.7000, "right": 0.3000},
                }
            },
        }


def test_policy_uses_system_one_contract() -> None:
    model = _FakeDecider()
    policy = DeciderPolicy(model, catalog_revision="catalog-1")

    result = policy.score(_snapshot(), _directive(), _candidates())

    assert result.candidate_id == "left"
    assert result.confidence == pytest.approx(0.7)
    assert result.catalog_revision == "catalog-1"
    assert result.snapshot_id == "snapshot-1"
    assert model.request[2:] == (True, "state_first")


@pytest.mark.parametrize(
    "answer",
    [
        {"type": "choice", "choice": "other", "confidence": 1.0, "probabilities": {"left": 0.5, "right": 0.5}},
        {"type": "choice", "choice": "left", "confidence": 0.5, "probabilities": {"left": 0.5}},
        {"type": "choice", "choice": "left", "confidence": 0.7, "probabilities": {"left": 0.7, "right": 0.7}},
        {"type": "choice", "choice": "left", "confidence": 0.4, "probabilities": {"left": 0.4, "right": 0.6}},
        {"type": "choice", "choice": "left", "confidence": math.nan, "probabilities": {"left": math.nan, "right": 0.0}},
    ],
)
def test_invalid_answers_fail_closed(answer: dict[str, object]) -> None:
    with pytest.raises(DecisionError):
        validate_system_one_answer(
            {"answers": {"action": answer}},
            ("left", "right"),
        )


def test_decider_is_not_called_for_invalid_candidate_count() -> None:
    with pytest.raises(DecisionError, match="2..255"):
        build_decider_questions((_candidates()[0],))
