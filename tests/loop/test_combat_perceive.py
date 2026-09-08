from __future__ import annotations

from loop.agent.perceive import SceneDescription, reconcile_combat_screen, validate_combat_perceive


def test_combat_fixture_requires_enemies_when_host_has_hp() -> None:
    """Regression: combat with visible enemy HP must yield enemy labels."""
    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44, "label": None}]}
    errors = validate_combat_perceive(desc, host)
    assert errors

    fixed = SceneDescription(
        screen="combat",
        combat={"active": True},
        controls=[
            {"label": "Strike", "role": "card", "confidence": "high"},
            {"label": "Jaw Worm", "role": "enemy", "confidence": "high"},
        ],
        enemies=[{"label": "Jaw Worm", "role": "enemy", "confidence": "high"}],
    )
    assert not validate_combat_perceive(fixed, host)


def test_unnamed_enemy_is_partial_pass() -> None:
    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44, "label": None}]}
    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        enemies=[
            {
                "label": "unidentified_enemy",
                "role": "enemy",
                "confidence": "low",
                "bbox": "680,580,820,760",
            }
        ],
        controls=[
            {
                "label": "unidentified_enemy",
                "role": "enemy",
                "confidence": "low",
                "bbox": "680,580,820,760",
            }
        ],
    )
    assert not validate_combat_perceive(desc, host)


def test_reconcile_combat_screen_flags_deck_overlay() -> None:
    ocr = "Deck (D) View Upgrades Obtained Card Type Prev Back"
    assert reconcile_combat_screen("combat", ocr=ocr) == "unknown"
    assert reconcile_combat_screen("combat", ocr="End Turn 3/3 Strike") == "combat"


def test_skip_rescan_when_host_ocr_has_enemy_hp() -> None:
    from loop.agent.perceive import (
        inject_host_enemies_into_scene,
        should_skip_combat_perceive_rescan,
    )

    host = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44, "label": None}]}
    errors = ["host OCR shows enemy HP but LLM1 returned no enemies"]
    assert should_skip_combat_perceive_rescan(errors, host)
    assert not should_skip_combat_perceive_rescan(["empty parse"], host)
    assert not should_skip_combat_perceive_rescan(["truncated json"], host)
    assert not should_skip_combat_perceive_rescan(["json_parse_failed"], host)

    desc = SceneDescription(
        screen="combat",
        combat={"active": True},
        controls=[{"label": "Strike", "role": "card", "confidence": "high"}],
    )
    inject_host_enemies_into_scene(desc, host)
    assert not validate_combat_perceive(desc, host)
    assert any(c.get("label") == "unidentified_enemy" for c in desc.controls)
