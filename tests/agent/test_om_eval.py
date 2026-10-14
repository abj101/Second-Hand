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
