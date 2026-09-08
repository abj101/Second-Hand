from __future__ import annotations

import pytest

from loop.agent.decision import (
    ACT_FALLBACK_MODEL,
    ACT_PRIMARY_MODEL,
    ActionIntentError,
    ActReasoner,
    ActReasonerError,
    BoundingBox,
    CardSnapshot,
    TurnSnapshot,
    action_intent_from_dict,
    compact_act_prompt,
)
from loop.model.gateway import ModelCall


def _snapshot() -> TurnSnapshot:
    return TurnSnapshot(
        snapshot_id="frame-9", screen="combat", turn="ours", hp=80, block=0, energy=3,
        cards=(CardSnapshot("card-strike", "Strike", BoundingBox(10, 20, 30, 40), True, cost=1),),
    )


def test_compact_prompt_exposes_stable_ids_but_no_geometry_or_legacy_fields() -> None:
    prompt = compact_act_prompt(_snapshot())
    assert "card-strike" in prompt
    assert "bbox" not in prompt
    assert '"mark"' not in prompt
    assert '"gx"' not in prompt
    assert '"thought"' not in prompt
    assert '"tool"' not in prompt


def test_snapshot_defensively_freezes_execution_result() -> None:
    result = {"code": "effect-unverified"}
    snapshot = TurnSnapshot("s", "combat", "ours", 80, 0, 3, last_execution=result)
    result["code"] = "mutated"
    assert snapshot.last_execution == {"code": "effect-unverified"}
    with pytest.raises(TypeError):
        assert snapshot.last_execution is not None
        snapshot.last_execution["code"] = "nope"  # type: ignore[index]


@pytest.mark.parametrize("payload", [
    {"kind": "play_card", "card_id": "card-strike", "gx": 500},
    {"kind": "play_card", "card_id": ""},
    {"kind": "play_card", "card_id": "card-strike", "target_id": 2},
    {"kind": "play_card", "card_id": "card-strike", "target_id": ""},
    {"kind": "end_turn", "control_id": "end", "note": "nope"},
])
def test_intent_parser_rejects_non_contract_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ActionIntentError):
        action_intent_from_dict(payload)


def test_reasoner_uses_one_text_only_primary_call() -> None:
    calls: list[tuple[dict[str, object], dict[str, object]]] = []

    class FakeGateway:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def complete_text(self, **kwargs: object) -> ModelCall:
            calls.append((self.kwargs, kwargs))
            return ModelCall('{"kind":"play_card","card_id":"card-strike"}',
                             {"kind": "play_card", "card_id": "card-strike"}, 12.0, "fake")

    result = ActReasoner(gateway_factory=FakeGateway).decide(_snapshot())
    assert result.intent.card_id == "card-strike"  # type: ignore[union-attr]
    assert result.backend == ACT_PRIMARY_MODEL
    assert result.fallback_reason is None
    assert result.metrics["image_supplied"] is False
    assert len(calls) == 1
    init, call = calls[0]
    assert init == {"model": ACT_PRIMARY_MODEL, "temperature": 0.1, "max_tokens": 96, "num_ctx": 2048}
    assert call["timeout_s"] == 8.0
    assert "frames" not in call
    assert "response_schema" in str(call["user"])


def test_reasoner_falls_back_once_after_malformed_primary_json() -> None:
    models: list[str] = []
    calls: list[dict[str, object]] = []

    class FakeGateway:
        def __init__(self, **kwargs: object) -> None:
            self.model = str(kwargs["model"])
            models.append(self.model)

        def complete_text(self, **kwargs: object) -> ModelCall:
            calls.append(kwargs)
            if self.model == ACT_PRIMARY_MODEL:
                return ModelCall("not json", None, 1.0, self.model)
            return ModelCall('{"kind":"inspect","object_id":"card-strike"}',
                             {"kind": "inspect", "object_id": "card-strike"}, 2.0, self.model)

    result = ActReasoner(gateway_factory=FakeGateway).decide(_snapshot())
    assert models == [ACT_PRIMARY_MODEL, ACT_FALLBACK_MODEL]
    assert result.backend == ACT_FALLBACK_MODEL
    assert result.fallback_reason == "malformed-json"
    assert [call["timeout_s"] for call in calls] == [8.0, 15.0]


def test_reasoner_falls_back_once_after_http_failure() -> None:
    models: list[str] = []

    class FakeGateway:
        def __init__(self, **kwargs: object) -> None:
            self.model = str(kwargs["model"])
            models.append(self.model)

        def complete_text(self, **kwargs: object) -> ModelCall:
            if self.model == ACT_PRIMARY_MODEL:
                raise RuntimeError("Ollama 402")
            return ModelCall('{"kind":"inspect","object_id":"card-strike"}',
                             {"kind": "inspect", "object_id": "card-strike"}, 2.0, self.model)

    result = ActReasoner(gateway_factory=FakeGateway).decide(_snapshot())
    assert models == [ACT_PRIMARY_MODEL, ACT_FALLBACK_MODEL]
    assert result.fallback_reason.startswith("RuntimeError:Ollama 402")


