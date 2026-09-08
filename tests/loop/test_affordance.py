from __future__ import annotations

from PIL import Image, ImageDraw

from loop.agent.perceive import SceneDescription, validate_perceive_schema
from loop.log.schema import ActionLog
from loop.os.affordance import (
    detect_affordance_diff,
    merge_legal_actions,
    resolve_legal_chord,
    validate_legal,
)
from loop.os.aim import AimGuide, AimMark
from loop.os.chords import is_empty_chord


def _mark(mid: int, *, x: float, y: float, half: float = 0.08) -> AimMark:
    return AimMark(
        id=mid,
        x=x,
        y=y,
        x0=x - half,
        y0=y - half,
        x1=x + half,
        y1=y + half,
        label=f"node_{mid}",
        score=0.8,
    )


def _frame_with_rect(
    size: tuple[int, int],
    rect: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int],
) -> Image.Image:
    img = Image.new("RGB", size, (40, 35, 30))
    draw = ImageDraw.Draw(img)
    draw.rectangle(rect, fill=fill)
    return img


def test_detect_affordance_highlighted_region() -> None:
    size = (200, 200)
    rect = (60, 60, 140, 140)
    pre = _frame_with_rect(size, rect, fill=(50, 50, 50))
    post = _frame_with_rect(size, rect, fill=(180, 180, 180))
    marks = [_mark(1, x=0.5, y=0.5)]
    verdicts = detect_affordance_diff(pre, post, marks)
    sig = verdicts["mk_1"]
    assert sig.delta == "highlighted"
    assert sig.verdict in {"legal", "uncertain"}
    assert sig.confidence >= 0.35


def test_detect_affordance_dimmed_region() -> None:
    size = (200, 200)
    rect = (60, 60, 140, 140)
    pre = _frame_with_rect(size, rect, fill=(200, 200, 200))
    post = _frame_with_rect(size, rect, fill=(40, 40, 40))
    marks = [_mark(2, x=0.5, y=0.5)]
    verdicts = detect_affordance_diff(pre, post, marks)
    sig = verdicts["mk_2"]
    assert sig.delta == "dimmed"
    assert sig.verdict in {"illegal", "uncertain"}


def test_affordance_signal_exposes_explicit_scope_and_provenance() -> None:
    pre = _frame_with_rect((100, 100), (30, 30, 70, 70), fill=(40, 40, 40))
    post = _frame_with_rect((100, 100), (30, 30, 70, 70), fill=(180, 180, 180))
    signal = detect_affordance_diff(
        pre,
        post,
        [AimMark(
            id=8,
            x=0.5,
            y=0.5,
            x0=0.3,
            y0=0.3,
            x1=0.7,
            y1=0.7,
            label="Strike",
            score=0.9,
            object_id="card_8",
        )],
        snapshot_id="turn-8",
        scope="object",
    )["mk_8"]

    assert signal.provenance == "cv"
    assert signal.scope == "object"
    assert signal.object_id == "card_8"
    assert signal.snapshot_id == "turn-8"


def test_validate_legal_blocks_illegal_mark() -> None:
    action = ActionLog(mark=3, click_x=0.5, click_y=0.5, duration_ms=400)
    legal_actions = [{"mark": "mk_3", "verdict": "illegal", "confidence": 0.9}]
    _, reason = validate_legal(action, legal_actions)
    assert reason is not None
    assert reason.startswith("illegal-chord:mk_3:")


def test_validate_legal_allows_uncertain() -> None:
    action = ActionLog(mark=4, click_x=0.5, click_y=0.5, duration_ms=400)
    legal_actions = [{"mark": "mk_4", "verdict": "uncertain", "confidence": 0.4}]
    _, reason = validate_legal(action, legal_actions)
    assert reason is None


def test_resolve_legal_chord_falls_back_to_safe_mark() -> None:
    guide = AimGuide.from_marks(
        Image.new("RGB", (100, 100)),
        (
            AimMark(id=1, x=0.3, y=0.8, x0=0.2, y0=0.7, x1=0.4, y1=0.9, label="Strike"),
            AimMark(id=2, x=0.7, y=0.3, x0=0.6, y0=0.2, x1=0.8, y1=0.4, label="Slime"),
        ),
    )
    illegal = ActionLog(mark=2, click_x=0.7, click_y=0.3, duration_ms=400)
    legal_actions = [
        {
            "mark": "mk_2",
            "verdict": "illegal",
            "confidence": 0.9,
            "source": "cv",
            "provenance": "cv",
            "scope": "unscoped",
        },
        {"mark": "mk_1", "verdict": "uncertain", "confidence": 0.4},
    ]
    resolved, reason = resolve_legal_chord(
        illegal,
        guide,
        legal_actions,
        recent=[],
        duration_ms=400,
        hud={"screen": "combat", "energy": 3},
    )
    assert reason is None
    assert resolved.mark == 1


