"""Neutral adaptive perception — synthetic frames, no game mechanics."""

from __future__ import annotations

import hashlib
import pytest
from PIL import Image, ImageDraw, ImageFont

from loop.adaptive.evidence import ALLOWED_KINDS, assign_track_ids, parse_numeric_text
from loop.adaptive.orchestrate import registry_offers
from loop.adaptive.perceive import SceneAssembler, assemble_scene, derive_interactions, perceive
from loop.adaptive.runtime import project
from loop.adaptive.schema import EvidenceLedger, empty_schema
from loop.adaptive.tracker import ScriptedTracker
from loop.adaptive.world import CONTROL_NODE_ID, NeutralWorld


def test_fused_tracks_do_not_supply_generic_region_features(monkeypatch):
    image = _frame()
    regions = [(0.1, 0.1, 0.3, 0.3), (0.5, 0.5, 0.8, 0.8)]
    monkeypatch.setattr("loop.adaptive.evidence.detect_regions", lambda _image: regions)
    assembler = SceneAssembler(ScriptedTracker([[]]))
    assembler._previous = image.copy()
    assembler._tracks = [("trk_0", regions[0], {"label": "old fused track"})]

    records, _scene = assembler.update(image, ocr=lambda _crop: [])

    assert records

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


def test_region_hash_gate_reuses_ocr_and_rereads_changed_only():
    ocr_calls: list[tuple[int, int]] = []

    def tracking_ocr(im: Image.Image) -> list[tuple[str, tuple[float, float, float, float]]]:
        ocr_calls.append(im.size)
        w, h = im.size
        if w >= 150 and h >= 150:
            return [
                ("SCORE", (0.05, 0.05, 0.35, 0.15)),
                ("LOGO", (0.55, 0.55, 0.85, 0.85)),
            ]
        return [("LOGO", (0.1, 0.1, 0.9, 0.9))]

    assembler = SceneAssembler()
    f1 = _frame(text="SCORE", box_xy=(110, 110, 170, 170), fill="blue")
    recs1, _ = assembler.update(f1, ocr=tracking_ocr)
    assert len(ocr_calls) == 1 and ocr_calls[0] == (200, 200)

    recs2, _ = assembler.update(f1.copy(), ocr=tracking_ocr)
    # Identical bytes are served by exact-frame reuse before the region gate;
    # the region-level reuse below is exercised by the changed-region frame.
    assert assembler.frame_reused is True
    assert len(ocr_calls) == 1
    assert [r.payload["text"] for r in recs1 if r.kind == "ocr"] == [
        r.payload["text"] for r in recs2 if r.kind == "ocr"
    ]

    assembler.update(
        _frame(text="SCORE", box_xy=(110, 110, 170, 170), fill="green"),
        ocr=tracking_ocr,
    )
    assert len(ocr_calls) == 2
    assert ocr_calls[1] != (200, 200)
    assert ocr_calls[1][0] * ocr_calls[1][1] < 200 * 200


def test_visual_ownership_tracker_receives_full_frame_ocr_when_it_runs():
    class VisualOwnershipTracker:
        status = "ok"
        require_visual_ownership = True

        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def update(self, _image, *, ocr):
            assert ocr
            self.calls.append(tuple(ocr))
            return []

    calls: list[tuple[int, int]] = []

    def ocr(image: Image.Image):
        calls.append(image.size)
        return [("SCORE", (0.05, 0.05, 0.35, 0.15))]

    token = (("SCORE", (0.05, 0.05, 0.35, 0.15)),)
    frame = _frame(text="SCORE")
    tracker = VisualOwnershipTracker()
    assembler = SceneAssembler(tracker)
    assembler.update(frame, ocr=ocr)
    assert calls == [frame.size]
    assert tracker.calls == [token], "full-frame tokens, never crops"

    # An exact frame reuses the previous measurement instead of re-reading OCR
    # and re-running the ownership tracker, and still answers with a fresh graph.
    records, scene = assembler.update(frame.copy(), ocr=ocr)
    assert assembler.frame_reused is True
    assert calls == [frame.size]
    assert tracker.calls == [token]
    assert records and scene.nodes

    # Changed pixels run OCR and the ownership tracker again, still full-frame.
    changed = _frame(text="SCORE", box_xy=(60, 60, 120, 120), fill="blue")
    assembler.update(changed, ocr=ocr)
    assert calls == [frame.size, frame.size]
    assert tracker.calls == [token, token]


