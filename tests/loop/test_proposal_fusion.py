"""Synthetic role-aware fusion and identity tests. No weights."""

from __future__ import annotations

from PIL import Image

from loop.adaptive.proposal_fusion import FrameProposal, ProposalFusion

IMG = Image.new("RGB", (64, 64), (20, 30, 40))


def _p(bbox, role="ui", source="gpa", hint="", conf=0.9, frame=0):
    return FrameProposal(bbox, role, source, hint, conf, frame)


def test_five_same_name_cards_stay_distinct():
    fusion = ProposalFusion()
    cards = [
        (0.05, 0.70, 0.15, 0.95),
        (0.16, 0.70, 0.26, 0.95),
        (0.27, 0.70, 0.37, 0.95),
        (0.38, 0.70, 0.48, 0.95),
        (0.49, 0.70, 0.59, 0.95),
    ]
    props = [_p(b, hint="Strike") for b in cards]
    hits = fusion.fuse(IMG, props)
    assert len(hits) == 5
    assert len({h.track_id for h in hits}) == 5


def test_gpa_and_dino_same_ui_one_identity():
    fusion = ProposalFusion()
    box = (0.40, 0.05, 0.60, 0.12)
    props = [
        _p(box, role="ui", source="gpa", hint="End Turn"),
        _p(box, role="ui", source="grounding_dino", hint="button", conf=0.7),
    ]
    first = fusion.fuse(IMG, props)
    assert len(first) == 1
    assert "gpa" in first[0].source
    assert first[0].class_hint == "End Turn"
    second = fusion.fuse(IMG, props)
    assert len(second) == 1
    assert second[0].track_id == first[0].track_id


def test_ocr_spelling_change_keeps_id():
    fusion = ProposalFusion()
    box = (0.10, 0.20, 0.30, 0.35)
    props = [_p(box, hint="card")]
    first = fusion.fuse(IMG, props, ocr=[("Helo", box)])
    second = fusion.fuse(IMG, props, ocr=[("Hello", box)])
    assert first[0].track_id == second[0].track_id


def test_missing_detection_drops_live_offer():
    fusion = ProposalFusion()
    box = (0.12, 0.22, 0.32, 0.42)
    first = fusion.fuse(IMG, [_p(box)])
    assert len(first) == 1
    assert fusion.fuse(IMG, []) == ()


def test_reset_clears_stale_geometry():
    fusion = ProposalFusion()
    box = (0.12, 0.22, 0.32, 0.42)
    first = fusion.fuse(IMG, [_p(box)])
    fusion.reset()
    assert fusion.fuse(IMG, []) == ()
    second = fusion.fuse(IMG, [_p(box)])
    assert second[0].state == "new"
    assert not fusion.memory.lost


def test_overlapping_world_actors_stay_distinct():
    fusion = ProposalFusion()
    a = (0.20, 0.30, 0.35, 0.70)
    b = (0.30, 0.30, 0.45, 0.70)  # partial overlap, IoU below 0.7
    hits = fusion.fuse(
        IMG,
        [
            _p(a, role="world", source="dino", hint="enemy"),
            _p(b, role="world", source="dino", hint="enemy"),
        ],
    )
    assert len(hits) == 2
    assert len({h.track_id for h in hits}) == 2


def test_ui_world_overlap_no_id_steal():
    fusion = ProposalFusion()
    box = (0.25, 0.40, 0.55, 0.75)
    first = fusion.fuse(
        IMG,
        [
            _p(box, role="ui", source="gpa", hint="panel"),
            _p(box, role="world", source="dino", hint="character"),
        ],
    )
    assert len(first) == 2
    ids = {h.track_id for h in first}
    second = fusion.fuse(
        IMG,
        [
            _p(box, role="ui", source="gpa", hint="panel"),
            _p(box, role="world", source="dino", hint="character"),
        ],
    )
    assert {h.track_id for h in second} == ids


def test_moving_box_keeps_identity():
    fusion = ProposalFusion()
    path = [
        (0.10, 0.20, 0.20, 0.40),
        (0.12, 0.22, 0.22, 0.42),
        (0.14, 0.24, 0.24, 0.44),
    ]
    ids = []
    for i, box in enumerate(path):
        hits = fusion.fuse(IMG, [_p(box, role="world", source="dino", hint="enemy", frame=i)])
        ids.append(hits[0].track_id)
    assert len(set(ids)) == 1


