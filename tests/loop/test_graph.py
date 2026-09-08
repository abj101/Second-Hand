from __future__ import annotations

from pathlib import Path

from loop.memory.graph import PlayGraph, pick_focus
from loop.memory.journal import FactJournal
from loop.memory.notebook import MISSION, Notebook


def test_tutorial_ocr_becomes_claim() -> None:
    graph = PlayGraph()
    added = graph.ingest_ocr(
        "tutorial",
        "Energy is spent when you play a card. Block reduces incoming damage. Yes",
    )
    assert added >= 1
    texts = " ".join(row.text.lower() for row in graph.claims.values())
    assert "energy is spent" in texts
    assert "yes" not in {row.text.lower() for row in graph.claims.values()}
    combat = graph.slice("combat", None, cap=6)
    assert any(line.startswith("Lesson:") and "energy" in line.lower() for line in combat)


def test_click_edge_tutorial_to_map(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = FactJournal(path=path)
    fact = journal.record_click(
        screen="tutorial",
        x=0.54,
        y=0.86,
        device="mk_6",
        pixel_changed=True,
        step_id=4,
        label="Got it",
        next_screen="map",
    )
    assert fact is not None
    assert fact.outcome == "moved"
    edges = list(journal.graph.edges.values())
    assert edges
    assert any(row.src == "tutorial" and row.dst == "map" for row in edges)
    loaded = FactJournal(path=path)
    assert loaded.graph.edges
    assert any(row.dst == "map" for row in loaded.graph.edges.values())
    line = loaded.graph.best_edge_line("tutorial")
    assert "map" in line


def test_focus_clears_on_screen_change(tmp_path: Path) -> None:
    notebook = Notebook(FactJournal(path=tmp_path / "journal.json"))
    notebook.focus = "hover unlabeled mk_1"
    notebook.observe_step(
        screen="map",
        prev_screen="tutorial",
        reward=0.0,
        pixel_changed=True,
    )
    assert notebook.focus == ""


def test_focus_clears_on_reward_or_new_fact(tmp_path: Path) -> None:
    notebook = Notebook(FactJournal(path=tmp_path / "journal.json"))
    notebook.focus = "click printed :Yes"
    notebook.observe_step(
        screen="tutorial",
        prev_screen="tutorial",
        reward=1.0,
        pixel_changed=False,
    )
    assert notebook.focus == ""
    notebook.focus = "click printed :Got it"
    notebook.observe_step(
        screen="tutorial",
        prev_screen="tutorial",
        reward=0.0,
        pixel_changed=False,
        new_fact=True,
    )
    assert notebook.focus == ""


def test_focus_pivot_after_repeat_noop(tmp_path: Path) -> None:
    notebook = Notebook(FactJournal(path=tmp_path / "journal.json"))
    notebook.focus = "click printed :Yes"
    for _ in range(3):
        notebook.observe_step(
            screen="tutorial",
            prev_screen="tutorial",
            pixel_changed=False,
        )
    assert notebook.focus == ""
    block = notebook.prompt_block(screen="tutorial", aim_listing="6@540,858:Yes")
    assert any(line.startswith("PIVOT:") for line in block.splitlines())
    assert f"Goal: {MISSION}" in block


def test_pick_focus_progress_then_unseen() -> None:
    graph = PlayGraph()
    graph.record_edge(
        src="tutorial",
        dst="map",
        heading="g540,g860",
        label="Got it",
        outcome="moved",
    )
    listing = "6@540,858:Got_it 15@80,500:icon"
    assert pick_focus(graph, screen="tutorial", aim_listing=listing, hovered=()) == (
        "click printed :Got it"
    )
    unlabeled = "1@80,500:icon 2@920,500:icon 6@540,858:Yes"
    assert pick_focus(PlayGraph(), screen="tutorial", aim_listing=unlabeled) == (
        "click printed :Yes"
    )
    named = "5@321,862:No 6@540,858:Yes"
    assert pick_focus(PlayGraph(), screen="tutorial", aim_listing=named) == "click printed :Yes"
    combat = "3@400,850:Strike 8@820,880:End_Turn"
    assert pick_focus(
        PlayGraph(), screen="combat", aim_listing=combat, energy=3
    ) == "click printed :Strike"
    assert "End Turn" not in pick_focus(
        PlayGraph(), screen="combat", aim_listing=combat, energy=3
    )
    assert pick_focus(
        PlayGraph(), screen="combat", aim_listing=combat, energy=0
    ) == "click printed :End Turn"


def test_ocr_claims_persist_in_journal(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = FactJournal(path=path)
    n = journal.ingest_ocr(
        "tutorial",
        "Energy is spent when you play a card. Cards go to the discard pile.",
    )
    assert n >= 1
    loaded = FactJournal(path=path)
    texts = " ".join(row.text.lower() for row in loaded.graph.claims.values())
    assert "energy is spent" in texts
    notebook = Notebook(loaded)
    block = notebook.prompt_block(screen="combat")
    assert f"Goal: {MISSION}" in block
    assert any("Lesson:" in line for line in block.splitlines())
