"""Tracker protocol, OCR binding, and the none-sensor fallback. No weights."""

from __future__ import annotations

from PIL import Image

from loop.adaptive.cli import _semantic_candidates_from_graph
from loop.adaptive.environment import StepResult
from loop.adaptive.orchestrate import offer_decisions, registry_offers
from loop.adaptive.perceive import SceneAssembler, _hard_screen_change
from loop.adaptive.semantic_confirmation import SemanticConfirmer, _ContextState
from loop.adaptive.snapshot import MotionPerception
from loop.adaptive.tracker import (
    APPEARANCE_GRID,
    APPEARANCE_MAX_DISTANCE,
    ScriptedTracker,
    TrackMemory,
    appearance_distance,
    bind_ocr_to_tracks,
    crop_appearance,
    open_tracker,
)

BOX = (0.10, 0.20, 0.40, 0.40)
OTHER = (0.60, 0.60, 0.80, 0.85)
APP = b"appearance-token-0001"

BG = (16, 16, 16)
RED = (200, 40, 40)
GREEN = (30, 180, 70)


def _canvas(rects: dict[tuple[float, float, float, float], tuple[int, int, int]]) -> Image.Image:
    """64x64 frame with painted regions, so crops have distinguishable content."""

    image = Image.new("RGB", (64, 64), BG)
    for rect, color in rects.items():
        w, h = image.size
        x0, y0, x1, y1 = (int(round(value * side)) for value, side in zip(rect, (w, h, w, h)))
        image.paste(Image.new("RGB", (x1 - x0, y1 - y0), color), (x0, y0))
    return image


def _reconcile(memory: TrackMemory, image: Image.Image, rows: list[tuple]) -> tuple:
    """rows: (bbox, label, conf, role[, pref]); appearance is cropped from the frame."""

    return memory.reconcile(
        [
            (
                row[0],
                row[1],
                row[2],
                crop_appearance(image, row[0]),
                row[4] if len(row) > 4 else None,
                row[3],
            )
            for row in rows
        ]
    )


def _ids(hits) -> dict:
    """bbox -> track id, for checking identity against geometry."""

    return {hit.bbox: hit.track_id for hit in hits}


def test_distant_animation_pixels_do_not_replace_screen():
    before = Image.new("RGB", (64, 64), (10, 20, 30))
    animated = before.copy()
    animated.putpixel((0, 0), (255, 255, 255))
    animated.putpixel((63, 63), (255, 255, 255))
    assert not _hard_screen_change(before, animated, [BOX], [BOX])
    assert _hard_screen_change(before, Image.new("RGB", (64, 64), "white"), [BOX], [BOX])


def _step(image: Image.Image, n: int = 0) -> StepResult:
    return StepResult(
        observation=image,
        frame_digest=f"d{n}-{image.tobytes()[:8]!r}",
        episode_id="ep",
        episode_generation=1,
        step=n,
        tick=n,
        virtual_game_time=0.0,
        score=0.0,
        score_delta=0.0,
        reward=0.0,
        terminated=False,
        truncated=False,
        termination_reason=None,
        environment_revision="t",
        action_catalog_revision="t",
        errors=(),
        privileged_info={},
        capture_ts=0.0,
        dispatch_ts=0.0,
    )


def test_open_tracker_none_is_absent():
    assert open_tracker("none") is None


def test_scripted_states_are_unique_and_do_not_invent_boxes():
    image = Image.new("RGB", (32, 32), (1, 2, 3))
    tracker = ScriptedTracker(
        [
            [(BOX, "character", 0.9, APP), (OTHER, "coin", 0.8, b"other-appearance-02")],
            [(BOX, "character", 0.9, APP)],
            [],
            [(BOX, "", 0.4, APP)],
        ]
    )
    first = tracker.update(image)
    assert len(first) == 2
    assert len({hit.track_id for hit in first}) == 2
    assert {hit.state for hit in first} == {"new"}
    second = tracker.update(image)
    assert len(second) == 1
    assert second[0].state == "continuing"
    assert second[0].track_id == first[0].track_id
    assert tracker.update(image) == ()
    returned = tracker.update(image)
    assert len(returned) == 1
    assert returned[0].state == "returned"
    assert returned[0].track_id == first[0].track_id
    assert returned[0].detector_label == "character"


def test_ambiguous_ocr_does_not_bind():
    from loop.adaptive.tracker import TrackHit

    hits = (
        TrackHit("a", BOX, b"", "", 1.0, "new"),
        TrackHit("b", BOX, b"", "", 1.0, "new"),
    )
    assert bind_ocr_to_tracks(hits, [("Hello", BOX)]) == {}


