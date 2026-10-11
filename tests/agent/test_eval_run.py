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
