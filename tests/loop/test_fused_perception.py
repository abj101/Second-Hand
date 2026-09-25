"""Fused perception wiring. Stub detectors only; no weight download."""

from __future__ import annotations

from PIL import Image, ImageDraw

from loop.adaptive.card_proposals import synth_hand_image
from loop.adaptive.fused_perception import FusedPerceptionTracker, _split_hands
from loop.adaptive.gui_yolo import GpaGuiDetector
from loop.adaptive.orchestrate import offer_decisions, registry_offers
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.schema import SceneGraph, SceneNode
from loop.adaptive.tracker import open_tracker

HAND = (0.1, 0.55, 0.9, 0.95)
END = (0.82, 0.02, 0.96, 0.10)
PLAYER = (0.18, 0.35, 0.32, 0.70)
ENEMY = (0.68, 0.48, 0.86, 0.72)


def _gui():
    def detector(_image):
        return [
            {"bbox": HAND, "class_hint": "icon", "conf": 0.9},
            {"bbox": END, "class_hint": "button", "conf": 0.92},
        ]

    return GpaGuiDetector(detector=detector)


def _dino(player=PLAYER, enemy=ENEMY):
    class Stub:
        status = "ok"
        load_ms = 0.0
        last_infer_ms = 1.0

        def propose(self, _image, *, frame_id=""):
            return [
                {
                    "bbox": player,
                    "role": "world",
                    "source": "grounding-dino",
                    "class_hint": "person",
                    "confidence": 0.8,
                    "frame_id": frame_id,
                },
                {
                    "bbox": enemy,
                    "role": "world",
                    "source": "grounding-dino",
                    "class_hint": "animal",
                    "confidence": 0.75,
                    "frame_id": frame_id,
                },
            ]

    return Stub()


def test_open_tracker_default_none():
    assert open_tracker("none") is None


def test_fused_offers_use_track_ids_not_ocr():
    image = synth_hand_image(card_count=5, gap_px=8)
    tracker = FusedPerceptionTracker("fused", gui=_gui(), dino=_dino())
    assembler = SceneAssembler(tracker)
    ocr = [
        ("End Turn", END),
        ("Strike", (0.12, 0.60, 0.24, 0.72)),
        ("Strike", (0.28, 0.60, 0.40, 0.72)),
        ("Bash", (0.44, 0.60, 0.56, 0.72)),
        ("Strike", (0.60, 0.60, 0.72, 0.72)),
        ("Defend", (0.76, 0.60, 0.88, 0.72)),
        ("80/80", (0.20, 0.66, 0.30, 0.70)),
    ]
    _recs, scene = assembler.update(image, ocr=ocr)
    offers = registry_offers(scene)
    assert offers
    assert all(o.offer_id.startswith("trk_") for o in offers)
    assert all(n.attributes.get("registry_owner") != "ocr" or not n.attributes.get("registry_id")
               for n in scene.nodes if n.node_id.startswith("ocr"))
    world_ids = {
        n.attributes["registry_id"]
        for n in scene.nodes
        if n.attributes.get("track_role") == "world" and n.attributes.get("registry_id")
    }
    assert world_ids
    assert {o.offer_id for o in offers}.isdisjoint(world_ids)
    assert any(d.reason == "world_target_not_control" for d in offer_decisions(scene))


def test_label_change_keeps_track_id():
    image = synth_hand_image(card_count=5, gap_px=8)
    tracker = FusedPerceptionTracker("fused-gpa", gui=_gui(), dino=None)
    assembler = SceneAssembler(tracker)
    _, first = assembler.update(image, ocr=[("End Turn 1", END)])
    end = next(n for n in first.nodes if n.attributes.get("label") == "End Turn 1")
    _, second = assembler.update(image, ocr=[("End Turn 2", END)])
    again = next(n for n in second.nodes if "End Turn" in str(n.attributes.get("label")))
    assert end.attributes["registry_id"] == again.attributes["registry_id"]


