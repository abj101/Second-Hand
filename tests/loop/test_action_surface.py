from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from loop.agent.loop import CollectAgent
from loop.agent.decision import TurnSnapshot, legal_intents_for_snapshot
from loop.fixtures.sts2 import Sts2Fixture
from loop.memory.screen_objects import ScreenObjectStore
from loop.mcp.tools.targeting import object_role
from loop.os.aim import AimMark
from loop.os.aim import AimGuide
from loop.os.aim import apply_enemy_bboxes
from loop.os.frame_model import FrameContext, build_action_surface, build_frame_model


def _mark(
    mid: int,
    label: str,
    *,
    x: float,
    y: float,
    role: str = "",
    object_id: str = "",
) -> AimMark:
    return AimMark(
        id=mid,
        x=x,
        y=y,
        x0=max(0.0, x - 0.06),
        y0=max(0.0, y - 0.05),
        x1=min(1.0, x + 0.06),
        y1=min(1.0, y + 0.05),
        label=label,
        role=role,
        object_id=object_id,
        source="test",
        confidence=0.95,
    )


@pytest.mark.parametrize(
    "screen,label",
    [
        ("select", "Yes"),
        ("select", "No"),
        ("title", "Continue"),
        ("title", "Settings"),
        ("shop", "Buy"),
        ("inventory", "Equip"),
        ("reward", "Skip"),
        ("pause", "Resume"),
    ],
)
def test_bottom_controls_never_become_cards_outside_combat(
    tmp_path: Path,
    screen: str,
    label: str,
) -> None:
    mark = _mark(1, label, x=0.5, y=0.88)
    context = FrameContext(screen=screen, activity=screen)
    surface = build_action_surface(
        [mark], run_id="r", step_id=1, screen=screen, activity=screen,
    )
    assert len(surface.objects) == 1
    obj = surface.objects[0]
    assert obj.role != "card"
    assert not obj.object_id.startswith("card_")

    store = ScreenObjectStore(tmp_path / f"{screen}.db")
    try:
        store.save_frame(
            run_id="r", step_id=1, marks=[mark], screen=screen,
            context=context, surface=surface,
        )
        stored = store.by_id(1)
        assert stored is not None
        assert stored.role != "card"
        assert object_role(stored) != "card"
        assert store.hand_cards() == []
    finally:
        store.close()


def test_explicit_button_role_beats_hand_band_geometry() -> None:
    surface = build_action_surface(
        [_mark(1, "Accept", x=0.5, y=0.92, role="button")],
        run_id="r",
        step_id=2,
        screen="other",
        activity="unknown",
    )
    button = surface.objects[0]
    assert button.role == "button"
    assert button.object_id == "control:accept"
    assert surface.legal_intents == ("click_control",)


def test_reward_card_choice_becomes_click_control_with_skip() -> None:
    fixture = Sts2Fixture()
    context = FrameContext(screen="reward", activity="reward")
    enriched = fixture.enrich_frame_marks(
        [
            _mark(1, "Add a card", x=0.50, y=0.59, role="card", object_id="card_1"),
            _mark(2, "Skip", x=0.89, y=0.92, role="button"),
        ],
        context,
        {"screen": "reward", "activity": "reward"},
    )
    surface = build_action_surface(
        enriched, run_id="r", step_id=1, screen="reward", activity="reward",
    )
    assert [(obj.label, obj.role) for obj in surface.interactive()] == [
        ("Add a card", "button"), ("Skip", "button"),
    ]
    assert surface.legal_intents == ("click_control",)

    snapshot = TurnSnapshot.from_action_surface(surface)
    assert [(control.control_id, control.label) for control in snapshot.controls] == [
        ("control:add_a_card", "Add a card"), ("control:skip", "Skip"),
    ]


@pytest.mark.parametrize("screen", ["map", "select"])
def test_card_marks_remain_suppressed_on_noncombat_screens(screen: str) -> None:
    fixture = Sts2Fixture()
    context = FrameContext(screen=screen, activity=screen)
    enriched = fixture.enrich_frame_marks(
        [_mark(1, "Add a card", x=0.50, y=0.59, role="card")],
        context,
        {"screen": screen, "activity": screen},
    )
    surface = build_action_surface(
        enriched, run_id="r", step_id=1, screen=screen, activity=screen,
    )
    assert surface.objects[0].role == "suppressed"
    assert "click_control" not in surface.legal_intents


def test_next_label_is_nav_next() -> None:
    mark = _mark(1, "Next", x=0.9, y=0.5)
    frame = build_frame_model([mark], screen="tutorial")
    assert frame.objects[0].role == "nav_next"

    surface = build_action_surface(
        [mark], run_id="r", step_id=1, screen="tutorial", activity="tutorial",
    )
    assert surface.objects[0].role == "nav_next"
    assert surface.objects[0].object_id == "control:next"


def test_modal_over_combat_suppresses_background_domain_objects() -> None:
    fixture = Sts2Fixture()
    context = FrameContext(
        screen="tutorial",
        activity="combat",
        layout="modal_dialog",
        blocking_overlay=True,
    )
    marks = [
        _mark(1, "Strike", x=0.38, y=0.88),
        _mark(2, "Cultist", x=0.72, y=0.61, role="enemy"),
        _mark(3, "Yes", x=0.56, y=0.86, role="button"),
        _mark(4, "End Turn", x=0.90, y=0.82, role="button"),
    ]
    enriched = fixture.enrich_frame_marks(marks, context, {"screen": "tutorial"})
    surface = build_action_surface(
        enriched,
        run_id="r",
        step_id=1,
        screen=context.screen,
        activity=context.activity,
        layout=context.layout,
        blocking_overlay=context.blocking_overlay,
    )
    roles = {obj.label: obj.role for obj in surface.objects}
    assert roles["Yes"] == "button"
    assert roles["Strike"] != "card"
    assert roles["Cultist"] != "enemy"
    assert roles["End Turn"] == "suppressed"
    assert surface.legal_intents == ("click_control",)


