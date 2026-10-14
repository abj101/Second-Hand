import json, re
from pathlib import Path
import pytest
from scripts import eval_act

CASES = Path(__file__).resolve().parents[2] / "scripts" / "om_cases.json"

def test_action_option_maps_each_action_shape():
    ents = [{"name": "stairs"}, {"name": "Mom"}]
    assert eval_act.action_option({"go_to": "exit: door mat"}) == ("go_to", "exit: door mat")
    assert eval_act.action_option({"buttons": ["UP", "A"]}) == ("buttons", "UP A")
    assert eval_act.action_option({"thing": 1, "verb": "approach"}, ents) == ("approach", "Mom")
    assert eval_act.action_option({"thing": 9, "verb": "approach"}, ents, target="stairs") == ("approach", "stairs")
    assert eval_act.action_option({"verb": "advance"}) == ("advance", "")
    assert eval_act.action_option(None) == ("none", "")

def test_judging_matches_tool_and_label():
    case = {"right": [{"tool": "approach|go_to", "label_re": "door mat|rug"}]}
    assert eval_act.is_right(case, "go_to", "exit door mat: never used")
    assert eval_act.is_right(case, "approach", "approach rug")
    assert not eval_act.is_right(case, "buttons", "UP A -> door mat")  # the tool must match too
    assert not eval_act.is_right(case, "approach", "approach stairs")

def test_cases_file_is_well_formed():
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    assert len(cases) == 8 and len({(c["run"], c["step"]) for c in cases}) == 8
    for c in cases:
        assert c["run"] in {"m1_gp5", "m1_gp2"} and c["right"] and {"map", "x", "y"} <= set(c["truth"])
        for e in c["right"] + c["wrong"]: re.compile(e.get("tool", ".*")); re.compile(e["label_re"])

def test_replay_never_reaches_the_network_and_stops_after_three_perceiver_misses(tmp_path):
    rep = eval_act.Replay(None, tmp_path, counter={"requests": 0})
    with pytest.raises(eval_act.OmAbort): rep.real._post({})  # no live case step: no request
    for n in range(3): assert rep.ask("s", f"unseen perception {n}", [], None, role="perceiver").data is None
    with pytest.raises(eval_act.OmAbort): rep.ask("s", "unseen perception 3", [], None, role="perceiver")
    assert rep.misses == 4 and rep.counter["requests"] == 0

def _synthetic(step=103, floor_map=38, label_re="stairs"):
    return {"run": "m1_gp5", "step": step, "truth": {"map": floor_map, "x": 0, "y": 0}, "situation": "synthetic",
            "right": [{"tool": "approach|go_to", "label_re": label_re}], "wrong": []}

def _opt(n, tool, label, action="a", notes=()):
    return {"n": n, "tool": tool, "label": label, "action": action, "notes": list(notes)}

FREE = _opt(9, "buttons", "free buttons", action=None)
DEAD_END = "everything unexplored from here is through this exit"
STAIRS_OK = _opt(1, "go_to", "exit stairs → 1F (used 2×, last step 99) (1 unexplored exit, 10 unexplored spots beyond)", notes=(DEAD_END,))
EXPLORE_0 = _opt(3, "explore", "walk to an unexplored part of this place (0 spots lead anywhere unseen)", action={"go_to": "unexplored"})
DOORWAY = _opt(2, "approach", "possible exit doorway (unverified, never crossed)")

def _case_2f(options=None, exits=None, prompt="x" * 400):
    if options is None: options = [STAIRS_OK, DOORWAY, _opt(3, "approach", "approach Mom"), FREE]
    if exits is None: exits = [{"dest": "1F", "tile": (3, 4)}, {"dest": "x", "tile": (5, 5)}]
    return {"case": _synthetic(), "options": options, "prompt": prompt, "exits": exits}

def _case_1f(options=None, exits=None):
    if options is None: options = [_opt(1, "approach", "possible exit door mat (unverified, never crossed)"), _opt(2, "approach", "approach Mom"), FREE]
    if exits is None: exits = [{"dest": "outside", "tile": (5, 3)}]
    return {"case": _synthetic(step=82, floor_map=37, label_re="door mat|rug"), "options": options, "prompt": "x" * 400, "exits": exits}

def _status(rows): return {g: ok for g, ok, _ in rows}

def test_dry_gates_pass_on_synthetic_2F_and_1F_cases():
    assert all(ok for _, ok, _ in eval_act.dry_gates(_case_2f()))
    assert all(ok for _, ok, _ in eval_act.dry_gates(_case_1f()))

def test_right_present_fails_without_a_right_option():
    rows = eval_act.dry_gates(_case_2f(options=[DOORWAY, FREE]))
    assert _status(rows)["right present"] is False

