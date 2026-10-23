import json

import pytest

from agent.notebook import MAX_OPEN, Notebook


def notebook(tmp_path, **kwargs):
    return Notebook(tmp_path, **kwargs)


def add(key, *, target=None):
    value = {"op": "add", "key": key, "text": f"Quest {key}", "why": ["t1"]}
    if target is not None: value["target"] = target
    return value


def closed_setup():
    return [add("q"), {"op": "drop", "key": "q", "reason": "No longer needed"}]


CASES = [
    ("non-object", [], None, "op must be an object"),
    ("unknown-op", [], {"op": "other", "key": "q"}, "unknown op"),
    ("missing-op", [], {"key": "q"}, "missing op"),
    ("missing-key", [], {"op": "top"}, "missing key"),
    ("long-key", [], {"op": "top", "key": "q" * 41}, "key exceeds 40 characters"),
    ("add-ok", [], add("q"), None),
    ("add-key-used", closed_setup(), add("q"), "key is already used"),
    ("add-empty-text", [], {**add("q"), "text": "  "}, "text is empty"),
    ("add-empty-why", [], {**add("q"), "why": []}, "why must cite game-text keys, newest: t1"),
    ("add-unknown-why", [], {**add("q"), "why": ["missing"]}, "why must cite game-text keys, newest: t1"),
    ("add-open-cap", [add(f"q{i}") for i in range(MAX_OPEN)], add("extra"), "maximum open quests reached"),
    ("add-unresolved-target", [], {**add("q"), "target": "unknown"}, None),
    ("reopen-not-closed", [add("q")], {"op": "reopen", "key": "q", "reason": "Needed again"}, "key is not closed"),
    ("reopen-ok", closed_setup(), {"op": "reopen", "key": "q", "reason": "Needed again"}, None),
    ("done-not-open", [], {"op": "done", "key": "q", "reason": "Finished"}, "q is not a quest yet; add it first"),
    ("done-closed", closed_setup(), {"op": "done", "key": "q", "reason": "Finished"}, "q is closed (dropped at step 2); reopen it first"),
    ("done-ok", [add("q")], {"op": "done", "key": "q", "reason": "Found the archive"}, None),
    ("done-empty-reason", [add("q")], {"op": "done", "key": "q", "reason": " "}, "reason is empty"),
    ("done-tkeys-why", [add("q")], {"op": "done", "key": "q", "why": ["t1"]}, "reason is empty (a sentence, not t-keys)"),
    ("done-sentence-why", [add("q")], {"op": "done", "key": "q", "why": "The archive was found."}, None),
    ("drop-not-open", [], {"op": "drop", "key": "q", "reason": "Finished"}, "q is not a quest yet; add it first"),
    ("drop-ok", [add("q")], {"op": "drop", "key": "q", "reason": "Changed course"}, None),
    ("drop-empty-reason", [add("q")], {"op": "drop", "key": "q", "reason": " "}, "reason is empty"),
    ("top-not-open", [], {"op": "top", "key": "q"}, "q is not a quest yet; add it first"),
    ("top-ok", [add("q"), add("next")], {"op": "top", "key": "q"}, None),
    ("target-not-open", [], {"op": "target", "key": "q", "target": "place:room"}, "q is not a quest yet; add it first"),
    ("target-missing", [add("q")], {"op": "target", "key": "q"}, "target is missing"),
    ("target-unresolved", [add("q")], {"op": "target", "key": "q", "target": "unknown"}, 'target "unknown" matches no known place or thing; no known places or things yet'),
    ("target-ok", [add("q")], {"op": "target", "key": "q", "target": "place:room"}, None),
    ("target-outside-kind", [add("q")], {"op": "target", "key": "q", "target": "exit:door"}, 'target "exit:door" matches no known place or thing; no known places or things yet'),
    ("target-kind-may-change", [add("q", target="place:room")], {"op": "target", "key": "q", "target": "thing:guide"}, None),
]


