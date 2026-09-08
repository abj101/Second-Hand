from __future__ import annotations

from PIL import Image, ImageDraw

from loop.eval.outcome import column_shift, compute_step_outcome, view_changed
from loop.fixtures.base import chapel_success
from loop.log.schema import ActionLog, Observation, StepLog, StepResult
from loop.os.chords import walk_stall_count


def _step(
    *,
    stick_r: str | None = None,
    stick_l: str | None = None,
    tapped: list[str] | None = None,
    view_changed: bool = False,
    pixel_changed: bool = False,
    prompt_cleared: bool = False,
    prompt_source: str | None = None,
    prompt_on: bool | None = None,
    prompt_glyph: str | None = None,
    runes: float | None = None,
    health: float | None = 0.8,
    terminal: str = "timeout",
    step_id: int = 1,
) -> StepLog:
    return StepLog(
        session_id="s",
        episode_id="e",
        step_id=step_id,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="x.jpg"),
        action=ActionLog(
            stick_l=stick_l,
            stick_r=stick_r,
            tapped=list(tapped or []),
            duration_ms=400,
        ),
        result=StepResult(
            view_changed=view_changed,
            pixel_changed=pixel_changed,
            prompt_cleared=prompt_cleared,
        ),
        metrics={"health_frac": health, "runes": runes, "fail_screen": False},
        terminal=terminal,  # type: ignore[arg-type]
        prompt_source=prompt_source,
        prompt_on=prompt_on,
        prompt_glyph=prompt_glyph,
    )


def _stripe(width: int = 64, height: int = 64) -> Image.Image:
    image = Image.new("RGB", (width, height), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 8):
        draw.rectangle((x, 0, x + 3, height), fill=(180, 180, 180))
    return image


def _bar(width: int = 80, height: int = 80, x: int = 30) -> Image.Image:
    image = Image.new("RGB", (width, height), (30, 30, 30))
    ImageDraw.Draw(image).rectangle((x, 0, x + 6, height), fill=(220, 220, 220))
    return image


def _shift_left(image: Image.Image, pixels: int) -> Image.Image:
    width, height = image.size
    out = Image.new("RGB", (width, height), (0, 0, 0))
    out.paste(image.crop((pixels, 0, width, height)), (0, 0))
    return out


def test_view_changed_true_on_synthetic_yaw() -> None:
    before = _bar()
    after = _shift_left(before, 6)
    changed, mad, shift = view_changed(before, after, stick_r="e")
    assert mad > 0.01
    assert shift > 0
    assert changed is True
    outcome = compute_step_outcome(
        before=before,
        after=after,
        action=ActionLog(stick_r="e", duration_ms=400),
        proposed=ActionLog(stick_r="e", duration_ms=400),
        prompt_before=False,
        prompt_after=False,
        pixel_changed=True,
    )
    assert outcome.view_changed is True
    assert outcome.executed_as_proposed is True
    assert outcome.stall is False


def test_view_changed_false_on_hud_flicker() -> None:
    before = Image.new("RGB", (80, 80), (40, 40, 40))
    after = before.copy()
    ImageDraw.Draw(after).rectangle((0, 0, 80, 8), fill=(255, 255, 255))
    changed, mad, _shift = view_changed(before, after, stick_r="e")
    assert mad < 0.01
    assert changed is False


def test_chapel_success_false_on_zero_look_timeout() -> None:
    steps = [_step(stick_l="n", step_id=i + 1) for i in range(9)]
    assert chapel_success(steps, "timeout") is False


def test_chapel_success_true_on_view_coverage() -> None:
    steps = [
        _step(stick_r="e", view_changed=True, pixel_changed=True, step_id=1),
        _step(stick_r="w", view_changed=True, pixel_changed=True, step_id=2),
        _step(stick_r="e", view_changed=True, pixel_changed=True, step_id=3),
        _step(stick_l="n", pixel_changed=True, step_id=4),
    ]
    assert chapel_success(steps, "timeout") is True


def test_chapel_success_false_on_fail_with_rune_gain() -> None:
    steps = [
        _step(runes=10, step_id=1),
        _step(runes=25, step_id=2),
        _step(runes=25, step_id=3),
    ]
    assert chapel_success(steps, "fail") is False
    assert chapel_success(steps, "timeout") is True
    flicker = [
        _step(runes=1, health=None, step_id=1),
        _step(runes=2, health=None, step_id=2),
        _step(runes=2, health=None, step_id=3),
    ]
    assert chapel_success(flicker, "timeout") is False
    one_hud = [
        _step(runes=10, step_id=1),
        _step(runes=25, step_id=2),
    ]
    assert chapel_success(one_hud, "timeout") is False


def test_chapel_success_ignores_dino_only_prompt_clear() -> None:
    steps = [
        _step(prompt_cleared=True, prompt_source="dino", step_id=1),
        _step(prompt_on=False, step_id=2),
    ]
    assert chapel_success(steps, "timeout") is False
    last_only = [_step(prompt_cleared=True, prompt_source="cv", prompt_on=True, step_id=1)]
    assert chapel_success(last_only, "timeout") is False
    flicker = [
        _step(prompt_cleared=True, prompt_source="cv", prompt_on=True, step_id=1),
        _step(prompt_on=True, step_id=2),
    ]
    assert chapel_success(flicker, "timeout") is False
    one_off = [
        _step(prompt_cleared=True, prompt_source="cv", prompt_on=True, step_id=1),
        _step(prompt_on=False, step_id=2),
    ]
    assert chapel_success(one_off, "timeout") is False
    walk_away = [
        _step(prompt_cleared=True, prompt_source="cv", prompt_on=True, step_id=1),
        _step(prompt_on=False, step_id=2),
        _step(prompt_on=False, step_id=3),
    ]
    assert chapel_success(walk_away, "timeout") is False
    opened = [
        _step(
            tapped=["a"],
            prompt_cleared=True,
            prompt_source="cv",
            prompt_on=True,
            prompt_glyph="a",
            step_id=1,
        ),
        _step(prompt_on=False, step_id=2),
        _step(prompt_on=False, step_id=3),
    ]
    assert chapel_success(opened, "timeout") is True
    lock_probe = [
        _step(
            stick_l="s",
            stick_r="w",
            prompt_on=True,
            prompt_glyph="a",
            prompt_source="cv",
            step_id=1,
        ),
        _step(
            tapped=["a"],
            prompt_cleared=True,
            prompt_source="cv",
            prompt_on=True,
            prompt_glyph="a",
            step_id=2,
        ),
        _step(prompt_on=False, step_id=3),
        _step(prompt_on=False, step_id=4),
    ]
    assert chapel_success(lock_probe, "timeout") is False


def test_walk_stall_ignores_face_taps() -> None:
    stall, walked = walk_stall_count(["stick_l_n", "x", "stick_l_n", "a", "stick_l_n"])
    assert stall == 3
    assert walked == "stick_l_n"


def test_column_shift_sign_matches_leftward_slide() -> None:
    before = _stripe()
    after = _shift_left(before, 3)
    assert column_shift(before, after) > 0
