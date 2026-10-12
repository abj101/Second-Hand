from agent.notebook import Notebook


def test_apply_counts_history_and_reload(tmp_path):
    nb = Notebook(tmp_path)
    ops = [{"op": "set", "section": "goals", "key": "find oak", "value": {"text": "go see Oak", "status": "active"}},
           {"op": "set", "section": "places", "key": "Town", "value": "start town"},
           {"op": "remove", "section": "goals", "key": "absent"},
           {"op": "set", "section": "bogus", "key": "x", "value": {}},
           {"op": "set", "section": "hints", "key": "  ", "value": {}},
           "junk"]
    assert nb.apply(ops, 1) == 2
    assert nb.apply({"op": "set"}, 2) == 0
    assert nb.apply([{"op": "remove", "section": "goals", "key": "find oak"}], 3) == 1
    assert nb.places() == ["Town"]
    assert len((tmp_path / "notebook.history").read_text(encoding="utf-8").splitlines()) == 3
    (tmp_path / "notebook.json").write_text("{not json", encoding="utf-8")
    assert Notebook(tmp_path).render() == "(empty)"


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
    short = nb.render(limit=120)
    assert len(short) <= 120 and "- active_one" in short and "- Town" in short and "- done_one" not in short
