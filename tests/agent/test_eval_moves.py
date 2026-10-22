import json

from scripts import eval_moves
from scripts.eval_moves import audit


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_failed_movement_classes_use_ram_cells_and_precedence(tmp_path):
    run = tmp_path / "run"
    (run / "checkpoint" / "000000" / "memory").mkdir(parents=True)
    (run / "memory").mkdir()
    rows = []
    for step in range(1, 7):
        x = step - 1
        stage = "crossing" if step == 5 else "route"
        rows.append({"step": step, "screen": "world", "place": "p", "tile": [x, 0],
                     "choice": {"verb": "approach", "target": "exit", "intent": f"p|approach|exit:{step},0"},
                     "execution": "blocked",
                     "skill": {"status": "blocked", "reason": "route blocked",
                               "failure": {"stage": stage, "reason": "route blocked",
                                           **({"direction": "RIGHT"} if step != 5 else {})}}})
    rows += [
        {"step": 7, "screen": "world", "place": "p", "tile": [6, 0],
         "choice": {"verb": "approach", "target": "exit", "intent": "p|approach|exit:7,0"},
         "execution": "done", "skill": {"status": "done"}},
        {"step": 8, "screen": "world", "place": "p", "tile": [6, 0],
         "choice": {"verb": "interact", "target": "thing", "intent": "p|interact|thing:e"},
         "execution": "blocked", "skill": {"status": "blocked"}},
    ]
    write(run / "trace.jsonl", rows)
    truth = [{"step": 1, "map": 1, "x": 0, "y": 0, "sprites": []},
             {"step": 2, "map": 1, "x": 0, "y": 0, "sprites": []},
             {"step": 3, "map": 1, "x": 1, "y": 0, "sprites": []},
             {"step": 4, "map": 1, "x": 2, "y": 0,
              "sprites": [{"slot": 0, "x": 0, "y": 0}, {"slot": 1, "x": 16, "y": 0}]},
             {"step": 5, "map": 1, "x": 3, "y": 0, "sprites": []},
             {"step": 6, "map": 1, "x": 4, "y": 0, "sprites": []},
             {"step": 7, "map": 1, "x": 5, "y": 0, "sprites": []},
             {"step": 8, "map": 1, "x": 6, "y": 0, "sprites": []},
             {"step": 9, "map": 1, "x": 6, "y": 0, "sprites": []}]
    write(run / "truth.jsonl", truth)
    write(run / "presses.jsonl", [{"step": step, "keys": ["RIGHT"]} for step in range(1, 7)])
    write(run / "memory" / "tiles.jsonl", [
        {"id": f"p@{x},0", "place": "p", "x": x, "y": 0,
         "floor": x == 4, "step": 1}
        for x in range(2, 7)])
    write(run / "memory" / "entities.jsonl", [
        {"id": "wall-at-unknown", "place": "p", "tile": [1, 0], "kind": "object", "last": 1},
        {"id": "furniture", "place": "p", "tile": [2, 0], "kind": "object", "last": 1},
        {"id": "actor", "place": "p", "tile": [3, 0], "kind": "character", "last": 1},
    ])

    result = audit("hand", run_root=run)

    assert result["movement_rows"] == 7
    assert result["failed_rows"] == 6
    assert [row["class"] for row in result["rows"]] == [
        "unknown_cell", "static_thing", "actor", "known_floor", "crossing", "other"]
    assert result["rows"][0]["blocked_agent"] == [1, 0]
    assert result["rows"][0]["evidence"]["static_entities"]
    assert result["rows"][2]["evidence"]["ram_actors"]
    assert result["class_counts"]["static_thing"]["wasted_press_events"] == 1
    assert result["class_counts"]["static_thing"]["wasted_key_entries"] == 1
    assert result["requests"] == 0 and result["ledger_unchanged"] is True

    filtered = audit("hand", 2, 5, run_root=run)
    assert filtered["movement_rows"] == 4 and filtered["failed_rows"] == 4


def test_offsets_use_after_place_and_exclude_map_changes():
    truth = {
        1: {"map": 1, "x": 0, "y": 0},
        2: {"map": 1, "x": 1, "y": 2},
        3: {"map": 2, "x": 1, "y": 2},
    }
    trace = [
        {"step": 1, "screen": "world", "place": "source", "next_place": "after",
         "tile": [3, 4]},
        {"step": 2, "screen": "world", "place": "after", "next_place": "other",
         "tile": [3, 4]},
    ]

    offsets = eval_moves._offsets(trace, truth)

    assert offsets == {("after", 1): {"offset": [2, 2], "samples": 1,
                                      "matching": 1, "confidence": 1.0}}


def test_explore_frontier_reports_agent_and_ram_coordinates(tmp_path, monkeypatch):
    (tmp_path / "checkpoint" / "000000" / "memory").mkdir(parents=True)
    monkeypatch.setattr(eval_moves.World, "explore_dirs",
                        lambda self, place: {"RIGHT": {"to": (13, 26)}})
    row = {"place": "p", "choice": {"intent": "p|approach|explore:p_place_4:right"}}
    truth = {5: {"map": 1, "x": 4, "y": 5}}
    offsets = {("p", 1): {"offset": [10, 20], "confidence": 1.0}}

    agent, ram, reason = eval_moves._explore_target(tmp_path, row, truth, offsets, 5)

    assert agent == [13, 26]
    assert ram == [3, 6]
    assert reason is None
