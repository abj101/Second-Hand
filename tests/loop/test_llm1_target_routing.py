from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from loop.agent.loop import CollectAgent, CollectConfig
from loop.agent.perceive import (
    SceneDescription,
    perceive_scene,
    revalidate_grounded_perception,
    validate_perceive_schema,
)
from loop.agent.prompts import (
    SCENE_PERCEIVE_CANONICAL_EXAMPLE,
    allowed_sets_from_controls,
    instance_ids_from_controls,
    reason_controls_for_turn,
    validate_choice,
)
from loop.log.schema import ActionLog
from loop.memory.screen_objects import ScreenObjectStore
from loop.model.gateway import ModelCall
from loop.os.aim import AimGuide, AimMark, action_from_labels, steer_to_labeled
from loop.os.capture import EncodedFrame
from loop.mcp.tools.targeting import object_role, targeting_options


def _frame() -> EncodedFrame:
    return EncodedFrame(
        data=b"abc",
        mime="image/jpeg",
        width=100,
        height=100,
        jpeg_quality=90,
        longest_side=100,
        original_width=100,
        original_height=100,
    )


_GEMMA_ABBREV = {
    "screen": "combat",
    "controls": [{"label": "Strike"}],
    "visible_text": [],
    "legal_actions": [],
    "unresolved": [],
    "hand_cards": [{"label": "Strike", "bbox": [200, 780, 320, 980]}],
    "characters": [{"label": "Ironclad", "bbox": [80, 420, 220, 780]}],
    "enemies": [{"label": None, "bbox": [680, 500, 820, 720]}],
}


def test_canonical_example_validates() -> None:
    assert validate_perceive_schema(SCENE_PERCEIVE_CANONICAL_EXAMPLE) == []


def test_abbreviated_first_pass_has_schema_errors() -> None:
    errors = validate_perceive_schema(_GEMMA_ABBREV)
    assert any("inspectable" in item or "playable" in item or "role" in item or "name" in item for item in errors)


def test_perceive_scene_retries_abbreviated_then_accepts() -> None:
    class _SeqGateway:
        def __init__(self) -> None:
            self.users: list[str] = []

        def complete_json(self, **kwargs):  # type: ignore[no-untyped-def]
            self.users.append(str(kwargs.get("user") or ""))
            if len(self.users) == 1:
                return ModelCall(json.dumps(_GEMMA_ABBREV), _GEMMA_ABBREV, 1.0, "fake")
            return ModelCall(
                json.dumps(SCENE_PERCEIVE_CANONICAL_EXAMPLE),
                SCENE_PERCEIVE_CANONICAL_EXAMPLE,
                2.0,
                "fake",
            )

    gw = _SeqGateway()
    desc, call = perceive_scene(gw, _frame())
    assert desc.schema_valid
    assert desc.llm1_first_pass_valid is False
    assert desc.llm1_final_schema_valid is True
    assert desc.llm1_schema_attempts == 2
    assert desc.llm1_attempts[0]["accepted"] is False
    assert desc.llm1_attempts[1]["accepted"] is True
    assert "inspectable" in gw.users[1] or "Fix ONLY" in gw.users[1]
    assert call.text == json.dumps(SCENE_PERCEIVE_CANONICAL_EXAMPLE)
    assert desc.llm1_raw_text == call.text
    grounded = json.dumps(desc.raw, separators=(",", ":"))
    assert call.text != grounded or desc.validated_perception == SCENE_PERCEIVE_CANONICAL_EXAMPLE


def test_grounded_strike_cannot_stay_unresolved() -> None:
    desc = SceneDescription(
        hand_cards=[{"label": "Strike", "name": "Strike", "name_source": "unresolved", "role": "card"}],
    )
    errors = revalidate_grounded_perception(desc)
    assert errors
    assert desc.hand_cards[0]["label"] == "unknown_card"
    assert desc.hand_cards[0]["name_source"] == "unresolved"


def test_mark_9_rejected_even_if_in_allowed_targets() -> None:
    result = validate_choice(
        {"choice": "Strike", "target": "mark_9"},
        {"Strike"},
        {"mark_9", "Jaw Worm"},
        {},
        attack_cards={"Strike"},
        target_ids={"enemy_1"},
    )
    assert result["type"] in {"force_rescan", "abort"}
    assert "mark" in str(result.get("reason") or "").lower()


