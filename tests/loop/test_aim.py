from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from loop.log.schema import ActionLog
from loop.os.aim import bind_aim, build_aim, detect_marks, fill_from_aim, paint_aim
from loop.os.chords import parse_action


def _buttons() -> Image.Image:
    image = Image.new("RGB", (400, 300), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 120, 90), fill=(220, 80, 40))
    draw.rectangle((250, 50, 340, 100), fill=(40, 180, 90))
    draw.rectangle((150, 200, 260, 260), fill=(50, 90, 220))
    return image


def _parchment_nodes() -> Image.Image:
    image = Image.new("RGB", (640, 400), (4, 4, 4))
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 40, 500, 370), fill=(166, 138, 99))
    for x, y in ((140, 300), (240, 240), (340, 240), (240, 170), (340, 170), (420, 110)):
        draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(72, 48, 32))
    draw.rectangle((530, 70, 620, 330), fill=(90, 130, 155))
    draw.ellipse((555, 120, 590, 155), fill=(28, 36, 48))
    return image


def test_detect_marks_finds_bright_menu_bar() -> None:
    image = Image.new("RGB", (400, 300), (8, 8, 12))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 120, 80), fill=(200, 70, 40))
    draw.rectangle((130, 200, 270, 218), fill=(230, 230, 235))
    marks = detect_marks(image)
    assert any(m.y > 0.55 for m in marks)


def test_merge_puts_lower_ocr_marks_first() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    logo = AimMark(id=9, x=0.5, y=0.2, x0=0.4, y0=0.1, x1=0.6, y1=0.3, label="SPIRE")
    cont = AimMark(id=8, x=0.5, y=0.68, x0=0.4, y0=0.66, x1=0.6, y1=0.70, label="Continue")
    merged = _merge_aim_marks([cont], [logo], cap=8)
    assert merged[0].y > 0.5
    assert merged[0].id == 1
    assert merged[0].label == "Continue"
    marks = detect_marks(_buttons())
    assert len(marks) >= 2
    xs = [m.x for m in marks]
    ys = [m.y for m in marks]
    assert min(xs) < 0.4
    assert max(xs) > 0.6
    assert max(ys) > 0.55


def test_detect_marks_finds_ink_nodes_on_parchment() -> None:
    marks = detect_marks(_parchment_nodes())
    on_paper = [m for m in marks if m.x < 0.78]
    assert len(on_paper) >= 4
    xs = [m.x for m in on_paper]
    ys = [m.y for m in on_paper]
    assert min(xs) < 0.35
    assert max(xs) < 0.75
    assert max(ys) > 0.6
    assert min(ys) < 0.45
    assert any(m.y > 0.65 and m.x < 0.35 for m in on_paper)


def test_paint_aim_keeps_size_and_bind_mark() -> None:
    image = _buttons()
    guide = build_aim(image, last_xy=(0.2, 0.2))
    assert guide.image.size == image.size
    assert guide.listing()
    raw = parse_action({"mark": 1}, "sts")
    bound = bind_aim(raw, guide)
    assert bound.mark == 1
    assert bound.click_x is not None
    assert bound.click_y is not None
    filled = fill_from_aim(guide, recent=["mk_1"], duration_ms=400)
    assert filled is not None
    assert filled.mark != 1 or len(guide.marks) == 1
    if len(guide.marks) >= 2:
        pair = parse_action({"mark": 1, "target": 2}, "sts")
        bound_pair = bind_aim(pair, guide)
        assert bound_pair.mark == 1
        assert bound_pair.target == 2
        assert bound_pair.click_x2 is not None
        assert bound_pair.click_y2 is not None
        assert bound_pair.derive_name().startswith("mk_1>")


def test_invalid_mark_clears() -> None:
    guide = build_aim(_buttons())
    action = ActionLog(mark=99, name="mk_99", duration_ms=400)
    bound = bind_aim(action, guide)
    assert bound.mark is None
    assert bound.click_x is None
    assert bound.name is None
    named = ActionLog(name="mk_1", duration_ms=400)
    rebound = bind_aim(named, guide)
    assert rebound.mark == 1
    assert rebound.click_x is not None