@pytest.mark.parametrize("name,setup,candidate,error", CASES, ids=[row[0] for row in CASES])
def test_op_validation_table_folds_only_successful_ops(tmp_path, name, setup, candidate, error):
    resolve = lambda value: value if isinstance(value, str) and value.startswith(("place:", "thing:")) else None
    nb = notebook(tmp_path / "actual", ref=resolve)
    expected = notebook(tmp_path / "expected", ref=resolve)
    for current in (nb, expected): current.heard(1, "Guide", "Hall", "Take the silver key.")
    if setup:
        assert all(row["result"] == "ok" for row in nb.apply_ops(setup, 2))
        assert all(row["result"] == "ok" for row in expected.apply_ops(setup, 2))

    result = nb.apply_ops([candidate], 3)
    if error is None:
        assert result == [{"op": candidate["op"], "key": candidate["key"], "result": "ok"}]
        assert expected.apply_ops([candidate], 3)[0]["result"] == "ok"
    else:
        assert result[0]["result"] == f"error: {error}"
    assert {key: nb.data[key] for key in ("text", "quests")} == {
        key: expected.data[key] for key in ("text", "quests")
    }


def test_drop_then_add_at_open_cap_in_one_reply(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value if value.startswith(("place:", "thing:")) else None)
    nb.heard(1, None, "Hall", "A door opens.")
    setup = [add(f"q{i}") for i in range(MAX_OPEN)]
    assert all(row["result"] == "ok" for row in nb.apply_ops(setup, 2))

    result = nb.apply_ops([{"op": "drop", "key": "q0", "reason": "Found another route"}, add("replacement")], 3)

    assert [row["result"] for row in result] == ["ok", "ok"]
    assert nb.focus == "q1"
    assert next(q for q in nb.data["quests"] if q["key"] == "replacement")["status"] == "open"


@pytest.mark.parametrize("op,status", [("done", "done"), ("drop", "dropped")])
def test_done_and_drop_use_sentence_why_as_reason(tmp_path, op, status):
    nb = notebook(tmp_path)
    nb.heard(1, None, "Hall", "A clue.")
    nb.apply_ops([add("q")], 2)

    result = nb.apply_ops([{"op": op, "key": "q", "why": "Found another way forward."}], 3)

    assert result[0]["result"] == "ok"
    assert nb.data["quests"][0]["status"] == status
    assert nb.data["quests"][0]["reason"] == "Found another way forward."


def test_add_resolves_legend_target_from_current_world_view(tmp_path):
    from agent.memory import Memory
    from agent.world import World

    mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place",
                           "first": 1, "last": 1, "visits": 1, "edges": []})
    for ident, name, tile in (("a", "Object A", [-2, 0]), ("b", "Object B", [-1, 0]), ("c", "Object C", [0, 0])):
        mem.upsert("entities", {"id": ident, "name": name, "kind": "object", "place": "room", "tile": tile,
                               "seen": 1, "tier": "interest", "status": "remembered"})
    world = World(mem, 16)
    world.view("", "room")
    nb = notebook(tmp_path / "notebook", ref=world.ref)
    nb.heard(1, None, "Room", "Go to the third object.")

    result = nb.apply_ops([add("q", target="Thing C")], 2)

    assert result == [{"op": "add", "key": "q", "result": "ok"}]
    assert nb.data["quests"][0]["target"] == "thing:c"
    assert nb.apply_ops([add("unknown", target="Thing Z")], 3)[0]["result"] == "ok"
    assert nb.data["quests"][-1]["target"] is None


def test_bad_reply_shapes_and_ninth_op_are_saved(tmp_path):
    nb = notebook(tmp_path)
    nb.heard(1, "Guide", "Hall", "Wait here.")
    for ops, expected in ((None, "error: ops must be a list"), ([None], "error: op must be an object"),
                          ([{"op": "top", "key": f"q{i}"} for i in range(9)], "error: not applied: more than 8 ops")):
        result = nb.apply_ops(ops, 4, "preserve this reason")
        assert result[-1]["result"] == expected
        assert nb.data["last"] == {"step": 4, "why": "preserve this reason", "results": result}
        history = [json.loads(line) for line in nb.history.read_text(encoding="utf-8").splitlines()]
        assert history[-1] == {"step": 4, "op": "last", "value": nb.data["last"]}


def test_heard_uses_sequential_keys_and_preserves_game_text(tmp_path):
    nb = notebook(tmp_path)

    assert nb.heard(7, "Mira", "Old Tower", 'She said, "Leave now."') == "t1"
    assert nb.heard(8, None, "Old Tower", "  Keep every space.  ") == "t2"
    assert nb.data["text"] == [
        {"key": "t1", "step": 7, "speaker": "Mira", "where": "Old Tower", "text": 'She said, "Leave now."', "who": None},
        {"key": "t2", "step": 8, "speaker": None, "where": "Old Tower", "text": "  Keep every space.  ", "who": None},
    ]


