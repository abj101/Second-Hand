from __future__ import annotations

from PIL import Image

from loop.agent.heuristic import HEURISTIC_POLICY_ID, heuristic_action
from loop.os.aim import AimGuide, AimMark


def _guide(*marks: AimMark) -> AimGuide:
    return AimGuide(marks=marks, last_xy=None, image=Image.new("RGB", (8, 8), (0, 0, 0)))


def test_heuristic_two_click_strike_cultist() -> None:
    guide = _guide(
        AimMark(id=3, x=0.40, y=0.85, x0=0.3, y0=0.82, x1=0.5, y1=0.90, label="Strike"),
        AimMark(id=7, x=0.55, y=0.30, x0=0.5, y0=0.25, x1=0.6, y1=0.35, label="Cultist"),
        AimMark(id=8, x=0.82, y=0.88, x0=0.78, y0=0.86, x1=0.90, y1=0.92, label="End Turn"),
    )
    action = heuristic_action(
        guide,
        metrics={"screen": "combat", "energy": 3},
        selected=None,
        recent=[],
        duration_ms=400,
    )
    assert action is not None
    assert action.mark == 3
    assert action.target == 7
    assert action.click_y2 is not None and action.click_y2 < 0.55


def test_heuristic_end_turn_at_zero_energy() -> None:
    guide = _guide(
        AimMark(id=3, x=0.40, y=0.85, x0=0.3, y0=0.82, x1=0.5, y1=0.90, label="Strike"),
        AimMark(id=8, x=0.82, y=0.88, x0=0.78, y0=0.86, x1=0.90, y1=0.92, label="End Turn"),
    )
    action = heuristic_action(
        guide,
        metrics={"screen": "combat", "energy": 0},
        selected=None,
        recent=[],
        duration_ms=400,
    )
    assert action is not None
    assert action.mark == 8
    assert action.target is None


def test_heuristic_menu_prefers_continue_over_quit() -> None:
    guide = _guide(
        AimMark(id=1, x=0.40, y=0.87, x0=0.3, y0=0.85, x1=0.5, y1=0.89, label="Quit"),
        AimMark(id=2, x=0.40, y=0.68, x0=0.3, y0=0.66, x1=0.5, y1=0.70, label="Continue"),
    )
    action = heuristic_action(
        guide,
        metrics={"screen": "title"},
        selected=None,
        recent=[],
        duration_ms=400,
    )
    assert action is not None
    assert action.mark == 2
    assert HEURISTIC_POLICY_ID.startswith("sts2-heuristic")


def test_heuristic_character_select_clicks_confirm_not_locks() -> None:
    guide = _guide(
        AimMark(id=3, x=0.94, y=0.88, x0=0.91, y0=0.85, x1=0.98, y1=0.96, label="Confirm"),
        AimMark(id=4, x=0.53, y=0.79, x0=0.50, y0=0.76, x1=0.56, y1=0.82, label=""),
        AimMark(id=16, x=0.32, y=0.38, x0=0.18, y0=0.34, x1=0.45, y1=0.42, label="Ironclad"),
    )
    action = heuristic_action(
        guide,
        metrics={"screen": "select"},
        selected=None,
        recent=[],
        duration_ms=400,
    )
    assert action is not None
    assert action.mark == 3
    assert action.target is None
