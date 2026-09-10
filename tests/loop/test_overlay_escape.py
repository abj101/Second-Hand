from __future__ import annotations

import json

from PIL import Image, ImageDraw

from loop.agent.decision import ACTION_INTENT_SCHEMA
from loop.fixtures.base import FixtureBase
from loop.fixtures.sts2 import Sts2Fixture
from loop.os.overlay_escape import (
    OverlayEscapeState,
    overlay_looks_dimmed,
    pick_dismiss_control,
    pick_scrim_point,
    plan_overlay_escape,
)


def _panel_on_scrim() -> Image.Image:
    image = Image.new("RGB", (200, 200), (40, 40, 40))
    ImageDraw.Draw(image).rectangle((60, 56, 140, 144), fill=(210, 210, 210))
    return image


def _uniform() -> Image.Image:
    return Image.new("RGB", (200, 200), (80, 80, 80))


def test_dim_probe_center_vs_corners() -> None:
    assert overlay_looks_dimmed(_panel_on_scrim())
    assert not overlay_looks_dimmed(_uniform())
    assert not overlay_looks_dimmed(None)


def test_dismiss_picker_prefers_back_over_next_and_chevron() -> None:
    rows = [
        {"control_id": "control:next", "label": "Next", "role": "nav_next"},
        {"control_id": "control:arrow", "label": "Arrow", "role": "nav_prev"},
        {"control_id": "control:back", "label": "Back", "role": "button"},
    ]
    hit = pick_dismiss_control(rows)
    assert hit is not None
    assert hit["control_id"] == "control:back"
    without_back = pick_dismiss_control(rows[:2])
    assert without_back is not None
    assert without_back["control_id"] == "control:arrow"
    assert pick_dismiss_control(rows[:1]) is None


def test_scrim_point_outside_bboxes_and_refuses_repeat() -> None:
    image = _panel_on_scrim()
    left = (0.0, 0.0, 0.22, 1.0)
    point = pick_scrim_point(image, [left], tried=False)
    assert point is not None
    assert point[0] > 0.22
    assert pick_scrim_point(image, [left], tried=True) is None
    covered = [(0.0, 0.0, 1.0, 1.0)]
    assert pick_scrim_point(image, covered, tried=False) is None


def test_ladder_stops_after_three_rungs() -> None:
    state = OverlayEscapeState()
    state.arm(trigger="blocked-twice")
    image = _panel_on_scrim()
    controls = [{"control_id": "control:back", "label": "Back", "role": "button"}]
    first = plan_overlay_escape(state, controls=controls, image=image, dismiss_key="escape")
    assert first is not None and first.rung == 1 and first.kind == "click_control"
    state.last_rung = 1
    state.note_failure(0.0)
    second = plan_overlay_escape(state, controls=controls, image=image, dismiss_key="escape")
    assert second is not None and second.rung == 2 and second.kind == "scrim"
    state.last_rung = 2
    state.note_failure(0.0, used_scrim=True)
    third = plan_overlay_escape(state, controls=controls, image=image, dismiss_key="escape")
    assert third is not None and third.rung == 3 and third.key == "escape"
    state.last_rung = 3
    state.note_failure(0.0)
    assert not state.armed
    assert state.exhausted
    assert plan_overlay_escape(state, controls=controls, image=image, dismiss_key="escape") is None


def test_act_schema_has_no_dismiss_overlay() -> None:
    blob = json.dumps(ACTION_INTENT_SCHEMA)
    assert "dismiss_overlay" not in blob


def test_sts2_names_escape_and_base_does_not() -> None:
    assert Sts2Fixture().overlay_dismiss_key() == "escape"
    assert FixtureBase(id="fake", window_title="", device="mouse", action_table="lab").overlay_dismiss_key() is None
