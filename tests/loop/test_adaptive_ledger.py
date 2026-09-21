from pathlib import Path

from loop.adaptive.evidence import evidence_record
from loop.adaptive.schema import EvidenceLedger


def test_ledger_jsonl_reload(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = EvidenceLedger(path)
    ledger.append(evidence_record("ocr", {"text": "a", "bbox": [0, 0, 1, 1]}))
    ledger.append(evidence_record("region", {"bbox": [0.1, 0.1, 0.2, 0.2]}))
    reloaded = EvidenceLedger(path)
    assert len(reloaded.all()) == 2
    assert {r.kind for r in reloaded.all()} == {"ocr", "region"}