def test_duplicate_strikes_require_choice_id() -> None:
    result = validate_choice(
        {"choice": "Strike", "target": "Jaw Worm", "target_id": "enemy_1"},
        {"Strike"},
        {"Jaw Worm"},
        {},
        attack_cards={"Strike"},
        choice_ids={"card_1", "card_2"},
        target_ids={"enemy_1"},
        duplicate_choice_labels={"strike"},
    )
    assert result["type"] == "force_rescan"
    assert "choice_id" in str(result.get("reason") or "")


def test_choice_id_and_target_id_accepted() -> None:
    result = validate_choice(
        {
            "choice": "Strike",
            "choice_id": "card_1",
            "target": "unidentified_enemy",
            "target_id": "enemy_1",
        },
        {"Strike"},
        {"unidentified_enemy"},
        {},
        attack_cards={"Strike"},
        choice_ids={"card_1"},
        target_ids={"enemy_1"},
    )
    assert result["type"] == "click"
    assert result["choice_id"] == "card_1"
    assert result["target_id"] == "enemy_1"


def test_legacy_strike_jaw_worm_without_ids_still_validates() -> None:
    result = validate_choice(
        {"choice": "Strike", "target": "Jaw Worm"},
        {"Strike"},
        {"Jaw Worm"},
        {},
        attack_cards={"Strike"},
    )
    assert result["type"] == "click"


def test_action_from_labels_rejects_mark_token() -> None:
    image = Image.new("RGB", (64, 64))
    guide = AimGuide.from_marks(
        image,
        [
            AimMark(1, 0.4, 0.85, 0.3, 0.78, 0.5, 0.95, "Strike", object_id="card_1", role="card"),
            AimMark(9, 0.75, 0.63, 0.65, 0.56, 0.85, 0.70, "unidentified_enemy", object_id="enemy_1", role="enemy"),
        ],
    )
    assert action_from_labels(guide, choice="Strike", target_label="mark_9") is None


def test_action_from_labels_binds_stable_ids() -> None:
    image = Image.new("RGB", (64, 64))
    guide = AimGuide.from_marks(
        image,
        [
            AimMark(4, 0.4, 0.85, 0.3, 0.78, 0.5, 0.95, "Strike", object_id="card_1", role="card"),
            AimMark(9, 0.75, 0.63, 0.65, 0.56, 0.85, 0.70, "unidentified_enemy", object_id="enemy_1", role="enemy"),
        ],
    )
    action = action_from_labels(guide, choice="card_1", target_label="enemy_1")
    assert action is not None
    assert action.mark == 4
    assert action.target == 9


def test_rebind_prefers_object_id_not_old_mark_number() -> None:
    image = Image.new("RGB", (64, 64))
    before = AimMark(9, 0.75, 0.63, 0.65, 0.56, 0.85, 0.70, "unidentified_enemy", object_id="enemy_1", role="enemy")
    fresh = AimMark(14, 0.76, 0.64, 0.66, 0.57, 0.86, 0.71, "unidentified_enemy", object_id="enemy_1", role="enemy")
    stale = AimMark(9, 0.40, 0.85, 0.30, 0.78, 0.50, 0.95, "Strike", object_id="card_1", role="card")
    agent = CollectAgent.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(image, [stale, fresh])
    rebound = agent._rebind_target(before)
    assert rebound is not None
    assert rebound.id == 14
    assert rebound.object_id == "enemy_1"


def test_rebind_cancels_when_object_id_missing() -> None:
    image = Image.new("RGB", (64, 64))
    before = AimMark(9, 0.75, 0.63, 0.65, 0.56, 0.85, 0.70, "Jaw Worm", object_id="enemy_1", role="enemy")
    agent = CollectAgent.__new__(CollectAgent)
    agent._aim = AimGuide.from_marks(
        image,
        [AimMark(2, 0.4, 0.85, 0.3, 0.78, 0.5, 0.95, "Strike", object_id="card_1", role="card")],
    )
    assert agent._rebind_target(before) is None