def test_two_ocr_strings_keep_one_track_registry():
    image = Image.new("RGB", (80, 60), (8, 8, 8))
    tracker = ScriptedTracker([[(BOX, "character", 0.9, APP)], [(BOX, "character", 0.9, APP)]])
    assembler = SceneAssembler(tracker)
    tokens = [("Hello", (0.12, 0.22, 0.38, 0.38)), ("World", (0.11, 0.21, 0.36, 0.36))]
    _recs, first = assembler.update(image, ocr=tokens)
    _recs, second = assembler.update(image, ocr=tokens)
    registered = [node for node in first.nodes if node.attributes.get("registry_id")]
    assert len(registered) == 1
    assert registered[0].attributes["registry_owner"] == "track"
    assert all(not node.attributes.get("registry_id") for node in first.nodes if node.node_id.startswith("ocr"))
    offers = registry_offers(first, semantic="off")
    assert len(offers) == 1
    assert offers[0].offer_id == registered[0].attributes["registry_id"]
    withheld = offer_decisions(first, semantic="required")
    assert withheld[0].status == "withheld"
    assert withheld[0].reason == "semantic_missing"
    continuing = _semantic_candidates_from_graph(second)
    assert continuing == ()
    assert _semantic_candidates_from_graph(first)[0].candidate_id == offers[0].offer_id


def test_screen_replacement_can_return_and_cannot_invent():
    tracker = ScriptedTracker([[(BOX, "character", 0.9, APP)], [(BOX, "character", 0.9, APP)]])
    assembler = SceneAssembler(tracker)
    black = Image.new("RGB", (32, 32), (0, 0, 0))
    white = Image.new("RGB", (32, 32), (255, 255, 255))
    _, first = assembler.update(black, ocr=[])
    assert first.nodes[0].attributes["track_state"] == "new"
    _, second = assembler.update(white, ocr=[])
    assert assembler.screen_replaced is True
    states = [node.attributes.get("track_state") for node in second.nodes if node.attributes.get("registry_id")]
    assert states == ["returned"]
    assert {node.attributes["registry_id"] for node in second.nodes if node.attributes.get("registry_id")} == {
        node.attributes["registry_id"] for node in first.nodes if node.attributes.get("registry_id")
    }


def test_unavailable_tracker_keeps_the_ocr_sensor():
    class Dead:
        status = "unavailable"
        associator = "none"

        def update(self, image):
            raise AssertionError("unavailable tracker must not run")

        def clear_live(self):
            raise AssertionError("unavailable tracker must not run")

    image = Image.new("RGB", (40, 30), (12, 12, 12))
    assembler = SceneAssembler(Dead())
    _recs, graph = assembler.update(image, ocr=[("Continue", (0.08, 0.20, 0.40, 0.32))])
    assert assembler.tracker_status == "unavailable"
    assert any(node.attributes.get("registry_owner") == "ocr" for node in graph.nodes)
    perception = MotionPerception(Dead())
    snap = perception.qualify(_step(image))
    assert snap.fields["tracker_status"].value == "unavailable"


def test_motion_perception_uses_every_tracker_hit():
    image = Image.new("RGB", (32, 32), (4, 4, 4))
    tracker = ScriptedTracker([[(BOX, "character", 0.9, APP), (OTHER, "enemy", 0.7, b"e")]])
    snap = MotionPerception(tracker).qualify(_step(image))
    assert len(snap.entities) == 2
    assert len({ent.stable_id for ent in snap.entities}) == 2
    assert snap.fields["tracker_status"].value == "ok"


def test_track_memory_role_blocks_cross_role_iou_match():
    from loop.adaptive.tracker import TrackMemory, crop_appearance

    image = Image.new("RGB", (32, 32), (5, 5, 5))
    box = (0.20, 0.20, 0.50, 0.60)
    app = crop_appearance(image, box)
    mem = TrackMemory()
    hits = mem.reconcile(
        [
            (box, "panel", 0.9, app, None, "ui"),
            (box, "hero", 0.9, app, None, "world"),
        ]
    )
    assert len(hits) == 2
    assert len({hit.track_id for hit in hits}) == 2


def test_returned_id_is_classified_again():
    confirmer = SemanticConfirmer(None, model="m")
    ctx = confirmer._contexts.setdefault(("t", "g"), _ContextState())
    ctx.confirmed["trk_0"] = "control"
    ctx.observations["trk_0"] = {"o": "control"}
    confirmer.invalidate(["trk_0"])
    assert "trk_0" not in ctx.confirmed
    ctx.observations["trk_1"] = {"o": "control"}
    confirmer.clear_pending()
    assert ctx.observations == {}


# --- D1: tolerant appearance descriptor, evidence gating, identity lifecycle ---

WIDE = (0.10, 0.10, 0.55, 0.55)
CARD = (0.20, 0.20, 0.40, 0.40)


