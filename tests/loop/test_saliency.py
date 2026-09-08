from __future__ import annotations

from PIL import Image, ImageDraw

from loop.os.saliency import (
    bright_patches,
    burst_caption,
    compose_obs,
    contact_sheet,
    format_patches,
    pick_peak,
    pick_prompt_ocr,
    prompt_glyph,
    prompt_inset,
    prompt_visible,
    ui_band,
    ui_contrast,
)


def test_ui_band_is_bottom_center() -> None:
    image = Image.new("RGB", (200, 100), (0, 0, 0))
    ImageDraw.Draw(image).rectangle((80, 96, 120, 99), fill=(255, 255, 255))
    band = ui_band(image)
    assert band.size[0] < image.size[0]
    assert band.size[1] < image.size[1]
    assert band.size[1] <= 32
    # Bottom edge of the source is in the crop (not truncated at ~0.94).
    assert band.getpixel((band.size[0] // 2, band.size[1] - 2)) == (255, 255, 255)


def test_prompt_inset_and_compose_obs() -> None:
    image = Image.new("RGB", (320, 180), (12, 12, 12))
    draw = ImageDraw.Draw(image)
    draw.rectangle((95, 168, 225, 178), fill=(18, 18, 18))
    draw.rectangle((112, 170, 126, 176), fill=(40, 220, 40))
    draw.rectangle((135, 170, 200, 176), fill=(210, 210, 210))
    inset = prompt_inset(image)
    assert inset.size[0] >= 2 * int(320 * 0.56) - 2
    assert inset.size[1] >= 2 * int(180 * 0.22) - 2
    # Bright glyph / text chips survive the zoom somewhere in the inset.
    assert max(inset.convert("L").getextrema()) >= 100
    composed = compose_obs(image)
    assert composed.size[1] > image.size[1]
    assert composed.size[1] > ui_band(image).size[1]
    # Yellow PROMPT banner border under the full frame.
    border = composed.getpixel((1, image.size[1]))
    assert border[0] >= 200 and border[1] >= 180


def test_pick_prompt_ocr_prefers_verb() -> None:
    assert pick_prompt_ocr(["", "lll ###", "Open"]) == "Open"
    assert pick_prompt_ocr(["Need item to open", "abc"]) == "Need item to open"
    assert pick_prompt_ocr(["", "   "]) == ""


def test_bright_patches_find_left_glow() -> None:
    image = Image.new("RGB", (320, 180), (8, 8, 8))
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 40, 50, 90), fill=(220, 180, 255))
    patches = bright_patches(image, k=2)
    assert patches
    x, y = patches[0]
    assert x < 0.35
    assert y < 0.7
    assert "0." in format_patches(patches)
    assert "stick_l_" in format_patches(patches)


def test_walk_dir_maps_screen_to_stick_l() -> None:
    from loop.os.saliency import pick_walk_patch, walk_dir_for_point

    assert walk_dir_for_point(0.5, 0.5) == "n"
    # Mid-screen doors must walk forward, not strafe e/w.
    assert walk_dir_for_point(0.40, 0.51) == "n"
    assert walk_dir_for_point(0.62, 0.51) == "n"
    assert walk_dir_for_point(0.2, 0.5) == "nw"
    assert walk_dir_for_point(0.8, 0.5) == "ne"
    assert walk_dir_for_point(0.5, 0.12) == "n"
    picked = pick_walk_patch([(0.5, 0.15), (0.62, 0.48)])
    assert picked == (0.62, 0.48)


def test_pick_peak_prefers_motion() -> None:
    dark = Image.new("RGB", (200, 100), (10, 10, 10))
    moved = Image.new("RGB", (200, 100), (10, 10, 10))
    ImageDraw.Draw(moved).rectangle((20, 10, 80, 50), fill=(200, 200, 200))
    peak = pick_peak([dark, moved, dark])
    assert peak.tobytes() == moved.tobytes()


