import json

from scripts.eval_run import evaluate


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_eval_run_metrics_and_excludes_candidates(tmp_path):
    run = tmp_path / "run"
    places = [
        {"id": "p26", "state": "place", "edges": [{"to": "p25", "ok": 1}]},
        {"id": "p25", "state": "place", "edges": [{"to": "p00", "ok": 1}]},
        {"id": "p00", "state": "place", "edges": [{"to": "p28", "ok": 1}]},
        {"id": "p28", "state": "place", "edges": []},
        {"id": "candidate", "state": "candidate", "edges": []},
    ]
    trace = [
        {"step": 1, "place": "p26", "frame": "same.png", "frame_hash": "h1", "choice": {"verb": "approach", "target": "door"}, "verdict": "miss"},
        {"step": 2, "place": "p26", "frame": "same.png", "frame_hash": "h1", "choice": {"verb": "approach", "target": "door"}, "verdict": "miss",
         "scene_cut": True, "next_place": "p25",
         "outcome": {"effects": ["screen_changes"], "basis": {"screen_changes": "pixel"}}},
        {"step": 3, "place": "p25", "frame": "next.png", "frame_hash": "h2", "choice": {"verb": "advance", "target": "text"}, "verdict": "hit",
         "scene_cut": True, "next_place": "p00",
         "outcome": {"effects": ["screen_changes"], "basis": {"screen_changes": "pixel"}}},
        {"step": 4, "place": "p00", "frame": "town.png", "frame_hash": "h3", "choice": {"verb": "approach", "target": "lab"}, "verdict": "partial",
         "scene_cut": True, "next_place": "p28",
         "outcome": {"effects": ["screen_changes"], "basis": {"screen_changes": "pixel"}}},
        {"step": 5, "place": "p28", "frame": "lab.png", "choice": {"verb": "interact", "target": "Oak"}, "verdict": "hit"},
        {"step": 6, "place": "candidate", "frame": "candidate.png", "choice": {"verb": "wait", "target": ""}, "verdict": "hit"},
    ]
    truth = [
        {"step": 1, "map": "0x26", "party": 0}, {"step": 2, "map": "0x26", "party": 0},
        {"step": 3, "map": "0x25", "party": 0}, {"step": 4, "map": "0x00", "party": 0},
        {"step": 5, "map": "0x28", "party": 1, "player_name": "Hand", "rival_name": "Blue"},
        {"step": 6, "map": "0x26", "party": 1},
    ]
    write(run / "memory" / "places.jsonl", places)
    write(run / "memory" / "knowledge.jsonl", [{"id": f"k{i}", "src": "game_text"} for i in range(10)] +
          [{"id": "k0", "src": "game_text"}])
    write(run / "memory" / "relations.jsonl", [
        {"id": "r1", "src": "model", "state": "refuted"},
        {"id": "r2", "src": "model", "state": "refuted"},
        {"id": "r1", "src": "model", "state": "live"},
    ])
    write(run / "trace.jsonl", trace)
    write(run / "truth.jsonl", truth)
    write(run / "calls.jsonl", [
        {"cached": False, "retries": 0, "requests": 0},
        {"cached": False, "retries": 1},
    ])

    result = evaluate(run)

    assert result["place_purity"] == {"0x26": 1.0, "0x25": 1.0, "0x00": 1.0, "0x28": 1.0}
    assert result["places_matched"] is True
    assert result["place_mapping"]["Red's house 2F"]["place"] == "p26"
    assert result["m1_starter"] is True and result["steps_to_starter"] == 4
    assert result["prediction_accuracy"]["overall"] == 0.5
    assert result["repeated_mistakes"]["count"] == 1
    assert all(result["edges_found"].values())
    assert result["knowledge_game_text"] == 10
    assert result["poison_rate"] == {"refuted_model_claims": 1, "model_claims": 2, "rate": 0.5}
    assert result["cost"]["http_attempts"] == 2
    assert result["m1_gate_pass"] is True


def gate_run(run, steps, acquired_after):
    places = [{"id": pid, "state": "place", "edges": edges} for pid, edges in (
        ("p26", [{"to": "p25", "ok": 1}]), ("p25", [{"to": "p00", "ok": 1}]),
        ("p00", [{"to": "p28", "ok": 1}]), ("p28", []))]
    place_by_step = [None, "p26", "p26", "p25", "p00"] + ["p28"] * (steps - 4)
    transition = {2: "p25", 3: "p00", 4: "p28"}
    trace = [{"step": i, "place": place_by_step[i], "scene_cut": i in transition,
              "next_place": transition.get(i),
              "outcome": {"effects": ["screen_changes"], "basis": {"screen_changes": "pixel"}}}
             for i in range(1, steps + 1)]
    map_by_place = {"p26": "0x26", "p25": "0x25", "p00": "0x00", "p28": "0x28"}
    truth = [{"step": i, "map": map_by_place[place_by_step[i] if i <= steps else "p28"],
              "party": int(i > acquired_after), "player_name": "Hand", "rival_name": "Blue"}
             for i in range(1, steps + 2)]
    write(run / "memory" / "places.jsonl", places)
    write(run / "memory" / "knowledge.jsonl", [{"id": f"k{i}", "src": "game_text"} for i in range(10)])
    write(run / "trace.jsonl", trace)
    write(run / "truth.jsonl", truth)


