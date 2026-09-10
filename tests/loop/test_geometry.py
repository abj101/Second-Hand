from __future__ import annotations

from PIL import Image

from loop.os.geometry import (
    box_is_full,
    boxes_close,
    content_box_for,
    crop_content,
    encode_observe,
    fit_aspect_box,
    geometry_error,
    geometry_from_image,
    is_top_left_scaled_capture,
    letterbox_frac,
    perceive_longest_side,
)


def test_letterbox_crop_any_ratio() -> None:
    frame = Image.new("RGB", (400, 400), (0, 0, 0))
    frame.paste(Image.new("RGB", (400, 200), (80, 40, 20)), (0, 100))
    box = letterbox_frac(frame)
    cropped = crop_content(frame, box)
    assert cropped.size == (400, 200)
    geo = geometry_from_image(frame)
    assert geo.client_w == 400 and geo.client_h == 400
    assert geo.content_h == 200


def test_full_frame_keeps_ultrawide_and_portrait() -> None:
    ultra = Image.new("RGB", (3440, 1440), (30, 30, 30))
    assert content_box_for(ultra) == (0.0, 0.0, 1.0, 1.0)
    assert geometry_error(ultra) is None
    portrait = Image.new("RGB", (1080, 1920), (30, 30, 30))
    assert content_box_for(portrait) == (0.0, 0.0, 1.0, 1.0)
    assert crop_content(portrait).size == (1080, 1920)


def test_encode_observe_preserves_aspect() -> None:
    tall = Image.new("RGB", (720, 1280), (10, 20, 30))
    frame = encode_observe(tall, cap=640)
    assert frame.original_width == 720
    assert frame.original_height == 1280
    assert max(frame.width, frame.height) == 640
    assert abs(frame.width / frame.height - 720 / 1280) < 0.02
    small = Image.new("RGB", (320, 200), (10, 20, 30))
    native = encode_observe(small, cap=1280)
    assert native.width == 320 and native.height == 200
    assert perceive_longest_side(small, 1280) == 320


def test_geometry_change_detects_resize() -> None:
    a = geometry_from_image(Image.new("RGB", (1280, 720), (1, 1, 1)))
    b = geometry_from_image(Image.new("RGB", (1920, 1080), (1, 1, 1)))
    assert a.size_changed(b)
    assert not a.size_changed(a)
    fitted = fit_aspect_box(2560, 1080)
    assert fitted[1] == 0.0 and fitted[3] == 1.0
    assert fitted[0] > 0.0


def test_cropped_vs_raw_thumbs_are_not_like_for_like() -> None:
    from loop.os.profile import mean_absdiff, roi_thumb

    raw = Image.new("RGB", (300, 300), (0, 0, 0))
    raw.paste(Image.new("RGB", (200, 200), (255, 255, 255)), (0, 0))
    cropped = crop_content(raw, letterbox_frac(raw))
    mixed = mean_absdiff(roi_thumb(cropped, 64), roi_thumb(raw, 64))
    like = mean_absdiff(roi_thumb(cropped, 64), roi_thumb(cropped, 64))
    assert like < 0.01
    assert mixed > 0.08


def test_box_is_full_and_boxes_close() -> None:
    assert box_is_full((0.0, 0.0, 1.0, 1.0))
    assert box_is_full((0.005, 0.0, 0.995, 1.0))
    assert not box_is_full((0.0, 0.0, 0.67, 0.67))
    assert boxes_close((0.0, 0.0, 1.0, 1.0), (0.01, 0.01, 0.99, 0.99))
    assert not boxes_close((0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 0.67, 0.67))


def test_printwindow_scaled_frame_is_suspect_not_letterbox() -> None:
    frame = Image.new("RGB", (1915, 1078), (0, 0, 0))
    frame.paste(Image.new("RGB", (1277, 718), (80, 40, 20)), (0, 0))
    box = letterbox_frac(frame)
    assert abs(box[2] - 1277 / 1915) < 0.01
    assert abs(box[3] - 718 / 1078) < 0.01
    assert box[0] == 0.0 and box[1] == 0.0
    assert is_top_left_scaled_capture(frame)


def test_centered_letterbox_is_not_top_left_scaled() -> None:
    frame = Image.new("RGB", (400, 400), (0, 0, 0))
    frame.paste(Image.new("RGB", (400, 200), (80, 40, 20)), (0, 100))
    box = letterbox_frac(frame)
    assert abs(box[1] - 0.25) < 1e-6
    assert abs(box[3] - 0.75) < 1e-6
    assert not is_top_left_scaled_capture(frame)
    assert not is_top_left_scaled_capture(Image.new("RGB", (1915, 1078), (40, 20, 20)))
