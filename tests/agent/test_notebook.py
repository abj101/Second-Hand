import json
import shutil
from pathlib import Path

from agent.notebook import Notebook


def notebook(tmp_path, **kwargs):
    return Notebook(tmp_path, **kwargs)


def fact(key="heard_fact", text="A lead was heard.", source="heard"):
    return {"key": key, "text": text, "source": source, "step": 1}


def quest(key="q", **kwargs):
    return {"key": key, "text": "Find the lead", "why": ["heard_fact"], "done_when": "Learn more",
            "status": "open", "evidence": "", "opened": 1, **kwargs}


def plan(quests=None, **kwargs):
    return {"quests": quests if quests is not None else [quest()], "beliefs": [], "facts": [], **kwargs}


def test_inv1_omitted_heard_quest_kept_and_flagged(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["heard_fact"] = fact()
    nb.data["quests"] = [quest()]

    result = nb.apply_plan(plan([quest("new", why=[])]), 2)

    assert [q["key"] for q in nb.data["quests"]] == ["new", "q"]
    assert "omitted q: keep, park or drop it with a reason" in result["flags"]


def test_inv2_done_and_dropped_require_evidence_and_reference(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["heard_fact"] = fact()
    nb.data["quests"] = [quest(), quest("drop")]

    result = nb.apply_plan(plan([quest(status="done"), quest("drop", status="dropped", why=[], evidence="No longer needed.")]), 2)

    assert nb.data["quests"][0]["status"] == "open"
    assert nb.data["quests"][1]["status"] == "open"
    assert any("done requires evidence" in f for f in result["flags"])
    assert any("dropping a heard quest needs" in f for f in result["flags"])
    nb.apply_plan(plan([quest(status="dropped", evidence="heard_fact", why=["heard_fact"])]), 3)
    assert nb.data["quests"][0]["status"] == "dropped"


def test_inv3_open_quests_over_limit_are_parked(tmp_path):
    nb = notebook(tmp_path)
    items = [quest(f"q{i}", why=[]) for i in range(7)]

    nb.apply_plan(plan(items), 2)

    assert sum(q["status"] == "open" for q in nb.data["quests"]) == 5
    assert [q["key"] for q in nb.data["quests"] if q["status"] == "parked"] == ["q5", "q6"]


def test_inv4_tactical_quest_text_is_rejected(tmp_path):
    nb = notebook(tmp_path, buttons=("SPACE",))
    bad = [quest("coord", text="Reach (8, 0)"), quest("door", text="Try door#4"),
           quest("button", text="Use SPACE"), quest("direction", text="Go left")]

    result = nb.apply_plan(plan(bad), 2)

    assert not nb.data["quests"]
    assert len([f for f in result["flags"] if f.endswith("name an outcome")]) == 4


def test_inv5_belief_revision_has_reason_and_refuted_is_rendered(tmp_path):
    nb = notebook(tmp_path)
    nb.data["beliefs"]["b1"] = {"key": "b1", "text": "The lead is nearby", "status": "held", "history": []}

    nb.apply_plan(plan([], beliefs=[{"key": "b1", "text": "The lead is elsewhere", "status": "refuted", "reason": "New evidence contradicted it."}]), 4)

    belief = nb.data["beliefs"]["b1"]
    assert belief["history"] == [{"step": 4, "from_text": "The lead is nearby", "reason": "New evidence contradicted it."}]
    assert "ruled out:" in nb.render() and "b1" in nb.render()


def test_inv6_quest_reset_preserves_evidence_opened_and_check_results(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(evidence="first-hand evidence", opened=1,
                                check_history=[{"step": 2, "result": "fail"}])]

    nb.apply_plan(plan([quest(text="Reworded quest", evidence="replacement", opened=99,
                              check_history=[{"step": 99, "result": "pass"}])]), 5)

    q = nb.data["quests"][0]
    assert (q["text"], q["evidence"], q["opened"], q["check_history"]) == (
        "Reworded quest", "first-hand evidence", 1, [{"step": 2, "result": "fail"}])


def test_inv7_facts_are_add_only_and_conflicts_get_suffix(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["f"] = fact("f", "Original.")

    nb.apply_plan(plan([], facts=[fact("f", "Changed."), fact("f", "Original.")]), 5)

    assert nb.data["facts"]["f"]["text"] == "Original."
    assert nb.data["facts"]["f_2"]["text"] == "Changed."


def test_inv8_nothing_deleted(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["f"] = fact("f", "Recorded.", "saw")
    nb.data["beliefs"]["b"] = {"key": "b", "text": "A belief", "status": "held", "history": []}
    nb.data["quests"] = [quest("q", why=[]), quest("heard", why=["heard_fact"])]
    nb.data["facts"]["heard_fact"] = fact()

    nb.apply_plan(plan([quest("new", why=[])]), 2)

    assert set(nb.data["facts"]) == {"f", "heard_fact"}
    assert set(nb.data["beliefs"]) == {"b"}
    assert {q["key"] for q in nb.data["quests"]} == {"q", "heard", "new"}
    assert len((tmp_path / "notebook.history").read_text(encoding="utf-8").splitlines()) >= 1


def test_reach_check_closes_only_after_label_matches(tmp_path):
    nb = notebook(tmp_path, labels=lambda: ["Town Square"])
    nb.data["quests"] = [quest(check={"kind": "reach", "arg": "town square"})]

    assert nb.run_checks(8) == ["q"]
    assert nb.data["quests"][0]["evidence"] == "check reach town square at step 8"


def test_speak_check_reads_dialogue_after_opened(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(check={"kind": "speak", "arg": "Guide"})]
    nb.log = [{"step": 2, "text": 'Guide at Town: "Welcome."'}]

    assert nb.run_checks(3) == ["q"]


def test_text_check_reads_dialogue_after_opened(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(check={"kind": "text", "arg": "welcome"})]
    nb.log = [{"step": 2, "text": 'Guide at Town: "Welcome."'}]

    assert nb.run_checks(3) == ["q"]


def test_chapter_guard_accepts_current_keys_and_clears_notes(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest()]
    nb.data["beliefs"]["b"] = {"key": "b", "text": "Belief", "status": "held", "history": []}
    nb.apply_plan(plan([quest()], note="Current thought", why="stall"), 2)

    assert nb.apply_chapter({"summary": "Progress so far", "ruled_out": [], "open_leads": []}, 3)
    assert nb.data["notes"] == [] and nb.data["chapters"][0]["summary"] == "Progress so far"
    history = [json.loads(line) for line in (tmp_path / "notebook.history").read_text(encoding="utf-8").splitlines()]
    assert any(row.get("op") == "planner_note" for row in history)


def test_chapter_guard_rejects_malformed_live_key_and_keeps_notes(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [{"text": "Missing key", "status": "open"}]
    nb.data["notes"] = [{"step": 2, "trigger": "stall", "text": "Keep me"}]

    assert not nb.apply_chapter({"summary": "Too early", "open_leads": []}, 3)
    assert nb.data["notes"][0]["text"] == "Keep me"
    assert not nb.data["chapters"]


def test_v1_migration_fixture_keeps_heard_leads_and_never_opens_try_goals(tmp_path):
    fixture = Path(__file__).parent / "data" / "qs" / "notebook-v1.json"
    shutil.copyfile(fixture, tmp_path / "notebook.json")
    source = (tmp_path / "notebook.json").read_bytes()

    nb = notebook(tmp_path)

    open_keys = {q["key"] for q in nb.data["quests"] if q["status"] == "open"}
    assert {"find_prof_oak", "find_grandpas_lab"} <= open_keys
    assert not any(q["key"].startswith("try_") for q in nb.data["quests"])
    assert nb.data["facts"]["prof_oak_looking_for_player"]["source"] == "heard"
    assert nb.data["chapters"]
    assert (tmp_path / "notebook.json").read_bytes() == source
    assert not (tmp_path / "notebook.history").exists()
    nb.note(101, "later event")
    history = [json.loads(line) for line in (tmp_path / "notebook.history").read_text(encoding="utf-8").splitlines()]
    assert history[0]["op"] == "migrate_v1" and history[0]["state"] == json.loads(source)


def test_render_budget_drops_old_chapters_before_closed_quests_and_keeps_facts_open_quests(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["important"] = fact("important", "Never compact this fact.", "saw")
    nb.data["quests"] = [quest("open", why=[]), quest("closed", status="done", why=[], evidence="Done.")]
    nb.data["chapters"] = [{"from": i, "to": i, "summary": f"old chapter {i} " + "x" * 1200} for i in range(4)]

    rendered = nb.render(limit=300)

    assert len(rendered) <= 300
    assert "important" in rendered and "open" in rendered
    assert "old chapter" not in rendered and "closed" not in rendered
    for line in rendered.splitlines():
        if line.startswith("- "):
            json.loads(line.split(": ", 1)[1])


def test_note_writes_code_log_for_mechanical_checks_and_planner_note_stays_separate(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(check={"kind": "speak", "arg": "Guide"})]

    nb.note(2, 'Guide at Town: "Welcome."')
    nb.apply_plan(plan([quest(check={"kind": "speak", "arg": "Guide"})], note="A plan note", why="new dialogue"), 3)

    assert nb.run_checks(3) == ["q"]
    assert nb.log == [{"step": 2, "text": 'Guide at Town: "Welcome."'}]
    assert nb.data["notes"] == [{"step": 3, "trigger": "new dialogue", "text": "A plan note"}]


def test_same_plan_facts_and_beliefs_can_justify_dropping_heard_quest(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(source="heard", why=[])]
    dropped = quest(status="dropped", evidence="new_fact explains the change", why=["new_fact"])

    result = nb.apply_plan(plan([dropped], facts=[fact("new_fact", "A new lead.")], beliefs=[
        {"key": "new_belief", "text": "The lead has changed", "status": "held", "confidence": "mid", "history": []}
    ]), 4)

    assert nb.data["quests"][0]["status"] == "dropped"
    assert not any("dropping a heard quest" in flag for flag in result["flags"])


def test_quest_reorder_counts_as_a_change(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest("first", why=[]), quest("second", why=[])]

    result = nb.apply_plan(plan([quest("second", why=[]), quest("first", why=[])]), 4)

    assert result["quests_changed"] > 0
    assert nb.focus == "second"


def test_render_keeps_long_facts_top_quest_and_held_beliefs_past_budget(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["f"] = fact("f", "F" * 800, "saw")
    nb.data["beliefs"]["held"] = {"key": "held", "text": "H" * 700, "status": "held"}
    nb.data["beliefs"]["ruled"] = {"key": "ruled", "text": "R" * 700, "status": "refuted"}
    nb.data["quests"] = [quest("top", text="Q" * 900, why=[]), quest("closed", status="done", why=[], evidence="done")]

    rendered = nb.render(limit=200)
    focus = nb.render_focus(limit=200)

    assert "F" * 800 in rendered and "H" * 700 in rendered and '"Q"' * 900 not in rendered
    assert "Q" * 900 in rendered and "R" * 700 not in rendered
    assert "Q" * 900 in focus and "F" * 800 in focus


def test_render_focus_has_full_top_quest_and_one_line_for_other_quests(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest("top", text="Main", done_when="Finish", approach="Explore west"),
                         quest("other", text="Secondary", why=[])]

    rendered = nb.render_focus()

    assert '"text":"Main"' in rendered and '"done_when":"Finish"' in rendered and '"approach":"Explore west"' in rendered
    assert "- other: Secondary" in rendered and '"done_when":"Learn more"' not in rendered


def test_chapter_lists_closed_quests_in_its_step_range(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest("old", status="done", closed=1, why=[], evidence="done"),
                         quest("current", status="done", closed=5, why=[], evidence="done")]
    nb.data["notes"] = [{"step": 3, "trigger": "test", "text": "Chapter begins here"}]

    assert nb.apply_chapter({"summary": "Progress", "ruled_out": [], "open_leads": []}, 6)
    assert nb.data["chapters"][0]["closed"] == ["current"]


def test_migration_rejects_tactical_heard_goals_and_summarizes_them(tmp_path):
    original = {"goals": {"heard_tactical": {"text": "Press SPACE", "source": "heard", "status": "active", "step": 2},
                          "heard_plain": {"text": "Find the person", "source": "heard", "status": "active", "step": 3}},
                "hints": {}, "lessons": {}}
    (tmp_path / "notebook.json").write_text(json.dumps(original), encoding="utf-8")

    nb = notebook(tmp_path, buttons=("SPACE",))

    assert [q["key"] for q in nb.data["quests"]] == ["heard_plain"]
    assert nb.data["quests"][0]["source"] == "heard"
    assert "Press SPACE" in nb.data["chapters"][0]["summary"]


def test_mechanical_closure_appends_evidence_without_replacing_prior_evidence(tmp_path):
    nb = notebook(tmp_path, labels=lambda: "Town Square")
    nb.data["quests"] = [quest(evidence="Earlier evidence", check_history=[{"step": 2, "result": "fail"}],
                                check={"kind": "reach", "arg": "Town Square"})]

    assert nb.run_checks(4) == ["q"]
    q = nb.data["quests"][0]
    assert q["evidence"] == "Earlier evidence; check reach Town Square at step 4"
    assert q["check_history"][0] == {"step": 2, "result": "fail"}
