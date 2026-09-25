"""Card split proposals from synthetic PIL layouts only."""

from __future__ import annotations

from PIL import Image, ImageDraw

from loop.adaptive.card_proposals import propose_hand_cards, synth_hand_image

HAND = (0.1, 0.55, 0.9, 0.95)


def _centers(props):
    return [((p["bbox"][0] + p["bbox"][2]) * 0.5, (p["bbox"][1] + p["bbox"][3]) * 0.5) for p in props]


def test_five_visual_gaps_yield_five_cards():
    image = synth_hand_image(card_count=5, gap_px=8)
    props = propose_hand_cards(image, HAND, frame_id="five")
    assert len(props) == 5
    assert all(p["source"] == "card-split" and p["role"] == "ui" for p in props)
    centers = _centers(props)
    assert len({(round(x, 4), round(y, 4)) for x, y in centers}) == 5


def test_hand_wide_box_is_never_emitted():
    image = synth_hand_image(card_count=4, gap_px=8)
    props = propose_hand_cards(image, HAND)
    for prop in props:
        assert prop["bbox"] != HAND


def test_empty_ocr_still_splits_on_visual_gaps():
    image = synth_hand_image(card_count=3, gap_px=10)
    props = propose_hand_cards(image, HAND, ocr_tokens=[])
    assert len(props) == 3


def test_duplicate_titles_in_one_column_withholds():
    image = synth_hand_image(card_count=3, gap_px=10)
    mid = (HAND[0] + HAND[2]) * 0.5
    tokens = [
        ("Strike", (mid - 0.05, 0.6, mid - 0.01, 0.7)),
        ("Strike", (mid - 0.04, 0.62, mid, 0.72)),
    ]
    props = propose_hand_cards(image, HAND, ocr_tokens=tokens)
    assert props == []


def test_hover_enlargement_keeps_distinct_centers():
    image = synth_hand_image(card_count=4, gap_px=6, hover_index=2, hover_scale=1.25)
    props = propose_hand_cards(image, HAND)
    assert len(props) >= 3
    centers = _centers(props)
    assert len(centers) == len({(round(x, 5), round(y, 5)) for x, y in centers})


def test_changing_hand_count():
    three = propose_hand_cards(synth_hand_image(card_count=3, gap_px=10), HAND)
    five = propose_hand_cards(synth_hand_image(card_count=5, gap_px=8), HAND)
    assert len(three) == 3
    assert len(five) == 5


def test_inseparable_solid_hand_withholds():
    image = synth_hand_image(solid=True)
    props = propose_hand_cards(image, HAND)
    assert props == []


def test_overlapping_cards_still_split_with_gaps():
    image = synth_hand_image(card_count=4, gap_px=4, overlap_px=8)
    props = propose_hand_cards(image, HAND)
    assert len(props) >= 2
    assert len(_centers(props)) == len(props)


def test_fanned_cards_use_upper_ocr_titles_not_lower_details():
    image = synth_hand_image(card_count=5, gap_px=0, overlap_px=8)
    xs = [0.18, 0.34, 0.50, 0.66, 0.82]
    ocr = []
    for i, x in enumerate(xs):
        ocr.append(("Strike" if i % 2 == 0 else "Defend", (x - 0.03, 0.62, x + 0.03, 0.68)))
        ocr.append(("Attack", (x - 0.03, 0.82, x + 0.03, 0.88)))
    props = propose_hand_cards(image, HAND, ocr_tokens=ocr)
    assert len(props) == 5
    assert len({round(x, 3) for x, _y in _centers(props)}) == 5


def test_single_card_region_is_not_split_into_cards():
    """A region showing one card and one title is a card, not a hand."""

    region = (0.35, 0.55, 0.55, 0.95)
    image = synth_hand_image(card_count=1, hand=region)
    assert propose_hand_cards(image, region, [("Strike", (0.38, 0.60, 0.52, 0.68))]) == []


def test_decorative_bottom_panel_is_withheld_not_offered():
    """A screen-spanning lower panel with spaced labels must not become cards.

    Each column of such a region exceeds the frozen oversized-offer area bound,
    so the splitter withholds instead of emitting false card offers.
    """

    image = Image.new("RGB", (320, 180), (18, 20, 24))
    draw = ImageDraw.Draw(image)
    for i in range(3):
        x0 = 0.06 + i * 0.32
        draw.rectangle((int(x0 * 320), 140, int((x0 + 0.22) * 320), 172), fill=(150, 150, 150))
    panel = (0.0, 0.09, 1.0, 1.0)
    tokens = [
        ("Victory", (0.08, 0.60, 0.20, 0.70)),
        ("Rewards", (0.40, 0.60, 0.54, 0.70)),
        ("Continue", (0.72, 0.60, 0.88, 0.70)),
    ]
    assert propose_hand_cards(image, panel, tokens, frame_id="panel") == []
