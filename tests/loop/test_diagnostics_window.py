import json
import os
import sys
from pathlib import Path

from loop.adaptive.cli import _diagnostics_executable
from loop.adaptive.diagnostics_window import JsonlTail, build_diagnostic_state


def test_jsonl_tail_only_reads_appended_rows(tmp_path):
    path = tmp_path / "shadow_react.jsonl"
    path.write_text(json.dumps({"frame_index": 1}) + "\n", encoding="utf-8")
    tail = JsonlTail(path)

    assert [row["frame_index"] for row in tail.poll()] == [1]

    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"frame_index": 2}) + "\n")

    assert [row["frame_index"] for row in tail.poll()] == [1, 2]


def test_diagnostics_launcher_uses_windowless_python_on_windows():
    executable = Path(_diagnostics_executable())
    pythonw = Path(sys.executable).with_name("pythonw.exe")

    if os.name == "nt" and pythonw.is_file():
        assert executable == pythonw
    else:
        assert executable == Path(sys.executable)


def test_diagnostic_state_keeps_last_completed_choice_during_pending_frame():
    rows = (
        {
            "frame_index": 7,
            "selected_action_id": "reg_fire",
            "mask_allowed_ids": ["reg_fire", "reg_dodge"],
            "candidate_scores": {"reg_fire": 0.72, "reg_dodge": 0.28},
            "candidate_labels": {"reg_fire": "Fire", "reg_dodge": "Dodge"},
            "decision_source": "QwenReactionPolicy",
            "dispatch_accepted": True,
            "dispatch_reason": "ok",
            "latency_policy_s": 0.12,
            "ocr_items": [{"text": "Score 42", "bbox": [0.1, 0.2, 0.3, 0.4]}],
            "directive_revision": 2,
            "directive_intent": "progress while surviving",
            "orchestrator_events": [{"kind": "screen_signature"}],
            "capture_integrity": "trusted",
            "focused": True,
        },
        {
            "frame_index": 8,
            "selected_action_id": None,
            "mask_allowed_ids": ["reg_fire", "reg_dodge"],
            "candidate_labels": {"reg_fire": "Fire", "reg_dodge": "Dodge"},
            "decision_source": "QwenReactionPolicy_pending",
            "dispatch_reason": "no_candidates",
            "ocr_items": [{"text": "Score 43", "bbox": [0.1, 0.2, 0.3, 0.4]}],
            "directive_revision": 2,
            "directive_intent": "progress while surviving",
            "capture_integrity": "trusted",
            "focused": True,
        },
    )

    state = build_diagnostic_state(rows)

    assert state is not None
    assert state.frame_index == 8
    assert state.selected_action_id == "reg_fire"
    assert state.selected_label == "Fire"
    assert state.choices[0].score == 0.72
    assert state.ocr_items[0]["text"] == "Score 43"
    assert {event["kind"] for event in state.orchestrator_events} == {
        "directive_published",
        "screen_signature",
    }
