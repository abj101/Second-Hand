from __future__ import annotations

import json
from pathlib import Path

from loop.memory.journal import FactJournal, _norm, journal_path


def test_save_load_roundtrip_lock_and_binding(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = FactJournal()
    journal.record_try(
        kind="doorway",
        heading="n",
        prompt="need item",
        glyph="a",
        device="a",
        outcome="lock",
        locked=True,
        step_id=3,
    )
    journal.save(path)

    loaded = FactJournal()
    loaded.load(path)
    assert len(loaded.facts) == 1
    fact = next(iter(loaded.facts.values()))
    assert fact.kind == "doorway"
    assert fact.heading == "n"
    assert fact.outcome == "lock"
    assert fact.device == "a"
    assert fact.glyph == "a"
    assert loaded.bindings
    bind = next(iter(loaded.bindings.values()))
    assert bind.glyph == "a"
    assert bind.device == "a"
    assert bind.outcome == "lock"
    assert loaded._order == journal._order


def test_locked_kinds_and_is_locked() -> None:
    journal = FactJournal()
    journal.observe(
        kind="doorway",
        heading="n",
        prompt="need item",
        outcome="lock",
        locked=True,
        step_id=1,
    )
    journal.observe(kind="wall", heading="s", outcome="blocked", step_id=2)
    journal.observe(kind="golden trail", heading="e", outcome="seen", step_id=3)
    assert journal.locked_kinds() == {"doorway"}
    assert journal.is_locked("doorway") is True
    assert journal.is_locked("doorway", "n") is True
    assert journal.is_locked("doorway", "s") is False
    assert journal.is_locked("wall") is False
    assert journal.is_locked(None) is False


def test_journal_path_under_root(tmp_path: Path) -> None:
    path = journal_path("elden", root=tmp_path)
    assert path == tmp_path / "data" / "loop" / "elden" / "journal.json"


def test_init_loads_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "journal.json"
    src = FactJournal()
    src.observe(
        kind="doorway",
        heading="n",
        prompt="need key",
        outcome="lock",
        locked=True,
        step_id=1,
    )
    src.save(path)
    loaded = FactJournal(path=path)
    assert loaded.is_locked("doorway", "n") is True
    assert "doorway" in loaded.locked_kinds()


def test_lock_autosaves_when_path_set(tmp_path: Path) -> None:
    path = tmp_path / "data" / "loop" / "elden" / "journal.json"
    journal = FactJournal(path=path)
    journal.record_try(
        kind="doorway",
        heading="n",
        prompt="need item",
        glyph="a",
        device="a",
        outcome="lock",
        locked=True,
        step_id=1,
    )
    assert path.exists()
    again = FactJournal(path=path)
    assert again.is_locked("doorway") is True
    assert again.bindings


def test_load_skips_unknown_keys_and_corrupt_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    path.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "key": "doorway|n|lock",
                        "kind": "doorway",
                        "heading": "n",
                        "prompt": "need item",
                        "glyph": None,
                        "device": None,
                        "outcome": "lock",
                        "step_id": 1,
                        "extra": "nope",
                    }
                ],
                "bindings": [],
                "_order": ["doorway|n|lock"],
            }
        ),
        encoding="utf-8",
    )
    journal = FactJournal()
    journal.load(path)
    assert journal.locked_kinds() == {"doorway"}
    fact = journal.facts["doorway|n|lock"]
    assert not hasattr(fact, "extra")

    path.write_text("{not json", encoding="utf-8")
    journal.load(path)
    assert journal.facts == {}
    assert journal.bindings == {}
    assert journal._order == []


def test_record_click_persists_quantized_heading(tmp_path: Path) -> None:
    from loop.memory.journal import click_heading, click_query

    assert click_heading(0.40, 0.70) == "g400,g700"
    assert click_heading(0.90, 0.40) == "right"
    assert click_query(screen="map", x=0.40, y=0.70) == "map g400 g700"

    path = tmp_path / "journal.json"
    journal = FactJournal(path=path)
    fact = journal.record_click(
        screen="map",
        x=0.40,
        y=0.70,
        device="pt_0.40_0.70",
        pixel_changed=False,
        step_id=4,
    )
    assert fact is not None
    assert fact.kind == "map"
    assert fact.heading == "g400,g700"
    assert fact.outcome == "noop"
    assert fact.prompt == ""
    assert not path.exists()

    fact2 = journal.record_click(
        screen="map",
        x=0.40,
        y=0.70,
        device="pt_0.40_0.70",
        pixel_changed=False,
        step_id=5,
    )
    assert fact2 is not None
    assert fact2.outcome == "blocked"
    assert path.exists()

    loaded = FactJournal(path=path)
    hits = loaded.recall("map g400")
    assert hits
    assert "map" in hits[0]
    assert "blocked" in hits[0]


