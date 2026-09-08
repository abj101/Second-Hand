from __future__ import annotations

from types import SimpleNamespace

import pytest

from loop.gsm.approach import ApproachGsmRuntime, approach_target_cx
from loop.gsm.explore import ExploreGsmRuntime, SurveyGsmRuntime, sandbox_explore
from loop.gsm.rl import (
    EXPLORE_RL_ACTIONS,
    explore_rl_spec,
    sandbox_explore_reward,
    train_explore_dqn,
    transitions_from_steps,
)
from loop.log.schema import ActionLog, Observation, StepLog, StepResult
from loop.os.chords import chord_name, steer_explore_chord


def test_explore_gsm_yaws_on_stall_without_rewriting_look() -> None:
    runtime = ExploreGsmRuntime()
    yaw, done = runtime.next_action(stall=True, prompt_on=False)
    assert done is False
    assert yaw.stick_r == "e"
    assert yaw.stick_l is None
    assert yaw.name == "stick_r_e"
    look = ActionLog(stick_r="nw", duration_ms=400)
    look.name = chord_name(look)
    steered, why = steer_explore_chord(
        look,
        patches=[(0.6, 0.4)],
        thought="look at the courtyard",
        recent=["stick_l_n", "stick_l_n", "stick_l_n"],
        prompt_on=False,
        last_mad=0.002,
    )
    assert why is None
    assert steered.stick_r == "nw"
    assert steered.stick_l is None
    ok, reason = sandbox_explore()
    assert ok, reason
    ok_rl, reason_rl = sandbox_explore_reward()
    assert ok_rl, reason_rl
    spec = explore_rl_spec()
    assert spec.kind == "rl"
    assert spec.name == "explore"
    assert "stick_r_e" in spec.action_space


def test_explore_yaws_on_lock_prompt() -> None:
    runtime = ExploreGsmRuntime()
    idle, done = runtime.next_action(stall=True, prompt_on=True)
    assert done is True
    assert idle.name == "idle"
    yaw, yaw_done = ExploreGsmRuntime().next_action(stall=False, prompt_on=True, lock=True)
    assert yaw_done is False
    assert yaw.stick_r == "e"
    assert yaw.stick_l is None


def test_explore_walks_north_when_not_stalled() -> None:
    runtime = ExploreGsmRuntime()
    yaw, _ = runtime.next_action(stall=True, prompt_on=False)
    assert yaw.stick_r == "e"
    walk, done = runtime.next_action(stall=False, prompt_on=False)
    assert done is False
    assert walk.stick_l == "n"
    assert walk.stick_r is None


def _explore_step(
    eid: str,
    step_id: int,
    *,
    name: str,
    view_changed: bool = False,
    stall: bool = False,
    prompt_cleared: bool = False,
    prompt_source: str = "",
) -> StepLog:
    stick_r = name.removeprefix("stick_r_") if name.startswith("stick_r_") else None
    stick_l = name.removeprefix("stick_l_") if name.startswith("stick_l_") else None
    action = ActionLog(stick_l=stick_l, stick_r=stick_r, duration_ms=400)  # type: ignore[arg-type]
    action.name = chord_name(action)
    return StepLog(
        session_id="s",
        episode_id=eid,
        step_id=step_id,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="obs.jpg"),
        action=action,
        result=StepResult(
            view_changed=view_changed,
            stall=stall,
            prompt_cleared=prompt_cleared,
            invoked_gsm="explore",
        ),
        prompt_source=prompt_source,
        terminal="timeout",
    )


