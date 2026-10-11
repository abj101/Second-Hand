import json

from agent.memory import Memory, conf


def test_upsert_restart_replay_and_unknown_store(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("odd_store", {"id": "x", "value": 1})
    mem.upsert("odd_store", {"id": "x", "value": 2})
    assert mem.get("odd_store", "x")["value"] == 2
    restarted = Memory(tmp_path)
    assert restarted.rows("odd_store") == [{"id": "x", "value": 2}]


def test_places_continuity_edge_landmark_candidate_and_promotion(tmp_path):
    mem = Memory(tmp_path)
    a = mem.match_place({"screen": "overworld", "entities": [{"name": "Oak", "kind": "npc"}]}, 1,
                        prev=None, cut=True)
    assert mem.get("places", a)["state"] == "candidate"
    assert mem.match_place({}, 2, prev=a, cut=False) == a
    b = mem.match_place({"screen": "overworld", "entities": []}, 3, prev=a, cut=True)
    mem.add_edge(a, "approach", "door", b, True)
    assert mem.match_place({"screen": "battle", "entities": []}, 4, prev=a, cut=True, via="door") == b
    same = mem.match_place({"screen": "overworld", "entities": [{"name": "Oak", "kind": "npc"}]}, 5,
                           prev=b, cut=True)
    assert same == a
    battle = mem.match_place({"screen": "battle", "entities": [{"name": "Rival", "kind": "npc"}]}, 6,
                             prev=a, cut=True)
    assert mem.get("places", battle)["state"] == "candidate"
    entity = mem.observe({"entities": [{"name": "Oak", "kind": "npc"}]}, a, 7)[0]
    mem.relate(entity, "moves_view", "", src="pixel", step=8, hit=True)
    assert mem.get("places", a)["state"] == "place"


def test_identity_kind_drift_edit_distance_tie_recurring_and_resolve(tmp_path):
    mem = Memory(tmp_path)
    one = mem.observe({"entities": [{"name": "mom", "kind": "npc"}]}, "p", 1)[0]
    assert mem.observe({"entities": [{"name": "Mom", "kind": "woman"}]}, "p", 2)[0] == one
    assert mem.observe({"entities": [{"name": "map", "kind": "npc"}]}, "p", 3)[0] != one
    pair = mem.observe({"entities": [{"name": "bug catcher", "kind": "npc"},
                                      {"name": "bug catcher", "kind": "npc"}]}, "p", 4)
    assert pair[0] != pair[1]
    assert mem.get("entities", pair[1])["name"] == "bug catcher#2"
    assert mem.resolve("bug catcher", "p") is None
    mem.observe({"entities": [{"name": "cat", "kind": "npc"}, {"name": "cut", "kind": "npc"}]}, "q", 5)
    tied = mem.observe({"entities": [{"name": "cot", "kind": "npc"}]}, "q", 6)[0]
    assert mem.get("entities", tied)["name"] == "cot#2"
    move = mem.observe({"entities": [{"name": "Thunderbolt", "kind": "move"}]}, "p1", 7)[0]
    assert mem.observe({"entities": [{"name": "Thunderbolts", "kind": "move"}]}, "p2", 8)[0] == move
    assert mem.resolve("mom", "p") == one


def test_relations_confidence_provenance_and_expiry(tmp_path):
    mem = Memory(tmp_path)
    rel = mem.relate("e_door", "leads_to", "p_lab", src="pixel", step=1, hit=True)
    mem.relate("e_door", "leads_to", "p_lab", src="pixel", step=2, hit=True)
    mem.relate("e_door", "leads_to", "p_lab", src="pixel", step=3, hit=True)
    mem.relate("e_door", "leads_to", "p_lab", src="pixel", step=4, hit=False)
    assert mem.get("relations", rel["id"])["state"] == "live"
    assert conf(mem.get("relations", rel["id"])) > .65
    mem.relate("e_door", "leads_to", "p_lab", src="model", step=5, hit=True)
    assert mem.get("relations", rel["id"])["src"] == "observed"
    assert mem.get("relations", rel["id"])["sup"] == 3
    mem.relate("e_door", "leads_to", "p_lab", src="pixel", step=6, hit=False)
    assert mem.get("relations", rel["id"])["state"] == "live"
    model = mem.relate("e_npc", "opens", "battle", src="model", step=10, hit=True)
    assert model["src"] == "model" and model["sup"] == 0
    mem.expire(210)
    assert mem.get("relations", model["id"])["state"] == "expired"


def test_view_budget_ids_and_recent_failed_affordances(tmp_path):
    mem = Memory(tmp_path)
    place = mem.match_place({"screen": "town", "entities": []}, 1, prev=None, cut=True)
    entity = mem.observe({"entities": [{"name": "door", "kind": "door", "at": [.2, .3]}]}, place, 2)[0]
    mem.relate(entity, "leads_to", "p_other", src="model", step=3)
    recent = [{"step": 2, "place": place, "choice": {"verb": "interact", "target": "door"},
               "verdict": "miss", "outcome": {"effects": ["no_change"]}}]
    for caller, budget in (("perceiver", 400), ("chooser", 1200), ("orchestrator", 3000)):
        text, ids = mem.view(caller, {"place": place, "task": "explore", "on_screen": [entity],
                                      "recent": recent, "step": 3})
        assert len(text) / 4 <= budget
        assert entity in ids
        if caller != "perceiver":
            assert "(unverified)" in text
        if caller == "chooser":
            assert "Failed here: interact door" in text
    mem.know("The sealed gate opens at dusk", src="model", step=4)
    assert "The sealed gate opens at dusk (unverified)" in mem.view(
        "chooser", {"place": place, "task": None, "on_screen": [], "recent": [], "step": 4}
    )[0]


def test_refuted_view_cap_and_chooser_history_survives_crowded_memory(tmp_path):
    mem = Memory(tmp_path)
    place = mem.match_place({"screen": "town", "entities": []}, 1, prev=None, cut=True)
    for i in range(3):
        mem.relate(f"e_{i}", "leads_to", f"p_{i}", src="pixel", step=2, hit=False)
        mem.relate(f"e_{i}", "leads_to", f"p_{i}", src="pixel", step=3, hit=False)
    for i in range(30):
        mem.upsert("entities", {"id": f"e_noise_{i}", "name": "noise" * 40, "kind": "npc",
                                  "place": place, "seen": 1})
        mem.upsert("knowledge", {"id": f"k_{i}", "text": "fact" * 40, "src": "model", "step": i})
    recent = [{"step": 9, "place": place, "choice": {"verb": "interact", "target": "old target"},
               "verdict": "surprise", "outcome": {"effects": ["screen_changes"]}}]
    text, _ = mem.view("chooser", {"place": place, "task": "explore", "on_screen": [],
                                    "recent": recent, "step": 10})
    assert text.count("Don't believe:") <= 2
    assert "Recent: interact old target → surprise" in text
    assert "Failed here: interact old target" in text
    assert "Surprise at step 9" in text
    assert len(text) / 4 <= 1200