def test_observe_closes_canonical_place_on_arrival_and_thing_only_via_heard(tmp_path):
    refs = {"Library": "place:old", "Mira": "thing:merged"}
    canon = {"place:old": "place:new", "thing:merged": "thing:mira"}
    nb = notebook(tmp_path, ref=refs.get, canon=lambda ref: canon.get(ref, ref))
    nb.heard(1, "Mira", "Library", "Meet me there.", who="thing:merged")
    assert all(row["result"] == "ok" for row in nb.apply_ops([
        add("place", target="Library"), add("person", target="Mira"), add("none"),
    ], 2))

    assert nb.observe(2, "place:hall") == []
    assert nb.observe(3, "place:old") == ["place"]
    assert nb.data["quests"][0]["reason"] == "reached" and nb.data["quests"][0]["by"] == "observe"
    assert nb.observe(4, "place:new") == []
    assert nb.heard(5, "Mira", "Library", "Here I am.", who="thing:merged") == "t2"
    assert nb.data["quests"][1]["status"] == "open"
    assert nb.data["text"][-1]["text"] == "Here I am." and nb.data["text"][-1]["who"] == "thing:mira"
    assert nb.observe(6, "place:new") == []
    assert nb.data["quests"][2]["status"] == "open"


def test_render_count_caps_cited_history_and_character_size(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value if value.startswith(("place:", "thing:")) else None)
    for i in range(20): nb.heard(i, "Guide", "Hall", f"Line {i}. " + "x" * 500)
    for i in range(7):
        assert all(row["result"] == "ok" for row in nb.apply_ops([
            add(f"closed{i}"), {"op": "drop", "key": f"closed{i}", "reason": f"Reason {i}"}
        ], 30 + i * 2))
    assert nb.apply_ops([add("open", target="place:hall")], 50)[0]["result"] == "ok"
    rendered = nb.render()

    assert len([q for q in nb.data["quests"] if q["status"] in ("done", "dropped")][-5:]) == 5
    assert rendered.count("Recently closed:") == 1 and '- closed0 "' not in rendered and '- closed6 "' in rendered
    assert "Held beliefs:" not in rendered and "Ruled out:" not in rendered
    assert "t1 Guide at Hall: \"Line 0. " + "x" * 500 + "\"" in rendered
    assert "t12 Guide" not in rendered and all(f"t{i} Guide" in rendered for i in range(13, 21))
    assert len(rendered) <= 3500


def test_render_shows_a_long_recent_line_whole_within_budget(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value if value.startswith(("place:", "thing:")) else None)
    for i in range(5): nb.heard(i, "Guide", "Hall", f"Short {i}.")
    nb.heard(5, "Guide", "Hall", "Cutscene " + "x" * 505)
    for i in range(6, 8): nb.heard(i, "Guide", "Hall", f"Short {i}.")
    rendered = nb.render()

    assert 't6 Guide at Hall: "Cutscene ' + "x" * 505 + '"' in rendered
    assert all(f"t{i} Guide" in rendered for i in range(1, 9))


def test_observe_ignores_thing_quest_until_heard_from_target(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None)
    nb.heard(1, "Mira", "Hall", "Hi.", who="thing:mira")
    assert nb.apply_ops([add("q", target="thing:mira")], 2)[0]["result"] == "ok"
    assert nb.observe(2, "place:hall") == []
    assert nb.heard(3, None, "Hall", "Hmm.") == "t2"
    assert nb.heard(3, "Guard", "Hall", "Halt.", who="thing:guard") == "t3"
    assert nb.observe(3, "place:hall") == [] and nb.data["quests"][0]["status"] == "open"

    nb.heard(4, "Mira", "Hall", "Hello.", who="thing:mira")

    assert nb.data["quests"][0]["status"] == "open"
    assert nb.data["text"][-1]["text"] == "Hello." and nb.data["text"][-1]["who"] == "thing:mira"
    assert nb.observe(4, "place:hall") == []
    assert nb.observe(5, "place:hall") == []