def test_resolve_legal_chord_blocks_when_no_safe_fallback() -> None:
    guide = AimGuide.from_marks(
        Image.new("RGB", (100, 100)),
        (
            AimMark(id=2, x=0.7, y=0.3, x0=0.6, y0=0.2, x1=0.8, y1=0.4, label="Slime"),
        ),
    )
    illegal = ActionLog(mark=2, click_x=0.7, click_y=0.3, duration_ms=400)
    legal_actions = [{"mark": "mk_2", "verdict": "illegal", "confidence": 0.92}]
    resolved, reason = resolve_legal_chord(
        illegal,
        guide,
        legal_actions,
        recent=[],
        duration_ms=400,
    )
    assert reason is not None


def test_illegal_hover_never_falls_back_to_clicking_a_card() -> None:
    """A stale inspection mark must not reproduce hv_enemy -> mk_card."""
    guide = AimGuide.from_marks(
        Image.new("RGB", (64, 64)),
        [
            AimMark(1, .40, .90, .32, .78, .48, 1.0, "Strike", .99, role="card"),
            AimMark(2, .75, .60, .68, .48, .82, .72, "unidentified_enemy", .80, role="enemy"),
        ],
    )
    hover = ActionLog(mark=2, click_x=.75, click_y=.60, hover=True, duration_ms=400)
    resolved, reason = resolve_legal_chord(
        hover,
        guide,
        [{"mark": "mk_2", "verdict": "illegal", "confidence": .92}],
        recent=[],
        duration_ms=400,
        hud={"screen": "combat", "energy": 3},
    )
    assert is_empty_chord(resolved)
    assert resolved.hover is False
    assert resolved.mark is None
    assert reason == "illegal-chord:mk_2:0.92"
    assert reason.startswith("illegal-chord:")
    assert resolved.mark is None


def test_typed_combat_unscoped_cv_dimming_cannot_block_action() -> None:
    guide = AimGuide.from_marks(
        Image.new("RGB", (100, 100)),
        (
            AimMark(id=1, x=0.3, y=0.8, x0=0.2, y0=0.7, x1=0.4, y1=0.9, label="Strike", object_id="card_1", role="card"),
            AimMark(id=2, x=0.7, y=0.3, x0=0.6, y0=0.2, x1=0.8, y1=0.4, label="Slime", object_id="enemy_1", role="enemy"),
        ),
    )
    illegal = ActionLog(
        mark=2, click_x=0.7, click_y=0.3, duration_ms=400,
        source_role="card", source_object_id="card_1",
    )
    legal_actions = [
        {
            "mark": "mk_2",
            "verdict": "illegal",
            "confidence": 0.9,
            "source": "cv",
            "provenance": "cv",
            "scope": "unscoped",
        },
        {"mark": "mk_1", "verdict": "uncertain", "confidence": 0.4},
    ]
    resolved, reason = resolve_legal_chord(
        illegal,
        guide,
        legal_actions,
        recent=[],
        duration_ms=400,
        hud={"screen": "combat", "energy": 3},
    )
    assert not is_empty_chord(resolved)
    assert resolved.mark == 2
    assert reason is None


def test_merge_legal_actions_keeps_parsed_verdict_and_attaches_cv_telemetry() -> None:
    cv = detect_affordance_diff(
        _frame_with_rect((100, 100), (30, 30, 70, 70), fill=(30, 30, 30)),
        _frame_with_rect((100, 100), (30, 30, 70, 70), fill=(200, 200, 200)),
        [_mark(5, x=0.5, y=0.5, half=0.2)],
    )
    merged = merge_legal_actions(
        [{"mark": "mk_5", "verdict": "uncertain", "confidence": 0.2}],
        cv,
    )
    assert merged
    assert merged[0]["mark"] == "mk_5"
    assert merged[0]["verdict"] == "uncertain"
    assert merged[0]["cv_verdict"] == cv["mk_5"].verdict
    assert merged[0]["cv_scope"] == "unscoped"


def test_unscoped_cv_only_rows_remain_legacy_blockers() -> None:
    action = ActionLog(mark=3, click_x=0.5, click_y=0.5, duration_ms=400)
    _, reason = validate_legal(
        action,
        [{
            "mark": "mk_3",
            "verdict": "illegal",
            "confidence": 0.9,
            "source": "cv",
            "provenance": "cv",
            "scope": "unscoped",
        }],
    )
    assert reason == "illegal-chord:mk_3:0.90"


