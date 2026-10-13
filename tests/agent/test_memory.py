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


def test_tile_identity_survives_scrolling_and_separates_same_name(tmp_path):
    mem = Memory(tmp_path)
    per = {"entities": [{"name": "sign", "kind": "object", "at": [.2, .3]}]}
    first = mem.observe(per, "room", 1, tiles={0: (4, 2)})[0]
    assert mem.observe(per, "room", 2, tiles={0: (5, 3)})[0] == first
    other = mem.observe(per, "room", 3, tiles={0: (8, 2)})[0]
    assert other != first
    assert len(mem.rows("entities")) == 2
    assert mem.observe(per, "room", 4, tiles={0: (8, 2)})[0] == other
    assert len(mem.rows("entities")) == 2


def test_known_exit_precedes_via_edge_and_no_cut_keeps_previous_place(tmp_path):
    mem = Memory(tmp_path)
    a = mem.match_place({"screen": "start", "entities": []}, 1, prev=None, cut=True)
    b = mem.match_place({"screen": "exit", "entities": []}, 2, prev=a, cut=True)
    c = mem.match_place({"screen": "other", "entities": []}, 3, prev=b, cut=True)
    mem.add_edge(a, "approach", "door", c, True)
    mem.upsert("tiles", {"id": f"{a}@4,2", "place": a, "x": 4, "y": 2,
                          "exit": {"dir": "RIGHT", "to": b, "arrive": [0, 0], "src": "observed"}})
    drifted = {"screen": "new screen", "entities": [{"name": "different landmark", "kind": "npc"}]}
    assert mem.match_place(drifted, 4, prev=a, cut=True, via="door", exit=(4, 2, "RIGHT")) == b
    assert mem.match_place(drifted, 5, prev=a, cut=False, via="door", exit=(4, 2, "RIGHT")) == a


def test_cut_with_identical_landmarks_does_not_return_previous_place(tmp_path):
    mem = Memory(tmp_path)
    per = {"screen": "world", "entities": [{"name": "Oak", "kind": "npc"}]}
    prev = mem.match_place(per, 1, prev=None, cut=True)

    assert mem.match_place(per, 2, prev=prev, cut=True) != prev


def test_cut_ignores_self_loop_exit_edges_and_relations(tmp_path):
    mem = Memory(tmp_path)
    for suffix, path in (("tile", "exit"), ("via", "edge"), ("relation", "relation")):
        prev = f"p_{suffix}"
        mem.upsert("places", {"id": prev, "name": suffix, "screen": suffix, "state": "place", "edges": []})
        if path == "exit":
            mem.upsert("tiles", {"id": f"{prev}@1,2", "place": prev, "x": 1, "y": 2,
                                  "exit": {"dir": "RIGHT", "to": prev, "arrive": [0, 0]}})
        elif path == "edge":
            mem.upsert("places", {**mem.get("places", prev),
                                  "edges": [{"verb": "approach", "via": "door", "to": prev}]})
        else:
            mem.upsert("relations", {"id": "r_self", "s": "e_door", "p": "leads_to", "o": prev, "state": "live"})
        matched = mem.match_place({"screen": suffix}, 2, prev=prev, cut=True, via="door",
                                  exit=(1, 2, "RIGHT"), via_id="e_door")
        assert matched != prev


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

def test_unresolved_exit_gets_place_identity_and_failed_view(tmp_path):
    mem = Memory(tmp_path)
    place = mem.match_place({"screen": "world", "entities": []}, 1, prev=None, cut=True)
    target = "exit: door mat"
    entity = mem.resolve(target, place)
    assert entity and mem.resolve(target, place) == entity
    recent = [{"step": 2, "place": place, "choice": {"verb": "approach", "target": target},
               "verdict": "miss", "outcome": {"effects": ["no_change"]}}]
    view, ids = mem.view("chooser", {"place": place, "task": None, "on_screen": [], "recent": recent})
    assert entity in ids and "Failed here: approach exit: door mat" in view


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