def test_reasoner_falls_back_once_after_invented_primary_id() -> None:
    models: list[str] = []

    class FakeGateway:
        def __init__(self, **kwargs: object) -> None:
            self.model = str(kwargs["model"])
            models.append(self.model)

        def complete_text(self, **kwargs: object) -> ModelCall:
            del kwargs
            if self.model == ACT_PRIMARY_MODEL:
                return ModelCall(
                    '{"kind":"play_card","card_id":"invented-card"}',
                    {"kind": "play_card", "card_id": "invented-card"},
                    1.0,
                    self.model,
                )
            return ModelCall(
                '{"kind":"play_card","card_id":"card-strike"}',
                {"kind": "play_card", "card_id": "card-strike"},
                2.0,
                self.model,
            )

    result = ActReasoner(gateway_factory=FakeGateway).decide(_snapshot())
    assert models == [ACT_PRIMARY_MODEL, ACT_FALLBACK_MODEL]
    assert result.intent.card_id == "card-strike"  # type: ignore[union-attr]
    assert result.fallback_reason == "invalid-act-intent:unknown-card-id"


def test_reasoner_rejects_invalid_primary_kind_then_uses_one_fallback() -> None:
    models: list[str] = []

    class FakeGateway:
        def __init__(self, **kwargs: object) -> None:
            self.model = str(kwargs["model"])
            models.append(self.model)

        def complete_text(self, **kwargs: object) -> ModelCall:
            del kwargs
            if self.model == ACT_PRIMARY_MODEL:
                return ModelCall(
                    '{"kind":"teleport","object_id":"card-strike"}',
                    {"kind": "teleport", "object_id": "card-strike"},
                    1.0,
                    self.model,
                )
            return ModelCall(
                '{"kind":"inspect","object_id":"card-strike"}',
                {"kind": "inspect", "object_id": "card-strike"},
                2.0,
                self.model,
            )

    result = ActReasoner(gateway_factory=FakeGateway).decide(_snapshot())
    assert models == [ACT_PRIMARY_MODEL, ACT_FALLBACK_MODEL]
    assert result.intent.object_id == "card-strike"  # type: ignore[union-attr]
    assert result.fallback_reason == "unknown-action-kind"


def test_reasoner_reports_invalid_act_intent_after_invalid_fallback() -> None:
    calls = 0

    class FakeGateway:
        def __init__(self, **kwargs: object) -> None:
            del kwargs

        def complete_text(self, **kwargs: object) -> ModelCall:
            nonlocal calls
            del kwargs
            calls += 1
            return ModelCall(
                '{"kind":"click_control","control_id":"invented"}',
                {"kind": "click_control", "control_id": "invented"},
                1.0,
                "fake",
            )

    with pytest.raises(ActReasonerError) as caught:
        ActReasoner(gateway_factory=FakeGateway).decide(_snapshot())
    assert str(caught.value) == "invalid-act-intent"
    assert getattr(caught.value, "fallback_error") == "invalid-act-intent:unknown-control-id"
    assert calls == 2


def test_empty_snapshot_skips_gateway_entirely() -> None:
    constructions = 0

    class FailIfConstructed:
        def __init__(self, **kwargs: object) -> None:
            nonlocal constructions
            del kwargs
            constructions += 1
            raise AssertionError("gateway must not be constructed for an empty action surface")

    empty = TurnSnapshot(
        snapshot_id="empty",
        screen="select",
        turn=None,
        hp=None,
        block=None,
        energy=None,
    )
    with pytest.raises(ActReasonerError) as caught:
        ActReasoner(gateway_factory=FailIfConstructed).decide(empty)
    assert str(caught.value) == "no-legal-intents"
    assert getattr(caught.value, "primary_error") == "no-legal-intents"
    assert constructions == 0


def test_unplayable_card_is_rejected_and_omitted_from_legal_ids() -> None:
    from loop.agent.decision import (
        PlayCardIntent,
        TargetSnapshot,
        legal_intents_for_snapshot,
        validate_action_intent,
    )

    snapshot = TurnSnapshot(
        snapshot_id="s",
        screen="combat",
        turn="ours",
        hp=80,
        block=0,
        energy=3,
        cards=(
            CardSnapshot("card_1", "Strike", BoundingBox(0.25, 0.85, 0.35, 0.95), False, cost=1),
            CardSnapshot("card_2", "Defend", BoundingBox(0.40, 0.85, 0.50, 0.95), True, cost=1),
        ),
        targets=(TargetSnapshot("enemy_1", BoundingBox(0.70, 0.50, 0.80, 0.70), "Jaw Worm"),),
    )
    legal = legal_intents_for_snapshot(snapshot)
    play = next(row for row in legal if row["kind"] == "play_card")
    assert "card_1" not in play["card_ids"]
    assert "card_2" in play["card_ids"]
    with pytest.raises(ActionIntentError, match="card-not-playable"):
        validate_action_intent(
            snapshot,
            PlayCardIntent(card_id="card_1", target_id="enemy_1"),
        )


