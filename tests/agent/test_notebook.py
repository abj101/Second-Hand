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

    result = nb.apply_plan(plan([quest("new", text="Find another lead", why=[])]), 2)

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

    nb.apply_plan(plan([quest("new", text="Find another lead", why=[])]), 2)

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


def test_chapter_with_invalid_summary_is_rejected(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [{"text": "Missing key", "status": "open"}]
    nb.data["notes"] = [{"step": 2, "trigger": "stall", "text": "Keep me"}]

    assert not nb.apply_chapter({"summary": None, "open_leads": []}, 3)
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
    assert not nb.data["chapters"]  # QR.3: the tactical goal is omitted from the summary, not summarized


def test_mechanical_closure_appends_evidence_without_replacing_prior_evidence(tmp_path):
    nb = notebook(tmp_path, labels=lambda: "Town Square")
    nb.data["quests"] = [quest(evidence="Earlier evidence", check_history=[{"step": 2, "result": "fail"}],
                                check={"kind": "reach", "arg": "Town Square"})]

    assert nb.run_checks(4) == ["q"]
    q = nb.data["quests"][0]
    assert q["evidence"] == "Earlier evidence; check reach Town Square at step 4"
    assert q["check_history"][0] == {"step": 2, "result": "fail"}


def test_planner_cannot_write_code_owned_fields(tmp_path):
    nb = notebook(tmp_path)
    result = nb.apply_plan(plan(
        [quest("new", opened=99, closed=99, check_history=[{"result": "fake"}], extra="drop")],
        beliefs=[{"key": "b", "text": "A belief", "status": "held", "history": [{"reason": "fake"}]}],
        facts=[{**fact("f"), "step": 99, "extra": "drop"}],
    ), 7)

    assert not result["flags"]
    assert nb.data["quests"][0]["opened"] == 7
    assert "closed" not in nb.data["quests"][0] and "check_history" not in nb.data["quests"][0]
    assert "extra" not in nb.data["quests"][0]
    assert nb.data["beliefs"]["b"]["history"] == []
    assert nb.data["facts"]["f"]["step"] == 7 and "extra" not in nb.data["facts"]["f"]


def test_check_swap_with_done_is_refused(tmp_path):
    nb = notebook(tmp_path, places=lambda: [(2, ["Harbor"])])
    nb.data["quests"] = [quest(check={"kind": "reach", "arg": "Mountain"})]

    result = nb.apply_plan(plan([quest(status="done", evidence="I went there.",
                                       check={"kind": "reach", "arg": "Harbor"})]), 3)

    assert nb.data["quests"][0]["check"] == {"kind": "reach", "arg": "Mountain"}
    assert nb.data["quests"][0]["status"] == "open"
    assert any("check can change" in flag for flag in result["flags"])


def test_reach_needs_entry_after_opened_and_matches_aliases_without_id_suffix(tmp_path):
    current = [(4, ["Town Square [p_2]", "World [p_2]"]) ]
    nb = notebook(tmp_path, places=lambda: current)
    nb.data["quests"] = [quest(opened=4, check={"kind": "reach", "arg": "World"})]

    assert nb.run_checks(4) == []
    current[:] = [(5, ["Town Square [p_2]", "World [p_2]"])]
    assert nb.run_checks(5) == ["q"]


def test_text_check_rejects_short_args_and_matches_words(tmp_path):
    nb = notebook(tmp_path)
    result = nb.apply_plan(plan([quest("short", check={"kind": "text", "arg": "the"}),
                                 quest("good", check={"kind": "text", "arg": "welcome"})]), 2)
    nb.log = [{"step": 3, "text": 'Guide at Town: "Welcome, friend!"'}]

    assert "check" not in nb.data["quests"][0]
    assert any("text check needs" in flag for flag in result["flags"])
    assert nb.run_checks(3) == ["good"]


def test_speak_check_ignores_lines_before_opened(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(opened=4, check={"kind": "speak", "arg": "Guide"})]
    nb.log = [{"step": 3, "text": 'Guide at Town: "Welcome."'}]

    assert nb.run_checks(4) == []
    nb.log.append({"step": 5, "text": 'Guide at Town: "Again."'})
    assert nb.run_checks(5) == ["q"]


def test_heard_is_transitive_through_beliefs(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["heard_fact"] = fact()
    nb.data["beliefs"]["b1"] = {"key": "b1", "because": ["b2"]}
    nb.data["beliefs"]["b2"] = {"key": "b2", "because": ["heard_fact"]}

    assert nb._heard_quest(quest(why=["b1"]))


def test_heard_quest_without_check_is_flagged(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"]["heard_fact"] = fact()

    result = nb.apply_plan(plan([quest()]), 2)

    assert "needs check q" in result["flags"]
    assert nb.data["quests"][0]["status"] == "open"


def test_rekeyed_quest_merges_into_existing_key(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest("old", text="Find the ancient key!")]

    result = nb.apply_plan(plan([quest("new", text="find the ancient key")]), 2)

    assert [q["key"] for q in nb.data["quests"]] == ["old"]
    assert "merged new into old" in result["flags"]


def test_parked_quest_promotes_when_slot_frees(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest(f"q{i}", why=[], opened=i) for i in range(5)]
    nb.data["quests"].append(quest("parked", why=[], status="parked", opened=0))
    incoming = [quest(f"q{i}", why=[], status="done" if i == 0 else "open",
                      evidence="Finished." if i == 0 else "") for i in range(5)]

    nb.apply_plan(plan(incoming), 6)

    assert nb.data["quests"][-1]["status"] == "open"


def test_rule4_compass_and_numbered_exit_but_not_badge_number(tmp_path):
    nb = notebook(tmp_path, buttons=("A", "B", "START"))
    good = [quest("badge", text="Get Badge #1", why=[]), quest("rival", text="Find a rival", why=[]),
            quest("one_button", text="Use A", why=[])]
    bad = [quest("compass", text="After that, go north", why=[]), quest("exit", text="Try door#4", why=[]),
           quest("button", text="Press START", why=[])]

    nb.apply_plan(plan([*good, *bad]), 2)

    assert {q["key"] for q in nb.data["quests"]} == {"badge", "rival", "one_button"}


def test_refuted_belief_cannot_relapse(tmp_path):
    nb = notebook(tmp_path)
    nb.data["beliefs"]["b"] = {"key": "b", "text": "The route is blocked", "status": "refuted", "history": []}

    result = nb.apply_plan(plan([], beliefs=[{"key": "b", "status": "held", "reason": "Maybe."}]), 3)

    assert nb.data["beliefs"]["b"]["status"] == "refuted"
    assert "relapse b: refuted beliefs stay ruled out" in result["flags"]


def test_chapter_with_summary_is_accepted(tmp_path):
    nb = notebook(tmp_path)
    nb.data["quests"] = [quest()]
    nb.data["notes"] = [{"step": 1, "trigger": "start", "text": "Keep this until accepted."}]

    assert nb.apply_chapter({"summary": "Progress."}, 2)
    assert nb.data["chapters"][0]["summary"] == "Progress."
    assert nb.data["notes"] == []


# --- QR.3: render budgets and migration ---
def test_render_bounds_facts_by_relevance_and_recency(tmp_path):
    nb = notebook(tmp_path)
    for i in range(30): nb.data["facts"][f"f{i}"] = fact(f"f{i}", f"Fact number {i}.", "saw")
    nb.data["beliefs"]["b"] = {"key": "b", "text": "Belief", "status": "held", "because": ["f1"]}
    nb.data["quests"] = [quest("q", why=["b", "f0"])]

    rendered = nb.render()

    assert "f0" in rendered and "f1" in rendered  # cited directly and through the belief
    assert all(f"f{i}\"" in rendered or f"- f{i}:" in rendered for i in range(22, 30))
    assert "- f10:" not in rendered and "(+20 older facts)" in rendered


def test_render_keeps_newest_notes_and_one_ruled_out_heading(tmp_path):
    nb = notebook(tmp_path)
    nb.data["notes"] = [{"step": i, "trigger": "t", "text": f"note {i} " + "x" * 80} for i in range(10)]
    nb.data["beliefs"] = {f"r{i}": {"key": f"r{i}", "text": "gone", "status": "refuted"} for i in range(3)}

    rendered = nb.render(limit=900)

    assert rendered.count("ruled out:") == 1 and "r0" in rendered and "r2" in rendered
    assert "note 9 " in rendered and "note 5 " in rendered and "note 0 " not in rendered  # the last 5 stay, older notes go first
    assert len(nb.render(limit=100).splitlines()) > 0 and nb.render(limit=100).count("ruled out:") == 0


def test_render_focus_shows_beliefs_and_latest_note_within_limit(tmp_path):
    nb = notebook(tmp_path)
    nb.data["facts"].update({f"f{i}": fact(f"f{i}", f"Fact {i}.", "saw") for i in range(10)})
    nb.data["beliefs"] = {f"b{i}": {"key": f"b{i}", "text": f"Belief {i}", "status": "held", "confidence": "mid", "because": []} for i in range(5)}
    nb.data["beliefs"]["cited"] = {"key": "cited", "text": "Cited belief", "status": "held", "confidence": "high", "because": ["f0"]}
    nb.data["quests"] = [quest("top", why=["cited"]), quest("other", why=[])]
    nb.data["notes"] = [{"step": 1, "trigger": "t", "text": "old note"}, {"step": 2, "trigger": "t", "text": "latest " + "n" * 500}]
    nb.log = [{"step": i, "text": "log line " + "z" * 300} for i in range(10)]

    rendered = nb.render_focus()

    assert "- cited: Cited belief (high)" in rendered and "b0" not in rendered
    assert "Planner note: latest " + "n" * 393 in rendered and "n" * 394 not in rendered and "old note" not in rendered
    assert "- f0:" in rendered and "- f9:" in rendered and "- f5:" not in rendered  # cited by the top quest through its belief, plus the 4 newest
    assert len(rendered) <= 4000 and "step 9:" in rendered and "step 0:" not in rendered  # the log keeps its newest lines


def test_migrated_chapter_omits_tactical_goals(tmp_path):
    original = {"goals": {"try_house_door": {"text": "Check the house", "status": "active", "step": 1},
                          "door": {"text": "Try house door#4", "status": "active", "step": 2},
                          "plain": {"text": "Find the person", "status": "active", "step": 3}}, "hints": {}, "lessons": {}}
    (tmp_path / "notebook.json").write_text(json.dumps(original), encoding="utf-8")

    summary = notebook(tmp_path).data["chapters"][0]["summary"]

    assert summary == "Find the person (active)"