def test_resolve_ambiguous_name_by_nearest_tile(tmp_path):
    from agent.decide import resolve_target
    mem = Memory(tmp_path)
    for i, tile in enumerate(([0, 0], [5, 5])):
        mem.upsert("entities", {"id": f"e_{i}", "kind": "object", "name": f"shelf#{i + 2}", "place": "p", "tile": tile})
    assert mem.resolve("shelf", "p") is None and mem.resolve("shelf", "p", (3, 3)) is None
    assert mem.resolve("shelf", "p", (5, 6)) == "e_1" and resolve_target(mem, "exit: shelf", "p", (1, 0)) == "e_0"


def test_match_place_ignores_exit_prefix_and_follows_leads_to(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_1f", "name": "1f", "edges": [{"verb": "approach", "via": "stairs", "to": "p_2f", "n": 1, "ok": 1}]})
    mem.upsert("places", {"id": "p_2f", "name": "2f", "edges": []})
    mem.upsert("places", {"id": "p_out", "name": "out", "edges": []})
    cut = {"prev": "p_1f", "cut": True}
    assert mem.match_place({"screen": "world"}, 5, via=" Exit: Stairs", **cut) == "p_2f"
    mem.relate("e_door", "leads_to", "p_2f", src="inferred", step=1)
    assert mem.match_place({"screen": "world"}, 5, prev="p_out", cut=True, via="exit: door", via_id="e_door") == "p_2f"


def test_observe_same_name_block_keeps_stable_ids(tmp_path):
    m = Memory(tmp_path)
    frame = {"entities": [{"name": "shelf", "kind": "object"} for _ in range(4)]}
    tiles = {0: (-5, -3), 1: (-4, -3), 2: (-5, -2), 3: (-4, -2)}
    first = m.observe(frame, "p", 1, tiles)
    for step in range(2, 5):
        assert m.observe(frame, "p", step, tiles) == first
    rows = [r for r in m.rows("entities") if r["name"].startswith("shelf")]
    assert len(rows) == 4 and all(r["seen"] == 4 for r in rows)


def test_border_exit_pools_across_kind_strings_resolves_and_places_ignore_transients(tmp_path):
    m = Memory(tmp_path)
    for step, (kind, tile) in enumerate([("exit", (1, 1)), ("exit: path south", (4, 2)), ("Exits", (7, 3)), ("exit: path south", (9, 5))]):
        m.observe({"entities": [{"name": "exit: path south", "kind": kind, "at": [.5, .99]}]}, "p", step, {0: tile})
    exits = [r for r in m.rows("entities") if "path south" in r["name"]]
    assert len(exits) == 1 and exits[0]["seen"] == 4 and "tile" not in exits[0]
    assert m.resolve("path south", "p") == exits[0]["id"] and not [r for r in m.rows("entities") if r.get("src") == "model"]
    tree = {"name": "tree", "kind": "object"}
    first = m.match_place({"screen": "world", "entities": [tree, {"name": "red", "kind": "player"}]}, 1, prev=None, cut=True)
    again = m.match_place({"screen": "world", "entities": [tree, {"name": "hi", "kind": "dialogue_box"}, {"name": "x", "kind": "exit: east"}]}, 2, prev=None, cut=True)
    assert first == again and len(m.rows("places")) == 1


def test_name_place_merges_new_arrival_retargets_refs_and_keeps_established_places(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "new", "name": "screen", "screen": "room", "state": "candidate", "first": 3, "landmarks": [["oak", "npc"]], "edges": []})
    mem.upsert("places", {"id": "hall", "name": "Hall", "screen": "room", "state": "place", "first": 1, "landmarks": [["oak", "npc"]], "edges": []})
    mem.upsert("places", {"id": "start", "name": "start", "screen": "room", "state": "place", "first": 1, "landmarks": [], "edges": [{"verb": "approach", "via": "door", "to": "new", "n": 1, "ok": 1}]})
    mem.upsert("tiles", {"id": "t1", "place": "start", "x": 1, "y": 2, "exit": {"to": "new"}})
    mem.upsert("relations", {"id": "r1", "s": "e1", "p": "leads_to", "o": "new", "state": "live"})
    assert mem.name_place("new", "hall", 3) == "hall"
    assert mem.get("places", "new")["state"] == "merged" and mem.get("places", "new")["into"] == "hall"
    assert mem.get("places", "start")["edges"][0]["to"] == "hall"
    assert mem.get("tiles", "t1")["exit"]["to"] == "hall" and mem.get("relations", "r1")["o"] == "hall"
    assert mem.match_place({"screen": "room", "entities": [{"name": "Oak", "kind": "npc"}]}, 4, prev=None, cut=True) == "hall"
    mem.upsert("places", {"id": "old", "name": "Cave", "screen": "room", "state": "place", "first": 1, "landmarks": [], "edges": []})
    mem.upsert("places", {"id": "other", "name": "Lake", "screen": "room", "state": "place", "first": 5, "landmarks": [], "edges": []})
    assert mem.name_place("other", "cave", 6) == "other" and mem.get("places", "other")["state"] == "place"
    assert mem.name_place("other", "Lakeside", 6) == "other"
    assert mem.get("places", "other")["name"] == "Lakeside" and mem.get("places", "other")["aliases"] == ["Lakeside"]
