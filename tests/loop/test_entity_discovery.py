from __future__ import annotations

from loop.agent.entities import EntityTracker, is_unknown_entity_label
from loop.agent.perceive import SceneDescription, validate_combat_perceive
from loop.os.aim import action_inspect_label, needs_tooltip
from loop.agent.prompts import reason_turn_user_prompt, reason_state_from_host, reason_hud_stats

# Sprite-band bbox (0–1000 grid). Center ≈ (0.75, 0.64) — clickable body, not HP plate.
_ENEMY_BBOX = "680,560,820,720"
_ENEMY_BBOX_LIST = [680, 560, 820, 720]
_ENEMY_CX, _ENEMY_CY = 0.75, 0.64


def test_unknown_entity_label_patterns() -> None:
    assert is_unknown_entity_label("unknown_character")
    assert is_unknown_entity_label("unknown_character_1")
    assert is_unknown_entity_label("unidentified_enemy")
    assert is_unknown_entity_label("unknown_enemy")
    assert not is_unknown_entity_label("Jaw Worm")


def test_entity_tracker_assigns_persistent_id() -> None:
    tracker = EntityTracker()
    rows = [
        {
            "label": "unknown_character",
            "role": "unknown_character",
            "confidence": "low",
            "bbox": _ENEMY_BBOX,
            "inspectable": True,
        }
    ]
    out = tracker.update_rows(rows)
    assert out[0]["label"] == "unknown_character_1"
    assert out[0]["entity_id"] == "unknown_character_1"
    assert out[0]["inspectable"] == "true"

    # Same bbox next frame → same id
    out2 = tracker.update_rows(rows)
    assert out2[0]["entity_id"] == "unknown_character_1"


def test_entity_tracker_does_not_identify_from_label_only_object_library() -> None:
    from loop.memory.lexicon import MarkLexicon

    lexicon = MarkLexicon()
    lexicon.remember("combat", _ENEMY_CX, _ENEMY_CY, "Cultist", source="hover")
    tracker = EntityTracker()
    rows = [
        {
            "label": "unknown_character",
            "role": "unknown_character",
            "confidence": "low",
            "bbox": _ENEMY_BBOX,
        }
    ]
    out = tracker.update_rows(rows, lexicon=lexicon, screen="combat")
    assert not any(r.get("label") == "Cultist" for r in out)
    assert not tracker.entities[0].identified
    assert tracker.entities[0].canonical_name == ""


def test_entity_tracker_merge_into_scene() -> None:
    tracker = EntityTracker()
    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[
            {
                "label": "unknown_character",
                "role": "unknown_character",
                "confidence": "low",
                "bbox": _ENEMY_BBOX,
            }
        ],
        controls=[],
    )
    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]}
    tracker.merge_into_scene(desc, host, lexicon=None)
    labels = [c.get("label") for c in desc.controls]
    assert "unknown_character_1" in labels


def test_entity_tracker_keeps_stale_phantom_out_of_current_scene() -> None:
    """A retained upper animation row must not become an ACT target next frame."""
    tracker = EntityTracker()
    tracker.update_rows([{
        "label": "unidentified_enemy",
        "role": "enemy",
        "confidence": "low",
        "bbox": [640, 195, 760, 355],
    }])
    stale_id = tracker.entities[0].entity_id

    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[{
            "label": "unknown_character",
            "role": "unknown_character",
            "confidence": "low",
            "bbox": _ENEMY_BBOX_LIST,
        }],
        controls=[],
    )
    tracker.merge_into_scene(desc, {"screen": "combat"}, lexicon=None)

    live_ids = {
        ent.entity_id for ent in tracker.entities
        if ent.last_seen_cycle == tracker._cycle
    }
    assert {row["entity_id"] for row in desc.enemies} == live_ids
    assert stale_id not in {row["entity_id"] for row in desc.enemies}
    assert stale_id in {ent.entity_id for ent in tracker.entities}


def test_combat_validate_accepts_unknown_character_bbox() -> None:
    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44, "label": None}]}
    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[
            {
                "label": "unknown_character_1",
                "role": "unknown_character",
                "confidence": "low",
                "bbox": _ENEMY_BBOX,
                "entity_id": "unknown_character_1",
            }
        ],
        controls=[
            {
                "label": "unknown_character_1",
                "role": "unknown_character",
                "confidence": "low",
                "bbox": _ENEMY_BBOX,
            }
        ],
    )
    assert not validate_combat_perceive(desc, host)