def test_steer_cannot_turn_card_into_enemy(tmp_path: Path) -> None:
    image = Image.new("RGB", (64, 64))
    card = AimMark(4, 0.4, 0.85, 0.3, 0.78, 0.5, 0.95, "Strike", object_id="card_1", role="card")
    enemy = AimMark(9, 0.75, 0.63, 0.65, 0.56, 0.85, 0.70, "Jaw Worm", object_id="enemy_1", role="enemy")
    guide = AimGuide.from_marks(image, [card, enemy])
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig()
    agent._host_stats = {"screen": "combat"}
    agent._obs_metrics = {"screen": "combat"}
    store = ScreenObjectStore(tmp_path / "objects.db")
    store.save_frame(run_id="r", step_id=1, marks=[card, enemy], screen="combat")
    agent._screen_objects = store
    before = ActionLog(mark=4, click_x=0.4, click_y=0.85, duration_ms=400, source_role="card", source_object_id="card_1")
    steered = steer_to_labeled(before, guide, thought="hit Jaw Worm", choice="Jaw Worm")
    kept = agent._preserve_steered_identity(before, steered)
    assert kept.mark == 4
    store.close()


def test_player_and_hp_anchor_excluded_from_enemies(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "inv.db")
    marks = [
        AimMark(1, 0.2, 0.55, 0.1, 0.4, 0.3, 0.7, "player", object_id="", role="player"),
        AimMark(2, 0.75, 0.63, 0.65, 0.56, 0.85, 0.70, "unidentified_enemy", object_id="", role="enemy"),
        AimMark(3, 0.75, 0.18, 0.70, 0.15, 0.80, 0.21, "12 / 40", object_id="", role=""),
        AimMark(4, 0.40, 0.85, 0.30, 0.78, 0.50, 0.95, "Strike", object_id="", role="card"),
    ]
    store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    labels = {obj.label for obj in store.enemies()}
    assert "Ironclad" not in labels
    assert "12 / 40" not in labels
    assert any("unidentified" in obj.label or obj.role == "enemy" for obj in store.enemies())
    assert object_role(store.by_id(1), entity_targets_ok=True) == "player"
    opts = targeting_options(store, choice="Strike", entity_targets_ok=True)
    target_labels = {row["label"] for row in opts.get("targets") or []}
    assert "player" in target_labels
    assert "12 / 40" not in target_labels
    assert store.by_id(1).object_id == "player_1"
    assert store.by_id(1).role == "player"
    ids = {obj.object_id for obj in store.objects if obj.object_id}
    assert not any(item.startswith("mark_") for item in ids)
    store.close()


def test_instance_ids_from_controls_and_named_enemy_acts() -> None:
    controls = [
        {"label": "Strike", "role": "card", "confidence": "high", "entity_id": "card_1"},
        {"label": "Jaw Worm", "role": "enemy", "confidence": "high", "entity_id": "enemy_1"},
    ]
    choice_ids, target_ids, dups = instance_ids_from_controls(controls)
    assert choice_ids == {"card_1"}
    assert target_ids == {"enemy_1"}
    assert not dups
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    scene = SceneDescription(
        screen="combat",
        controls=controls,
        enemies=[{"label": "Jaw Worm", "role": "enemy", "name_source": "ocr", "bbox": [680, 500, 820, 720]}],
    )
    assert agent._two_stage_reason_route(scene)[0] == "act"


def test_hud_icon_is_neither_discovery_nor_target() -> None:
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    scene = SceneDescription(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": ":Icon", "role": "icon", "confidence": "low", "inspectable": "true"},
        ],
        enemies=[{"label": "Jaw Worm", "role": "enemy", "name_source": "ocr", "bbox": [680, 500, 820, 720]}],
    )
    assert agent._two_stage_reason_route(scene)[0] == "act"
    assert not any(row["label"] in {":Icon", "icon"} and row.get("blocker") for row in agent._entity_route_facts)