def test_exit_truth_2F_fails_on_each_bad_stairs_label():
    bad = {
        "stairs unverified": [_opt(1, "approach", "possible exit stairs (unverified, never crossed)"), DOORWAY, FREE],
        "two verified exits": [STAIRS_OK, _opt(2, "go_to", "exit doorway → 1F (used 1×, last step 5)"), FREE],
        "verified exit is not the stairs": [_opt(1, "go_to", "exit doorway → 1F (used 1×) (1 unexplored exit, 2 unexplored spots beyond)"), FREE],
        "no beyond-summary": [_opt(1, "go_to", "exit stairs → 1F (used 2×, last step 99)"), FREE],
    }
    for name, options in bad.items():
        assert _status(eval_act.dry_gates(_case_2f(options=options)))["exit truth"] is False, name

def test_exit_truth_2F_needs_the_dead_end_note_and_a_zero_unseen_explore_label():
    ok = _case_2f(options=[STAIRS_OK, EXPLORE_0, DOORWAY, FREE])
    assert _status(eval_act.dry_gates(ok))["exit truth"] is True
    no_note = _opt(1, "go_to", STAIRS_OK["label"])
    rows = eval_act.dry_gates(_case_2f(options=[no_note, DOORWAY, FREE]))
    detail = next(d for g, ok_, d in rows if g == "exit truth")
    assert _status(rows)["exit truth"] is False and "the stairs are not named as the way to everything unexplored" in detail
    busy = _opt(3, "explore", "walk to an unexplored part of this place (26 spots lead somewhere unseen)", action={"go_to": "unexplored"})
    rows = eval_act.dry_gates(_case_2f(options=[STAIRS_OK, busy, DOORWAY, FREE]))
    detail = next(d for g, ok_, d in rows if g == "exit truth")
    assert _status(rows)["exit truth"] is False and "explore label reports unseen spots" in detail and "26 spots" in detail

def test_exit_truth_1F_fails_when_the_rug_is_verified():
    options = [_opt(1, "approach", "exit door mat → outside (used 3×, last step 40)"), FREE]
    assert _status(eval_act.dry_gates(_case_1f(options=options)))["exit truth"] is False

def test_exit_truth_fails_on_two_exit_records_with_the_same_destination_and_tile():
    exits = [{"dest": "1F", "tile": (3, 4)}, {"dest": "1F", "tile": (3, 4)}]
    assert _status(eval_act.dry_gates(_case_2f(exits=exits)))["exit truth"] is False

def test_size_counts_options_without_the_free_row_and_the_prompt_tokens():
    ten = [_opt(n, "approach", f"approach thing {n}") for n in range(1, 11)]
    assert _status(eval_act.dry_gates(_case_2f(options=ten + [FREE])))["size"] is True
    eleven = [_opt(n, "approach", f"approach thing {n}") for n in range(1, 12)]
    assert _status(eval_act.dry_gates(_case_2f(options=eleven + [FREE])))["size"] is False
    assert _status(eval_act.dry_gates(_case_2f(prompt="x" * 6801)))["size"] is False  # 1,700.25 tokens

def test_live_is_refused_when_a_dry_gate_fails(monkeypatch, capsys):
    rows = [("right present", False, "step 82: no right option is listed"), ("exit truth", True, ""), ("size", True, "")] * 8
    monkeypatch.setattr(eval_act, "dry_report", lambda cases, counter, fresh=False: (rows, 0))
    monkeypatch.setattr(eval_act, "Replay", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no replay may be built")))
    monkeypatch.setattr(eval_act, "live_cases", lambda *a: (_ for _ in ()).throw(AssertionError("live must not run")))
    assert eval_act.om_cases(True) == 1
    assert "DRY GATES FAILED: live measure not run" in capsys.readouterr().out

def test_live_runs_after_all_dry_gates_pass(monkeypatch):
    rows = [(g, True, "") for g in ("right present", "exit truth", "size")] * 8
    calls = []
    monkeypatch.setattr(eval_act, "dry_report", lambda cases, counter, fresh=False: (rows, 0))
    monkeypatch.setattr(eval_act, "live_cases", lambda cases, counter, before, old_right: calls.append((len(cases), old_right)) or 0)
    assert eval_act.om_cases(True) == 0
    assert calls == [(8, 0)]

def test_dry_run_never_calls_live(monkeypatch):
    rows = [(g, True, "") for g in ("right present", "exit truth", "size")] * 8
    monkeypatch.setattr(eval_act, "dry_report", lambda cases, counter, fresh=False: (rows, 0))
    monkeypatch.setattr(eval_act, "live_cases", lambda *a: (_ for _ in ()).throw(AssertionError("dry must not call live")))
    assert eval_act.om_cases(False) == 0

def test_classify_outcomes():
    b, tiles = {"map": 38, "x": 6, "y": 1}, {(7, 1)}
    at = lambda m, x, y: {"map": m, "x": x, "y": y}
    assert eval_act.classify(b, at(37, 7, 1), 37, tiles) == "crossed_right"
    assert eval_act.classify(b, at(40, 0, 0), 37, tiles) == "crossed_other"
    assert eval_act.classify(at(38, 3, 1), at(38, 4, 1), 37, tiles) == "closer"
    assert eval_act.classify(b, at(38, 5, 1), 37, tiles) == "other"
    assert eval_act.classify(b, at(38, 6, 1), 37, tiles) == "no_move"
