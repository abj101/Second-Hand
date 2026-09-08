from __future__ import annotations

from loop.reward.audits import audit_episode, audit_step, explore_progress, hud_identity_stuck, score_delta


def test_fail_terminal_is_minus_one() -> None:
    assert score_delta({}, {"health_frac": 0.0}, "fail") == -1.0
    assert score_delta({}, {}, "win") == 1.0


def test_vitals_disagree_flag() -> None:
    flags = audit_step(
        metrics={"health_frac": 0.0, "fail_screen": False},
        pixel_changed=True,
        failed_while_thinking=True,
        action_name="start",
        menu_streak=4,
    )
    assert "fail_screen_disagrees_vitals" in flags
    assert "failed_while_thinking" in flags
    assert "long_menu_stretch" in flags


def test_explore_progress_shapes_between_zero_and_win() -> None:
    assert explore_progress() == 0.0
    assert explore_progress(view_changed=True) == 0.02
    assert explore_progress(new_cell=True, new_prompt=True) == 0.04


def test_hud_identity_stuck_on_frozen_screen() -> None:
    from loop.log.schema import Observation, StepLog, StepResult

    def _step(i: int, *, screen: str = "reward", hp: int = 80, pixels: bool = False) -> StepLog:
        return StepLog(
            session_id="s",
            episode_id="e",
            step_id=i,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a"),
            result=StepResult(pixel_changed=pixels),
            metrics={"screen": screen, "hp": hp, "floor": 1},
        )

    frozen = [_step(i, pixels=(i % 3 == 0)) for i in range(1, 25)]
    assert hud_identity_stuck(frozen, window=24) is True
    flags = audit_episode(steps=frozen, stuck_steps=8)
    assert "hud_identity_stuck" in flags
    moved = frozen[:-1] + [_step(24, screen="combat", hp=72, pixels=True)]
    assert hud_identity_stuck(moved, window=24) is False
    empty = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=i,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a"),
            result=StepResult(pixel_changed=False),
            metrics={},
        )
        for i in range(1, 25)
    ]
    assert hud_identity_stuck(empty, window=24) is False
    tutorial = [_step(i, screen="tutorial", hp=80, pixels=False) for i in range(1, 25)]
    assert hud_identity_stuck(tutorial, window=24) is False
    flicker = [
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=i,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="a"),
            result=StepResult(pixel_changed=True),
            metrics={},
        )
        for i in range(1, 25)
    ]
    assert hud_identity_stuck(flicker, window=24) is False