def test_empty_frame_clears_offers():
    image = synth_hand_image(card_count=5, gap_px=8)
    tracker = FusedPerceptionTracker("fused-gpa", gui=_gui(), dino=None)
    assembler = SceneAssembler(tracker)
    assembler.update(image, ocr=[("End Turn", END)])
    blank = Image.new("RGB", image.size, (0, 0, 0))

    def empty(_image):
        return []

    tracker.gui = GpaGuiDetector(detector=empty)
    _, scene = assembler.update(blank, ocr=[])
    assert registry_offers(scene) == ()


def test_world_role_rejected_on_graph():
    graph = SceneGraph(
        "s",
        (
            SceneNode(
                "track_trk_0",
                PLAYER,
                (),
                {
                    "registry_id": "trk_0",
                    "registry_owner": "track",
                    "label": "enemy",
                    "track_role": "world",
                },
            ),
        ),
        (),
    )
    decisions = offer_decisions(graph)
    assert decisions[0].status == "rejected"
    assert decisions[0].reason == "world_target_not_control"


def test_unclassified_gui_icons_and_values_are_not_offers():
    def node(name, label, extra=None):
        return SceneNode(
            name,
            END,
            (),
            {
                "registry_id": name,
                "registry_owner": "track",
                "track_role": "ui",
                "track_source": "gpa-gui",
                "label": label,
                **(extra or {}),
            },
        )

    graph = SceneGraph(
        "s",
        (
            node("icon", "icon"),
            node("value", "3/3", {"numeric": 3}),
            node("version", "[v0.111.0](2026.08.14)"),
            node("end", "End Turn"),
        ),
        (),
    )
    assert {o.offer_id for o in registry_offers(graph)} == {"end"}
    reasons = {d.candidate_id: d.reason for d in offer_decisions(graph)}
    assert reasons["icon"] == "unclassified_gui_icon"
    assert reasons["value"] == "gui_value_not_control"
    assert reasons["version"] == "gui_version_not_control"


def test_unavailable_gui_is_not_zero_hit_success():
    def boom(_path):
        raise RuntimeError("missing")

    tracker = FusedPerceptionTracker(
        "fused-gpa",
        gui=GpaGuiDetector(loader=boom, weights_path="no-such.pt"),
        dino=None,
    )
    assert tracker.status == "unavailable"
    assert tracker.update(Image.new("RGB", (8, 8), 0)) == ()
    _, scene = SceneAssembler(tracker).update(
        Image.new("RGB", (32, 32), 0), ocr=[("Continue", (0.2, 0.2, 0.5, 0.4))]
    )
    assert registry_offers(scene) == ()


def test_fused_detector_failure_does_not_resume_ocr_offers():
    tracker = FusedPerceptionTracker("fused-gpa", gui=_gui(), dino=None)
    assembler = SceneAssembler(tracker)
    image = synth_hand_image(card_count=5, gap_px=8)
    _, first = assembler.update(image, ocr=[("End Turn", END)])
    assert registry_offers(first)
    tracker.gui.status = "unavailable"
    _, failed = assembler.update(image, ocr=[("End Turn", END)])
    assert registry_offers(failed) == ()


def test_inseparable_hand_is_withheld():
    tracker = FusedPerceptionTracker("fused-gpa", gui=_gui(), dino=None)
    hits = tracker.update(synth_hand_image(solid=True), ocr=[])
    assert all(hit.bbox != HAND for hit in hits)
    assert any(hit.bbox == END for hit in hits)


def test_nested_gui_badge_does_not_duplicate_split_card():
    def detector(_image):
        return [
            {"bbox": HAND, "class_hint": "icon", "conf": 0.9},
            {"bbox": (0.27, 0.60, 0.35, 0.90), "class_hint": "icon", "conf": 0.5},
        ]

    tracker = FusedPerceptionTracker(
        "fused-gpa", gui=GpaGuiDetector(detector=detector), dino=None
    )
    hits = tracker.update(synth_hand_image(card_count=5, gap_px=8), ocr=[])
    assert len(hits) == 5
    assert all(hit.bbox != HAND for hit in hits)