def test_prompt_visible_needs_bar_not_empty_floor() -> None:
    empty = Image.new("RGB", (320, 180), (12, 12, 12))
    assert prompt_visible(empty) is False
    # Mid-luma floor stone without bright glyphs stays false.
    floor = Image.new("RGB", (320, 180), (12, 12, 12))
    ImageDraw.Draw(floor).rectangle((90, 152, 230, 168), fill=(55, 55, 55))
    assert prompt_visible(floor) is False
    prompt = Image.new("RGB", (320, 180), (12, 12, 12))
    draw = ImageDraw.Draw(prompt)
    draw.rectangle((90, 171, 230, 175), fill=(55, 55, 55))
    # Bright glyph chips (button icons / text) on the bar.
    draw.rectangle((118, 171, 132, 175), fill=(230, 230, 230))
    draw.rectangle((140, 171, 190, 175), fill=(220, 220, 220))
    assert prompt_visible(prompt) is True
    assert ui_contrast(prompt) >= 0.0
    # Pickup / tutorial copy sits on the last rows; the old 0.84–0.94 crop missed it.
    low = Image.new("RGB", (320, 180), (12, 12, 12))
    draw = ImageDraw.Draw(low)
    draw.rectangle((90, 171, 230, 175), fill=(55, 55, 55))
    draw.rectangle((118, 171, 132, 175), fill=(230, 230, 230))
    draw.rectangle((140, 171, 190, 175), fill=(220, 220, 220))
    assert prompt_visible(low) is True
    # Thin near-black centered pill (closer to Elden Ring's prompt bar).
    thin = Image.new("RGB", (320, 180), (12, 12, 12))
    draw = ImageDraw.Draw(thin)
    draw.rectangle((95, 171, 225, 175), fill=(18, 18, 18))
    draw.rectangle((112, 171, 126, 175), fill=(230, 230, 230))
    draw.rectangle((135, 171, 200, 175), fill=(210, 210, 210))
    assert prompt_visible(thin) is True


def test_prompt_visible_read_message_regression() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parent / "fixtures" / "prompt_read_message.jpg"
    assert path.is_file(), f"missing regression fixture {path}"
    frame = Image.open(path).convert("RGB")
    assert prompt_visible(frame) is True
    inset = prompt_inset(frame)
    assert inset.size[0] > 100 and inset.size[1] > 20
    composed = compose_obs(frame)
    assert composed.size[1] > frame.size[1]
    # No-bar chapel-ish floor stays false.
    empty = Image.new("RGB", frame.size, (20, 18, 16))
    assert prompt_visible(empty) is False


def test_prompt_visible_open_door_regression() -> None:
    from pathlib import Path

    path = Path(__file__).resolve().parent / "fixtures" / "prompt_open_door.jpg"
    assert path.is_file(), f"missing regression fixture {path}"
    frame = Image.open(path).convert("RGB")
    assert prompt_visible(frame) is True
    assert prompt_glyph(frame) == "a"


def test_prompt_glyph_reads_green_a_chip() -> None:
    image = Image.new("RGB", (320, 180), (12, 12, 12))
    draw = ImageDraw.Draw(image)
    draw.rectangle((95, 171, 225, 175), fill=(18, 18, 18))
    draw.ellipse((108, 171, 124, 175), fill=(50, 210, 60))
    draw.rectangle((135, 171, 200, 175), fill=(220, 220, 220))
    assert prompt_visible(image) is True
    assert prompt_glyph(image) == "a"


def test_contact_sheet_and_caption() -> None:
    frames = [Image.new("RGB", (80, 40), (i * 40, 0, 0)) for i in range(5)]
    sheet = contact_sheet(frames, n=4, height=20)
    assert sheet.size[1] == 20
    assert sheet.size[0] > 80
    text = burst_caption([0.0, 0.04, 0.08, 0.03], prompt=False, n=5)
    assert "burst 5 frames" in text
    assert "after_prompt=no" in text
    from loop.os.saliency import result_caption

    assert "blocked/no-change" in result_caption(
        chord="stick_l_n",
        mad=0.002,
        pixel_changed=False,
        prompt_before=False,
        prompt_after=False,
    )
    assert "tap-failed/prompt-still" in result_caption(
        chord="y",
        mad=0.04,
        pixel_changed=True,
        prompt_before=True,
        prompt_after=True,
    )
    assert "prompt-cleared" in result_caption(
        chord="a",
        mad=0.02,
        pixel_changed=True,
        prompt_before=True,
        prompt_after=False,
    )
