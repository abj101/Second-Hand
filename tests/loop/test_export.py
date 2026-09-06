from __future__ import annotations

import json
from pathlib import Path

from loop.eval.export import calibrate_view, export_sft_rows, write_sft_jsonl
from loop.log.schema import ActionLog, EpisodeLog, Observation, StepLog, StepResult
from loop.log.store import EpisodeStore


def _v2_step(*, intent: dict, snapshot_id: str = "snap-1", prompt_id: str = "sts2-action-intent-v2") -> StepLog:
    return StepLog(
        session_id="s",
        episode_id="e",
        step_id=1,
        ts="2026-09-05T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="secret.jpg", vlm_screenshot_ref="secret-vlm.jpg"),
        result=StepResult(pixel_changed=True, executed_as_proposed=True),
        metrics={"screen": "combat", "hp": 50, "energy": 2, "pixel_changed": True},
        decision_schema="v2",
        snapshot_id=snapshot_id,
        intent=intent,
        prompt_id=prompt_id,
        execution_plan={"source": {"x": 0.5, "y": 0.8}, "snapshot": {"cards": []}},
    )


def test_v2_sft_is_text_only_and_never_exports_legacy_grounding(tmp_path: Path) -> None:
    from loop.eval.sft import sft_chat_row

    step = _v2_step(intent={"kind": "play_card", "card_id": "card_1", "target_id": "enemy_1"})
    EpisodeStore(tmp_path).write_step(step)
    rows = export_sft_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["decision_schema"] == "v2"
    assert row["intent"]["kind"] == "play_card"
    assert not {"obs", "chord", "mark", "gx", "gy", "click_x", "click_y"}.intersection(row)
    chat = sft_chat_row(row, run_dir=tmp_path)
    assert all(isinstance(message["content"], str) for message in chat["messages"])
    assert "secret.jpg" not in json.dumps(chat)
    assert "x" not in json.loads(chat["messages"][1]["content"]).get("snapshot", {})


def test_v2_and_v1_rows_are_not_exported_together(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    store.write_step(_v2_step(intent={"kind": "end_turn", "control_id": "end_turn"}))
    store.write_step(
        StepLog(
            session_id="s", episode_id="e", step_id=2, ts="t", fixture="sts2",
            observation=Observation(screenshot_ref="old.jpg"),
            proposed=ActionLog(mark=1, click_x=0.5, click_y=0.5),
            action=ActionLog(mark=1, click_x=0.5, click_y=0.5),
            result=StepResult(pixel_changed=True, executed_as_proposed=True),
        )
    )
    assert export_sft_rows(tmp_path) == []


def test_v2_dpo_rejects_mixed_schema_or_prompt_pair(tmp_path: Path) -> None:
    from loop.eval.dpo import dpo_chat_row, sandbox_dpo_pairs

    mixed = {
        "decision_schema": "v2",
        "chosen": {"kind": "end_turn", "control_id": "end_turn", "decision_schema": "v2"},
        "rejected": {"mark": 1, "decision_schema": "v1"},
        "chosen_reward": 1,
        "rejected_reward": 0,
    }
    assert sandbox_dpo_pairs([mixed])[0] is False
    mismatched = {
        "decision_schema": "v2",
        "prompt_id": "v2-a",
        "chosen_prompt_id": "v2-a",
        "rejected_prompt_id": "v2-b",
        "chosen": {"kind": "end_turn", "control_id": "end_turn"},
        "rejected": {"kind": "click_control", "control_id": "x"},
        "chosen_reward": 1,
        "rejected_reward": 0,
    }
    assert sandbox_dpo_pairs([mismatched])[0] is False
    try:
        dpo_chat_row(mixed, run_dir=tmp_path)
    except ValueError:
        pass

    valid = {
        "decision_schema": "v2",
        "prompt_id": "sts2-action-intent-v2",
        "snapshot": {"screen": "combat", "cards": [{"card_id": "card_1", "bbox": {"x": 1}}]},
        "chosen": {"kind": "play_card", "card_id": "card_1", "target_id": "enemy_1"},
        "rejected": {"kind": "end_turn", "control_id": "end_turn"},
        "chosen_reward": 1,
        "rejected_reward": 0,
        "same_frame": True,
    }
    chat = dpo_chat_row(valid, run_dir=tmp_path)
    assert all(isinstance(message["content"], str) for message in chat["prompt"])
    assert "bbox" not in json.dumps(chat)
    assert "image" not in json.dumps(chat)


def test_export_sft_keeps_verified_proposed_only(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    good = StepLog(
        session_id="s",
        episode_id="e",
        step_id=1,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="obs.jpg"),
        proposed=ActionLog(stick_r="e", duration_ms=400),
        action=ActionLog(stick_r="e", duration_ms=400),
        result=StepResult(view_changed=True, executed_as_proposed=True),
    )
    rewritten = StepLog(
        session_id="s",
        episode_id="e",
        step_id=2,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="obs2.jpg"),
        proposed=ActionLog(stick_r="e", duration_ms=400),
        action=ActionLog(stick_l="n", duration_ms=400),
        result=StepResult(view_changed=False, executed_as_proposed=False, pixel_changed=True),
    )
    store.write_step(good)
    store.write_step(rewritten)
    rows = export_sft_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["chord"]["stick_r"] == "e"
    dest = write_sft_jsonl(tmp_path)
    assert dest.exists()
    report = calibrate_view([tmp_path])
    assert report["look_only"] == 0
    assert "Do not SFT" in report["note"]