def test_place_quest_targeted_while_in_place_closes_only_on_arrival(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None)
    nb.heard(1, None, "Hall", "Go on.")
    nb.observe(1, "place:hall")
    assert nb.apply_ops([add("q", target="place:hall")], 2)[0]["result"] == "ok"

    assert nb.observe(2, "place:hall") == []
    assert nb.observe(3, "place:kitchen") == []
    assert nb.observe(4, "place:hall") == ["q"]
    assert nb.data["quests"][0]["reason"] == "reached"


@pytest.mark.parametrize(("target", "name", "done_when"), [
    ("thing:person", "Mira", "target: Mira; close this quest with done when it is achieved"),
    ("thing:exit", "doorway", "you go through doorway"),
    ("thing:building", "large house", "you enter large house"),
    ("place:hall", "Hall", "you enter Hall"),
])
def test_render_focus_labels_target_and_limits_cited_text(tmp_path, target, name, done_when):
    nb = notebook(tmp_path, ref=lambda value: value, is_exit=lambda ref: ref == "thing:exit",
                  leads_to=lambda ref: ["place:inside"] if ref == "thing:building" else [])
    cited = "Find the archive. " + "x" * 220
    nb.heard(1, "Mira", "Hall", cited)
    nb.heard(2, "Guide", "Hall", "Uncited text.")
    nb.apply_ops([{**add("q", target=target), "why": ["t1"]}], 4, "Secret planner why")
    nb.note(5, "Recent note")

    rendered = nb.render_focus(lambda ref: name)
    because = rendered.split("Because:\n", 1)[1].split("\nRecent:", 1)[0].splitlines()

    expected = f"Goal: Quest q ({done_when})" if target == "thing:person" else f"Goal: Quest q (done when: {done_when})"
    assert expected in rendered
    assert len(because) == 1 and because[0] == f'Mira, step 1: "{cited}"'
    assert "Uncited text" not in rendered
    assert "Recent note" in rendered


def test_plain_thing_focus_stays_open_until_done(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value)
    key = nb.heard(0, None, "here", "Look at Mira.")
    nb.apply_ops([{**add("q", target="thing:person"), "why": [key]}], 1)

    rendered = nb.render_focus(lambda ref: "Mira")

    assert "speaks to you" not in rendered
    assert "(target: Mira; close this quest with done when it is achieved)" in rendered


def test_focus_target_shows_a_text_name_as_a_guess(tmp_path):
    from agent.loop import Loop
    from agent.memory import Memory
    from tests.agent.fakes import GridEnv, StubModel
    mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "hall", "name": "Hall", "screen": "world", "state": "place"})
    mem.upsert("entities", {"id": "person", "name": "NPC", "kind": "npc", "place": "hall"})
    loop = Loop(GridEnv(), StubModel([]), mem, "grid")
    key = loop.notebook.heard(1, None, "Hall", "The sign says PROF.")
    loop.notebook.apply_ops([{**add("q", target="thing:person"), "why": [key]}], 2)
    mem.call("person", "PROF", 3, tier="text")

    rendered = loop.notebook.render_focus(loop.world.label)

    assert "(target: NPC, maybe PROF; close this quest with done when it is achieved)" in rendered


def test_render_focus_shows_newest_cited_text_whole_and_cuts_older(tmp_path):
    nb = notebook(tmp_path)
    nb.heard(1, "Mira", "Hall", "old " + "x" * 220)
    nb.heard(2, "Mira", "Hall", "new " + "y" * 280)
    nb.apply_ops([{**add("q"), "why": ["t1", "t2"]}], 3)

    because = nb.render_focus(lambda ref: ref).split("Because:\n", 1)[1].split("\nRecent:", 1)[0].splitlines()

    assert len(because) == 2 and len(because[0]) == 160
    assert because[0].startswith('Mira, step 1: "old xxx')
    assert because[1] == 'Mira, step 2: "new ' + "y" * 280 + '"'


def test_crossed_exit_evidence_says_went_through(tmp_path):
    nb = notebook(tmp_path, label=lambda ref: "doorway" if ref == "thing:exit" else "Mira",
                  is_exit=lambda ref: ref == "thing:exit")

    assert nb._evidence({"target": "thing:exit", "by": "observe", "reason": "crossed"}) == "went through doorway"
    assert nb._evidence({"target": "thing:person", "by": "observe", "reason": "talked"}) == "talked to Mira, who said nothing yet"


