from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.monitor_sts2_loop as monitor
from loop.config import project_root

RUN_B = project_root() / "runs" / "20260907T200835Z_sts2_collect"


def _row(
    *,
    step_id: int,
    screen: str = "combat",
    hp: int = 68,
    floor=None,
    energy: int | None = 3,
    cards: list | None = None,
    targets: list | None = None,
    error: str | None = None,
    pixel_changed: bool = True,
    action_name: str = "Strike",
    perception_source: str = "llm1",
) -> dict:
    return {
        "step_id": step_id,
        "terminal": "alive",
        "metrics": {"screen": screen, "hp": hp, "floor": floor, "energy": energy},
        "turn_snapshot": {"cards": cards or [], "targets": targets or []},
        "result": {"error": error, "pixel_changed": pixel_changed},
        "action": {"name": action_name},
        "perception_source": perception_source,
    }


def _run_b_like_tail(*, populated: bool, n: int = 12) -> list[dict]:
    cards = [{"card_id": "card_1", "name": "Strike"}] if populated else []
    targets = [{"target_id": "object:10", "name": "unknown enemy"}] if populated else []
    names = [f"act_{i % 5}" for i in range(n)]
    return [
        _row(step_id=i + 1, cards=cards, targets=targets, action_name=names[i], pixel_changed=True)
        for i in range(n)
    ]


def test_stuck_reason_hud_frozen_when_snapshot_populated() -> None:
    rows = _run_b_like_tail(populated=True)
    reason = monitor.stuck_reason(rows, window=12, min_no_change=12)
    assert reason is not None
    assert "HUD frozen" in reason
    assert "screen=combat" in reason


def test_stuck_reason_skips_hud_freeze_when_snapshot_empty() -> None:
    rows = _run_b_like_tail(populated=False)
    reason = monitor.stuck_reason(rows, window=12, min_no_change=12)
    assert reason is None or "HUD frozen" not in reason


def test_stuck_reason_run_b_tail() -> None:
    path = RUN_B / "turns.jsonl"
    if not path.exists():
        pytest.skip("Run B missing")
    rows = monitor.load_steps(path)
    reason = monitor.stuck_reason(rows, window=12, min_no_change=12)
    assert reason is not None
    assert "HUD frozen" in reason


def test_hygiene_counts_from_errors_and_ids() -> None:
    rows = [
        _row(
            step_id=1,
            cards=[{"card_id": "card_1"}],
            targets=[{"target_id": "object:10"}, {"target_id": "unknown_character_1"}],
            error="target-not-found:object:10",
            perception_source="forced_rescan",
        ),
        _row(
            step_id=2,
            cards=[{"card_id": "mk_3"}],
            targets=[{"target_id": "enemy_1"}],
            error="source-unconfirmed:card_3",
            perception_source="scene_cache",
        ),
        _row(step_id=3, error=None, perception_source="llm1"),
    ]
    counts = monitor.hygiene_counts(rows)
    assert counts["noncanonical_ids"] == 3
    assert counts["target_not_found"] == 1
    assert counts["source_unconfirmed"] == 1
    assert counts["forced_rescan"] == 1
    assert counts["steps"] == 3
    line = monitor.hygiene_line(rows)
    assert "noncanonical_ids=3" in line
    assert "target_not_found=1" in line
    assert "source_unconfirmed=1" in line
    assert "forced_rescan=1/3" in line


def test_consecutive_target_not_found_is_trailing_streak() -> None:
    rows = [
        _row(step_id=1, error="target-not-found:a"),
        _row(step_id=2, error="source-unconfirmed:b"),
        _row(step_id=3, error="target-not-found:c"),
        _row(step_id=4, error="target-not-found:d"),
    ]
    assert monitor.consecutive_target_not_found(rows) == 2


def test_once_exits_nonzero_on_target_bind_loop(tmp_path: Path, capsys) -> None:
    rows = [_row(step_id=i, error="target-not-found:enemy_1") for i in range(1, 5)]
    (tmp_path / "turns.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    code = monitor.main(["--run", str(tmp_path), "--once"])
    captured = capsys.readouterr()
    assert code == 1
    assert "STUCK: target bind loop" in captured.out
    assert "noncanonical_ids=" in captured.out


def test_once_three_tnf_does_not_bind_loop(tmp_path: Path) -> None:
    rows = [_row(step_id=i, error="target-not-found:enemy_1") for i in range(1, 4)]
    (tmp_path / "turns.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    assert monitor.main(["--run", str(tmp_path), "--once"]) == 0


@pytest.mark.skipif(not (RUN_B / "turns.jsonl").exists(), reason="Run B missing")
def test_once_run_b_prints_counters(capsys) -> None:
    code = monitor.main(["--run", str(RUN_B), "--once"])
    captured = capsys.readouterr()
    assert code == 0
    assert "noncanonical_ids=69" in captured.out
    assert "target_not_found=16" in captured.out
    assert "source_unconfirmed=7" in captured.out
    assert "forced_rescan=25/28" in captured.out
    assert "STUCK: target bind loop" not in captured.out


def test_stuck_reason_skips_hud_freeze_during_unconfirmed_bind_loop() -> None:
    rows = [
        _row(
            step_id=i + 1,
            error="source-unconfirmed:card_1" if i % 2 == 0 else "stale-obs:motion",
            pixel_changed=False,
            action_name="mk_2>mk_11",
            energy=3,
        )
        for i in range(12)
    ]
    assert monitor.stuck_reason(rows, window=12, min_no_change=12) is None
    assert monitor.unconfirmed_bind_loop(rows, window=12) is True


def test_once_flags_batch5_unconfirmed_without_hud_stop(tmp_path: Path, capsys) -> None:
    rows = [
        _row(step_id=i, error="source-unconfirmed:card_1", pixel_changed=False, energy=3)
        for i in range(1, 4)
    ]
    (tmp_path / "turns.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    code = monitor.main(["--run", str(tmp_path), "--once"])
    captured = capsys.readouterr()
    assert code == 0
    assert "BATCH5 fail: source-unconfirmed>=3" in captured.out
    assert "HUD frozen" not in captured.out