def test_confirmed_combat_retains_hand_and_end_turn() -> None:
    fixture = Sts2Fixture()
    context = FrameContext(
        screen="combat",
        activity="combat",
        layout="hand_and_board",
        blocking_overlay=False,
    )
    marks = [
        _mark(1, "Strike", x=0.36, y=0.88, role="card"),
        _mark(2, "Defend", x=0.50, y=0.88, role="card"),
        _mark(3, "Cultist", x=0.72, y=0.61, role="enemy"),
        _mark(4, "End Turn", x=0.90, y=0.82, role="button"),
    ]
    enriched = fixture.enrich_frame_marks(
        marks,
        context,
        {"screen": "combat", "activity": "combat", "turn": "ours"},
    )
    surface = build_action_surface(
        enriched,
        run_id="r",
        step_id=4,
        screen="combat",
        activity="combat",
        layout="hand_and_board",
        blocking_overlay=False,
    )
    by_label = {obj.label: obj for obj in surface.objects}
    assert by_label["Strike"].role == "card"
    assert by_label["Defend"].role == "card"
    assert by_label["Cultist"].role == "enemy"
    assert by_label["End Turn"].role == "button"
    assert by_label["Strike"].object_id.startswith("card_")
    assert by_label["Cultist"].object_id.startswith("enemy_")
    assert by_label["End Turn"].object_id == "control:end_turn"
    assert {"play_card", "end_turn"} <= set(surface.legal_intents)


def test_untyped_lower_band_labels_are_not_promoted_to_cards() -> None:
    fixture = Sts2Fixture()
    context = FrameContext(screen="combat", activity="combat")
    enriched = fixture.enrich_frame_marks(
        [_mark(1, "Unknown Label", x=0.36, y=0.88)], context,
        {"screen": "combat", "activity": "combat"},
    )
    assert enriched[0].role == ""


def test_combat_ghost_next_does_not_paginate_or_strip_hand() -> None:
    """Live 20260907T191012Z: leftover Prev/Next/Back on combat must not empty ACT."""
    fixture = Sts2Fixture()
    marks = [
        _mark(1, "Prev", x=0.08, y=0.47),
        _mark(2, "Back", x=0.09, y=0.81),
            _mark(3, "Defend", x=0.31, y=0.95, role="card"),
            _mark(4, "Strike", x=0.41, y=0.92, role="card"),
            _mark(5, "Bash", x=0.59, y=0.92, role="card"),
        _mark(6, "End Turn", x=0.89, y=0.82, role="button"),
        _mark(7, "Next", x=0.96, y=0.50),
        _mark(8, "Cultist", x=0.75, y=0.35, role="enemy"),
    ]
    host = {"screen": "combat", "activity": "combat", "turn": "ours"}
    provisional = build_action_surface(
        marks,
        run_id="r",
        step_id=3,
        screen="combat",
        activity="combat",
        host_stats=host,
    )
    assert provisional.context.layout == "hand_and_board"
    assert provisional.context.blocking_overlay is False
    assert provisional.context.allows_domain_entities is True
    enriched = fixture.enrich_frame_marks(marks, provisional.context, host)
    surface = build_action_surface(
        enriched,
        run_id="r",
        step_id=3,
        screen="combat",
        activity="combat",
        layout=provisional.context.layout,
        blocking_overlay=provisional.context.blocking_overlay,
        host_stats=host,
    )
    by_label = {obj.label: obj for obj in surface.objects}
    assert by_label["Strike"].role == "card"
    assert by_label["Defend"].role == "card"
    assert by_label["Bash"].role == "card"
    assert by_label["Cultist"].role == "enemy"
    assert by_label["End Turn"].role == "button"
    assert by_label["Next"].role != "nav_next"
    assert {"play_card", "end_turn"} <= set(surface.legal_intents)


def test_finished_tutorial_page_does_not_paginate_combat() -> None:
    marks = [
        _mark(1, "Prev", x=0.08, y=0.47),
        _mark(2, "Strike", x=0.41, y=0.92),
        _mark(3, "End Turn", x=0.89, y=0.82, role="button"),
        _mark(4, "Next", x=0.96, y=0.50),
    ]
    surface = build_action_surface(
        marks,
        run_id="r",
        step_id=3,
        screen="combat",
        activity="combat",
        host_stats={
            "screen": "combat",
            "activity": "combat",
            "tutorial": {"active": True, "title": "Combat Basics", "step": 3, "total": 3},
        },
    )
    assert surface.context.layout == "hand_and_board"
    assert surface.context.blocking_overlay is False


def test_open_tutorial_pagination_still_overlays_combat() -> None:
    marks = [
        _mark(1, "Prev", x=0.08, y=0.47),
        _mark(2, "Strike", x=0.41, y=0.92),
        _mark(3, "End Turn", x=0.89, y=0.82, role="button"),
        _mark(4, "Next", x=0.96, y=0.50),
    ]
    host = {
        "screen": "combat",
        "activity": "combat",
        "tutorial": {"active": True, "title": "Combat Basics", "step": 2, "total": 3},
    }
    provisional = build_action_surface(
        marks,
        run_id="r",
        step_id=1,
        screen="combat",
        activity="combat",
        host_stats=host,
    )
    assert provisional.context.layout == "paginated_overlay"
    assert provisional.context.blocking_overlay is True
    fixture = Sts2Fixture()
    enriched = fixture.enrich_frame_marks(marks, provisional.context, host)
    surface = build_action_surface(
        enriched,
        run_id="r",
        step_id=1,
        screen="combat",
        activity="combat",
        layout=provisional.context.layout,
        blocking_overlay=provisional.context.blocking_overlay,
        host_stats=host,
    )
    roles = {obj.label: obj.role for obj in surface.objects}
    assert roles["Next"] == "nav_next"
    assert roles["Strike"] != "card"
    assert "play_card" not in surface.legal_intents
    assert "click_control" in surface.legal_intents


def test_latest_tutorial_failure_has_visible_choices_and_dispatchable_yes() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[2]
        / "loop"
        / "fixtures"
        / "sts2_screens"
        / "tutorial_confirmation_failure.json"
    )
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    marks = [
        AimMark(
            id=row["id"],
            x=(row["bbox"][0] + row["bbox"][2]) / 2,
            y=(row["bbox"][1] + row["bbox"][3]) / 2,
            x0=row["bbox"][0],
            y0=row["bbox"][1],
            x1=row["bbox"][2],
            y1=row["bbox"][3],
            label=row["label"],
            role=row["role"],
            source=row["source"],
            confidence=row["confidence"],
        )
        for row in data["marks"]
    ]
    surface = build_action_surface(
        marks,
        run_id=data["source_run"],
        step_id=data["source_step"],
        screen=data["screen"],
        activity=data["activity"],
        layout=data["layout"],
        blocking_overlay=data["blocking_overlay"],
    )
    visible_labels = {row["label"] for row in data["scene_controls"]}
    assert set(data["must_include"]) <= visible_labels
    assert not set(data["must_not_include"]) & visible_labels

    executable = {obj.label: obj for obj in surface.interactive()}
    assert set(data["must_dispatch_one_of"]) & set(executable)
    yes = executable["Yes"]
    assert yes.role == "button"
    assert yes.object_id == "control:yes"
    assert "end_turn" not in surface.legal_intents

    snapshot = TurnSnapshot.from_action_surface(surface)
    assert [(control.control_id, control.label) for control in snapshot.controls] == [
        ("control:yes", "Yes")
    ]
    assert snapshot.cards == ()


