from __future__ import annotations

import json
from pathlib import Path

from loop.agent.prompts import prompt_id
from loop.eval.dpo import sandbox_dpo_pairs, train_dpo
from loop.log.schema import ActionLog, EpisodeLog, Observation, StepLog, StepResult
from loop.log.store import EpisodeStore


def _sts2_step(
    *,
    episode: str,
    step_id: int,
    vlm: str,
    metrics: dict,
    proposed,
    action=None,
    executed: bool = True,
    hid: str = "abc",
) -> StepLog:
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
        prompt_id=hid,
    )


def _write_dpo_run(tmp_path: Path, *, hid: str) -> Path:
    store = EpisodeStore(tmp_path)
    cont = ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
    miss = ActionLog(mark=9, click_x=0.50, click_y=0.18, duration_ms=400)
    store.write_step(
        _sts2_step(
            episode="run",
            step_id=1,
            vlm="vlm.jpg",
            metrics={"screen": "map", "choice": "Continue", "floor": 1},
            proposed=cont,
            hid=hid,
        )
    )
    store.write_step(
        _sts2_step(
            episode="run",
            step_id=2,
            vlm="vlm.jpg",
            metrics={"screen": "map", "choice": "logo"},
            proposed=miss,
            hid=hid,
        )
    )
    store.write_episode(EpisodeLog(session_id="s", episode_id="run", fixture="sts2", steps=2, terminal="alive"))
    return tmp_path


def test_train_dpo_dry_run_writes_pairs(tmp_path: Path) -> None:
    hid = prompt_id(arm="no_skill", pace="turn")
    run = _write_dpo_run(tmp_path, hid=hid)
    report = train_dpo(run, execute=False)
    assert report["ok"] is True
    assert report["executed"] is False
    assert report["n"] >= 1
    pairs_path = Path(report["pairs"])
    assert pairs_path.exists()
    assert pairs_path.read_text(encoding="utf-8").strip()
    chat = Path(report["chat"])
    assert chat.exists()
    row = json.loads(chat.read_text(encoding="utf-8").splitlines()[0])
    assert row["prompt"]
    assert row["chosen"]
    assert row["rejected"]
    assert str(report.get("reason") or "").startswith("dry-run")


def test_sandbox_dpo_rejects_inverted_rewards() -> None:
    hid = prompt_id(arm="no_skill", pace="turn")
    ok, reason = sandbox_dpo_pairs(
        [
            {
                "obs": "vlm.jpg",
                "prompt_id": hid,
                "same_frame": True,
                "chosen": {"mark": 1, "click_x": 0.5, "click_y": 0.7},
                "rejected": {"mark": 9, "click_x": 0.5, "click_y": 0.18},
                "chosen_reward": 0.0,
                "rejected_reward": 0.3,
            }
        ]
    )
    assert ok is False
    assert "chosen_reward" in reason
    empty_ok, _ = sandbox_dpo_pairs([])
    assert empty_ok is False


def test_dpo_chosen_ineligible_for_uncertain_errors(tmp_path: Path) -> None:
    from loop.eval.export import export_dpo_pairs

    store = EpisodeStore(tmp_path)
    good = ActionLog(mark=1, click_x=0.50, click_y=0.70, duration_ms=400)
    bad = ActionLog(mark=9, click_x=0.50, click_y=0.18, duration_ms=400)
    store.write_step(
        _sts2_step(
            episode="fight",
            step_id=1,
            vlm="vlm_a.jpg",
            metrics={"screen": "combat", "hp": 80, "energy": 3, "floor": 1, "choice": "Strike"},
            proposed=good,
        )
    )
    store.write_step(
        StepLog(
            session_id="s",
            episode_id="fight",
            step_id=2,
            ts="2026-08-26T00:00:00+00:00",
            fixture="sts2",
            observation=Observation(screenshot_ref="obs2.jpg", vlm_screenshot_ref="vlm_b.jpg"),
            proposed=bad,
            action=bad,
            result=StepResult(pixel_changed=False, executed_as_proposed=True, error="source-unconfirmed:card_2"),
            metrics={"screen": "combat", "hp": 80, "energy": 3, "floor": 1, "choice": "logo"},
            terminal="alive",
            prompt_id="abc",
        )
    )
    pairs = export_dpo_pairs(tmp_path)
    assert not any(pair["step_id"] == 2 and pair.get("pair_type") == "state_bound" for pair in pairs)