def test_train_explore_dqn_on_logged_pairs(tmp_path) -> None:
    steps = [
        _explore_step("e", 1, name="stick_r_e", stall=True),
        _explore_step("e", 2, name="stick_r_w", view_changed=True),
        _explore_step("e", 3, name="stick_l_n"),
        _explore_step("e", 4, name="stick_l_s", stall=True),
        _explore_step("e", 5, name="stick_r_e", view_changed=True),
        _explore_step("e", 6, name="stick_l_n"),
    ]
    trans = transitions_from_steps(steps)
    assert len(trans) >= 4
    assert all(0 <= a < len(EXPLORE_RL_ACTIONS) for _, a, _, _, _ in trans)
    dest = tmp_path / "explore_dqn.json"
    report = train_explore_dqn(steps, dest=dest, train_steps=16, seed=0)
    assert report["ok"], report["reason"]
    assert report["sandbox_ok"] is True
    assert dest.exists()
    assert report["n_transitions"] == len(trans)


def test_approach_centers_left_right_and_forward() -> None:
    runtime = ApproachGsmRuntime()
    left, done = runtime.next_action(box_cx=0.2, confirmed=False, locked=False)
    assert done is False
    assert left.stick_r == "w"
    assert left.stick_l is None
    assert left.name == "stick_r_w"
    runtime.reset()
    right, done = runtime.next_action(box_cx=0.8, confirmed=False, locked=False)
    assert done is False
    assert right.stick_r == "e"
    assert right.stick_l is None
    assert right.name == "stick_r_e"
    runtime.reset()
    walk, done = runtime.next_action(box_cx=0.5, confirmed=False, locked=False)
    assert done is False
    assert walk.stick_l == "n"
    assert walk.stick_r is None
    assert walk.name == "stick_l_n"


def test_approach_idles_when_confirmed_locked_or_missing() -> None:
    runtime = ApproachGsmRuntime()
    confirmed, done = runtime.next_action(box_cx=0.2, confirmed=True, locked=False)
    assert done is True
    assert confirmed.name == "idle"
    assert runtime.ticks == 0
    locked, done = runtime.next_action(box_cx=0.2, confirmed=False, locked=True)
    assert done is True
    assert locked.name == "idle"
    assert runtime.ticks == 0
    missing, done = runtime.next_action(box_cx=None, confirmed=False, locked=False)
    assert done is True
    assert missing.name == "idle"
    assert runtime.ticks == 0


def test_approach_done_after_max_ticks() -> None:
    runtime = ApproachGsmRuntime()
    last = None
    for _ in range(ApproachGsmRuntime.MAX_TICKS):
        last, done = runtime.next_action(box_cx=0.2, confirmed=False, locked=False)
    assert last is not None
    assert done is True
    assert last.name == "stick_r_w"
    assert last.stick_r == "w"
    assert runtime.ticks == ApproachGsmRuntime.MAX_TICKS


def test_approach_target_cx_prefers_doorway() -> None:
    boxes = [
        SimpleNamespace(label="wall", x=0.0, w=0.2),
        SimpleNamespace(label="Doorway glow", x=0.4, w=0.2),
    ]
    assert approach_target_cx(boxes) == 0.5
    assert approach_target_cx([]) is None
    assert approach_target_cx([SimpleNamespace(label="rock", x=0.1, w=0.2)]) == 0.2


def test_approach_target_cx_prefers_loot_trail() -> None:
    boxes = [
        SimpleNamespace(label="golden trail", x=0.2, w=0.2),
        SimpleNamespace(label="doorway", x=0.6, w=0.2),
    ]
    assert approach_target_cx(boxes) == pytest.approx(0.3)


def test_survey_emits_four_looks_then_done() -> None:
    runtime = SurveyGsmRuntime()
    dirs: list[str | None] = []
    dones: list[bool] = []
    for _ in range(4):
        action, done = runtime.next_action()
        dirs.append(action.stick_r)
        dones.append(done)
        assert action.stick_l is None
        assert action.duration_ms == 400
        assert action.name == f"stick_r_{action.stick_r}"
    assert dirs == ["e", "e", "w", "w"]
    assert dones == [False, False, False, True]
    runtime.reset()
    assert runtime.ticks == 0