def test_collect_agent_finalizes_tutorial_scene_from_current_marks(tmp_path: Path) -> None:
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 3
    agent._host_stats = {"screen": "select"}
    agent._obs_metrics = {"screen": "select"}
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(
        image,
        [_mark(7, "Yes", x=0.54, y=0.86)],
    )
    agent._screen_objects = ScreenObjectStore(tmp_path / "objects.db")
    agent._action_surface = None
    agent._frame_model = None
    scene = SimpleNamespace(
        screen="select",
        controls=[
            {"label": "Yes", "role": "button", "confidence": "high"},
            {"label": "No", "role": "button", "confidence": "high"},
        ],
        hand_cards=[],
        enemies=[],
        characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        assert [(obj.object_id, obj.label, obj.role) for obj in surface.interactive()] == [
            ("control:yes", "Yes", "button")
        ]
        stored = agent._screen_objects.by_object_id("control:yes")
        assert stored is not None and stored.role == "button"

        agent._game_state = None
        agent._last_execution_failure = None
        agent._v2_control_objects = {}
        snapshot = agent._build_turn_snapshot(scene)
        assert snapshot.snapshot_id == surface.snapshot_id
        assert snapshot.legal_intents == ("click_control",)
        assert [(row.control_id, row.label) for row in snapshot.controls] == [
            ("control:yes", "Yes")
        ]
    finally:
        agent._screen_objects.close()


def test_collect_agent_publishes_typed_reward_controls_from_unique_current_bboxes(tmp_path: Path) -> None:
    """Textless local marks may become controls only through unique typed bounds."""
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 2
    agent._host_stats = {"screen": "reward", "activity": "reward"}
    agent._obs_metrics = {"screen": "reward", "activity": "reward"}
    agent._last_obs_image = image
    # Shape distilled from 20260907T003540Z: three textless current-frame
    # marks aligned with the reward buttons and Skip.
    agent._aim = AimGuide.from_marks(image, [
        _mark(2, "icon", x=0.50, y=0.48),
        _mark(3, "icon", x=0.50, y=0.59),
        _mark(5, "icon", x=0.89, y=0.92),
    ])
    agent._screen_objects = ScreenObjectStore(tmp_path / "reward-objects.db")
    agent._action_surface = None
    agent._frame_model = None
    scene = SimpleNamespace(
        screen="reward",
        controls=[
            {"label": "11 Gold", "role": "button", "bbox": "390,430,610,530"},
            {"label": "Add a card", "role": "button", "bbox": "390,540,610,640"},
            {"label": "Skip", "role": "button", "bbox": "800,850,980,990"},
        ],
        hand_cards=[], enemies=[], characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        assert [(obj.label, obj.role) for obj in surface.interactive()] == [
            ("11 Gold", "button"), ("Add a card", "button"), ("Skip", "button"),
        ]
        agent._game_state = None
        agent._entity_tracker = None
        agent._reason_controls = []
        agent._last_execution_failure = None
        agent._v2_control_objects = {}
        snapshot = agent._build_turn_snapshot(scene)
        assert snapshot.legal_intents == ("click_control",)
        assert [control.label for control in snapshot.controls] == ["11 Gold", "Add a card", "Skip"]
    finally:
        agent._screen_objects.close()


def test_collect_agent_publishes_reward_card_choices_from_hand_cards(tmp_path: Path) -> None:
    """Choose-a-Card LLM1 hand_cards bind current-frame icons as click_control."""
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 2
    agent._host_stats = {"screen": "reward", "activity": "reward"}
    agent._obs_metrics = {"screen": "reward", "activity": "reward"}
    agent._last_obs_image = image
    # Distilled from 20260909T200030Z: three unlabeled card marks + Skip.
    agent._aim = AimGuide.from_marks(image, [
        _mark(12, "icon", x=0.31, y=0.68),
        _mark(15, "icon", x=0.50, y=0.62),
        _mark(2, "icon", x=0.67, y=0.67),
        _mark(9, "Skip", x=0.50, y=0.92, role="button"),
    ])
    agent._screen_objects = ScreenObjectStore(tmp_path / "choose-card.db")
    agent._action_surface = None
    agent._frame_model = None
    scene = SimpleNamespace(
        screen="reward",
        controls=[{"label": "Skip", "role": "button", "bbox": "400,850,600,990"}],
        hand_cards=[
            {"label": "Setup Strike", "role": "card", "bbox": [245, 480, 385, 880]},
            {"label": "Tremble", "role": "card", "bbox": [405, 450, 565, 930]},
            {"label": "Blood Wall", "role": "card", "bbox": [605, 475, 735, 860]},
        ],
        enemies=[],
        characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        labels = [(obj.label, obj.role) for obj in surface.interactive()]
        assert ("Setup Strike", "button") in labels
        assert ("Tremble", "button") in labels
        assert ("Blood Wall", "button") in labels
        assert ("Skip", "button") in labels
        assert surface.legal_intents == ("click_control",)
        agent._game_state = None
        agent._entity_tracker = None
        agent._reason_controls = []
        agent._last_execution_failure = None
        agent._v2_control_objects = {}
        snapshot = agent._build_turn_snapshot(scene)
        assert snapshot.cards == ()
        assert snapshot.legal_intents == ("click_control",)
        names = {control.label for control in snapshot.controls}
        assert {"Setup Strike", "Tremble", "Blood Wall", "Skip"} <= names
        assert all(control.control_id.startswith("control:") for control in snapshot.controls)
        kinds = {row["kind"] for row in legal_intents_for_snapshot(snapshot)}
        assert "click_control" in kinds
        assert kinds <= {"click_control", "inspect"}
        assert "play_card" not in kinds
    finally:
        agent._screen_objects.close()


def test_reward_hand_card_reconcile_requires_unique_bbox(tmp_path: Path) -> None:
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 2
    agent._host_stats = {"screen": "reward", "activity": "reward"}
    agent._obs_metrics = {"screen": "reward", "activity": "reward"}
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, [
        _mark(1, "icon", x=0.40, y=0.60),
        _mark(2, "icon", x=0.45, y=0.62),
    ])
    agent._screen_objects = ScreenObjectStore(tmp_path / "ambiguous-reward.db")
    agent._action_surface = None
    agent._frame_model = None
    scene = SimpleNamespace(
        screen="reward",
        controls=[],
        hand_cards=[
            {"label": "Tremble", "role": "card", "bbox": [300, 450, 600, 900]},
            {"label": "Setup Strike", "role": "card", "bbox": [300, 450, 600, 900]},
        ],
        enemies=[],
        characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        labels = [(obj.label, obj.role) for obj in surface.interactive()]
        assert ("Tremble", "button") not in labels
        assert ("Setup Strike", "button") not in labels
        assert not any(obj.role == "button" for obj in surface.interactive())
        assert "click_control" not in surface.legal_intents
    finally:
        agent._screen_objects.close()


def test_combat_hand_cards_still_reconcile_as_cards(tmp_path: Path) -> None:
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 3
    agent._host_stats = {"screen": "combat", "activity": "combat"}
    agent._obs_metrics = {"screen": "combat", "activity": "combat"}
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, [
        _mark(1, "Defend", x=0.31, y=0.92, role="hand_candidate"),
        _mark(2, "Strike", x=0.50, y=0.92, role="hand_candidate"),
        _mark(3, "End Turn", x=0.89, y=0.82, role="button"),
    ])
    agent._screen_objects = ScreenObjectStore(tmp_path / "combat-hand.db")
    agent._action_surface = None
    agent._frame_model = None
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
        agent._game_state = None
        agent._entity_tracker = None
        agent._reason_controls = []
        agent._last_execution_failure = None
        agent._v2_control_objects = {}
        snapshot = agent._build_turn_snapshot(scene)
        assert [card.name for card in snapshot.cards] == ["Defend", "Strike"]
        assert "play_card" in snapshot.legal_intents
        kinds = {row["kind"] for row in legal_intents_for_snapshot(snapshot)}
        assert "play_card" in kinds
        assert all(obj.role == "card" for obj in surface.objects if obj.label in {"Defend", "Strike"})
    finally:
        agent._screen_objects.close()


def test_combat_typed_hand_bboxes_mint_cards_without_candidates(tmp_path: Path) -> None:
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 3
    agent._host_stats = {"screen": "combat", "activity": "combat"}
    agent._obs_metrics = {"screen": "combat", "activity": "combat"}
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, [
        _mark(1, "icon", x=0.30, y=0.93, role="icon"),
        _mark(2, "icon", x=0.43, y=0.93, role="icon"),
        _mark(3, "icon", x=0.55, y=0.93, role="icon"),
        _mark(4, "icon", x=0.68, y=0.93, role="icon"),
        _mark(5, "icon", x=0.77, y=0.93, role="icon"),
        _mark(6, "End Turn", x=0.89, y=0.82, role="button"),
    ])
    agent._screen_objects = ScreenObjectStore(tmp_path / "combat-typed-hand.db")
    agent._action_surface = None
    agent._frame_model = None
    scene = SimpleNamespace(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "bbox": None},
            {"label": "Bash", "role": "card", "bbox": None},
            {"label": "Defend", "role": "card", "bbox": None},
            {"label": "Defend", "role": "card", "bbox": None},
            {"label": "Defend", "role": "card", "bbox": None},
            {"label": "End Turn", "role": "button"},
        ],
        hand_cards=[
            {"label": "Strike", "role": "card", "bbox": [246, 857, 363, 994], "id": "card_1"},
            {"label": "Bash", "role": "card", "bbox": [370, 857, 487, 994], "id": "card_2"},
            {"label": "Defend", "role": "card", "bbox": [494, 857, 611, 994], "id": "card_3"},
            {"label": "Defend", "role": "card", "bbox": [618, 857, 735, 994], "id": "card_4"},
            {"label": "Defend", "role": "card", "bbox": [718, 857, 820, 994], "id": "card_5"},
        ],
        enemies=[],
        characters=[],
    )
    try:
        agent._ground_aim_from_scene(scene)
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        agent._game_state = None
        agent._entity_tracker = None
        agent._reason_controls = []
        agent._last_execution_failure = None
        agent._v2_control_objects = {}
        snapshot = agent._build_turn_snapshot(scene)
        names = [card.name for card in sorted(snapshot.cards, key=lambda card: card.bbox.x0)]
        assert names == ["Strike", "Bash", "Defend", "Defend", "Defend"]
        assert "play_card" in snapshot.legal_intents
        kinds = {row["kind"] for row in legal_intents_for_snapshot(snapshot)}
        assert "play_card" in kinds
        named = [obj for obj in surface.interactive() if obj.label in {"Strike", "Bash", "Defend"}]
        assert named
        assert all(obj.role == "card" for obj in named)
        assert all(
            obj.role != "card"
            for obj in surface.objects
            if (obj.label or "").strip().lower() == "icon"
        )
    finally:
        agent._screen_objects.close()


