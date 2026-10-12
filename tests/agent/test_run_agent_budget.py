"""Persistent probe limits retain requests across invocations and rewinds."""
import json

import pytest

from scripts.run_agent import CallCap, _ledger


@pytest.mark.parametrize("stage,spent,cap", [("q",31,32), ("c",453,454)])
def test_repair_stage_caps_preserve_prior_spend(tmp_path, monkeypatch, stage, spent, cap):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "runs/e3_budget.json"
    path.parent.mkdir()
    prior = {"total":482, "smoke":18, "m1":464, "stage_a":142,
             "stage_b":297, "stage_p":25, f"stage_{stage}":spent}
    path.write_text(json.dumps(prior))
    _ledger("runs/m1", stage, 1000)
    expected = {**prior, "total":483, "m1":465, f"stage_{stage}":cap}
    assert json.loads(path.read_text()) == expected
    with pytest.raises(CallCap):
        _ledger("runs/m1", stage, 1000)
    assert json.loads(path.read_text()) == expected