def test_typed_player_is_not_explorer_at_arbitrary_layout() -> None:
    from loop.agent.entities import is_player_row

    player = {
        "label": "player",
        "role": "player",
        "bbox": [200, 450, 300, 690],
    }
    enemy = {
        "label": "Nibbit",
        "role": "enemy",
        "name_source": "ocr",
        "bbox": [680, 560, 820, 720],
    }
    assert is_player_row(player)
    assert not is_player_row(enemy)
    slime = {"label": "Slime", "role": "", "bbox": [200, 300, 400, 500]}
    hud = {"label": ":Icon", "role": "", "bbox": [10, 10, 150, 90]}
    assert not is_player_row(slime)
    assert not is_player_row(hud)
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    scene = SceneDescription(
        screen="combat",
        characters=[player],
        enemies=[enemy],
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    assert agent._two_stage_reason_route(scene)[0] == "act"
    assert not any(row.get("player") and row.get("blocker") for row in agent._entity_route_facts)


def test_left_lane_unknown_excluded_from_enemies_and_inventory(tmp_path: Path) -> None:
    from loop.agent.discovery import inventory_unknowns
    from loop.agent.prompts import reason_controls_for_turn, allowed_sets_from_controls

    store = ScreenObjectStore(tmp_path / "inv.db")
    marks = [
        AimMark(11, 0.24, 0.58, 0.20, 0.47, 0.30, 0.69, "unknown_character", object_id="", role="unknown_character"),
        AimMark(9, 0.74, 0.63, 0.68, 0.56, 0.80, 0.69, "Nibbit", object_id="", role="enemy"),
    ]
    store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    labels = {obj.label for obj in store.enemies()}
    assert "unknown_character" not in labels
    assert "Nibbit" in labels
    queued = inventory_unknowns(store.objects, energy=3)
    assert all(item.label != "unknown_character" for item in queued)
    store.close()
    controls = reason_controls_for_turn(
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "unknown_character", "role": "character", "confidence": "high", "bbox": [200, 450, 300, 690]},
            {"label": "Nibbit", "role": "enemy", "confidence": "high"},
        ]
    )
    names = {row["label"] for row in controls}
    assert "unknown_character" not in names
    assert "player" in names
    assert "Strike" in names
    allowed, targets = allowed_sets_from_controls(controls)
    assert "unknown_character" not in allowed
    assert "player" in targets
    assert "Nibbit" in targets


def test_left_lane_role_enemy_still_player_and_hp_blob_is_not_enemy_id(tmp_path: Path) -> None:
    from loop.agent.discovery import inventory_unknowns
    from loop.mcp.tools.targeting import object_role

    store = ScreenObjectStore(tmp_path / "inv.db")
    marks = [
        AimMark(12, 0.25, 0.58, 0.20, 0.47, 0.30, 0.69, "player", object_id="", role="player"),
        AimMark(10, 0.75, 0.35, 0.70, 0.32, 0.80, 0.38, "unidentified_enemy", object_id="", role=""),
        AimMark(11, 0.76, 0.64, 0.70, 0.56, 0.82, 0.72, "unknown_character_1", object_id="", role="enemy"),
    ]
    store.save_frame(run_id="r", step_id=1, marks=marks, screen="combat")
    by_y = {round(obj.y, 2): obj for obj in store.objects}
    sprite = by_y[0.64]
    hp = by_y[0.35]
    player = by_y[0.58]
    assert sprite.object_id == "enemy_1"
    assert not (hp.object_id or "").startswith("enemy_")
    assert object_role(hp, entity_targets_ok=True) != "enemy"
    assert object_role(player, entity_targets_ok=True) == "player"
    assert player.label == "player"
    assert player.object_id == "player_1"
    queued = inventory_unknowns(store.objects, energy=3)
    assert [item.object_id for item in queued] == ["enemy_1"]
    store.close()


def test_bboxless_unknown_character_is_not_choice_or_explore() -> None:
    from loop.agent.prompts import inspectable_targets_from_controls

    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    player = {"label": "unknown_character", "role": "unknown_character", "confidence": "high"}
    enemy = {
        "label": "Nibbit",
        "role": "enemy",
        "name_source": "ocr",
        "confidence": "high",
        "bbox": [680, 560, 820, 720],
    }
    scene = SceneDescription(
        screen="combat",
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            player,
        ],
        characters=[player],
        enemies=[enemy],
    )
    assert agent._two_stage_reason_route(scene)[0] == "act"
    assert not any(row.get("blocker") for row in agent._entity_route_facts if row.get("player"))
    controls = reason_controls_for_turn(
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            player,
            enemy,
        ]
    )
    names = {row["label"] for row in controls}
    assert "unknown_character" not in names
    assert "Strike" in names
    allowed, targets = allowed_sets_from_controls(controls)
    assert "unknown_character" not in allowed
    assert "unknown_character" not in inspectable_targets_from_controls(controls)


def test_right_lane_unknown_character_with_bbox_still_explores() -> None:
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True)
    scene = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
        enemies=[
            {
                "label": "unknown_character",
                "role": "unknown_character",
                "inspectable": "true",
                "bbox": [680, 560, 820, 720],
            }
        ],
    )
    assert agent._two_stage_reason_route(scene) == (
        "explore",
        "epistemic-blocker:unknown_character",
    )