def test_starter_steps_uses_before_action_truth_and_terminal_row(tmp_path):
    at_limit, over_limit = tmp_path / "at-limit", tmp_path / "over-limit"
    gate_run(at_limit, 600, 600)
    gate_run(over_limit, 601, 601)

    passed, failed = evaluate(at_limit), evaluate(over_limit)

    assert passed["steps_to_starter"] == 600 and passed["m1_gate_pass"] is True
    assert failed["steps_to_starter"] == 601 and failed["m1_gate_pass"] is False


def spatial_run(tmp_path, trace, truth, *, entities=(), tiles=()):
    run = tmp_path / "spatial"
    write(run / "trace.jsonl", trace)
    write(run / "truth.jsonl", truth)
    write(run / "memory" / "entities.jsonl", entities)
    write(run / "memory" / "tiles.jsonl", tiles)
    return evaluate(run)["spatial"]


def world(step, place, tile, *, next_place=None):
    return {"step": step, "place": place, "next_place": next_place, "screen": "world", "tile": tile}


def truth(step, map_, x, y):
    return {"step": step, "map": map_, "x": x, "y": y}


def test_tile_accuracy_handles_constant_per_visit_offset(tmp_path):
    result = spatial_run(tmp_path,
        [world(1, "p", [30, 40]), world(2, "p", [31, 40])],
        [truth(1, 0x26, 8, 9), truth(2, 0x26, 8, 9), truth(3, 0x26, 9, 9)])

    metric = result["tile_accuracy_per_visit"]
    assert metric["accuracy"] == 1.0
    assert metric["visits"][0]["offset"] == [-22, -31]
    assert metric["visits"][0]["total"] == 2


def test_tile_accuracy_counts_a_wrong_tile(tmp_path):
    result = spatial_run(tmp_path,
        [world(1, "p", [0, 0]), world(2, "p", [1, 0]), world(3, "p", [9, 0])],
        [truth(1, 0x26, 4, 4), truth(2, 0x26, 5, 4), truth(3, 0x26, 6, 4), truth(4, 0x26, 6, 4)])

    metric = result["tile_accuracy_per_visit"]
    assert metric["accuracy"] == 2 / 3
    assert metric["visits"][0]["correct"] == 2
    assert metric["worst_visit_accuracy"] == 2 / 3


def test_mixed_legacy_and_resumed_visits_report_coverage_separately(tmp_path):
    result = spatial_run(tmp_path,
        [world(53, "legacy", None), world(55, "resumed", [0, 0]),
         world(56, "resumed", None)],
        [truth(53, 0x26, 5, 5), truth(54, 0x26, 5, 5),
         truth(55, 0x25, 10, 10), truth(56, 0x25, 10, 10), truth(57, 0x25, 11, 10)])

    metric = result["tile_accuracy_per_visit"]
    assert metric["accuracy"] is not None
    assert metric["visits_measured"] == 1
    assert metric["worst_visit_accuracy"] == 0.5
    assert metric["post_resume"] == {"accuracy": 0.5, "correct": 1, "total": 2,
                                      "measured": 1, "world_steps": 2, "unmeasured": 1}


def test_tile_accuracy_starts_a_new_visit_after_place_and_map_changes(tmp_path):
    result = spatial_run(tmp_path,
        [world(1, "a", [0, 0]), world(2, "a", [1, 0], next_place="b"),
         world(3, "b", [2, 0]), world(4, "b", [1, 0], next_place="a")],
        [truth(1, 0x26, 0, 0), truth(2, 0x26, 1, 0), truth(3, 0x25, 10, 10),
         truth(4, 0x25, 11, 10), truth(5, 0x26, 20, 20)])

    visits = result["tile_accuracy_per_visit"]["visits"]
    assert [(v["place"], v["map"], v["visit"]) for v in visits] == [
        ("a", "0x26", 1), ("b", "0x25", 1), ("a", "0x26", 2)]
    assert all(v["accuracy"] == 1.0 for v in visits)


def test_cut_tile_uses_after_step_truth_and_after_place(tmp_path):
    result = spatial_run(tmp_path,
        [world(1, "upstairs", [4, 4], next_place="downstairs"),
         world(2, "downstairs", [5, 4])],
        [truth(1, 0x26, 1, 1), truth(2, 0x25, 10, 20), truth(3, 0x25, 11, 20)])

    visit = result["tile_accuracy_per_visit"]["visits"][0]
    assert visit["place"] == "downstairs" and visit["map"] == "0x25"
    assert visit["accuracy"] == 1.0


