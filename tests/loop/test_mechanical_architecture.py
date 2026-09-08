from __future__ import annotations

from loop.__main__ import _resolve_architecture
from loop.log.schema import Observation, StepLog


def test_sts2_defaults_to_two_stage_and_overlay_is_explicit() -> None:
    assert _resolve_architecture("sts2", None) == "two_stage"
    assert _resolve_architecture("sts2", "overlay") == "overlay"
    assert _resolve_architecture("fake", None) == "overlay"


def test_step_log_dual_agent_fields_are_backward_compatible() -> None:
    record = StepLog(
        session_id="s",
        episode_id="e",
        step_id=1,
        ts="now",
        fixture="sts2",
        observation=Observation(screenshot_ref="frame.jpg"),
    )
    assert record.perception_schema_version == ""
    assert record.reasoner_role == ""
    assert record.router_reason == ""
    assert record.epistemic_blockers == []

    restored = StepLog.model_validate(record.model_dump())
    assert restored.model_dump() == record.model_dump()