def test_identity_unresolved_after_two_inspects_allows_act_fallback(tmp_path: Path) -> None:
    store = ScreenObjectStore(tmp_path / "inv.db")
    enemy = AimMark(
        9, 0.74, 0.63, 0.68, 0.56, 0.80, 0.69, "unidentified_enemy",
        object_id="enemy_1", role="enemy",
    )
    card = AimMark(
        4, 0.40, 0.85, 0.30, 0.78, 0.50, 0.95, "Strike",
        object_id="card_1", role="card",
    )
    store.save_frame(run_id="r", step_id=1, marks=[enemy, card], screen="combat")
    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(identify_before_act=True, burst_ms=400)
    agent._discovery_attempts = {"enemy_1": 2, "unidentified_enemy": 2}
    agent._selected = None
    agent._obs_metrics = {"energy": 3}
    agent._screen_objects = store
    scene = SceneDescription(
        screen="combat",
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
        enemies=[
            {
                "label": "unidentified_enemy",
                "role": "enemy",
                "entity_id": "enemy_1",
                "inspectable": "true",
                "bbox": [680, 560, 820, 720],
            }
        ],
    )
    route, reason = agent._two_stage_reason_route(scene)
    assert route == "act"
    assert reason == "identity-unresolved-fallback"
    assert any(row["rule"] == "identity-unresolved" for row in agent._entity_route_facts)
    assert agent._next_discovery_action() is None
    store.close()


def test_enemy_1_1_resolves_to_sprite_not_hp_ocr(tmp_path: Path) -> None:
    from loop.os.frame_model import FrameModel, FrameObject
    from loop.state.game_state import reconcile

    hud = AimMark(8, 0.75, 0.35, 0.70, 0.32, 0.80, 0.38, "unidentified_enemy", object_id="", role="")
    sprite = AimMark(11, 0.76, 0.64, 0.70, 0.56, 0.82, 0.72, "unknown_character_1", object_id="", role="enemy")
    card = AimMark(1, 0.29, 0.95, 0.22, 0.88, 0.36, 1.0, "Strike", object_id="", role="card")
    store = ScreenObjectStore(tmp_path / "obj.db")
    store.save_frame(run_id="run1", step_id=1, marks=[hud, sprite, card], screen="combat")
    frame = FrameModel(
        objects=[
            FrameObject(8, "unidentified_enemy", "interactive", role="target", x=.75, y=.35, x0=.70, y0=.32, x1=.80, y1=.38),
            FrameObject(11, "unknown_character_1", "interactive", role="unknown_character", x=.76, y=.64, x0=.70, y0=.56, x1=.82, y1=.72),
            FrameObject(1, "Strike", "interactive", role="card", x=.29, y=.95, x0=.22, y0=.88, x1=.36, y1=1.0),
        ],
        screen_hint="combat",
    )
    stats = {
        "screen": "combat", "activity": "combat", "turn": "ours", "ocr_confidence": "high",
        "player": {"hp": 80, "hp_max": 80, "energy": 3, "energy_max": 3},
        "enemies": [{"hp": 45, "hp_max": 45}],
    }
    state = reconcile(None, step=1, host_stats=stats, frame=frame)
    agent = CollectAgent.__new__(CollectAgent)
    agent.session_id = "run1"
    agent._step_id = 1
    agent._screen_objects = store
    agent._game_state = state
    located = next(enemy for enemy in state.enemies if enemy.bbox.known)
    hit = agent._current_frame_object(located.id)
    assert hit is not None
    assert abs(float(hit.y) - 0.64) < 0.05
    assert object_role(hit, entity_targets_ok=True) == "enemy"
    card_hit = agent._current_frame_object("card_1")
    assert card_hit is not None
    assert card_hit.mark_id == 1
    store.close()


def test_empty_combat_chord_skips_labeled_hand_click() -> None:
    from types import SimpleNamespace

    agent = CollectAgent.__new__(CollectAgent)
    agent.config = CollectConfig(architecture="two_stage")
    agent.fixture = SimpleNamespace(pace="turn", allows_entity_targets=None)
    agent._host_stats = {"screen": "combat"}
    agent._obs_metrics = {"screen": "combat"}
    empty = ActionLog(duration_ms=400)
    assert agent._skip_labeled_combat_fill(empty) is True
    filled = ActionLog(mark=1, click_x=0.29, click_y=0.95, duration_ms=400)
    assert agent._skip_labeled_combat_fill(filled) is False
    agent.config = CollectConfig(architecture="overlay")
    assert agent._skip_labeled_combat_fill(empty) is False