def test_entity_duplicate_metric_separates_same_name_locations(tmp_path):
    entities = [
        {"id": "e1", "kind": "object", "name": "Doormat#3#9", "place": "p", "tile": [1, 2], "first": 10},
        {"id": "e2", "kind": "object", "name": "Doormat", "place": "p", "tile": [2, 2], "first": 55},
        {"id": "e3", "kind": "object", "name": "Doormat", "place": "p", "tile": [7, 2], "first": 55},
        {"id": "e4", "kind": "object", "name": "Doormat#3", "place": "p", "tile": [2, 3], "first": 56},
    ]
    result = spatial_run(tmp_path, [], [], entities=entities)

    assert result["entity_duplicates"] == {"total": 2, "legacy": 0, "new_after_resume": 1}


def test_recurring_kind_is_global_duplicate_even_across_places(tmp_path):
    entities = [
        {"id": "item-a", "kind": "item", "name": "Potion#3", "place": "p1", "tile": [1, 1], "first": 55},
        {"id": "item-b", "kind": "item", "name": "Potion", "place": "p2", "tile": [9, 9], "first": 56},
    ]

    duplicates = spatial_run(tmp_path, [], [], entities=entities)["entity_duplicates"]

    assert duplicates == {"total": 1, "legacy": 0, "new_after_resume": 1}


def test_known_exit_revisit_reuses_id_and_reports_mismatch(tmp_path):
    rows = [world(1, "b", [0, 0]), world(2, "b", [1, 0], next_place="a"),
            {**world(3, "a", [0, 0], next_place="b"), "scene_cut": True, "exit": [0, 0, "UP"]}]
    truths = [truth(1, 0x25, 0, 0), truth(2, 0x25, 1, 0), truth(3, 0x26, 0, 0), truth(4, 0x25, 0, 0)]
    tiles = [{"id": "a@0,0", "place": "a", "x": 0, "y": 0, "step": 2,
              "exit": {"to": "b", "dir": "UP", "src": "observed"}}]
    passed = spatial_run(tmp_path / "pass", rows, truths, tiles=tiles)["known_exit_id_reuse"]
    rows[-1] = {**world(3, "a", [0, 0], next_place="b-new"), "scene_cut": True,
                "exit": [0, 0, "UP"]}
    failed_truth = [*truths[:-1], truth(4, 0x00, 0, 0)]
    failed = spatial_run(tmp_path / "fail", rows, failed_truth, tiles=tiles)["known_exit_id_reuse"]

    assert passed == {"known_exit_revisits": 1, "reused": 1, "mismatches": 0,
                      "unmeasured_transitions": 0, "pass": True}
    assert failed == {"known_exit_revisits": 1, "reused": 0, "mismatches": 1,
                      "unmeasured_transitions": 0, "pass": False}


def test_unknown_exit_to_known_map_is_not_a_known_exit_revisit(tmp_path):
    rows = [world(1, "b", [0, 0]), world(2, "b", [1, 0], next_place="a"),
            {**world(3, "a", [0, 0], next_place="b"), "scene_cut": True, "exit": [9, 9, "UP"]}]
    truths = [truth(1, 0x25, 0, 0), truth(2, 0x25, 1, 0), truth(3, 0x26, 0, 0), truth(4, 0x25, 0, 0)]
    tiles = [{"id": "a@0,0", "place": "a", "x": 0, "y": 0, "step": 2,
              "exit": {"to": "b", "dir": "UP", "src": "observed"}}]

    result = spatial_run(tmp_path, rows, truths, tiles=tiles)["known_exit_id_reuse"]

    assert result == {"known_exit_revisits": 0, "reused": 0, "mismatches": 0,
                     "unmeasured_transitions": 0, "pass": None}


def test_legacy_cut_without_exit_metadata_is_unmeasured(tmp_path):
    rows = [world(1, "a", [0, 0], next_place="b") | {"scene_cut": True}]
    result = spatial_run(tmp_path, rows,
        [truth(1, 0x26, 0, 0), truth(2, 0x25, 0, 0)])["known_exit_id_reuse"]

    assert result["known_exit_revisits"] == 0
    assert result["unmeasured_transitions"] == 1
    assert result["pass"] is None


def test_legacy_trace_without_spatial_values_is_unmeasured(tmp_path):
    result = spatial_run(tmp_path,
        [{"step": 1, "place": "p"}], [truth(1, 0x26, 5, 6), truth(2, 0x26, 5, 6)])

    metric = result["tile_accuracy_per_visit"]
    assert metric["accuracy"] is None
    assert metric["total"] == 0
    assert metric["visits"] == []


