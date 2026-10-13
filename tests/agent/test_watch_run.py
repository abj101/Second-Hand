import json, os
from scripts.watch_run import Follow, RunState


def test_follow_switches_to_newest_trace_unless_pinned(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    for i, run in enumerate((old, new)):
        run.mkdir(); (run / "trace.jsonl").write_text("", encoding="utf-8"); os.utime(run / "trace.jsonl", (i, i))
    assert Follow(None, tmp_path).current().run == new
    os.utime(old / "trace.jsonl", (9, 9))
    assert Follow(None, tmp_path).current().run == old
    assert Follow(new, tmp_path).current().run == new


def test_state_tails_trace_dedupes_tiles_and_guards_frames(tmp_path):
    (tmp_path / "memory").mkdir(); (tmp_path / "frames").mkdir()
    (tmp_path / "trace.jsonl").write_text(json.dumps({"step": 1}) + "\n" + json.dumps({"step": 2}) + "\n" + '{"step": 3', encoding="utf-8")
    tile = {"id": "p@0,0", "place": "p", "x": 0, "y": 0}
    (tmp_path / "memory" / "tiles.jsonl").write_text(json.dumps({**tile, "floor": None}) + "\n" + json.dumps({**tile, "floor": True}) + "\n", encoding="utf-8")
    (tmp_path / "presses.jsonl").write_text(json.dumps({"step": 2, "keys": ["LEFT", "A"]}) + "\n", encoding="utf-8")
    (tmp_path / "calls.jsonl").write_text("".join(json.dumps({"step": 2, "role": r}) + "\n" for r in ("perceive", "act")), encoding="utf-8")
    run = RunState(tmp_path)
    state = run.state(0)
    assert [r["step"] for r in state["rows"]] == [1, 2]  # partial row 3 skipped
    assert state["tiles"] == [{**tile, "floor": True}] and state["presses"] == {"2": ["LEFT", "A"]}
    assert [c["role"] for c in state["call_rows"]["2"]] == ["perceive", "act"] and state["calls"] == 2
    with (tmp_path / "trace.jsonl").open("a", encoding="utf-8") as f: f.write("}\n")
    assert [r["step"] for r in run.state(2)["rows"]] == [3]
    (tmp_path / "frames" / "000001.png").write_bytes(b"png")
    assert run.frame("000001.png") == b"png" and run.frame("../trace.jsonl") is None
    (tmp_path / "frames" / "000001_after.png").write_bytes(b"after")
    assert run.frame("000001_after.png") == b"after" and run.state(0)["pending"] is None
    (tmp_path / "live.json").write_text(json.dumps({"step": 4}), encoding="utf-8")
    assert run.state(3)["pending"] == {"step": 4}