def test_collect_agent_publishes_typed_control_from_unique_current_ocr_word(tmp_path: Path) -> None:
    """A typed control can use one exact OCR word when its scene has no bbox."""
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "run"
    agent._step_id = 1
    agent._host_stats = {"screen": "reward", "activity": "reward"}
    agent._obs_metrics = {"screen": "reward", "activity": "reward"}
    agent._last_obs_image = image
    agent._aim = AimGuide.from_marks(image, [_mark(5, "icon", x=0.89, y=0.92)])
    # Shape distilled from 20260907T003920Z: the word is current OCR evidence
    # but aim merging retained only the visual icon.
    agent._last_ocr_words = [_mark(1, "Skip", x=0.89, y=0.92, role="", object_id="")]
    agent._screen_objects = ScreenObjectStore(tmp_path / "ocr-reward-objects.db")
    agent._action_surface = None
    agent._frame_model = None
    scene = SimpleNamespace(
        screen="reward",
        controls=[{"label": "Skip", "role": "button", "source": "ocr"}],
        hand_cards=[], enemies=[], characters=[],
    )
    try:
        surface = agent._finalize_action_surface(scene)
        assert surface is not None
        assert [(obj.label, obj.role) for obj in surface.interactive()] == [("Skip", "button")]
        assert "ocr-control" in surface.interactive()[0].provenance
        agent._game_state = None
        agent._entity_tracker = None
        agent._reason_controls = []
        agent._last_execution_failure = None
        agent._v2_control_objects = {}
        snapshot = agent._build_turn_snapshot(scene)
        assert snapshot.legal_intents == ("click_control",)
        assert [(control.control_id, control.label) for control in snapshot.controls] == [
            ("control:skip", "Skip")
        ]
    finally:
        agent._screen_objects.close()