def test_e4_metrics_and_offline_repredict_preserve_chooser_guess(tmp_path):
    run = tmp_path / "e4"
    trace = [
        {"step": 1, "place": "p", "frame_hash": "same", "wake": "periodic", "task": "goal",
         "choice": {"verb": "approach", "target": "door", "id": "door1"},
         "outcome": {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}, "to": "p2"}},
        {"step": 2, "place": "p", "frame_hash": "same", "task": "goal",
         "choice": {"verb": "approach", "target": "door", "id": "door1"},
         "pred": {"basis": "instance", "id": "future", "effect": "screen_changes", "detail": None},
         "skill": {"status": "done"},
         "outcome": {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}, "to": "p2"}},
        {"step": 3, "place": "p2", "frame_hash": "other",
         "choice": {"verb": "approach", "target": "table", "id": "table1"},
         "outcome": {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}, "to": "p3"}},
        {"step": 4, "place": "p", "frame_hash": "same",
         "choice": {"verb": "approach", "target": "door", "id": "door1"},
         "outcome": {"effects": ["place_changes"], "basis": {"place_changes": "pixel"}, "to": "p2"}},
        {"step": 5, "place": "p2", "frame_hash": "unknown-kind",
         "choice": {"verb": "approach", "target": "unknown", "id": "ghost1"},
         "outcome": {"effects": ["screen_changes"], "basis": {"screen_changes": "pixel"}}},
        {"step": 6, "place": "p2", "frame_hash": "battle",
         "choice": {"verb": "select", "target": "move"}},
    ]
    write(run / "trace.jsonl", trace)
    write(run / "truth.jsonl", [{"step": 1, "battle": 0}, {"step": 2, "battle": 2},
                                 {"step": 3, "battle": 2}, {"step": 4, "battle": 2},
                                 {"step": 5, "battle": 2}, {"step": 6, "battle": 2},
                                 {"step": 7, "battle": 0}])
    write(run / "memory" / "entities.jsonl", [
        {"id": "door1", "kind": "door", "name": "Door", "place": "p", "first": 1, "last": 1},
        {"id": "door1", "kind": "door", "name": "Door", "place": "p", "first": 1, "last": 4},
        {"id": "table1", "kind": "furniture", "name": "Table", "place": "p", "first": 3, "last": 3}])
    write(run / "memory" / "tasks.jsonl", [{"id": "planned-goal", "plan_step": 1, "status": "active"}])
    write(run / "memory" / "patterns.jsonl", [{"id": "pat1", "state": "promoted"}])
    write(run / "chooser.jsonl", [{"step": 2, "choice": {"effect": "no_change", "detail": None}},
                                   {"step": 5, "choice": {"effect": "screen_changes", "detail": None}}])

    before_done = evaluate(run)
    write(run / "memory" / "tasks.jsonl", [{"id": "planned-goal", "plan_step": 1, "status": "done"}])
    full = evaluate(run, repredict=True)
    hidden = evaluate(run, repredict=True, hide_memory=True)

    assert full["doors_verified"] == 1
    assert full["patterns_promoted"] == 1
    assert full["battles"] == 1 and full["moves_selected"] == 1
    assert before_done["reflection_usefulness"]["goals_completed"] == 0
    assert full["repeat_sequences"] == {"length": 2, "count": 0}
    assert full["reflection_usefulness"] == {"plan_calls": 1, "goals_completed": 1}
    assert (full["acc_first"], full["acc_repeat"], full["n_first"], full["n_repeat"]) == (None, 1.0, 0, 1)
    assert hidden["acc_repeat"] == 0.0 and hidden["n_repeat"] == full["n_repeat"]
    assert hidden["n"] == full["n"] == 1 and full["excluded"] == 5


def test_repeat_sequences_count_repeated_verified_action_pairs(tmp_path):
    run = tmp_path / "sequences"
    action = {"verb": "approach", "target": "door"}
    outcome = {"effects": ["screen_changes"], "basis": {"screen_changes": "pixel"}}
    trace = [{"step": i, "place": "p", "choice": action, "outcome": outcome} for i in (1, 2, 4, 5)]
    trace.insert(2, {"step": 3, "place": "p", "choice": action})
    write(run / "trace.jsonl", trace)

    result = evaluate(run)["repeat_sequences"]

    assert result == {"length": 2, "count": 1}


def test_cli_runs_standalone_outside_repo(tmp_path):
    import subprocess, sys
    from pathlib import Path
    run = tmp_path / "cli"
    write(run / "trace.jsonl", [{"step": 1, "place": "p", "choice": {"verb": "press", "target": "a"}}])
    script = Path(__file__).resolve().parents[2] / "scripts" / "eval_run.py"
    done = subprocess.run([sys.executable, str(script), str(run)], cwd=tmp_path, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert (run / "summary.json").exists()
