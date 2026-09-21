"""Neutral adaptive perception — synthetic frames, no game mechanics."""

from __future__ import annotations


import pytest
from PIL import Image, ImageDraw, ImageFont

from loop.adaptive.evidence import ALLOWED_KINDS, assign_track_ids, parse_numeric_text
from loop.adaptive.perceive import SceneAssembler, assemble_scene, derive_interactions, perceive
from loop.adaptive.schema import EvidenceLedger
from loop.adaptive.world import CONTROL_NODE_ID, NeutralWorld

def _frame(
    *,
    box_xy: tuple[int, int, int, int] | None = (40, 40, 100, 100),
    fill: str = "red",
    text: str | None = None,
    size: tuple[int, int] = (200, 200),
) -> Image.Image:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    if box_xy:
        draw.rectangle(box_xy, fill=fill, outline=fill)
    if text:
        font = ImageFont.load_default()
        draw.text((10, 10), text, fill="black", font=font)
    return img


def test_kinds_are_neutral():
    img = _frame(text="SCORE")
    fake_ocr = lambda im: [("SCORE", (0.05, 0.05, 0.35, 0.15))]  # noqa: E731
    recs = perceive(img, ocr=fake_ocr, vlm=lambda _: "maybe a button")
    kinds = {r.kind for r in recs}
    assert kinds <= ALLOWED_KINDS
    assert "ocr" in kinds
    assert "vlm_hypothesis" in kinds


def test_perception_does_not_invent_labels():
    img = _frame(text="SCORE")
    recs = perceive(
        img,
        ocr=lambda _: [("SCORE", (0.05, 0.05, 0.35, 0.15))],
    )
    ocr_texts = [
        r.payload.get("text", "")
        for r in recs
        if r.kind == "ocr"
    ]
    assert ocr_texts == ["SCORE"]


def test_motion_only_when_pixels_change():
    a = _frame(box_xy=(40, 40, 100, 100))
    b = _frame(box_xy=(55, 40, 115, 100))
    still = perceive(b, previous=a)
    motion = [r for r in still if r.kind == "motion"]
    assert len(motion) >= 1

    same = perceive(a, previous=a.copy())
    assert [r for r in same if r.kind == "motion"] == []


def test_tracks_persist_across_frames():
    f1 = _frame(box_xy=(40, 40, 100, 100), fill="blue")
    f2 = _frame(box_xy=(48, 42, 108, 102), fill="blue")
    r1 = perceive(f1)
    r2 = perceive(f2, previous=f1)
    t1 = {r.payload["track_id"] for r in r1 if r.kind == "track"}
    t2 = {r.payload["track_id"] for r in r2 if r.kind == "track"}
    assert t1 & t2, "expected at least one shared track_id across frames"


def test_ledger_receives_appends():
    ledger = EvidenceLedger()
    img = _frame()
    perceive(img, ledger=ledger)
    assert len(ledger.all()) >= 1


def test_assemble_scene_does_not_invent_clickable():
    img = _frame()
    recs = perceive(img)
    scene = assemble_scene(recs, snapshot_id="s")
    assert all(not n.attributes.get("clickable") for n in scene.nodes)
    marked = assemble_scene(recs, snapshot_id="s", clickable_ids={0})
    hit = next((n for n in marked.nodes if n.node_id == "track_0"), None)
    if hit is not None:
        assert hit.attributes.get("clickable") is True


def test_assemble_scene_one_node_per_track():
    img = _frame(text="widget:3")
    recs = perceive(
        img,
        ocr=lambda _: [("widget:3", (0.05, 0.05, 0.35, 0.15))],
    )
    scene = assemble_scene(recs, snapshot_id="snap_test")
    tracks = [n for n in scene.nodes if n.node_id.startswith("track_")]
    assert len(tracks) == len([r for r in recs if r.kind == "track"])
    assert scene.snapshot_id == "snap_test"
    assert scene.relations == ()


def test_derive_interactions_emits_delta():
    before = assemble_scene(
        (
            perceive(
                _frame(text="score:0"),
                ocr=lambda _: [("score:0", (0.05, 0.05, 0.35, 0.15))],
            )
        ),
        snapshot_id="a",
    )
    after = assemble_scene(
        (
            perceive(
                _frame(text="score:2"),
                ocr=lambda _: [("score:2", (0.05, 0.05, 0.35, 0.15))],
            )
        ),
        snapshot_id="b",
    )
    deltas = derive_interactions(before, after, "track_0")
    assert len(deltas) == 1
    assert deltas[0].kind == "interaction_delta"
    assert deltas[0].payload["field_id"] == "score"
    assert deltas[0].payload["op"] == "increased"
    assert deltas[0].payload["delta"] == 2


def test_scene_assembler_neutral_world_tracks_and_numeric():
    world = NeutralWorld()
    world.reset(0)
    assembler = SceneAssembler()
    pixels, _, truth = world.observe()
    assert pixels is not None
    rgb = pixels.convert("RGB")
    _, scene0 = assembler.update(rgb, ocr=lambda im: world.ocr_tokens())
    assert len([n for n in scene0.nodes if n.node_id.startswith("track_")]) >= 1

    display = truth["labels"]["display"]
    x0, y0, x1, y1 = next(
        n.bbox for n in world._scene().nodes if n.node_id == CONTROL_NODE_ID
    )
    world.click_at((x0 + x1) / 2, (y0 + y1) / 2)
    pixels2, _, _ = world.observe()
    _, scene1 = assembler.update(pixels2.convert("RGB"), ocr=lambda im: world.ocr_tokens())

    numerics = [
        n.attributes.get("numeric")
        for n in scene1.nodes
        if n.attributes.get("label") == display
    ]
    assert 1 in numerics
    assert display in next(
        n.attributes for n in scene1.nodes if n.attributes.get("numeric") == 1
    )


def test_interaction_delta_kind_allowed():
    assert "interaction_delta" in ALLOWED_KINDS


@pytest.mark.parametrize(
    ("text", "expected"),
    [("42", (42, None)), ("-3.5", (-3.5, None)), ("7/10", (7, 10))],
)
def test_parse_standalone_numeric_text(text, expected):
    assert parse_numeric_text(text) == expected


def test_mutual_best_track_ids_ignore_detection_order():
    prev = [(0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 1.0, 0.5)]
    curr = [(0.4, 0.0, 0.9, 0.5), (0.0, 0.0, 0.45, 0.5)]
    matched = assign_track_ids(prev, curr, prev_ids=[0, 1])
    by_box = {tuple(b): tid for tid, b in matched}
    assert by_box[curr[1]] == 0
    assert by_box[curr[0]] == 1


def test_ocr_cluster_id_survives_bbox_jitter():
    assembler = SceneAssembler()
    tokens_a = [("7/10", (0.10, 0.20, 0.20, 0.30))]
    tokens_b = [("7/10", (0.11, 0.21, 0.21, 0.31))]
    recs0, scene0 = assembler.update(_frame(), ocr=tokens_a)
    recs1, scene1 = assembler.update(_frame(), ocr=tokens_b)
    c0 = next(r.payload["cluster_id"] for r in recs0 if r.kind == "ocr")
    c1 = next(r.payload["cluster_id"] for r in recs1 if r.kind == "ocr")
    assert c0 == c1
    n0 = next(n.node_id for n in scene0.nodes if n.node_id.startswith("ocr_"))
    n1 = next(n.node_id for n in scene1.nodes if n.node_id.startswith("ocr_"))
    assert n0 == n1 == f"ocr_c{c0}"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
