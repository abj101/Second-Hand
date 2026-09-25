"""Eligible offer extraction, projection, and steer signature stability (task D)."""

from __future__ import annotations

import pytest

from loop.adaptive.candidates import compile_transient_one_click_plan
from loop.adaptive.orchestrate import (
    MAX_OFFER_AREA_FRACTION,
    offer_decisions,
    registry_offers,
)
from loop.adaptive.orchestrator import maybe_screen_steer_wake
from loop.adaptive.perceive import SceneAssembler
from loop.adaptive.runtime import project, project_registry_offers
from loop.adaptive.schema import SceneGraph, SceneNode, empty_schema

from tests.loop.test_control_registry_identity import (
    EXPECTED_CONTROLS,
    LOGO_BBOX,
    _build_image,
    _menu_frame_tokens,
    _ocr_from_tokens,
    _run_frames,
    _shift_bbox,
    frames_duplicate_labels,
)

_MENU_FIVE = frozenset({"continue", "settings", "statistics", "credits", "quit"})


def _five_menu_logo_noise_frames(count: int = 8):
    for i in range(count):
        img = _build_image(
            include=set(_MENU_FIVE),
            logo_text=f"LogoNoise{i}",
            logo_color=((30 + i * 5) % 200, (50 + i * 3) % 200, (70 + i * 7) % 200),
        )
        tokens: list[tuple[str, tuple[float, float, float, float]]] = []
        for key in _MENU_FIVE:
            text, bbox, _color = EXPECTED_CONTROLS[key]
            tokens.append((text, bbox))
        tokens.append((f"LogoNoise{i}", LOGO_BBOX))
        yield img, list(tokens)


def _assemble(frames) -> list[SceneGraph]:
    return _run_frames(SceneAssembler(), frames)


def test_five_menu_offers_stable_under_logo_animation():
    scenes = _assemble(_five_menu_logo_noise_frames(12))
    first_ids = {o.offer_id for o in registry_offers(scenes[0])}
    assert len(first_ids) == 5
    last = scenes[-1]
    logo_nodes = [
        n
        for n in last.nodes
        if str(n.attributes.get("label", "")).startswith("LogoNoise")
    ]
    assert logo_nodes
    decisions_by_node = {d.node_id: d for d in offer_decisions(last)}
    for node in logo_nodes:
        if node.attributes.get("registry_id"):
            dec = decisions_by_node[node.node_id]
            assert dec.status == "rejected"
            assert dec.reason == "oversized"
    assert not any(o.label.startswith("LogoNoise") for o in registry_offers(last))
    # Logo-sized registry binding is rejected even when injected on the scene graph.
    injected = SceneGraph(
        last.snapshot_id,
        last.nodes
        + (
            SceneNode(
                "logo-offer-candidate",
                LOGO_BBOX,
                (),
                {
                    "registry_id": "logo-injected",
                    "label": "LogoNoise",
                    "registry_owner": "ocr",
                },
            ),
        ),
    )
    inj = next(d for d in offer_decisions(injected) if d.candidate_id == "logo-injected")
    assert inj.reason == "oversized"
    assert inj.status == "rejected"
    for scene in scenes[1:]:
        assert {o.offer_id for o in registry_offers(scene)} == first_ids
        assert len(registry_offers(scene)) == 5

    prev_sig: frozenset[str] | None = None
    prev_unkeyed: int | None = None
    wake_count = 0
    for scene in scenes:
        prev_sig, prev_unkeyed, ev = maybe_screen_steer_wake(
            scene,
            previous_signature=prev_sig,
            previous_unkeyed=prev_unkeyed,
        )
        if ev is not None:
            wake_count += 1
    assert wake_count == 1


def test_semantic_required_withholds_decoration_and_unknown():
    graph = SceneGraph(
        "sem",
        nodes=(
            SceneNode(
                "n-control",
                EXPECTED_CONTROLS["continue"][1],
                (),
                {
                    "registry_id": "rid-control",
                    "label": "Continue",
                    "registry_owner": "ocr",
                    "semantic_status": "confirmed",
                    "semantic_class": "control",
                },
            ),
            SceneNode(
                "n-deco",
                EXPECTED_CONTROLS["settings"][1],
                (),
                {
                    "registry_id": "rid-deco",
                    "label": "Settings",
                    "registry_owner": "ocr",
                    "semantic_status": "confirmed",
                    "semantic_class": "decoration",
                },
            ),
            SceneNode(
                "n-unknown",
                EXPECTED_CONTROLS["statistics"][1],
                (),
                {
                    "registry_id": "rid-unknown",
                    "label": "Statistics",
                    "registry_owner": "ocr",
                    "semantic_status": "confirmed",
                    "semantic_class": "unknown",
                },
            ),
        ),
    )
    offers = registry_offers(graph, semantic="required")
    assert [o.offer_id for o in offers] == ["rid-control"]
    decisions = {d.candidate_id: d for d in offer_decisions(graph, semantic="required")}
    assert decisions["rid-deco"].status == "withheld"
    assert decisions["rid-deco"].reason == "semantic_decoration"
    assert decisions["rid-unknown"].status == "withheld"
    assert decisions["rid-unknown"].reason == "semantic_unknown"


