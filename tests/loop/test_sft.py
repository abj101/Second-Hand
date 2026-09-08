from __future__ import annotations

import json
from pathlib import Path

from loop.agent.prompts import prompt_id, system_for
from loop.eval.sft import (
    chord_json,
    sandbox_sft_dataset,
    sft_chat_row,
    train_sft,
    write_sft_chat_jsonl,
)
from loop.log.schema import ActionLog, EpisodeLog, Observation, StepLog, StepResult
from loop.log.store import EpisodeStore


def _write_verified(tmp_path: Path) -> Path:
    store = EpisodeStore(tmp_path)
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="ok",
            step_id=1,
            ts="2026-08-25T00:00:00+00:00",
            fixture="elden",
            observation=Observation(screenshot_ref="obs.jpg"),
            proposed=ActionLog(stick_r="e", duration_ms=400),
            action=ActionLog(stick_r="e", duration_ms=400),
            result=StepResult(view_changed=True, executed_as_proposed=True),
            terminal="timeout",
            prompt_source="cv",
        )
    )
    store.write_episode(
        EpisodeLog(
            session_id="s",
            episode_id="ok",
            fixture="elden",
            steps=1,
            terminal="timeout",
        )
    )
    (tmp_path / "obs.jpg").write_bytes(b"\xff\xd8\xff")
    return tmp_path


def test_sft_chat_row_is_unsloth_messages() -> None:
    row = {
        "obs": "obs.jpg",
        "chord": {"stick_r": "e", "duration_ms": 400},
        "outcome": {"view_changed": True},
    }
    chat = sft_chat_row(row, run_dir=Path("runs/x"))
    roles = [m["role"] for m in chat["messages"]]
    assert roles == ["system", "user", "assistant"]
    user = chat["messages"][1]["content"]
    assert any(part.get("type") == "image" for part in user)
    assistant = json.loads(chat["messages"][2]["content"])
    assert assistant["stick_r"] == "e"
    assert "stick_l" in assistant


def test_sandbox_sft_rejects_empty_and_unverified() -> None:
    ok, _ = sandbox_sft_dataset([])
    assert ok is False
    bad = [{"obs": "x.jpg", "chord": {}, "outcome": {"view_changed": True}}]
    ok, reason = sandbox_sft_dataset(bad)
    assert ok is False
    assert "empty chord" in reason
    good = [
        {
            "obs": "x.jpg",
            "chord": {"stick_r": "e", "duration_ms": 400},
            "outcome": {"view_changed": True, "prompt_cleared": False},
        }
    ]
    ok, reason = sandbox_sft_dataset(good)
    assert ok, reason
    unknown = [
        {
            "fixture": "sts2",
            "prompt_id": "deadbeefdeadbeef",
            "obs": "x.jpg",
            "chord": {"mark": 1, "click_x": 0.5, "click_y": 0.7},
            "outcome": {"turn_reward": 0.3},
        }
    ]
    ok, reason = sandbox_sft_dataset(unknown)
    assert ok is False
    assert "unknown prompt_id" in reason


def test_train_sft_dry_run_writes_chat(tmp_path: Path) -> None:
    run = _write_verified(tmp_path)
    report = train_sft(run, execute=False)
    assert report["ok"] is True
    assert report["executed"] is False
    assert report["n"] == 1
    assert report["ollama_unloaded"] is True
    chat = Path(report["chat"])
    assert chat.exists()
    line = json.loads(chat.read_text(encoding="utf-8").splitlines()[0])
    assert line["messages"][2]["content"]
    assert write_sft_chat_jsonl(run).exists()
    parsed = json.loads(chord_json({"stick_l": "n", "tapped": [], "held": []}))
    assert parsed["stick_l"] == "n"
    turn = json.loads(chord_json({"mark": 3, "click_x": 0.4, "click_y": 0.85, "target": 7}, turn=True))
    assert turn["mark"] == 3
    assert turn["target"] == 7
    assert turn.get("stick_l") is None


def test_sft_chat_row_matches_prompt_id_prior() -> None:
    dump_id = prompt_id(arm="dump_all", pace="turn")
    row = {
        "fixture": "sts2",
        "obs": "obs.jpg",
        "prompt_id": dump_id,
        "chord": {"mark": 1, "click_x": 0.5, "click_y": 0.7},
        "outcome": {"turn_reward": 0.3},
    }
    chat = sft_chat_row(row, run_dir=Path("runs/x"))
    assert chat["messages"][0]["content"] == system_for("dump_all", pace="turn").strip()
    missing = dict(row)
    missing.pop("prompt_id")
    default = sft_chat_row(missing, run_dir=Path("runs/x"))
    assert default["messages"][0]["content"] == system_for("no_skill", pace="turn").strip()


def test_train_sft_refuses_mixed_prompt_id(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    for i, hid in enumerate(("aaa", "bbb"), start=1):
        store.write_step(
            StepLog(
                session_id="s",
                episode_id="run",
                step_id=i,
                ts="2026-08-26T00:00:00+00:00",
                fixture="sts2",
                observation=Observation(screenshot_ref=f"obs{i}.jpg", vlm_screenshot_ref=f"vlm{i}.jpg"),
                proposed=ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400),
                action=ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400),
                result=StepResult(pixel_changed=True, executed_as_proposed=True),
                metrics={"screen": "map", "choice": "Continue", "floor": 1},
                terminal="alive",
                prompt_id=hid,
            )
        )
    report = train_sft(tmp_path, execute=False)
    assert report["ok"] is False
    assert "mixed prompt_id" in str(report.get("reason") or "")