def test_appearance_descriptor_supports_a_distance_not_just_equality():
    image = _canvas({WIDE: RED})
    same = _canvas({WIDE: RED})
    noisy = same.point(lambda value: min(255, value + 3))
    other = _canvas({WIDE: GREEN})

    descriptor = crop_appearance(image, CARD)
    assert len(descriptor) == APPEARANCE_GRID * APPEARANCE_GRID * 3
    assert descriptor != b"" and len(descriptor) != 32  # not a sha256 digest

    assert appearance_distance(descriptor, crop_appearance(same, CARD)) == 0.0
    assert appearance_distance(descriptor, crop_appearance(noisy, CARD)) <= APPEARANCE_MAX_DISTANCE
    assert appearance_distance(descriptor, crop_appearance(other, CARD)) > APPEARANCE_MAX_DISTANCE
    # An incomparable descriptor is never a match.
    assert appearance_distance(descriptor, b"") == float("inf")


def test_minor_pixel_noise_still_continues():
    memory = TrackMemory()
    first = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "card", 0.9, "ui")])
    louder = _canvas({WIDE: RED}).point(lambda value: min(255, value + 3))
    second = _reconcile(memory, louder, [(CARD, "card", 0.9, "ui")])
    assert [hit.state for hit in second] == ["continuing"]
    assert second[0].track_id == first[0].track_id


def test_hover_scale_change_still_continues():
    memory = TrackMemory()
    first = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "card", 0.9, "ui")])
    hovered = (0.16, 0.16, 0.44, 0.44)  # ~2x area, 0.95 IoU, same content
    second = _reconcile(memory, _canvas({WIDE: RED}), [(hovered, "card", 0.9, "ui")])
    assert [hit.state for hit in second] == ["continuing"]
    assert second[0].track_id == first[0].track_id
    assert second[0].bbox == hovered


def test_label_edits_are_applied_and_absence_of_a_label_keeps_the_old_one():
    memory = TrackMemory()
    first = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "Attack", 0.9, "ui")])
    renamed = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "Defend", 0.9, "ui")])
    assert renamed[0].state == "continuing"
    assert renamed[0].track_id == first[0].track_id
    assert renamed[0].detector_label == "Defend"  # labels stay mutable
    blanked = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "", 0.9, "ui")])
    assert blanked[0].detector_label == "Defend"


def test_changed_object_on_the_old_box_gets_a_new_identity():
    memory = TrackMemory()
    first = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "card", 0.9, "ui")])
    changed = _reconcile(memory, _canvas({WIDE: GREEN}), [(CARD, "card", 0.9, "ui")])
    assert [hit.state for hit in changed] == ["new"]
    assert changed[0].track_id != first[0].track_id
    # The old identity is absent, not silently transferred, and offers nothing.
    assert first[0].track_id not in memory.live
    assert first[0].track_id not in {hit.track_id for hit in changed}


def test_absent_object_returns_with_a_tolerant_appearance_match():
    memory = TrackMemory()
    first = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "card", 0.9, "ui")])
    gone = _reconcile(memory, _canvas({WIDE: RED}), [])
    assert gone == ()
    assert first[0].track_id not in memory.live
    assert [tid for tid, *_ in memory.lost] == [first[0].track_id]
    noisy = _canvas({WIDE: RED}).point(lambda value: min(255, value + 3))
    back = _reconcile(memory, noisy, [(CARD, "card", 0.9, "ui")])
    assert [hit.state for hit in back] == ["returned"]
    assert back[0].track_id == first[0].track_id
    assert back[0].detector_label == "card"


def test_elapsed_absence_window_gates_returns():
    image = _canvas({WIDE: RED})
    memory = TrackMemory(window=2)
    first = _reconcile(memory, image, [(CARD, "card", 0.9, "ui")])
    for _ in range(2):
        assert _reconcile(memory, image, []) == ()
    assert [tid for tid, *_ in memory.lost] == [first[0].track_id]
    within = _reconcile(memory, image, [(CARD, "card", 0.9, "ui")])
    assert [hit.state for hit in within] == ["returned"]
    assert within[0].track_id == first[0].track_id
    for _ in range(4):
        assert _reconcile(memory, image, []) == ()
    assert memory.lost == []  # aged out past the window
    again = _reconcile(memory, image, [(CARD, "card", 0.9, "ui")])
    assert [hit.state for hit in again] == ["new"]
    assert again[0].track_id != first[0].track_id


