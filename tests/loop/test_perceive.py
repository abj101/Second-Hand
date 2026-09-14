from __future__ import annotations

from loop.agent.perceive import SceneDescription, normalize_perceive_provenance, validate_perceive_schema


def test_detector_name_source_normalizes_without_losing_enemy_bbox() -> None:
    raw = {
        "screen": "combat",
        "legal_actions": [],
        "unresolved": [],
        "characters": [],
        "controls": [],
        "hud": [],
        "hand_cards": [
            {
                "id": "card_2",
                "label": "Strike",
                "name": "Strike",
                "name_source": "detector",
                "role": "card",
                "confidence": "high",
                "playable": True,
                "bbox": [406, 785, 512, 995],
            }
        ],
        "enemies": [
            {
                "label": None,
                "name": None,
                "name_source": "unresolved",
                "role": "enemy",
                "confidence": "high",
                "inspectable": True,
                "bbox": [690, 580, 820, 710],
            }
        ],
    }
    normalized = normalize_perceive_provenance(raw)
    assert normalized["hand_cards"][0]["name_source"] == "dino"
    assert validate_perceive_schema(normalized) == []
    desc = SceneDescription.from_parsed(normalized)
    assert desc.enemies[0]["bbox"] == "690,580,820,710"

    raw["hand_cards"][0]["name_source"] = "untrusted_producer"
    assert any("invalid name_source" in error for error in validate_perceive_schema(
        normalize_perceive_provenance(raw)
    ))


def test_truncated_json_fails_schema() -> None:
    errors = validate_perceive_schema(None, raw_text='{"screen":"combat"')
    assert errors
