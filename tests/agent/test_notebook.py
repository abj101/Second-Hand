import json

import pytest

from agent.notebook import MAX_HELD, MAX_OPEN, Notebook


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
    ("add-empty-why", [], {**add("q"), "why": []}, "why must cite text or a belief"),
    ("add-unknown-why", [], {**add("q"), "why": ["missing"]}, "why must cite text or a belief"),
    ("add-open-cap", [add(f"q{i}") for i in range(MAX_OPEN)], add("extra"), "maximum open quests reached"),
    ("add-unresolved-target", [], {**add("q"), "target": "unknown"}, "target must resolve to a place or thing"),
    ("done-not-open", [], {"op": "done", "key": "q", "reason": "Finished"}, "key is not open"),
    ("done-ok", [add("q")], {"op": "done", "key": "q", "reason": "Found the archive"}, None),
    ("done-empty-reason", [add("q")], {"op": "done", "key": "q", "reason": " "}, "reason is empty"),
    ("drop-not-open", [], {"op": "drop", "key": "q", "reason": "Finished"}, "key is not open"),
    ("drop-ok", [add("q")], {"op": "drop", "key": "q", "reason": "Changed course"}, None),
    ("drop-empty-reason", [add("q")], {"op": "drop", "key": "q", "reason": " "}, "reason is empty"),
    ("top-not-open", [], {"op": "top", "key": "q"}, "key is not open"),
    ("top-ok", [add("q"), add("next")], {"op": "top", "key": "q"}, None),
    ("target-not-open", [], {"op": "target", "key": "q", "target": "place:room"}, "key is not open"),
    ("target-missing", [add("q")], {"op": "target", "key": "q"}, "target must resolve to a place or thing"),
    ("target-unresolved", [add("q")], {"op": "target", "key": "q", "target": "unknown"}, "target must resolve to a place or thing"),
    ("target-ok", [add("q")], {"op": "target", "key": "q", "target": "place:room"}, None),
    ("target-outside-kind", [add("q")], {"op": "target", "key": "q", "target": "exit:door"}, "target must resolve to a place or thing"),
    ("target-kind-may-change", [add("q", target="place:room")], {"op": "target", "key": "q", "target": "thing:guide"}, None),
    ("believe-ok", [], {"op": "believe", "key": "b", "text": "A lead exists", "because": ["t1"]}, None),
    ("believe-key-used", [{"op": "believe", "key": "b", "text": "A lead exists"}], {"op": "believe", "key": "b", "text": "Another lead"}, "key is already used"),
    ("believe-empty-text", [], {"op": "believe", "key": "b", "text": " "}, "text is empty"),
    ("believe-held-cap", [{"op": "believe", "key": f"b{i}", "text": "A lead"} for i in range(MAX_HELD)], {"op": "believe", "key": "extra", "text": "Another lead"}, "maximum held beliefs reached"),
    ("believe-unknown-because", [], {"op": "believe", "key": "b", "text": "A lead", "because": ["missing"]}, "because cites an unknown key"),
    ("refute-not-held", [], {"op": "refute", "key": "b", "reason": "Evidence changed"}, "key is not held"),
    ("refute-ok", [{"op": "believe", "key": "b", "text": "A lead"}], {"op": "refute", "key": "b", "reason": "Evidence changed"}, None),
    ("refute-empty-reason", [{"op": "believe", "key": "b", "text": "A lead"}], {"op": "refute", "key": "b", "reason": " "}, "reason is empty"),
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
    assert {key: nb.data[key] for key in ("text", "quests", "beliefs")} == {
        key: expected.data[key] for key in ("text", "quests", "beliefs")
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
        {"key": "t1", "step": 7, "speaker": "Mira", "where": "Old Tower", "text": 'She said, "Leave now."'},
        {"key": "t2", "step": 8, "speaker": None, "where": "Old Tower", "text": "  Keep every space.  "},
    ]