def test_two_custom_labels_two_distinct_offers():
    assembler = SceneAssembler()
    image, ocr = next(iter(frames_duplicate_labels()))
    _recs, scene = assembler.update(image, ocr=ocr)
    custom_offers = [o for o in registry_offers(scene) if o.label == "Custom"]
    assert len(custom_offers) == 2
    assert custom_offers[0].offer_id != custom_offers[1].offer_id


def test_forbidden_id_withheld():
    scenes = _assemble(_five_menu_logo_noise_frames(1))
    scene = scenes[0]
    target = registry_offers(scene)[0]
    blocked = registry_offers(scene, forbidden=frozenset({target.offer_id}))
    assert target.offer_id not in {o.offer_id for o in blocked}
    decision = next(d for d in offer_decisions(scene, forbidden={target.offer_id}) if d.candidate_id == target.offer_id)
    assert decision.status == "withheld"
    assert decision.reason == "forbidden"


def test_remove_then_return_same_offer_id():
    def _five_remove_return():
        img_full, _ = _menu_frame_tokens(include_quit=True)
        tokens_full = [
            (EXPECTED_CONTROLS[k][0], EXPECTED_CONTROLS[k][1])
            for k in _MENU_FIVE
        ]
        yield img_full, _ocr_from_tokens(tokens_full)
        img_no, _ = _menu_frame_tokens(include_quit=False)
        tokens_no = [
            (EXPECTED_CONTROLS[k][0], EXPECTED_CONTROLS[k][1])
            for k in _MENU_FIVE - {"quit"}
        ]
        for _ in range(3):
            yield img_no.copy(), _ocr_from_tokens(tokens_no)
        yield img_full.copy(), _ocr_from_tokens(tokens_full)

    scenes = _assemble(_five_remove_return())
    quit_first = next(o for o in registry_offers(scenes[0]) if o.label == "Quit")
    for scene in scenes[1:4]:
        assert "Quit" not in {o.label for o in registry_offers(scene)}
    quit_back = next(o for o in registry_offers(scenes[4]) if o.label == "Quit")
    assert quit_back.offer_id == quit_first.offer_id


def test_oversized_registry_node_rejected():
    graph = SceneGraph(
        "big",
        nodes=(
            SceneNode(
                "screen-fill",
                (0.0, 0.0, 1.0, 0.5),
                (),
                {"registry_id": "fill", "label": "Backdrop", "registry_owner": "ocr"},
            ),
        ),
    )
    assert registry_offers(graph) == ()
    decision = offer_decisions(graph)[0]
    assert decision.status == "rejected"
    assert decision.reason == "oversized"
    assert MAX_OFFER_AREA_FRACTION == 0.25


def test_empty_ocr_yields_no_offers_or_plan():
    assembler = SceneAssembler()
    img = _build_image(include=set(_MENU_FIVE))
    _recs, scene = assembler.update(img, ocr=_ocr_from_tokens([]))
    assert registry_offers(scene) == ()
    snap = project(scene, empty_schema())
    assert snap.surface.action_ids == ()
    with pytest.raises(Exception):
        compile_transient_one_click_plan(
            type("O", (), {"offer_id": "", "label": "", "node_id": ""})()
        )


def test_one_offer_compiles_transient_plan_on_scene_node():
    scenes = _assemble(_five_menu_logo_noise_frames(1))
    scene = scenes[0]
    offers = registry_offers(scene)
    offer = offers[0]
    plan = compile_transient_one_click_plan(offer)
    assert plan.node_id == offer.node_id
    assert any(n.node_id == plan.node_id for n in scene.nodes)
    snap = project_registry_offers(scene, empty_schema(), offers)
    assert offer.offer_id in snap.surface.action_ids


def test_steer_signature_once_per_eligible_set_change_not_logo_pixels():
    scenes = _assemble(_five_menu_logo_noise_frames(6))
    prev_sig: frozenset[str] | None = None
    prev_unkeyed: int | None = None
    wakes: list[frozenset[str]] = []
    for scene in scenes:
        prev_sig, prev_unkeyed, ev = maybe_screen_steer_wake(
            scene,
            previous_signature=prev_sig,
            previous_unkeyed=prev_unkeyed,
        )
        if ev is not None:
            wakes.append(frozenset(ev.get("registry_signature") or ()))

    assert len(wakes) == 1

    # Drop one eligible control, then restore it — one additional wake.
    def _drop_settings():
        keys = set(_MENU_FIVE) - {"settings"}
        img = _build_image(include=keys)
        tokens = [(EXPECTED_CONTROLS[k][0], EXPECTED_CONTROLS[k][1]) for k in keys]
        yield img, _ocr_from_tokens(tokens)

    drop_scenes = _assemble(_drop_settings())
    prev_sig, prev_unkeyed, ev = maybe_screen_steer_wake(
        drop_scenes[0],
        previous_signature=prev_sig,
        previous_unkeyed=prev_unkeyed,
    )
    assert ev is not None
    assert "registry_signature_changed" in ev["wake_reason"]
