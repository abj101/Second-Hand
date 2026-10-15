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


@pytest.mark.parametrize("key,limit,initial", [("total",3265,3264), ("m1",3215,3214)])
def test_phase_and_m1_caps_preserve_spend(tmp_path, monkeypatch, key, limit, initial):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "runs/e3_budget.json"; path.parent.mkdir()
    prior = {"total":initial if key == "total" else 900,"m1":initial if key == "m1" else 900,
             "smoke":18,"stage_a":142,"stage_b":297,"stage_p":25,"stage_q":17,"stage_c":0}
    path.write_text(json.dumps(prior)); _ledger("runs/m1", None, 1000)
    data = json.loads(path.read_text()); assert data[key] == limit
    with pytest.raises(CallCap): _ledger("runs/m1", None, 1000)
    assert json.loads(path.read_text()) == data


def test_e4_uses_separate_600_request_ledger(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    e3 = tmp_path / "runs/e3_budget.json"; e3.parent.mkdir()
    e3.write_text('{"total":2925,"smoke":50,"m1":2875}')
    e3_before = e3.read_bytes()
    e4 = tmp_path / "runs/e4_budget.json"
    e4.write_text('{"total":598}')

    _ledger("runs/e4", None, 1000)
    _ledger("runs/e4", None, 1000)

    assert json.loads(e4.read_text()) == {"total": 600}
    with pytest.raises(CallCap): _ledger("runs/e4", None, 1000)
    assert json.loads(e4.read_text()) == {"total": 600}
    assert e3.read_bytes() == e3_before