def _reward_phrase_agent(*, screen: str = "reward") -> CollectAgent:
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(image, [_mark(9, "icon", x=0.50, y=0.59)])
    agent._host_stats = {"screen": screen, "activity": screen}
    agent._obs_metrics = {"screen": screen, "activity": screen}
    return agent


def _phrase_scene(*, screen: str = "reward") -> SimpleNamespace:
    return SimpleNamespace(
        screen=screen,
        controls=[{"label": "Add a card to your deck", "role": "button"}],
        hand_cards=[], enemies=[], characters=[],
    )


def test_reward_phrase_grounding_joins_split_current_ocr_words() -> None:
    agent = _reward_phrase_agent()
    agent._last_ocr_words = [
        _mark(1, "Add", x=.34, y=.59),
        _mark(2, "a", x=.42, y=.59),
        _mark(3, "card", x=.50, y=.59),
        _mark(4, "to", x=.58, y=.59),
        _mark(5, "your", x=.66, y=.59),
        _mark(6, "deck", x=.74, y=.59),
    ]

    reconciled = agent._reconcile_scene_marks(_phrase_scene())

    assert [(mark.label, mark.role) for mark in reconciled] == [
        ("Add a card to your deck", "button")
    ]
    assert "ocr-control" in reconciled[0].source


def test_reward_phrase_grounding_fails_closed_for_duplicate_current_lines() -> None:
    agent = _reward_phrase_agent()
    agent._last_ocr_words = [
        _mark(1, "Add", x=.42, y=.48),
        _mark(2, "a", x=.50, y=.48),
        _mark(3, "card", x=.58, y=.48),
        _mark(4, "to", x=.66, y=.48),
        _mark(5, "your", x=.74, y=.48),
        _mark(6, "deck", x=.82, y=.48),
        # The duplicate is a complete second OCR line, not a second word.
        _mark(7, "Add", x=.42, y=.74),
        _mark(8, "a", x=.50, y=.74),
        _mark(9, "card", x=.58, y=.74),
        _mark(10, "to", x=.66, y=.74),
        _mark(11, "your", x=.74, y=.74),
        _mark(12, "deck", x=.82, y=.74),
    ]

    reconciled = agent._reconcile_scene_marks(_phrase_scene())

    assert [(mark.label, mark.role) for mark in reconciled] == [("icon", "")]


def test_phrase_grounding_is_reward_only() -> None:
    agent = _reward_phrase_agent(screen="map")
    agent._last_ocr_words = [
        _mark(1, "Add", x=.34, y=.59),
        _mark(2, "a", x=.42, y=.59),
        _mark(3, "card", x=.50, y=.59),
        _mark(4, "to", x=.58, y=.59),
        _mark(5, "your", x=.66, y=.59),
        _mark(6, "deck", x=.74, y=.59),
    ]

    reconciled = agent._reconcile_scene_marks(_phrase_scene(screen="map"))

    assert [(mark.label, mark.role) for mark in reconciled] == [("icon", "")]