def test_export_sft_drops_fail_and_dino_only_prompt(tmp_path: Path) -> None:
    store = EpisodeStore(tmp_path)
    look = StepLog(
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
    dino_clear = StepLog(
        session_id="s",
        episode_id="ok",
        step_id=2,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="obs2.jpg"),
        proposed=ActionLog(tapped=["a"], duration_ms=400),
        action=ActionLog(tapped=["a"], duration_ms=400),
        result=StepResult(prompt_cleared=True, executed_as_proposed=True),
        terminal="timeout",
        prompt_source="dino",
    )
    cv_clear = StepLog(
        session_id="s",
        episode_id="ok",
        step_id=3,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="obs3.jpg"),
        proposed=ActionLog(tapped=["a"], duration_ms=400),
        action=ActionLog(tapped=["a"], duration_ms=400),
        result=StepResult(prompt_cleared=True, executed_as_proposed=True),
        terminal="timeout",
        prompt_source="cv",
    )
    fail_look = StepLog(
        session_id="s",
        episode_id="dead",
        step_id=1,
        ts="2026-08-25T00:00:00+00:00",
        fixture="elden",
        observation=Observation(screenshot_ref="fail.jpg"),
        proposed=ActionLog(stick_r="w", duration_ms=400),
        action=ActionLog(stick_r="w", duration_ms=400),
        result=StepResult(view_changed=True, executed_as_proposed=True),
        terminal="fail",
        prompt_source="cv",
    )
    store.write_step(look)
    store.write_step(dino_clear)
    store.write_step(cv_clear)
    store.write_step(fail_look)
    store.write_episode(
        EpisodeLog(
            session_id="s",
            episode_id="ok",
            fixture="elden",
            steps=3,
            terminal="timeout",
        )
    )
    store.write_episode(
        EpisodeLog(
            session_id="s",
            episode_id="dead",
            fixture="elden",
            steps=1,
            terminal="fail",
        )
    )
    rows = export_sft_rows(tmp_path)
    ids = {(row["episode_id"], row["step_id"]) for row in rows}
    assert ("ok", 1) in ids
    assert ("ok", 3) in ids
    assert ("ok", 2) not in ids
    assert ("dead", 1) not in ids