def test_entered_thing_evidence_names_linked_place(tmp_path):
    links = {"thing:building": ["place:inside"]}.get
    nb = notebook(tmp_path, label=lambda ref: {"thing:building": "large house", "place:inside": "room with counters"}.get(ref, ref),
                  leads_to=links)
    nb.data["moves"] = [(4, "place:outside", "place:inside")]

    assert nb._evidence({"target": "thing:building", "by": "observe", "reason": "entered", "opened": 4, "closed": 4}) == "entered large house (room with counters)"


def test_exit_focus_wording_precedes_linked_place_wording(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value, is_exit=lambda ref: ref == "thing:door",
                  leads_to=lambda ref: ["place:inside"])
    nb.heard(1, None, "Outside", "Go through the door.")
    nb.apply_ops([add("q", target="thing:door")], 2)

    assert "Goal: Quest q (done when: you go through thing:door)" in nb.render_focus(lambda ref: ref)


def test_render_focus_limits_recent_to_four_lines_of_120_chars(tmp_path):
    nb = notebook(tmp_path)
    for step in range(6): nb.note(step, f"note {step} " + "x" * 200)

    recent = nb.render_focus(lambda ref: ref).split("Recent:\n", 1)[1].splitlines()

    assert len(recent) == 4
    assert all(len(line) <= 120 for line in recent)
    assert recent[0].startswith("- step 2: note 2") and recent[-1].startswith("- step 5: note 5")
    assert "note 0" not in "\n".join(recent) and "note 1" not in "\n".join(recent)


def test_render_focus_uses_supplied_recent_trace_rows(tmp_path):
    nb = notebook(tmp_path); nb.note(1, "Notebook log")

    recent = nb.render_focus(lambda ref: ref, recent=["step 4: approach door -> blocked"])

    assert "- step 4: approach door -> blocked" in recent
    assert "Notebook log" not in recent


def test_render_focus_empty_notebook_has_no_goal(tmp_path):
    nb = notebook(tmp_path)

    assert nb.render_focus(lambda ref: ref).startswith("Goal: (none)\nBecause:\nRecent:")


def test_v2_file_loads_as_empty(tmp_path):
    (tmp_path / "notebook.json").write_text(json.dumps({"facts": {}, "quests": [], "notes": []}), encoding="utf-8")

    nb = notebook(tmp_path)

    assert nb.data == {"text": [], "quests": [], "last": {}, "here": None, "moves": []}
    assert nb.log == []


def test_why_error_names_newest_text_keys_and_closest_target_candidates(tmp_path):
    labels = {"place:hall": "Hall", "thing:mom": "MOM", "thing:guide": "Guide"}
    refs_by_label = {label: ref for ref, label in labels.items()}
    nb = notebook(tmp_path, ref=refs_by_label.get, label=lambda ref: labels.get(ref, ref), refs=lambda: list(labels))
    assert nb.apply_ops([add("q")], 1)[0]["result"] == "error: why must cite game-text keys, none heard yet"
    for i in range(4): nb.heard(i, None, "Hall", f"Line {i}.")

    assert nb.apply_ops([{**add("q"), "why": ["missing"]}], 5)[0]["result"] == \
        "error: why must cite game-text keys, newest: t4, t3, t2"
    assert nb.apply_ops([add("q", target="Hal")], 6)[0]["result"] == "ok"


def test_add_with_unknown_target_keeps_the_quest_without_a_target(tmp_path):
    ties = {"exit: stairs": [("thing:stairs_a", "Hall"), ("thing:stairs_b", "Room")]}
    nb = notebook(tmp_path, ties=lambda text: ties.get(text, []))
    for text in ("What is his name again?", "Introduction", "Begin your adventure", "PROF. OAK, next door, is looking for you."):
        nb.heard(0, None, "room", text)
    op = {"op": "add", "key": "q3", "text": "Find Professor Oak (t4: 'PROF. OAK, next door, is looking for you.')", "why": ["t4"], "target": "PROF. OAK"}

    assert nb.apply_ops([op], 57)[0]["result"] == "ok"
    quest = nb.data["quests"][-1]
    assert quest["target"] is None and quest["text"] == op["text"] and quest["why"] == ["t4"]
    assert nb.focus == "q3"
    assert nb.apply_ops([add("ambiguous", target="exit: stairs")], 58)[0]["result"].startswith('error: target "exit: stairs" is ambiguous:')


