from __future__ import annotations

from loop.memory.episodic import WorkingMemory


def test_working_memory_recalls_overlapping_glyphs() -> None:
    mem = WorkingMemory(window=8, rag_k=2)
    mem.add(chord="stick_l_n", pixels=False, glyphs=[], note="walk")
    mem.add(chord="stick_l_n", pixels=False, glyphs=["x"], note="saw pillage x but walked")
    mem.add(chord="x", pixels=True, glyphs=["x"], note="tapped x on prompt", mad=0.04, prompt=True)
    hits = mem.recall(["x"], "stick_l_n")
    assert hits
    assert any("x" in row for row in hits)
    block = mem.prompt_block(glyphs=["x"], chord="stick_l_n")
    assert "Working memory:" in block
    assert "pillage" not in block
    assert "walk" not in block
    assert "Lessons:" in block or "Similar traces:" in block


def test_working_memory_ignores_thought_glyphs_without_prompt() -> None:
    mem = WorkingMemory(window=8)
    mem.add(chord="y", pixels=True, glyphs=[], mad=0.03, prompt=False)
    last = mem.last()
    assert last is not None
    assert last.prompt is False
    assert last.glyphs == ()
    block = mem.prompt_block()
    assert "glyphs=-" in block
    assert " prompt" not in block


def test_condense_mentions_unused_glyphs() -> None:
    mem = WorkingMemory(window=8)
    for _ in range(8):
        mem.add(chord="stick_l_n", pixels=False, glyphs=["x"], note="pillage")
    assert "unused_glyphs" in mem.lessons
    assert "x" in mem.lessons


def test_blocked_hint_after_still_walks() -> None:
    mem = WorkingMemory(window=12)
    for _ in range(4):
        mem.add(chord="stick_l_n", pixels=False, glyphs=[], mad=0.002, prompt=False)
    hint = mem.blocked_hint()
    assert "BLOCKED" in hint
    assert "stick_l_s" in hint
    block = mem.prompt_block()
    assert "BLOCKED" in block
    # Motion without a prompt should not outrank a real prompt tap in RAG.
    mem.add(chord="b", pixels=True, glyphs=["b"], mad=0.05, prompt=False)
    mem.add(chord="a", pixels=True, glyphs=["a"], mad=0.02, prompt=True)
    mem.add(chord="stick_l_e", pixels=True, glyphs=[], mad=0.03, prompt=False)
    hits = mem.recall(["a", "b"], "a")
    assert hits
    assert hits[0].startswith("a ")


def test_journal_keys_lock_prompt_not_thoughts() -> None:
    from avatar.gsm.rule.detector import DetectedBox
    from loop.log.schema import ActionLog
    from loop.memory.journal import FactJournal, face_device, heading_from_boxes, kind_from_boxes

    journal = FactJournal()
    box = DetectedBox(label="doorway", x=0.45, y=0.30, w=0.20, h=0.25, score=0.6)
    kind = kind_from_boxes([box])
    heading = heading_from_boxes([box], last_walk="n")
    assert kind == "doorway"
    assert heading == "n"
    journal.observe(
        kind=kind or "prompt",
        heading=heading,
        prompt="This door opens only to those carrying a finger",
        glyph="a",
        outcome="lock",
        locked=True,
        step_id=1,
    )
    journal.observe(
        kind="doorway",
        heading="n",
        prompt="ONLY THOSE WHO CARRY",
        glyph="a",
        outcome="seen",
        locked=True,
        step_id=2,
    )
    assert len(journal.facts) == 1
    journal.record_try(
        kind="doorway",
        heading="n",
        prompt="This door opens only to those carrying a finger",
        glyph="a",
        device="a",
        outcome="lock",
        locked=True,
        step_id=3,
    )
    block = journal.prompt_block()
    assert "Journal:" in block
    assert "heading=n" in block
    assert "lock" in block
    assert "Bindings:" in block
    assert "pillage" not in block
    hits = journal.recall("doorway a")
    assert hits
    assert any("doorway" in row for row in hits)
    empty = journal.recall("")
    assert empty
    assert face_device(ActionLog(tapped=["a"], duration_ms=400)) == "a"
    assert face_device(ActionLog(stick_l="n", duration_ms=400)) is None


