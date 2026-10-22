import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import eval_entities


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_report_metrics(tmp_path, capsys):
    ent = lambda n: [{"id": None, "name": "player", "kind": "player", "at": [0, 0]} for _ in range(n)]
    _write(tmp_path / "trace.jsonl", [
        {"screen": "world", "step": 1, "entities": ent(2), "option": {"label": "check cup, 1 up"}},
        {"screen": "world", "step": 2, "entities": ent(4), "option": {"label": "check cup, 1 up"}},
        {"screen": "world", "step": 3, "entities": ent(9), "option": {"label": "check cup, 1 up"}},
        {"screen": "world", "step": 4, "entities": ent(3), "option": {"label": "talk to mum"}},
    ])
    _write(tmp_path / "truth.jsonl", [{"step": s, "map": 0, "x": x, "y": 0} for s, x in [(1, 0), (2, 0), (3, 1), (4, 1)]])
    _write(tmp_path / "memory" / "entities.jsonl", [
        {"id": "e_cup", "kind": "object", "name": "cup", "place": "p", "tile": [0, 0], "status": "seen", "last": 1},
        {"id": "e_cup", "kind": "object", "name": "mug", "place": "p", "tile": [0, 1], "status": "seen", "last": 2},
        {"id": "e_cup", "kind": "object", "name": "cup", "place": "p", "tile": [0, 1], "status": "seen", "last": 3},
        {"id": "e_bob", "kind": "object", "name": "bob", "place": "p", "tile": [5, 5], "status": "seen", "last": 1},
        {"id": "e_bob", "kind": "player", "name": "bob", "place": "p", "tile": [5, 5], "status": "seen", "last": 2},
        {"id": "e_box", "kind": "object", "name": "box", "place": "p", "tile": [1, 1], "status": "gone", "last": 1},
        {"id": "e_box", "kind": "object", "name": "box", "place": "p", "tile": [1, 1], "status": "seen", "last": 3},
        {"id": "e_shelf", "kind": "object", "name": "shelf", "place": "p", "tile": [2, 2], "status": "gone", "last": 2},
        {"id": "e_npc", "kind": "npc", "name": "nurse", "place": "p", "tile": [3, 3], "status": "seen", "last": 1},
        {"id": "e_npc", "kind": "npc", "name": "nurse", "place": "p", "tile": [4, 4], "status": "seen", "last": 2},
    ])
    eval_entities.report(tmp_path)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "perceived_static mean=3.50 min=3 max=4 steps=2"  # step 2 (unchanged) and step 4 (unchanged vs 3)
    assert out[1] == "shown_name_flips total=2 max_per_id=2 ids=1"  # cup -> mug -> cup
    assert out[2] == "kind_flips total=1 max_per_id=1 ids=1"  # bob object -> player
    assert out[3] == "player_kind_ids n=1"
    assert out[4] == "false_gone ids=1 static_gone_at_end=1"  # box gone then seen again; shelf gone at end
    assert out[5] == "static_tile_moves total=1"  # cup moved once; npc move excluded
    assert out[6].startswith("option_label_churn changes=1 thing_steps=2")  # step 2 and 4 labels differ
