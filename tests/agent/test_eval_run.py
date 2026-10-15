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


def test_p3b_evaluator_counts_cut_loop_and_recent_line(tmp_path):
    run = tmp_path / "p3b"
    write(run / "trace.jsonl", [
        world(1, "p", [0, 0]) | {"entities": [{"kind": "player", "at": [0.45, 68 / 144]}]},
        world(2, "p", [1, 0]) | {"scene_cut": True, "next_place": "p",
                                  "entities": [{"kind": "player", "at": [0.25, 0.8]}],
                                  "act": {"flags": ["recent: line seen"]}},
    ])
    write(run / "truth.jsonl", [
        {"step": 1, "map": 0x26, "sprites": [{"slot": 0, "x": 64, "y": 60}]},
        {"step": 2, "map": 0x26, "sprites": [{"slot": 0, "x": 64, "y": 60}]},
    ])

    result = evaluate(run)

    assert result["cut_self_loops"] == 1
    assert result["recent_line_steps"] == 1


def test_self_spot_uses_native_frame_size_and_after_step_truth(tmp_path):
    from PIL import Image

    run = tmp_path / "self-spot"
    rows = [world(i, "p", [i - 1, 0]) | {"frame": f"frames/{i}.png",
             "self_spot": [.45, 68 / 144], "self_locked": True} for i in (1, 2)]
    write(run / "trace.jsonl", rows)
    write(run / "truth.jsonl", [
        {"step": i, "map": 0x26, "sprites": [{"slot": 0, "x": 64, "y": 60}]}
        for i in (1, 2, 3)])
    for row in rows:
        frame = run / row["frame"]
        frame.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (160, 144)).save(frame)

    result = evaluate(run)
    resumed = evaluate(run, from_step=2)

    assert result["self_spot_accuracy"] == 1.0
    assert result["self_spot_coverage"] == 1.0
    assert resumed["self_spot_accuracy"] == 1.0
    assert resumed["self_spot_measured"] == 1
    assert resumed["from_step"] == 2
    assert not any(row["field"] == "seen_in_bounds" for row in resumed["health"])


def test_false_wall_requires_a_later_crossing_and_from_step_filters_it(tmp_path):
    run = tmp_path / "false-wall"
    write(run / "trace.jsonl", [world(1, "p", [0, 0]), world(2, "p", [1, 0])])
    write(run / "truth.jsonl", [])
    write(run / "memory" / "tiles.jsonl", [
        {"id": "p@0,0", "place": "p", "x": 0, "y": 0, "blocked": ["RIGHT"], "step": 1},
        # The existing row step can be stale when B1 removes a block from a known floor.
        {"id": "p@0,0", "place": "p", "x": 0, "y": 0, "blocked": [], "step": 1},
    ])

    assert evaluate(run)["false_walls"] == 1
    assert evaluate(run, from_step=2)["false_walls"] == 1
    assert evaluate(run, from_step=3)["false_walls"] == 0


def test_from_step_scopes_battles_and_calls_but_keeps_starter_progress(tmp_path):
    run = tmp_path / "resumed-segment"
    write(run / "trace.jsonl", [
        {"step": 1, "screen": "world", "calls": 2},
        {"step": 3, "screen": "world", "calls": 1},
        {"step": 4, "screen": "world", "calls": 3},
    ])
    write(run / "truth.jsonl", [
        {"step": 1, "party": 1, "battle": 2}, {"step": 2, "battle": 2},
        {"step": 3, "battle": 0}, {"step": 4, "battle": 2},
    ])
    write(run / "calls.jsonl", [
        {"step": 1, "ok": True, "model": "local", "requests": 1},
        {"step": 1, "ok": True, "model": "local", "requests": 1},
        {"step": 3, "ok": True, "model": "local", "requests": 1},
        {"step": 4, "ok": True, "model": "local", "requests": 1},
    ])

    full, resumed = evaluate(run), evaluate(run, from_step=3)

    assert full["battles"] == 2 and resumed["battles"] == 1
    assert full["cost"]["total_asks"] == 4 and resumed["cost"]["total_asks"] == 2
    assert resumed["steps_to_starter"] == 0


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


