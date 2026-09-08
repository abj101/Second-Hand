from __future__ import annotations

from pathlib import Path

from loop.memory.clear import clear_fixture_memory, clear_loop_memory
from loop.memory.journal import FactJournal, journal_path


def test_clear_fixture_memory(tmp_path: Path) -> None:
    base = tmp_path / "data" / "loop" / "sts2"
    base.mkdir(parents=True)
    journal = base / "journal.json"
    journal.write_text('{"facts": [], "bindings": [], "slots": [], "graph": {}, "_order": [], "goal": "old"}')
    sqlite = base / "memory.sqlite"
    sqlite.write_bytes(b"x")
    semantic = base / "semantic_memory.json"
    semantic.write_text("{}")

    removed = clear_fixture_memory("sts2", root=tmp_path)
    assert str(journal) in removed
    assert str(sqlite) in removed
    assert str(semantic) in removed
    assert not journal.exists()
    assert not sqlite.exists()
    assert not semantic.exists()


def test_clear_loop_memory_resets_journal(tmp_path: Path) -> None:
    path = journal_path("sts2", root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"facts": [{"key": "k", "kind": "map", "heading": "c", "prompt": "x", "outcome": "seen", "step_id": 1}], "bindings": [], "slots": [], "graph": {}, "_order": ["k"], "goal": ""}')
    clear_loop_memory(fixture="sts2", root=tmp_path)
    journal = FactJournal(path=path)
    assert not journal.facts