def test_export_sts2_keeps_progress_not_pixels(tmp_path: Path) -> None:
    from loop.agent.prompts import prompt_id
    from loop.eval.export import export_dpo_pairs, turn_run_metrics, write_train_jsonl
    from loop.eval.sft import SFT_USER_TURN, chord_json, sft_chat_row

    hid = prompt_id(arm="no_skill", pace="turn")
    store = EpisodeStore(tmp_path)
    cont = StepLog(
        session_id="s",
        episode_id="run",
        step_id=1,
        ts="2026-08-26T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="obs.jpg", vlm_screenshot_ref="vlm.jpg"),
        thought="click continue",
        proposed=ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400),
        action=ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400),
        result=StepResult(pixel_changed=True, executed_as_proposed=True),
        metrics={"screen": "map", "choice": "Continue", "floor": 1},
        terminal="alive",
        prompt_id=hid,
    )
    miss = StepLog(
        session_id="s",
        episode_id="run",
        step_id=2,
        ts="2026-08-26T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="obs2.jpg", vlm_screenshot_ref="vlm.jpg"),
        proposed=ActionLog(mark=9, click_x=0.50, click_y=0.18, duration_ms=400),
        action=ActionLog(mark=9, click_x=0.50, click_y=0.18, duration_ms=400),
        result=StepResult(pixel_changed=True, executed_as_proposed=True),
        metrics={"screen": "map", "choice": "logo"},
        terminal="alive",
        prompt_id=hid,
    )
    esc = StepLog(
        session_id="s",
        episode_id="run",
        step_id=3,
        ts="2026-08-26T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref="obs3.jpg"),
        proposed=ActionLog(name="escape", duration_ms=400),
        action=ActionLog(name="escape", duration_ms=400),
        result=StepResult(pixel_changed=True, executed_as_proposed=True),
        metrics={"screen": "pause", "choice": "escape"},
        terminal="fail",
        prompt_id=hid,
    )
    store.write_step(cont)
    store.write_step(miss)
    store.write_step(esc)
    store.write_episode(
        EpisodeLog(session_id="s", episode_id="run", fixture="sts2", steps=3, terminal="fail")
    )
    rows = export_sft_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["chord"]["mark"] == 1
    assert rows[0]["outcome"]["turn_reward"] > 0
    chat = sft_chat_row(rows[0], run_dir=tmp_path)
    assert "stick_l" not in chat["messages"][0]["content"] or "turn-based" in chat["messages"][0]["content"]
    assert SFT_USER_TURN in chat["messages"][1]["content"][1]["text"]
    parsed = json.loads(chord_json(rows[0]["chord"], turn=True))
    assert parsed["mark"] == 1
    assert "gx" in parsed
    dest = write_train_jsonl(tmp_path)
    train = dest.read_text(encoding="utf-8").strip().splitlines()
    assert len(train) == 3
    pairs = export_dpo_pairs(tmp_path)
    assert pairs
    assert pairs[0]["chosen"]["mark"] == 1
    assert pairs[0]["rejected"]["mark"] == 9
    metrics = turn_run_metrics(tmp_path)
    assert metrics["title_to_map"] in {0, 1}
    assert metrics["escape_rate"] > 0
    assert metrics["false_fail"] == 1
    assert metrics["mixed_prompt_id"] is False


def _sts2_step(
    tmp_path,
    *,
    episode: str,
    step_id: int,
    vlm: str,
    metrics: dict,
    proposed,
    action=None,
    executed: bool = True,
    prompt_id: str = "abc",
    policy_id: str = "qwen3.5:4b-instruct-q4_K_M",
):
    return StepLog(
        session_id="s",
        episode_id=episode,
        step_id=step_id,
        ts="2026-08-26T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref=f"obs{step_id}.jpg", vlm_screenshot_ref=vlm),
        proposed=proposed,
        action=action if action is not None else proposed,
        result=StepResult(pixel_changed=True, executed_as_proposed=executed),
        metrics=metrics,
        terminal="alive",
        prompt_id=prompt_id,
        policy_id=policy_id,
    )


