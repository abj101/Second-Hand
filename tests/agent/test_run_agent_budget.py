"""Persistent E3R3 limits retain requests across invocations and rewinds."""
import json
import pytest
from scripts.run_agent import CallCap, _ledger


@pytest.mark.parametrize("stage,spent,cap", [("q",59,60), ("c",545,546)])
def test_repair_stage_caps_preserve_prior_spend(tmp_path, monkeypatch, stage, spent, cap):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "runs/e3_budget.json"; path.parent.mkdir()
    prior = {"total":499,"smoke":18,"m1":481,"stage_a":142,"stage_b":297,
             "stage_p":25,"stage_q":17,"stage_c":0,f"stage_{stage}":spent}
    path.write_text(json.dumps(prior)); _ledger("runs/m1", stage, 1000)
    expected = {**prior,"total":500,"m1":482,f"stage_{stage}":cap}
    assert json.loads(path.read_text()) == expected
    with pytest.raises(CallCap): _ledger("runs/m1", stage, 1000)
    assert json.loads(path.read_text()) == expected


@pytest.mark.parametrize("key,limit,initial", [("total",1100,1099), ("m1",1050,1049)])
def test_phase_and_m1_caps_preserve_spend(tmp_path, monkeypatch, key, limit, initial):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "runs/e3_budget.json"; path.parent.mkdir()
    prior = {"total":initial if key == "total" else 900,"m1":initial if key == "m1" else 900,
             "smoke":18,"stage_a":142,"stage_b":297,"stage_p":25,"stage_q":17,"stage_c":0}
    path.write_text(json.dumps(prior)); _ledger("runs/m1", None, 1000)
    data = json.loads(path.read_text()); assert data[key] == limit
    with pytest.raises(CallCap): _ledger("runs/m1", None, 1000)
    assert json.loads(path.read_text()) == data
