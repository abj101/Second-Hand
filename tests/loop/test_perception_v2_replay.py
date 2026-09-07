from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from loop.agent.decision import legal_intents_for_snapshot
from loop.agent.loop import CollectAgent
from loop.agent.perception_cache import PerceptionCache
from loop.fixtures.sts2 import Sts2Fixture
from loop.memory.grounded import export_uncertain_reason
from loop.memory.overlay_state import OverlayState
from loop.memory.screen_objects import ScreenObjectStore
from loop.os.aim import AimGuide, AimMark, ground_spatial_labels
from loop.os.frame_model import build_action_surface
from loop.log.schema import StepLog, Observation, StepResult, ActionLog


FIXTURES = Path(__file__).resolve().parents[2] / "loop" / "fixtures" / "sts2_screens"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _mark_from_row(row: dict) -> AimMark:
    bbox = row.get("bbox") or [row["x"] - 0.06, row["y"] - 0.05, row["x"] + 0.06, row["y"] + 0.05]
    return AimMark(
        id=int(row["id"]),
        x=float(row["x"]),
        y=float(row["y"]),
        x0=float(bbox[0]),
        y0=float(bbox[1]),
        x1=float(bbox[2]),
        y1=float(bbox[3]),
        label=str(row.get("label") or ""),
        role=str(row.get("role") or ""),
        object_id=str(row.get("object_id") or ""),
        source=str(row.get("source") or "test"),
        confidence=float(row.get("confidence") or 0.9),
    )


def _agent_for_combat(tmp_path: Path, marks: list[AimMark], host: dict) -> CollectAgent:
    image = Image.new("RGB", (128, 96), (40, 40, 40))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "replay"
    agent._step_id = 4
    agent._host_stats = dict(host)
    agent._obs_metrics = dict(host)
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, marks)
    agent._screen_objects = ScreenObjectStore(tmp_path / "objects.db")
    agent._action_surface = None
    agent._frame_model = None
    agent._game_state = None
    agent._entity_tracker = None
    agent._reason_controls = []
    agent._last_execution_failure = None
    agent._v2_control_objects = {}
    agent._last_ocr_words = []
    agent._overlay_state = OverlayState()
    agent._perception_cache = PerceptionCache()
    agent.gateway = None
    agent.perceive_gateway = None
    agent._force_perceive = False
    agent._pending_inventory_rescan = False
    agent._empty_action_surface_key = ""
    agent._empty_action_surface_count = 0
    agent._decision_softlock = False
    agent._session_flags = []
    agent._last_frame_mad = 0.0
    agent._turn_transition = False
    agent._pending_exec_error = None
    return agent


def _scene_from_llm1(payload: dict) -> SimpleNamespace:
    llm1 = payload["llm1"]
    return SimpleNamespace(
        screen=llm1.get("screen") or "combat",
        tutorial=llm1.get("tutorial") or {},
        turn=llm1.get("turn") or "ours",
        controls=list(llm1.get("controls") or []),
        hand_cards=list(llm1.get("hand_cards") or []),
        enemies=list(llm1.get("enemies") or []),
        characters=list(llm1.get("characters") or []),
        informational_text=lambda: [],
    )


def test_replay_overlay_leftover_nav_does_not_paginate_combat() -> None:
    data = _load("combat_inventory_failure.json")
    marks = [_mark_from_row(row) for row in data["aim_marks"]]
    surface = build_action_surface(
        marks,
        run_id="replay",
        step_id=4,
        screen="combat",
        activity="combat",
        host_stats=data["host_stats"],
    )
    assert surface.context.layout != "paginated_overlay"
    assert surface.context.allows_domain_entities is True


def test_replay_191012z_combat_inventory_reaches_act(tmp_path: Path) -> None:
    data = _load("combat_inventory_failure.json")
    marks = [_mark_from_row(row) for row in data["aim_marks"]]
    agent = _agent_for_combat(tmp_path, marks, data["host_stats"])
    scene = _scene_from_llm1(data)
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        names = [card.name for card in snapshot.cards]
        assert names == data["truth"]["card_names"]
        assert any("end turn" in control.label.lower().replace("_", " ") for control in snapshot.controls)
        assert len(snapshot.targets) >= 1
        kinds = {row["kind"] for row in legal_intents_for_snapshot(snapshot)}
        assert "play_card" in kinds
        assert "end_turn" in kinds
        assert "inspect" in kinds
        target = snapshot.targets[0]
        assert not target.target_id.startswith("unknown_character")
        assert (target.identity_confidence or 0) < 0.75
        assert (target.name or "").lower() in {"unknown enemy", "unknown_enemy"}
    finally:
        agent._screen_objects.close()


