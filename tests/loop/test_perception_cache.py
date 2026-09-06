from __future__ import annotations

from PIL import Image

from loop.agent.entities import EntityTracker, TrackedEntity
from loop.agent.perception_cache import (
    PerceptionCache,
    enemy_roster_changed,
    enemy_roster_signature,
    merge_cached_enemy_marks,
    ocr_uncertain_for_reuse,
    PerceptionReuse,
)
from loop.os.aim import AimMark, in_combat_sprite_band
from loop.os.profile import mean_absdiff, roi_thumb


def _blank(size: tuple[int, int] = (960, 540), color: int = 128) -> Image.Image:
    return Image.new("RGB", size, (color, color, color))


def test_frame_identical_reuses_ocr_and_enemies() -> None:
    cache = PerceptionCache()
    view = _blank()
    words = [
        AimMark(id=1, x=0.5, y=0.85, x0=0.4, y0=0.8, x1=0.6, y1=0.9, label="Strike")
    ]
    enemies = [
        AimMark(id=2, x=0.6, y=0.35, x0=0.5, y0=0.25, x1=0.7, y1=0.45, label="Jaw Worm")
    ]
    cache.update(
        view=view,
        screen="combat",
        ocr_words=words,
        marks=words + enemies,
        host_stats={"enemies": [{"hp_max": 44, "label": "Jaw Worm"}]},
    )
    reuse = cache.evaluate(
        view=view,
        screen="combat",
        host_stats={"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]},
        prev_host_stats={"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44}]},
    )
    assert reuse.reuse_ocr
    assert reuse.reuse_enemies
    assert reuse.reason == "frame-identical"


def test_combat_enemies_reused_when_hand_changes() -> None:
    cache = PerceptionCache()
    base = _blank()
    enemies = [
        AimMark(id=2, x=0.6, y=0.35, x0=0.5, y0=0.25, x1=0.7, y1=0.45, label="Jaw Worm")
    ]
    cache.update(
        view=base,
        screen="combat",
        ocr_words=[],
        marks=enemies,
        host_stats={"enemies": [{"hp_max": 44, "label": "Jaw Worm"}]},
    )
    changed = base.copy()
    px = changed.load()
    for x in range(100, 300):
        for y in range(400, 500):
            px[x, y] = (20, 200, 20)
    host = {"screen": "combat", "enemies": [{"hp": 30, "hp_max": 44, "label": "Jaw Worm"}]}
    prev = {"screen": "combat", "enemies": [{"hp": 44, "hp_max": 44, "label": "Jaw Worm"}]}
    reuse = cache.evaluate(
        view=changed,
        screen="combat",
        host_stats=host,
        prev_host_stats=prev,
    )
    assert not reuse.reuse_ocr
    assert reuse.reuse_enemies
    assert reuse.reason == "combat-enemies"
    assert not enemy_roster_changed(prev, host)


def test_enemy_roster_change_invalidates_cache() -> None:
    cache = PerceptionCache()
    view = _blank()
    cache.update(
        view=view,
        screen="combat",
        ocr_words=[],
        marks=[
            AimMark(id=1, x=0.6, y=0.35, x0=0.5, y0=0.25, x1=0.7, y1=0.45, label="Jaw Worm")
        ],
        host_stats={"enemies": [{"hp_max": 44}]},
    )
    prev = {"enemies": [{"hp_max": 44}]}
    host = {"enemies": [{"hp_max": 44}, {"hp_max": 32}]}
    reuse = cache.evaluate(
        view=view,
        screen="combat",
        host_stats=host,
        prev_host_stats=prev,
    )
    assert reuse.reason == "enemy-roster-change"


def test_inspectable_tracker_blocks_enemy_reuse() -> None:
    cache = PerceptionCache()
    view = _blank()
    cache.update(
        view=view,
        screen="combat",
        ocr_words=[],
        marks=[
            AimMark(id=1, x=0.6, y=0.35, x0=0.5, y0=0.25, x1=0.7, y1=0.45, label="Jaw Worm")
        ],
        host_stats={"enemies": [{"hp_max": 44}]},
    )
    tracker = EntityTracker(
        entities=[
            TrackedEntity(
                entity_id="unknown_character_1",
                label="unknown_character_1",
                role="unknown_character",
                cx=0.6,
                cy=0.35,
                inspectable=True,
                identified=False,
            )
        ]
    )
    host = {"enemies": [{"hp_max": 44}]}
    reuse = cache.evaluate(
        view=view,
        screen="combat",
        host_stats=host,
        prev_host_stats=host,
        entity_tracker=tracker,
    )
    assert reuse.reason == "unknown-needs-inspect"


def test_merge_cached_enemy_marks_keeps_hand_ocr() -> None:
    fresh = [
        AimMark(id=1, x=0.5, y=0.85, x0=0.4, y0=0.8, x1=0.6, y1=0.9, label="Defend"),
        AimMark(id=2, x=0.6, y=0.35, x0=0.5, y0=0.25, x1=0.7, y1=0.45, label="Slime"),
    ]
    cached = [
        AimMark(id=9, x=0.62, y=0.33, x0=0.52, y0=0.23, x1=0.72, y1=0.43, label="Jaw Worm")
    ]
    merged = merge_cached_enemy_marks(fresh, cached)
    labels = {m.label for m in merged}
    assert "Defend" in labels
    assert "Jaw Worm" in labels
    assert "Slime" not in labels
    enemy = [m for m in merged if in_combat_sprite_band(m.y, m.x)]
    assert len(enemy) == 1
    assert enemy[0].label == "Jaw Worm"


def test_merge_cached_enemy_marks_keeps_midboard_sprite() -> None:
    fresh = [
        AimMark(id=1, x=0.5, y=0.85, x0=0.4, y0=0.8, x1=0.6, y1=0.9, label="Strike"),
        AimMark(id=2, x=0.75, y=0.645, x0=0.69, y0=0.58, x1=0.81, y1=0.71, label="Slime"),
    ]
    cached = [
        AimMark(id=9, x=0.75, y=0.645, x0=0.69, y0=0.58, x1=0.81, y1=0.71, label="unidentified_enemy")
    ]
    merged = merge_cached_enemy_marks(fresh, cached)
    labels = {m.label for m in merged}
    assert "Strike" in labels
    assert "unidentified_enemy" in labels
    assert "Slime" not in labels
    enemy = [m for m in merged if in_combat_sprite_band(m.y, m.x)]
    assert len(enemy) == 1
    assert enemy[0].y == 0.645


def test_enemy_roster_signature_ignores_hp_drift() -> None:
    a = {"enemies": [{"hp": 44, "hp_max": 44, "label": "Jaw Worm"}]}
    b = {"enemies": [{"hp": 20, "hp_max": 44, "label": "Jaw Worm"}]}
    assert enemy_roster_signature(a) == enemy_roster_signature(b)


def test_screen_change_resets_reuse() -> None:
    cache = PerceptionCache()
    view = _blank()
    cache.update(view=view, screen="combat", ocr_words=[], marks=[])
    reuse = cache.evaluate(
        view=view,
        screen="map",
        host_stats={"screen": "map"},
        prev_host_stats={"screen": "combat"},
    )
    assert reuse.reason == "screen-change"
    assert not reuse.reuse_ocr


def test_screen_change_invalidates_ocr_region_cache(monkeypatch) -> None:
    from loop.os import ocr

    monkeypatch.setenv("AVATAR_OCR", "off")
    cache = PerceptionCache()
    view = _blank()
    cache.update(view=view, screen="combat", ocr_words=[], marks=[])
    ocr.set_ocr_context(screen="combat", uncertain=False)
    ocr.read_words(view)
    ocr.read_words(view)
    assert ocr.last_read_stats().get("cache_hits", 0) >= 1
    ocr.set_ocr_context(screen="map", uncertain=True)
    ocr.read_words(view)
    assert ocr.last_read_stats().get("ocr_mode") == "full"


def test_ocr_uncertain_for_reuse() -> None:
    assert ocr_uncertain_for_reuse(PerceptionReuse(reason="refresh"))
    assert ocr_uncertain_for_reuse(PerceptionReuse(reason="screen-change"))
    assert not ocr_uncertain_for_reuse(PerceptionReuse(reason="combat-enemies"))
    assert not ocr_uncertain_for_reuse(PerceptionReuse(reason="frame-stable"))
