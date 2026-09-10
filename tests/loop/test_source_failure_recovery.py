from __future__ import annotations

import pytest
from types import SimpleNamespace

from loop.agent.decision import BoundingBox, ControlSnapshot, TurnSnapshot
from loop.agent.loop import CollectAgent
from loop.agent.recovery import (
    RecoveryAction,
    SourceFailureRecovery,
    source_failure_fingerprint,
)


def _failure(*, mark_id: int, chord: str, target_id: str, x0: float = 0.30) -> dict[str, object]:
    return {
        "code": "source-unconfirmed",
        "screen": "combat",
        "evidence": {"source": {"label": "Strike", "role": "card", "bbox": {
            "x0": x0, "y0": 0.80, "x1": x0 + 0.20, "y1": 0.92,
        }}},
        "source": {"mark_id": mark_id, "chord": chord, "object_id": f"card-{mark_id}"},
        "target_id": target_id,
    }


def test_source_failure_fingerprint_ignores_volatile_ids_chords_and_targets() -> None:
    first = _failure(mark_id=3, chord="card-3->enemy-1", target_id="enemy-1")
    second = _failure(mark_id=17, chord="mk17->enemy-9", target_id="enemy-9")

    assert source_failure_fingerprint(first) == source_failure_fingerprint(second)


def test_source_failure_fingerprint_includes_screen_and_source_evidence_bbox() -> None:
    base = _failure(mark_id=3, chord="a", target_id="enemy-1")
    assert source_failure_fingerprint(base) != source_failure_fingerprint(
        {**base, "screen": "map"}
    )
    assert source_failure_fingerprint(base) != source_failure_fingerprint(
        _failure(mark_id=3, chord="a", target_id="enemy-1", x0=0.42)
    )


def test_first_matching_source_failure_requests_clean_rebuild_and_quarantine() -> None:
    recovery = SourceFailureRecovery()
    result = recovery.observe(_failure(mark_id=3, chord="a", target_id="enemy-1"))

    assert result.action is RecoveryAction.REBUILD
    assert result.quarantine is True
    assert result.fingerprint


def test_second_matching_source_failure_after_rebuild_returns_typed_end_turn() -> None:
    recovery = SourceFailureRecovery()
    failure = _failure(mark_id=3, chord="a", target_id="enemy-1")
    recovery.observe(failure)
    recovery.mark_rebuilt()

    result = recovery.observe(failure, end_turn={"kind": "end_turn", "control_id": "control:end_turn"})

    assert result.action is RecoveryAction.END_TURN
    assert result.intent == {"kind": "end_turn", "control_id": "control:end_turn"}


def test_matching_source_failure_never_uses_end_turn_behind_overlay() -> None:
    recovery = SourceFailureRecovery()
    failure = _failure(mark_id=3, chord="a", target_id="enemy-1")
    recovery.observe(failure)
    recovery.mark_rebuilt()

    result = recovery.observe(
        failure,
        overlay_active=True,
        end_turn={"kind": "end_turn", "control_id": "control:end_turn"},
    )

    assert result.action is RecoveryAction.SOFTLOCK
    assert result.intent is None
    assert result.reason == "overlay-blocks-end-turn-fallback"


def test_matching_source_failure_without_end_turn_is_explicit_softlock() -> None:
    recovery = SourceFailureRecovery()
    failure = _failure(mark_id=3, chord="a", target_id="enemy-1")
    recovery.observe(failure)
    recovery.mark_rebuilt()

    result = recovery.observe(failure, end_turn=None)

    assert result.action is RecoveryAction.SOFTLOCK
    assert result.intent is None
    assert result.reason == "no-end-turn-fallback"


def test_different_source_failure_starts_a_new_rebuild_budget() -> None:
    recovery = SourceFailureRecovery()
    recovery.observe(_failure(mark_id=3, chord="a", target_id="enemy-1"))
    recovery.mark_rebuilt()

    result = recovery.observe(_failure(mark_id=4, chord="b", target_id="enemy-1", x0=0.42))

    assert result.action is RecoveryAction.REBUILD


def test_collect_agent_rebuilds_once_then_binds_published_end_turn(monkeypatch) -> None:
    agent = CollectAgent.__new__(CollectAgent)
    agent._source_failure_recovery = SourceFailureRecovery()
    agent._last_execution_failure = _failure(mark_id=3, chord="a", target_id="enemy-1")
    agent._unchanged_failure_count = 2
    agent._session_flags = []
    agent._decision_softlock = False
    agent._screen_objects = None
    agent._v2_control_objects = {
        "control:end_turn": SimpleNamespace(
            object_id="control:end_turn", mark_id=9, role="button",
            label="End Turn", x=.90, y=.82,
        )
    }
    rebuilds = {"count": 0}
    monkeypatch.setattr(agent, "_overlay_blocking_now", lambda: False)
    monkeypatch.setattr(
        agent, "_rebuild_after_source_failure",
        lambda: rebuilds.__setitem__("count", rebuilds["count"] + 1),
    )
    monkeypatch.setattr(agent, "_log_combat_decision", lambda _parsed: None)
    snapshot = TurnSnapshot(
        snapshot_id="snap-1", screen="combat", turn="ours",
        hp=70, block=0, energy=3,
        controls=(ControlSnapshot(
            "control:end_turn", "End Turn", BoundingBox(.85, .78, .95, .88),
        ),),
        legal_intents=("end_turn",),
    )
    legal = ({"kind": "end_turn", "control_ids": ("control:end_turn",)},)

    assert agent._handle_source_failure_recovery(snapshot, legal) == {}
    assert rebuilds["count"] == 1
    assert agent._last_execution_failure is None

    # The retry may have a different mark/chord/target id. Its stable source
    # evidence is unchanged, so ACT is bypassed in favor of the typed control.
    agent._last_execution_failure = _failure(mark_id=19, chord="b", target_id="enemy-7")
    parsed = agent._handle_source_failure_recovery(snapshot, legal)

    assert parsed == {"action": "end_turn", "choice": "End Turn"}
    assert rebuilds["count"] == 1
    assert agent._last_intent == {"kind": "end_turn", "control_id": "control:end_turn"}
    assert agent._last_execution_plan["control_id"] == "control:end_turn"
