from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from loop.log.schema import ActionLog
from loop.os.affordance import detect_affordance_diff, merge_legal_actions, validate_legal
from loop.os.aim import AimMark

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "affordance"


def _mark(mid: int) -> AimMark:
    return AimMark(
        id=mid,
        x=0.5,
        y=0.5,
        x0=0.25,
        y0=0.25,
        x1=0.75,
        y1=0.75,
        label="target",
        score=0.9,
    )


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures() -> None:
    if not (FIXTURE_DIR / "highlight_target_pre.png").exists():
        import runpy

        runpy.run_path(str(FIXTURE_DIR / "generate.py"), run_name="__main__")


@pytest.mark.parametrize(
    ("stem", "expected_delta"),
    [
        ("highlight_target", "highlighted"),
        ("dim_hand_card", "dimmed"),
        ("unchanged_ui", "unchanged"),
    ],
)
def test_frozen_fixture_pairs(stem: str, expected_delta: str) -> None:
    pre = Image.open(FIXTURE_DIR / f"{stem}_pre.png")
    post = Image.open(FIXTURE_DIR / f"{stem}_post.png")
    verdicts = detect_affordance_diff(pre, post, [_mark(1)])
    assert verdicts["mk_1"].delta == expected_delta


def test_unrelated_frame_pair_cannot_make_all_typed_cards_illegal() -> None:
    """Generic frame deltas stay telemetry when current typed state is present."""
    pre = Image.open(FIXTURE_DIR / "highlight_target_pre.png")
    post = Image.open(FIXTURE_DIR / "dim_hand_card_post.png")
    marks = [_mark(1), _mark(2), _mark(3)]
    cv = detect_affordance_diff(pre, post, marks)
    parsed = [
        {"mark": f"mk_{mid}", "verdict": "legal", "confidence": 0.9}
        for mid in (1, 2, 3)
    ]

    merged = merge_legal_actions(parsed, cv)

    assert [row["verdict"] for row in merged] == ["legal", "legal", "legal"]
    assert all(row["cv_scope"] == "unscoped" for row in merged)


def test_fixture_dimming_does_not_reject_typed_combat_action() -> None:
    pre = Image.open(FIXTURE_DIR / "highlight_target_pre.png")
    post = Image.open(FIXTURE_DIR / "dim_hand_card_post.png")
    cv = detect_affordance_diff(pre, post, [_mark(7)])
    action = ActionLog(
        mark=7,
        click_x=0.5,
        click_y=0.5,
        source_role="card",
        source_object_id="card_7",
    )

    _, reason = validate_legal(action, merge_legal_actions([], cv))

    assert cv["mk_7"].confidence >= 0.65
    assert reason is None