def test_unknown_blocked_target_id_is_rejected() -> None:
    from loop.agent.decision import (
        PlayCardIntent,
        TargetSnapshot,
        legal_intents_for_snapshot,
        validate_action_intent,
    )

    snapshot = TurnSnapshot(
        snapshot_id="s",
        screen="combat",
        turn="ours",
        hp=80,
        block=0,
        energy=3,
        cards=(CardSnapshot("card_1", "Strike", BoundingBox(0.25, 0.85, 0.35, 0.95), True, cost=1),),
        targets=(TargetSnapshot("enemy_1", BoundingBox(0.70, 0.50, 0.80, 0.70), "Nibbit"),),
    )
    legal = legal_intents_for_snapshot(snapshot)
    play = next(row for row in legal if row["kind"] == "play_card")
    assert "enemy_2" not in play["target_ids"]
    with pytest.raises(ActionIntentError, match="unknown-target-id"):
        validate_action_intent(snapshot, PlayCardIntent(card_id="card_1", target_id="enemy_2"))


def _empty_surface_agent(screen: str):
    """CollectAgent stub whose action surface publishes nothing at all."""
    from unittest.mock import Mock

    from loop.agent.loop import CollectAgent

    agent = CollectAgent.__new__(CollectAgent)
    agent._action_surface = None
    agent.gateway = Mock()
    agent.perceive_gateway = None
    agent._last_obs_image = None
    agent._last_execution_failure = None
    agent._last_reason_state_key = ""
    agent._unchanged_failure_count = 0
    agent._empty_action_surface_key = ""
    agent._empty_action_surface_count = 0
    agent._loot_surface_recoveries = 0
    agent._loot_surface_screen = ""
    agent._decision_softlock = False
    agent._session_flags = []
    agent._pending_exec_error = None
    agent._pending_inventory_rescan = False
    agent._force_perceive = False
    agent._perception_cache = Mock()
    agent._blocked_controls = {"control:12_gold"}
    agent._blocked_controls_screen = screen
    agent._host_stats = {"screen": screen}
    agent._obs_metrics = {"screen": screen}
    agent._scene_desc = None
    agent._last_frame_mad = 0.0
    agent._turn_transition = False
    return agent


def _loot_snapshot(screen: str) -> TurnSnapshot:
    return TurnSnapshot(
        snapshot_id="loot", screen=screen, turn="ours", hp=80, block=0, energy=None,
    )


@pytest.mark.parametrize("screen", ["reward", "map"])
def test_empty_loot_surface_rescans_instead_of_softlocking(screen: str) -> None:
    """20260908T194352Z steps 14/17/18: reward went quiet and burned the budget."""
    agent = _empty_surface_agent(screen)
    snapshot = _loot_snapshot(screen)

    assert agent._run_v2_act(snapshot) == ({}, 0.0)
    assert agent._pending_exec_error == "empty-action-surface"
    assert agent._decision_softlock is False

    agent._pending_exec_error = None
    assert agent._run_v2_act(snapshot) == ({}, 0.0)
    assert agent._decision_softlock is False
    assert agent._pending_exec_error == "loot-surface-stall"
    # The no-op click blocked its own id; a stalled loot screen gets it back.
    assert agent._blocked_controls == set()
    assert agent._force_perceive is True
    assert agent._pending_inventory_rescan is True
    assert agent._loot_surface_recoveries == 1
    agent.gateway.assert_not_called()


def test_loot_surface_recovery_is_bounded() -> None:
    from loop.agent.loop import LOOT_SURFACE_RECOVERY_LIMIT

    agent = _empty_surface_agent("reward")
    snapshot = _loot_snapshot("reward")
    for _ in range(2 * (LOOT_SURFACE_RECOVERY_LIMIT + 2)):
        agent._run_v2_act(snapshot)
        if agent._decision_softlock:
            break
    assert agent._decision_softlock is True
    assert agent._loot_surface_recoveries == LOOT_SURFACE_RECOVERY_LIMIT
    assert "decision_softlock" in agent._session_flags


def test_empty_combat_surface_still_softlocks() -> None:
    """Recovery is loot-only; combat keeps the Batch 6 behaviour."""
    agent = _empty_surface_agent("combat")
    snapshot = _loot_snapshot("combat")

    agent._run_v2_act(snapshot)
    agent._pending_exec_error = None
    agent._run_v2_act(snapshot)
    assert agent._decision_softlock is True
    assert agent._pending_exec_error == "empty-action-surface"
