from loop.agent.perceive import SceneDescription
from loop.agent.prompts import REASON_TURN_SYSTEM, scene_perceive_system


def _combat_payload(label: str) -> dict:
    row = {
        "label": label,
        "role": "enemy",
        "confidence": "high",
        "inspectable": False,
        "bbox": [100, 100, 200, 300],
    }
    return {
        "screen": "combat",
        "controls": [row],
        "visible_text": [],
        "legal_actions": [],
        "unresolved": [],
        "combat": {"active": True, "enemies": [label]},
        "hud": [],
        "hand_cards": [],
        "characters": [],
        "enemies": [row],
    }


def test_unobserved_canonical_enemy_is_downgraded() -> None:
    desc = SceneDescription.from_parsed(_combat_payload("Jaw Worm"), hud={"screen": "combat"})

    assert all(row["label"] == "unknown_enemy" for row in desc.enemies)
    assert all(row["name_source"] == "unresolved" for row in desc.enemies)
    assert "Jaw Worm" not in desc.as_text()


def test_observed_enemy_name_is_retained_with_provenance() -> None:
    desc = SceneDescription.from_parsed(
        _combat_payload("Jaw Worm"),
        hud={"screen": "combat", "ocr": "Jaw Worm"},
    )

    assert desc.enemies[0]["label"] == "Jaw Worm"
    assert desc.enemies[0]["name_source"] == "ocr"


def test_prompt_examples_contain_no_canonical_enemy_names() -> None:
    assert "Jaw Worm" not in scene_perceive_system()
    assert "Cultist" not in scene_perceive_system()
    assert "Jaw Worm" not in REASON_TURN_SYSTEM
    assert "Cultist" not in REASON_TURN_SYSTEM
