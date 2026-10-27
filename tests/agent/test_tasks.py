import json
from pathlib import Path

import pytest

from agent import tasks
from agent.predict import result
from agent.tasks import Stuck


def test_stuck_tracks_only_novel_places_edges_and_progress():
    stuck = Stuck()
    row = {"screen": "world", "place": "p_a", "next_place": "p_b"}
    assert stuck.update(row) is None
    assert stuck.progress_n == 0
    assert stuck.update({**row, "verified_edge": True}) is None
    assert stuck.seen_edges == {"p_a>p_b"}
    assert stuck.progress_n == 0
    assert stuck.update({**row, "verified_edge": True}) is None
    assert stuck.progress_n == 1
    assert stuck.update({**row, "verdict": "progress"}) is None
    assert stuck.progress_n == 0


def test_stuck_offworld_only_counts_repeated_text():
    stuck = Stuck()
    assert stuck.update({"screen": "dialogue", "fresh_text": False}) is None
    assert stuck.progress_n == 1
    assert stuck.update({"screen": "dialogue", "fresh_text": True}) is None
    assert stuck.progress_n == 1


def test_stuck_backoff_caps_and_progress_resets_it():
    stuck = Stuck()
    stuck.replanned(False)
    stuck.replanned(False)
    stuck.replanned(False)
    stuck.replanned(False)
    assert stuck.u == 3
    stuck.progress_n = 1
    stuck.replanned(True)
    assert stuck.u == 0
    stuck.progress()
    assert (stuck.progress_n, stuck.u) == (0, 0)


def test_stuck_roundtrips_only_current_state_keys():
    stuck = Stuck()
    stuck.progress_n, stuck.seen_places, stuck.seen_edges, stuck.u = 4, {"p_a"}, {"p_a>p_b"}, 2
    state = stuck.state()
    assert set(state) == {"progress_n", "seen_places", "seen_edges", "u"}
    restored = Stuck()
    restored.restore(state)
    assert restored.state() == state
    restored.restore({"n": 7, "action": ["old"], "same_action": 3})
    assert restored.state() == {"progress_n": 0, "seen_places": [], "seen_edges": [], "u": 0}


def _m1_replay(rows, k):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tasks, "STALL", k)
        stuck, fires = Stuck(), []
        for row in rows:
            effects = [e for e in row["outcome"]["effects"] if not e.startswith("entity_")]
            novel = tuple(row.get("scroll") or (0, 0)) != (0, 0) or row["next_place"] != row["place"]
            verdict = result(row["skill"]["status"], effects, novel)
            if stuck.update({**row, "verdict": verdict}) == "no_progress":
                fires.append(row["step"])
        return fires


def test_m1_replay_backoff_thresholds_and_chosen_k():
    path = Path(__file__).parent / "data" / "r4_m1_rows.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf8").splitlines()]
    fires = {k: _m1_replay(rows, k) for k in (6, 8, 10, 12)}
    chosen = max(k for k, steps in fires.items() if any(64 <= step <= 73 for step in steps))
    assert chosen == tasks.STALL
    assert fires[chosen] == [72]
