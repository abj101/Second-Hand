"""Control registry identity fixtures and behavioral tests (task A contract)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PIL import Image, ImageDraw

from loop.adaptive.perceive import SceneAssembler, _registry_label_key

IMAGE_SIZE = (200, 120)
BG = (10, 10, 10)
LOGO_BBOX = (0.20, 0.02, 0.95, 0.55)
VERSION_BBOX = (0.70, 0.05, 0.90, 0.10)
JITTER_DELTA = 0.008

# key -> (display text, normalized bbox, fill rgb)
EXPECTED_CONTROLS: dict[str, tuple[str, tuple[float, float, float, float], tuple[int, int, int]]] = {
    "continue": ("Continue", (0.08, 0.20, 0.28, 0.26), (200, 40, 40)),
    "settings": ("Settings", (0.08, 0.30, 0.28, 0.36), (40, 180, 40)),
    "statistics": ("Statistics", (0.08, 0.40, 0.28, 0.46), (40, 40, 200)),
    "credits": ("Credits", (0.08, 0.50, 0.28, 0.56), (200, 200, 40)),
    "quit": ("Quit", (0.08, 0.60, 0.28, 0.66), (200, 40, 200)),
    "custom_top": ("Custom", (0.08, 0.72, 0.22, 0.78), (40, 200, 200)),
    "custom_bottom": ("Custom", (0.70, 0.72, 0.84, 0.78), (180, 100, 40)),
}

REPLACEMENT_CONTROLS: dict[str, tuple[str, tuple[float, float, float, float], tuple[int, int, int]]] = {
    "alpha": ("Alpha", (0.10, 0.40, 0.30, 0.48), (120, 120, 120)),
    "beta": ("Beta", (0.10, 0.55, 0.30, 0.63), (90, 90, 90)),
}


def _shift_bbox(
    bbox: tuple[float, float, float, float], delta: float
) -> tuple[float, float, float, float]:
    return tuple(min(1.0, max(0.0, v + delta)) for v in bbox)  # type: ignore[return-value]


def _paint_boxes(
    img: Image.Image,
    boxes: list[tuple[tuple[float, float, float, float], tuple[int, int, int]]],
) -> None:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for bbox, color in boxes:
        x0, y0, x1, y1 = bbox
        px0, py0 = int(x0 * w), int(y0 * h)
        px1, py1 = int(x1 * w), int(y1 * h)
        draw.rectangle((px0, py0, max(px1, px0 + 1), max(py1, py0 + 1)), fill=color)


def _build_image(
    *,
    include: set[str] | None = None,
    jitter: float = 0.0,
    logo_text: str | None = None,
    logo_color: tuple[int, int, int] | None = None,
    include_version: bool = False,
    extra_tokens: list[tuple[str, tuple[float, float, float, float], tuple[int, int, int]]] | None = None,
    replacement: bool = False,
) -> Image.Image:
    img = Image.new("RGB", IMAGE_SIZE, BG)
    paint: list[tuple[tuple[float, float, float, float], tuple[int, int, int]]] = []
    ocr_tokens: list[tuple[str, tuple[float, float, float, float]]] = []

    catalog = REPLACEMENT_CONTROLS if replacement else EXPECTED_CONTROLS
    keys = include if include is not None else set(catalog)

    if not replacement:
        if logo_text is not None and logo_color is not None:
            paint.append((LOGO_BBOX, logo_color))
            ocr_tokens.append((logo_text, LOGO_BBOX))
        if include_version:
            paint.append((VERSION_BBOX, (80, 80, 80)))
            ocr_tokens.append(("Version", VERSION_BBOX))

    for key in keys:
        if key not in catalog:
            continue
        text, bbox, color = catalog[key]
        bb = _shift_bbox(bbox, jitter) if jitter else bbox
        paint.append((bb, color))
        ocr_tokens.append((text, bb))

    if extra_tokens:
        for text, bbox, color in extra_tokens:
            paint.append((bbox, color))
            ocr_tokens.append((text, bbox))

    _paint_boxes(img, paint)
    return img


def _ocr_from_tokens(tokens: list[tuple[str, tuple[float, float, float, float]]]):
    def _fn(_: Image.Image) -> list[tuple[str, tuple[float, float, float, float]]]:
        return list(tokens)

    return _fn


def _menu_frame_tokens(
    *,
    include_quit: bool = True,
    include_version: bool = False,
    jitter: float = 0.0,
    logo_text: str | None = None,
    logo_color: tuple[int, int, int] | None = None,
) -> tuple[Image.Image, list[tuple[str, tuple[float, float, float, float]]]]:
    keys = set(EXPECTED_CONTROLS) - (set() if include_quit else {"quit"})
    img = _build_image(
        include=keys,
        jitter=jitter,
        logo_text=logo_text,
        logo_color=logo_color,
        include_version=include_version,
    )
    tokens: list[tuple[str, tuple[float, float, float, float]]] = []
    for key in keys:
        text, bbox, _color = EXPECTED_CONTROLS[key]
        bb = _shift_bbox(bbox, jitter) if jitter else bbox
        tokens.append((text, bb))
    if logo_text is not None:
        tokens.append((logo_text, LOGO_BBOX))
    if include_version:
        tokens.append(("Version", VERSION_BBOX))
    return img, tokens


def frames_identical(count: int = 30) -> Iterator[tuple[Image.Image, object]]:
    img, tokens = _menu_frame_tokens()
    ocr = _ocr_from_tokens(tokens)
    for _ in range(count):
        yield img.copy(), ocr


def frames_logo_noise(count: int = 30) -> Iterator[tuple[Image.Image, object]]:
    for i in range(count):
        img, tokens = _menu_frame_tokens(
            logo_text=f"LogoNoise{i}",
            logo_color=((30 + i * 5) % 200, (50 + i * 3) % 200, (70 + i * 7) % 200),
        )
        # Iterable tokens bypass region OCR cache so every control is re-emitted.
        yield img, list(tokens)


def frames_duplicate_labels() -> Iterator[tuple[Image.Image, object]]:
    """Two distinct Custom labels at different locations (stable ids)."""
    img, tokens = _menu_frame_tokens()
    yield img, _ocr_from_tokens(tokens)
    yield img.copy(), _ocr_from_tokens(tokens)


def frames_jitter(count: int = 10) -> Iterator[tuple[Image.Image, object]]:
    for _ in range(count):
        img, tokens = _menu_frame_tokens(jitter=JITTER_DELTA)
        yield img, _ocr_from_tokens(tokens)


def frames_remove_return() -> Iterator[tuple[Image.Image, object]]:
    img_full, tokens_full = _menu_frame_tokens(include_quit=True)
    yield img_full, _ocr_from_tokens(tokens_full)
    img_no_quit, tokens_no = _menu_frame_tokens(include_quit=False)
    for _ in range(4):
        yield img_no_quit.copy(), _ocr_from_tokens(tokens_no)
    yield img_full.copy(), _ocr_from_tokens(tokens_full)


def frames_replacement() -> Iterator[tuple[Image.Image, object]]:
    img_old, tokens_old = _menu_frame_tokens()
    yield img_old, _ocr_from_tokens(tokens_old)
    img_new = _build_image(replacement=True)
    tokens_new = [(REPLACEMENT_CONTROLS[k][0], REPLACEMENT_CONTROLS[k][1]) for k in ("alpha", "beta")]
    yield img_new, _ocr_from_tokens(tokens_new)


def frames_ambiguous_custom_pair() -> Iterator[tuple[Image.Image, object]]:
    """Two Custom nodes overlapping one historical location — both stay unbound."""
    img1, tokens1 = _menu_frame_tokens(include_quit=False)
    # Drop custom_bottom so only one Custom seeds history.
    tokens1 = [t for t in tokens1 if t[0] != "Custom" or t[1][0] < 0.5]
    yield img1, _ocr_from_tokens(tokens1)
    hist_bbox = EXPECTED_CONTROLS["custom_top"][1]
    color = EXPECTED_CONTROLS["custom_top"][2]
    overlap_a = hist_bbox
    x0, y0, x1, y1 = hist_bbox
    overlap_b = (x0 + 0.05, y0, x1 + 0.05, y1)
    img2 = _build_image(
        include={"continue", "settings", "statistics", "credits"},
        extra_tokens=[
            ("Custom", overlap_a, color),
            ("Custom", overlap_b, color),
        ],
    )
    tokens2 = [
        ("Continue", EXPECTED_CONTROLS["continue"][1]),
        ("Settings", EXPECTED_CONTROLS["settings"][1]),
        ("Statistics", EXPECTED_CONTROLS["statistics"][1]),
        ("Credits", EXPECTED_CONTROLS["credits"][1]),
        ("Custom", overlap_a),
        ("Custom", overlap_b),
    ]
    yield img2, _ocr_from_tokens(tokens2)


def _ocr_registry_ids(scene) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in scene.nodes:
        if node.attributes.get("registry_owner") != "ocr":
            continue
        rid = node.attributes.get("registry_id")
        label = node.attributes.get("label")
        if not rid or not label:
            continue
        bb = tuple(node.bbox)
        for key, (text, eb, _c) in EXPECTED_CONTROLS.items():
            if text != label:
                continue
            if all(abs(bb[i] - eb[i]) < 0.05 for i in range(4)):
                out[key] = str(rid)
                break
    return out


def _run_frames(assembler: SceneAssembler, frames) -> list:
    scenes = []
    for image, ocr in frames:
        _recs, scene = assembler.update(image, ocr=ocr)
        scenes.append(scene)
    return scenes


def test_thirty_identical_frames_stable_unique_ids():
    assembler = SceneAssembler()
    scenes = _run_frames(assembler, frames_identical(30))
    first = _ocr_registry_ids(scenes[0])
    assert len(first) == len(EXPECTED_CONTROLS)
    assert len(set(first.values())) == len(EXPECTED_CONTROLS)
    for scene in scenes[1:]:
        assert _ocr_registry_ids(scene) == first


def test_logo_noise_does_not_disturb_control_ids():
    assembler = SceneAssembler()
    scenes = _run_frames(assembler, frames_logo_noise(30))
    first = _ocr_registry_ids(scenes[0])
    assert len(first) == len(EXPECTED_CONTROLS)
    for scene in scenes[1:]:
        assert _ocr_registry_ids(scene) == first
    logo_ids = {
        n.attributes.get("registry_id")
        for n in scenes[-1].nodes
        if "LogoNoise" in str(n.attributes.get("text", ""))
    }
    assert logo_ids <= {None}


def test_two_custom_labels_get_distinct_registry_ids():
    assembler = SceneAssembler()
    image, ocr = next(iter(frames_duplicate_labels()))
    _recs, scene = assembler.update(image, ocr=ocr)
    custom = [
        n
        for n in scene.nodes
        if n.attributes.get("label") == "Custom" and n.attributes.get("registry_owner") == "ocr"
    ]
    assert len(custom) == 2
    ids = {n.attributes["registry_id"] for n in custom}
    assert len(ids) == 2


def test_jitter_preserves_registry_ids():
    assembler = SceneAssembler()
    scenes = _run_frames(assembler, frames_jitter(12))
    first = _ocr_registry_ids(scenes[0])
    for scene in scenes[1:]:
        assert _ocr_registry_ids(scene) == first


def test_remove_quit_absent_then_returns_same_id():
    assembler = SceneAssembler()
    scenes = _run_frames(assembler, frames_remove_return())
    quit_first = _ocr_registry_ids(scenes[0]).get("quit")
    assert quit_first
    for scene in scenes[1:5]:
        assert "quit" not in _ocr_registry_ids(scene)
    quit_back = _ocr_registry_ids(scenes[5]).get("quit")
    assert quit_back == quit_first


def test_replacement_screen_drops_old_labels_and_ids():
    assembler = SceneAssembler()
    scenes = _run_frames(assembler, frames_replacement())
    old_labels = {EXPECTED_CONTROLS[k][0] for k in EXPECTED_CONTROLS}
    old_ids = set(_ocr_registry_ids(scenes[0]).values())
    repl = scenes[1]
    current_labels = {n.attributes.get("label") for n in repl.nodes if n.attributes.get("registry_owner") == "ocr"}
    current_ids = {
        n.attributes.get("registry_id")
        for n in repl.nodes
        if n.attributes.get("registry_owner") == "ocr" and n.attributes.get("registry_id")
    }
    assert not (old_labels & current_labels)
    assert not (old_ids & current_ids)


def test_tracks_never_carry_registry_id_when_ocr_owns_text():
    assembler = SceneAssembler()
    image, ocr = next(iter(frames_identical(1)))
    _recs, scene = assembler.update(image, ocr=ocr)
    ocr_labels = {
        n.attributes.get("label")
        for n in scene.nodes
        if n.attributes.get("registry_owner") == "ocr" and n.attributes.get("registry_id")
    }
    for node in scene.nodes:
        if not node.node_id.startswith("track_"):
            continue
        label = node.attributes.get("label") or node.attributes.get("text")
        if label in ocr_labels:
            assert "registry_id" not in node.attributes


def test_ambiguous_custom_pair_has_no_registry_ids():
    assembler = SceneAssembler()
    for _image, ocr in frames_ambiguous_custom_pair():
        _recs, scene = assembler.update(_image, ocr=ocr)
    custom = [
        n
        for n in scene.nodes
        if n.node_id.startswith("ocr_") and n.attributes.get("label") == "Custom"
    ]
    assert len(custom) == 2
    assert all("registry_id" not in n.attributes for n in custom)


def test_ocr_node_exposes_frozen_attribute_shape():
    assembler = SceneAssembler()
    image, ocr = next(iter(frames_identical(1)))
    _recs, scene = assembler.update(image, ocr=ocr)
    node = next(
        n
        for n in scene.nodes
        if n.attributes.get("label") == "Settings" and n.attributes.get("registry_owner") == "ocr"
    )
    assert set(node.attributes.keys()) >= {"registry_id", "registry_owner", "registry_label", "label", "bbox", "text"}
    assert node.attributes["registry_owner"] == "ocr"
    assert node.attributes["registry_label"] == _registry_label_key("Settings")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