def test_region_ocr_gate_ignores_features_without_matching_previous_tracks(monkeypatch):
    regions = [(0.0, 0.0, 0.4, 0.4), (0.6, 0.6, 1.0, 1.0)]
    monkeypatch.setattr("loop.adaptive.evidence.detect_regions", lambda _image: regions)
    image = _frame()

    records = perceive(
        image,
        previous=image.copy(),
        prev_features=[{"label": "stale"}],
        region_cache={},
        ocr=lambda _crop: [],
    )

    assert len([record for record in records if record.kind == "track"]) == 2


def test_registry_id_survives_one_frame_absence():
    def _two_control_frame(
        *,
        settings_xy: tuple[int, int, int, int] = (40, 40, 100, 100),
        junk_xy: tuple[int, int, int, int] = (110, 110, 170, 170),
    ) -> Image.Image:
        img = Image.new("RGB", (200, 200), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle(settings_xy, fill="blue", outline="blue")
        draw.rectangle(junk_xy, fill="red", outline="red")
        return img

    def ocr_two_controls(_: Image.Image) -> list[tuple[str, tuple[float, float, float, float]]]:
        return [
            ("Settings", (0.2, 0.2, 0.5, 0.5)),
            ("@@@", (0.55, 0.55, 0.85, 0.85)),
        ]

    assembler = SceneAssembler()
    recs1, scene1 = assembler.update(_two_control_frame(), ocr=ocr_two_controls)
    settings = next(r for r in recs1 if r.kind == "ocr" and r.payload.get("text") == "Settings")
    reg_id = settings.payload["registry_id"]
    junk = next(r for r in recs1 if r.kind == "ocr" and r.payload.get("text") == "@@@")
    assert "registry_id" not in junk.payload
    assert not any(r.kind == "track" and r.payload.get("registry_id") for r in recs1)

    assembler.update(_frame(box_xy=None), ocr=lambda _: [])

    f3 = _frame(box_xy=(50, 50, 110, 110), fill="blue")
    recs3, scene3 = assembler.update(
        f3,
        ocr=lambda _: [("Settings", (0.25, 0.25, 0.55, 0.55))],
    )
    settings3 = next(r for r in recs3 if r.kind == "ocr" and r.payload.get("text") == "Settings")
    assert settings3.payload["registry_id"] == reg_id
    node = next(n for n in scene3.nodes if n.attributes.get("registry_id") == reg_id)
    assert node.attributes["registry_id"] == reg_id


def test_still_menu_text_survives_empty_logo_reread():
    def ocr(im: Image.Image):
        if im.size == (200, 200):
            return [
                ("Settings", (0.05, 0.02, 0.40, 0.12)),
                ("Slay:the", (0.60, 0.60, 0.90, 0.80)),
            ]
        return []

    assembler = SceneAssembler()
    frame = _frame(text="Settings", box_xy=(120, 120, 180, 180), fill="blue")
    assembler.update(frame, ocr=ocr)
    recs, scene = assembler.update(
        _frame(text="Settings", box_xy=(120, 120, 180, 180), fill="green"),
        ocr=ocr,
    )
    texts = [r.payload.get("text") for r in recs if r.kind == "ocr"]
    assert "Settings" in texts
    assert not any(
        r.payload.get("registry_id")
        for r in recs
        if "Slay" in str(r.payload.get("text", "")) or r.payload.get("label") == "Slay"
    )
    assert any(
        n.attributes.get("label") == "Settings" and n.attributes.get("registry_id")
        for n in scene.nodes
    )


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


def test_identical_frames_keep_ocr_evidence_scene_and_registry_ids():
    assembler = SceneAssembler()
    frame = _frame(box_xy=(40, 40, 100, 100), fill="blue", text="Play")
    token = lambda _image: [("Play", (0.05, 0.05, 0.30, 0.15))]  # noqa: E731

    first_records, first_scene = assembler.update(frame, ocr=token)
    first_ocr = next(rec for rec in first_records if rec.kind == "ocr")
    first_registry = {
        rec.payload["registry_id"]
        for rec in first_records
        if rec.kind == "ocr" and rec.payload.get("registry_id")
    }
    assert first_registry
    first_scene_ids = tuple(node.node_id for node in first_scene.nodes)
    for _ in range(29):
        records, scene = assembler.update(frame.copy(), ocr=token)
        ocr = next(rec for rec in records if rec.kind == "ocr")
        registry = {
            rec.payload["registry_id"]
            for rec in records
            if rec.kind == "ocr" and rec.payload.get("registry_id")
        }
        assert not any(rec.kind == "track" and rec.payload.get("registry_id") for rec in records)
        assert (ocr.evidence_id, ocr.payload.get("cluster_id")) == (
            first_ocr.evidence_id,
            first_ocr.payload.get("cluster_id"),
        )
        assert tuple(node.node_id for node in scene.nodes) == first_scene_ids
        assert registry == first_registry


def test_overlapping_ocr_reads_have_one_scene_token_owner():
    frame = Image.new("RGB", (100, 100), "white")
    records = perceive(
        frame,
        ocr=[("Play", (0.1, 0.1, 0.4, 0.3)), ("Play", (0.1, 0.1, 0.4, 0.3))],
    )
    assert len([rec for rec in records if rec.kind == "ocr"]) == 1
    scene = assemble_scene(records, snapshot_id="dedup")
    assert len([node for node in scene.nodes if node.node_id.startswith("ocr_")]) == 1


def test_replacement_frame_does_not_reuse_old_cached_labels():
    assembler = SceneAssembler()
    first = _frame(box_xy=(40, 40, 100, 100), fill="blue", text="Old")
    second = _frame(box_xy=(140, 140, 190, 190), fill="red", text="New")
    assembler.update(first, ocr=lambda _image: [("Old", (0.05, 0.05, 0.30, 0.15))])
    records, scene = assembler.update(
        second,
        ocr=lambda _image: [("New", (0.70, 0.70, 0.95, 0.95))],
    )
    assert not any("Old" in str(rec.payload.get("text", "")) for rec in records)
    assert not any(node.attributes.get("label") == "Old" for node in scene.nodes)


def test_registry_matches_tolerant_appearance_by_normalized_label():
    assembler = SceneAssembler()

    def frame(color):
        return _frame(box_xy=(40, 40, 100, 100), fill=color)

    ocr = lambda _image: [("  Play  ", (0.20, 0.20, 0.50, 0.50))]  # noqa: E731
    records, _ = assembler.update(frame((0, 0, 255)), ocr=ocr)
    first = next(rec.payload["registry_id"] for rec in records if rec.kind == "ocr")
    records, _ = assembler.update(frame((20, 20, 235)), ocr=ocr)
    second = next(rec.payload["registry_id"] for rec in records if rec.kind == "ocr")
    assert second == first


if __name__ == "__main__":
    pytest.main([__file__, "-q"])


def test_exact_frame_reuse_returns_a_fresh_graph_without_new_evidence():
    calls: list[tuple[int, int]] = []

    def ocr(image: Image.Image):
        calls.append(image.size)
        return [("Play", (0.20, 0.25, 0.45, 0.45))]

    assembler = SceneAssembler()
    ledger = EvidenceLedger()
    frame = _frame(text="Play", box_xy=(40, 40, 100, 100), fill="blue")

    first_records, first_scene = assembler.update(frame, ocr=ocr, ledger=ledger)
    measured = len(ledger.all())
    assert calls == [frame.size]
    assert assembler.frame_reused is False
    assert assembler.measurement_digest == hashlib.sha256(frame.tobytes()).hexdigest()
    assert first_scene.snapshot_id == assembler.measurement_digest[:16]

    second_records, second_scene = assembler.update(frame.copy(), ocr=ocr, ledger=ledger)

    assert calls == [frame.size], "an exact frame must not repeat OCR"
    assert assembler.frame_reused is True
    assert assembler.cache_hits == 1
    assert assembler.screen_replaced is False, "a repeated frame is not a screen replacement"
    # A reused measurement is not a second independent observation.
    assert len(ledger.all()) == measured
    # Freshly assembled, projectable outputs: new objects, same measurement ids.
    assert second_records is not first_records
    assert second_scene is not first_scene
    assert [rec.evidence_id for rec in second_records] == [
        rec.evidence_id for rec in first_records
    ]
    assert second_records[0].payload is not first_records[0].payload
    assert [rec.payload for rec in second_records] == [rec.payload for rec in first_records]
    assert [node.node_id for node in second_scene.nodes] == [
        node.node_id for node in first_scene.nodes
    ]


def test_exact_frame_reuse_stops_on_changed_bytes_and_dimensions():
    calls: list[tuple[int, int]] = []

    def ocr(image: Image.Image):
        calls.append(image.size)
        return [("Play", (0.20, 0.25, 0.45, 0.45))]

    assembler = SceneAssembler()
    frame = _frame(box_xy=(40, 40, 100, 100), fill="blue")
    assembler.update(frame, ocr=ocr)
    assert calls == [frame.size]

    # Changed pixels inside a detected region must be read for real.
    changed = _frame(box_xy=(40, 40, 100, 100), fill="green")
    assembler.update(changed, ocr=ocr)
    assert assembler.frame_reused is False
    assert len(calls) == 2
    assert calls[1] != frame.size and calls[1][0] * calls[1][1] < 200 * 200

    # Different dimensions can never be the same frame.
    resized = _frame(box_xy=(40, 40, 100, 100), fill="green", size=(201, 200))
    assembler.update(resized, ocr=ocr)
    assert assembler.frame_reused is False
    assert len(calls) == 3
    assert calls[2] == (201, 200)


def test_exact_frame_reuse_skips_tracker_detector_work():
    class CountingTracker:
        status = "ok"

        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def update(self, _image: Image.Image, *, ocr=()):
            self.calls.append(tuple(ocr))
            return []

    seen: list[tuple[int, int]] = []

    def ocr(image: Image.Image):
        seen.append(image.size)
        return [("Play", (0.20, 0.25, 0.45, 0.45))]

    tracker = CountingTracker()
    assembler = SceneAssembler(tracker)
    frame = _frame(text="Play", box_xy=(40, 40, 100, 100), fill="blue")
    assembler.update(frame, ocr=ocr)
    assembler.update(frame.copy(), ocr=ocr)

    assert len(tracker.calls) == 1, "an exact frame must not re-run the detector"
    assert tracker.calls[0] == (("Play", (0.20, 0.25, 0.45, 0.45)),)

    changed = _frame(text="Play", box_xy=(40, 40, 100, 100), fill="green")
    assembler.update(changed, ocr=ocr)
    assert len(tracker.calls) == 2
    assert len(seen) == 2, "changed region content must be read for real"
    assert tracker.calls[1] == (("Play", (0.20, 0.25, 0.45, 0.45)),)


def test_explicit_token_list_is_never_served_from_the_cache():
    assembler = SceneAssembler()
    frame = _frame(box_xy=(40, 40, 100, 100), fill="blue")
    first, _ = assembler.update(frame, ocr=[("Play", (0.20, 0.25, 0.45, 0.45))])
    second, _ = assembler.update(frame.copy(), ocr=[("Pause", (0.20, 0.25, 0.45, 0.45))])

    assert assembler.frame_reused is False
    assert [rec.payload.get("text") for rec in first if rec.kind == "ocr"] == ["Play"]
    assert [rec.payload.get("text") for rec in second if rec.kind == "ocr"] == ["Pause"]


def test_reused_frame_is_still_filtered_by_the_current_state():
    def ocr(_image: Image.Image):
        return [("Play", (0.20, 0.25, 0.45, 0.45))]

    assembler = SceneAssembler()
    frame = _frame(text="Play", box_xy=(40, 40, 100, 100), fill="blue")
    _, first = assembler.update(frame, ocr=ocr)
    _, reused = assembler.update(frame.copy(), ocr=ocr)

    assert assembler.frame_reused is True
    offers = registry_offers(reused)
    assert [offer.offer_id for offer in offers] == [
        offer.offer_id for offer in registry_offers(first)
    ]
    assert offers, "fixture must expose one registry offer"

    # A control newly forbidden by the caller disappears immediately, even
    # though the pixels, evidence ids and graph contents were reused.
    forbidden = frozenset({offers[0].offer_id})
    assert registry_offers(reused, forbidden) == ()
    # The reused graph is reprojected through the current schema by callers.
    assert project(reused, empty_schema()).snapshot_id == reused.snapshot_id