def test_closed_thing_quest_shows_current_label_and_who_said(tmp_path):
    nb = notebook(tmp_path, ref={"person": "thing:a"}.get, label=lambda ref: {"thing:a": "MOM"}.get(ref, ref))
    nb.heard(1, "MOM", "kitchen", "Go find the professor.", who="thing:a")
    assert nb.apply_ops([add("q", target="person")], 2)[0]["result"] == "ok"
    nb.heard(39, "MOM", "kitchen", "Ah, you came back.", who="thing:a")
    assert nb.apply_ops([{"op": "done", "key": "q", "reason": "talked to MOM, who said t1"}], 39)[0]["result"] == "ok"
    nb.heard(44, "MOM", "room", "PROF next door is looking for you.", who="thing:a")

    rendered = nb.render()

    assert '- q "Quest q": done at step 39 — talked to MOM, who said t' in rendered
    assert "PROF next door" in rendered
    assert "talked to person" not in rendered


def test_ambiguous_target_lists_each_candidate_ref(tmp_path):
    ties = {"exit: stairs": [("thing:e_exit_stairs", "tiled room"), ("thing:e_exit_stairs_2", "room")]}
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None,
                  ties=lambda text: ties.get(text, []), label=lambda ref: ref, refs=lambda: ["thing:e_exit_right_passage"])
    nb.heard(1, None, "Hall", "Go on.")

    assert nb.apply_ops([add("q", target="exit: stairs")], 2)[0]["result"] == \
        'error: target "exit: stairs" is ambiguous: thing:e_exit_stairs (tiled room), thing:e_exit_stairs_2 (room); use one of these refs'
    assert nb.data["quests"] == []


def test_exit_quest_closes_on_a_crossing_from_its_opening_even_if_target_is_set_later(tmp_path):
    route = {"thing:stairs": ("place:tiled", "place:room")}.get
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None, route=route)
    nb.heard(1, None, "Hall", "Go on.")
    nb.observe(1, "place:tiled")
    assert nb.apply_ops([add("q")], 2)[0]["result"] == "ok"
    assert nb.observe(3, "place:room") == []  # crossed before any target existed

    assert nb.apply_ops([{"op": "target", "key": "q", "target": "thing:stairs"}], 4)[0]["result"] == "ok"

    quest = nb.data["quests"][0]
    assert quest["status"] == "done" and quest["reason"] == "crossed" and quest["closed"] == 4 and quest["by"] == "observe"


def test_exit_quest_closes_on_crossing_after_target_was_set_but_not_in_reverse(tmp_path):
    route = {"thing:stairs": ("place:tiled", "place:room")}.get
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None, route=route)
    nb.heard(1, None, "Hall", "Go on.")
    nb.observe(1, "place:tiled")
    nb.observe(2, "place:room")  # the stairs' crossing, two steps before the quest opens
    assert nb.apply_ops([add("late")], 5)[0]["result"] == "ok"
    assert nb.apply_ops([{"op": "target", "key": "late", "target": "thing:stairs"}], 6)[0]["result"] == "ok"
    assert nb.data["quests"][0]["status"] == "open"  # crossed before it opened: not the step before its opening

    assert nb.observe(7, "place:tiled") == []  # back up the other way: not this exit's route
    assert nb.observe(8, "place:room") == ["late"]  # crossed after the target was set
    assert nb.data["quests"][0]["status"] == "done"
    assert nb.data["quests"][0]["closed"] == 8 and nb.data["quests"][0]["reason"] == "crossed"


def test_thing_quest_closes_entered_when_target_is_set_after_linked_move(tmp_path):
    links = {"thing:building": ["place:inside"]}.get
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None,
                  leads_to=links)
    nb.heard(1, None, "Outside", "Go inside.")
    nb.observe(0, "place:outside")
    nb.observe(1, "place:inside")  # opened - 1
    assert nb.apply_ops([add("q")], 2)[0]["result"] == "ok"
    assert nb.apply_ops([{"op": "target", "key": "q", "target": "thing:building"}], 3)[0]["result"] == "ok"

    quest = nb.data["quests"][0]
    assert quest["status"] == "done" and quest["reason"] == "entered" and quest["closed"] == 3 and quest["by"] == "observe"


