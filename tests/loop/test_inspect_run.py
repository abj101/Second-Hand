from __future__ import annotations

from pathlib import Path

import pytest

from loop.config import project_root
from loop.eval.export import inspect_run, is_noncanonical_snapshot_id, perception_hygiene_from_steps
from loop.log.schema import Observation, StepLog, StepResult
from loop.log.store import EpisodeStore

RUN_B = project_root() / "runs" / "20260907T200835Z_sts2_collect"


def _step(
    *,
    step_id: int,
    cards: list | None = None,
    targets: list | None = None,
    router_reason: str = "",
    perception_source: str = "other",
) -> StepLog:
    return StepLog(
        session_id="s",
        episode_id="e",
        step_id=step_id,
        ts="2026-09-07T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="obs.jpg"),
        result=StepResult(pixel_changed=True),
        metrics={"screen": "combat", "hp": 68},
        turn_snapshot={"cards": cards or [], "targets": targets or []},
        router_reason=router_reason,
        perception_source=perception_source,  # type: ignore[arg-type]
    )


def test_is_noncanonical_snapshot_id() -> None:
    assert is_noncanonical_snapshot_id("object:13")
    assert is_noncanonical_snapshot_id("mark_9")
    assert is_noncanonical_snapshot_id("mk_4")
    assert is_noncanonical_snapshot_id("unknown_character_1")
    assert not is_noncanonical_snapshot_id("card_1")
    assert not is_noncanonical_snapshot_id("enemy_1")


def test_perception_hygiene_synthetic_fields() -> None:
    overlap = {"x0": 0.10, "y0": 0.10, "x1": 0.50, "y1": 0.50}
    steps = [
        _step(
            step_id=1,
            cards=[{"card_id": "card_1"}],
            targets=[
                {"target_id": "object:10", "bbox": overlap, "identity_confidence": 2.5},
                {"target_id": "object:11", "bbox": overlap, "identity_confidence": 0.4},
            ],
            router_reason="epistemic-blocker:unknown_character_1",
            perception_source="forced_rescan",
        ),
        _step(
            step_id=2,
            cards=[{"card_id": "mk_2"}],
            targets=[{"target_id": "enemy_1", "bbox": [0.7, 0.1, 0.9, 0.3], "identity_confidence": 0.2}],
            router_reason="confirmed",
            perception_source="llm1",
        ),
    ]
    report = perception_hygiene_from_steps(steps)
    assert report["noncanonical_target_ids"] == 3
    assert report["duplicate_target_bboxes"] == 1
    assert report["confidence_out_of_range"] == 1
    assert report["router_reason"]["epistemic-blocker:unknown_character_1"] == 1
    assert report["router_reason"]["confirmed"] == 1
    assert report["perception_source"]["forced_rescan"] == 1
    assert report["perception_source"]["llm1"] == 1


def test_inspect_run_includes_hygiene_fields(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    store.write_step(
        _step(
            step_id=1,
            cards=[{"card_id": "card_1"}],
            targets=[{"target_id": "object:7", "identity_confidence": 108.5}],
            router_reason="epistemic-blocker:unknown_character_2",
            perception_source="scene_cache",
        )
    )
    report = inspect_run([tmp_path])
    assert report["noncanonical_target_ids"] == 1
    assert report["duplicate_target_bboxes"] == 0
    assert report["confidence_out_of_range"] == 1
    assert report["router_reason"]["epistemic-blocker:unknown_character_2"] == 1
    assert report["perception_source"]["scene_cache"] == 1


@pytest.mark.skipif(not (RUN_B / "turns.jsonl").exists(), reason="Run B missing")
def test_inspect_run_b_pre_fix_numbers() -> None:
    report = inspect_run([RUN_B])
    assert report["noncanonical_target_ids"] == 69
    assert report["confidence_out_of_range"] == 11
    assert report["duplicate_target_bboxes"] == 0
    assert report["router_reason"]["epistemic-blocker:unknown_character_1"] == 5
    assert report["router_reason"]["epistemic-blocker:unknown_character_2"] == 23
    assert report["perception_source"]["llm1"] == 1
    assert report["perception_source"]["forced_rescan"] == 25
    assert report["perception_source"]["scene_cache"] == 2