def test_llm1_hand_cards_promote_unlabeled_hand_marks(tmp_path: Path) -> None:
    image = Image.new("RGB", (64, 48))
    marks = [
        AimMark(id=1, x=0.31, y=0.92, x0=0.25, y0=0.86, x1=0.37, y1=1.0, label="", role="unknown"),
        AimMark(id=2, x=0.50, y=0.92, x0=0.44, y0=0.86, x1=0.56, y1=1.0, label="icon", role="icon"),
        AimMark(id=3, x=0.89, y=0.82, x0=0.82, y0=0.78, x1=0.96, y1=0.88, label="End Turn", role="button"),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat"})
    scene = SimpleNamespace(
        screen="combat",
        controls=[{"label": "End Turn", "role": "button"}],
        hand_cards=[
            {"label": "Defend", "role": "card", "bbox": [250, 860, 370, 1000]},
            {"label": "Strike", "role": "card", "bbox": [440, 860, 560, 1000]},
        ],
        enemies=[],
        characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        snapshot = agent._build_turn_snapshot(scene)
        assert [card.name for card in snapshot.cards] == ["Defend", "Strike"]
        assert all(card.card_id.startswith("card_") for card in snapshot.cards)
        assert all("llm1-current-frame" in (obj.provenance or "") for obj in surface.objects if obj.role == "card")
    finally:
        agent._screen_objects.close()


def test_llm1_cannot_promote_button_to_card(tmp_path: Path) -> None:
    marks = [
        AimMark(id=1, x=0.50, y=0.92, x0=0.44, y0=0.86, x1=0.56, y1=1.0, label="Skip", role="button"),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat"})
    scene = SimpleNamespace(
        screen="combat",
        controls=[],
        hand_cards=[{"label": "Strike", "role": "card", "bbox": [440, 860, 560, 1000]}],
        enemies=[],
        characters=[],
    )
    try:
        reconciled = agent._reconcile_scene_marks(scene)
        assert [(mark.label, mark.role) for mark in reconciled] == [("Skip", "button")]
    finally:
        agent._screen_objects.close()


def test_anonymous_enemy_becomes_low_confidence_target_with_inspect(tmp_path: Path) -> None:
    marks = [
        AimMark(id=1, x=0.40, y=0.92, x0=0.34, y0=0.86, x1=0.46, y1=1.0, label="Strike", role="card"),
        AimMark(id=2, x=0.73, y=0.58, x0=0.65, y0=0.50, x1=0.81, y1=0.70, label="unknown_character_1", role="unknown"),
        AimMark(id=3, x=0.89, y=0.82, x0=0.82, y0=0.78, x1=0.96, y1=0.88, label="End Turn", role="button"),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat", "turn": "ours"})
    scene = SimpleNamespace(
        screen="combat",
        controls=[{"label": "End Turn", "role": "button"}],
        hand_cards=[{"label": "Strike", "role": "card", "bbox": [340, 860, 460, 1000]}],
        enemies=[{"role": "enemy", "label": "unknown_enemy", "bbox": [650, 500, 810, 700], "entity_id": "unknown_character_1"}],
        characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        snapshot = agent._build_turn_snapshot(scene)
        assert len(snapshot.targets) == 1
        assert snapshot.targets[0].target_id.startswith("enemy_")
        kinds = {row["kind"] for row in legal_intents_for_snapshot(snapshot)}
        assert "inspect" in kinds
        assert "play_card" in kinds
    finally:
        agent._screen_objects.close()


def test_tutorial_page_prefers_current_frame() -> None:
    state = OverlayState()
    host = {"active": True, "title": "Combat Basics", "step": 3, "total": 3}
    llm1 = {"active": True, "title": "Combat Basics", "step": 2, "total": 3}
    resolved = state.resolve_tutorial(
        host_tutorial=host,
        llm1_tutorial=llm1,
        frame_hash="aaa",
        layout="paginated_overlay",
        dialog_open=True,
        activity="combat",
    )
    assert resolved.get("step") == 2
    closed = state.resolve_tutorial(
        host_tutorial=host,
        llm1_tutorial=None,
        frame_hash="bbb",
        layout="hand_and_board",
        dialog_open=False,
        activity="combat",
    )
    assert not closed.get("active")


def test_transition_frame_is_observation_only(tmp_path: Path) -> None:
    data = _load("transition_empty_frame.json")
    agent = _agent_for_combat(tmp_path, [], data["host_stats"])
    agent._last_obs_image = Image.new("RGB", (64, 48), (8, 8, 8))
    agent._last_frame_mad = float(data["pixel_mad"])
    agent._aim = AimGuide.from_marks(agent._last_obs_image, [])
    from loop.agent.decision import TurnSnapshot

    snapshot = TurnSnapshot(
        snapshot_id="t",
        screen="other",
        turn="unknown",
        hp=None,
        block=None,
        energy=None,
    )
    parsed, _ = agent._run_v2_act(snapshot)
    assert parsed == {}
    assert agent._pending_exec_error is None
    assert agent._decision_softlock is False
    assert agent._turn_transition is True


def test_two_transition_frames_do_not_softlock(tmp_path: Path) -> None:
    agent = _agent_for_combat(tmp_path, [], {"screen": "other", "hud": "off"})
    agent._last_obs_image = Image.new("RGB", (32, 24), (4, 4, 4))
    agent._last_frame_mad = 0.07
    agent._aim = AimGuide.from_marks(agent._last_obs_image, [])
    from loop.agent.decision import TurnSnapshot

    snapshot = TurnSnapshot(snapshot_id="t", screen="other", turn="unknown", hp=None, block=None, energy=None)
    agent._run_v2_act(snapshot)
    agent._run_v2_act(snapshot)
    assert agent._decision_softlock is False
    assert agent._empty_action_surface_count == 0


def test_stable_empty_combat_frame_still_softlocks(tmp_path: Path) -> None:
    agent = _agent_for_combat(tmp_path, [], {"screen": "combat", "activity": "combat", "hud": "on"})
    agent._last_obs_image = Image.new("RGB", (64, 48), (80, 80, 80))
    agent._last_frame_mad = 0.01
    agent._aim = AimGuide.from_marks(agent._last_obs_image, [])
    from loop.agent.decision import TurnSnapshot

    snapshot = TurnSnapshot(snapshot_id="t", screen="combat", turn="ours", hp=80, block=None, energy=3)
    agent._run_v2_act(snapshot)
    agent._run_v2_act(snapshot)
    assert agent._decision_softlock is True
    assert agent._pending_exec_error == "empty-action-surface"


def test_inventory_inconsistent_requests_rescan(tmp_path: Path) -> None:
    marks = [
        AimMark(id=1, x=0.5, y=0.5, x0=0.4, y0=0.4, x1=0.6, y1=0.6, label="icon", role="icon"),
    ]
    agent = _agent_for_combat(tmp_path, marks, {"screen": "combat", "activity": "combat"})
    agent._action_surface = build_action_surface(
        marks, run_id="replay", step_id=4, screen="combat", activity="combat",
    )
    from loop.agent.decision import TurnSnapshot

    snapshot = TurnSnapshot(snapshot_id="t", screen="combat", turn="ours", hp=80, block=None, energy=3)
    agent._run_v2_act(snapshot)
    assert agent._pending_exec_error == "inventory-inconsistent"
    assert agent._force_perceive is True
    assert agent._pending_inventory_rescan is True


def test_placeholder_node_stays_inspect_until_high_confidence_bind() -> None:
    placeholder = AimMark(id=1, x=0.47, y=0.40, x0=0.43, y0=0.36, x1=0.51, y1=0.44, label="node_0", role="")
    surface = build_action_surface(
        [placeholder], run_id="r", step_id=1, screen="map", activity="map",
    )
    assert "inspect" in surface.legal_intents
    assert "click_control" not in surface.legal_intents
    grounded = ground_spatial_labels(
        [placeholder],
        [{"label": "node_0", "role": "button", "confidence": "high"}],
        screen="map",
    )
    bound = build_action_surface(grounded, run_id="r", step_id=1, screen="map", activity="map")
    assert "click_control" in bound.legal_intents
    assert any(obj.object_id == "control:node_0" for obj in bound.objects)


def test_low_confidence_target_is_excluded_from_positive_export() -> None:
    step = StepLog(
        session_id="s",
        episode_id="e",
        step_id=1,
        ts="2026-09-07T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="x"),
        action=ActionLog(name="inspect"),
        result=StepResult(executed_as_proposed=True),
        turn_snapshot={
            "targets": [{"target_id": "enemy_1", "name": "unknown enemy", "identity_confidence": 0.25}],
        },
        intent={"kind": "inspect", "object_id": "enemy_1"},
    )
    assert export_uncertain_reason(step) == "unresolved-identity"