def test_observe_closes_canonical_place_and_thing_targets_only(tmp_path):
    refs = {"Library": "place:old", "Mira": "thing:merged"}
    canon = {"place:old": "place:new", "thing:merged": "thing:mira"}
    nb = notebook(tmp_path, ref=refs.get, canon=lambda ref: canon.get(ref, ref))
    nb.heard(1, "Mira", "Library", "Meet me there.")
    assert all(row["result"] == "ok" for row in nb.apply_ops([
        add("place", target="Library"), add("person", target="Mira"), add("none"),
        {"op": "drop", "key": "place", "reason": "already done"},
    ], 2))
    nb.data["quests"][0]["target"] = "place:new"
    nb.data["quests"][1]["target"] = "thing:mira"
    nb.data["quests"][0]["label"] = "Library"
    nb.data["quests"][1]["label"] = "Mira"

    assert nb.observe(1, "place:new", ["thing:merged"]) == []
    assert nb.observe(5, "place:new", ["thing:merged"]) == ["person"]
    assert nb.observe(6, "place:old", []) == []
    assert nb.data["quests"][0]["status"] == "dropped"
    assert nb.data["quests"][1]["reason"] == "talked to Mira"
    assert nb.data["quests"][2]["status"] == "open"


def test_render_count_caps_cited_history_and_character_size(tmp_path):
    nb = notebook(tmp_path, ref=lambda value: value if value.startswith(("place:", "thing:")) else None)
    for i in range(20): nb.heard(i, "Guide", "Hall", f"Line {i}. " + "x" * 500)
    for i in range(7):
        assert all(row["result"] == "ok" for row in nb.apply_ops([
            add(f"closed{i}"), {"op": "drop", "key": f"closed{i}", "reason": f"Reason {i}"}
        ], 30 + i * 2))
    assert nb.apply_ops([add("open", target="place:hall")], 50)[0]["result"] == "ok"
    nb.data["beliefs"] = {
        **{f"b{i}": {"key": f"b{i}", "text": f"Belief {i}", "because": ["t1"], "status": "refuted", "reason": f"Ruled {i}", "step": i} for i in range(7)},
        **{f"h{i}": {"key": f"h{i}", "text": f"Held {i}", "because": ["t1"], "status": "held", "step": i} for i in range(7)},
    }

    rendered = nb.render()

    assert len([q for q in nb.data["quests"] if q["status"] in ("done", "dropped")][-5:]) == 5
    assert rendered.count("Recently closed:") == 1 and "closed0:" not in rendered and "closed6:" in rendered
    assert rendered.count("Held beliefs:") == 1 and rendered.count("- h") == MAX_HELD
    assert rendered.count("Ruled out:") == 1 and rendered.count("- b") == 5
    assert "t1 Guide at Hall: \"Line 0. " + "x" * 500 + "\"" in rendered
    assert "t12 Guide" not in rendered and all(f"t{i} Guide" in rendered for i in range(13, 21))
    assert len(rendered) <= 3500


def test_render_focus_has_cited_text_and_no_belief_or_why(tmp_path):
    nb = notebook(tmp_path)
    nb.heard(1, "Mira", "Hall", "Find the archive.")
    nb.apply_ops([{"op": "believe", "key": "b", "text": "Secret belief", "because": ["t1"]}], 2)
    nb.apply_ops([{**add("q"), "why": ["b"]}], 3, "Secret planner why")
    nb.note(4, "Moved north")

    rendered = nb.render_focus()

    assert "Focus:" in rendered and "Top quest: Quest q" in rendered
    assert "Because:" in rendered and "t1 Mira at Hall: \"Find the archive.\"" in rendered
    assert "Secret belief" not in rendered and "Secret planner why" not in rendered
    assert "Moved north" in rendered


def test_v2_file_loads_as_empty(tmp_path):
    (tmp_path / "notebook.json").write_text(json.dumps({"facts": {}, "quests": [], "notes": []}), encoding="utf-8")

    nb = notebook(tmp_path)

    assert nb.data == {"text": [], "quests": [], "beliefs": {}, "last": {}}
    assert nb.log == []