def test_new_proposal_before_existing_keeps_metadata_with_track():
    fusion = ProposalFusion()
    existing_box = (0.10, 0.15, 0.28, 0.38)
    new_box = (0.62, 0.55, 0.90, 0.92)

    existing = fusion.fuse(
        IMG,
        [_p(existing_box, role="world", source="dino", hint="existing enemy")],
    )[0]
    # Higher confidence puts the UI proposal first in merge_proposals. On
    # reconcile, however, the continuing world track is returned before the
    # newly created UI track.
    hits = fusion.fuse(
        IMG,
        [
            _p(new_box, role="ui", source="card-split", hint="new card", conf=0.99),
            _p(existing_box, role="world", source="dino", hint="existing enemy", conf=0.9),
        ],
    )

    by_id = {hit.track_id: hit for hit in hits}
    assert len(hits) == 2
    assert by_id[existing.track_id].bbox == existing_box
    assert by_id[existing.track_id].role == "world"
    assert by_id[existing.track_id].source == "dino"
    assert by_id[existing.track_id].class_hint == "existing enemy"
    assert by_id[existing.track_id].detector_label == "existing enemy"

    created = next(hit for hit in hits if hit.track_id != existing.track_id)
    assert created.bbox == new_box
    assert created.role == "ui"
    assert created.source == "card-split"
    assert created.class_hint == "new card"
    assert created.detector_label == "new card"


def test_rejects_full_screen_and_malformed():
    fusion = ProposalFusion()
    hits = fusion.fuse(
        IMG,
        [
            _p((0.0, 0.0, 1.0, 1.0), hint="bg"),
            _p((0.5, 0.5, 0.4, 0.6), hint="bad"),
        ],
    )
    assert hits == ()
    assert fusion.diagnostics.rejections.get("full_screen") == 1
    assert fusion.diagnostics.rejections.get("malformed") == 1


def test_fanned_cards_keep_their_own_titles():
    fusion = ProposalFusion()
    titles = ["Strike", "Guard", "Bash", "Focus", "Feint"]
    boxes = [(0.05 + 0.04 * i, 0.60, 0.15 + 0.04 * i, 0.95) for i in range(5)]
    tokens = [(t, (b[0] + 0.025, 0.72, b[0] + 0.075, 0.80)) for t, b in zip(titles, boxes)]
    props = [_p(b, hint="card") for b in boxes]

    first = fusion.fuse(IMG, props, ocr=tokens)
    assert len(first) == 5
    assert len({h.track_id for h in first}) == 5
    assert {h.bbox for h in first} == set(boxes)
    by_box = {h.bbox: h for h in first}
    for title, box in zip(titles, boxes):
        assert by_box[box].detector_label == title
    assert fusion.diagnostics.ocr_attached == 5
    assert fusion.diagnostics.ocr_orphans == []

    second = fusion.fuse(IMG, props, ocr=tokens)
    assert {h.track_id for h in second} == {h.track_id for h in first}
    assert {h.track_id: h.detector_label for h in second} == {
        h.track_id: h.detector_label for h in first
    }


def test_contained_title_beats_a_neighbouring_centre_claim():
    fusion = ProposalFusion()
    left = (0.10, 0.50, 0.30, 0.90)
    right = (0.16, 0.50, 0.36, 0.90)  # IoU 0.54: overlapping, not merged
    hits = fusion.fuse(
        IMG,
        [_p(left, hint="card"), _p(right, hint="card")],
        ocr=[("Strike", left), ("Guard", right)],
    )
    assert len(hits) == 2
    assert {h.bbox for h in hits} == {left, right}
    by_box = {h.bbox: h for h in hits}
    # Each title is contained by its own card and only centre-claimed by the
    # neighbour, so containment decides.
    assert by_box[left].detector_label == "Strike"
    assert by_box[right].detector_label == "Guard"
    assert fusion.diagnostics.ocr_attached == 2
    assert fusion.diagnostics.ocr_orphans == []


def test_token_covering_a_small_hit_centre_attaches():
    fusion = ProposalFusion()
    icon = (0.20, 0.20, 0.26, 0.26)
    blob = (0.16, 0.16, 0.34, 0.34)  # IoU 0.11: overlap alone would not bind
    hits = fusion.fuse(IMG, [_p(icon, hint="icon")], ocr=[("End Turn", blob)])
    assert len(hits) == 1
    assert hits[0].detector_label == "End Turn"
    assert fusion.diagnostics.ocr_attached == 1
    assert fusion.diagnostics.ocr_orphans == []


