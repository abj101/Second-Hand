from __future__ import annotations

from pathlib import Path

from loop.memory.journal import FactJournal
from loop.memory.notebook import MISSION, Notebook, default_goal, note_copies_thought


def test_default_goal_is_standing_mission() -> None:
    assert default_goal("title") == MISSION
    assert default_goal("select") == MISSION
    assert default_goal("combat") == MISSION
    assert default_goal("tutorial") == MISSION
    assert default_goal("map") == MISSION


def test_notebook_prompt_block_cap_and_pivot(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    journal = FactJournal(path=path)
    notebook = Notebook(journal)
    journal.record_click(
        screen="select",
        x=0.44,
        y=0.88,
        device="mk_4",
        pixel_changed=False,
        step_id=2,
        label="Locked",
    )
    journal.record_click(
        screen="select",
        x=0.44,
        y=0.88,
        device="mk_4",
        pixel_changed=False,
        step_id=3,
        label="Locked",
    )
    journal.record_click(
        screen="title",
        x=0.40,
        y=0.70,
        device="mk_1",
        pixel_changed=True,
        step_id=1,
        label="Singleplayer",
    )
    journal.record_click(
        screen="select",
        x=0.72,
        y=0.86,
        device="mk_6",
        pixel_changed=True,
        step_id=4,
        label="Confirm",
    )
    notebook.set_goal("start a run")
    notebook.ingest(
        {"goal": "start a run", "note": "Ironclad portrait has a check; other class icons are locked"},
        thought="I am testing the Locked badge",
        step_id=3,
        screen="select",
    )
    block = notebook.prompt_block(
        screen="select",
        blocked_hint="BLOCKED: 3x mk_4 no-change — pick a different gx,gy",
    )
    lines = block.splitlines()
    assert lines[0] == "Notebook:"
    assert len(lines) <= 12
    assert any(line == f"Goal: {MISSION}" for line in lines)
    assert not any("Confirm the unlocked portrait" in line for line in lines if line.startswith("Goal:"))
    assert any(line.startswith("PIVOT:") for line in lines)
    assert not any(line.startswith("Goal: PIVOT:") for line in lines)
    assert "mk_4" in block
    assert "Last:" in block
    assert "Known:" in block
    assert any(line.startswith("Hunch:") and "check" in line.lower() for line in lines)
    loaded = Notebook(FactJournal(path=path))
    assert loaded.journal.goal == MISSION
    assert any(fact.kind == "note" for fact in loaded.journal.facts.values())


def test_ingest_ignores_empty_and_thought_copy(tmp_path: Path) -> None:
    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    assert notebook.ingest({"goal": "", "note": ""}, thought="testing", step_id=1) is None
    assert notebook.ingest({"note": "testing the badge"}, thought="testing the badge", step_id=1) is None
    assert not any(fact.kind == "note" for fact in journal.facts.values())
    assert notebook.ingest(
        {"goal": "Confirm Ironclad", "note": "lower-right pennant is Confirm on select"},
        thought="click the check",
        step_id=2,
    )
    assert journal.goal == MISSION
    assert any("Confirm" in fact.prompt for fact in journal.facts.values() if fact.kind == "note")
    assert note_copies_thought("  Foo  ", "foo")
    locked_note = notebook.ingest(
        {"note": "mk_14 is character on locked card"},
        thought="click play",
        step_id=4,
    )
    assert locked_note
    keys = [fact.key for fact in journal.facts.values() if fact.kind == "note"]
    assert any("locked card" in key for key in keys)
    assert not any(key.endswith("|lock") for key in keys)


def test_known_skips_blocked_and_ingest_rejects_poison(tmp_path: Path) -> None:
    from loop.labels import bind_verb_ok, progress_index
    from loop.memory.notebook import note_disagrees_aim, note_is_schema_example

    assert progress_index("Singleplayer") is not None
    assert progress_index("Play") is not None
    assert progress_index("Play a single run with ??? to unlock character") is None
    assert bind_verb_ok("Singleplayer")
    assert not bind_verb_ok("character")
    assert not bind_verb_ok("7727")
    assert not bind_verb_ok("Locked")
    assert note_is_schema_example("mk_1 is Singleplayer on title")
    assert note_disagrees_aim(
        "mk_1 is Singleplayer on title",
        "1@223,396:Locked 15@159,505:Play",
    )
    assert note_disagrees_aim(
        "mk_15 is Got it on Enable Tutorials?",
        "15@476,634:tut 5@321,862:icon 6@540,858:Yes",
    )

    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    journal.record_click(
        screen="select",
        x=0.40,
        y=0.50,
        device="mk_14",
        pixel_changed=False,
        step_id=1,
        label="character",
    )
    journal.record_click(
        screen="select",
        x=0.15,
        y=0.50,
        device="mk_15",
        pixel_changed=False,
        step_id=2,
        label="Play",
    )
    journal.record_click(
        screen="title",
        x=0.40,
        y=0.70,
        device="mk_1",
        pixel_changed=True,
        step_id=3,
        label="Singleplayer",
    )
    journal.record_click(
        screen="select",
        x=0.72,
        y=0.86,
        device="mk_6",
        pixel_changed=True,
        step_id=4,
        label="Confirm",
    )
    block = notebook.prompt_block(screen="select")
    assert "Known:" in block
    known = next(line for line in block.splitlines() if line.startswith("Known:"))
    assert "blocked" not in known
    assert "Confirm" in known
    assert "Singleplayer" not in known
    assert "character" not in known.lower()
    assert notebook.ingest(
        {"note": "mk_1 is Singleplayer on title"},
        thought="start",
        step_id=4,
        aim_listing="1@223,396:Locked",
    ) is None
    assert notebook.ingest(
        {"note": "mk_1 is Singleplayer on title"},
        thought="start",
        step_id=4,
    ) is None


def test_ingest_freezes_unlock_goal_and_drops_play_note(tmp_path: Path) -> None:
    from loop.memory.journal import Binding
    from loop.memory.notebook import avoid_devices

    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    notebook.set_goal("Confirm the unlocked portrait")
    listing = "11@400,500:Ironclad 13@440,880:Locked 14@160,500:Play 6@960,724:Confirm"
    assert notebook.ingest(
        {"goal": "unlock the character", "note": "mk_14 is Play/Unlock on character select"},
        thought="click play",
        step_id=5,
        screen="select",
        aim_listing=listing,
    ) is None
    assert journal.goal == MISSION
    assert not any(fact.kind == "note" for fact in journal.facts.values())
    assert notebook.ingest(
        {"goal": "Confirm Ironclad", "note": "lower-right pennant is Confirm"},
        thought="click the check",
        step_id=6,
        screen="select",
        aim_listing=listing,
    )
    assert journal.goal == MISSION
    journal.bindings["ui|select|mk_14|play"] = Binding(
        key="ui|select|mk_14|play",
        glyph="mk_14",
        verb="Play",
        device="mk_14",
        outcome="blocked",
        n=8,
        kind="select",
    )
    journal.bindings["ui|select|mk_5|icon"] = Binding(
        key="ui|select|mk_5|icon",
        glyph="mk_5",
        verb="icon",
        device="mk_5",
        outcome="moved",
        n=4,
        kind="select",
    )
    assert "mk_14" in avoid_devices(journal, "select")
    assert "mk_14" not in avoid_devices(journal, "combat")
    block = notebook.prompt_block(screen="select")
    known = next((line for line in block.splitlines() if line.startswith("Known:")), "")
    assert "icon" not in known.lower()


def test_overlay_cannot_steal_goal(tmp_path: Path) -> None:
    from loop.memory.notebook import overlay_bind_note, overlay_default_goal, overlay_screen

    listing = "5@321,862:No 6@540,858:Yes 15@476,634:icon"
    assert overlay_screen("other", listing) == "tutorial"
    assert overlay_bind_note("3@500,160:Close", 3, "Close") == "mk_3 is Close on overlay"
    assert overlay_default_goal("3@500,160:Close") == "click :Close on the darkened backdrop"
    assert overlay_default_goal("1@80,500:icon 2@920,500:icon 3@500,160:Close") is None
    assert overlay_screen("combat", listing) == "combat"
    assert overlay_bind_note(listing, 5, "No") == "mk_5 is No on dialog"
    assert overlay_bind_note(listing, 15, "Got it") is None
    assert overlay_bind_note("9@80,60:Map", 9, "Map") == "mk_9 is Map on hud"

    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    notebook.set_goal("dismiss overlay")
    assert notebook.ingest(
        {"goal": "click Got it", "note": "mk_15 is Got it on Enable Tutorials"},
        thought="click Got it",
        step_id=10,
        screen="other",
        aim_listing=listing,
    ) is None
    assert journal.goal == MISSION
    assert not any(fact.kind == "note" for fact in journal.facts.values())
    assert notebook.ingest(
        {"note": "Got it dismisses Enable Tutorials"},
        thought="click the popup",
        step_id=11,
        aim_listing=listing,
    ) is None

    saved = notebook.bind_overlay(mark_id=5, label="No", listing=listing, step_id=12)
    assert saved == "mk_5 is No on dialog"
    assert notebook.bind_overlay(mark_id=5, label="No", listing=listing, step_id=13) is None
    journal.record_click(
        screen="tutorial",
        x=0.32,
        y=0.86,
        device="mk_5",
        pixel_changed=True,
        step_id=12,
        label="No",
    )
    block = notebook.prompt_block(screen="tutorial", aim_listing=listing)
    assert any(line == f"Goal: {MISSION}" for line in block.splitlines())
    assert any(line.startswith("Hint: click a printed :Yes or :No") for line in block.splitlines())
    assert "mk_5 is No on dialog" in block
    known = next(line for line in block.splitlines() if line.startswith("Known:"))
    assert "No" in known
    assert "Got it" not in block


def test_arrows_close_listing_does_not_set_goal(tmp_path: Path) -> None:
    from loop.memory.notebook import overlay_default_goal

    arrows = "1@80,500:icon 2@920,500:icon 3@500,160:Close"
    assert overlay_default_goal(arrows) is None
    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    notebook.set_goal("click :Close on the darkened backdrop")
    block = notebook.prompt_block(screen="tutorial", aim_listing=arrows)
    goal = next(line for line in block.splitlines() if line.startswith("Goal:"))
    assert goal == f"Goal: {MISSION}"
    assert "Close" not in goal
    assert not any(line.startswith("Hint:") and "Close" in line for line in block.splitlines())


def test_goal_stays_mission_across_screens(tmp_path: Path) -> None:
    from loop.memory.notebook import note_disagrees_aim, overlay_screen, resolve_goal

    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    notebook.set_goal("rest")
    combat_listing = "6@900,920:End_Turn 4@400,800:icon"
    assert overlay_screen("other", combat_listing) == "combat"
    assert resolve_goal("rest", screen="combat") == MISSION
    block = notebook.prompt_block(screen="combat", aim_listing=combat_listing)
    assert f"Goal: {MISSION}" in block
    assert "rest" not in block.lower().split("goal:", 1)[-1].splitlines()[0]
    assert journal.goal == MISSION
    assert "Focus:" in block
    assert notebook.ingest(
        {"goal": "select Strike card", "note": "mk_4 is Strike"},
        thought="play a card",
        step_id=2,
        screen="combat",
        aim_listing=combat_listing,
    ) is None
    assert journal.goal == MISSION
    notebook.ingest(
        {"goal": "rest", "note": "campfire heals"},
        thought="go rest",
        step_id=3,
        screen="combat",
        aim_listing=combat_listing,
    )
    assert journal.goal == MISSION
    notebook.set_goal("rest")
    still = notebook.prompt_block(screen="map")
    assert f"Goal: {MISSION}" in still
    notebook.prompt_block(screen="other")
    assert journal.goal == MISSION
    notebook.set_goal("return to map")
    notebook.prompt_block(screen="combat", aim_listing=combat_listing)
    assert journal.goal == MISSION
    notebook.set_goal("read tooltip")
    notebook.prompt_block(screen="combat")
    assert journal.goal == MISSION
    assert notebook.ingest(
        {"goal": "read tooltip", "note": "mk_7 is potion icon"},
        thought="hover the badge",
        step_id=4,
        screen="combat",
        aim_listing=combat_listing,
    ) is None
    assert journal.goal == MISSION
    assert not any(
        "potion" in (fact.prompt or "")
        for fact in journal.facts.values()
        if fact.kind == "note"
    )
    assert note_disagrees_aim(
        "mk_4 is potion icon",
        combat_listing,
    )
    assert not note_disagrees_aim("mk_6 is End Turn", combat_listing)


def test_raw_hud_dump_rejected_as_lesson_and_note(tmp_path: Path) -> None:
    from loop.memory.notebook import lesson_is_sane

    raw = "hp=68/80 energy=3 block=0 floor=1 screen=combat choice=Strike"
    assert not lesson_is_sane(raw)
    journal = FactJournal(path=tmp_path / "journal.json")
    notebook = Notebook(journal)
    notebook.write_lesson(raw)
    assert not notebook.grounded.lessons
    assert notebook.ingest({"note": raw}, thought="copy hud", step_id=1, screen="combat") is None
