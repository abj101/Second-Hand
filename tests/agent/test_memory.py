import json

from agent.memory import Memory, conf


def test_upsert_restart_replay_and_unknown_store(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("odd_store", {"id": "x", "value": 1})
    mem.upsert("odd_store", {"id": "x", "value": 2})
    assert mem.get("odd_store", "x")["value"] == 2
    restarted = Memory(tmp_path)
    assert restarted.rows("odd_store") == [{"id": "x", "value": 2}]


def test_places_cut_mints_and_modal_keeps_place(tmp_path):
    mem = Memory(tmp_path)
    a = mem.match_place({"screen": "world", "entities": [{"name": "Oak", "kind": "npc"}]}, 1,
                        prev=None, cut=True)
    assert mem.get("places", a)["state"] == "candidate"
    assert "landmarks" not in mem.get("places", a)
    assert mem.match_place({}, 2, prev=a, cut=False) == a
    b = mem.match_place({"screen": "world", "entities": []}, 3, prev=a, cut=True)
    assert b != a and "landmarks" not in mem.get("places", b)
    moved = mem.match_place({"screen": "world", "entities": [{"name": "Oak", "kind": "npc"}]}, 4,
                            prev=b, cut=True)
    assert moved not in (a, b)
    # a battle is a state of the current place, not a place: no new place, no state change
    assert mem.match_place({"screen": "battle", "entities": [{"name": "Rival", "kind": "npc"}]}, 6,
                           prev=moved, cut=True) == moved
    assert len(mem.rows("places")) == 3
    entity = mem.observe({"entities": [{"name": "Oak", "kind": "npc"}]}, a, 7)[0]
    mem.relate(entity, "moves_view", "", src="pixel", step=8, hit=True)
    assert mem.get("places", a)["state"] == "place"


def test_run_start_placeholder_becomes_world_place_without_cut(tmp_path):
    mem = Memory(tmp_path)
    placeholder = mem.match_place({"screen": "dialogue"}, 1, prev=None, cut=True)
    assert mem.get("places", placeholder).get("screen") is None

    place = mem.match_place({"screen": "world", "entities": []}, 2, prev=placeholder, cut=False)

    assert place == placeholder
    assert mem.get("places", place)["screen"] == "world"
    assert len(mem.rows("places")) == 1


def test_identity_kind_drift_edit_distance_tie_and_recurring(tmp_path):
    mem = Memory(tmp_path)
    one = mem.observe({"entities": [{"name": "mom", "kind": "npc"}]}, "p", 1)[0]
    assert mem.observe({"entities": [{"name": "Mom", "kind": "woman"}]}, "p", 2)[0] == one
    assert mem.get("entities", one)["kind"] == "npc"
    assert "Mom" in mem.get("entities", one)["aliases"]
    assert mem.observe({"entities": [{"name": "map", "kind": "npc"}]}, "p", 3)[0] != one
    pair = mem.observe({"entities": [{"name": "bug catcher", "kind": "npc"},
                                      {"name": "bug catcher", "kind": "npc"}]}, "p", 4)
    assert pair[0] != pair[1]
    assert mem.get("entities", pair[1])["name"] == "bug catcher"
    mem.observe({"entities": [{"name": "cat", "kind": "npc"}, {"name": "cut", "kind": "npc"}]}, "q", 5)
    tied = mem.observe({"entities": [{"name": "cot", "kind": "npc"}]}, "q", 6)[0]
    assert mem.get("entities", tied)["name"] == "cot"
    move = mem.observe({"entities": [{"name": "Thunderbolt", "kind": "move"}]}, "p1", 7)[0]
    assert mem.observe({"entities": [{"name": "Thunderbolts", "kind": "move"}]}, "p2", 8)[0] == move


def test_position_identity_relabels_co_visible_things_and_keeps_tracks(tmp_path):
    mem = Memory(tmp_path)
    first = mem.observe({"entities": [{"name": "fence post", "kind": "object"}]}, "p", 1, {0: (2, 3)})[0]
    assert mem.observe({"entities": [{"name": "pillar", "kind": "object"}]}, "p", 2, {0: (2, 3)})[0] == first
    row = mem.get("entities", first)
    assert row["name"] == "fence post" and row["kind"] == "object" and row["aliases"] == ["pillar"]

    frame = {"entities": [{"name": "post", "kind": "object"}, {"name": "barrel", "kind": "object"}]}
    pair = mem.observe(frame, "q", 1, {0: (4, 4), 1: (4, 4)})
    assert pair[0] != pair[1]
    assert mem.observe(frame, "q", 2, {0: (4, 4), 1: (4, 4)}) == pair

    actor = mem.observe({"entities": [{"name": "guard", "kind": "npc"}]}, "r", 1, {0: (0, 0)})[0]
    assert mem.observe({"entities": [{"name": "person", "kind": "npc"}]}, "r", 5, {0: (2, 0)})[0] == actor
    track = mem.observe({"entities": [{"name": "mom", "kind": "npc"}]}, "s", 1, {0: (0, 0)})[0]
    assert mem.observe({"entities": [{"name": "player", "kind": "player"}]}, "s", 2,
                       {0: (8, 8)}, {0: track})[0] == track
    assert mem.get("entities", track)["kind"] == "npc"


def test_tile_identity_survives_scrolling_and_separates_same_name(tmp_path):
    mem = Memory(tmp_path)
    per = {"entities": [{"name": "sign", "kind": "object", "at": [.2, .3]}]}
    first = mem.observe(per, "room", 1, tiles={0: (4, 2)})[0]
    assert mem.observe(per, "room", 2, tiles={0: (5, 3)})[0] == first
    other = mem.observe(per, "room", 3, tiles={0: (7, 3)})[0]
    assert other != first
    assert len(mem.rows("entities")) == 2
    assert mem.observe(per, "room", 4, tiles={0: (7, 3)})[0] == other
    assert len(mem.rows("entities")) == 2


def test_known_exit_precedes_via_relation_and_no_cut_keeps_previous_place(tmp_path):
    mem = Memory(tmp_path)
    a = mem.match_place({"screen": "world", "entities": []}, 1, prev=None, cut=True)
    b = mem.match_place({"screen": "world", "entities": []}, 2, prev=a, cut=True)
    c = mem.match_place({"screen": "world", "entities": [{"name": "Sign", "kind": "object"}]}, 3, prev=b, cut=True)
    mem.relate("e_door", "leads_to", c, src="inferred", step=3)
    mem.upsert("tiles", {"id": f"{a}@4,2", "place": a, "x": 4, "y": 2,
                          "exit": {"dir": "RIGHT", "to": b, "arrive": [0, 0], "src": "observed"}})
    drifted = {"screen": "world", "entities": [{"name": "different landmark", "kind": "npc"}]}
    assert mem.match_place(drifted, 4, prev=a, cut=True, via="door", exit=(4, 2, "RIGHT"), via_id="e_door") == b
    assert mem.match_place(drifted, 5, prev=a, cut=False, via="door", exit=(4, 2, "RIGHT")) == a


def test_cut_without_view_or_exit_mints_despite_matching_landmarks(tmp_path):
    mem = Memory(tmp_path)
    known = mem.match_place({"screen": "world", "entities": [{"name": "Oak", "kind": "npc"}]}, 1,
                            prev=None, cut=True)
    prev = mem.match_place({"screen": "world", "entities": [{"name": "Pine", "kind": "npc"}]}, 2,
                           prev=known, cut=True)

    result = mem.match_place({"screen": "world", "entities": [{"name": "Oak", "kind": "npc"}]}, 3,
                             prev=prev, cut=True)

    assert result not in (known, prev)
    assert "landmarks" not in mem.get("places", result)


def test_cut_ignores_self_loop_exit_edges_and_relations(tmp_path):
    mem = Memory(tmp_path)
    for suffix, path in (("tile", "exit"), ("via", "edge"), ("relation", "relation")):
        mem = Memory(tmp_path / suffix)
        prev = f"p_{suffix}"
        mem.upsert("places", {"id": prev, "name": suffix, "screen": "world", "state": "place", "edges": []})
        if path == "exit":
            mem.upsert("tiles", {"id": f"{prev}@1,2", "place": prev, "x": 1, "y": 2,
                                  "exit": {"dir": "RIGHT", "to": prev, "arrive": [0, 0]}})
        elif path == "edge":
            mem.upsert("places", {**mem.get("places", prev),
                                  "edges": [{"verb": "approach", "via": "door", "to": prev}]})
        else:
            mem.upsert("relations", {"id": "r_self", "s": "e_door", "p": "leads_to", "o": prev, "state": "live"})
        matched = mem.match_place({"screen": "world"}, 2, prev=prev, cut=True, via="door",
                                  exit=(1, 2, "RIGHT"), via_id="e_door")
        assert matched != prev


def test_relations_confidence_and_provenance(tmp_path):
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
def test_match_place_ignores_exit_prefix_and_follows_leads_to(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_1f", "name": "1f", "edges": [{"verb": "approach", "via": "stairs", "to": "p_2f", "n": 1, "ok": 1}]})
    mem.upsert("places", {"id": "p_2f", "name": "2f", "edges": []})
    mem.upsert("places", {"id": "p_out", "name": "out", "edges": []})
    cut = {"prev": "p_1f", "cut": True}
    assert mem.match_place({"screen": "world"}, 5, via=" Exit: Stairs", **cut) not in ("p_2f", "p_1f")  # a label is never arrival identity (HF contract 4)
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


def test_border_exit_pools_across_kind_strings_and_places_ignore_transients(tmp_path):
    m = Memory(tmp_path)
    for step, (kind, tile) in enumerate([("exit", (1, 1)), ("exit: path south", (4, 2)), ("Exits", (7, 3)), ("exit: path south", (9, 5))]):
        m.observe({"entities": [{"name": "exit: path south", "kind": kind, "at": [.5, .99]}]}, "p", step, {0: tile})
    assert not [r for r in m.rows("entities") if "path south" in r["name"]]
    assert not [r for r in m.rows("entities") if r.get("src") == "model"]
    tree = {"name": "tree", "kind": "object"}
    first = m.match_place({"screen": "world", "entities": [tree, {"name": "red", "kind": "player"}]}, 1, prev=None, cut=True)
    again = m.match_place({"screen": "world", "entities": [tree, {"name": "hi", "kind": "dialogue_box"}, {"name": "x", "kind": "exit: east"}]}, 2, prev=None, cut=True)
    assert first != again and len(m.rows("places")) == 2


def test_name_place_same_as_known_place_adds_alias_without_merging(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "new", "screen": "world", "state": "candidate", "first": 3})
    mem.upsert("places", {"id": "hall", "name": "Hall", "screen": "world", "state": "place", "first": 1})

    assert mem.name_place("new", "hall", 3) is None
    assert mem.get("places", "new")["state"] == "candidate"
    assert mem.get("places", "new")["name"] == "hall"
    assert mem.get("places", "new")["aliases"] == ["hall"]
    assert mem.get("places", "hall")["state"] == "place"
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


def test_world_dialogue_menu_world_sequence_on_gridenv_is_one_place(tmp_path):
    from tests.agent.fakes import GridEnv
    env, mem = GridEnv(hud=True), Memory(tmp_path)
    world = {"screen": "world", "entities": [{"name": "npc", "kind": "npc"}]}
    place = mem.match_place(world, 1, prev=None, cut=True, frame=env.screenshot(), tile_px=16)
    mem.remember_view(place, (0, 0), env.screenshot())
    env.text_box = True
    dialogue = mem.match_place({"screen": "dialogue", "text": ["Hello!"], "entities": []}, 2, prev=place, cut=True, frame=env.screenshot(), tile_px=16)
    env.text_box = False; env.set_hud_value(3)
    menu = mem.match_place({"screen": "menu", "entities": [{"name": "Start", "kind": "menu_item"}]}, 3, prev=dialogue, cut=True, frame=env.screenshot(), tile_px=16)
    env.set_hud_value(0)
    back = mem.match_place(world, 4, prev=menu, cut=True, frame=env.screenshot(), tile_px=16)
    assert dialogue == menu == back == place
    assert [p["id"] for p in mem.rows("places") if p.get("screen") == "world"] == [place]


def test_cut_back_from_dialogue_matches_the_place_left_not_a_new_one(tmp_path):
    from tests.agent.fakes import GridEnv
    env, mem = GridEnv(), Memory(tmp_path)
    world = {"screen": "world", "entities": [{"name": "npc", "kind": "npc"}]}
    place = mem.match_place(world, 1, prev=None, cut=True, frame=env.screenshot(), tile_px=16)
    mem.remember_view(place, (0, 0), env.screenshot())
    mem.match_place({"screen": "dialogue", "entities": []}, 2, prev=place, cut=True)
    assert mem.match_place(world, 3, prev=place, cut=True, frame=env.screenshot(), tile_px=16) == place
    mem.match_place({"screen": "dialogue", "entities": []}, 4, prev=place, cut=True)
    room = {"screen": "world", "entities": [{"name": "Sign", "kind": "object"}]}  # another room behind the dialogue
    assert mem.match_place(room, 5, prev=place, cut=True) != place
    assert mem.match_place(room, 6, prev=place, cut=True) != place  # a real move between navigable screens still excludes prev


def test_name_place_refuses_screen_types_and_accepts_references(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p", "screen": "world", "state": "candidate", "first": 1})
    assert mem.name_place("p", "world", 2) is None
    assert "name" not in mem.get("places", "p")
    assert mem.name_place("p", "Town [p_x]", 2) is None
    assert mem.get("places", "p")["name"] == "Town [p_x]"


def test_nameable_rejects_screen_types_only(tmp_path):
    from agent.memory import _nameable, place_name
    assert not _nameable("") and not _nameable("world") and not _nameable("Dialogue")
    assert _nameable("place 3") and _nameable("Town [p_x]")
    assert place_name({"name": "world"}) == ""
    assert place_name({"name": "place 3"}) == "place 3"
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p", "screen": "world", "state": "candidate", "first": 1})
    mem.name_place("p", "place 3", 2)
    assert mem.get("places", "p")["name"] == "place 3"


def test_cut_arrival_uses_a_known_exit_within_one_tile_when_they_agree(tmp_path):
    from agent.space import mark_exit
    mem = Memory(tmp_path)
    for pid in ("down", "up", "yard"): mem.upsert("places", {"id": pid, "screen": "world", "state": "place", "landmarks": [], "edges": []})
    mark_exit(mem, "up", (4, -4), "UP", "down", (0, 0), 1)  # down@0,0 DOWN -> up is inferred; the real stairs press is UP from 0,1
    per = {"screen": "world", "entities": [{"name": "rug", "kind": "object"}]}

    assert mem.match_place(per, 5, prev="down", cut=True, exit=(0, 1, "UP")) == "up"
    mark_exit(mem, "down", (1, 1), "DOWN", "yard", (0, 0), 6)  # two different exits within one tile: no guess
    assert mem.match_place(per, 7, prev="down", cut=True, exit=(0, 1, "UP")) not in ("up", "yard")
    assert mem.match_place(per, 8, prev="down", cut=True, exit=(1, 1, "DOWN")) == "yard"  # the exact exit still wins


def test_first_place_name_sticks_and_later_names_become_aliases(tmp_path):
    mem = Memory(tmp_path); mem.upsert("places", {"id": "p", "screen": "world", "state": "place", "first": 1})
    for step, name in ((2, "tiled room"), (3, "library"), (4, "dining room")): mem.name_place("p", name, step)
    row = mem.get("places", "p")
    assert row["name"] == "tiled room" and row["aliases"] == ["tiled room", "library", "dining room"]


def test_never_crossed_door_with_a_crossed_doors_label_is_a_new_place_not_its_destination(tmp_path):
    # m1_hf_checkpoint_1 step 135: "house door" at another tile resolved by label to where "exit: house door" led; Oak's lab merged into the house
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_out", "screen": "world", "state": "place", "landmarks": [["sign", "object"]], "edges": []})
    mem.upsert("places", {"id": "p_house", "screen": "world", "state": "place", "landmarks": [["bed", "furniture"], ["tv", "object"]], "edges": []})
    mem.relate("e_house_door_1", "leads_to", "p_house", src="inferred", step=8)
    lab = {"screen": "world", "entities": [{"name": "machine", "kind": "object"}, {"name": "bookshelf", "kind": "furniture"}]}
    pid = mem.match_place(lab, 9, prev="p_out", cut=True, via="house door", exit=(-1, 6, "UP"), via_id="e_house_door_2")
    assert pid not in ("p_house", "p_out") and mem.get("places", pid)["state"] == "candidate"


def test_new_record_is_logged_with_the_records_it_did_not_match(tmp_path):
    mem = Memory(tmp_path)
    mem.observe({"entities": [{"kind": "npc", "name": "girl", "at": [.5, .5]}]}, "p", 1, {0: (2, 2)})
    mem.observe({"entities": [{"kind": "npc", "name": "girl", "at": [.9, .5]}]}, "p", 2, {0: (9, 2)})  # too far for the same girl: a second record
    rows = [json.loads(line) for line in (tmp_path / "mints.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["name"] for r in rows] == ["girl", "girl"]
    assert rows[1]["near"][0]["name"] == "girl" and rows[1]["near"][0]["tile_d"] == 7 and rows[1]["near"][0]["name_d"] == 0