def test_reason_prompt_lists_inspectable_targets() -> None:
    prompt = reason_turn_user_prompt(
        state=reason_state_from_host(screen="combat", turn="ours"),
        hud_stats=reason_hud_stats(),
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {
                "label": "unknown_character_1",
                "role": "unknown_character",
                "confidence": "low",
                "inspectable": "true",
                "bbox": [680, 560, 820, 720],
            },
        ],
    )
    assert "Inspectable targets" in prompt
    assert "unknown_character_1" in prompt


def test_needs_tooltip_for_unknown_entity() -> None:
    assert needs_tooltip("unknown_character_1")
    assert needs_tooltip("unidentified_enemy")


def test_action_inspect_label_finds_mark() -> None:
    from loop.os.aim import AimGuide, AimMark
    from PIL import Image

    img = Image.new("RGB", (100, 100))
    marks = (
        AimMark(
            id=1,
            x=0.55,
            y=0.32,
            x0=0.48,
            y0=0.24,
            x1=0.62,
            y1=0.40,
            label="unknown_character_1",
            score=0.7,
        ),
    )
    guide = AimGuide.from_marks(img, marks)
    action = action_inspect_label(guide, "unknown_character_1")
    assert action is not None
    assert action.hover is True
    assert action.mark == 1


def test_entity_tracker_respects_llm_id_field() -> None:
    tracker = EntityTracker()
    rows = [
        {
            "id": "unknown_character_1",
            "label": None,
            "role": "unknown_character",
            "confidence": "low",
            "bbox": _ENEMY_BBOX_LIST,
            "inspectable": True,
        }
    ]
    out = tracker.update_rows(rows)
    assert out[0]["entity_id"] == "unknown_character_1"
    assert out[0]["label"] == "unknown_character_1"


def test_pending_inspect_target_after_object_library_identify() -> None:
    from loop.agent.entities import pending_inspect_target
    from loop.memory.lexicon import MarkLexicon

    tracker = EntityTracker()
    rows = [
        {
            "label": "unknown_character",
            "role": "unknown_character",
            "confidence": "low",
            "bbox": _ENEMY_BBOX,
        }
    ]
    tracker.update_rows(rows)
    assert pending_inspect_target(tracker) == "unknown_character_1"

    lexicon = MarkLexicon()
    lexicon.remember("combat", _ENEMY_CX, _ENEMY_CY, "Jaw Worm", source="hover")
    tracker.update_rows(rows, lexicon=lexicon, screen="combat")
    assert pending_inspect_target(tracker) == "unknown_character_1"
    assert not tracker.entities[0].identified
    assert tracker.entities[0].canonical_name == ""


def test_pending_inspect_uses_typed_role_not_layout() -> None:
    from loop.agent.entities import TrackedEntity, pending_inspect_target

    tracker = EntityTracker(entities=[
        TrackedEntity(
            entity_id="unknown_character_1",
            label="unknown_character",
            role="unknown_character",
            cx=0.24,
            cy=0.58,
            bbox=(0.20, 0.47, 0.30, 0.69),
            inspectable=True,
            identified=False,
        ),
        TrackedEntity(
            entity_id="unknown_character_2",
            label="unidentified_enemy",
            role="enemy",
            cx=0.74,
            cy=0.63,
            bbox=(0.68, 0.56, 0.80, 0.69),
            inspectable=True,
            identified=False,
        ),
    ])
    assert pending_inspect_target(tracker) == "unknown_character_1"


def test_inspect_flow_does_not_relabel_enemy_from_label_only_memory() -> None:
    """An untyped memory label cannot identify a current-frame enemy."""
    from loop.memory.lexicon import MarkLexicon

    tracker = EntityTracker()
    bbox_row = {
        "label": "unknown_character",
        "role": "unknown_character",
        "confidence": "low",
        "bbox": _ENEMY_BBOX,
        "inspectable": True,
    }
    tracker.update_rows([bbox_row])
    assert tracker.entities[0].entity_id == "unknown_character_1"

    lexicon = MarkLexicon()
    lexicon.remember("combat", _ENEMY_CX, _ENEMY_CY, "Jaw Worm", source="hover")
    merged = tracker.update_rows([bbox_row], lexicon=lexicon, screen="combat")
    assert not any(r.get("label") == "Jaw Worm" for r in merged)
    assert tracker.entities[0].entity_id == "unknown_character_1"
    assert tracker.entities[0].role == "unknown_character"
    assert tracker.entities[0].inspectable


def test_player_display_label_keeps_class_names() -> None:
    from loop.agent.entities import player_display_label

    assert player_display_label("unknown_character") == "player"
    assert player_display_label("character") == "player"
    assert player_display_label("Ironclad") == "Ironclad"
    assert player_display_label("The Silent") == "The Silent"