def test_dpo_skips_different_hp_when_vlm_paths_differ(tmp_path: Path) -> None:
    from loop.eval.export import export_dpo_pairs, inspect_run

    store = EpisodeStore(tmp_path)
    setup = ActionLog(mark=3, click_x=0.40, click_y=0.85, duration_ms=400)
    play = ActionLog(mark=3, click_x=0.40, click_y=0.85, target=7, click_x2=0.55, click_y2=0.30, duration_ms=400)
    miss = ActionLog(mark=9, click_x=0.50, click_y=0.18, duration_ms=400)
    store.write_step(
        _sts2_step(
            tmp_path,
            episode="fight",
            step_id=1,
            vlm="vlm_setup.jpg",
            metrics={"screen": "combat", "hp": 80, "energy": 3, "floor": 1, "block": 0, "choice": "Strike"},
            proposed=setup,
        )
    )
    store.write_step(
        _sts2_step(
            tmp_path,
            episode="fight",
            step_id=2,
            vlm="vlm_a.jpg",
            metrics={"screen": "combat", "hp": 80, "energy": 2, "floor": 1, "block": 5, "choice": "Strike"},
            proposed=play,
        )
    )
    store.write_step(
        _sts2_step(
            tmp_path,
            episode="fight",
            step_id=3,
            vlm="vlm_b.jpg",
            metrics={"screen": "combat", "hp": 40, "energy": 3, "floor": 1, "block": 0, "choice": "logo"},
            proposed=miss,
        )
    )
    pairs = export_dpo_pairs(tmp_path)
    assert pairs == []
    report = inspect_run([tmp_path])
    assert "transition" in report["reward_mix"]
    assert "combat" in report["reward_mix"]
    assert report["reward_mix"]["n"] == 3
    assert report["reward_mix"]["combat"] > 0