def test_turn_snapshot_keeps_canonical_card_name_when_scene_rows_reflow(tmp_path: Path) -> None:
    """Perception metadata cannot change which card a prompt-facing ID denotes.

    This is distilled from the live reflow regression: the surface's third
    card was visibly a Strike, while a separately ordered perception row
    claimed that ``card_3`` was Defend.  The ACT prompt must retain the
    surface label or a valid Strike intent can dispatch the wrong card.
    """
    marks = [
        _mark(1, "Defend", x=0.36, y=0.91, role="card", object_id="card_1"),
        _mark(2, "Strike", x=0.50, y=0.90, role="card", object_id="card_3"),
        _mark(3, "Cultist", x=0.75, y=0.63, role="enemy", object_id="enemy_1"),
    ]
    surface = build_action_surface(
        marks, run_id="r", step_id=2, screen="combat", activity="combat",
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture()
    agent.session_id = "r"
    agent._step_id = 2
    agent._host_stats = {"screen": "combat", "energy": 3}
    agent._obs_metrics = {"screen": "combat", "energy": 3}
    agent._screen_objects = ScreenObjectStore(tmp_path / "objects.db")
    agent._screen_objects.save_frame(
        run_id="r", step_id=2, marks=marks, screen="combat",
        context=surface.context, surface=surface,
    )
    agent._action_surface = surface
    agent._game_state = None
    agent._entity_tracker = None
    agent._reason_controls = []
    agent._last_execution_failure = None
    agent._v2_control_objects = {}
    scene = SimpleNamespace(
        hand_cards=[
            {"entity_id": "card_1", "label": "Defend"},
            # Stale, independently ordered metadata: never an execution authority.
            {"entity_id": "card_3", "label": "Defend"},
        ],
        enemies=[], characters=[], controls=[],
    )
    try:
        snapshot = agent._build_turn_snapshot(scene)
        assert [(card.card_id, card.name) for card in snapshot.cards] == [
            ("card_1", "Defend"), ("card_3", "Strike"),
        ]
    finally:
        agent._screen_objects.close()


def test_scene_metadata_cannot_promote_nearby_unknown_object_to_enemy() -> None:
    """A bounding box is evidence, not authority to mint an executable role."""
    image = Image.new("RGB", (128, 96))
    agent = CollectAgent.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(image, [
        _mark(1, "Strike", x=0.40, y=0.90, role="card", object_id="card_1"),
        _mark(2, "icon", x=0.58, y=0.61, role="unknown", object_id="object:2"),
        _mark(3, "unidentified_enemy", x=0.75, y=0.63, role="enemy", object_id="enemy_1"),
    ])
    scene = SimpleNamespace(
        controls=[], hand_cards=[], characters=[],
        enemies=[{
            "entity_id": "unknown_character_1",
            "role": "enemy",
            "label": "Enemy",
            # Coordinates are fixture evidence only. The nearby icon must not
            # become an enemy when this row lacks an exact canonical binding.
            "bbox": [520, 560, 640, 660],
        }],
    )
    reconciled = agent._reconcile_scene_marks(scene)
    assert [(mark.object_id, mark.role) for mark in reconciled] == [
        ("card_1", "card"), ("object:2", "unknown"), ("enemy_1", "enemy"),
    ]


def test_enemy_bbox_survives_unrelated_control_ocr_across_frames(tmp_path: Path) -> None:
    """A control label must not suppress or relabel typed enemy bbox evidence."""
    frame_one = apply_enemy_bboxes(
        [_mark(1, "Strike", x=.15, y=.15, role="card", object_id="card_1")],
        [{"role": "enemy", "label": "unknown_enemy", "bbox": [.70, .55, .82, .70]}],
    )
    frame_two = apply_enemy_bboxes(
        [
            _mark(1, "Strike", x=.15, y=.15, role="card", object_id="card_1"),
            # Same frame includes unrelated control OCR and an untyped cached
            # label. Neither is enemy-compatible evidence.
            _mark(2, "End Turn", x=.90, y=.10, role="button", object_id="control:end_turn"),
            _mark(3, "unknown_enemy", x=.76, y=.62, role="unknown", object_id="object:3"),
        ],
        [{"role": "enemy", "name": "Nibbit", "name_source": "ocr", "bbox": [.70, .55, .82, .70]}],
    )
    enemies = [mark for mark in frame_two if mark.role == "enemy"]
    assert len(enemies) == 1
    # Identity is optional, but a control's label must never leak across roles.
    assert enemies[0].label != "End Turn"

    store = ScreenObjectStore(tmp_path / "sequence.db")
    try:
        store.save_frame(run_id="r", step_id=1, marks=frame_one, screen="combat")
        store.save_frame(run_id="r", step_id=2, marks=frame_two, screen="combat")
        target = store.by_object_id("enemy_1")
        assert target is not None and target.role == "enemy"
    finally:
        store.close()


def test_empty_action_surface_skips_act_and_recovers_once() -> None:
    surface = build_action_surface(
        [_mark(1, "Mystery", x=0.5, y=0.5)],
        run_id="r",
        step_id=1,
        screen="tutorial",
        activity="combat",
        layout="modal_dialog",
        blocking_overlay=True,
    )
    snapshot = TurnSnapshot.from_action_surface(surface, turn="ours")
    assert snapshot.legal_intents == ()

    agent = CollectAgent.__new__(CollectAgent)
    agent._action_surface = surface
    agent.gateway = Mock()
    agent._last_execution_failure = None
    agent._last_reason_state_key = ""
    agent._unchanged_failure_count = 0
    agent._empty_action_surface_key = ""
    agent._empty_action_surface_count = 0
    agent._decision_softlock = False
    agent._session_flags = []
    agent._pending_exec_error = None
    agent._force_perceive = False
    agent._perception_cache = Mock()

    parsed, latency = agent._run_v2_act(snapshot)
    assert (parsed, latency) == ({}, 0.0)
    assert agent._pending_exec_error == "inventory-inconsistent"
    assert agent._force_perceive is True
    agent.gateway.assert_not_called()
    agent._perception_cache.reset.assert_called_once_with()

    agent._pending_exec_error = None
    parsed, latency = agent._run_v2_act(snapshot)
    assert (parsed, latency) == ({}, 0.0)
    assert agent._pending_exec_error == "empty-action-surface"
    assert agent._decision_softlock is True
    agent.gateway.assert_not_called()
    agent._perception_cache.reset.assert_called_once_with()


def test_combat_next_without_overlay_is_not_nav_next() -> None:
    mark = _mark(1, "Next", x=0.9, y=0.5)
    frame = build_frame_model([mark], screen="combat", host_stats={"screen": "combat", "activity": "combat"})
    assert frame.objects[0].role != "nav_next"


def test_tutorial_next_remains_nav_next() -> None:
    mark = _mark(1, "Next", x=0.9, y=0.5)
    frame = build_frame_model(
        [mark],
        screen="tutorial",
        host_stats={"screen": "tutorial", "tutorial": {"active": True, "step": 1, "total": 3}},
    )
    assert frame.objects[0].role == "nav_next"


def test_allowed_controls_admit_only_typed_generic_roles() -> None:
    from loop.agent.prompts import allowed_controls_for_turn, allowed_sets_from_controls

    admitted, source = allowed_controls_for_turn(
        inventory=[
            {"label": "Next", "role": "other", "confidence": "high"},
            {"label": "Confirm", "role": "button", "confidence": "high", "x": 0.5, "y": 0.9},
            {"label": "Strike", "role": "card", "confidence": "high"},
        ],
    )
    assert source == "inventory"
    assert [row["label"] for row in admitted] == ["Confirm"]
    allowed, targets = allowed_sets_from_controls(
        [
            {"label": "Confirm", "role": "button", "confidence": "high"},
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "Cultist", "role": "enemy", "confidence": "high"},
        ]
    )
    assert allowed == {"Confirm"}
    assert targets == {"Cultist"}
    assert "Strike" not in allowed


def test_control_suppression_blocks_twice_on_unchanged_surface() -> None:
    from loop.memory.overlay_state import ControlSuppressionState

    agent = CollectAgent.__new__(CollectAgent)
    agent._control_suppression = ControlSuppressionState()
    agent._last_surface_screen = "combat"
    agent._last_surface_membership = ()
    agent._prev_surface_controls = []
    agent._host_stats = {"screen": "combat"}
    agent._obs_metrics = {"screen": "combat"}
    controls = [
        {"label": "Next", "role": "button", "confidence": "high", "x": 0.9, "y": 0.5, "x0": 0.86, "y0": 0.45, "x1": 0.94, "y1": 0.55},
    ]
    first = agent._apply_control_suppression(controls)
    assert [row["label"] for row in first] == ["Next"]
    agent._record_blocked_control_attempt("Next")
    second = agent._apply_control_suppression(controls)
    assert [row["label"] for row in second] == ["Next"]
    agent._record_blocked_control_attempt("Next")
    third = agent._apply_control_suppression(controls)
    assert third == []
    assert agent._control_suppression.last_log_reason == "suppressed:blocked-twice"


def test_surface_change_clears_control_suppression() -> None:
    from loop.memory.overlay_state import ControlSuppressionState

    agent = CollectAgent.__new__(CollectAgent)
    agent._control_suppression = ControlSuppressionState()
    agent._last_surface_screen = "combat"
    agent._last_surface_membership = ()
    agent._prev_surface_controls = []
    agent._host_stats = {"screen": "combat"}
    agent._obs_metrics = {"screen": "combat"}
    controls = [
        {"label": "Next", "role": "button", "confidence": "high", "x": 0.9, "y": 0.5, "x0": 0.86, "y0": 0.45, "x1": 0.94, "y1": 0.55},
    ]
    agent._apply_control_suppression(controls)
    agent._record_blocked_control_attempt("Next")
    agent._record_blocked_control_attempt("Next")
    agent._host_stats = {"screen": "loot"}
    agent._obs_metrics = {"screen": "loot"}
    controls[0]["label"] = "Continue"
    refreshed = agent._apply_control_suppression(controls)
    assert [row["label"] for row in refreshed] == ["Continue"]