def test_partial_card_sized_region_does_not_infer_neighbor_cards():
    seed = (0.26, 0.72, 0.40, 0.98)
    ui = [{"bbox": seed, "class_hint": "icon", "confidence": 0.8}]
    ocr = [
        ("Label", (0.30, 0.80, 0.34, 0.84)),
        ("Detail", (0.51, 0.80, 0.57, 0.84)),
    ]

    split = _split_hands(synth_hand_image(), ui, ocr, "partial-seed")

    assert split == ui


def test_oversized_lower_screen_row_is_not_offered_as_cards():
    """A decorative lower panel must not become card offers (E3 measured boundary).

    The row passes the hand gate (w/h/cy) but is screen-spanning; every split
    column would exceed the frozen oversized-offer area bound and is withheld.
    """

    panel = (0.0, 0.12, 1.0, 0.99)
    image = Image.new("RGB", (320, 180), (18, 20, 24))
    draw = ImageDraw.Draw(image)
    for i in range(3):
        x0 = 0.06 + i * 0.32
        draw.rectangle((int(x0 * 320), 140, int((x0 + 0.22) * 320), 172), fill=(150, 150, 150))
    ui = [{"bbox": panel, "class_hint": "icon", "confidence": 0.8}]
    ocr = [
        ("Victory", (0.08, 0.60, 0.20, 0.70)),
        ("Rewards", (0.40, 0.60, 0.54, 0.70)),
        ("Continue", (0.72, 0.60, 0.88, 0.70)),
    ]

    split = _split_hands(image, ui, ocr, "panel")

    assert [row for row in split if row.get("source") == "card-split"] == []
    assert panel not in [tuple(row["bbox"]) for row in split]


def test_hand_row_is_not_widened_to_an_adjacent_card_row():
    """Cards come from the hand row's own evidence, not from neighbouring rows.

    E3 measured that widening a truncated hand region to an adjacent card-shaped
    row adds an unmatched column (hover frame: 1 -> 2 false offers, no new match),
    so the routing stays scoped to the detected hand container.
    """

    hand = (0.1, 0.55, 0.5, 0.95)
    neighbour = (0.52, 0.58, 0.68, 0.99)
    ui = [
        {"bbox": hand, "class_hint": "icon", "confidence": 0.8},
        {"bbox": neighbour, "class_hint": "icon", "confidence": 0.6},
    ]
    ocr = [
        ("Card A", (0.17, 0.62, 0.23, 0.70)),
        ("Card B", (0.37, 0.62, 0.43, 0.70)),
        ("Neighbour", (0.58, 0.62, 0.66, 0.70)),
    ]

    split = _split_hands(synth_hand_image(card_count=2, hand=hand), ui, ocr, "adjacent")
    cards = [tuple(row["bbox"]) for row in split if row.get("source") == "card-split"]

    assert len(cards) == 2
    assert all(card[0] >= hand[0] - 1e-9 and card[2] <= hand[2] + 1e-9 for card in cards)


def _fixed_gui(rows):
    return GpaGuiDetector(detector=lambda _image: rows)


def test_diagnostics_report_raw_accepted_rejected_merged_and_sources():
    rows = [
        {"bbox": END, "class_hint": "button", "conf": 0.92},
        {"bbox": (0.40, 0.02, 0.60, 0.10), "class_hint": "icon", "conf": 0.8},
        {"bbox": "nope", "class_hint": "icon", "conf": 0.4},
    ]
    tracker = FusedPerceptionTracker("fused-gpa", gui=_fixed_gui(rows), dino=None)
    hits = tracker.update(Image.new("RGB", (64, 64), 0), ocr=[], frame_id="d1")
    diag = tracker.diagnostics
    assert diag["available"] is True and diag["reason"] is None
    assert diag["frame_id"] == "d1"
    assert diag["raw"] == {"gui": 3, "world": None}
    assert diag["accepted"] == {"gui": 2, "world": None}
    assert diag["rejected"] == {
        "gui": {"malformed": 1, "off_frame": 0, "full_screen": 0},
        "world": None,
        "fusion": {},
    }
    assert diag["merged"] == 0
    assert diag["tracks"] == len(hits) == 2
    assert diag["sources"] == {"gpa-gui": 2}
    assert diag["split"] == {"added": 0, "dropped": 0}
    assert diag["providers"]["gui"]["status"] == "ok"
    assert diag["providers"]["gui"]["backend"] == "stub"