def test_listing_includes_ocr_label() -> None:
    from loop.os.aim import AimGuide, AimMark

    guide = AimGuide(
        marks=(
            AimMark(id=1, x=0.4, y=0.68, x0=0.3, y0=0.66, x1=0.5, y1=0.70, label="Continue"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    assert "1@400,680:Continue" in guide.listing()


def test_fill_from_aim_prefers_continue_label() -> None:
    from loop.os.aim import AimGuide, AimMark, fill_from_aim

    guide = AimGuide(
        marks=(
            AimMark(id=1, x=0.4, y=0.87, x0=0.3, y0=0.85, x1=0.5, y1=0.89, label="Quit"),
            AimMark(id=2, x=0.4, y=0.68, x0=0.3, y0=0.66, x1=0.5, y1=0.70, label="Continue"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    filled = fill_from_aim(guide, recent=[], duration_ms=400)
    assert filled is not None
    assert filled.mark == 2
    assert filled.hover is False


def test_steer_off_danger_rewrites_quit_to_continue() -> None:
    from loop.os.aim import AimGuide, AimMark, steer_off_danger

    guide = AimGuide(
        marks=(
            AimMark(id=1, x=0.4, y=0.68, x0=0.3, y0=0.66, x1=0.5, y1=0.70, label="Continue"),
            AimMark(id=4, x=0.4, y=0.84, x0=0.3, y0=0.82, x1=0.5, y1=0.86, label="Quit"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    quit_click = ActionLog(mark=4, click_x=0.4, click_y=0.84, duration_ms=400)
    steered = steer_off_danger(quit_click, guide, recent=[], duration_ms=400)
    assert steered.mark == 1
    yes = ActionLog(mark=4, click_x=0.45, click_y=0.67, duration_ms=400)
    guide_yes = AimGuide(
        marks=(
            AimMark(id=1, x=0.4, y=0.68, x0=0.3, y0=0.66, x1=0.5, y1=0.70, label="Continue"),
            AimMark(id=3, x=0.3, y=0.5, x0=0.2, y0=0.48, x1=0.4, y1=0.52, label="Quit"),
            AimMark(id=4, x=0.45, y=0.67, x0=0.4, y0=0.65, x1=0.5, y1=0.69, label="Yes"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    steered_yes = steer_off_danger(yes, guide_yes, recent=[], duration_ms=400)
    assert steered_yes.mark == 1
    give_up = ActionLog(mark=4, click_x=0.5, click_y=0.62, duration_ms=400)
    guide_pause = AimGuide(
        marks=(
            AimMark(id=1, x=0.5, y=0.38, x0=0.4, y0=0.36, x1=0.6, y1=0.40, label="Resume"),
            AimMark(id=4, x=0.5, y=0.62, x0=0.4, y0=0.60, x1=0.6, y1=0.64, label="Give Up"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    steered_give_up = steer_off_danger(give_up, guide_pause, recent=[], duration_ms=400)
    assert steered_give_up.mark == 1


def test_paint_aim_without_marks() -> None:
    blank = Image.new("RGB", (64, 64), (12, 12, 12))
    painted = paint_aim(blank, marks=(), last_xy=None)
    assert painted.size == blank.size


def _faded_map() -> Image.Image:
    image = Image.new("RGB", (640, 400), (4, 4, 4))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 30, 500, 380), fill=(166, 138, 99))
    faded = (148, 132, 108)
    for x, y in ((160, 80), (280, 80), (220, 160), (300, 160), (180, 220)):
        draw.ellipse((x - 16, y - 16, x + 16, y + 16), fill=faded)
    draw.ellipse((202, 302, 238, 338), fill=(28, 18, 12))
    draw.ellipse((214, 312, 222, 320), fill=(240, 240, 240))
    draw.rectangle((520, 60, 630, 340), fill=(200, 210, 220))
    draw.rectangle((540, 200, 610, 230), fill=(90, 70, 50))
    return image


def test_detect_marks_drops_faded_map_nodes() -> None:
    image = _faded_map()
    marks = detect_marks(image)
    on_paper = [m for m in marks if m.x < 0.78]
    assert any(m.y > 0.70 and m.x < 0.5 for m in on_paper)
    faded = [m for m in on_paper if m.y < 0.60]
    live = [m for m in on_paper if m.y > 0.70]
    assert live
    assert all(m.score >= 35 for m in live)
    assert not faded or max(m.score for m in faded) < max(m.score for m in live)


def test_merge_skips_legend_and_keeps_solid_node() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks, is_legend_label

    assert is_legend_label("Enemy")
    assert is_legend_label("Legend")
    assert is_legend_label("Res")
    assert not is_legend_label("Continue")
    legend = AimMark(id=4, x=0.90, y=0.60, x0=0.85, y0=0.55, x1=0.95, y1=0.65, label="Enemy")
    live = AimMark(id=9, x=0.38, y=0.75, x0=0.34, y0=0.70, x1=0.42, y1=0.80, score=90.0)
    faded = AimMark(id=8, x=0.40, y=0.30, x0=0.36, y0=0.26, x1=0.44, y1=0.34, score=18.0)
    merged = _merge_aim_marks([legend], [faded, live], cap=8)
    assert merged[0].y > 0.7
    assert merged[0].score >= 80
    assert all(m.label.lower() != "enemy" for m in merged)


def test_strip_legend_drops_right_panel_marks() -> None:
    from loop.os.aim import AimMark, _strip_legend

    ocr = [
        AimMark(id=1, x=0.90, y=0.35, x0=0.84, y0=0.32, x1=0.96, y1=0.38, label="Legend"),
        AimMark(id=2, x=0.91, y=0.60, x0=0.85, y0=0.56, x1=0.97, y1=0.64, label="Enemy"),
    ]
    cv = [
        AimMark(id=3, x=0.90, y=0.60, x0=0.85, y0=0.55, x1=0.95, y1=0.65, score=40.0),
        AimMark(id=4, x=0.38, y=0.75, x0=0.34, y0=0.70, x1=0.42, y1=0.80, score=90.0),
    ]
    words, _gui, kept = _strip_legend(ocr, cv)
    assert words == []
    assert len(kept) == 1
    assert kept[0].x < 0.5
    hud = AimMark(id=5, x=0.05, y=0.04, x0=0.02, y0=0.01, x1=0.08, y1=0.07, score=140.0)
    _, _gui, kept_hud = _strip_legend(ocr, cv + [hud])
    assert all(m.y >= 0.12 for m in kept_hud)


def test_fill_from_aim_prefers_high_contrast_node() -> None:
    from loop.os.aim import AimGuide, AimMark, fill_from_aim

    guide = AimGuide(
        marks=(
            AimMark(id=1, x=0.4, y=0.30, x0=0.36, y0=0.26, x1=0.44, y1=0.34, score=18.0),
            AimMark(id=2, x=0.38, y=0.75, x0=0.34, y0=0.70, x1=0.42, y1=0.80, score=90.0),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    filled = fill_from_aim(guide, recent=[], duration_ms=400)
    assert filled is not None
    assert filled.mark == 2


def test_same_chord_includes_two_click_and_hover() -> None:
    from loop.os.chords import same_chord

    card = ActionLog(mark=3, click_x=0.40, click_y=0.85, duration_ms=400)
    pair = ActionLog(
        mark=3,
        click_x=0.40,
        click_y=0.85,
        target=7,
        click_x2=0.55,
        click_y2=0.30,
        duration_ms=400,
    )
    pair_again = ActionLog(
        mark=3,
        click_x=0.40,
        click_y=0.85,
        target=7,
        click_x2=0.55,
        click_y2=0.30,
        duration_ms=400,
    )
    hover = ActionLog(mark=3, click_x=0.40, click_y=0.85, hover=True, duration_ms=400)
    assert same_chord(card, pair) is False
    assert same_chord(pair, pair_again) is True
    assert same_chord(card, hover) is False


def test_merge_labels_unlabeled_corner_check_confirm() -> None:
    from loop.os.aim import AimGuide, AimMark, _merge_aim_marks, fill_from_aim

    check = AimMark(id=3, x=0.941, y=0.883, x0=0.91, y0=0.85, x1=0.98, y1=0.96, score=80.0)
    lock = AimMark(id=10, x=0.53, y=0.79, x0=0.50, y0=0.76, x1=0.56, y1=0.82, score=40.0)
    name = AimMark(id=16, x=0.32, y=0.38, x0=0.18, y0=0.34, x1=0.45, y1=0.42, label="Ironclad")
    merged = _merge_aim_marks([name], [check, lock], cap=8)
    confirm = [m for m in merged if m.label == "Confirm"]
    assert len(confirm) == 1
    assert confirm[0].x >= 0.88
    assert confirm[0].y >= 0.80
    already = AimMark(id=4, x=0.94, y=0.88, x0=0.91, y0=0.85, x1=0.98, y1=0.96, label="Confirm")
    skip = _merge_aim_marks([already], [check], cap=8)
    assert sum(1 for m in skip if m.label == "Confirm") == 1
    mid_right = AimMark(id=8, x=0.82, y=0.88, x0=0.78, y0=0.86, x1=0.90, y1=0.92, score=70.0)
    combat = _merge_aim_marks([], [mid_right, lock], cap=8)
    assert all(m.label != "Confirm" for m in combat)
    guide = AimGuide(marks=tuple(merged), last_xy=None, image=Image.new("RGB", (8, 8), (0, 0, 0)))
    filled = fill_from_aim(guide, recent=[], duration_ms=400)
    assert filled is not None
    assert filled.mark == confirm[0].id


def test_corner_confirm_clips_fused_floor_glow() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, _merge_aim_marks, bind_aim, steer_to_labeled

    fused = AimMark(id=13, x=0.939, y=0.883, x0=0.903, y0=0.767, x1=0.975, y1=1.0, score=150.0)
    lock = AimMark(id=10, x=0.53, y=0.79, x0=0.50, y0=0.76, x1=0.56, y1=0.82, score=40.0)
    merged = _merge_aim_marks([], [fused, lock], cap=8)
    confirm = next(m for m in merged if m.label == "Confirm")
    assert confirm.y1 - confirm.y0 <= 0.14 + 1e-6
    assert confirm.y < 0.82
    assert confirm.y > 0.76
    compact = AimMark(id=3, x=0.941, y=0.883, x0=0.91, y0=0.85, x1=0.98, y1=0.96, score=80.0)
    kept = _merge_aim_marks([], [compact], cap=8)
    assert abs(kept[0].y - (0.85 + 0.11 * 0.20)) < 0.002

    guide = AimGuide(marks=tuple(merged), last_xy=None, image=Image.new("RGB", (8, 8), (0, 0, 0)))
    armor = next(m for m in merged if m.label != "Confirm")
    copied = bind_aim(ActionLog(mark=armor.id, duration_ms=400), guide)
    steered = steer_to_labeled(
        copied,
        guide,
        thought="This is character select. Click the Confirm button.",
    )
    assert steered.mark == confirm.id
    assert steered.target is None
    assert abs(steered.click_y - confirm.y) < 1e-9
    two = bind_aim(ActionLog(mark=confirm.id, target=armor.id, duration_ms=400), guide)
    dropped = steer_to_labeled(two, guide, thought="")
    assert dropped.mark == confirm.id
    assert dropped.target is None
    skipped = steer_to_labeled(copied, guide, thought="I will click the Ironclad portrait.")
    assert skipped.mark == armor.id


def test_steer_title_singleplayer_off_example_mark() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, steer_to_labeled

    guide = AimGuide(
        marks=(
            AimMark(id=1, x=0.404, y=0.705, x0=0.30, y0=0.68, x1=0.50, y1=0.73, label="Singleplayer"),
            AimMark(id=4, x=0.428, y=0.428, x0=0.40, y0=0.40, x1=0.46, y1=0.46, label=""),
            AimMark(id=16, x=0.404, y=0.751, x0=0.30, y0=0.73, x1=0.50, y1=0.77, label="Multiplayer"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    copied = ActionLog(mark=4, click_x=0.428, click_y=0.428, duration_ms=400)
    steered = steer_to_labeled(
        copied,
        guide,
        thought="Click the Singleplayer option to start the game",
        choice="Singleplayer",
    )
    assert steered.mark == 1
    assert steered.target is None
    assert abs(steered.click_y - 0.705) < 1e-9
    hover = ActionLog(mark=4, hover=True, click_x=0.428, click_y=0.428, duration_ms=400)
    hovered = steer_to_labeled(hover, guide, thought="hovering Singleplayer", choice="Singleplayer")
    assert hovered.mark == 1
    assert hovered.hover is True


def test_steer_combat_two_click_stays() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, steer_to_labeled

    guide = AimGuide(
        marks=(
            AimMark(id=3, x=0.40, y=0.85, x0=0.3, y0=0.82, x1=0.5, y1=0.90, label="Strike"),
            AimMark(id=7, x=0.55, y=0.30, x0=0.5, y0=0.25, x1=0.6, y1=0.35, label="Cultist"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    pair = ActionLog(
        mark=3,
        target=7,
        click_x=0.40,
        click_y=0.85,
        click_x2=0.55,
        click_y2=0.30,
        duration_ms=400,
    )
    kept = steer_to_labeled(pair, guide, thought="Strike then the left enemy", choice="Strike")
    assert kept.mark == 3
    assert kept.target == 7


def test_steer_skips_character_placeholder_and_body_sentence() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, steer_to_labeled

    guide = AimGuide(
        marks=(
            AimMark(id=14, x=0.40, y=0.50, x0=0.35, y0=0.45, x1=0.45, y1=0.55, label="character"),
            AimMark(id=15, x=0.16, y=0.50, x0=0.12, y0=0.48, x1=0.20, y1=0.52, label="Play a single run with ???"),
            AimMark(id=4, x=0.50, y=0.88, x0=0.48, y0=0.86, x1=0.52, y1=0.90, label="icon"),
            AimMark(id=2, x=0.82, y=0.88, x0=0.78, y0=0.86, x1=0.90, y1=0.92, label="Confirm"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    play = ActionLog(mark=15, click_x=0.16, click_y=0.50, duration_ms=400)
    stayed = steer_to_labeled(
        play,
        guide,
        thought="The locked character cannot be selected; click Play",
        choice="character",
    )
    assert stayed.mark == 15
    copied = ActionLog(mark=4, click_x=0.50, click_y=0.88, duration_ms=400)
    confirm = steer_to_labeled(
        copied,
        guide,
        thought="This is character select. Click Confirm.",
        choice="Confirm",
    )
    assert confirm.mark == 2


def test_steer_skips_locked_flavor_avoid_and_class_name() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, steer_to_labeled

    guide = AimGuide(
        marks=(
            AimMark(id=13, x=0.44, y=0.88, x0=0.40, y0=0.84, x1=0.48, y1=0.92, label="Locked"),
            AimMark(id=14, x=0.16, y=0.50, x0=0.12, y0=0.48, x1=0.20, y1=0.52, label="Play"),
            AimMark(id=11, x=0.32, y=0.38, x0=0.18, y0=0.34, x1=0.45, y1=0.42, label="Ironclad"),
            AimMark(id=5, x=0.50, y=0.72, x0=0.48, y0=0.70, x1=0.52, y1=0.74, label="icon"),
            AimMark(id=2, x=0.96, y=0.72, x0=0.93, y0=0.70, x1=0.99, y1=0.76, label="Confirm"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    proposed = ActionLog(mark=5, click_x=0.50, click_y=0.72, duration_ms=400)
    stayed = steer_to_labeled(
        proposed,
        guide,
        thought="click Play to unlock the character",
        choice="Play",
    )
    assert stayed.mark == 5
    named = steer_to_labeled(
        proposed,
        guide,
        thought="The Ironclad portrait is showing",
        choice="",
    )
    assert named.mark == 5
    confirm = steer_to_labeled(
        proposed,
        guide,
        thought="Ironclad card, click Confirm",
        choice="Confirm",
    )
    assert confirm.mark == 2
    avoided = steer_to_labeled(
        proposed,
        guide,
        thought="try Play",
        choice="",
        avoid={"mk_14"},
    )
    assert avoided.mark == 5
    unlocked = AimGuide(
        marks=(
            AimMark(id=14, x=0.16, y=0.50, x0=0.12, y0=0.48, x1=0.20, y1=0.52, label="Play"),
            AimMark(id=5, x=0.50, y=0.72, x0=0.48, y0=0.70, x1=0.52, y1=0.74, label="icon"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    yanked = steer_to_labeled(proposed, unlocked, thought="click Play", choice="")
    assert yanked.mark == 14
    skipped_avoid = steer_to_labeled(
        proposed,
        unlocked,
        thought="click Play",
        choice="",
        avoid={"mk_14"},
    )
    assert skipped_avoid.mark == 5


def test_merge_labels_confirm_pennant_y072() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    check = AimMark(id=6, x=0.96, y=0.724, x0=0.93, y0=0.70, x1=0.99, y1=0.76, score=80.0)
    merged = _merge_aim_marks([], [check], cap=8)
    confirm = [m for m in merged if m.label == "Confirm"]
    assert len(confirm) == 1
    assert confirm[0].x >= 0.88
    mid_right = AimMark(id=8, x=0.82, y=0.72, x0=0.78, y0=0.70, x1=0.90, y1=0.76, score=70.0)
    combat = _merge_aim_marks([], [mid_right], cap=8)
    assert all(m.label != "Confirm" for m in combat)


def test_merge_dialog_yes_no_drops_body_ocr() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    body = [
        AimMark(id=1, x=0.43, y=0.65, x0=0.38, y0=0.62, x1=0.48, y1=0.68, label="tutorial"),
        AimMark(id=2, x=0.48, y=0.63, x0=0.44, y0=0.60, x1=0.52, y1=0.66, label="tut"),
        AimMark(id=3, x=0.43, y=0.60, x0=0.38, y0=0.58, x1=0.48, y1=0.63, label="brief"),
    ]
    no_btn = AimMark(id=4, x=0.32, y=0.86, x0=0.26, y0=0.82, x1=0.38, y1=0.90, label="icon", score=0.8)
    yes_btn = AimMark(id=5, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="icon", score=0.8)
    merged = _merge_aim_marks(body, [], cap=8, gui=[no_btn, yes_btn])
    labels = {m.label for m in merged}
    assert "No" in labels
    assert "Yes" in labels
    assert "tut" not in labels
    assert "brief" not in labels
    assert "tutorial" not in labels
    no_mark = next(m for m in merged if m.label == "No")
    yes_mark = next(m for m in merged if m.label == "Yes")
    assert no_mark.x < yes_mark.x
    silent = _merge_aim_marks([], [], cap=8, gui=[no_btn, yes_btn])
    assert all(m.label not in {"Yes", "No"} for m in silent)


def test_merge_inspect_arrows_does_not_mint_close() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks, fill_from_aim, needs_tooltip, steer_to_labeled
    from loop.log.schema import ActionLog

    left = AimMark(id=1, x=0.08, y=0.50, x0=0.04, y0=0.44, x1=0.12, y1=0.56, label="icon", score=0.9)
    right = AimMark(id=2, x=0.92, y=0.50, x0=0.88, y0=0.44, x1=0.96, y1=0.56, label="icon", score=0.9)
    art = AimMark(id=3, x=0.50, y=0.48, x0=0.32, y0=0.22, x1=0.68, y1=0.74, label="icon", score=0.4)
    merged = _merge_aim_marks([], [], cap=8, gui=[left, right, art])
    labels = {m.label for m in merged}
    assert "Close" not in labels
    assert labels == {"Arrow"}
    assert any(m.x <= 0.16 for m in merged)
    assert any(m.x >= 0.84 for m in merged)
    assert not any(0.28 <= m.x <= 0.72 and (m.x1 - m.x0) * (m.y1 - m.y0) >= 0.08 for m in merged)
    assert not needs_tooltip("Arrow")
    from loop.os.aim import AimGuide

    guide = AimGuide(marks=tuple(merged), last_xy=None, image=Image.new("RGB", (8, 8), (0, 0, 0)))
    filled = fill_from_aim(guide, recent=[], duration_ms=400)
    assert filled is not None
    hit = guide.by_id(int(filled.mark))
    assert hit is not None
    assert hit.label != "Close"
    arrow = ActionLog(mark=merged[0].id, click_x=merged[0].x, click_y=merged[0].y, duration_ms=400)
    steered = steer_to_labeled(arrow, guide, thought="click the darkened background")
    assert steered.mark == merged[0].id


def test_tutorial_lateral_nav_reclaims_inspect_arrows() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    left = AimMark(id=1, x=0.04, y=0.50, x0=0.02, y0=0.44, x1=0.08, y1=0.56, label="icon", score=0.9)
    right = AimMark(id=2, x=0.96, y=0.50, x0=0.92, y0=0.44, x1=0.99, y1=0.56, label="icon", score=0.9)
    merged = _merge_aim_marks([], [], cap=8, gui=[left, right], screen="tutorial")
    labels = {m.label for m in merged}
    assert "Prev" in labels
    assert "Next" in labels
    assert "Arrow" not in labels


def test_steer_dialog_got_it_to_no_and_yes() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, steer_to_labeled

    guide = AimGuide(
        marks=(
            AimMark(id=5, x=0.32, y=0.86, x0=0.26, y0=0.82, x1=0.38, y1=0.90, label="No"),
            AimMark(id=6, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="Yes"),
            AimMark(id=15, x=0.48, y=0.63, x0=0.44, y0=0.60, x1=0.52, y1=0.66, label="icon"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    body = ActionLog(mark=15, click_x=0.48, click_y=0.63, duration_ms=400)
    got = steer_to_labeled(body, guide, thought="click Got it")
    assert got.mark == 5
    dismiss = steer_to_labeled(body, guide, thought="Dismiss the tutorial overlay")
    assert dismiss.mark == 5
    yes = steer_to_labeled(body, guide, thought="click Yes", choice="Yes")
    assert yes.mark == 6
    stay = steer_to_labeled(
        ActionLog(mark=6, click_x=0.54, click_y=0.86, duration_ms=400),
        guide,
        thought="dismiss overlay",
    )
    assert stay.mark == 6
    bounced = steer_to_labeled(body, guide, thought="mash body text", avoid={"mk_15"})
    assert bounced.mark == 5


def test_clamp_to_overlay_drops_keys_and_snaps_clicks() -> None:
    from loop.os.aim import AimGuide, AimMark, clamp_to_overlay, fill_from_aim
    from loop.os.chords import is_empty_chord, parse_action

    guide = AimGuide(
        marks=(
            AimMark(id=5, x=0.32, y=0.86, x0=0.26, y0=0.82, x1=0.38, y1=0.90, label="No"),
            AimMark(id=6, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="Yes"),
            AimMark(id=15, x=0.48, y=0.63, x0=0.44, y0=0.60, x1=0.52, y1=0.66, label="icon"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    enter = clamp_to_overlay(parse_action({"action": "enter"}, "sts"), guide)
    assert enter.mark is None
    assert enter.name is None
    assert is_empty_chord(enter)
    filled = fill_from_aim(guide, recent=[], duration_ms=400)
    assert filled is not None
    assert filled.mark == 5
    near = clamp_to_overlay(parse_action({"gx": 330, "gy": 860}, "sts"), guide)
    assert near.mark == 5
    far = clamp_to_overlay(parse_action({"gx": 500, "gy": 180}, "sts"), guide)
    assert far.mark is None
    assert far.click_x is None
    keep = clamp_to_overlay(parse_action({"mark": 6}, "sts"), guide)
    keep = bind_aim(keep, guide)
    keep = clamp_to_overlay(keep, guide)
    assert keep.mark == 6
    bogus = clamp_to_overlay(ActionLog(mark=99, name="mk_99", duration_ms=400), guide)
    assert bogus.mark is None
    assert bogus.name is None


def test_prefer_hover_unlabeled_then_click() -> None:
    from loop.os.aim import AimGuide, AimMark, bind_aim, prefer_hover
    from loop.os.chords import parse_action

    guide = AimGuide(
        marks=(
            AimMark(id=3, x=0.50, y=0.16, x0=0.42, y0=0.08, x1=0.58, y1=0.24, label="Close"),
            AimMark(id=6, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="Yes"),
            AimMark(id=15, x=0.40, y=0.85, x0=0.34, y0=0.80, x1=0.46, y1=0.90, label="icon"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    icon = prefer_hover(
        bind_aim(parse_action({"mark": 15}, "sts"), guide),
        guide,
        hovered=set(),
    )
    assert icon.hover is True
    assert icon.mark == 15
    again = prefer_hover(icon.model_copy(update={"hover": False}), guide, hovered={15})
    assert again.hover is False
    yes = prefer_hover(
        bind_aim(parse_action({"mark": 6}, "sts"), guide),
        guide,
        hovered=set(),
    )
    assert yes.hover is False
    close = prefer_hover(
        bind_aim(parse_action({"mark": 3}, "sts"), guide),
        guide,
        hovered=set(),
    )
    assert close.hover is False
    pair = prefer_hover(
        bind_aim(parse_action({"mark": 15, "target": 6}, "sts"), guide),
        guide,
        hovered=set(),
    )
    assert pair.hover is False
    assert pair.target == 6


def test_fill_from_aim_prefers_dialog_buttons() -> None:
    from loop.os.aim import AimGuide, AimMark, fill_from_aim

    guide = AimGuide(
        marks=(
            AimMark(id=15, x=0.48, y=0.63, x0=0.44, y0=0.60, x1=0.52, y1=0.66, label="icon", score=9.0),
            AimMark(id=5, x=0.32, y=0.86, x0=0.26, y0=0.82, x1=0.38, y1=0.90, label="No", score=1.0),
            AimMark(id=6, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="Yes", score=1.0),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    filled = fill_from_aim(guide, recent=[], duration_ms=400)
    assert filled is not None
    assert filled.mark in {5, 6}


def test_merge_keeps_menu_words_ahead_of_art() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks, label_kind

    assert label_kind("Singleplayer") == "progress"
    assert label_kind("Multiplayer") == "other"
    assert label_kind("Gameplay") == "other"
    words = [
        AimMark(id=1, x=0.40, y=0.70, x0=0.30, y0=0.68, x1=0.50, y1=0.72, label="Singleplayer"),
        AimMark(id=2, x=0.40, y=0.75, x0=0.30, y0=0.73, x1=0.50, y1=0.77, label="Multiplayer"),
        AimMark(id=3, x=0.40, y=0.80, x0=0.30, y0=0.78, x1=0.50, y1=0.82, label="Settings"),
        AimMark(id=4, x=0.50, y=0.50, x0=0.48, y0=0.48, x1=0.52, y1=0.52, label="the"),
    ]
    art = [
        AimMark(
            id=10 + i,
            x=0.20 + i * 0.03,
            y=0.30,
            x0=0.18 + i * 0.03,
            y0=0.28,
            x1=0.22 + i * 0.03,
            y1=0.32,
            score=90.0,
        )
        for i in range(20)
    ]
    merged = _merge_aim_marks(words, art, cap=16)
    assert [m.label for m in merged[:3]] == ["Singleplayer", "Multiplayer", "Settings"]
    assert all(m.label != "the" for m in merged)
    assert len(merged) >= 3


def test_merge_labels_corner_back() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    check = AimMark(id=3, x=0.941, y=0.883, x0=0.91, y0=0.85, x1=0.98, y1=0.96, score=80.0)
    back = AimMark(id=2, x=0.08, y=0.88, x0=0.04, y0=0.82, x1=0.12, y1=0.94, score=70.0)
    merged = _merge_aim_marks([], [check, back], cap=8)
    labels = {m.label: m for m in merged if m.label}
    assert "Confirm" in labels
    assert "Back" in labels
    assert labels["Back"].x <= 0.12


def test_map_toolbar_corner_is_not_labeled_back() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    # STS2's map toolbar has an unlabeled bottom-left icon at the same location
    # as menu Back.  It must remain a non-actionable icon on the map.
    toolbar = AimMark(id=2, x=0.08, y=0.88, x0=0.04, y0=0.82, x1=0.12, y1=0.94, score=70.0)
    merged = _merge_aim_marks([], [toolbar], cap=8, screen="map")
    assert not any(mark.label == "Back" for mark in merged)
    assert merged[0].label == ""


def test_build_marks_map_toolbar_corner_is_not_back() -> None:
    from loop.os.aim import AimMark, build_marks

    toolbar = AimMark(
        id=2, x=0.08, y=0.88, x0=0.04, y0=0.82, x1=0.12, y1=0.94, label="Back", score=70.0
    )
    marks = build_marks(
        Image.new("RGB", (64, 64)), cv_marks=[toolbar], words=[], gui_marks=[], screen="map"
    )
    assert not any(mark.label == "Back" for mark in marks)


def test_map_corner_back_heuristics_require_ocr() -> None:
    from loop.os.aim import AimMark, _relabel_from_lexicon, _strip_unverified_menu_labels, fuse_dino_marks, AimGuide

    corner_back = AimMark(
        id=2, x=0.08, y=0.88, x0=0.04, y0=0.82, x1=0.12, y1=0.94, label="Back", score=0.8
    )
    assert _strip_unverified_menu_labels([corner_back], [], screen="map")[0].label == ""
    assert _strip_unverified_menu_labels([corner_back], [], screen="title")[0].label == "Back"

    guide = AimGuide.from_marks(Image.new("RGB", (32, 32)), [corner_back])
    assert fuse_dino_marks(guide, [], screen="map").marks[0].label == ""

    class _Lexicon:
        def label_for(self, screen, x, y):  # type: ignore[no-untyped-def]
            return "Back"

    unlabeled = AimMark(
        id=3, x=0.08, y=0.88, x0=0.04, y0=0.82, x1=0.12, y1=0.94, label="icon", score=0.8
    )
    assert _relabel_from_lexicon([unlabeled], _Lexicon(), "map", ocr=[])[0].label == "icon"
    assert _relabel_from_lexicon([unlabeled], _Lexicon(), "title", ocr=[])[0].label == "Back"


def test_badge_centered_on_click() -> None:
    from loop.os.aim import AimMark, badge_box

    mark = AimMark(id=1, x=0.404, y=0.705, x0=0.30, y0=0.68, x1=0.50, y1=0.73, label="Singleplayer")
    bx0, by0, bx1, by1, cx, cy = badge_box(mark, width=1280, height=720, tw=10, th=14, pad=3)
    assert abs((bx0 + bx1) / 2 - cx) <= 1
    assert abs((by0 + by1) / 2 - cy) <= 1
    assert abs(cx - round(0.404 * 1279)) <= 1
    assert abs(cy - round(0.705 * 719)) <= 1


def test_parse_listing_roundtrip() -> None:
    from loop.os.aim import AimGuide, AimMark, parse_listing

    guide = AimGuide(
        marks=(
            AimMark(id=3, x=0.4, y=0.85, x0=0.3, y0=0.82, x1=0.5, y1=0.90, label="Strike"),
            AimMark(id=7, x=0.55, y=0.30, x0=0.5, y0=0.25, x1=0.6, y1=0.35, label="Cultist"),
        ),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    rebuilt = parse_listing(guide.listing())
    assert [m.id for m in rebuilt] == [3, 7]
    assert rebuilt[0].label == "Strike"
    assert abs(rebuilt[1].y - 0.30) < 0.002


def test_merge_attaches_ocr_label_to_yolo_box() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    word = AimMark(id=1, x=0.404, y=0.705, x0=0.30, y0=0.68, x1=0.50, y1=0.73, label="Singleplayer")
    yolo = AimMark(id=9, x=0.40, y=0.70, x0=0.32, y0=0.66, x1=0.48, y1=0.76, label="icon", score=0.9)
    art = AimMark(id=10, x=0.42, y=0.42, x0=0.38, y0=0.38, x1=0.46, y1=0.46, score=90.0)
    merged = _merge_aim_marks([word], [art], cap=8, gui=[yolo])
    hit = next(m for m in merged if m.label == "Singleplayer")
    assert abs(hit.x - 0.40) < 1e-9
    assert abs(hit.y - 0.70) < 1e-9
    assert abs(hit.x0 - 0.32) < 1e-9
    assert hit.source == "ocr-gui"
    assert any(abs(m.x - 0.42) < 0.02 and abs(m.y - 0.42) < 0.02 for m in merged)


def test_merge_labels_yolo_corner_icon_confirm() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    icon = AimMark(id=3, x=0.941, y=0.883, x0=0.91, y0=0.85, x1=0.98, y1=0.96, label="icon", score=0.8)
    merged = _merge_aim_marks([], [], cap=8, gui=[icon])
    confirm = next(m for m in merged if m.label == "Confirm")
    assert confirm.x >= 0.88
    assert abs(confirm.y - (0.85 + 0.11 * 0.20)) < 0.002
    tall = AimMark(id=4, x=0.94, y=0.90, x0=0.90, y0=0.77, x1=0.98, y1=1.0, label="icon", score=0.7)
    clipped = _merge_aim_marks([], [], cap=8, gui=[tall])
    hit = next(m for m in clipped if m.label == "Confirm")
    assert hit.y1 - hit.y0 <= 0.14 + 1e-6


def test_merge_drops_giant_yolo_and_keeps_complementary_cv() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    giant = AimMark(id=1, x=0.5, y=0.5, x0=0.05, y0=0.05, x1=0.95, y1=0.95, label="icon", score=0.99)
    icon = AimMark(id=2, x=0.20, y=0.80, x0=0.16, y0=0.76, x1=0.24, y1=0.84, label="icon", score=0.6)
    art = AimMark(id=10, x=0.50, y=0.30, x0=0.46, y0=0.26, x1=0.54, y1=0.34, score=90.0)
    dropped = _merge_aim_marks([], [art], cap=8, gui=[giant])
    assert any(abs(m.x - 0.50) < 0.02 and abs(m.y - 0.30) < 0.02 for m in dropped)
    merged = _merge_aim_marks([], [art], cap=8, gui=[icon])
    assert any(abs(m.x - 0.20) < 0.02 for m in merged)
    assert any(abs(m.y - 0.30) < 0.02 for m in merged)


def test_build_aim_empty_gui_matches_ocr_cv() -> None:
    from loop.os.aim import build_aim

    image = _buttons()
    with_empty = build_aim(image, gui_marks=[])
    defaulted = build_aim(image)
    assert [m.id for m in with_empty.marks] == [m.id for m in defaulted.marks]
    assert with_empty.listing() == defaulted.listing()


def test_merge_drops_top_bar_icons_for_hand_card() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    icon = AimMark(id=1, x=0.27, y=0.04, x0=0.24, y0=0.01, x1=0.30, y1=0.07, label="icon", score=0.9)
    gold = AimMark(id=2, x=0.10, y=0.04, x0=0.06, y0=0.01, x1=0.14, y1=0.07, label="6230", score=0.8)
    strike = AimMark(id=3, x=0.40, y=0.85, x0=0.32, y0=0.80, x1=0.48, y1=0.90, label="Strike")
    card = AimMark(id=4, x=0.40, y=0.85, x0=0.30, y0=0.78, x1=0.50, y1=0.94, label="icon", score=0.9)
    merged = _merge_aim_marks([strike], [], cap=8, gui=[icon, gold, card], screen="combat")
    assert any(m.label == "Strike" for m in merged)
    assert all(not (m.y <= 0.12 and (m.label or "").lower() in {"icon", "6230"}) for m in merged)


def test_merge_end_turn_is_progress() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks, label_kind

    assert label_kind("End Turn 4") == "progress"
    end = AimMark(id=1, x=0.82, y=0.88, x0=0.72, y0=0.84, x1=0.92, y1=0.94, label="End Turn 4")
    icon = AimMark(id=2, x=0.20, y=0.40, x0=0.16, y0=0.36, x1=0.24, y1=0.44, label="icon", score=0.9)
    merged = _merge_aim_marks([end], [], cap=8, gui=[icon])
    assert merged[0].label == "End Turn 4"


def test_tutorial_prose_cannot_mint_start_or_next_controls() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks, _ocr_click_label, _prefer_menu_label

    sentence = "Block wears off at the start of your next turn."
    assert _prefer_menu_label(sentence) == sentence
    assert not _ocr_click_label(sentence)

    # RapidOCR split the live sentence at exactly this word and previously
    # promoted it ahead of the real right-side paginator.
    body_start = AimMark(
        id=1, x=0.64, y=0.54, x0=0.53, y0=0.52, x1=0.75, y1=0.56,
        label="Start", score=0.99,
    )
    right_arrow = AimMark(
        id=2, x=0.97, y=0.50, x0=0.94, y0=0.44, x1=1.0, y1=0.56,
        label="icon", score=0.9,
    )
    merged = _merge_aim_marks(
        [body_start], [], cap=8, gui=[right_arrow], screen="tutorial"
    )

    assert [mark.label for mark in merged] == ["Next"]


def test_merge_tutorial_drops_body_copy_keeps_dialog_and_play() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    title = AimMark(
        id=1, x=0.50, y=0.78, x0=0.30, y0=0.74, x1=0.70, y1=0.82, label="Combat Basics"
    )
    card = AimMark(
        id=2, x=0.50, y=0.78, x0=0.32, y0=0.72, x1=0.68, y1=0.86, label="icon", score=0.9
    )
    yes = AimMark(id=3, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="Yes")
    no = AimMark(id=4, x=0.32, y=0.86, x0=0.26, y0=0.82, x1=0.38, y1=0.90, label="No")
    end = AimMark(
        id=5, x=0.82, y=0.88, x0=0.72, y0=0.84, x1=0.92, y1=0.94, label="End Turn"
    )
    merged = _merge_aim_marks(
        [title, yes, no, end], [], cap=8, gui=[card], screen="tutorial"
    )
    labels = {m.label for m in merged}
    assert "Yes" in labels and "No" in labels
    assert not any("combat basics" in (m.label or "").lower() for m in merged)
    assert "End Turn" in labels


def test_merge_drops_end_fragment_when_end_turn_exists() -> None:
    from loop.os.aim import AimMark, _compact_label, _merge_aim_marks

    end = AimMark(id=1, x=0.78, y=0.88, x0=0.74, y0=0.84, x1=0.82, y1=0.92, label="End")
    turn = AimMark(
        id=2, x=0.86, y=0.88, x0=0.82, y0=0.84, x1=0.94, y1=0.94, label="End Turn"
    )
    merged = _merge_aim_marks([end, turn], [], cap=8)
    assert any("end turn" in (m.label or "").lower() for m in merged)
    assert not any(_compact_label(m.label) == "end" for m in merged)


def test_merge_drops_version_string_ocr() -> None:
    from loop.os.aim import AimMark, _merge_aim_marks

    ver = AimMark(
        id=1, x=0.10, y=0.96, x0=0.02, y0=0.94, x1=0.22, y1=0.99, label="[v0.111.0]"
    )
    yes = AimMark(id=2, x=0.54, y=0.86, x0=0.48, y0=0.82, x1=0.60, y1=0.90, label="Yes")
    merged = _merge_aim_marks([ver, yes], [], cap=8)
    assert all("v0" not in (m.label or "").lower() for m in merged)


def test_fill_and_steer_skip_end_turn_when_energy_remains() -> None:
    from loop.log.schema import ActionLog
    from loop.os.aim import AimGuide, AimMark, fill_from_aim, steer_to_labeled

    strike = AimMark(
        id=3, x=0.40, y=0.85, x0=0.32, y0=0.80, x1=0.48, y1=0.90, label="Strike", score=0.8
    )
    end = AimMark(
        id=8, x=0.82, y=0.88, x0=0.72, y0=0.84, x1=0.92, y1=0.94, label="End Turn", score=0.9
    )
    guide = AimGuide(
        marks=(strike, end),
        last_xy=None,
        image=Image.new("RGB", (8, 8), (0, 0, 0)),
    )
    hud = {"energy": 3, "screen": "combat"}
    filled = fill_from_aim(guide, recent=[], duration_ms=400, hud=hud)
    assert filled is not None
    hit = guide.by_id(int(filled.mark))
    assert hit is not None
    assert hit.label == "Strike"
    copied = ActionLog(mark=8, click_x=0.82, click_y=0.88, duration_ms=400)
    steered = steer_to_labeled(
        copied, guide, thought="click Strike", choice="Strike", hud=hud
    )
    assert steered.mark == 3
    stay = ActionLog(mark=3, click_x=0.40, click_y=0.85, duration_ms=400)
    skipped = steer_to_labeled(
        stay, guide, thought="end this turn and click End Turn", hud=hud
    )
    assert skipped.mark == 3
    already = ActionLog(mark=8, click_x=0.82, click_y=0.88, duration_ms=400)
    kept = steer_to_labeled(already, guide, thought="click End Turn", hud=hud)
    assert kept.mark == 8

def test_strip_misplaced_end_turn_in_enemy_band() -> None:
    from loop.os.aim import AimMark, strip_misplaced_progress

    ghost = AimMark(id=1, x=0.93, y=0.04, x0=0.9, y0=0.02, x1=0.96, y1=0.06, label="End Turn")
    real = AimMark(id=2, x=0.89, y=0.82, x0=0.85, y0=0.78, x1=0.93, y1=0.86, label="End Turn")
    strike = AimMark(id=3, x=0.69, y=0.88, x0=0.64, y0=0.84, x1=0.74, y1=0.92, label="Strike")
    kept = strip_misplaced_progress([ghost, real, strike])
    labels = {(round(m.y, 2), m.label) for m in kept}
    assert (0.04, "End Turn") not in labels
    assert any(m.label == "End Turn" and m.y > 0.5 for m in kept)
    assert any(m.label == "Strike" for m in kept)


def test_ground_combat_labels_binds_only_observed_geometry() -> None:
    from loop.os.aim import (
        AimGuide,
        AimMark,
        action_from_labels,
        ground_combat_labels,
        in_enemy_band,
    )

    end = AimMark(id=1, x=0.93, y=0.04, x0=0.9, y0=0.02, x1=0.96, y1=0.06, label="End Turn")
    strike = AimMark(
        id=2, x=0.69, y=0.88, x0=0.64, y0=0.84, x1=0.74, y1=0.92,
        label="Strike", object_id="card_1", role="card", source="typed",
    )
    icon = AimMark(id=3, x=0.55, y=0.30, x0=0.48, y0=0.22, x1=0.62, y1=0.38, label="icon")
    hand_icon = AimMark(id=4, x=0.45, y=0.88, x0=0.38, y0=0.78, x1=0.52, y1=1.0, label="icon")
    grounded = ground_combat_labels(
        [end, strike, icon, hand_icon],
        enemies=["Jaw Worm"],
        hand=["Strike", "Defend"],
        screen="combat",
    )
    labels = {m.label for m in grounded}
    assert "Jaw Worm" in labels
    assert "Strike" in labels
    # Semantic hand lists may not turn an arbitrary lower-band icon into a card.
    assert "Defend" not in labels
    assert next(m for m in grounded if abs(m.x - .45) < .001).label == "icon"
    jaw = next(m for m in grounded if m.label == "Jaw Worm")
    assert in_enemy_band(jaw.y, jaw.x)
    guide = AimGuide.from_marks(Image.new("RGB", (64, 64)), grounded)
    action = action_from_labels(guide, choice="Strike", target_label="Jaw Worm")
    assert action is not None
    assert action.mark is not None
    assert action.target is not None
    assert guide.by_id(int(action.target)).label == "Jaw Worm"


def test_seed_enemies_from_host_skips_unidentified_without_geometry() -> None:
    """Unnamed host rows without HP OCR must not invent a y=0.32 clone.

    Live STS2 sprites sit mid-board; apply_enemy_bboxes owns unnamed placement.
    """
    from loop.os.aim import AimMark, in_enemy_band, seed_enemies_from_host

    marks = [
        AimMark(id=1, x=0.89, y=0.82, x0=0.85, y0=0.78, x1=0.93, y1=0.86, label="Strike"),
        AimMark(id=2, x=0.93, y=0.04, x0=0.9, y0=0.02, x1=0.96, y1=0.06, label=":Icon"),
    ]
    host = {
        "screen": "combat",
        "player": {"hp": 80, "hp_max": 80},
        "enemies": [{"hp": 44, "hp_max": 44, "label": None}],
    }
    seeded = seed_enemies_from_host(marks, host_stats=host)
    assert "unidentified_enemy" not in {m.label for m in seeded}
    assert not any(m.label == ":Icon" and in_enemy_band(m.y, m.x) for m in seeded)


def test_seed_enemies_from_host_never_uses_bboxless_library_geometry() -> None:
    from loop.memory.lexicon import MarkLexicon
    from loop.os.aim import AimMark, seed_enemies_from_host

    lexicon = MarkLexicon()
    lexicon.remember("combat", 0.55, 0.32, "Jaw Worm", source="hover")
    marks = [
        AimMark(id=1, x=0.89, y=0.82, x0=0.85, y0=0.78, x1=0.93, y1=0.86, label="Strike"),
    ]
    host = {
        "screen": "combat",
        "player": {"hp": 80, "hp_max": 80},
        "enemies": [{"hp": 44, "hp_max": 44, "label": None}],
    }
    seeded = seed_enemies_from_host(
        marks, host_stats=host, lexicon=lexicon, screen="combat"
    )
    assert "Jaw Worm" not in {m.label for m in seeded}


def test_fuse_dino_marks_requires_ocr_or_overlap() -> None:
    from loop.os.aim import AimGuide, AimMark, fuse_dino_marks

    image = Image.new("RGB", (400, 300), (20, 20, 30))
    base = AimGuide.from_marks(
        image,
        [AimMark(id=1, x=0.5, y=0.5, x0=0.45, y0=0.45, x1=0.55, y1=0.55, label="")],
    )
    ocr = [
        AimMark(
            id=0,
            x=0.5,
            y=0.7,
            x0=0.42,
            y0=0.66,
            x1=0.58,
            y1=0.74,
            label="Got it",
            score=0.9,
        )
    ]
    dino_far = [
        AimMark(
            id=0,
            x=0.1,
            y=0.1,
            x0=0.05,
            y0=0.05,
            x1=0.15,
            y1=0.15,
            label="Strike",
            score=0.8,
        )
    ]
    fused = fuse_dino_marks(
        base,
        dino_far,
        ocr_words=ocr,
        allowed_controls={"Got it"},
    )
    labels = {m.label for m in fused.marks if m.label}
    assert "Strike" not in labels

    dino_near = [
        AimMark(
            id=0,
            x=0.5,
            y=0.7,
            x0=0.42,
            y0=0.66,
            x1=0.58,
            y1=0.74,
            label="Got it",
            score=0.85,
        )
    ]
    fused2 = fuse_dino_marks(
        base,
        dino_near,
        ocr_words=ocr,
        allowed_controls={"Got it"},
    )
    labels2 = {m.label for m in fused2.marks if m.label}
    assert "Got it" in labels2


def test_dino_ground_labels_uses_detector_detect(monkeypatch) -> None:
    from avatar.gsm.rule.detector import DetectedBox
    from loop.os.aim import dino_ground_labels

    image = Image.new("RGB", (120, 80), (30, 30, 30))
    seen: dict[str, object] = {}

    class _Det:
        family = "grounding-dino"

        def detect(self, frame, labels, **kwargs):  # type: ignore[no-untyped-def]
            seen["labels"] = list(labels)
            seen["kwargs"] = dict(kwargs)
            return [DetectedBox(label="Strike", x=0.4, y=0.7, w=0.1, h=0.08, score=0.9)]

    def fake_detect(detector, frame, labels, **kwargs):  # type: ignore[no-untyped-def]
        seen["via"] = "_detector_detect"
        return list(detector.detect(frame, labels, **kwargs))

    monkeypatch.setattr("loop.os.ground._detector_detect", fake_detect)
    marks = dino_ground_labels(image, ["Strike"], _Det())
    assert seen.get("via") == "_detector_detect"
    assert seen.get("labels") == ["Strike"]
    assert marks and marks[0].label == "Strike"


def test_number_spatial_nodes_labels_map_icons() -> None:
    from loop.os.aim import AimMark, number_spatial_nodes

    marks = [
        AimMark(id=1, x=0.35, y=0.75, x0=0.32, y0=0.72, x1=0.38, y1=0.78, label="icon", score=90.0),
        AimMark(id=2, x=0.45, y=0.45, x0=0.42, y0=0.42, x1=0.48, y1=0.48, label="icon", score=40.0),
        AimMark(id=3, x=0.90, y=0.60, x0=0.86, y0=0.56, x1=0.94, y1=0.64, label="Enemy", score=30.0),
        AimMark(id=4, x=0.47, y=0.90, x0=0.44, y0=0.87, x1=0.50, y1=0.93, label="icon", score=50.0),
    ]
    ocr = [
        AimMark(id=10, x=0.90, y=0.35, x0=0.86, y0=0.32, x1=0.94, y1=0.38, label="Legend"),
        AimMark(id=11, x=0.91, y=0.60, x0=0.87, y0=0.56, x1=0.95, y1=0.64, label="Enemy"),
    ]
    numbered = number_spatial_nodes(marks, screen="map", ocr_words=ocr)
    labels = {m.label for m in numbered}
    assert "node_0" in labels
    assert "node_1" in labels
    assert "Enemy" in labels
    assert not any(lab.startswith("node_") and m.y > 0.85 for m, lab in ((m, m.label) for m in numbered))


def test_ground_spatial_labels_keeps_high_confidence_nodes() -> None:
    from loop.os.aim import AimMark, ground_spatial_labels

    marks = [
        AimMark(id=1, x=0.35, y=0.75, x0=0.32, y0=0.72, x1=0.38, y1=0.78, label="node_0", score=90.0),
        AimMark(id=2, x=0.47, y=0.90, x0=0.44, y0=0.87, x1=0.50, y1=0.93, label="Share", score=50.0),
    ]
    controls = [
        {"label": "node_0", "role": "button", "confidence": "high"},
        {"label": "node_1", "role": "button", "confidence": "low"},
    ]
    grounded = ground_spatial_labels(marks, controls, screen="map")
    labels = [m.label for m in grounded]
    assert "node_0" in labels
    assert "Share" not in labels
    assert "" in labels or all(lab != "Share" for lab in labels)


def test_apply_enemy_bboxes_includes_midboard_sprite() -> None:
    from loop.os.aim import AimMark, apply_enemy_bboxes

    out = apply_enemy_bboxes(
        [],
        [{"label": "unidentified_enemy", "bbox": [690, 580, 810, 710]}],
    )
    assert len(out) == 1
    assert abs(out[0].x - 0.75) < 0.02
    assert abs(out[0].y - 0.645) < 0.02
    assert out[0].label == "unidentified_enemy"

    arbitrary_layout = apply_enemy_bboxes(
        [AimMark(id=1, x=0.4, y=0.85, x0=0.3, y0=0.8, x1=0.5, y1=0.9, label="Strike")],
        [{"role": "enemy", "label": "unidentified_enemy", "bbox": [100, 850, 200, 950]}],
    )
    assert any(m.role == "enemy" for m in arbitrary_layout)

    hp_only = apply_enemy_bboxes(
        [AimMark(id=2, x=0.75, y=0.35, x0=0.70, y0=0.32, x1=0.80, y1=0.38, label="unidentified_enemy")],
        [{"entity_id": "unknown_character_1", "role": "enemy", "label": "unknown_character_1", "bbox": [700, 320, 800, 380]}],
    )
    assert any(m.role == "enemy" for m in hp_only)


def test_apply_player_bboxes_stamps_left_lane_hero() -> None:
    from loop.os.aim import AimMark, apply_enemy_bboxes

    proposal = AimMark(4, .25, .58, .20, .47, .30, .69, "unknown_character", .6, role="player")
    out = apply_enemy_bboxes(
        [proposal],
        [{"id": "player_1", "label": "player", "bbox": [200, 470, 300, 690]}],
        role="player",
    )
    assert len(out) == 1
    assert out[0].id == 4
    assert out[0].label == "player"
    assert out[0].role == "player"
    assert out[0].object_id == "player_1"


def test_hand_proposals_preserve_duplicate_card_instances() -> None:
    from loop.os.aim import AimMark, propose_hand_cards

    words = [
        AimMark(i, x, .84, x - .02, .82, x + .02, .86, label, .99)
        for i, (x, label) in enumerate(
            ((.30, "Defend"), (.40, "Strike"), (.50, "Strike"), (.60, "Defend"), (.70, "Strike")),
            start=1,
        )
    ]
    words.append(AimMark(9, .90, .83, .85, .80, .95, .86, "End Turn 3", .99))
    cards = propose_hand_cards(words, screen="combat")
    assert [card.label for card in cards] == ["Defend", "Strike", "Strike", "Defend", "Strike"]
    assert all(card.object_id == "" for card in cards)
    assert all(card.role == "hand_candidate" and card.source == "ocr-hand" for card in cards)


def test_hand_proposals_reject_descriptions_and_ocr_fragments() -> None:
    from loop.os.aim import AimMark, propose_hand_cards

    words = [
        AimMark(1, .35, .84, .30, .82, .40, .86, "Strike", .99),
        AimMark(2, .46, .84, .40, .80, .52, .88, "Deal 6 damage", .99),
        AimMark(3, .56, .84, .52, .82, .60, .86, "Apply", .99),
        AimMark(4, .66, .84, .62, .82, .70, .86, "damage", .99),
        AimMark(5, .70, .84, .67, .82, .73, .86, "trike", .99),
        AimMark(6, .74, .84, .71, .82, .77, .86, "Defe", .99),
        AimMark(7, .58, .84, .52, .80, .64, .88, "Gain 3 Strength this turn.", .99),
        AimMark(8, .62, .84, .56, .80, .68, .88, "Deal 7 damage.", .99),
    ]
    cards = propose_hand_cards(words, screen="combat")
    assert [card.label for card in cards] == ["Strike"]
    assert cards[0].role == "hand_candidate"
    assert cards[0].object_id == ""


def test_unknown_vlm_hand_bboxes_preserve_ocr_card_names_and_instances() -> None:
    from loop.os.aim import AimMark, apply_hand_bboxes

    marks = [
        AimMark(
            i, x, .91, x - .07, .79, x + .07, 1.0, label, .96,
            object_id=f"ocr_{i}", role="card", source="ocr-hand", confidence=.96,
        )
        for i, (x, label) in enumerate(
            ((.36, "Defend"), (.45, "Strike"), (.55, "Strike"), (.64, "Defend")),
            start=1,
        )
    ]
    rows = [
        {
            "id": f"card_{i}", "label": "unknown_card", "role": "card",
            "bbox": [int((x - .055) * 1000), 795, int((x + .055) * 1000), 997],
        }
        for i, x in enumerate((.36, .45, .55, .64), start=1)
    ]
    out = apply_hand_bboxes(marks, rows)
    cards = [mark for mark in out if mark.role == "card"]
    assert [card.label for card in cards] == ["Defend", "Strike", "Strike", "Defend"]
    assert [card.object_id for card in cards] == [f"card_{i}" for i in range(1, 5)]
    assert len(cards) == 4


def test_hand_bboxes_fail_closed_for_missing_or_ambiguous_geometry() -> None:
    from loop.os.aim import AimMark, apply_hand_bboxes

    candidates = [
        AimMark(1, .45, .90, .38, .79, .52, 1.0, "Strike", role="hand_candidate", source="ocr-hand"),
        AimMark(2, .46, .90, .39, .79, .53, 1.0, "Strike", role="hand_candidate", source="ocr-hand"),
    ]
    rows = [
        {"id": "card_missing", "label": "Defend"},
        {"id": "card_ambiguous", "label": "Strike", "bbox": [390, 795, 520, 997]},
    ]
    out = apply_hand_bboxes(candidates, rows)
    assert [mark.role for mark in out] == ["hand_candidate", "hand_candidate"]
    assert all(mark.object_id == "" for mark in out)


def test_hand_bbox_promotion_requires_one_to_one_overlap() -> None:
    from loop.os.aim import AimMark, apply_hand_bboxes

    broad = AimMark(
        1, .45, .90, .30, .79, .60, 1.0, "Strike",
        role="hand_candidate", source="ocr-hand",
    )
    rows = [
        {"id": "card_1", "label": "Strike", "bbox": [320, 795, 470, 997]},
        {"id": "card_2", "label": "Defend", "bbox": [430, 795, 580, 997]},
    ]

    out = apply_hand_bboxes([broad], rows)

    assert len(out) == 1
    assert out[0].role == "hand_candidate"
    assert out[0].object_id == ""


def test_hand_bboxes_do_not_promote_left_energy_orb() -> None:
    from loop.os.aim import AimMark, apply_hand_bboxes

    orb = AimMark(1, .10, .88, .04, .76, .16, 1.0, "Strike", role="hand_candidate", source="ocr-hand")
    out = apply_hand_bboxes(
        [orb], [{"id": "card_1", "label": "Strike", "bbox": [40, 760, 160, 998]}]
    )
    assert len(out) == 1
    assert out[0].role == "hand_candidate"
    assert out[0].object_id == ""


def test_typed_hand_bboxes_promote_exact_strike_defend_cardinality() -> None:
    from loop.os.aim import AimMark, apply_hand_bboxes

    candidates = [
        AimMark(i, x, .90, x - .06, .79, x + .06, 1.0, label, role="hand_candidate", source="ocr-hand")
        for i, (x, label) in enumerate(((.34, "Strike"), (.48, "Defend"), (.62, "Strike")), start=1)
    ]
    rows = [
        {"id": f"card_{i}", "label": label, "bbox": [int((x - .05) * 1000), 795, int((x + .05) * 1000), 997]}
        for i, (x, label) in enumerate(((.34, "Strike"), (.48, "Defend"), (.62, "Strike")), start=1)
    ] + [{"id": "card_unmatched", "label": "Defend", "bbox": [700, 795, 780, 997]}]
    out = apply_hand_bboxes(candidates, rows)
    cards = [mark for mark in out if mark.role == "card"]
    assert [mark.label for mark in cards] == ["Strike", "Defend", "Strike"]
    assert [mark.object_id for mark in cards] == ["card_1", "card_2", "card_3"]
    assert len(out) == 3


def test_hand_bboxes_mint_typed_geometry_when_no_candidates() -> None:
    from loop.os.aim import apply_hand_bboxes

    rows = [
        {"id": "card_1", "label": "Strike", "bbox": [246, 857, 363, 994]},
        {"id": "card_2", "label": "Bash", "bbox": [340, 857, 457, 994]},
        {"id": "card_3", "label": "Defend", "bbox": [434, 857, 551, 994]},
        {"id": "card_4", "label": "Defend", "bbox": [528, 857, 645, 994]},
        {"id": "card_5", "label": "Defend", "bbox": [622, 857, 739, 994]},
    ]
    out = apply_hand_bboxes([], rows)
    cards = [mark for mark in out if mark.role == "card"]
    assert [card.object_id for card in cards] == [f"card_{i}" for i in range(1, 6)]
    assert [card.label for card in cards] == ["Strike", "Bash", "Defend", "Defend", "Defend"]
    assert len(cards) == 5


def test_hand_bboxes_mint_typed_geometry_beside_gui_icons() -> None:
    from loop.os.aim import AimMark, apply_hand_bboxes

    icons = [
        AimMark(i, x, .90, x - .04, .80, x + .04, 1.0, "", role="icon", source="gui")
        for i, x in enumerate((.30, .45, .60), start=1)
    ]
    rows = [
        {"id": "card_1", "label": "Strike", "bbox": [246, 857, 363, 994]},
        {"id": "card_2", "label": "Bash", "bbox": [340, 857, 457, 994]},
        {"id": "card_3", "label": "Defend", "bbox": [434, 857, 551, 994]},
        {"id": "card_4", "label": "Defend", "bbox": [528, 857, 645, 994]},
        {"id": "card_5", "label": "Defend", "bbox": [622, 857, 739, 994]},
    ]
    out = apply_hand_bboxes(icons, rows)
    cards = [mark for mark in out if mark.role == "card"]
    leftover_icons = [mark for mark in out if mark.role == "icon"]
    assert [card.object_id for card in cards] == [f"card_{i}" for i in range(1, 6)]
    assert [card.label for card in cards] == ["Strike", "Bash", "Defend", "Defend", "Defend"]
    assert leftover_icons
    assert all(mark.role == "icon" and mark.object_id == "" for mark in leftover_icons)


def test_duplicate_card_label_requires_instance_id_path() -> None:
    from loop.os.aim import AimGuide, AimMark, action_from_labels

    marks = [
        AimMark(i, x, .9, x - .07, .78, x + .07, 1.0, "Strike", .9, object_id=f"card_{i}", role="card")
        for i, x in ((1, .4), (2, .55))
    ]
    guide = AimGuide.from_marks(Image.new("RGB", (64, 64)), marks)
    assert action_from_labels(guide, choice="Strike") is None


def test_enemy_bbox_relabels_existing_board_geometry() -> None:
    from loop.os.aim import AimMark, apply_enemy_bboxes

    proposal = AimMark(4, .745, .625, .688, .572, .803, .678, "", .6, role="enemy")
    out = apply_enemy_bboxes(
        [proposal],
        [{"id": "enemy_1", "label": "unidentified_enemy", "bbox": [688, 572, 810, 710]}],
    )
    assert len(out) == 1
    assert out[0].id == 4
    assert out[0].object_id == "enemy_1"
    assert out[0].role == "enemy"


def test_real_failed_run_frame_has_five_cards_and_no_end_turn_card(monkeypatch) -> None:
    from pathlib import Path
    from loop.os.aim import build_marks, ocr_click_marks

    path = Path(__file__).resolve().parents[2] / "runs/20260904T073454Z_sts2_collect/frames/62cbfc6033/000001_obs.jpg"
    if not path.exists():
        pytest.skip("diagnostic run frame not present")
    monkeypatch.setenv("AVATAR_OCR", "auto")
    image = Image.open(path).convert("RGB")
    marks = build_marks(image, screen="combat", words=ocr_click_marks(image), gui_marks=[])
    candidates = [mark for mark in marks if mark.role == "hand_candidate"]
    assert len(candidates) == 5
    assert [card.label for card in candidates].count("Strike") == 3
    assert [card.label for card in candidates].count("Defend") == 2
    assert all(card.object_id == "" for card in candidates)
    assert all("end turn" not in card.label.lower() for card in candidates)


def test_step20_enlarged_card_replay_has_no_executable_description_cards(
    monkeypatch,
) -> None:
    from loop.os.aim import build_marks, ocr_click_marks
    from loop.os.frame_model import build_action_surface

    root = Path(__file__).resolve().parents[2]
    path = (
        root / "runs" / "20260910T024029Z_sts2_collect" / "frames"
        / "6f3efd017c" / "000020_obs.jpg"
    )
    if not path.exists():
        pytest.skip("local diagnosed run artifact is not packaged")
    monkeypatch.setenv("AVATAR_OCR", "auto")
    image = Image.open(path).convert("RGB")
    marks = build_marks(
        image, screen="combat", words=ocr_click_marks(image), gui_marks=[]
    )
    forbidden = {"gain 3 strength this turn", "deal 7 damage", "trike", "defe"}
    candidates = {
        " ".join(str(mark.label or "").casefold().rstrip(".").split())
        for mark in marks if mark.role == "hand_candidate"
    }
    assert not candidates & forbidden

    surface = build_action_surface(
        marks, run_id="replay", step_id=20, screen="combat", activity="combat",
    )
    assert not any(obj.role == "card" for obj in surface.interactive())
    assert "play_card" not in surface.legal_intents


def test_heuristic_targets_midboard_anonymous_enemy() -> None:
    from loop.agent.heuristic import heuristic_action
    from loop.os.aim import AimGuide, AimMark

    image = Image.new("RGB", (64, 64))
    guide = AimGuide.from_marks(
        image,
        [
            AimMark(id=1, x=0.5, y=0.86, x0=0.4, y0=0.8, x1=0.6, y1=0.92, label="Strike"),
            AimMark(
                id=2,
                x=0.75,
                y=0.645,
                x0=0.69,
                y0=0.58,
                x1=0.81,
                y1=0.71,
                label="unidentified_enemy",
            ),
        ],
    )
    action = heuristic_action(
        guide,
        metrics={"screen": "combat", "energy": 3},
        selected=None,
        recent=[],
        duration_ms=400,
    )
    assert action is not None
    assert action.mark == 1
    assert action.target == 2


def test_ocr_junk_rejects_cjk_identity_keeps_card_names() -> None:
    from loop.os.aim import _ocr_junk

    assert _ocr_junk("12入") is True
    assert _ocr_junk("12") is True
    assert _ocr_junk("Strike") is False
    assert _ocr_junk("End Turn") is False


def _box(
    mid: int,
    x: float,
    y: float,
    *,
    label: str = "",
    score: float = 0.8,
    half: float = 0.04,
    source: str = "",
) -> "AimMark":
    from loop.os.aim import AimMark

    return AimMark(
        id=mid,
        x=x,
        y=y,
        x0=x - half,
        y0=y - half,
        x1=x + half,
        y1=y + half,
        label=label,
        score=score,
        source=source,
    )


def test_merge_keeps_complementary_cv_when_gui_present() -> None:
    from loop.os.aim import _is_placeholder_label, _merge_aim_marks

    gui = _box(1, 0.20, 0.80, label="icon", source="gui")
    cv = _box(2, 0.50, 0.32, score=90.0, source="cv")
    audit: dict = {}
    merged = _merge_aim_marks([], [cv], cap=8, gui=[gui], audit=audit)
    assert any(abs(m.x - 0.20) < 0.02 for m in merged)
    assert any(abs(m.x - 0.50) < 0.02 and abs(m.y - 0.32) < 0.02 for m in merged)
    by_xy = {(round(m.x, 2), round(m.y, 2)): m for m in merged}
    assert by_xy[(0.2, 0.8)].source == "gui"
    assert by_xy[(0.5, 0.32)].source == "cv"
    assert audit["cv_reserve"] >= 1
    for mark in merged:
        if _is_placeholder_label(mark.label) or mark.label in {"icon", "node_0", ""}:
            assert mark.role == ""
            assert mark.confidence == 0.0


def test_merge_dense_gui_cluster_cannot_starve_distant_cv() -> None:
    from loop.os.aim import _merge_aim_marks

    cluster = [_box(i, 0.90, 0.16 + i * 0.07, label="icon", score=0.9) for i in range(12)]
    nodes = [
        _box(20, 0.40, 0.36, score=80.0),
        _box(21, 0.52, 0.44, score=78.0),
        _box(22, 0.46, 0.54, score=76.0),
        _box(23, 0.58, 0.38, score=74.0),
    ]
    audit: dict = {}
    merged = _merge_aim_marks([], nodes, cap=8, gui=cluster, screen="map", audit=audit)
    cv_kept = [m for m in merged if abs(m.x - 0.90) > 0.2]
    assert len(cv_kept) >= 3
    assert audit["cv_reserve"] >= 3


def test_merge_dedupes_overlapping_cv_against_gui() -> None:
    from loop.os.aim import _merge_aim_marks

    gui = _box(1, 0.50, 0.40, label="icon", score=0.9, half=0.06)
    cv = _box(2, 0.51, 0.41, score=90.0, half=0.05)
    merged = _merge_aim_marks([], [cv], cap=8, gui=[gui])
    near = [m for m in merged if abs(m.x - 0.50) < 0.05 and abs(m.y - 0.40) < 0.05]
    assert len(near) == 1
    assert near[0].source == "gui"


def test_placeholder_names_do_not_become_verified() -> None:
    from loop.os.aim import _is_placeholder_label, _merge_aim_marks

    gui = _box(1, 0.22, 0.80, label="icon")
    named = _box(2, 0.48, 0.40, label="node_0", score=70.0)
    merged = _merge_aim_marks([], [named], cap=8, gui=[gui], screen="map")
    node = next(m for m in merged if "node" in (m.label or "").lower() or abs(m.x - 0.48) < 0.02)
    assert node.role == ""
    assert node.confidence == 0.0
    assert _is_placeholder_label("icon")
    assert not (node.role in {"button", "card", "enemy"})


def test_merge_cached_does_not_reattach_clustered_duplicate() -> None:
    from loop.os.aim import AimMark, in_combat_sprite_band, merge_cached_enemy_marks

    current = [
        AimMark(
            id=1, x=0.50, y=0.90, x0=0.40, y0=0.80, x1=0.60, y1=1.0,
            label="Strike", role="card", source="ocr-hand",
        ),
        AimMark(
            id=2, x=0.75, y=0.645, x0=0.69, y0=0.57, x1=0.81, y1=0.72,
            label="Nibbit", role="enemy", object_id="enemy_1",
            source="test", confidence=0.95,
        ),
    ]
    cached = [
        AimMark(
            id=9, x=0.75, y=0.54, x0=0.70, y0=0.50, x1=0.80, y1=0.58,
            label="Nibbit", role="enemy", object_id="enemy_2",
            source="cache", confidence=0.55,
        ),
    ]
    merged = merge_cached_enemy_marks(current, cached)
    enemy_band = [
        mark for mark in merged
        if in_combat_sprite_band(mark.y, mark.x)
        and (mark.role or "").strip().lower() in {"enemy", "target"}
    ]
    if not enemy_band:
        enemy_band = [mark for mark in merged if in_combat_sprite_band(mark.y, mark.x)]
    assert len(enemy_band) == 1, [(m.object_id, m.label, m.y) for m in enemy_band]