def test_player_row_excluded_from_enemy_discovery() -> None:
    from loop.agent.entities import is_enemy_discovery_row

    assert not is_enemy_discovery_row(
        {"label": "player", "role": "character", "bbox": "100,500,250,800"}
    )
    assert is_enemy_discovery_row(
        {
            "label": "unknown_character",
            "role": "unknown_character",
            "bbox": "100,20,250,70",  # layout is not a role signal
        }
    )
    assert not is_enemy_discovery_row(
        {
            "label": "unidentified_enemy",
            "role": "enemy",
        }
    )
    assert is_enemy_discovery_row(
        {
            "label": "unidentified_enemy",
            "role": "enemy",
            "bbox": "680,200,820,400",
        }
    )
    assert is_enemy_discovery_row(
        {
            "label": "unidentified_enemy",
            "role": "enemy",
            "bbox": _ENEMY_BBOX,
        }
    )
    assert is_enemy_discovery_row(
        {
            "label": "unknown_character_1",
            "role": "unknown_character",
            "bbox": [690, 580, 810, 710],
        }
    )


def test_update_rows_skips_bboxless_enemy() -> None:
    tracker = EntityTracker()
    out = tracker.update_rows(
        [{"label": "unidentified_enemy", "role": "enemy", "confidence": "low"}]
    )
    assert out == []
    assert tracker.entities == []


def test_merge_into_scene_drops_bboxless_anonymous_clones() -> None:
    tracker = EntityTracker()
    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[
            {
                "label": "unknown_character_1",
                "role": "unknown_character",
                "confidence": "low",
                "bbox": [690, 580, 810, 710],
            }
        ],
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "unidentified_enemy", "role": "enemy", "confidence": "low"},
        ],
    )
    tracker.merge_into_scene(desc, {"screen": "combat"}, lexicon=None)
    labels = [c.get("label") for c in desc.controls]
    assert "Strike" in labels
    assert "unidentified_enemy" not in labels
    assert any(is_unknown_entity_label(str(lab or "")) for lab in labels)


def test_identify_ignores_ocr_junk_cjk() -> None:
    tracker = EntityTracker()
    rows = [
        {
            "label": "unknown_character",
            "role": "unknown_character",
            "confidence": "low",
            "bbox": [700, 560, 820, 720],
            "inspectable": True,
        }
    ]
    tracker.update_rows(rows)
    assert tracker.entities
    eid = tracker.entities[0].entity_id
    tracker.identify(eid, "12入")
    assert tracker.entities[0].identified is False
    assert not tracker.entities[0].canonical_name
    tracker.identify(eid, "Jaw Worm")
    assert tracker.entities[0].identified is True
    assert tracker.entities[0].canonical_name == "Jaw Worm"


_SECOND_ENEMY_BBOX = "180,560,320,720"
_HOST_ONE = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]}


def _anon_row(bbox: str) -> dict:
    return {
        "label": "unknown_character",
        "role": "unknown_character",
        "confidence": "low",
        "bbox": bbox,
        "inspectable": True,
    }


def test_tracker_prunes_entities_unseen_two_cycles() -> None:
    tracker = EntityTracker()
    tracker.update_rows([_anon_row(_ENEMY_BBOX)])
    eid = tracker.entities[0].entity_id
    tracker.identify(eid, "Nibbit")
    assert tracker.entities[0].identified

    tracker.update_rows([], host_stats=_HOST_ONE)
    assert len(tracker.entities) == 1
    assert tracker.entities[0].entity_id == eid

    tracker.update_rows([], host_stats=_HOST_ONE)
    assert tracker.entities == []
    assert "Nibbit" in tracker._identified_memory

    out = tracker.update_rows([_anon_row(_ENEMY_BBOX)], host_stats=_HOST_ONE)
    assert len(tracker.entities) == 1
    assert tracker.entities[0].identified
    assert tracker.entities[0].canonical_name == "Nibbit"
    assert out[0]["label"] == "Nibbit"
    assert out[0]["inspectable"] == "false"


def test_single_host_enemy_allows_one_blocker_max() -> None:
    tracker = EntityTracker()
    tracker.update_rows([
        _anon_row(_ENEMY_BBOX),
        _anon_row(_SECOND_ENEMY_BBOX),
        _anon_row("480,200,620,360"),
    ])
    assert len(tracker.entities) == 3

    tracker.update_rows([], host_stats=_HOST_ONE)
    unresolved = [ent for ent in tracker.entities if not ent.identified]
    assert len(unresolved) == 1
    assert len(tracker.entities) == 1

    live = EntityTracker()
    live.update_rows(
        [_anon_row(_ENEMY_BBOX), _anon_row(_SECOND_ENEMY_BBOX)],
        host_stats=_HOST_ONE,
    )
    assert len(live.entities) == 2
    assert all(ent.last_seen_cycle == live._cycle for ent in live.entities)
