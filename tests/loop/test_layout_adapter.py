from __future__ import annotations

from PIL import Image

from loop.os.aim import AimMark, build_marks, propose_hand_cards
from loop.os.layout_adapter import (
    generic_layout_adapter,
    legacy_combat_layout_adapter,
)


def _map_board_marks() -> tuple[list[AimMark], list[AimMark]]:
    """Map-like frame: mid-board icons, no combat hand strip."""
    words = [
        AimMark(
            id=1, x=0.38, y=0.75, x0=0.34, y0=0.70, x1=0.42, y1=0.80,
            label="icon", score=90.0,
        ),
        AimMark(
            id=2, x=0.48, y=0.45, x0=0.44, y0=0.40, x1=0.52, y1=0.50,
            label="icon", score=85.0,
        ),
    ]
    cv = [
        AimMark(
            id=3, x=0.55, y=0.38, x0=0.50, y0=0.33, x1=0.60, y1=0.43,
            label="", score=80.0,
        ),
    ]
    return words, cv


def test_generic_adapter_skips_combat_role_stamps_on_map() -> None:
    image = Image.new("RGB", (640, 400), (166, 138, 99))
    words, cv = _map_board_marks()
    marks = build_marks(
        image,
        screen="map",
        words=words,
        gui_marks=[],
        cv_marks=cv,
        layout_adapter=generic_layout_adapter(),
    )
    assert not any(m.role == "board_candidate" for m in marks)
    assert not any(m.role == "button" for m in marks)
    assert not any(m.source == "ocr-hand" for m in marks)


def test_generic_adapter_skips_hand_card_takeover_on_combat() -> None:
    words = [
        AimMark(i, x, 0.84, x - 0.02, 0.82, x + 0.02, 0.86, label, 0.99)
        for i, (x, label) in enumerate(
            ((0.30, "Defend"), (0.40, "Strike"), (0.50, "Strike")),
            start=1,
        )
    ]
    image = Image.new("RGB", (640, 400), (20, 20, 30))
    marks = build_marks(
        image,
        screen="combat",
        words=words,
        gui_marks=[],
        cv_marks=[],
        layout_adapter=generic_layout_adapter(),
    )
    assert propose_hand_cards(words, screen="combat", layout_adapter=generic_layout_adapter()) == []
    assert not any(m.role == "card" and m.source == "ocr-hand" for m in marks)


def test_legacy_adapter_preserves_combat_hand_and_sprite_rules() -> None:
    words = [
        AimMark(i, x, 0.84, x - 0.02, 0.82, x + 0.02, 0.86, label, 0.99)
        for i, (x, label) in enumerate(
            ((0.30, "Defend"), (0.40, "Strike")),
            start=1,
        )
    ]
    board_icon = AimMark(
        id=9, x=0.55, y=0.35, x0=0.48, y0=0.28, x1=0.62, y1=0.42,
        label="icon", score=0.6,
    )
    adapter = legacy_combat_layout_adapter()
    cards = propose_hand_cards(words, screen="combat", layout_adapter=adapter)
    assert len(cards) == 2
    assert all(card.role == "hand_candidate" and not card.object_id for card in cards)

    image = Image.new("RGB", (640, 400), (20, 20, 30))
    marks = build_marks(
        image,
        screen="combat",
        words=words,
        gui_marks=[board_icon],
        cv_marks=[],
        layout_adapter=adapter,
    )
    assert any(m.role == "board_candidate" for m in marks)
    assert any(m.role == "hand_candidate" for m in marks)
    assert not any(m.role == "card" for m in marks)


def test_generic_adapter_skips_enemy_seeding_from_host() -> None:
    from loop.os.aim import seed_enemies_from_host

    host = {
        "screen": "combat",
        "enemies": [{"hp": 30, "hp_max": 30, "label": "Slime"}],
    }
    seeded = seed_enemies_from_host(
        [],
        host_stats=host,
        layout_adapter=generic_layout_adapter(),
    )
    assert seeded == []


def test_collect_config_generic_selects_generic_adapter() -> None:
    from loop.agent.loop import CollectAgent, CollectConfig
    from loop.os.layout_adapter import GenericLayoutAdapter, LegacyCombatLayoutAdapter

    agent = object.__new__(CollectAgent)
    agent.config = CollectConfig(layout_adapter="generic")
    agent.fixture = type("F", (), {})()
    assert isinstance(agent._layout_adapter(), GenericLayoutAdapter)

    agent.config = CollectConfig()
    assert isinstance(agent._layout_adapter(), LegacyCombatLayoutAdapter)

    agent.config = CollectConfig(layout_adapter="legacy")
    agent.fixture = type("F", (), {"layout_adapter": "generic"})()
    assert isinstance(agent._layout_adapter(), GenericLayoutAdapter)


def test_generic_adapter_does_not_cache_enemy_band_marks() -> None:
    from loop.agent.perception_cache import PerceptionCache, enemy_marks_from
    from loop.os.aim import AimMark
    from PIL import Image

    marks = [
        AimMark(id=1, x=0.55, y=0.35, x0=0.48, y0=0.28, x1=0.62, y1=0.42, label="Jaw Worm"),
        AimMark(id=2, x=0.89, y=0.82, x0=0.85, y0=0.78, x1=0.93, y1=0.86, label="Strike"),
    ]
    assert enemy_marks_from(marks, layout_adapter=generic_layout_adapter()) == []

    cache = PerceptionCache()
    cache.update(
        view=Image.new("RGB", (64, 64), (20, 20, 30)),
        screen="combat",
        ocr_words=[],
        marks=marks,
        layout_adapter=generic_layout_adapter(),
    )
    assert cache.last_enemy_marks == []


def test_generic_adapter_keeps_end_turn_outside_legacy_enemy_strip() -> None:
    from loop.os.aim import AimMark, strip_misplaced_progress

    ghost = AimMark(
        id=1, x=0.5, y=0.22, x0=0.4, y0=0.18, x1=0.6, y1=0.26, label="End Turn",
    )
    kept = strip_misplaced_progress([ghost], layout_adapter=generic_layout_adapter())
    assert [m.label for m in kept] == ["End Turn"]


def test_generic_adapter_skips_corner_hud_chrome_rule() -> None:
    from loop.os.aim import AimMark, _eligible_label_for_region

    mark = AimMark(id=1, x=0.90, y=0.18, x0=0.86, y0=0.14, x1=0.94, y1=0.22, label="Jaw Worm")
    legacy = legacy_combat_layout_adapter()
    generic = generic_layout_adapter()
    assert legacy.in_corner_hud(0.90, 0.18) is True
    assert generic.in_corner_hud(0.90, 0.18) is False
    assert _eligible_label_for_region(mark, "Jaw Worm", screen="combat", layout_adapter=legacy) is False
    assert _eligible_label_for_region(mark, "Jaw Worm", screen="combat", layout_adapter=generic) is True