def test_journal_ranks_lock_over_seen() -> None:
    from loop.memory.journal import FactJournal

    journal = FactJournal()
    journal.observe(kind="golden trail", heading="e", outcome="seen", step_id=1)
    journal.observe(kind="doorway", heading="n", prompt="need item", outcome="lock", locked=True, step_id=2)
    ranked = journal._ranked_facts()
    assert ranked[0].outcome == "lock"
    assert "need" in journal.prompt_block() or "lock" in journal.prompt_block()


def test_face_try_outcome_flicker_is_jump() -> None:
    from loop.memory.journal import face_try_outcome

    assert (
        face_try_outcome(
            prompt_on=True,
            prompt_cleared=True,
            prompt_after=False,
            confirmed=True,
            pixel_mad=0.006,
            view_changed=False,
        )
        == "jump"
    )
    assert (
        face_try_outcome(
            prompt_on=True,
            prompt_cleared=True,
            prompt_after=False,
            confirmed=False,
            pixel_mad=0.08,
            view_changed=True,
        )
        == "jump"
    )
    assert (
        face_try_outcome(
            prompt_on=True,
            prompt_cleared=True,
            prompt_after=False,
            confirmed=True,
            pixel_mad=0.08,
            view_changed=True,
        )
        == "cleared"
    )
    assert (
        face_try_outcome(
            prompt_on=True,
            prompt_cleared=False,
            prompt_after=True,
            confirmed=True,
            pixel_mad=0.01,
            view_changed=False,
        )
        == "lock"
    )


def test_pose_round_trip_bumps_origin_visits() -> None:
    mem = WorkingMemory(window=12)
    for _ in range(4):
        mem.add(chord="stick_l_n", pixels=True)
    origin_after_north = mem.pose.visits[(0, 0)]
    for _ in range(4):
        mem.add(chord="stick_l_s", pixels=True)
    assert mem.pose.visits[(0, 0)] > origin_after_north
    assert mem.pose.visits[(0, 0)] >= 2


def test_n_a_cycle_eventually_revisits() -> None:
    mem = WorkingMemory(window=12)
    chords = ["stick_l_n", "a", "stick_l_n", "a", "stick_l_n"]
    hint = ""
    block = ""
    for chord in chords:
        mem.add(chord=chord, pixels=True)
        hint = mem.cycle_hint()
        block = mem.prompt_block()
        if "REVISIT" in hint or "REVISIT" in block:
            break
    assert "REVISIT" in hint or "REVISIT" in block


def test_blocked_hint_still_stick_l_n() -> None:
    mem = WorkingMemory(window=12)
    for _ in range(4):
        mem.add(chord="stick_l_n", pixels=False, glyphs=[], mad=0.002, prompt=False)
    hint = mem.blocked_hint()
    assert "BLOCKED" in hint
    assert "stick_l_n" in hint
    assert "BLOCKED" in mem.prompt_block()


def test_blocked_hint_after_still_clicks() -> None:
    mem = WorkingMemory(window=12)
    for _ in range(4):
        mem.add(chord="pt_0.39_0.71", pixels=False, glyphs=[], mad=0.001, prompt=False)
    hint = mem.blocked_hint()
    assert "BLOCKED" in hint
    assert "pt_0.39_0.71" in hint
    assert "labeled mark" in hint
    assert "BLOCKED" in mem.prompt_block()
    aimed = mem.blocked_hint(selected="Strike")
    assert "Strike" in aimed
    assert "pick a different gx,gy" not in aimed
    assert "enemy" in aimed.lower()


def test_identical_nochange_streak() -> None:
    mem = WorkingMemory(window=12)
    assert mem.identical_nochange_streak() == (0, "")
    mem.add(chord="mk_4", pixels=False, mad=0.001)
    assert mem.identical_nochange_streak() == (0, "")
    mem.add(chord="mk_4", pixels=False, mad=0.001)
    streak, chord = mem.identical_nochange_streak()
    assert streak == 2
    assert chord == "mk_4"
    assert "BLOCKED" in mem.blocked_hint()