def test_object_scoped_cv_only_blocks_matching_typed_object() -> None:
    action = ActionLog(
        mark=3,
        click_x=0.5,
        click_y=0.5,
        duration_ms=400,
        source_role="card",
        source_object_id="card_3",
    )
    _, reason = validate_legal(
        action,
        [{
            "mark": "mk_3",
            "verdict": "illegal",
            "confidence": 0.9,
            "source": "cv",
            "provenance": "cv",
            "scope": "object",
            "object_id": "card_3",
            "snapshot_id": "turn-7",
        }],
    )
    assert reason == "illegal-chord:mk_3:0.90"


def test_object_scoped_cv_for_different_object_cannot_block_typed_action() -> None:
    action = ActionLog(
        mark=3,
        click_x=0.5,
        click_y=0.5,
        duration_ms=400,
        source_role="card",
        source_object_id="card_3",
    )
    _, reason = validate_legal(
        action,
        [{
            "mark": "mk_3",
            "verdict": "illegal",
            "confidence": 0.9,
            "source": "cv",
            "provenance": "cv",
            "scope": "object",
            "object_id": "enemy_1",
            "snapshot_id": "turn-7",
        }],
    )
    assert reason is None


def test_scene_description_unresolved_and_legal() -> None:
    parsed = {
        "screen": "combat",
        "controls": [{"label": "Strike", "role": "card", "confidence": "high"}],
        "visible_text": [],
        "unresolved": ["mk_9"],
        "legal_actions": [{"mark": "mk_1", "verdict": "legal", "confidence": 0.8}],
    }
    desc = SceneDescription.from_parsed(parsed)
    assert desc.unresolved == ["mk_9"]
    assert desc.legal_actions[0]["verdict"] == "legal"
    assert "unresolved=mk_9" in desc.as_text()


def test_validate_perceive_schema_requires_fields() -> None:
    assert validate_perceive_schema({"screen": "combat", "controls": []})
    assert "legal_actions" in validate_perceive_schema(
        {"screen": "combat", "controls": [], "unresolved": []}
    )[0]
    assert "characters" in validate_perceive_schema(
        {
            "screen": "combat",
            "controls": [],
            "legal_actions": [],
            "unresolved": [],
        }
    )[0]
    assert not validate_perceive_schema(
        {
            "screen": "combat",
            "controls": [],
            "legal_actions": [],
            "unresolved": [],
                "characters": [],
                "enemies": [],
                "hand_cards": [],
            }
    )


def test_validate_perceive_schema_classifies_parse_failure() -> None:
    from loop.agent.perceive import classify_perceive_parse_failure

    assert classify_perceive_parse_failure(None) == "empty parse"
    assert classify_perceive_parse_failure("") == "empty parse"
    assert classify_perceive_parse_failure("   ") == "empty parse"
    truncated = (
        '{   "screen": "combat",   "controls": [     {       "label": "Strike",'
        '       "role": "card",       "confidence": "high"'
    )
    assert classify_perceive_parse_failure(truncated) == "truncated json"
    assert validate_perceive_schema(None, raw_text=truncated) == ["truncated json"]
    # Balanced braces but not valid JSON object content → parse failed, not empty.
    assert classify_perceive_parse_failure("{not json}") == "json_parse_failed"
    assert validate_perceive_schema(None, raw_text="{not json}") == ["json_parse_failed"]
    assert validate_perceive_schema(None) == ["empty parse"]


def test_validate_perceive_schema_unknown_sprite_requires_bbox() -> None:
    base = {
        "screen": "combat",
        "controls": [],
        "visible_text": [],
        "legal_actions": [],
        "unresolved": [],
        "hand_cards": [],
        "characters": [],
        "enemies": [],
    }
    unknown = {
        "label": None,
        "name": None,
        "name_source": "unresolved",
        "role": "unknown_character",
        "confidence": "low",
        "inspectable": True,
    }
    bad = dict(base)
    bad["characters"] = [dict(unknown)]
    assert any("bbox" in e for e in validate_perceive_schema(bad))

    good = dict(base)
    row = dict(unknown, bbox=[680, 580, 820, 760])
    good["characters"] = [row]
    good["enemies"] = [dict(row)]
    assert not validate_perceive_schema(good)


def test_perceive_schema_invalid_sets_schema_valid_false() -> None:
    from loop.agent.perceive import SceneDescription

    desc = SceneDescription(
        screen="combat",
        schema_valid=False,
        legal_actions=[{"mark": "mk_1", "verdict": "uncertain", "confidence": 0.3}],
        unresolved=[],
    )
    assert desc.schema_valid is False
    assert desc.legal_actions
