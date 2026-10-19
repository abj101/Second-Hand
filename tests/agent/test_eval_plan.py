import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.eval_plan import _cite, notebook_at


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _run(root):
    run = root / "run"
    memory = run / "memory"
    _jsonl(memory / "places.jsonl", [
        {"id": "p1", "name": "Bedroom", "state": "live"},
        {"id": "p2", "name": "Hall", "state": "live"},
        {"id": "p3", "name": "Kitchen", "state": "live"},
    ])
    _jsonl(memory / "entities.jsonl", [{"id": "e1", "name": "Mom", "state": "live"}])
    _jsonl(memory / "relations.jsonl", [
        {"step": 4, "s": "e1", "p": "says", "o": "hello"},
        {"step": 5, "s": "e1", "p": "says", "o": "again"},
    ])
    _jsonl(memory / "notebook.history", [
        {"step": 1, "op": "text", "value": {"key": "t1", "step": 1, "speaker": "Mom", "where": "Bedroom", "text": "Find the hall."}},
        {"step": 1, "op": "quests", "value": [
            {"key": "q0", "text": "Find the hall", "why": ["t1"], "status": "open", "opened": 1},
            {"key": "old_hall", "text": "Visit the hall", "why": ["t1"], "status": "done", "closed": 1, "check": {"kind": "reach", "arg": "p2"}},
            {"key": "legacy_fact", "text": "Keep its cited source", "why": ["fact_legacy"], "status": "done", "closed": 1},
        ]},
        {"step": 2, "op": "log", "text": "text at Bedroom: \"The door is open.\""},
    ])
    _jsonl(run / "trace.jsonl", [
        {"step": step, "place": place, "plan": {"trigger": "fixture"}}
        for step, place in enumerate(("p1", "p1", "p2", "p2", "p3", "p3"), 1)
    ])
    calls = []
    for step in range(1, 7):
        quests, beliefs = [], []
        if step == 2:
            quests = [{"key": "new_hall", "text": "Go to the hall", "why": "b_hall", "status": "open",
                       "check": {"kind": "reach", "arg": "p2"}}]
            beliefs = [{"key": "b_hall", "text": "The hall is next", "because": ["t1"], "status": "held"}]
        calls.append({"step": step, "role": "plan", "response": json.dumps({"quests": quests, "beliefs": beliefs, "why": "recorded"})})
    _jsonl(run / "calls.jsonl", calls)
    return run


def _check(run, check):
    return subprocess.run([sys.executable, "scripts/eval_plan.py", "--run", str(run), "--check", check],
                          capture_output=True, text=True)


def test_cite_resolves_belief_keys_to_text_keys():
    nb = SimpleNamespace(data={"text": [{"key": "t1"}]})
    beliefs = [{"key": "b", "because": ["t1", "missing"]}, {"key": "c", "because": ["b"]}]
    assert _cite(nb, "b", beliefs) == ["t1"] and _cite(nb, ["c", "unknown"], beliefs) == ["t1"]
    assert _cite(nb, "unknown", beliefs) == []


def test_replays_six_row_run_and_rebuilds_notebook(tmp_path):
    run = _run(tmp_path)
    nb = notebook_at(run, 2)
    assert [(q["key"], q["status"]) for q in nb.data["quests"]] == [
        ("q0", "open"), ("old_hall", "done"), ("legacy_fact", "done")]
    assert nb.data["quests"][-1]["why"] == ["fact_legacy"]
    assert [row["key"] for row in nb.data["text"]] == ["t1", "t2"]
    assert 'Mom at Bedroom: "Find the hall."' in nb.render()
    assert 'text at Bedroom: "The door is open."' in nb.render()

    for check in ("size", "ops", "observe"):
        result = _check(run, check)
        assert result.returncode == 0, result.stdout + result.stderr
    result = _check(run, "ops")
    assert "re-adds of finished target: 1" in result.stdout
    assert '"why": ["t1"]' in result.stdout
    assert "cannot map quest" not in result.stdout


def test_v3_replay_skips_belief_ops(tmp_path):
    run = _run(tmp_path)
    calls = [json.loads(line) for line in (run / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    calls[0]["response"] = json.dumps({"ops": [{"op": "believe", "key": "b1", "text": "A lead", "because": ["t1"]}], "why": "v3"})
    _jsonl(run / "calls.jsonl", calls)
    result = _check(run, "ops")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "step 1: skipped belief op b1" in result.stdout
    assert '"op": "believe"' not in result.stdout


def test_ops_fails_reply_over_600_chars_without_truncating_quest(tmp_path):
    run = _run(tmp_path)
    calls = [json.loads(line) for line in (run / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    calls[1]["response"] = json.dumps({"quests": [{"key": "long", "text": "x" * 650, "why": "t1"}], "why": ""})
    _jsonl(run / "calls.jsonl", calls)
    result = _check(run, "ops")
    assert result.returncode != 0
    assert "transcribed reply is over 600 chars" in result.stdout
    assert "x" * 650 in result.stdout