def test_diagnostics_pass_through_provider_counters_and_missing_as_unavailable():
    tracker = FusedPerceptionTracker("fused", gui=_gui(), dino=_dino())
    hits = tracker.update(synth_hand_image(card_count=5, gap_px=8), ocr=[], frame_id="d4")
    diag = tracker.diagnostics
    assert diag["raw"] == {"gui": 2, "world": None}
    assert diag["accepted"]["world"] is None
    assert diag["rejected"]["world"] is None
    assert diag["sources"] == {"gpa-gui": 1, "card-split": 5, "grounding-dino": 2}
    assert diag["providers"]["world"]["status"] == "ok"
    assert diag["providers"]["world"]["fallback"] is None
    assert diag["tracks"] == len(hits)


def test_diagnostics_count_split_additions_and_dropped_hand_regions():
    def detector(_image):
        return [
            {"bbox": HAND, "class_hint": "icon", "conf": 0.9},
            {"bbox": (0.27, 0.60, 0.35, 0.90), "class_hint": "icon", "conf": 0.5},
        ]

    tracker = FusedPerceptionTracker("fused-gpa", gui=GpaGuiDetector(detector=detector), dino=None)
    hits = tracker.update(synth_hand_image(card_count=5, gap_px=8), ocr=[], frame_id="d5")
    diag = tracker.diagnostics
    assert diag["split"] == {"added": 5, "dropped": 2}
    assert diag["sources"] == {"card-split": 5}
    assert diag["tracks"] == len(hits) == 5
    # Truthful accounting: what reached fusion is what the provider accepted,
    # minus the regions the splitter consumed, plus the cards it produced.
    assert sum(diag["sources"].values()) == (
        diag["accepted"]["gui"] - diag["split"]["dropped"] + diag["split"]["added"]
    )


def test_provider_device_and_fallback_are_reported_for_the_gpa_backend(monkeypatch):
    monkeypatch.setattr("loop.adaptive.gui_yolo._cuda", lambda: True)

    class Model:
        def __init__(self, fail_gpu):
            self.fail_gpu = fail_gpu

        def predict(self, _image, *, device, **_kw):
            if device == 0 and self.fail_gpu:
                raise NotImplementedError("CPU torchvision NMS")
            return []

    image = Image.new("RGB", (16, 16), 0)
    cuda = FusedPerceptionTracker(
        "fused-gpa", gui=GpaGuiDetector(loader=lambda _p: Model(False)), dino=None
    )
    cuda.update(image, ocr=[], frame_id="d6")
    gui = cuda.diagnostics["providers"]["gui"]
    assert gui["backend"] == "gpa-gui"
    assert gui["device"] == "0" and gui["fallback"] is False
    assert cuda.diagnostics["available"] is True

    fallback = FusedPerceptionTracker(
        "fused-gpa", gui=GpaGuiDetector(loader=lambda _p: Model(True)), dino=None
    )
    fallback.update(image, ocr=[], frame_id="d7")
    assert fallback.diagnostics["providers"]["gui"]["device"] == "cpu"
    assert fallback.diagnostics["providers"]["gui"]["fallback"] is True


def test_provider_failure_clears_diagnostics_and_output():
    tracker = FusedPerceptionTracker("fused-gpa", gui=_gui(), dino=None)
    image = synth_hand_image(card_count=5, gap_px=8)
    assert tracker.update(image, ocr=[], frame_id="ok") != ()
    assert tracker.diagnostics["available"] is True
    tracker.gui.status = "unavailable"
    assert tracker.update(image, ocr=[], frame_id="failed") == ()
    diag = tracker.diagnostics
    assert diag["available"] is False
    assert diag["frame_id"] == "failed"
    assert str(diag["reason"]).startswith("provider_unavailable")
    assert diag["raw"] == {"gui": None, "world": None}
    assert diag["accepted"] == {"gui": None, "world": None}
    assert diag["rejected"] == {"gui": None, "world": None, "fusion": None}
    assert diag["merged"] is None
    assert diag["tracks"] is None
    assert diag["sources"] is None
    assert diag["split"] == {"added": None, "dropped": None}