def test_recall_map_cell_ranks_over_combat() -> None:
    journal = FactJournal()
    journal.record_click(
        screen="combat",
        x=0.20,
        y=0.20,
        device="mk_1",
        pixel_changed=True,
        step_id=1,
    )
    journal.record_click(
        screen="map",
        x=0.40,
        y=0.70,
        device="pt_0.40_0.70",
        pixel_changed=False,
        step_id=2,
    )
    ranked = journal.recall("map g400")
    assert ranked
    assert "map" in ranked[0]
    assert "combat" not in ranked[0]


def test_record_click_stores_ui_label_and_hover() -> None:
    journal = FactJournal()
    fact = journal.record_click(
        screen="combat",
        x=0.90,
        y=0.85,
        device="mk_5",
        pixel_changed=True,
        step_id=3,
        label="End Turn",
        hovered=True,
    )
    assert fact is not None
    assert fact.outcome == "tooltip"
    assert fact.prompt == "End Turn"
    hits = journal.recall("end turn")
    assert hits
    assert any("End Turn" in line or "end turn" in line.lower() for line in hits)
    bind_lines = [row.line() for row in journal.bindings.values()]
    assert any("mk_5" in line and "End Turn" in line for line in bind_lines)
    journal.record_click(
        screen="combat",
        x=0.40,
        y=0.40,
        device="mk_4",
        pixel_changed=False,
        step_id=4,
        label="",
    )
    block = journal.prompt_block()
    assert "End Turn" in block
    assert block.index("End Turn") < block.index("mk_4")


def test_remember_note_ranks_in_prompt_block(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = FactJournal(path=path)
    journal.record_click(
        screen="select",
        x=0.32,
        y=0.38,
        device="mk_11",
        pixel_changed=False,
        step_id=2,
        label="Ironclad",
    )
    journal.remember_note("Ironclad portrait has a check; other class icons are locked", step_id=2)
    block = journal.prompt_block()
    assert "Journal:" in block
    assert "note" in block
    assert "check" in block.lower()
    assert "other class icons are locked" in block
    fact_lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
    sensor_i = next(i for i, ln in enumerate(fact_lines) if 'prompt="Ironclad"' in ln)
    note_i = next(i for i, ln in enumerate(fact_lines) if ln.startswith("- note"))
    assert sensor_i < note_i
    loaded = FactJournal(path=path)
    assert any(fact.kind == "note" for fact in loaded.facts.values())


def test_journal_loads_without_provenance_fields(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    path.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "key": "combat|right|end turn",
                        "kind": "combat",
                        "heading": "right",
                        "prompt": "End Turn",
                        "glyph": "mk_5",
                        "device": "mk_5",
                        "outcome": "tooltip",
                        "step_id": 2,
                    }
                ],
                "bindings": [
                    {
                        "key": "stable|combat|right|control",
                        "glyph": "mk_5",
                        "verb": "End Turn",
                        "device": "mk_5",
                        "outcome": "tooltip",
                        "n": 1,
                        "kind": "combat",
                    }
                ],
                "slots": [],
                "graph": {},
                "_order": ["combat|right|end turn"],
                "goal": "win a run",
            }
        ),
        encoding="utf-8",
    )
    journal = FactJournal()
    journal.load(path)
    assert journal.facts
    fact = next(iter(journal.facts.values()))
    assert fact.provenance == ""
    assert fact.confidence == 0.5
    bind = next(iter(journal.bindings.values()))
    assert bind.evidence == "observation"


def test_stable_identity_rejects_contradictory_control_labels() -> None:
    journal = FactJournal()
    journal.record_click(
        screen="combat",
        x=0.90,
        y=0.85,
        device="mk_5",
        pixel_changed=True,
        step_id=1,
        label="End Turn",
        hovered=True,
    )
    for label in ("Back", "Defend", "Confirm"):
        journal.record_click(
            screen="combat",
            x=0.90,
            y=0.85,
            device="mk_5",
            pixel_changed=True,
            step_id=2,
            label=label,
        )
    control_binds = [row for row in journal.bindings.values() if row.role == "control"]
    assert len(control_binds) == 1
    assert _norm(control_binds[0].verb) == "end turn"


def test_card_pair_does_not_create_single_button_binding() -> None:
    journal = FactJournal()
    journal.record_click(
        screen="combat",
        x=0.40,
        y=0.80,
        device="mk_2>mk_11",
        pixel_changed=True,
        step_id=3,
        label="Strike",
        next_screen="combat",
    )
    assert not journal.bindings

