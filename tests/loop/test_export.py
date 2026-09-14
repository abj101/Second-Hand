from __future__ import annotations

from pathlib import Path

from loop.eval.export import compare_turn_runs, export_sft_rows
from loop.log.schema import ActionLog, EpisodeLog, Observation, StepLog, StepResult
from loop.log.store import EpisodeStore


def _v2_step(*, intent: dict, prompt_id: str = "sts2-action-intent-v2", step_id: int = 1) -> StepLog:
    return StepLog(
        session_id="s",
        episode_id="e",
        step_id=step_id,
        ts="2026-09-05T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="secret.jpg"),
        result=StepResult(pixel_changed=True, executed_as_proposed=True),
        metrics={"screen": "combat", "hp": 50, "energy": 2, "pixel_changed": True},
        decision_schema="v2",
        snapshot_id="snap-1",
        intent=intent,
        prompt_id=prompt_id,
        execution_plan={"source": {"x": 0.5, "y": 0.8}, "snapshot": {"cards": []}},
    )


def test_mixed_decision_schema_is_not_exported(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    store.write_step(_v2_step(intent={"kind": "end_turn", "control_id": "end_turn"}))
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="e",
            step_id=2,
            ts="t",
            fixture="sts2",
            observation=Observation(screenshot_ref="old.jpg"),
            proposed=ActionLog(mark=1, click_x=0.5, click_y=0.5),
            action=ActionLog(mark=1, click_x=0.5, click_y=0.5),
            result=StepResult(pixel_changed=True, executed_as_proposed=True),
        )
    )
    assert export_sft_rows(tmp_path) == []


def test_mixed_prompt_id_is_not_exported(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    store.write_step(_v2_step(intent={"kind": "end_turn", "control_id": "end_turn"}))
    store.write_step(
        _v2_step(
            intent={"kind": "end_turn", "control_id": "end_turn"},
            prompt_id="sts2-action-intent-v3-knowledge",
            step_id=2,
        )
    )
    assert export_sft_rows(tmp_path) == []


def test_compare_turn_runs_refuses_mixed_prompt_between(tmp_path: Path) -> None:
    def write(path: Path, hid: str) -> None:
        store = EpisodeStore(path)
        store.write_step(
            StepLog(
                session_id="s",
                episode_id="run",
                step_id=1,
                ts="t",
                fixture="sts2",
                observation=Observation(screenshot_ref="obs.jpg"),
                proposed=ActionLog(mark=1, click_x=0.5, click_y=0.7),
                action=ActionLog(mark=1, click_x=0.5, click_y=0.7),
                result=StepResult(pixel_changed=True, executed_as_proposed=True),
                metrics={"screen": "map", "floor": 1.0},
                prompt_id=hid,
            )
        )
        store.write_episode(EpisodeLog(session_id="s", episode_id="run", fixture="sts2", steps=1))

    write(tmp_path / "baseline", "aaa")
    write(tmp_path / "candidate", "bbb")
    report = compare_turn_runs(tmp_path / "baseline", tmp_path / "candidate")
    assert report["ok"] is False
    assert "prompt_id" in str(report.get("reason") or "").lower()