def test_base_removes_repeated_numeric_suffixes():
    from agent.memory import _base

    assert _base("Furniture#2#3") == "furniture"


def test_revisit_recognised_by_stored_view_with_tile_scroll(tmp_path):
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(0)
    room = rng.integers(1, 4, (144 + 64, 160 + 64), dtype=np.uint8) * 80  # bigger than one screen
    view = lambda x, y: Image.fromarray(room[32 + 16 * y:176 + 16 * y, 32 + 16 * x:192 + 16 * x])
    mem = Memory(tmp_path)
    up = mem.match_place({"screen": "world", "entities": [{"name": "bed", "kind": "object"}]}, 1, prev=None, cut=True)
    mem.remember_view(up, (0, 0), view(0, 0))
    down = mem.match_place({"screen": "world", "entities": [{"name": "table", "kind": "object"}]}, 2, prev=up, cut=True,
                           frame=Image.fromarray(rng.integers(1, 4, (144, 160), dtype=np.uint8) * 80), tile_px=16)
    assert down != up and mem.arrival is None
    # back upstairs, one tile right of the stored view, perceiver names differ: pixels still say it's `up`
    back = mem.match_place({"screen": "world", "entities": [{"name": "machine", "kind": "object"}]}, 3, prev=down, cut=True,
                           frame=view(1, 0), tile_px=16)
    assert back == up and mem.arrival == (up, (1, 0))


def test_views_agree_vetoes_merging_different_rooms(tmp_path):
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(1)
    room = lambda: rng.integers(1, 4, (144 + 64, 160 + 64), dtype=np.uint8) * 80
    up, down = room(), room()
    view = lambda r, x, y: Image.fromarray(r[32 + 16 * y:176 + 16 * y, 32 + 16 * x:192 + 16 * x])
    mem = Memory(tmp_path)
    mem.remember_view("a", (0, 0), view(up, 0, 0))
    mem.remember_view("b", (1, 0), view(up, 1, 0))   # same room, other id
    mem.remember_view("c", (1, 0), view(down, 1, 0))  # different room
    assert mem.views_agree("a", "b", (0, 0), 16) is True
    assert mem.views_agree("a", "c", (0, 0), 16) is False
    assert mem.views_agree("a", "c", (20, 0), 16) is None  # out of reach: no pixel evidence either way


def test_seen_view_only_locates_within_one_place(tmp_path):
    import numpy as np
    from PIL import Image
    room = np.random.default_rng(2).integers(1, 4, (144 + 64, 160 + 64), dtype=np.uint8) * 80
    view = lambda x, y: Image.fromarray(room[32 + 16 * y:176 + 16 * y, 32 + 16 * x:192 + 16 * x])
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "bed", "screen": "dialogue", "state": "place"})  # first seen under a dialogue box
    mem.remember_view("bed", (0, 0), view(0, 0))
    assert mem.seen_view(view(1, 0), "world", None, 16) == ("bed", (1, 0))  # recognised despite its "dialogue" screen type
    assert mem.seen_view(view(1, 0), None, None, 16, only="bed") == ("bed", (1, 0))