def test_rebound_control_with_new_bounds_is_new_attempt() -> None:
    from loop.memory.overlay_state import ControlSuppressionState
    from loop.os.frame_model import control_key_from_row

    agent = CollectAgent.__new__(CollectAgent)
    agent._control_suppression = ControlSuppressionState()
    agent._last_surface_screen = "tutorial"
    agent._last_surface_membership = ()
    agent._prev_surface_controls = []
    agent._host_stats = {"screen": "tutorial"}
    agent._obs_metrics = {"screen": "tutorial"}
    row_a = {"label": "Next", "role": "nav_next", "confidence": "high", "x": 0.9, "y": 0.5, "x0": 0.86, "y0": 0.45, "x1": 0.94, "y1": 0.55}
    row_b = dict(row_a)
    row_b.update({"x0": 0.80, "x1": 0.88})
    assert control_key_from_row(row_a, screen="tutorial") != control_key_from_row(row_b, screen="tutorial")
    agent._apply_control_suppression([row_a])
    agent._record_blocked_control_attempt("Next")
    agent._record_blocked_control_attempt("Next")
    rebound = agent._apply_control_suppression([row_b])
    assert [row["label"] for row in rebound] == ["Next"]


def test_control_key_prefers_stable_object_id_over_mark_number() -> None:
    from loop.os.frame_model import control_key_from_row

    bounds = {"x0": 0.86, "y0": 0.45, "x1": 0.94, "y1": 0.55}
    row_mark_3 = {
        "id": 3,
        "label": "Next",
        "role": "nav_next",
        "object_id": "control:next",
        **bounds,
    }
    row_mark_9 = dict(row_mark_3)
    row_mark_9["id"] = 9
    key_a = control_key_from_row(row_mark_3, screen="tutorial")
    key_b = control_key_from_row(row_mark_9, screen="tutorial")
    assert key_a == key_b
    assert "oid:control:next" in key_a
    assert "|3|" not in key_a and "|9|" not in key_a


def test_control_key_same_label_icons_at_different_bounds_are_distinct() -> None:
    from loop.os.frame_model import control_key_from_row

    left = {
        "label": "icon",
        "role": "button",
        "x0": 0.10,
        "y0": 0.40,
        "x1": 0.22,
        "y1": 0.55,
    }
    right = {
        "label": "icon",
        "role": "button",
        "x0": 0.78,
        "y0": 0.40,
        "x1": 0.90,
        "y1": 0.55,
    }
    assert control_key_from_row(left, screen="reward") != control_key_from_row(right, screen="reward")


def test_control_key_rebound_same_object_id_new_bounds() -> None:
    from loop.os.frame_model import control_key_from_row

    row_a = {
        "label": "Next",
        "role": "nav_next",
        "object_id": "control:next",
        "x0": 0.86,
        "y0": 0.45,
        "x1": 0.94,
        "y1": 0.55,
    }
    row_b = dict(row_a)
    row_b.update({"x0": 0.80, "x1": 0.88})
    assert control_key_from_row(row_a, screen="tutorial") != control_key_from_row(row_b, screen="tutorial")
    assert "oid:control:next" in control_key_from_row(row_a, screen="tutorial")


def test_control_key_includes_observation_context_when_present() -> None:
    from loop.os.frame_model import control_key_from_row

    base = {
        "label": "Yes",
        "role": "button",
        "object_id": "control:yes",
        "x0": 0.45,
        "y0": 0.80,
        "x1": 0.55,
        "y1": 0.90,
    }
    with_obs = dict(base, observation_id="obs-42")
    without_obs = control_key_from_row(base, screen="select")
    with_obs_key = control_key_from_row(with_obs, screen="select")
    assert "obs-42" in with_obs_key
    assert with_obs_key != without_obs


def test_control_key_ignores_mark_derived_object_ids() -> None:
    from loop.os.frame_model import control_key_from_row

    row = {
        "id": 7,
        "label": "icon",
        "role": "button",
        "object_id": "object:7",
        "x0": 0.40,
        "y0": 0.40,
        "x1": 0.60,
        "y1": 0.60,
    }
    key = control_key_from_row(row, screen="reward")
    assert key.startswith("reward|lbl:icon|")
    assert "object:7" not in key


def test_record_blocked_attempt_uses_spatial_match_for_duplicate_icons() -> None:
    from loop.memory.overlay_state import ControlSuppressionState

    agent = CollectAgent.__new__(CollectAgent)
    agent._control_suppression = ControlSuppressionState()
    agent._last_surface_screen = "reward"
    agent._last_surface_membership = ()
    agent._prev_surface_controls = []
    agent._last_snapshot_id = ""
    agent._host_stats = {"screen": "reward"}
    agent._obs_metrics = {"screen": "reward"}
    left = {
        "label": "icon",
        "role": "button",
        "x": 0.16,
        "y": 0.47,
        "x0": 0.10,
        "y0": 0.40,
        "x1": 0.22,
        "y1": 0.55,
    }
    right = {
        "label": "icon",
        "role": "button",
        "x": 0.84,
        "y": 0.47,
        "x0": 0.78,
        "y0": 0.40,
        "x1": 0.90,
        "y1": 0.55,
    }
    agent._apply_control_suppression([left, right])
    agent._record_blocked_control_attempt("icon", x=0.84, y=0.47)
    agent._record_blocked_control_attempt("icon", x=0.84, y=0.47)
    kept = agent._apply_control_suppression([left, right])
    assert len(kept) == 1
    assert abs(float(kept[0]["x0"]) - 0.10) < 0.01


def test_spatial_overlap_resolves_duplicate_label_rows() -> None:
    from loop.memory.overlay_state import ControlSuppressionState
    from loop.os.frame_model import control_key_from_row, find_control_row_for_suppression

    left = {
        "label": "icon",
        "role": "button",
        "x0": 0.10,
        "y0": 0.40,
        "x1": 0.22,
        "y1": 0.55,
    }
    right = {
        "label": "icon",
        "role": "button",
        "x0": 0.78,
        "y0": 0.40,
        "x1": 0.90,
        "y1": 0.55,
    }
    prior = [left, right]
    matched = find_control_row_for_suppression(right, prior, screen="reward")
    assert matched is right
    suppression = ControlSuppressionState()
    assert suppression.key_for_row(right, screen="reward", prior_rows=prior) == control_key_from_row(
        right, screen="reward"
    )