def test_thing_quest_closes_entered_on_later_linked_move_but_ignores_other_place(tmp_path):
    links = {"thing:building": ["place:inside"]}.get
    nb = notebook(tmp_path, ref=lambda value: value if str(value).startswith(("place:", "thing:")) else None,
                  leads_to=links)
    nb.heard(1, None, "Outside", "Go inside.")
    nb.observe(1, "place:outside")
    assert nb.apply_ops([add("q", target="thing:building")], 2)[0]["result"] == "ok"

    assert nb.observe(3, "place:elsewhere") == []
    assert nb.data["quests"][0]["status"] == "open"
    assert nb.observe(4, "place:inside") == ["q"]
    quest = nb.data["quests"][0]
    assert quest["status"] == "done" and quest["reason"] == "entered" and quest["closed"] == 4 and quest["by"] == "observe"


def test_name_op_names_a_thing_from_cited_text(tmp_path):
    named = []
    nb = notebook(tmp_path, ref=lambda t: "thing:e1" if t == "e1" else None, name=lambda ref, text, key, step: named.append((ref.partition(":")[2], text, key, step)))
    key = nb.heard(3, "Sign", "here", "A BALL OF FIRE")
    rows = nb.apply_ops([{"op": "name", "key": "e1", "text": "Fire ball", "why": [key]}, {"op": "name", "key": "e1", "text": "Nope"},
                         {"op": "name", "key": "zzz", "text": "X", "why": [key]}, {"op": "name", "key": "e1", "text": "", "why": [key]}], 4)
    assert [r["result"] for r in rows][0] == "ok" and all(r["result"].startswith("error") for r in rows[1:])
    assert named == [("e1", "Fire ball", key, 4)]


def test_name_op_names_a_place_and_resolves_its_guess(tmp_path):
    from agent.loop import Loop
    from agent.memory import Memory
    from tests.agent.fakes import GridEnv, StubModel
    mem = Memory(tmp_path / "memory")
    mem.upsert("places", {"id": "hall", "name": "Hall", "screen": "world", "state": "place"})
    loop = Loop(GridEnv(), StubModel([]), mem, "grid")
    key = loop.notebook.heard(1, None, "Hall", "A sign says LAB.")

    result = loop.notebook.apply_ops([{"op": "name", "key": "place:hall", "text": "LAB", "why": [key]}], 2)

    assert result[0]["result"] == "ok"
    assert loop.world.ref("LAB") == "place:hall"
    assert loop.world.label("place:hall") == "Hall, maybe LAB"
    assert loop.notebook.apply_ops([{"op": "name", "key": "unknown", "text": "X", "why": [key]}], 3)[0]["result"] == 'error: "unknown" names no known thing or place; closest: Hall, maybe LAB'


def test_name_error_lists_closest_label(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: "place:lab" if value == "Oak's Lab" else None,
                  refs=lambda: ["place:lab"], label=lambda _: "Oak's Lab")
    key = nb.heard(1, None, "Room", "The laboratory is nearby.")

    result = nb.apply_ops([{"op": "name", "key": "oak", "text": "Oak", "why": [key]}], 2)

    assert result[0]["result"] == 'error: "oak" names no known thing or place; closest: Oak\'s Lab'


def test_name_of_another_place_reports_its_label(tmp_path):
    from agent.loop import Loop
    from agent.memory import Memory
    from tests.agent.fakes import GridEnv, StubModel
    mem = Memory(tmp_path / "memory")
    for ident, name in (("hall", "Hall"), ("lab", "Laboratory")):
        mem.upsert("places", {"id": ident, "name": name, "screen": "world", "state": "place"})
    loop = Loop(GridEnv(), StubModel([]), mem, "grid")
    key = loop.notebook.heard(1, None, "Hall", "A sign says LAB.")
    loop.notebook.observe(1, "place:hall")

    result = loop.notebook.apply_ops([{"op": "name", "key": "place:lab", "text": "LAB", "why": [key]}], 2)

    assert result[0]["result"] == "ok (named Laboratory, maybe LAB, not where you are)"
