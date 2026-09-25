"""Tracker comparison metrics. The Mario run itself is marker benchmark."""

from __future__ import annotations

import pytest

from benchmarks.infinite_mario.track_experiment import _Hit, pick_winner, summarize_arm


def _frame(hits, latency=10.0, player_box=None):
    return {"hits": hits, "latency_ms": latency, "player_box": player_box}


def test_semantic_calls_skip_continuing_and_count_tiles():
    box = (0.10, 0.10, 0.16, 0.16)
    episodes = [[
        _frame([
            _Hit("a", (0.2, 0.2, 0.4, 0.5), b"player", "new"),
            _Hit("t1", box, b"tile", "new"),
            _Hit("t2", (0.3, 0.1, 0.36, 0.16), b"tile", "new"),
        ]),
        _frame([
            _Hit("a", (0.25, 0.2, 0.45, 0.5), b"player", "continuing"),
        ]),
        _frame([
            _Hit("a", (0.3, 0.2, 0.5, 0.5), b"player", "returned"),
        ]),
    ]]
    summary = summarize_arm("yolo-world", episodes, load_ms=12.0, unavailable=False, associator="botsort")
    assert summary["semantic_calls"] == 4
    assert summary["semantic_equals_new_plus_returned"] is True
    assert summary["id_switches"] == 0
    assert summary["tile_explosion"] == 2
    assert summary["load_ms"] == 12.0
    assert summary["latency_median_ms"] == 10.0


def test_player_box_overlap_counts_a_switch():
    player = (0.40, 0.40, 0.55, 0.70)
    other = (0.05, 0.05, 0.15, 0.15)
    episodes = [[
        _frame([_Hit("p", player, b"p", "new"), _Hit("e", other, b"e", "new")], player_box=player),
        _frame([_Hit("q", player, b"p", "new"), _Hit("e", other, b"e", "continuing")], player_box=player),
    ]]
    summary = summarize_arm("sam2", episodes, load_ms=0.0, unavailable=False, associator="sam2")
    assert summary["id_switches"] == 1
    assert summary["player_id_coverage"] == 0.5


def test_winner_must_beat_none_under_a_second():
    none = {"ran": True, "unavailable": False, "id_switches": 4, "latency_median_ms": 20.0}
    slow = {"ran": True, "unavailable": False, "id_switches": 1, "latency_median_ms": 1500.0}
    fast = {"ran": True, "unavailable": False, "id_switches": 1, "latency_median_ms": 80.0}
    missing = {"ran": False, "unavailable": True, "id_switches": 0, "latency_median_ms": 10.0}
    assert pick_winner({"none": none, "yolo-world": slow, "sam2": slow}) == "none"
    assert pick_winner({"none": none, "yolo-world": fast, "sam2": missing}) == "yolo-world"
    assert pick_winner({"none": none, "yolo-world": missing, "sam2": missing}) == "none"


@pytest.mark.benchmark
def test_track_comparison_harness(tmp_path):
    from benchmarks.infinite_mario.track_experiment import main

    assert main(["--workspace", str(tmp_path), "--frames", "1"]) == 0
    assert (tmp_path / "track_comparison.json").is_file()