def test_ambiguous_overlapping_candidates_abstain_instead_of_stealing_an_id():
    memory = TrackMemory()
    left = (0.12, 0.12, 0.26, 0.28)
    right = (0.20, 0.12, 0.34, 0.28)
    between = (0.16, 0.12, 0.30, 0.28)  # equal IoU to both, identical content
    first = _reconcile(
        memory,
        _canvas({(0.10, 0.10, 0.45, 0.30): RED}),
        [(left, "Strike", 0.9, "ui"), (right, "Strike", 0.9, "ui")],
    )
    assert len({hit.track_id for hit in first}) == 2
    second = _reconcile(memory, _canvas({(0.10, 0.10, 0.45, 0.30): RED}), [(between, "Strike", 0.9, "ui")])
    assert [hit.state for hit in second] == ["new"]
    assert second[0].track_id not in {hit.track_id for hit in first}
    assert {tid for tid, *_ in memory.lost} == {hit.track_id for hit in first}


def test_reordered_input_rows_keep_identity_with_position():
    memory = TrackMemory()
    frame = _canvas({(0.10, 0.10, 0.30, 0.30): RED, (0.60, 0.10, 0.80, 0.30): GREEN})
    left = (0.12, 0.12, 0.28, 0.28)
    right = (0.62, 0.12, 0.78, 0.28)
    first = _reconcile(memory, frame, [(left, "A", 0.9, "ui"), (right, "B", 0.9, "ui")])
    assert len({hit.track_id for hit in first}) == 2
    second = _reconcile(memory, frame, [(right, "B", 0.9, "ui"), (left, "A", 0.9, "ui")])
    assert {hit.state for hit in second} == {"continuing"}
    assert _ids(second) == _ids(first)


def test_same_name_adjacent_cards_keep_distinct_identities():
    memory = TrackMemory()
    frame = _canvas({(0.05, 0.70, 0.15, 0.95): RED, (0.16, 0.70, 0.26, 0.95): GREEN})
    boxes = [(0.06, 0.71, 0.14, 0.94), (0.17, 0.71, 0.25, 0.94)]
    first = _reconcile(memory, frame, [(box, "Strike", 0.9, "ui") for box in boxes])
    second = _reconcile(memory, frame, [(box, "Strike", 0.9, "ui") for box in boxes])
    assert len({hit.track_id for hit in first}) == 2
    assert {hit.state for hit in second} == {"continuing"}
    assert _ids(second) == _ids(first)


def test_role_change_blocks_a_return():
    memory = TrackMemory()
    first = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "panel", 0.9, "ui")])
    assert _reconcile(memory, _canvas({WIDE: RED}), []) == ()
    other_role = _reconcile(memory, _canvas({WIDE: RED}), [(CARD, "hero", 0.9, "world")])
    assert [hit.state for hit in other_role] == ["new"]
    assert other_role[0].track_id != first[0].track_id


def test_reset_never_reissues_an_old_id():
    memory = TrackMemory()
    frame = _canvas({(0.10, 0.10, 0.30, 0.30): RED, (0.60, 0.10, 0.80, 0.30): GREEN})
    rows = [
        ((0.12, 0.12, 0.28, 0.28), "A", 0.9, "ui"),
        ((0.62, 0.12, 0.78, 0.28), "B", 0.9, "ui"),
    ]
    before = {hit.track_id for hit in _reconcile(memory, frame, rows)}
    memory.reset()
    assert memory.lost == [] and memory.live == {}
    after = _reconcile(memory, frame, rows)
    assert {hit.state for hit in after} == {"new"}
    assert not ({hit.track_id for hit in after} & before)


def test_lost_memory_is_bounded():
    memory = TrackMemory(max_lost=3)
    boxes = [
        (0.02 + 0.18 * index, 0.10, 0.10 + 0.18 * index, 0.30)
        for index in range(5)
    ]
    _reconcile(
        memory,
        _canvas({(0.0, 0.0, 1.0, 0.5): RED}),
        [(box, f"c{index}", 0.9, "ui") for index, box in enumerate(boxes)],
    )
    assert len(memory.live) == 5
    assert _reconcile(memory, _canvas({}), []) == ()
    assert len(memory.lost) == 3


def test_detector_supplied_id_is_still_gated_and_never_reissued():
    memory = TrackMemory()
    frame = _canvas({WIDE: RED})
    first = _reconcile(memory, frame, [(CARD, "card", 0.9, "ui", "7")])
    assert first[0].track_id == "7" and first[0].state == "new"
    kept = _reconcile(memory, frame, [(CARD, "card", 0.9, "ui", "7")])
    assert kept[0].state == "continuing" and kept[0].track_id == "7"
    # Same supplied id, different content on the old box: no identity transfer,
    # and the still-remembered id is not reissued for the new object.
    changed = _reconcile(memory, _canvas({WIDE: GREEN}), [(CARD, "card", 0.9, "ui", "7")])
    assert [hit.state for hit in changed] == ["new"]
    assert changed[0].track_id != "7"
    assert "7" not in memory.live
    assert "7" in {tid for tid, *_ in memory.lost}
