from agent.notebook import Notebook


def test_apply_counts_history_and_reload(tmp_path):
    nb = Notebook(tmp_path)
    ops = [{"op": "set", "section": "goals", "key": "find oak", "value": {"text": "go see Oak", "status": "active"}},
           {"op": "set", "section": "places", "key": "Town", "value": "start town"},
           {"op": "remove", "section": "goals", "key": "absent"},
           {"op": "set", "section": "bogus", "key": "x", "value": {}},
           {"op": "set", "section": "hints", "key": "  ", "value": {}},
           "junk"]
    assert nb.apply(ops, 1) == 1
    assert nb.apply({"op": "set"}, 2) == 0
    assert nb.apply([{"op": "remove", "section": "goals", "key": "find oak"}], 3) == 1
    assert "places" not in nb.data
    assert len((tmp_path / "notebook.history").read_text(encoding="utf-8").splitlines()) == 2
    (tmp_path / "notebook.json").write_text("{not json", encoding="utf-8")
    assert Notebook(tmp_path).render() == "(empty)"


def test_legacy_places_are_dropped(tmp_path):
    (tmp_path / "notebook.json").write_text('{"places":{"Town":{"text":"old"}}}', encoding="utf-8")
    nb = Notebook(tmp_path)
    assert nb.data == {"goals": {}, "hints": {}, "lessons": {}}
    nb.apply([], 1)
    assert "places" not in (tmp_path / "notebook.json").read_text(encoding="utf-8")


def test_lessons_cap_render_order_and_limit(tmp_path):
    nb = Notebook(tmp_path)
    for i in range(12):
        nb.apply([{"op": "set", "section": "lessons", "key": f"l{i}", "value": {"text": "x" * 50}}], i)
    nb.apply([{"op": "set", "section": "goals", "key": "done_one", "value": {"text": "d", "status": "done"}},
              {"op": "set", "section": "goals", "key": "active_one", "value": {"text": "a", "status": "active"}},
              {"op": "set", "section": "places", "key": "Town", "value": {"text": "t"}}], 20)
    text = nb.render()
    assert "- l1:" not in text and "- l11:" in text
    assert text.index("- active_one") < text.index("- done_one")
    assert Notebook(tmp_path).render() == text
    short = nb.render(limit=80)
    assert len(short) <= 80 and "- active_one" in short and "- done_one" not in short


def test_goal_tree_focus_path_log_and_orphan_safe_trim(tmp_path):
    nb = Notebook(tmp_path)
    nb.apply([{"op": "set", "section": "goals", "key": "top", "value": {"text": "finish", "status": "active"}},
              {"op": "set", "section": "goals", "key": "out", "value": {"text": "leave house", "parent": "top", "done_when": "outdoors"}}], 1)
    nb.set_focus("out", 1)
    assert nb.path() == ["top", "out"] and nb.data["goals"]["out"]["status"] == "active"
    assert nb.apply([{"op": "set", "section": "goals", "key": "x", "value": {"text": "x"}}], 2, structure=False) == 0  # act can't add goals
    nb.apply([{"op": "tried", "text": "stairs"}, {"op": "focus", "status": "blocked", "evidence": "stairs go up"}], 3, structure=False)
    assert nb.data["goals"]["out"]["tried"] == ["stairs"] and nb.data["goals"]["out"]["status"] == "blocked"
    for i in range(12):  # many finished leaves under a live parent: the live chain survives trimming
        nb.apply([{"op": "set", "section": "goals", "key": f"d{i}", "value": {"text": "d", "parent": "top", "status": "done"}}], 10 + i)
    assert "top" in nb.data["goals"] and sum(k.startswith("d") for k in nb.data["goals"]) <= 10
    reloaded = Notebook(tmp_path)
    assert reloaded.focus == "out" and "goal out blocked: stairs go up" in reloaded.render_log()
    assert "Goal path (top -> focus):" in reloaded.render_focus() and "stairs go up" in reloaded.render_focus()