def test_reward_inventory_has_no_combat_actions_after_combat_won(tmp_path: Path) -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.agent.prompts import allowed_sets_from_controls
    from loop.log.store import EpisodeStore
    from loop.memory.overlay_state import ControlSuppressionState
    from loop.agent.entities import EntityTracker
    from loop.agent.perception_cache import PerceptionCache
    from loop.os.aim import AimGuide

    agent = CollectAgent.__new__(CollectAgent)
    agent.fixture = Sts2Fixture(controller=None)
    agent.store = EpisodeStore(tmp_path)
    agent.config = CollectConfig(skip_models=True, arm="no_skill")
    agent.session_id = "sess"
    agent._step_id = 3
    agent._screen_objects = ScreenObjectStore(tmp_path / "reward.db")
    agent._last_click = None
    agent._current_episode_id = "ep1"
    agent._combat_index = 1
    agent._combat_won_keys = set()
    agent._prev_host_stats = {
        "screen": "combat",
        "enemies": [{"hp": 0, "hp_max": 40, "label": "Nibbit"}],
        "player": {"energy": 0},
    }
    agent._host_stats = {
        "screen": "loot",
        "ocr": "Loot 40 Gold Take Gold Skip",
        "enemies": [],
        "player": None,
    }
    agent._obs_metrics = {"screen": "combat", "energy": 1}
    agent._control_suppression = ControlSuppressionState()
    agent._entity_tracker = EntityTracker()
    agent._perception_cache = PerceptionCache()
    agent._selected = "Strike"
    image = Image.new("RGB", (64, 64), (30, 30, 30))
    agent._aim = AimGuide.from_marks(
        image,
        [
            _mark(3, "Strike", x=0.4, y=0.85, role="card", object_id="card_3"),
            _mark(9, "Nibbit", x=0.7, y=0.6, role="enemy", object_id="enemy_9"),
            _mark(4, "Take Gold", x=0.5, y=0.8, role="button", object_id="ctl_4"),
        ],
    )
    metrics = agent._maybe_apply_combat_exit_transition(agent._obs_metrics)
    assert metrics["screen"] == "reward"
    assert metrics["combat_won"] is True
    allowed, targets = allowed_sets_from_controls(
        [
            {"label": "Take Gold", "role": "button", "confidence": "high"},
            {"label": "Skip", "role": "button", "confidence": "high"},
        ]
    )
    assert "Strike" not in allowed
    assert targets == set()
    assert agent._selected is None


def test_map_control_failure_replans_not_softlock(monkeypatch) -> None:
    from loop.agent.decision import ActDecision, BoundingBox, ClickControlIntent, ControlSnapshot

    snapshot = TurnSnapshot(
        snapshot_id="map-1",
        screen="map",
        turn=None,
        hp=73,
        block=None,
        energy=None,
        controls=(
            ControlSnapshot("control:node_0", "node_0", BoundingBox(0.22, 0.45, 0.32, 0.55)),
            ControlSnapshot("control:node_3", "node_3", BoundingBox(0.50, 0.45, 0.60, 0.55)),
        ),
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent.gateway = Mock(model="fake")
    agent._action_surface = None
    agent._last_execution_failure = {
        "code": "blocked",
        "dispatch": "ok",
        "pixel_changed": False,
        "intent": {"kind": "click_control", "control_id": "control:node_0"},
    }
    agent._last_intent = {"kind": "click_control", "control_id": "control:node_0"}
    agent._last_reason_state_key = ""
    agent._unchanged_failure_count = 0
    agent._empty_action_surface_key = ""
    agent._empty_action_surface_count = 0
    agent._decision_softlock = False
    agent._session_flags = []
    agent._pending_exec_error = None
    agent._force_perceive = False
    agent._blocked_controls = set()
    agent._blocked_controls_screen = "map"
    agent._host_stats = {"screen": "map"}
    agent._obs_metrics = {"screen": "map"}
    agent._v2_control_objects = {
        "control:node_0": SimpleNamespace(label="node_0", x=0.27, y=0.50, object_id="control:node_0"),
        "control:node_3": SimpleNamespace(label="node_3", x=0.55, y=0.50, object_id="control:node_3"),
    }
    agent._screen_objects = SimpleNamespace()
    agent.config = SimpleNamespace(reason_fallback_model=None)
    monkeypatch.setattr(agent, "_rescan_scene", lambda **_k: 0.0)
    monkeypatch.setattr(agent, "_combat_screen_now", lambda: False)

    def _decide(self, snap):
        return ActDecision(
            snap.snapshot_id,
            ClickControlIntent(control_id="control:node_3"),
            "fake",
            None,
            1.0,
        )

    monkeypatch.setattr("loop.agent.loop.ActReasoner.decide", _decide)
    monkeypatch.setattr("loop.agent.loop.ActReasoner.__init__", lambda self, **_k: None)
    monkeypatch.setattr(
        "loop.agent.loop.current_snapshot_identity",
        lambda _store: {"snapshot_id": "f", "run_id": "r", "step_id": 1},
    )

    agent._run_v2_act(snapshot)
    agent._run_v2_act(snapshot)
    parsed, _ = agent._run_v2_act(snapshot)
    assert agent._decision_softlock is False
    assert "control:node_0" in agent._blocked_controls
    assert parsed.get("action") == "click_control"


def test_stale_control_binding_does_not_blacklist_unexecuted_control(monkeypatch) -> None:
    from loop.agent.decision import BoundingBox, ControlSnapshot

    snapshot = TurnSnapshot(
        snapshot_id="map-1", screen="map", turn=None, hp=73, block=None, energy=None,
        controls=(ControlSnapshot("control:node_0", "node_0", BoundingBox(.2, .4, .3, .5)),),
    )
    agent = CollectAgent.__new__(CollectAgent)
    agent._last_execution_failure = {
        "code": "stale-control-binding",
        "intent": {"kind": "click_control", "control_id": "control:node_0"},
    }
    agent._last_intent = {"kind": "click_control", "control_id": "control:node_0"}
    agent._blocked_controls = set()
    agent._blocked_controls_screen = "map"
    monkeypatch.setattr(agent, "_current_surface_screen", lambda: "map")

    assert agent._replan_after_control_failure(snapshot, ()) is False
    assert agent._blocked_controls == set()