def test_cli_no_write_preserves_run_directory(tmp_path):
    import subprocess, sys
    from pathlib import Path
    run = tmp_path / "read-only-cli"
    write(run / "trace.jsonl", [{"step": 1, "place": "p", "choice": {"verb": "press", "target": "a"}}])
    script = Path(__file__).resolve().parents[2] / "scripts" / "eval_run.py"
    done = subprocess.run([sys.executable, str(script), str(run), "--no-write"], cwd=tmp_path,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert not (run / "summary.json").exists()


def p3_run(tmp_path, places, trace, truth, *, seen=(), knowledge=(), entities=(), notebook=None):
    run = tmp_path / "p3"
    write(run / "memory" / "places.jsonl", places)
    write(run / "trace.jsonl", trace)
    write(run / "truth.jsonl", truth)
    write(run / "memory" / "seen.jsonl", seen)
    write(run / "memory" / "knowledge.jsonl", knowledge)
    write(run / "memory" / "entities.jsonl", entities)
    if notebook is not None:
        (run / "memory" / "notebook.json").write_text(json.dumps(notebook), encoding="utf-8")
    return evaluate(run)


def test_p3_place_ids_skip_merged_and_seen_uses_each_visit_offset(tmp_path):
    places = [{"id": "a", "state": "place"}, {"id": "b", "state": "place"},
              {"id": "old", "state": "merged", "into": "a"}]
    trace = [world(1, "a", [10, 10]), world(2, "b", [0, 0]), world(3, "a", [20, 10]),
             world(4, "old", [0, 0])]
    truth_rows = [truth(i, 0x26, x, y) | {"map_h": 5, "map_w": 6}
                  for i, (x, y) in enumerate(((0, 0), (5, 5), (2, 2), (10, 8), (1, 1)), 1)]
    result = p3_run(tmp_path, places, trace, truth_rows,
                    seen=[{"id": "a", "cells": ["5,5", "20,20"]},
                          {"id": "b", "cells": ["0,0"]},
                          {"id": "old", "cells": ["100,100"]}],
                    entities=[{"id": "a1", "kind": "person", "name": "Oak", "place": "a",
                               "tier": "interest", "tile": [1, 1], "first": 55},
                              {"id": "a2", "kind": "person", "name": "Oak#2", "place": "a",
                               "tier": "interest", "tile": [2, 1], "first": 75},
                              {"id": "m1", "kind": "person", "name": "Oak", "place": "old",
                               "tier": "interest", "tile": [1, 1], "first": 75},
                              {"id": "m2", "kind": "person", "name": "Oak#2", "place": "old",
                               "tier": "interest", "tile": [2, 1], "first": 76}])

    assert result["place_ids_per_map"] == {"0x26": ["a", "b"]}
    assert result["seen_in_bounds"]["by_map"]["0x26"] == {"inside": 2, "total": 5, "rate": 0.4}
    assert result["interest_entity_duplicates"] == {"total": 1, "new_after_74": 1}


def test_p3_repeat_actions_require_three_consecutive_live_place_steps(tmp_path):
    places = [{"id": "p", "state": "place"}, {"id": "merged", "state": "merged", "into": "p"}]
    trace = [{"step": i, "place": "merged" if i == 4 else "p", "frame": f"{i}.png",
              "choice": {"verb": "approach", "target": "door"},
              "act": {"why": f"reason {i}"}} for i in range(1, 8)]
    result = p3_run(tmp_path, places, trace, [truth(i, 0x26, i, 0) for i in range(1, 9)])

    assert [case["steps"] for case in result["repeat_actions"]] == [[1, 2, 3], [5, 6, 7]]
    assert result["repeat_actions"][1]["why"] == ["reason 5", "reason 6", "reason 7"]


def test_p3_reentries_use_five_step_window_and_travel_checks_planned_place(tmp_path):
    places = [{"id": "a", "state": "place", "name": "House"},
              {"id": "b", "state": "place", "name": "Lab", "aliases": ["Professor Oak's lab"]}]
    trace = [{"step": i, "place": "a", "frame": f"{i}.png", "screen": "world",
              "next_place": "b" if i == 1 else "a", "skill": {"reason": "travel: done"},
              "choice": {"id": "b"}, "entities": [],
              "act": {"why": f"go {i}", "action": {"go_to": "Lab"}}}
             for i in range(1, 7)]
    trace[0]["skill"]["status"] = "done"
    trace[1]["act"] = {"why": "blocked exact place", "action": {"go_to": "world [b]"}}
    trace[1]["choice"] = {"id": "e_npc"}
    trace[1]["skill"]["status"] = "blocked"
    trace[1]["next_place"] = "a"
    trace[2]["act"] = {"why": "memory route", "action": {"go_to": "Professor Oak"}}
    trace[2]["choice"] = {"id": "e_npc"}
    truth_rows = [truth(i, map_, i, 0) for i, map_ in enumerate((0x26, 0x26, 0x25, 0x26, 0x25, 0x25, 0x28), 1)]
    entities = [{"id": "e_npc", "kind": "person", "name": "Professor Oak", "place": "b", "tile": [1, 1]}]
    result = p3_run(tmp_path, places, trace, truth_rows, entities=entities)

    assert [(row["map"], row["left_step"], row["step"]) for row in result["reentries"]] == [
        ("0x26", 2, 4), ("0x25", 3, 5)]
    assert result["travel"]["started"] == 6 and result["travel"]["arrived"] == 1
    assert result["travel"]["cases"][1]["planned_place"] == "b"
    assert result["travel"]["cases"][1]["arrived"] is False


def test_p3_reentry_ignores_return_without_observed_departure(tmp_path):
    places = [{"id": "a", "state": "place"}, {"id": "b", "state": "place"},
              {"id": "merged", "state": "merged", "into": "a"}]
    trace = [world(1, "a", [0, 0]), world(2, "merged", [0, 0]),
             world(3, "b", [0, 0]), world(4, "a", [0, 0])]
    truth_rows = [truth(i, map_, 0, 0) for i, map_ in enumerate((0x26, 0x25, 0x25, 0x26, 0x26), 1)]

    result = p3_run(tmp_path, places, trace, truth_rows)

    assert result["reentries"] == []


def test_p3_goal_evidence_must_quote_game_text_and_missing_sources_are_null(tmp_path):
    result = p3_run(tmp_path, [{"id": "p", "state": "place"}], [], [],
                    knowledge=[{"id": "k1", "src": "game_text", "text": "OAK'S AIDE: OAK is looking for you."}],
                    notebook={"goals": {"find Oak": {"evidence": 'Mom said "OAK\'S AIDE"'},
                                         "catch starter": {"evidence": "I choose Bulbasaur"}}})
    assert result["notebook_goals_with_evidence"] == [
        {"key": "find Oak", "evidence": "OAK'S AIDE: OAK is looking for you."}]

    sparse = evaluate(tmp_path / "missing")
    assert all(sparse[key] is None for key in ("place_ids_per_map", "seen_in_bounds", "stuck_streak_max",
                                               "travel", "notebook_goals_with_evidence", "repeat_actions",
                                               "reentries", "interest_entity_duplicates"))


def test_l1_screen_places_only_count_if_created_on_a_run_step(tmp_path):
    run = tmp_path / "screen-places"
    write(run / "trace.jsonl", [{"step": 10}, {"step": 12}])
    write(run / "memory" / "places.jsonl", [
        {"id": "world", "screen": "world", "name": "world", "first": 10},
        {"id": "dialogue", "screen": "dialogue_screen", "first": 12},
        {"id": "named-menu", "screen": "world", "name": "menu", "first": 10},
        {"id": "before-run", "screen": "menu", "first": 9},
        {"id": "after-run", "screen": "dialogue_box", "first": 13},
    ])

    assert evaluate(run)["screen_places"] == 2


def test_l1_no_effect_streak_requires_exact_effect_list(tmp_path):
    run = tmp_path / "no-effect-streak"
    effects = [["no_change"], ["no_change", "screen_changes"], ["no_change"],
               ["no_change"], ["no_change"], [], ["no_change"]]
    write(run / "trace.jsonl", [{"step": i, "outcome": {"effects": value}}
                                 for i, value in enumerate(effects, 1)])

    assert evaluate(run)["no_effect_streak_max"] == 3


def test_lifecycle_replays_stuck_and_reads_v2_history(tmp_path):
    from scripts.eval_run import _lifecycle

    run = tmp_path / "lifecycle"
    trace = [{"step": step, "screen": "world", "place": "p", "outcome": {"effects": ["no_change"]}}
             for step in range(1, 44)]
    trace[21]["plan"] = {"trigger": "new game text: heard a lead"}
    write(run / "trace.jsonl", trace)
    write(run / "memory" / "notebook.history", [
        {"step": 2, "op": "quests", "value": [{"key": "lead", "text": "Find the guide", "source": "heard",
                                                         "evidence": "talked to guide", "status": "active"}]},
        {"step": 3, "op": "belief", "key": "route", "value": {"stance": "held", "reason": "saw a path"}},
        {"step": 4, "op": "belief", "key": "route", "value": {"stance": "ruled_out", "reason": "door was locked"}},
        {"step": 6, "op": "quests", "value": [{"key": "lead", "text": "Find the guide", "source": "heard",
                                                         "evidence": "talked to guide", "status": "closed"}]},
    ])
    (run / "memory" / "notebook.json").write_text(json.dumps({
        "quests": [{"key": "lead", "text": "Find the guide", "source": "heard", "evidence": "talked to guide", "status": "closed"},
                   {"key": "tiny", "text": "Press A to open the door", "status": "active"}],
        "beliefs": {"route": {"stance": "ruled_out", "reason": "door was locked"}}, "labels": ["A"], "notes": []
    }), encoding="utf-8")

    result = _lifecycle(run)

    assert {row["trigger"] for row in result["trigger_replay"]["calls"]} == {
        "T1 new game text", "T2 first world place", "T4 no progress", "T5 empty first step"}
    assert result["history_metrics"]["heard_leads"] == {
        "opened": 1, "closed": 1, "parked": 0, "still_open": 0, "silently_lost": 0}
    assert len(result["history_metrics"]["belief_revisions"]) == 1
    assert result["history_metrics"]["belief_revisions"][0]["reason"] == "door was locked"
    assert result["history_metrics"]["rule4_quest_matches"] == ["tiny"]


def test_lifecycle_prioritizes_text_over_first_place_at_one_step(tmp_path):
    from scripts.eval_run import _lifecycle

    run = tmp_path / "priority"
    trace = [
        {"step": 1, "screen": "world", "place": "first"},
        {"step": 2, "screen": "world", "place": "second"},
        {"step": 4, "screen": "world", "place": "second",
         "plan": {"trigger": "new game text: new dialogue"}},
        {"step": 7, "screen": "world", "place": "second"},
    ]
    write(run / "trace.jsonl", trace)

    calls = _lifecycle(run)["trigger_replay"]["calls"]

    assert [(row["step"], row["trigger"]) for row in calls] == [
        (1, "T5 empty first step"), (4, "T1 new game text"), (7, "T2 first world place")]


def test_lifecycle_reads_v1_notebook_log_and_goal_history_shapes(tmp_path):
    from scripts.eval_run import _lifecycle

    run = tmp_path / "v1-history"
    write(run / "trace.jsonl", [
        {"step": 1, "screen": "world", "place": "a"},
        {"step": 4, "screen": "world", "place": "a"},
        {"step": 8, "screen": "world", "place": "b"},
    ])
    write(run / "memory" / "notebook.history", [
        {"step": 2, "op": "set", "section": "goals", "key": "lead-1",
         "value": {"text": "Find the guide", "source": "heard", "status": "active"}},
        {"step": 3, "op": "log", "text": 'SPEAKER at tiledroom: "The road is north"'},
        {"step": 6, "op": "set", "section": "goals", "key": "lead-1",
         "value": {"text": "Find the guide", "source": "heard", "status": "closed"}},
        {"step": 6, "op": "check", "key": "lead-1", "evidence": "found"},
    ])
    (run / "memory" / "notebook.json").write_text(json.dumps({
        "goals": {"lead-1": {"text": "Find the guide", "source": "heard", "status": "closed"},
                  "false-positive": {"text": "North is a town", "status": "active"},
                  "tactical": {"text": "Go to #12", "status": "active"}},
        "facts": {}, "beliefs": {}, "notes": []
    }), encoding="utf-8")

    result = _lifecycle(run)

    assert result["trigger_replay"]["calls"] == [
        {"step": 1, "trigger": "T5 empty first step"},
        {"step": 4, "trigger": "T1 new game text"},
        {"step": 8, "trigger": "T2 first world place"},
    ]
    assert result["trigger_replay"]["text_event_steps"] == [3]
    assert "notebook.history" in result["trigger_replay"]["text_event_source"]
    assert result["history_metrics"]["heard_leads"] == {
        "opened": 1, "closed": 1, "parked": 0, "still_open": 0, "silently_lost": 0}
    assert result["history_metrics"]["rule4_quest_matches"] == ["tactical"]


def test_lifecycle_reads_v2_fact_references_quest_snapshots_and_revisions(tmp_path):
    from scripts.eval_run import _lifecycle

    run = tmp_path / "v2-history"
    write(run / "trace.jsonl", [
        {"step": 1, "screen": "world", "place": "a"},
        {"step": 4, "screen": "world", "place": "a",
         "plan": {"trigger": "stuck:no_progress", "quests_changed": 0, "beliefs_changed": 0}},
        {"step": 7, "screen": "world", "place": "a",
         "plan": {"trigger": "stuck:no_progress", "quests_changed": 1, "beliefs_changed": 0}},
    ])
    heard = {"key": "lead-key", "text": "Find the path", "why": ["heard-fact"], "status": "active"}
    lost = {"key": "lost-key", "text": "Find the other path", "why": ["heard-fact"], "status": "active"}
    write(run / "memory" / "notebook.history", [
        {"step": 2, "op": "migrate_fact", "fact": {"key": "heard-fact", "source": "heard"}},
        {"step": 2, "op": "migrate_quest", "quest": heard},
        {"step": 2, "op": "migrate_quest", "quest": lost},
        {"step": 3, "op": "belief", "key": "route", "value": {"history": [
            {"step": 3, "from_text": "old route", "reason": "saw sign"},
            {"step": 5, "from_text": "new route", "reason": "door blocked"}]}},
        {"step": 4, "op": "quests", "value": [{**heard, "status": "active"},
                                                        {**lost, "status": "dropped"}]},
        {"step": 6, "op": "quests", "value": [{**heard, "status": "parked"}]},
        {"step": 6, "op": "check", "key": "lead-key", "evidence": "confirmed"},
        {"step": 7, "op": "plan", "value": {"trigger": "stuck:no_progress"}},
    ])
    (run / "memory" / "notebook.json").write_text(json.dumps({
        "quests": [{**heard, "status": "parked"}],
        "facts": {"heard-fact": {"source": "heard"}}, "beliefs": {}, "notes": []
    }), encoding="utf-8")

    metrics = _lifecycle(run)["history_metrics"]

    assert metrics["heard_leads"] == {
        "opened": 2, "closed": 1, "parked": 0, "still_open": 0, "silently_lost": 1}
    assert [row["reason"] for row in metrics["belief_revisions"]] == ["saw sign", "door blocked"]
    assert metrics["stall_calls_changed_nothing"] == 1
    assert metrics["plan_calls_per_step"]["7"] == 1


def test_lifecycle_counts_removed_heard_leads_as_lost_and_groups_planner_writes(tmp_path):
    from scripts.eval_run import _lifecycle

    run = tmp_path / "removed-lead"
    write(run / "trace.jsonl", [{"step": 1, "screen": "world", "place": "p",
                                 "plan": {"trigger": "no open quest"}}])
    write(run / "memory" / "notebook.history", [
        {"step": 1, "op": "set", "section": "goals", "key": "lead",
         "value": {"text": "Find the library", "source": "heard", "status": "active"}},
        {"step": 1, "op": "set", "section": "goals", "key": "other",
         "value": {"text": "Find a task", "source": "assumed", "status": "active"}},
        {"step": 1, "op": "belief", "key": "route", "value": {"stance": "held"}},
        {"step": 2, "op": "remove", "section": "goals", "key": "lead"},
    ])
    (run / "memory" / "notebook.json").write_text(json.dumps({
        "goals": {"other": {"text": "Find a task", "source": "assumed", "status": "active"}},
        "facts": {},
    }), encoding="utf-8")

    metrics = _lifecycle(run)["history_metrics"]

    assert metrics["heard_leads"] == {
        "opened": 1, "closed": 0, "parked": 0, "still_open": 0, "silently_lost": 1}
    assert metrics["plan_calls_per_step"]["1"] == 1


def test_e4_reflection_attributes_v2_completion_to_opening_plan_step(tmp_path):
    from scripts.eval_run import _e4_metrics

    run = tmp_path / "e4-v2"
    (run / "memory").mkdir(parents=True)
    (run / "memory" / "notebook.json").write_text(json.dumps({
        "quests": [{"key": "q", "text": "Find the guide", "status": "done", "opened": 5, "closed": 9}],
        "facts": {}, "beliefs": {}, "notes": []
    }), encoding="utf-8")
    write(run / "memory" / "notebook.history", [
        {"step": 5, "op": "plan", "value": {"trigger": "new game text"}},
        {"step": 9, "op": "quests", "value": [{"key": "q", "text": "Find the guide",
                                                       "status": "done", "opened": 5, "closed": 9}]},
    ])

    result = _e4_metrics(run, [{"step": 5, "plan": {"trigger": "new game text"}}], [])

    assert result["reflection_usefulness"] == {"plan_calls": 1, "goals_completed": 1}


def test_l1_health_keeps_missing_evidence_unknown(tmp_path):
    health = evaluate(tmp_path / "missing")["health"]

    assert all(row["green"] is None for row in health)


def test_l1_health_does_not_ignore_world_maps_without_place_ids(tmp_path):
    run = tmp_path / "unmapped-world"
    write(run / "trace.jsonl", [{"step": 1, "screen": "world"}])
    write(run / "truth.jsonl", [{"step": 1, "map": 0x26}])

    row = next(row for row in evaluate(run)["health"] if row["field"] == "place_ids_per_map")

    assert row == {"field": "place_ids_per_map", "value": {"0x26": 0}, "green": False}


def test_cli_health_prints_table_without_writing_summary(tmp_path):
    import subprocess, sys
    from pathlib import Path
    run = tmp_path / "health-cli"
    write(run / "trace.jsonl", [{"step": 1}])
    script = Path(__file__).resolve().parents[2] / "scripts" / "eval_run.py"

    done = subprocess.run([sys.executable, str(script), str(run), "--no-write", "--health"],
                          cwd=tmp_path, capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert "| Field | Value | Green |" in done.stdout
    assert "| self_spot_accuracy | unknown | unknown |" in done.stdout
    assert "| self_spot_coverage | unknown | unknown |" in done.stdout
    assert not (run / "summary.json").exists()


def test_p3_stuck_streak_resets_on_new_text_and_truth_map_change(tmp_path):
    maps = (0x26, 0x26, 0x25, 0x25, 0x25, 0x25)
    places = [{"id": "a", "state": "place"}, {"id": "b", "state": "place"}]
    trace = [{"step": i, "place": "a" if map_ == 0x26 else "b"} for i, map_ in enumerate(maps, 1)]
    truth_rows = [truth(i, map_, 0, 0) for i, map_ in enumerate((*maps, 0x25), 1)]
    result = p3_run(tmp_path, places, trace, truth_rows,
                    knowledge=[{"id": "k", "src": "game_text", "step": 4, "text": "new"}])

    assert result["stuck_streak_max"] == 2


def test_truth_reader_keeps_symbol_map_dimensions_in_two_by_two_blocks():
    from scripts.truth_pokered import read as read_truth

    class StubEnv:
        def ram(self, address):
            return {0xD368: 7, 0xD369: 10}.get(address, 0)

    row = read_truth(StubEnv())

    assert row["map_h"] == 7 and row["map_w"] == 10


def test_execution_success_skips_pending_rows_and_counts_each_action_once(tmp_path):
    run = tmp_path / "exec"
    trace = [
        {"step": 1, "place": "p", "observation_status": "pending", "execution": "done", "action_step": None, "verdict": "hit", "skill": {"status": "done", "inputs": 2}},
        {"step": 2, "place": "p", "observation_status": "settled", "execution": "done", "action_step": 1, "verdict": "hit", "skill": {"status": "done", "inputs": 2}},
        {"step": 3, "place": "p", "observation_status": "settled", "execution": "blocked", "action_step": None, "verdict": "miss", "skill": {"status": "blocked", "inputs": 1}},
        {"step": 4, "place": "p", "observation_status": "settled", "execution": "failed", "action_step": None, "verdict": "hit", "skill": {"status": "failed", "inputs": 0}},
        {"step": 5, "place": "p", "observation_status": "pending", "execution": None, "action_step": None, "verdict": "unobservable", "skill": None},
        {"step": 6, "place": "p", "observation_status": "settled", "execution": "done", "action_step": 5, "verdict": "hit", "skill": {"status": "done", "inputs": 1}},
        {"step": 7, "place": "p", "observation_status": "settled", "execution": "done", "action_step": 5, "verdict": "hit", "skill": {"status": "done", "inputs": 1}},
    ]
    write(run / "trace.jsonl", trace)

    result = evaluate(run)

    # actions 1 (done), 3 (blocked), 4 (zero-input select, failed though its verdict is a hit), 5 (done, resolved once at step 6)
    assert result["execution_success"] == {"done": 2, "failed": 2, "total": 4, "pending_rows": 2, "rate": 0.5}