def test_dpo_safety_override_skips_hover_promote(tmp_path: Path) -> None:
    from loop.eval.export import export_dpo_pairs

    store = EpisodeStore(tmp_path)
    danger = ActionLog(mark=6, click_x=0.50, click_y=0.80, duration_ms=400, name="give up")
    safe = ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
    store.write_step(
        _sts2_step(
            tmp_path,
            episode="menu",
            step_id=1,
            vlm="vlm_over.jpg",
            metrics={"screen": "title", "choice": "Continue"},
            proposed=danger,
            action=safe,
            executed=False,
        )
    )
    hover = ActionLog(hover=True, mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
    click = ActionLog(hover=False, mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
    store.write_step(
        _sts2_step(
            tmp_path,
            episode="menu",
            step_id=2,
            vlm="vlm_hover.jpg",
            metrics={"screen": "title", "choice": "Continue"},
            proposed=hover,
            action=click,
            executed=False,
        )
    )
    empty = ActionLog(duration_ms=400)
    store.write_step(
        _sts2_step(
            tmp_path,
            episode="menu",
            step_id=3,
            vlm="vlm_empty.jpg",
            metrics={"screen": "map", "choice": "Continue"},
            proposed=empty,
            action=safe,
            executed=False,
        )
    )
    pairs = export_dpo_pairs(tmp_path)
    kinds = {row["pair_type"] for row in pairs}
    assert "safety_override" in kinds
    overrides = [row for row in pairs if row["pair_type"] == "safety_override"]
    assert any(row["rejected"].get("name") == "give up" for row in overrides)
    assert any(not (row["rejected"].get("mark") or row["rejected"].get("click_x")) for row in overrides)
    assert not any(row["rejected"].get("hover") for row in overrides)


def test_sft_marks_heuristic_source_and_pools_runs(tmp_path: Path) -> None:
    from loop.eval.export import export_sft_rows, inspect_run

    a = tmp_path / "run_a"
    b = tmp_path / "run_b"
    for path, pid in ((a, "abc"), (b, "abc")):
        store = EpisodeStore(path)
        chord = ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
        store.write_step(
            _sts2_step(
                path,
                episode="run",
                step_id=1,
                vlm="vlm.jpg",
                metrics={"screen": "map", "choice": "Continue", "floor": 1},
                proposed=chord,
                policy_id="sts2-heuristic-v1",
            )
        )
    rows = export_sft_rows(a, run_dirs=[a, b])
    assert rows == []
    rows = export_sft_rows(a, run_dirs=[a, b], source="heuristic")
    assert len(rows) == 2
    assert {row["source"] for row in rows} == {"heuristic"}
    both = export_sft_rows(a, run_dirs=[a, b], source="all")
    assert len(both) == 2
    vlm_run = tmp_path / "run_v"
    store = EpisodeStore(vlm_run)
    store.write_step(
        _sts2_step(
            vlm_run,
            episode="run",
            step_id=1,
            vlm="vlm.jpg",
            metrics={"screen": "map", "choice": "Continue", "floor": 1},
            proposed=ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400),
        )
    )
    vlm_only = export_sft_rows(a, run_dirs=[a, vlm_run])
    assert len(vlm_only) == 1
    assert vlm_only[0]["source"] == "vlm"
    mixed = export_sft_rows(a, run_dirs=[a, vlm_run], source="all")
    assert {row["source"] for row in mixed} == {"heuristic", "vlm"}
    report = inspect_run([a, b])
    assert report["sft_n"] == 2
    assert report["sft_sources"].get("heuristic") == 2
    assert report["prompt"]["mixed"] is False


def _write_eval_run(
    path: Path,
    *,
    hid: str,
    screens: list[str],
    floor: float,
    two_click: bool = False,
    success: bool = False,
) -> None:
    store = EpisodeStore(path)
    for i, screen in enumerate(screens, start=1):
        chord = ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
        if two_click:
            chord = ActionLog(
                mark=3,
                click_x=0.40,
                click_y=0.85,
                target=7,
                click_x2=0.55,
                click_y2=0.30,
                duration_ms=400,
            )
        store.write_step(
            _sts2_step(
                path,
                episode="run",
                step_id=i,
                vlm=f"vlm{i}.jpg",
                metrics={"screen": screen, "floor": floor, "choice": "Continue"},
                proposed=chord,
                prompt_id=hid,
            )
        )
    store.write_episode(
        EpisodeLog(
            session_id="s",
            episode_id="run",
            fixture="sts2",
            steps=len(screens),
            terminal="win" if success else "timeout",
            success=success,
        )
    )


def test_compare_turn_runs_deltas(tmp_path: Path) -> None:
    from loop.eval.export import compare_turn_runs

    hid = "abc"
    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_eval_run(baseline, hid=hid, screens=["tutorial"], floor=1.0, success=False)
    _write_eval_run(
        candidate,
        hid=hid,
        screens=["tutorial", "title", "map", "combat"],
        floor=2.0,
        two_click=True,
        success=True,
    )
    report = compare_turn_runs(baseline, candidate)
    assert report["ok"] is True
    deltas = report["deltas"]
    assert deltas["tutorial_clear"] == 1
    assert deltas["title_to_map"] == 1
    assert deltas["combats_started"] == 1
    assert deltas["mean_floor"] == 1.0
    assert deltas["false_fail"] == 0
    assert deltas["two_click_rate"] == 1.0
    assert deltas["success_rate"] == 1.0
    assert "escape_rate" in deltas


def test_compare_turn_runs_refuses_mixed_prompt_between(tmp_path: Path) -> None:
    from loop.eval.export import compare_turn_runs

    baseline = tmp_path / "baseline"
    candidate = tmp_path / "candidate"
    _write_eval_run(baseline, hid="aaa", screens=["map"], floor=1.0)
    _write_eval_run(candidate, hid="bbb", screens=["map", "combat"], floor=2.0)
    report = compare_turn_runs(baseline, candidate)
    assert report["ok"] is False
    assert "prompt_id" in str(report.get("reason") or "").lower()
    assert "baseline" in report and "candidate" in report


def _baseline_gate_step(
    *,
    step_id: int,
    error: str | None = None,
    intent: dict | None = None,
    tx_verified: bool = False,
) -> StepLog:
    from loop.log.schema import CardTransaction

    tx = None
    if error and error.startswith("source-unconfirmed"):
        tx = CardTransaction(outcome="source_unconfirmed", error=error)
    elif error and error.startswith("target-not-found"):
        tx = CardTransaction(outcome="target_not_found", error=error)
    elif error and error.startswith("effect-unverified"):
        tx = CardTransaction(outcome="effect_unverified", error=error)
    elif tx_verified:
        tx = CardTransaction(outcome="success", effect_verified=True, source_verified=True)
    return StepLog(
        session_id="s",
        episode_id="baseline",
        step_id=step_id,
        ts="2026-09-06T00:00:00+00:00",
        fixture="sts2",
        observation=Observation(screenshot_ref=f"obs{step_id}.jpg", vlm_screenshot_ref=f"vlm{step_id}.jpg"),
        proposed=ActionLog(mark=1, click_x=0.5, click_y=0.7, duration_ms=400),
        action=ActionLog(mark=1, click_x=0.5, click_y=0.7, duration_ms=400),
        result=StepResult(
            pixel_changed=True,
            executed_as_proposed=True,
            error=error,
            card_transaction=tx,
        ),
        metrics={"screen": "combat", "hp": 68, "energy": 2, "floor": 1, "choice": "Strike"},
        prompt_id="abc",
        decision_schema="v2" if intent is not None else "v1",
        intent=intent or {},
    )


def test_export_gate_drops_baseline_uncertain_rows(tmp_path: Path) -> None:
    from loop.eval.export import export_gate_report, export_sft_rows
    from loop.log.schema import CardTransaction

    store = EpisodeStore(tmp_path)
    errors = [
        "source-unconfirmed:card_1",
        "source-unconfirmed:card_2",
        "source-unconfirmed:card_2",
        "source-unconfirmed:card_2",
        "source-unconfirmed:card_5",
        "source-unconfirmed:object:9",
        "source-unconfirmed:card_2",
        "source-unconfirmed:card_2",
        "target-not-found:object:6",
        "target-not-found:object:6",
        "effect-unverified:Strike",
        "effect-unverified:Strike",
        "inventory-inconsistent",
    ]
    for i, err in enumerate(errors, start=1):
        store.write_step(_baseline_gate_step(step_id=i, error=err))
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="baseline",
            step_id=len(errors) + 1,
            ts="2026-09-06T00:00:00+00:00",
            fixture="sts2",
            observation=Observation(screenshot_ref="obs_empty.jpg", vlm_screenshot_ref="vlm_empty.jpg"),
            proposed=ActionLog(duration_ms=400),
            action=ActionLog(duration_ms=400),
            result=StepResult(pixel_changed=False, executed_as_proposed=True),
            metrics={"screen": "combat", "hp": 68, "energy": 2, "floor": 1},
            prompt_id="abc",
        )
    )
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="baseline",
            step_id=len(errors) + 2,
            ts="2026-09-06T00:00:00+00:00",
            fixture="sts2",
            observation=Observation(screenshot_ref="obs_title.jpg", vlm_screenshot_ref="vlm_title.jpg"),
            proposed=ActionLog(mark=1, click_x=0.5, click_y=0.7, duration_ms=400),
            action=ActionLog(mark=1, click_x=0.5, click_y=0.7, duration_ms=400),
            result=StepResult(
                pixel_changed=True,
                executed_as_proposed=True,
                card_transaction=CardTransaction(
                    outcome="success",
                    effect_verified=True,
                    source_verified=True,
                ),
            ),
            metrics={"screen": "title", "choice": "Continue", "floor": 1},
            prompt_id="abc",
        )
    )
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="baseline",
            step_id=len(errors) + 3,
            ts="2026-09-06T00:00:01+00:00",
            fixture="sts2",
            observation=Observation(screenshot_ref="obs_map.jpg", vlm_screenshot_ref="vlm_map.jpg"),
            proposed=ActionLog(mark=2, click_x=0.5, click_y=0.7, duration_ms=400),
            action=ActionLog(mark=2, click_x=0.5, click_y=0.7, duration_ms=400),
            result=StepResult(pixel_changed=True, executed_as_proposed=True),
            metrics={"screen": "map", "choice": "Continue", "floor": 1},
            prompt_id="abc",
        )
    )
    report = export_gate_report([tmp_path])
    assert report["excluded_n"] == 14
    assert report["by_category"]["source-unconfirmed"] == 8
    assert report["by_category"]["target-not-found"] == 2
    assert report["by_category"]["effect-unverified"] == 2
    assert report["by_category"]["inventory-inconsistent"] == 1
    assert report["by_category"]["empty-intent"] == 1
    rows = export_sft_rows(tmp_path)
    assert len(rows) == 2
    assert {row["step_id"] for row in rows} == {len(errors) + 2, len(errors) + 3}