def test_same_title_cards_keep_distinct_ids_and_update_their_text():
    fusion = ProposalFusion()
    a = (0.05, 0.60, 0.15, 0.95)
    b = (0.45, 0.60, 0.55, 0.95)
    props = [_p(a, hint="card"), _p(b, hint="card")]

    first = fusion.fuse(IMG, props, ocr=[("Strike", a), ("Strike", b)])
    assert len(first) == 2
    assert len({h.track_id for h in first}) == 2
    assert {h.detector_label for h in first} == {"Strike"}
    ids = {h.bbox: h.track_id for h in first}

    # A value edit changes the text on a stable identity.
    second = fusion.fuse(IMG, props, ocr=[("Strike", a), ("Strike+", b)])
    by_id = {h.track_id: h for h in second}
    assert set(by_id) == set(ids.values())
    assert by_id[ids[a]].detector_label == "Strike"
    assert by_id[ids[b]].detector_label == "Strike+"
    assert fusion.diagnostics.ocr_attached == 2


def test_ambiguous_straddling_title_is_rejected_and_keeps_both_labels():
    fusion = ProposalFusion()
    a = (0.10, 0.10, 0.30, 0.40)
    b = (0.14, 0.14, 0.34, 0.44)  # IoU 0.53: kept apart
    token = (0.15, 0.15, 0.29, 0.39)  # inside the overlap, so inside both
    hits = fusion.fuse(IMG, [_p(a, hint="card"), _p(b, hint="card")], ocr=[("Strike", token)])

    assert len(hits) == 2
    assert {h.detector_label for h in hits} == {"card"}
    assert fusion.diagnostics.ocr_attached == 0
    assert fusion.diagnostics.rejections.get("ocr_ambiguous") == 1
    orphan = fusion.diagnostics.ocr_orphans[0]
    assert orphan["reason"] == "ambiguous"
    assert orphan["text"] == "Strike"
    assert sorted(orphan["candidates"]) == sorted(h.track_id for h in hits)


def test_unmatched_text_stays_diagnostic_evidence():
    fusion = ProposalFusion()
    card = (0.10, 0.60, 0.30, 0.95)
    stray = (0.70, 0.05, 0.95, 0.15)
    hits = fusion.fuse(IMG, [_p(card, hint="card")], ocr=[("Stray", stray)])

    assert len(hits) == 1
    assert hits[0].detector_label == "card"
    assert fusion.diagnostics.ocr_attached == 0
    assert fusion.diagnostics.rejections.get("ocr_unmatched") == 1
    orphan = fusion.diagnostics.ocr_orphans[0]
    assert orphan["reason"] == "unmatched"
    assert orphan["candidates"] == []
    assert orphan["bbox"] == [0.70, 0.05, 0.95, 0.15]


def test_ocr_text_never_moves_or_grows_the_target_box():
    fusion = ProposalFusion()
    card = (0.10, 0.60, 0.30, 0.95)
    token = (0.02, 0.50, 0.40, 1.00)  # larger than the card: still its text
    hits = fusion.fuse(IMG, [_p(card, hint="card")], ocr=[("Strike", token)])

    assert len(hits) == 1
    assert hits[0].bbox == card
    assert hits[0].detector_label == "Strike"
    assert fusion.diagnostics.ocr_attached == 1


def test_merge_keeps_the_tight_ui_box_as_the_target_interior():
    fusion = ProposalFusion()
    tight = (0.30, 0.60, 0.36, 0.80)
    loose = (0.295, 0.595, 0.365, 0.805)  # IoU 0.82, lower-ranked source
    hits = fusion.fuse(
        IMG,
        [
            _p(tight, role="ui", source="gpa", hint="card"),
            _p(loose, role="ui", source="grounding-dino", hint="panel", conf=0.99),
        ],
    )

    assert len(hits) == 1
    assert hits[0].bbox == tight
    assert hits[0].source.split(",")[0] == "gpa"
    assert fusion.diagnostics.merges == 1


def test_rejection_counters_describe_one_frame_only():
    fusion = ProposalFusion()
    junk = _p((0.0, 0.0, 1.0, 1.0), hint="bg")
    card = (0.10, 0.60, 0.30, 0.95)

    assert fusion.fuse(IMG, [junk]) == ()
    assert fusion.diagnostics.rejections == {"full_screen": 1}

    # A clean frame reports its own counters, not the previous frame's total.
    assert len(fusion.fuse(IMG, [_p(card, hint="card")])) == 1
    assert fusion.diagnostics.rejections == {}
    assert fusion.diagnostics.merges == 0

    assert fusion.fuse(IMG, [junk]) == ()
    assert fusion.diagnostics.rejections == {"full_screen": 1, "repeat_junk": 1}
