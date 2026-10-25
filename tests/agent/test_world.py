import json
import pytest
from types import SimpleNamespace

from agent.memory import Memory
from agent.world import World
from tests.agent.fakes import GridEnv


def place(mem, id, name, first=1):
    mem.upsert("places", {"id": id, "name": name, "screen": "world", "state": "place", "first": first, "last": first, "edges": [], "visits": 1})  # memory always sets visits

def camera(seed=1):
    import numpy as np
    from PIL import Image
    pixels = np.random.default_rng(seed).integers(0, 256, (700, 700, 3), dtype=np.uint8)
    return lambda x, y: Image.fromarray(pixels[y:y + 144, x:x + 160])

def feed(odometer, frame, seq, x, y, dx, dy):
    count = max(abs(dx), abs(dy)) // 4
    for i in range(1, count + 1):
        seq += 1; odometer(frame(x + dx * i // count, y + dy * i // count), seq)
    return seq, x + dx, y + dy


def test_merge_carries_departure_and_arrival_crossing_counts(tmp_path):
    mem = Memory(tmp_path); world = World(mem, 16)
    for pid in ("src", "dst", "hall"): place(mem, pid, pid)
    mem.upsert("tiles", {"id": "src@1,0", "place": "src", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "hall", "arrive": [0, 0]}, "step": 1})
    for ident, n, last in (("src@1,0>hall", 2, 3), ("dst@3,1>hall", 3, 2), ("hall@9,9>src", 1, 4)):
        mem.upsert("crossings", {"id": ident, "n": n, "last": last})
    world.merge("src", "dst", 5, (2, 1)); world.current_place, world.pos = "dst", (0, 0)
    assert mem.get("crossings", "dst@3,1>hall") == {"id": "dst@3,1>hall", "n": 5, "last": 3}
    assert mem.get("crossings", "hall@9,9>dst")["n"] == 1 and mem.get("crossings", "src@1,0>hall")["n"] == 2
    assert any("crossed 5×" in " ".join(row.get("notes", [])) for row in world.options({"screen": "world", "entities": []}, "dst", {}, 5))


@pytest.mark.parametrize("score", [.6, .49])
def test_minted_place_reports_partial_view_without_merging(tmp_path, monkeypatch, score):
    from agent import memory
    from PIL import Image
    mem = Memory(tmp_path); place(mem, "known", "Hall"); frame = Image.new("RGB", (16, 16), "white")
    mem.remember_view("known", (0, 0), frame)
    monkeypatch.setattr(memory, "_view_score", lambda *_: score)
    minted = mem.match_place({"screen": "world"}, 2, prev=None, cut=True, frame=frame, tile_px=16)
    world = World(mem, 16); world.current_place = minted
    assert minted != "known" and mem.arrival == (minted, (0, 0), "new")
    if score >= .5:
        assert mem.get("places", minted)["looks_like"] == ["known", .6, 2]
        assert ", partly looks like Hall (0.6)" in world.here(minted)
        world.merge(minted, "known", 3, (0, 0))
        assert "partly looks like" not in world.here(world.current_place)
    else:
        assert "looks_like" not in mem.get("places", minted) and "partly looks like" not in world.here(minted)


def test_only_navigable_places_resolve_as_targets(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Hall")
    mem.upsert("places", {"id": "p_place_1", "screen": "title", "state": "candidate", "first": 1})
    world = World(mem, 16)
    assert all(world.ref(value) is None for value in ("p_place_1", "place:p_place_1", "place 1", "title [p_place_1]"))
    assert world.ref("room") == world.ref("Hall") == world.ref("place:room") == "place:room"


def entity(mem, id, name, place, tile, seen=2):
    mem.upsert("entities", {"id": id, "name": name, "kind": "object", "place": place, "tile": tile, "seen": seen})


def test_held_push_refutes_exit_after_every_direction_was_tried(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    mem.upsert("tiles", {"id": "room@0,0", "place": "room", "x": 0, "y": 0, "floor": True,
                         "blocked": ["DOWN"], "pushed": ["LEFT", "RIGHT"], "held": ["UP"], "exit": None, "step": 1})
    entity(mem, "hatch", "exit: hatch", "room", [0, 0])
    world = World(mem, 16)

    assert world._refuted("room", (0, 0)) == 1
    assert world._exits("room", {"room"})[0]["status"] == "refuted"


def test_held_push_is_shown_as_tried_in_exit_options(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    mem.upsert("tiles", {"id": "room@0,0", "place": "room", "x": 0, "y": 0, "floor": True,
                         "blocked": ["DOWN"], "pushed": ["LEFT"], "held": ["UP"], "exit": None, "step": 1})
    entity(mem, "hatch", "exit: hatch", "room", [0, 0])
    world = World(mem, 16); world.current_place, world.pos = "room", (0, 0)

    row = next(o for o in world.options({"screen": "world", "entities": []}, "room", {}, 2)
               if o.get("action", {}).get("push") == "UP")

    assert row["state"] == "failed" and "pressed UP: someone stood there" in row["notes"]


def test_fix_player_swaps_entity_near_locked_self_spot(tmp_path):
    world = World(Memory(tmp_path), 16); world.self_spot, world._self_from_motion = [.5, .5], True
    player = {"name": "Guide", "kind": "player", "at": [.75, .5]}
    npc = {"name": "Hero", "kind": "npc", "at": [.5, .5]}
    per = {"screen": "world", "entities": [player, npc]}

    assert world.fix_player(per, (160, 144)) == {"from": [.75, .5], "to": [.5, .5]}
    assert player["kind"] == "character" and player["name"] == "Guide"
    assert npc["kind"] == "player" and npc["name"] == "player"
    assert world.fix_player(per, (160, 144)) is None


def test_fix_player_inserts_at_locked_spot_or_leaves_perception_alone(tmp_path):
    world = World(Memory(tmp_path), 16); player = {"kind": "player", "at": [.75, .5]}
    per = {"screen": "world", "entities": [player, {"kind": "object", "at": [.75, .8]}]}
    world.self_spot, world._self_from_motion = [.5, .5], True
    assert world.fix_player(per, (160, 144)) == {"from": [.75, .5], "to": [.5, .5]}
    assert player["kind"] == "character" and player["at"] == [.75, .5]
    assert per["entities"][-1] == {"kind": "player", "name": "player", "at": [.5, .5]}

    unchanged = {"kind": "player", "name": "player", "at": [.5, .5]}
    assert world.fix_player({"screen": "world", "entities": [unchanged]}, (160, 144)) is None
    assert world.fix_player({"screen": "menu", "entities": [{"kind": "player", "at": [.75, .5]}]}, (160, 144)) is None
    world.self_spot, world._self_from_motion = None, False
    assert world.fix_player({"screen": "world", "entities": [{"kind": "player", "at": [.75, .5]}]}, (160, 144)) is None


def test_fix_player_uses_half_tile_threshold_and_demotes_other_players(tmp_path):
    world = World(Memory(tmp_path), 16); world.self_spot, world._self_from_motion = [.5, .5], True
    npc = {"kind": "npc", "name": "NPC", "at": [.54, .5]}
    old = {"kind": "player", "name": "player", "at": [.75, .5]}
    per = {"screen": "world", "entities": [npc, old]}
    assert world.fix_player(per, (160, 144)) == {"from": [.75, .5], "to": [.54, .5]}
    assert npc == {"kind": "player", "name": "player", "at": [.54, .5]}
    assert old["kind"] == "character" and old["name"] == "character"

    side = {"kind": "npc", "name": "NPC", "at": [.6, .5]}
    per = {"screen": "world", "entities": [side]}
    assert world.fix_player(per, (160, 144)) == {"from": None, "to": [.5, .5]}
    assert per["entities"][-1] == {"kind": "player", "name": "player", "at": [.5, .5]}


def test_mark_draws_only_fixed_outline_on_copy(tmp_path):
    from PIL import Image

    world = World(Memory(tmp_path), 16); world.self_spot, world._self_from_motion = [.5, .5], True
    raw = Image.new("RGB", (160, 144), "black")
    image, marked = world.mark(raw, True)
    assert marked and image is not raw and raw.getpixel((71, 62)) == (0, 0, 0)
    changed = {(x, y) for y in range(144) for x in range(160) if image.getpixel((x, y)) != raw.getpixel((x, y))}
    expected = {(x, y) for x in range(71, 90) for y in range(62, 82)
                if x in (71, 89) or y in (62, 81)}
    assert changed == expected and all(image.getpixel(p) == (255, 0, 0) for p in expected)
    assert world.mark(raw, False) == (raw, False)


def test_fix_player_keeps_own_player_within_tolerance(tmp_path):
    world = World(Memory(tmp_path), 16); world.self_spot, world._self_from_motion = [.5, .5], True
    player = {"kind": "player", "name": "Guide", "at": [.64, .5]}
    per = {"screen": "world", "entities": [player]}
    assert world.fix_player(per, (160, 144)) == {"from": [.64, .5], "to": [.64, .5]}
    assert player == {"kind": "player", "name": "player", "at": [.64, .5]}
    assert world.fix_player(per, (160, 144)) is None


def test_motion_self_spot_locks_persists_and_unlocks_after_two_contradictions(tmp_path):
    from PIL import Image

    world = World(Memory(tmp_path), 16)
    frame = Image.new("RGB", (160, 144))
    def observe(spot, step, screen="world"):
        move = {"dir": "RIGHT", "shift": (-16, 0), "self_at": spot}
        skill = SimpleNamespace(scroll=(-16, 0), moves=[move])
        world.observe_after({"screen": screen, "entities": []}, frame, True, "room", SIZE, step, skill=skill)

    observe((.5, .5), 1)
    assert world.self_spot == [.5, .5]
    world.before({"screen": "world", "entities": [{"kind": "player", "at": [.31, .5]}]}, "room", "room", SIZE, 2)
    assert world.pa == [.5, .5]
    state = json.loads(json.dumps(world.state()))
    replay = World(Memory(tmp_path), 16); replay.restore(state)
    assert replay.self_spot == [.5, .5]
    observe((.75, .5), 3, "menu")
    assert world.self_spot == [.5, .5]
    observe((.75, .5), 4)
    assert world.self_spot is None


def test_merge_uses_pixel_offset_and_class_not_names(tmp_path):
    mem = Memory(tmp_path); place(mem, "src", "room"); place(mem, "dst", "Hall"); place(mem, "from", "Start")
    entity(mem, "s1", "Different label", "src", [1, 1]); entity(mem, "s2", "Far thing", "src", [20, 20], 3)
    entity(mem, "d1", "Oak", "dst", [5, 6], 4)
    mem.upsert("tiles", {"id": "src@1,1", "place": "src", "x": 1, "y": 1, "floor": True, "blocked": ["UP"], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "dst@5,6", "place": "dst", "x": 5, "y": 6, "floor": False, "blocked": [], "pushed": ["LEFT"], "exit": {"dir": "UP", "to": "from", "arrive": [0, 0]}, "step": 1})
    mem.upsert("tiles", {"id": "from@0,0", "place": "from", "x": 0, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "DOWN", "to": "src", "arrive": [1, 1]}, "step": 1})
    mem.upsert("relations", {"id": "r1", "s": "src", "p": "leads_to", "o": "from", "state": "live"})
    world = World(mem, 16); world.current_place, world.pos = "src", (3, 4)
    assert world.merge("src", "dst", 8, (4, 5))
    assert mem.get("places", "src")["offset"] == [4, 5] and world.pos == (7, 9)
    tile = mem.get("tiles", "dst@5,6")
    assert tile["floor"] is True and tile["blocked"] == ["UP"] and tile["pushed"] == ["LEFT"]
    assert mem.get("tiles", "from@0,0")["exit"]["to"] == "dst" and mem.get("tiles", "from@0,0")["exit"]["arrive"] == [5, 6]
    assert mem.get("relations", "r1")["s"] == "dst"
    assert mem.get("entities", "d1")["seen"] == 6
    assert mem.get("entities", "s1")["state"] == "merged" and mem.get("entities", "s1")["into"] == "d1"
    assert mem.get("entities", "s2")["place"] == "dst" and mem.get("entities", "s2")["tile"] == [24, 25]
    replay = Memory(tmp_path)
    assert replay.get("places", "src")["state"] == "merged" and replay.get("places", "src")["into"] == "dst"
    assert world.canon("thing:s1") == "thing:d1"


def test_place_names_are_write_only_and_never_merge(tmp_path):
    mem = Memory(tmp_path); place(mem, "a", "Room", 1); place(mem, "b", "Room B", 1)
    world = World(mem, 16)
    assert world.name("a", "room b", 3) is None
    assert mem.get("places", "a")["aliases"] == ["room b"] and mem.get("places", "a").get("state") != "merged"
    place(mem, "new", "", 5); place(mem, "old", "Known", 1)
    world.name("new", "known", 5)
    assert mem.get("places", "new")["aliases"] == ["known"] and mem.get("places", "new").get("state") != "merged"
    assert world.name("new", "Known", 6) is None
    assert mem.get("places", "old").get("state") != "merged"


def test_leads_to_returns_only_observed_live_other_places_and_canonicalizes_merges(tmp_path):
    mem = Memory(tmp_path)
    place(mem, "room", "Room"); place(mem, "old", "Old"); place(mem, "live", "Live")
    mem.upsert("places", {**mem.get("places", "old"), "state": "merged", "into": "live", "offset": [0, 0]})
    entity(mem, "building", "Building", "room", [2, 2])
    mem.upsert("entities", {"id": "building_alias", "name": "Old building", "kind": "object", "place": "room", "state": "merged", "into": "building"})
    place(mem, "elsewhere", "Elsewhere"); place(mem, "model_place", "Model place"); place(mem, "disputed_place", "Disputed place")
    rows = [
        {"id": "yes", "s": "building_alias", "p": "leads_to", "o": "old", "src": "observed", "state": "live", "sup": 2, "con": 1},
        {"id": "disputed", "s": "building", "p": "leads_to", "o": "disputed_place", "src": "observed", "state": "live", "sup": 2, "con": 2},
        {"id": "own", "s": "building", "p": "leads_to", "o": "room", "src": "observed", "state": "live", "sup": 2, "con": 0},
        {"id": "model", "s": "building", "p": "leads_to", "o": "model_place", "src": "model", "state": "live", "sup": 2, "con": 0},
        {"id": "unobserved", "s": "building", "p": "leads_to", "o": "elsewhere", "src": "model", "state": "live", "sup": 2, "con": 0},
        {"id": "gone", "s": "building", "p": "leads_to", "o": "gone", "src": "observed", "state": "live", "sup": 2, "con": 0},
    ]
    for row in rows: mem.upsert("relations", row)
    world = World(mem, 16)

    assert world.leads_to("thing:building_alias") == ["place:live"]


def test_here_names_the_thing_that_leads_into_the_place(tmp_path):
    mem = Memory(tmp_path)
    place(mem, "outside", "Outside"); place(mem, "inside", "Room with counters")
    entity(mem, "door", "large house door", "outside", [2, 2])
    mem.upsert("relations", {"id": "entry", "s": "door", "p": "leads_to", "o": "inside", "src": "observed", "state": "live", "sup": 1, "con": 0})
    world = World(mem, 16)

    assert world.here("inside") == "Room with counters, entered through large house door"


def test_here_says_when_the_place_is_unsettled(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); world = World(mem, 16)
    assert world.here("room") == "Room"
    world.unsettled = True
    assert world.here("room") == "Room: the scene changed under the text; where you are is not known yet"


def test_world_before_does_not_store_a_dialogue_place_name(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    world = World(mem, 16)
    world.before({"screen": "dialogue", "place": "Professor Oak's lab", "entities": []}, "room", None, (160, 144), 2)
    assert mem.get("places", "room").get("name") == "Room"
    assert not mem.get("places", "room").get("aliases")


def test_player_label_off_self_spot_keeps_the_actor_id_and_kind(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    entity(mem, "npc", "Mom", "room", [2, 0])
    mem.upsert("entities", {**mem.get("entities", "npc"), "kind": "npc", "aliases": [], "kinds": ["npc"]})
    world = World(mem, 16); world.self_spot, world._self_from_motion = [.5, .5], True
    per = {"screen": "world", "entities": [{"name": "player", "kind": "player", "at": [.7, .5]}]}

    world.fix_player(per, (160, 144))
    _, tiles, ids = world.before(per, "room", None, (160, 144), 3)

    assert tiles == {0: (2, 0)} and ids == {0: "npc"}
    assert mem.get("entities", "npc")["kind"] == "npc"


def test_motion_locked_self_is_not_observed_even_when_mislabeled(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    world = World(mem, 16); world.self_spot, world._self_from_motion = [.5, .5], True
    per = {"screen": "world", "entities": [
        {"name": "Player", "kind": "npc", "at": [.5, .5]},
        {"name": "Girl", "kind": "npc", "at": [.7, .5]}]}

    _, tiles, ids = world.before(per, "room", None, (160, 144), 1)

    assert tiles == {1: (2, 0)} and 0 not in ids and ids[1] == mem.rows("entities")[0]["id"]
    assert [row["name"] for row in mem.rows("entities")] == ["Girl"]
    assert per["entities"][0] == {"name": "Player", "kind": "npc", "at": [.5, .5]}
    from PIL import Image
    world.observe_after(per, Image.new("RGB", (160, 144)), False, "room", (160, 144), 2)  # the loop always passes the settled frame
    assert 0 not in world.entity_ids and [row["name"] for row in mem.rows("entities")] == ["Girl"]


def test_title_screen_things_get_no_tiles_so_the_first_map_does_not_inherit_them(tmp_path):
    mem = Memory(tmp_path); mem.upsert("places", {"id": "p_start", "state": "candidate", "visits": 1, "first": 1, "last": 1})  # run-start place, no screen yet
    world = World(mem, 16); world.self_spot, world._self_from_motion = [.5, .5], True
    title = {"screen": "title", "entities": [{"name": "logo text", "kind": "text", "at": [.7, .5]}]}
    _, tiles, ids = world.before(title, "p_start", None, (160, 144), 1)
    assert tiles == {} and ids == {} and mem.rows("entities") == []
    mem.upsert("places", {**mem.get("places", "p_start"), "screen": "world"})  # the first map frame promotes the placeholder (no cut)
    room = {"screen": "world", "entities": [{"name": "cabinet", "kind": "object", "at": [.7, .5]}]}
    _, tiles, room_ids = world.before(room, "p_start", "p_start", (160, 144), 2)
    assert tiles == {0: (2, 0)} and mem.get("entities", room_ids[0])["tile"] == [2, 0]


def test_pre_map_things_are_no_refs_and_reentry_lands_on_the_matched_exit_arrival(tmp_path):
    mem = Memory(tmp_path); world = World(mem, 16)
    start = mem.match_place({"screen": "title"}, 1, prev=None, cut=True)
    mem.observe({"entities": [{"name": "Professor Oak", "kind": "character"}]}, start, 1)
    room = mem.match_place({"screen": "world", "entities": []}, 2, prev=start, cut=False)
    assert room != start and world.ref("Professor Oak") is None and world.resolve("Professor Oak", {"entities": []}, room) is None
    hall = mem.match_place({"screen": "world", "entities": []}, 3, prev=room, cut=True)
    mem.upsert("tiles", {"id": f"{hall}@3,0", "place": hall, "x": 3, "y": 0, "floor": True, "blocked": [], "pushed": [],
                         "exit": {"dir": "UP", "to": room, "arrive": [4, -5], "src": "observed"}})
    world.current_place, world.pos = hall, (2, 0)  # drifted one tile, pressed another way: the within-1 exit match still says room
    skill = SimpleNamespace(moves=[{"dir": "LEFT"}], scroll=None, after=None)
    world.prepare(skill, hall, True, 4)
    assert mem.match_place({"screen": "world", "entities": []}, 4, prev=hall, cut=True, exit=(2, 0, "LEFT")) == room
    world.after(skill, hall, room, True, 4)
    assert world.pos == (4, -5) and mem.unplaced is None


def test_known_place_without_arrival_evidence_is_unplaced_until_a_view_matches(tmp_path):
    mem = Memory(tmp_path); place(mem, "a", "A"); place(mem, "b", "B")
    world = World(mem, 16); world.self_spot, world._self_from_motion = [.5, .5], True
    skill = SimpleNamespace(moves=[{"dir": "UP"}], scroll=None, after=None)
    world.current_place, world.pos = "a", (3, 3); world.prepare(skill, "a", True, 1); mem.arrival = None
    world.after(skill, "a", "b", True, 1)
    assert mem.unplaced == "b"
    _, tiles, _ = world.before({"screen": "world", "entities": [{"name": "desk", "kind": "object", "at": [.7, .5]}]}, "b", "b", (160, 144), 2)
    assert tiles == {}  # no guessed tile: nothing is placed at a wrong position


def test_inferred_arrival_uses_matching_view_and_corrects_its_exit(tmp_path):
    import numpy as np
    from PIL import Image
    mem = Memory(tmp_path); place(mem, "a", "A"); place(mem, "b", "B")
    row = {"id": "a@1,0", "place": "a", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [],
           "exit": {"dir": "RIGHT", "to": "b", "arrive": [4, 5], "src": "inferred"}, "step": 1}
    mem.upsert("tiles", row); mem.arrival = ("b", (4, 5), "inferred"); mem.arrival_exit = row["id"]
    frame = Image.fromarray(np.random.default_rng(7).integers(1, 4, (144, 160), dtype=np.uint8) * 80); mem.remember_view("b", (7, 8), frame)
    world = World(mem, 16)

    world.before({"screen": "world", "entities": []}, "b", "a", (160, 144), 2, frame=frame)

    assert world.pos == (7, 8) and mem.get("tiles", row["id"])["exit"] == {"dir": "RIGHT", "to": "b", "arrive": [7, 8], "src": "observed"}


def test_observed_arrival_keeps_its_tile_when_a_view_matches_elsewhere(tmp_path):
    import numpy as np
    from PIL import Image
    mem = Memory(tmp_path); place(mem, "a", "A"); place(mem, "b", "B")
    mem.arrival = ("b", (4, 5), "observed")
    frame = Image.fromarray(np.random.default_rng(7).integers(1, 4, (144, 160), dtype=np.uint8) * 80); mem.remember_view("b", (7, 8), frame)
    world = World(mem, 16)

    world.before({"screen": "world", "entities": []}, "b", "a", (160, 144), 2, frame=frame)

    assert world.pos == (4, 5)


def test_inferred_arrival_without_view_keeps_its_tile(tmp_path):
    from PIL import Image
    mem = Memory(tmp_path); place(mem, "a", "A"); place(mem, "b", "B")
    mem.arrival = ("b", (4, 5), "inferred")
    world = World(mem, 16)

    world.before({"screen": "world", "entities": []}, "b", "a", (160, 144), 2, frame=Image.new("RGB", (160, 144), "red"))

    assert world.pos == (4, 5)


def test_inferred_arrival_without_exit_id_uses_view_without_correction(tmp_path):
    import numpy as np
    from PIL import Image
    mem = Memory(tmp_path); place(mem, "a", "A"); place(mem, "b", "B")
    row = {"id": "a@1,0", "place": "a", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [],
           "exit": {"dir": "RIGHT", "to": "b", "arrive": [4, 5], "src": "inferred"}, "step": 1}
    mem.upsert("tiles", row); mem.arrival = ("b", (4, 5), "inferred")
    frame = Image.fromarray(np.random.default_rng(7).integers(1, 4, (144, 160), dtype=np.uint8) * 80); mem.remember_view("b", (7, 8), frame)
    world = World(mem, 16)

    world.before({"screen": "world", "entities": []}, "b", "a", (160, 144), 2, frame=frame)

    assert world.pos == (7, 8) and mem.get("tiles", row["id"])["exit"] == row["exit"]


def test_inferred_arrival_correction_updates_only_the_matched_exit(tmp_path):
    import numpy as np
    from PIL import Image
    mem = Memory(tmp_path); place(mem, "a", "A"); place(mem, "b", "B")
    for ident, x, direction, arrive in (("a@0,0", 0, "RIGHT", [4, 5]), ("a@1,0", 1, "LEFT", [6, 7])):
        mem.upsert("tiles", {"id": ident, "place": "a", "x": x, "y": 0, "floor": True, "blocked": [], "pushed": [],
                              "exit": {"dir": direction, "to": "b", "arrive": arrive, "src": "inferred"}, "step": 1})
    frame = Image.fromarray(np.random.default_rng(7).integers(1, 4, (144, 160), dtype=np.uint8) * 80); mem.remember_view("b", (8, 9), frame)
    assert mem.match_place({"screen": "world", "entities": []}, 2, prev="a", cut=True, exit=(1, 0, "LEFT")) == "b"
    world = World(mem, 16)

    world.before({"screen": "world", "entities": []}, "b", "a", (160, 144), 3, frame=frame)

    assert mem.get("tiles", "a@0,0")["exit"]["arrive"] == [4, 5] and mem.get("tiles", "a@1,0")["exit"] == {"dir": "LEFT", "to": "b", "arrive": [8, 9], "src": "observed"}


def test_place_refs_round_trip_printed_labels(tmp_path):
    mem = Memory(tmp_path); place(mem, "p_room", "Name"); place(mem, "p_x", "Name"); place(mem, "p_place_3", "", 3)
    place(mem, "p_unique", "Only"); place(mem, "p_old", "Old")
    mem.upsert("places", {**mem.get("places", "p_old"), "state": "merged", "into": "p_unique"})
    world = World(mem, 16)

    assert world.ref("name [p_x]") == "place:p_x"
    assert world.ref("Only [p_unique]") == "place:p_unique"
    assert world.ref("Old [p_old]") == "place:p_unique"
    assert world.ref("place 3") == "place:p_place_3"
    assert world.resolve("name [p_x]", {"entities": []}, "p_room")["id"] == "p_x"
    assert world.resolve("place 3", {"entities": []}, "p_room")["id"] == "p_place_3"


def test_history_reads_actor_dialogue_object_checks_and_exit_truth(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); place(mem, "hall", "Hall")
    entity(mem, "mom", "Mom", "room", [0, 0]); entity(mem, "sign", "Sign", "room", [1, 0])
    mem.upsert("entities", {**mem.get("entities", "mom"), "kind": "npc", "called": "MOM", "status": "seen", "last": 9})
    for ident, text, steps in (("mom", "Hi Red", [4, 7]), ("sign", "The sign is old", [8])):
        mem.upsert("relations", {"id": f"says-{ident}", "s": ident, "p": "says", "o": text, "ev": steps, "step": steps[-1]})
    mem.upsert("relations", {"id": "walk", "s": "sign", "p": "does_nothing", "o": "", "con": 2, "ev": [5, 6]})
    mem.upsert("tiles", {"id": "room@2,0", "place": "room", "x": 2, "y": 0, "floor": True, "blocked": ["UP"], "pushed": [],
                         "exit": {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"}, "step": 3})
    world = World(mem, 16); world.current_place = "room"; world._count("room", (2, 0), "hall", 10)

    assert world.history("thing:mom") == {"called": "MOM", "talked": {"steps": [4, 7], "text": "Hi Red"}, "checked": None,
                                           "walked_into": 0, "status": "seen", "last": 9}
    assert world.history("thing:sign")["checked"] == "The sign is old"
    assert world.history("thing:sign")["walked_into"] == 2
    assert world.history("exit:2,0") == {"status": "verified", "uses": 1, "dest": "hall", "blocked": ["UP"]}


def test_inferred_exit_keeps_destination_and_is_routable_without_a_departure(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); place(mem, "bedroom", "Bedroom")
    mem.upsert("tiles", {"id": "room@8,0", "place": "room", "x": 8, "y": 0, "floor": True,
                         "blocked": [], "pushed": [], "exit": {"dir": "UP", "to": "bedroom", "arrive": [0, 0], "src": "inferred"}, "step": 3})
    world = World(mem, 16); world.current_place = "room"
    live = {"room", "bedroom"}

    exit = next(row for row in world._exits("room", live) if row["tile"] == (8, 0))

    assert exit["dest"] == "bedroom" and exit["status"] == "unverified" and exit["uses"] == 0
    assert "label" not in exit and exit["head"] == "Bedroom"
    assert "bedroom" in world._paths("room")
    assert "Bedroom → Bedroom: possible exit, unverified (never crossed)" in world.view("", "room")


def test_loop_closure_needs_two_consistent_view_hits(tmp_path, monkeypatch):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "older", "Older", 1); place(mem, "newer", "Newer", 2)
    world = World(mem, 16); world.current_place = "newer"
    monkeypatch.setattr(mem, "seen_view", lambda *_: ("older", (world.pos[0] + 4, world.pos[1] + 5)))
    frame = Image.new("RGB", (160, 144), "white")
    per = {"screen": "world", "entities": []}

    world.pos = (0, 0); world.observe_after(per, frame, False, "newer", (160, 144), 3)
    assert mem.get("places", "newer").get("state") != "merged"
    world.pos = (1, 0); world.observe_after(per, frame, False, "newer", (160, 144), 4)

    assert mem.get("places", "newer")["state"] == "merged"
    assert mem.get("places", "newer")["into"] == "older"
    assert mem.get("places", "newer")["offset"] == [4, 5]
    assert world.current_place == "older" and world.pos == (5, 5)


def test_resolve_and_plan_skip_merged_history_and_plan_entity_facing(tmp_path):
    mem = Memory(tmp_path)
    for key in ("a", "b", "c", "old"): place(mem, key, key)
    mem.upsert("places", {**mem.get("places", "old"), "state": "merged", "into": "c"})
    for key, pos, dest in (("a", (1, 0), "b"), ("b", (1, 0), "c"), ("old", (0, 0), "c")): mem.upsert("tiles", {"id": f"{key}@{pos[0]},{pos[1]}", "place": key, "x": pos[0], "y": pos[1], "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": dest, "arrive": [0, 0]}, "step": 1})
    for pos in ((0, 0), (2, 0)): mem.upsert("tiles", {"id": f"c@{pos[0]},{pos[1]}", "place": "c", "x": pos[0], "y": pos[1], "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
    entity(mem, "fern", "Fern", "c", [1, 0]); entity(mem, "ghost", "Ghost", "old", [0, 0])
    mem.upsert("entities", {**mem.get("entities", "fern"), "status": "remembered"})
    world = World(mem, 16); world.current_place = "a"
    target = world.resolve("Fern", {"entities": []}, "a")
    assert target == {"kind": "memory", "place": "c", "tile": (1, 0), "id": "fern", "stale": False}
    assert world.resolve("Ghost", {"entities": []}, "a") is None
    legs = world.plan(target)
    assert [leg["place"] for leg in legs] == ["a", "b", "c"]
    assert legs[-1]["dirs"] == ["RIGHT"]
    world.current_place = "old"
    assert world.plan({**target, "place": "c"}) is None


def test_plan_unknown_exit_pushes_outward_and_travel_reports_uncrossed(tmp_path, monkeypatch):
    from agent import world as world_module
    from agent.skills import Ctx, SkillResult
    from tests.agent.fakes import StubModel

    mem = Memory(tmp_path); place(mem, "room", "room")
    entity(mem, "door", "exit: doorway", "room", (-5, 6))
    mem.upsert("entities", {**mem.get("entities", "door"), "kind": "exit"})
    mem.upsert("tiles", {"id": "room@-5,0", "place": "room", "x": -5, "y": 0, "floor": True,
                          "blocked": [], "pushed": [], "exit": None, "step": 1})
    world = World(mem, 16); world.current_place, world.pos = "room", (-5, 6)
    target = world.resolve("exit:-5,6", {"screen": "world", "entities": []}, "room")

    legs = world.plan(target)

    assert legs[-1] == {"place": "room", "dirs": [], "push": "DOWN", "expect": None,
                        "cross": True, "exit_tile": (-5, 6), "target": target}
    attempted = {}
    def no_cut(ctx, _target, _player, *, exit_target=None, route=None):
        attempted.update(exit_target=exit_target, route=route)
        frame = ctx.env.screenshot()
        return SkillResult("blocked", "exit not crossed", 1, 0, frame, frame, (0, 0), False,
                           failure={"stage": "crossing", "reason": "exit not crossed"})
    monkeypatch.setattr(world_module, "approach", no_cut)
    result = world.travel(Ctx(GridEnv(), StubModel([]), 16), legs, 2)

    assert attempted == {"exit_target": "exit", "route": {"dirs": [], "push": "DOWN"}}
    assert result.status == "blocked" and result.failure == {"stage": "crossing", "reason": "exit not crossed", "place": "room", "tile": (-5, 6)}


def test_plan_unknown_exit_scene_cut_is_open_travel(tmp_path, monkeypatch):
    from agent import world as world_module
    from agent.skills import Ctx, SkillResult
    from tests.agent.fakes import StubModel

    mem = Memory(tmp_path); place(mem, "room", "room")
    entity(mem, "door", "exit: doorway", "room", (-5, 6))
    mem.upsert("entities", {**mem.get("entities", "door"), "kind": "exit"})
    mem.upsert("tiles", {"id": "room@-5,0", "place": "room", "x": -5, "y": 0, "floor": True,
                          "blocked": [], "pushed": [], "exit": None, "step": 1})
    world = World(mem, 16); world.current_place, world.pos = "room", (-5, 6)
    target = world.resolve("exit:-5,6", {"screen": "world", "entities": []}, "room")
    legs = world.plan(target)
    assert legs[-1] == {"place": "room", "dirs": [], "push": "DOWN", "expect": None,
                        "cross": True, "exit_tile": (-5, 6), "target": target}

    frame = GridEnv().screenshot(); marked = []
    monkeypatch.setattr(world_module, "approach", lambda *_a, **_k: SkillResult(
        "done", "scene cut", 1, 0, frame, frame, (0, 0), False,
        [{"dir": "DOWN", "shift": (0, 0), "self_at": None}]))
    monkeypatch.setattr(world_module, "mark_exit", lambda *args: marked.append(args))
    result = world.travel(Ctx(GridEnv(), StubModel([]), 16), legs, 2)

    assert result.status == "done" and world._travel_open
    assert marked == [] and world.current_place == "room"
    assert [row["id"] for row in mem.rows("places")] == ["room"]
    assert mem.get("tiles", "room@-5,0")["exit"] is None


def test_plan_routes_to_unknown_entity_neighbor_around_blocked_edges(tmp_path):
    from agent.space import DIRS
    from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "room", "room"); entity(mem, "fern", "Fern", "room", [-1, 1])
    mem.upsert("tiles", {"id": "room@-1,0", "place": "room", "x": -1, "y": 0, "floor": False, "blocked": [], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "room@0,0", "place": "room", "x": 0, "y": 0, "floor": True, "blocked": ["DOWN", "RIGHT"], "pushed": [], "exit": None, "step": 1})
    env, model = GridEnv(), StubModel([]); world = World(mem, 16); world.current_place = "room"
    target = world.resolve("Fern", {"entities": []}, "room")

    legs = world.plan(target)

    dirs = legs[-1]["dirs"]; at = (0, 0)
    for direction in dirs[:-1]:
        dx, dy = DIRS[direction]; at = (at[0] + dx, at[1] + dy)
        assert at != (-1, 0)
    assert at == (0, 1) and dirs[-1] == "LEFT" and dirs[0] not in ("DOWN", "RIGHT")
    assert mem.get("tiles", "room@0,0")["blocked"] == ["DOWN", "RIGHT"]
    assert len(mem.rows("tiles")) == 2 and model.calls == 0 and env.player == (8, 8)


def test_travel_leg_cap_and_blocked_leg_reason(tmp_path, monkeypatch):
    from agent.skills import Ctx, SkillResult; from tests.agent.fakes import GridEnv, StubModel; from agent import world as world_module

    mem = Memory(tmp_path); place(mem, "room", "room")
    env, model = GridEnv(), StubModel([]); world = World(mem, 16); world.current_place = "room"
    def consume(ctx, target, player, **kwargs): frame = ctx.env.screenshot(); return SkillResult("done", "", ctx.max_inputs, 0, frame, frame, (0, 0), False)
    monkeypatch.setattr(world_module, "approach", consume)
    legs = [{"place": "room", "dirs": ["UP"] * 30, "push": None} for _ in range(5)]
    result = world.travel(Ctx(env, model, 16), legs, 1)
    assert result.inputs == 120
    assert result.calls == model.calls == 0 and result.reason == "travel: leg 5 input limit"
    frame = env.screenshot(); monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("blocked", "route blocked", 1, 0, frame, frame, (0, 0), False, [("UP", (0, 0))]))
    result = world.travel(Ctx(env, model, 16), [{"place": "room", "dirs": ["UP"], "push": None}], 2)
    assert result.reason == "travel: leg 1 route blocked"


def test_travel_corrects_wrong_arrival_from_exact_trace_frame(tmp_path, monkeypatch):
    import hashlib; from PIL import Image; from agent.skills import Ctx, SkillResult; from agent.trace import Trace; from agent import world as world_module; from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "source", "Source"); place(mem, "expected", "Expected"); place(mem, "actual", "Actual")
    row = {"id": "source@1,0", "place": "source", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "expected", "arrive": [0, 0]}}
    mem.upsert("tiles", row); frame = Image.new("RGB", (8, 8), "red"); digest = hashlib.sha256(frame.tobytes()).hexdigest()
    trace = Trace(tmp_path / "trace.jsonl")
    trace.write({"place": "source", "next_place": "actual", "tile": [3, 4]})
    trace.write({"place": "actual", "frame_hash": digest, "next_place": "expected", "tile": [99, 99]})
    env, model = GridEnv(), StubModel([]); world = World(mem, 16); world.current_place = "source"
    monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("done", "scene cut", 1, 0, frame, frame, (0, 0), False, [("RIGHT", (0, 0))]))
    result = world.travel(Ctx(env, model, 16), [{"place": "source", "dirs": ["RIGHT"], "push": "RIGHT", "expect": "expected", "exit_tile": (1, 0)}], 3, trace)
    assert model.calls == 0 and result.reason == "travel: leg 1 arrival mismatch"
    assert world.current_place == "actual" and world.pos == (3, 4)
    assert [r["id"] for r in mem.rows("crossings")] == ["source@1,0>actual"]  # travel counts only the departure
    assert {k: mem.get("tiles", row["id"])["exit"][k] for k in ("to", "arrive")} == {"to": "actual", "arrive": [3, 4]}
    modal = Trace(tmp_path / "modal.jsonl"); modal.write({"place": "source", "next_place": "actual", "tile": [3, 4]}); modal.write({"place": "actual", "screen": "dialogue", "frame_hash": digest})
    world2 = World(mem, 16); world2.current_place = "source"
    assert world2.travel(Ctx(env, model, 16), [{"place": "source", "dirs": ["RIGHT"], "push": "RIGHT", "expect": "expected", "exit_tile": (1, 0)}], 4, modal).reason == "travel: leg 1 modal opened"


def test_loop_travel_fetch_leg_traverses_three_rooms_and_faces_remembered_target(tmp_path, monkeypatch):
    from PIL import ImageDraw
    from types import SimpleNamespace
    from agent.loop import Loop
    from agent import loop as loop_module, world as world_module
    from tests.agent.fakes import GridEnv, StubModel

    class ThreeRooms(GridEnv):
        def __init__(self): super().__init__(); self.room, self.facing, self.npc = 0, None, (9, 8)
        def _frame(self):
            if self._transition:
                self._transition += 1
                if self._transition > 2: self.room += 1; self.scene = self.room; self.player = (8, 8); self._transition = 0
            elif self._moving:
                self._travel += 2
                if self._travel >= self.tile_px:
                    x, y = self.player; dx, dy = self._steps[self._direction]; self.player, self._travel, self._moving = (x+dx, y+dy), 0, False
                    if self.player == self.door and self.room < 2: self._transition = 1
        def press(self, key, frames=8, on_frame=None):
            if key in self._steps:
                self._direction = self.facing = key; dx, dy = self._steps[key]; dest = (self.player[0]+dx, self.player[1]+dy)
                self._moving = dest not in self.walls and not (self.room == 2 and dest == self.npc)
            for _ in range(frames+8):
                self._frame()
                if on_frame:
                    image = self.screenshot()
                    if key in self._steps and not self._moving and dest != self.player and _ < 4: image.putpixel((78, 72), (245, 140, 120))
                    on_frame(image)
            self._direction = ""
        def screenshot(self):
            image = super().screenshot()
            if self.room == 2: ImageDraw.Draw(image).rectangle((88, 64, 103, 79), fill=(88, 77, 171))
            return image

    mem = Memory(tmp_path / "memory"); [mem.upsert("places", {"id": p, "name": p, "screen": "world", "state": "place", "edges": []}) for p in ("a", "b", "c")]
    for p, q in (("a", "b"), ("b", "c")): mem.upsert("tiles", {"id": f"{p}@1,0", "place": p, "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": q, "arrive": [0, 0]}})
    for x in (0, 2): mem.upsert("tiles", {"id": f"c@{x},0", "place": "c", "x": x, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": None})
    entity(mem, "fern", "Fern", "c", [1, 0]); mem.upsert("entities", {**mem.get("entities", "fern"), "status": "remembered"})
    env = ThreeRooms(); player = {"name": "Player", "kind": "player", "at": [.5, .5]}; fern = {"name": "Fern", "kind": "npc", "at": [.6, .5]}
    initial = {"screen": "world", "text": [], "entities": [player]}; final = {**initial, "entities": [player, fern]}; model = StubModel([SimpleNamespace(data=initial), SimpleNamespace(data=final)])
    loop = Loop(env, model, mem, "grid"); loop.place, loop.prev_frame = "a", env.screenshot()
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    acts, calls = [], []
    monkeypatch.setattr(loop_module, "act", lambda *_a, **_k: (acts.append(model.calls) or SimpleNamespace(place="a", ops=[], action={"go_to": "Fern"}, expect="screen_changes", why="test", text=None)))
    approach = world_module.approach
    def watch(ctx, *args, **kwargs):
        before = ctx.model.calls; result = approach(ctx, *args, **kwargs); calls.append((before, ctx.model.calls)); return result
    monkeypatch.setattr(world_module, "approach", watch)
    row = loop.step()
    assert acts == [1] and model.calls == 2 and calls == [(1, 1)] * 3
    assert row["next_place"] == "c" and env.room == 2 and env.player == (8, 8) and env.facing == "RIGHT" and abs(env.player[0]-env.npc[0])+abs(env.player[1]-env.npc[1]) == 1
    assert row["act"]["action"]["go_to"] == "Fern" and row["skill"]["reason"] == ""


def test_evidence_counts_distinct_world_frames_and_restores_seen_entities(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room")
    world = World(mem, 16); player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    item = {"name": "Coin", "kind": "object", "at": [.6, .5]}
    world.before({"screen": "world", "entities": [player, item], "text": []}, "room", None, (160, 144), 1)
    ident = next(r["id"] for r in mem.rows("entities") if r["name"] == "Coin")
    for step, color in ((2, "red"), (3, "blue")):
        world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), color), False, "room", (160, 144), step)
        assert mem.get("entities", ident)["misses"] == step - 1
        assert mem.get("entities", ident)["status"] == ("stale" if step == 3 else "remembered")
    frame = Image.new("RGB", (160, 144), "green")
    world.observe_after({"screen": "world", "entities": [player]}, frame, True, "room", (160, 144), 4)
    assert mem.get("entities", ident)["misses"] == 2
    modal = {"name": "Text box", "kind": "dialogue_box", "at": [.5, .9]}
    world.observe_after({"screen": "world", "entities": [player, modal]}, Image.new("RGB", (160, 144), "yellow"), False, "room", (160, 144), 5)
    assert mem.get("entities", ident)["misses"] == 2
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "purple"), False, "room", (160, 144), 6)
    assert mem.get("entities", ident)["misses"] == 3 and mem.get("entities", ident)["status"] == "stale"
    world.observe_after({"screen": "world", "entities": [player, item]}, Image.new("RGB", (160, 144), "black"), False, "room", (160, 144), 7)
    assert mem.get("entities", ident)["status"] == "seen" and mem.get("entities", ident)["misses"] == 0
    for step, color in zip(range(8, 14), ("white", "orange", "cyan", "gray", "pink", "brown")):
        world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), color), False, "room", (160, 144), step)
    assert mem.get("entities", ident)["misses"] == 6 and mem.get("entities", ident)["status"] == "stale"
    assert any(o["ref"] == f"thing:{ident}" for o in world.options({"screen": "world", "entities": [player]}, "room", {}, 13, targets=[f"thing:{ident}"]))


def test_seen_cells_union_the_full_view_rectangles(tmp_path):
    from PIL import Image
    from types import SimpleNamespace
    from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    env = GridEnv()
    world.tracker.wrap(env); env.tick()
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    world.before({"screen": "world", "entities": [player], "text": []}, "room", None, (160, 144), 1)
    world.observe_after({"screen": "world", "entities": [player]}, env.screenshot(), False, "room", (160, 144), 1)
    env.press("RIGHT", frames=16)
    world.prepare(SimpleNamespace(moves=[("RIGHT", (-16, 0))]), "room", False, 2)
    frame = env.screenshot()
    world.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 2)
    cells = set(mem.get("seen", "room")["cells"])
    assert len(cells) == 99 and {"-4,-4", "5,4", "-3,-4", "6,4"} <= cells
    world.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 3)
    assert len((tmp_path / "seen.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_tiers_keep_landmarks_promote_interest_blockers_and_cap_is_not_deletion(tmp_path):
    from types import SimpleNamespace
    from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room")
    mem.upsert("tiles", {"id": "room@-2,0", "place": "room", "x": -2, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": None})
    p = {"name": "Player", "kind": "player", "at": [.5, .5]}
    entities = [p, {"name": "Oak", "kind": "object", "at": [.6, .5]},
                {"name": "Guide", "kind": "npc", "at": [.4, .5]},
                {"name": "exit: path south", "kind": "affordance", "at": [.3, .5]},
                {"name": "Chest", "kind": "object", "at": [.55, .6]},
                {"name": "Sign", "kind": "object", "at": [.45, .6]}]
    env = GridEnv(); env.walls.add((10, 8)); world = World(mem, 16)
    _, _, ids = world.before({"screen": "world", "entities": entities, "text": ["Sign"]}, "room", None, (160, 144), 1)
    oak, guide, exit_id, chest, sign = (ids[i] for i in (1, 2, 3, 4, 5))
    assert mem.get("entities", oak)["tier"] == "landmark"
    assert mem.get("entities", guide)["tier"] == "interest"
    assert mem.get("entities", chest)["tier"] == "landmark" and mem.get("entities", sign)["tier"] == "interest"
    mem.upsert("tiles", {"id": "room@-3,0", "place": "room", "x": -3, "y": 0, "floor": True, "blocked": ["RIGHT"], "pushed": [], "exit": None})
    world._tiers({"entities": [entities[3]], "text": []}, "room", {0: exit_id})
    assert mem.get("entities", exit_id)["tier"] == "landmark"
    world.tracker.wrap(env); env.tick(); at = world.odometer.seq; env.press("RIGHT")
    assert world.odometer.pixels_since(at, world.odometer.seq) == (-16, 0)
    bump_at = world.odometer.seq; env.press("RIGHT")
    assert world.odometer.pixels_since(bump_at, world.odometer.seq) == (0, 0)
    world.prepare(SimpleNamespace(moves=[{"dir": "RIGHT", "shift": (-16, 0), "at": at}, {"dir": "RIGHT", "shift": (0, 0), "at": bump_at}]), "room", False, 2)
    assert mem.get("entities", oak)["tier"] == "interest"
    for i in range(10):
        mem.upsert("entities", {"id": f"landmark{i}", "name": f"Landmark {i}", "kind": "object", "place": "room", "tier": "landmark", "seen": i})
    assert len([r for r in mem.rows("entities") if r.get("tier") == "landmark"]) > 8


def test_actor_observation_radius_and_status_reset(tmp_path):
    mem = Memory(tmp_path)
    ident = mem.observe({"entities": [{"name": "Guide", "kind": "npc"}]}, "room", 1, {0: (0, 0)})[0]
    mem.upsert("entities", {**mem.get("entities", ident), "status": "stale", "misses": 3})
    assert mem.observe({"entities": [{"name": "Guide", "kind": "person"}]}, "room", 13, {0: (4, 0)}) == {0: ident}
    assert mem.get("entities", ident)["status"] == "seen" and mem.get("entities", ident)["misses"] == 0


def test_resolve_place_labels_precede_entity_names_and_gone_targets_remain_executable(tmp_path):
    mem = Memory(tmp_path); place(mem, "p_world", "Town"); place(mem, "p_other", "TOWN"); place(mem, "p_2f", "2F")
    entity(mem, "world-thing", "world", "p_2f", [1, 0])
    entity(mem, "gone-chest", "Chest", "p_2f", [2, 0])
    mem.upsert("entities", {**mem.get("entities", "gone-chest"), "status": "gone", "tier": "landmark"})
    world = World(mem, 16); world.current_place = "p_2f"
    screen = {"entities": [{"name": "world", "kind": "object"}]}

    assert world.place_label("p_world") == "Town [p_world]"
    assert world.resolve("Town [p_world]", screen, "p_2f")["id"] == "p_world"
    assert world.resolve("p_world", screen, "p_2f")["id"] == "p_world"
    assert world.resolve("Chest", {"entities": []}, "p_2f") == {
        "kind": "memory", "place": "p_2f", "tile": (2, 0), "id": "gone-chest", "stale": False}
    assert world.resolve("world", screen, "p_2f")["kind"] == "screen"


def test_world_view_is_bounded_deterministic_promotes_goal_and_caps_landmark_map(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium"); place(mem, "other", "Kitchen"); place(mem, "merged", "Kitchen old")
    mem.upsert("places", {**mem.get("places", "merged"), "state": "merged", "into": "other"})
    mem.upsert("tiles", {"id": "here@0,0", "place": "here", "x": 0, "y": 0, "floor": True, "blocked": ["RIGHT"], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "here@0,-1", "place": "here", "x": 0, "y": -1, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "UP", "to": "other", "arrive": [2, 3]}, "step": 1})
    mem.upsert("tiles", {"id": "other@2,3", "place": "other", "x": 2, "y": 3, "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
    mem.upsert("seen", {"id": "here", "cells": ["3,2", "4,2"], "step": 5})
    entity(mem, "goal", "Chest", "here", [1, 0], 2)
    mem.upsert("entities", {**mem.get("entities", "goal"), "tier": "landmark", "status": "stale", "last": 4})
    entity(mem, "gone", "Vanished", "here", [2, 0]); mem.upsert("entities", {**mem.get("entities", "gone"), "tier": "interest", "status": "gone"})
    entity(mem, "merged-ghost", "Merged ghost", "merged", [1, 1]); mem.upsert("entities", {**mem.get("entities", "merged-ghost"), "tier": "interest"})
    for i in range(10):
        entity(mem, f"landmark{i}", f"Landmark {i}", "here", [i - 4, 2], i)
        mem.upsert("entities", {**mem.get("entities", f"landmark{i}"), "tier": "landmark", "last": i})
    entity(mem, "kitchen-maple", "Maple", "other", [2, 3]); mem.upsert("entities", {**mem.get("entities", "kitchen-maple"), "tier": "interest", "status": "remembered"})
    world = World(mem, 16); world.current_place, world.pos = "here", (0, 0)

    view = world.view("Find Chest near Maple", "here")

    assert view == world.view("Find Chest near Maple", "here") and len(view) <= 2000
    assert view.startswith("Here: Atrium (0,0)  map: 2 walked, 2 seen,")
    assert "Map: . walked, , seen, # blocked side, E exit already used, @ you" in view
    assert "E at (0,-1) Kitchen → Kitchen: possible exit, unverified (never crossed)" in view and "Place: Kitchen" in view
    assert "Thing A Chest (1, 0) object last seen (1, 0) step 4; may have moved" in view
    assert "Vanished" not in view and "Merged ghost" not in view
    assert mem.get("entities", "goal")["tier"] == "interest"
    assert all(f"Landmark {i}" in view for i in range(2, 10))
    assert all(f"Landmark {i}" not in view for i in range(2))
    assert view.index("Place: Kitchen") < view.index("Edges:")


def test_world_view_place_ranking_prefers_goal_overlap_and_keeps_duplicate_labels_unique(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Town"); place(mem, "p_world", "Town"); place(mem, "goal", "Forest")
    place(mem, "recent", "Cave", first=9)
    for src, dst, ident in (("here", "goal", "a"), ("here", "recent", "b")):
        mem.upsert("tiles", {"id": f"{src}@{ident}", "place": src, "x": 0 if ident == "a" else 1, "y": 0,
                              "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": dst, "arrive": [0, 0]}, "step": 1})
    world = World(mem, 16); world.current_place = "here"
    labels = [world.place_label(p["id"]) for p in mem.rows("places") if p.get("state") != "merged"]

    view = world.view("reach forest", "here")

    assert len(labels) == len(set(labels)) and "Town [here]" in labels and "Town [p_world]" in labels
    assert view.index("Place: Forest") < view.index("Place: Cave")


def test_world_view_groups_entities_and_hides_unseen_model_stubs(tmp_path):
    from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "here", "Atrium")
    for i in range(17):
        entity(mem, f"shelf-{i}", "shelf#11#16" if i == 16 else f"shelf#{i + 1}", "here", [i % 5, 0], i + 1)
        row = mem.get("entities", f"shelf-{i}")
        mem.upsert("entities", {**row, "tier": "interest", "status": "stale" if i == 0 else "remembered"})
    mem.upsert("entities", {"id": "stub", "name": "path east", "kind": "affordance", "place": "here",
                            "src": "model", "tile": None, "seen": 0, "tier": "interest"})
    env, model = GridEnv(), StubModel([])
    world = World(mem, 16); world.current_place, world.pos = "here", (0, 0)
    before = mem.rows("entities")

    view = world.view("", "here")

    shelf_lines = [line for line in view.splitlines() if "shelf" in line.casefold()]
    assert shelf_lines == ["Thing A shelf ×17 (0, 0) object remembered step 0 (nearest (0, 0))"]
    assert "path east" not in view and "stub" not in view
    assert mem.rows("entities") == before and model.calls == 0 and env.player == (8, 8)


def _legend_world(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    ids = ["a", "b", "c", "d", "f", "g", "h", "i", "j"]
    for n, ident in enumerate(ids):
        entity(mem, ident, f"Object {ident}", "room", (n - 4, 0), n + 1)
        mem.upsert("entities", {**mem.get("entities", ident), "tier": "landmark" if n == 8 else "interest"})
    world = World(mem, 16); world.current_place = "room"
    return mem, world


def test_world_view_legend_handles_resolve_and_persist(tmp_path):
    mem, world = _legend_world(tmp_path)

    view = world.view("", "room")

    assert "Thing C Object c (-2, 0) object remembered step 0" in view
    assert "Landmark J Object j (4, 0) object remembered step 0" in view
    assert world.ref("Thing C") == world.ref("C") == "thing:c"
    assert world.ref("landmark J") == "thing:j"
    assert world.ref("Thing Z") is None
    restored = World(mem, 16); restored.restore(json.loads(json.dumps(world.state())))
    assert restored.ref("Thing C") == "thing:c" and restored.ref("landmark J") == "thing:j"

    for row in mem.rows("entities"):
        mem.upsert("entities", {**row, "tier": "transient"})
    world.view("", "room")
    assert world.legend == {} and world.ref("C") is None


def test_world_view_legend_exit_resolves_to_exit_thing(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); place(mem, "hall", "Hall")
    entity(mem, "stairs", "exit: stairs", "room", (1, 0))
    mem.upsert("entities", {**mem.get("entities", "stairs"), "kind": "exit", "tier": "interest"})
    mem.upsert("tiles", {"id": "room@1,0", "place": "room", "x": 1, "y": 0, "floor": True,
                          "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "hall", "arrive": [0, 0]}, "step": 1})
    world = World(mem, 16); world.current_place = "room"

    view = world.view("", "room")

    assert "Thing A stairs (1, 0) exit remembered step 0" in view
    assert world.legend["A"] == "thing:stairs" and world.ref("Thing A") == "thing:stairs"


def test_exact_thing_name_precedes_legend_symbol(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    entity(mem, "a", "B", "room", (0, 0)); entity(mem, "b", "Other", "room", (1, 0))
    for ident in ("a", "b"):
        mem.upsert("entities", {**mem.get("entities", ident), "tier": "interest"})
    world = World(mem, 16); world.current_place = "room"

    world.view("", "room")

    assert world.legend["B"] == "thing:b"
    assert world.ref("B") == "thing:a" and world.ref("Thing B") == "thing:b"


def test_called_thing_name_resolves_and_ties(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    entity(mem, "scientist", "scientist", "room", (0, 0))
    mem.upsert("entities", {**mem.get("entities", "scientist"), "called": "OAK"})
    world = World(mem, 16)

    assert world.ref("OAK") == world.ref("oak") == "thing:scientist"

    entity(mem, "scientist_2", "scientist", "room", (1, 0))
    mem.upsert("entities", {**mem.get("entities", "scientist_2"), "called": "OAK"})
    assert {ident for ident, _ in world.ties("oak")} == {"thing:scientist", "thing:scientist_2"}


def test_text_tier_name_is_shown_as_a_guess_in_options_and_view(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}, {"name": "NPC", "kind": "npc", "at": [.6, .5]}]}
    world = World(mem, 16); world.before(per, "room", None, (160, 144), 1)
    ident = next(iter(world.entity_ids.values())); mem.call(ident, "PROF", 2, tier="text")

    assert world.label(f"thing:{ident}") == "NPC, maybe PROF"
    assert world.ref("PROF") == f"thing:{ident}"
    assert any("talk to NPC, maybe PROF" in row["label"] for row in world.options(per, "room", {}, 2, inputs=GridEnv().inputs()))
    assert "NPC, maybe PROF" in world.view("", "room")


def test_said_and_legacy_called_names_are_shown_without_maybe(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    entity(mem, "npc", "NPC", "room", [1, 0])
    mem.call("npc", "PROF", 2)
    world = World(mem, 16)
    assert world.label("thing:npc") == "PROF"
    mem.upsert("entities", {**mem.get("entities", "npc"), "called_src": None})
    assert world.label("thing:npc") == "PROF"


def test_name_shared_across_places_resolves_to_the_one_here(tmp_path):
    mem = Memory(tmp_path); place(mem, "town", "Town"); place(mem, "lab", "Lab")
    for ident, at in (("e_oak", "town"), ("e_customer", "lab")):
        entity(mem, ident, "man", at, (0, 0)); mem.upsert("entities", {**mem.get("entities", ident), "called": "OAK"})
    world = World(mem, 16); world.current_place = "lab"

    assert world.ref("OAK") == "thing:e_customer" and world.ties("OAK") == []
    entity(mem, "e_oak_2", "man", "lab", (1, 0)); mem.upsert("entities", {**mem.get("entities", "e_oak_2"), "called": "OAK"})
    assert world.ref("OAK") is None and len(world.ties("OAK")) == 3


def test_world_view_lists_all_place_exits_before_any_other_place_things(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium")
    for name in ("North", "South", "West"):
        place(mem, name.lower(), name)
        mem.upsert("tiles", {"id": f"{name.lower()}@0,0", "place": name.lower(), "x": 0, "y": 0,
                              "floor": True, "blocked": [], "pushed": [],
                              "exit": {"dir": "DOWN", "to": "here", "arrive": [0, 0]}, "step": 1})
        entity(mem, f"thing-{name}", f"Thing {name}", name.lower(), [1, 0])
        mem.upsert("entities", {**mem.get("entities", f"thing-{name}"), "tier": "interest"})
    entity(mem, "lamp", "Lamp", "here", [1, 0])
    mem.upsert("entities", {**mem.get("entities", "lamp"), "tier": "interest"})
    world = World(mem, 16); world.current_place = "here"

    view = world.view("", "here")

    headings = [view.index(f"Place: {name}") for name in ("North", "South", "West")]
    exit_rows = [line for line in view.splitlines() if line == "  Exit Atrium → Atrium: possible exit, unverified (never crossed)"]
    exits = [view.index(line) for line in exit_rows]
    first_other_things = view.find("Things: ")
    assert len(set(headings)) == 3 and len(exits) == 3 and view.index("Things here: Atrium") < min(headings)
    assert max(headings + exits) < first_other_things and len(view) <= 2000


def test_world_view_keeps_drawn_letters_in_legend_when_crowded(tmp_path):
    from agent.world import MAP_H, MAP_W, VIEW_TOKENS

    mem, world = _legend_world(tmp_path)
    for i in range(20):
        pid = f"place-{i}"
        place(mem, pid, f"Place {i}")
        for n in range(8):
            entity(mem, f"{pid}-thing-{n}", f"Object {n} at {pid}", pid, (n, 0))
            mem.upsert("entities", {**mem.get("entities", f"{pid}-thing-{n}"), "tier": "interest"})
    for i in range(50):
        x, y = i % 10 - 5, i // 10 - 2
        entity(mem, f"local-{i:02}", f"Local {i} " + "word " * 35, "room", (x, y))
        mem.upsert("entities", {**mem.get("entities", f"local-{i:02}"), "tier": "interest"})
        mem.upsert("tiles", {"id": f"room@{x},{y}", "place": "room", "x": x, "y": y, "floor": True, "blocked": [], "pushed": [], "step": 1})
    view = world.view("", "room")
    rows = view.splitlines()
    grid = rows[1:1 + MAP_H]
    legend = {line.split()[1] for line in rows if line.startswith(("Thing ", "Landmark ")) and len(line.split()) > 2}
    drawn = {c for row in grid for c in row if c.isalnum() and c not in "@E"}

    assert drawn <= legend and len(world.legend) < 50 and len(view) <= VIEW_TOKENS * 4
    x0, y0 = world.pos[0] - MAP_W // 2, world.pos[1] - MAP_H // 2
    assert grid[2 - y0][4 - x0] == "."  # the last entity's legend does not fit, so its floor tile stays plain


def test_world_view_labels_other_place_detail_bundle(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium"); place(mem, "hall", "Hall")
    for source, target, direction in (("here", "hall", "RIGHT"), ("hall", "here", "LEFT")):
        mem.upsert("tiles", {"id": f"{source}@0,0", "place": source, "x": 0, "y": 0, "floor": True,
                              "blocked": [], "pushed": [], "exit": {"dir": direction, "to": target, "arrive": [0, 0]}, "step": 1})
    entity(mem, "chest", "Chest", "hall", [1, 0])
    mem.upsert("entities", {**mem.get("entities", "chest"), "tier": "interest"})
    entity(mem, "lamp", "Lamp", "here", [1, 0])
    mem.upsert("entities", {**mem.get("entities", "lamp"), "tier": "interest"})
    world = World(mem, 16); world.current_place = "here"

    view = world.view("", "here")

    lines = view.splitlines()
    assert "Things here: Atrium" in lines
    assert lines.index("Place: Hall") < lines.index("  Exit Atrium → Atrium: possible exit, unverified (never crossed)") < lines.index("Things: Hall")
    assert lines[lines.index("Things: Hall") + 1] == "  Thing: Chest remembered"


def test_memory_call_newer_same_tier_name_replaces_and_world_uses_called_name(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    entity(mem, "person", "person", "room", [1, 0])
    mem.call("person", "MOM", 41)
    mem.call("person", "MOTHER", 42)
    assert mem.get("entities", "person")["called"] == "MOTHER"

    mem.upsert("entities", {**mem.get("entities", "person"), "kind": "npc", "tier": "interest", "status": "seen"})
    world = World(mem, 16); world.current_place = "room"
    assert "Thing A MOTHER" in world.view("", "room")
    assert world.resolve("mother", {"entities": []}, "room")["id"] == "person"


def test_loop_act_uses_world_facts_active_quests_and_labels(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent.loop import Loop
    from agent import loop as loop_module
    from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path / "act"); place(mem, "home", "Home"); place(mem, "p_world", "Town"); place(mem, "p_world2", "Town")
    mem.upsert("tiles", {"id": "home@0,0", "place": "home", "x": 0, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "p_world", "arrive": [1, 0]}, "step": 1})
    entity(mem, "chest", "Chest", "home", [1, 0]); mem.upsert("entities", {**mem.get("entities", "chest"), "tier": "landmark"})
    entity(mem, "oak", "Oak", "home", [2, 0]); mem.upsert("entities", {**mem.get("entities", "oak"), "tier": "landmark"})
    per = {"screen": "world", "text": [], "entities": [{"name": "Player", "kind": "player", "at": [550, 500]},
                                                                  {"name": "Chest", "kind": "object", "at": [650, 500]}]}
    env = GridEnv(wander=True); model = StubModel([SimpleNamespace(data=per), SimpleNamespace(data=per)]); loop = Loop(env, model, mem, "grid")
    loop.place, loop.prev_frame = "home", env.screenshot()
    loop.world.self_spot, loop.world._self_from_motion = [.55, .5], True
    t1 = loop.notebook.heard(0, None, "Home", "Chest is in Home.")
    loop.notebook.apply_ops([{"op": "add", "key": "find_chest", "text": "Find Chest", "why": [t1]}, {"op": "top", "key": "find_chest"}], 0)
    seen = {}
    monkeypatch.setattr(loop_module, "act", lambda *args, **kwargs: (seen.update(focus=args[2], options=args[3]) or
                      SimpleNamespace(place="Home", ops=[], action=None, expect="none", why="test", text=None)))

    loop.step()

    assert "Goal: Find Chest" in seen["focus"] and "Chest is in Home" in seen["focus"]
    assert "Here: Home" in seen["focus"]
    assert any(row["label"].startswith("check Chest,") and row["action"] == {"thing": 1, "verb": "interact"} for row in seen["options"])
    assert mem.get("entities", "chest")["tier"] == "interest" and mem.get("entities", "oak")["tier"] == "landmark"



def test_evidence_actor_stales_after_twenty_steps(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    npc = {"name": "Guide", "kind": "npc", "at": [.4, .5]}
    _, _, ids = world.before({"screen": "world", "entities": [player, npc], "text": []}, "room", None, (160, 144), 1)
    ident = ids[1]
    world.pos = (100, 0)  # Keep this test about off-screen aging, not in-view absence.
    place(mem, "other", "other")
    mem.upsert("entities", {"id": "remote", "name": "Remote", "kind": "object", "place": "other", "status": "seen", "misses": 0, "last": 1})
    mem.upsert("entities", {"id": "remote_npc", "name": "Remote NPC", "kind": "npc", "place": "other", "status": "remembered", "misses": 0, "last": 1})
    place(mem, "merged", "old"); mem.upsert("places", {**mem.get("places", "merged"), "state": "merged"})
    mem.upsert("entities", {"id": "merged_npc", "name": "Merged NPC", "kind": "npc", "place": "merged", "status": "remembered", "misses": 0, "last": 1})
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "red"), False, "room", (160, 144), 21)
    assert mem.get("entities", ident)["status"] == "stale" and mem.get("entities", ident)["misses"] == 0
    assert mem.get("entities", "remote")["status"] == "remembered"
    assert mem.get("entities", "remote_npc")["status"] == "stale"
    assert mem.get("entities", "merged_npc")["status"] == "remembered"


def test_miss_hash_survives_world_checkpoint(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    item = {"name": "Coin", "kind": "object", "at": [.55, .5]}
    ids = world.before({"screen": "world", "entities": [player, item], "text": []}, "room", None, (160, 144), 1)[2]
    ident, frame = ids[1], Image.new("RGB", (160, 144), "red")
    world.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 2)
    restored = World(mem, 16); restored.restore(world.state())
    restored.observe_after({"screen": "world", "entities": [player]}, frame, False, "room", (160, 144), 3)
    assert mem.get("entities", ident)["misses"] == 1


def test_evidence_actor_is_remembered_then_stale_after_twenty_steps(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room"); world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    npc = {"name": "Guide", "kind": "npc", "at": [.4, .5]}
    ident = world.before({"screen": "world", "entities": [player, npc], "text": []}, "room", None, (160, 144), 1)[2][1]
    world.pos = (100, 0)  # Keep this test about off-screen aging, not in-view absence.
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "red"), False, "room", (160, 144), 2)
    assert mem.get("entities", ident)["status"] == "remembered"
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "blue"), False, "room", (160, 144), 20)
    assert mem.get("entities", ident)["status"] == "remembered"
    world.observe_after({"screen": "world", "entities": [player]}, Image.new("RGB", (160, 144), "green"), False, "room", (160, 144), 21)
    assert mem.get("entities", ident)["status"] == "stale"


def _drift_world(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "Room")
    world = World(mem, 16)
    player = {"name": "Player", "kind": "player", "at": [.5, .5]}
    thing = {"name": "Coin", "kind": "object", "at": [.55, .5]}
    world.before({"screen": "world", "entities": [player, thing]}, "room", None, SIZE, 1)
    shifted = {"screen": "world", "entities": [player, {**thing, "at": [.65, .5]}]}
    return world, shifted, Image.new("RGB", (160, 144))


def test_drift_fix_requires_two_world_observations(tmp_path):
    world, shifted, frame = _drift_world(tmp_path)

    world.observe_after(shifted, frame, False, "room", SIZE, 2)
    assert world.drift == (-1, 0) and world.pos == (0, 0)
    drift_ref = world.drift_ref
    dialogue = {**shifted, "screen": "dialogue"}
    assert world.observe_after(dialogue, frame, False, "room", SIZE, 3) is None
    assert world.drift == (-1, 0) and world.drift_ref is drift_ref and world.pos == (0, 0)


def test_dialogue_between_world_frames_preserves_drift_confirmation(tmp_path):
    world, shifted, frame = _drift_world(tmp_path)

    world.observe_after(shifted, frame, False, "room", SIZE, 2)
    drift_ref = world.drift_ref
    assert world.observe_after({**shifted, "screen": "dialogue"}, frame, False, "room", SIZE, 3) is None
    assert world.drift == (-1, 0) and world.drift_ref is drift_ref and world.pos == (0, 0)
    assert world.observe_after(shifted, frame, False, "room", SIZE, 4) == (-1, 0)
    assert world.pos == (-1, 0) and world.drift is None and world.drift_ref is None


def test_modal_world_frame_does_not_change_pending_drift(tmp_path):
    world, shifted, frame = _drift_world(tmp_path)

    world.observe_after(shifted, frame, False, "room", SIZE, 2)
    drift_ref = world.drift_ref
    modal = {"name": "Text box", "kind": "dialogue_box", "at": [.5, .9]}
    with_modal = {**shifted, "entities": [*shifted["entities"], modal]}
    assert world.observe_after(with_modal, frame, False, "room", SIZE, 3) is None
    assert world.drift == (-1, 0) and world.drift_ref is drift_ref and world.pos == (0, 0)


def test_tiers_preserve_transient_interest_and_promote_offscreen_text_names(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    world = World(mem, 16)
    ui_id = mem.observe({"entities": [{"name": "Start", "kind": "menu_item"}]}, "room", 1)[0]
    mem.upsert("entities", {**mem.get("entities", ui_id), "tier": "interest"})
    note_id = mem.upsert("entities", {"id": "map", "name": "Old Map", "kind": "object", "place": "room", "tier": "landmark", "status": "remembered", "misses": 0})["id"]
    world.before({"screen": "menu", "entities": [{"name": "Start", "kind": "menu_item"}], "text": []}, "room", None, (160, 144), 2)
    world._tiers({"entities": [], "text": ["The Old Map points north"]}, "room", {})
    assert mem.get("entities", ui_id)["tier"] == "interest"
    assert mem.get("entities", note_id)["tier"] == "interest"


SIZE = (160, 144)

def _per(env, extra=()):
    per = env.perception()
    per["entities"].extend(extra)
    return per

def _drive(world, env, steps, start=1, extra=lambda step: ()):
    """RIGHT/LEFT alternately (the door is never reached), fed to World as the loop feeds it."""
    if world.odometer.seq is None: world.tracker.wrap(env); env.tick()
    for step in range(start, start + steps):
        before = env.screenshot()
        world.before(_per(env, extra(step)), "room", "room", SIZE, step)
        key, x0, seq = ("RIGHT" if env.player[0] <= 8 else "LEFT"), env.player[0], world.odometer.seq
        env.press(key)
        moved = env.player[0] - x0
        scroll = (-16 * moved, 0)
        assert world.odometer.pixels_since(seq, world.odometer.seq) == scroll
        skill = SimpleNamespace(scroll=scroll, moves=[(key, scroll if moved else (0, 0))], status="done", reason="", inputs=1, calls=0)
        world.prepare(skill, "room", False, step); world.after(skill, "room", "room", False, step)
        world.observe_after(_per(env, extra(step)), env.screenshot(), False, "room", SIZE, step, before=before, skill=skill)

def test_anchor_transient_dialogue_box_is_screen_and_not_in_view(tmp_path):
    mem = Memory(tmp_path); env = GridEnv(); world = World(mem, 16)
    box = {"name": "dialogue box", "kind": "dialogue_box", "at": [.5, .9]}
    _drive(world, env, 2, extra=lambda step: [box])
    row = next(r for r in mem.rows("entities") if r["name"] == "dialogue box")
    assert row["anchor"] == "screen" and row["transient"] is True
    assert "HUD dialogue box" not in world.view("", "room")


def test_world_dialogue_overlay_still_observes_room_things(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); world = World(mem, 16)
    per = {"screen": "world", "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "Coin", "kind": "object", "at": [.6, .5]},
        {"name": "Text box", "kind": "dialogue_box", "at": [.5, .9]},
    ]}
    world.before(per, "room", None, (160, 144), 1)
    ident = world.entity_ids[1]
    assert mem.get("entities", ident)["tile"] == [2, 0]


@pytest.mark.parametrize("screen,entities", [
    ("battle", [{"name": "hp_bar", "kind": "hp_bar"}, {"name": "button", "kind": "button"}]),
    ("keyboard", [{"name": "keyboard", "kind": "keyboard"}, {"name": "button", "kind": "button"}]),
])
def test_non_world_before_skips_world_entity_observation(tmp_path, monkeypatch, screen, entities):
    mem = Memory(tmp_path); place(mem, "room", "Room"); world = World(mem, 16)
    old = {"id": "thing:old", "name": "old", "kind": "object", "place": "room", "tile": [1, 1], "seen": 1}
    mem.upsert("entities", old)
    before = mem.rows("entities")
    monkeypatch.setattr(mem, "observe", lambda *a, **k: pytest.fail("modal screen reached Memory.observe"))
    per = {"screen": screen, "entities": entities}
    world.before(per, "room", None, (160, 144), 1)
    assert world._tiles(per, [0.5, 0.5], (160, 144), "room") == {}
    assert world.entity_ids == {} and mem.rows("entities") == before


def test_non_world_after_skips_world_entity_observation(tmp_path, monkeypatch):
    mem = Memory(tmp_path); place(mem, "room", "Room"); world = World(mem, 16)
    world.entity_ids = {0: "thing:stale"}
    world.entity_tiles = {"thing:known": (0, 0)}
    monkeypatch.setattr(mem, "observe", lambda *a, **k: pytest.fail("modal screen reached Memory.observe"))
    world.observe_after({"screen": "battle", "entities": [{"name": "button", "kind": "button"}]}, None, False, "room", (160, 144), 1)
    assert world.entity_ids == {} and world.entity_tiles == {"thing:known": (0, 0)}

def test_anchor_hud_change_is_flagged_in_view(tmp_path):
    mem = Memory(tmp_path); env = GridEnv(hud=True); world = World(mem, 16)
    _drive(world, env, 1)
    row = next(r for r in mem.rows("entities") if r["name"] == "hp bar")
    mem.upsert("entities", {**row, "anchor": "screen"})
    frame = env.screenshot()
    at = next(e["at"] for e in env.perception()["entities"] if e["name"] == "hp bar")
    world._hud[row["id"]] = {"crop": frame.crop(world._box(frame, [at[i] * SIZE[i] for i in (0, 1)])), "changed": None}
    env.set_hud_value(3); _drive(world, env, 1, start=2)
    assert "HUD hp bar changed at step" in world.view("", "room")

def test_old_anchor_checkpoint_keys_are_ignored_and_motion_spot_restores(tmp_path):
    mem = Memory(tmp_path); env = GridEnv(hud=True, wander=True); world = World(mem, 16)
    _drive(world, env, 3)
    world.self_spot, world._self_from_motion = [.5, .5], True
    state = json.loads(json.dumps(world.state()))
    assert "cam_px" not in state and "evidence" not in state
    state.update(cam_px=[-16, 0], evidence={"old": [[0, 0, 0, False]]})
    replay = World(Memory(tmp_path), 16); replay.restore(state)
    assert replay.self_spot == world.self_spot and replay._self_from_motion

def test_anchor_stand_in_applies_only_while_anchor_unknown(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); world = World(mem, 16)  # the loop always has the place row (match_place mints it)
    per = {"screen": "world", "text": [], "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "guard", "kind": "npc", "at": [.3, .5]}, {"name": "bard", "kind": "npc", "at": [.7, .5]}]}
    _, _, ids = world.before(per, "room", "room", SIZE, 1)
    mem.upsert("entities", {**mem.get("entities", ids[1]), "anchor": "world"})
    _, tiles, _ = world.before(per, "room", "room", SIZE, 2)
    assert tiles[1] not in world.actor_tiles and tiles[2] in world.actor_tiles


def test_locked_world_treats_other_player_label_as_actor_tile(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room")
    world = World(mem, 16); world.self_spot, world._self_from_motion = [.5, .5], True
    per = {"screen": "world", "entities": [
        {"name": "player", "kind": "player", "at": [.5, .5]},
        {"name": "player", "kind": "player", "at": [.5, .6111111111111112]},
    ]}

    _, tiles, _ = world.before(per, "room", "room", SIZE, 1)
    assert tiles[1] == (0, 1) and tiles[1] in world.actor_tiles
    for step in (2, 3):
        skill = SimpleNamespace(moves=[{"dir": "DOWN", "shift": None, "no_response": True}], scroll=(0, 0))
        world.prepare(skill, "room", False, step)
        world.after(skill, "room", "room", False, step)
        world.before(per, "room", "room", SIZE, step + 1)
        tile = mem.get("tiles", "room@0,0")
        if step == 2: assert tile.get("silent", {}).get("DOWN") == 2 and "DOWN" not in tile.get("blocked", [])
        else: assert "DOWN" in tile.get("held", []) and "DOWN" not in tile.get("blocked", [])

def test_before_reuses_ids_after_observe_after_cached_perception(tmp_path, monkeypatch):
    from PIL import Image

    mem = Memory(tmp_path)
    calls = 0
    observe = mem.observe

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return observe(*args, **kwargs)

    monkeypatch.setattr(mem, "observe", counted)
    world = World(mem, 16)
    first = {"screen": "world", "entities": [{"name": "chair", "kind": "object"}]}
    second = {"screen": "world", "entities": [{"name": "chair", "kind": "object"}]}
    world.before(first, "p", None, (160, 144), 1)
    world.observe_after(second, Image.new("RGB", (160, 144)), False, "p", (160, 144), 2)
    world.before(second, "p", "p", (160, 144), 3)

    assert calls == 2


def test_boot_title_and_dialogues_leave_one_navigable_place_with_no_screen_label(tmp_path):
    mem = Memory(tmp_path); world = World(mem, 16)
    run_start = mem.match_place({"screen": "title", "text": ["PRESS START"], "entities": []}, 1, prev=None, cut=True)
    place = run_start
    for step in range(2, 7):
        place = mem.match_place({"screen": "dialogue", "text": [f"line {step}"], "entities": []}, step, prev=place, cut=True)
    arrived = mem.match_place({"screen": "world", "entities": [{"name": "npc", "kind": "npc"}]}, 7, prev=place, cut=True)
    assert place == run_start and arrived != run_start
    assert [p["id"] for p in mem.rows("places") if p.get("screen") == "world"] == [arrived]
    assert not {world.place_label(p["id"]) for p in mem.rows("places")} & {"title", "dialogue", "menu", "world"}
    world.name(run_start, "Hall", 2)
    assert "name" not in mem.get("places", run_start)  # a run-start place is never named
    world.name(arrived, "Hall", 8)
    assert world.place_label(arrived) == "Hall"


def test_model_screen_type_is_never_a_name_but_reference_labels_are_allowed(tmp_path):
    mem = Memory(tmp_path); world = World(mem, 16)
    arrived = mem.match_place({"screen": "world", "entities": [{"name": "npc", "kind": "npc"}]}, 1, prev=None, cut=True)
    for step, bad in enumerate(("world", "dialogue", "Menu", "Town [p_place]", "world [p_world]"), 2):
        world.name(arrived, bad, step)
    row = mem.get("places", arrived)
    assert row["name"] == "Town [p_place]" and row["aliases"] == ["Town [p_place]", "world [p_world]"]
    world.name(arrived, "Hall", 8); world.name(arrived, "Lake", 9)
    assert mem.get("places", arrived)["name"] == "Town [p_place]" and mem.get("places", arrived)["aliases"] == ["Town [p_place]", "world [p_world]", "Hall", "Lake"]


def test_legacy_screen_type_alias_does_not_match_but_bracketed_reference_is_allowed(tmp_path):
    mem = Memory(tmp_path); world = World(mem, 16)
    mem.upsert("places", {"id": "p_old", "name": "Hall", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": [], "aliases": ["world", "world [p_old]"]})
    arrived = mem.match_place({"screen": "world", "entities": [{"name": "sign", "kind": "object"}]}, 2, prev=None, cut=True)
    world.name(arrived, "world", 3); world.name(arrived, "world [p_old]", 3)
    assert mem.get("places", "p_old")["aliases"] == ["world", "world [p_old]"]
    assert mem.get("places", arrived)["name"] == "world [p_old]"


def test_refuted_thing_renders_evidence_and_is_never_deleted(tmp_path):
    mem = Memory(tmp_path); place(mem, "here", "Atrium")
    entity(mem, "door", "doorway", "here", [0, -1], 2)
    mem.upsert("entities", {**mem.get("entities", "door"), "kind": "exit", "tier": "interest"})
    mem.upsert("relations", {"id": "dn", "s": "door", "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 3, "ev": [104, 105, 107], "state": "refuted", "verb": "buttons", "step": 107})
    mem.upsert("relations", {"id": "say", "s": "door", "p": "says", "o": "Hi", "src": "model", "sup": 0, "con": 0, "ev": [], "state": "live", "why": "says the same as before (step 90)", "step": 91})
    world = World(mem, 16); world.current_place, world.pos = "here", (0, 0)
    view = world.view("", "here")
    assert "doorway" in view and "not working: walked into 3×, steps 104–107; says the same as before (step 90)" in view
    assert any(r["id"] == "door" for r in mem.rows("entities"))
    mem.upsert("relations", {"id": "dn", "s": "door", "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 1, "ev": [104], "state": "live", "verb": "buttons", "step": 104})
    assert "not working" not in world.view("", "here")


def test_view_exit_table_refuted_thing_and_run_start_place(tmp_path):
    from agent.world import VIEW_TOKENS
    mem = Memory(tmp_path); place(mem, "here", "Atrium"); place(mem, "hall", "Hall"); place(mem, "garden", "Garden"); place(mem, "shop", "Shop")
    mem.upsert("places", {"id": "run_start", "state": "place", "first": 1, "last": 1, "visits": 1, "edges": []})  # no screen: the unnamed run-start place
    mem.upsert("places", {**mem.get("places", "here"), "edges": [{"verb": "approach", "via": "door", "to": "hall", "n": 2, "ok": 2}]})
    for src, dst, direction, at in (("here", "hall", "RIGHT", [2, 0]), ("here", "garden", "UP", [0, -1]), ("hall", "here", "LEFT", [0, 0])):
        mem.upsert("tiles", {"id": f"{src}@{at[0]},{at[1]}", "place": src, "x": at[0], "y": at[1], "floor": True, "blocked": [], "pushed": [],
                              "exit": {"dir": direction, "to": dst, "arrive": [0, 0]}, "step": 1})
    entity(mem, "side", "exit: side door", "here", [-1, 0])
    mem.upsert("entities", {**mem.get("entities", "side"), "kind": "exit"})
    mem.upsert("relations", {"id": "lead", "s": "side", "p": "leads_to", "o": "shop", "src": "observed", "sup": 1, "con": 0, "ev": [], "state": "live", "step": 5})
    entity(mem, "sign", "sign", "here", [1, 0])
    mem.upsert("relations", {"id": "dn", "s": "sign", "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 3, "ev": [104, 105, 107], "state": "refuted", "verb": "buttons", "step": 107})
    world = World(mem, 16); world.current_place, world.pos = "here", (0, 0)

    view = world.view("", "here")

    assert "E at (2,0) Hall → Hall: possible exit, unverified (never crossed)" in view and "E at (0,-1) Garden → Garden: possible exit, unverified (never crossed)" in view  # edge counts per destination no longer count
    assert "E at (-1,0) side door: possible exit, unverified (never crossed)" in view and "  Exit Atrium → Atrium: possible exit, unverified (never crossed)" in view
    assert "Don't believe: sign (not working: walked into 3×, steps 104–107)" in view
    assert "Place: Hall" in view and "run_start" not in view and "Place: place" not in view and "place 1" not in view
    assert len(view) <= VIEW_TOKENS * 4


@pytest.mark.parametrize("src", ["inferred", "observed"])
def test_travel_inferred_arrival_uses_view_but_observed_arrival_keeps_precedence(tmp_path, monkeypatch, src):
    import numpy as np
    from PIL import Image; from agent.skills import Ctx, SkillResult; from agent import world as world_module; from tests.agent.fakes import GridEnv, StubModel
    mem = Memory(tmp_path); place(mem, "source", "Source"); place(mem, "expected", "Expected")
    edge = {"dir": "RIGHT", "to": "expected", "arrive": [0, 0], "src": src}
    mem.upsert("tiles", {"id": "source@1,0", "place": "source", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": edge, "step": 1})
    frame = Image.fromarray(np.random.default_rng(7).integers(1, 4, (144, 160), dtype=np.uint8) * 80); mem.remember_view("expected", (2, 1), frame)
    monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("done", "scene cut", 1, 0, frame, frame, (0, 0), False, [("RIGHT", (0, 0))]))
    world = World(mem, 16); world.current_place = "source"
    result = world.travel(Ctx(GridEnv(), StubModel([]), 16), [{"place": "source", "dirs": ["RIGHT"], "push": "RIGHT", "expect": "expected", "exit_tile": (1, 0)}], 5)
    assert result.status == "done" and world.current_place == "expected" and world.pos == ((2, 1) if src == "inferred" else (0, 0))
    assert mem.get("tiles", "source@1,0")["exit"] == ({**edge, "arrive": [2, 1], "src": "observed"} if src == "inferred" else edge)


def test_travel_leg_through_a_matching_exit_counts_the_departure(tmp_path, monkeypatch):
    from PIL import Image; from agent.skills import Ctx, SkillResult; from agent import world as world_module; from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "source", "Source"); place(mem, "expected", "Expected")
    mem.upsert("tiles", {"id": "source@1,0", "place": "source", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "expected", "arrive": [0, 0]}, "step": 1})
    frame = Image.new("RGB", (8, 8), "red")
    monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("done", "scene cut", 1, 0, frame, frame, (0, 0), False, [("RIGHT", (0, 0))]))
    world = World(mem, 16); world.current_place = "source"
    result = world.travel(Ctx(GridEnv(), StubModel([]), 16), [{"place": "source", "dirs": ["RIGHT"], "push": "RIGHT", "expect": "expected", "exit_tile": (1, 0)}], 5)

    assert result.status == "done" and world.current_place == "expected"
    assert [r["id"] for r in mem.rows("crossings")] == ["source@1,0>expected"]


ROUTE_DIRS = [("RIGHT", 1, 0), ("LEFT", -1, 0), ("UP", 0, -1), ("DOWN", 0, 1)]


def _route_world(mem, env):  # player starts at (8, 8); callers route it towards a blocker two tiles away
    from agent.skills import Ctx; from tests.agent.fakes import StubModel
    world = World(mem, 16); world.current_place = "room"; world.pa = [.5, .5]; world.pos = (8, 8)
    world.tracker.wrap(env)
    return world, Ctx(env, StubModel([]), 16, self_spot=(.5, .5), odometer=world.odometer)


@pytest.mark.parametrize("guarded", [True, False])
@pytest.mark.parametrize("key, dx, dy", ROUTE_DIRS)
def test_route_blocked_expect_leg_is_not_an_exit_mismatch(tmp_path, monkeypatch, key, dx, dy, guarded):
    from tests.agent.fakes import GridEnv; from agent import world as world_module

    mem = Memory(tmp_path); place(mem, "room", "room"); place(mem, "hall", "hall")
    blocker, exit_tile = (8 + 2 * dx, 8 + 2 * dy), (8 + 3 * dx, 8 + 3 * dy)
    mem.upsert("tiles", {"id": f"room@{exit_tile[0]},{exit_tile[1]}", "place": "room", "x": exit_tile[0], "y": exit_tile[1], "floor": True, "blocked": [], "pushed": [], "step": 1,
                         "exit": {"dir": key, "to": "hall", "arrive": [0, 0], "src": "observed"}})
    exit_before = mem.get("tiles", f"room@{exit_tile[0]},{exit_tile[1]}")["exit"]
    env = GridEnv(); env.door, env.npc = (-5, -5), blocker
    world, ctx = _route_world(mem, env); world.entity_tiles = {"guard": blocker} if guarded else {}
    monkeypatch.setattr(world_module, "route", lambda *_a, **_k: None)  # this case asserts the no-detour route failure; R1 detours are covered separately
    legs = [{"place": "room", "dirs": [key] * 3, "push": key, "expect": "hall", "exit_tile": exit_tile},
            {"place": "hall", "dirs": ["LEFT"], "push": None}]

    result = world.travel(ctx, legs, 1)

    expected = {"stage": "route", "reason": "route blocked", "direction": key, "place": "room", "tile": (8 + dx, 8 + dy)}
    assert result.status == "blocked" and result.reason == "travel: leg 1 route blocked"
    assert result.inputs == 2 and env.player == (8 + dx, 8 + dy)
    assert result.failure == ({**expected, "blocker_id": "guard"} if guarded else expected)
    assert world.current_place == "room" and world.pos == (8 + dx, 8 + dy) and not world._travel_open
    assert mem.get("tiles", f"room@{exit_tile[0]},{exit_tile[1]}")["exit"] == exit_before and mem.rows("crossings") == []


@pytest.mark.parametrize("key, dx, dy", ROUTE_DIRS)
def test_route_blocked_travel_folds_each_move_once_and_prepare_does_not_refold(tmp_path, key, dx, dy):
    from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room")
    blocker = (8 + 2 * dx, 8 + 2 * dy)
    env = GridEnv(); env.door, env.npc = (-5, -5), blocker
    world, ctx = _route_world(mem, env); world.actor_tiles = frozenset({blocker})

    result = world.travel(ctx, [{"place": "room", "dirs": [key] * 3, "push": None}], 1)
    assert world.pos == (8 + dx, 8 + dy)
    world.prepare(result, "room", False, 2)

    assert world.pos == (8 + dx, 8 + dy)


def _walled_leg(mem, env):  # a hidden wall at (9, 8) across the two-step route to (10, 8)
    env.door, env.npc = (-5, -5), (-6, -6); env.walls.add((9, 8)); place(mem, "room", "room")
    return _route_world(mem, env), [{"place": "room", "dirs": ["RIGHT", "RIGHT"], "push": None, "target": {"kind": "screen", "tile": (10, 8)}}]


def test_every_travelled_move_is_folded_exactly_once(tmp_path, monkeypatch):
    from tests.agent.fakes import GridEnv; from agent import world as world_module

    folded, real = [], world_module.fold
    monkeypatch.setattr(world_module, "fold", lambda mem, place, pos, moves, *a, **k: (folded.append(list(moves)), real(mem, place, pos, moves, *a, **k))[1])
    (world, ctx), legs = _walled_leg(Memory(tmp_path), GridEnv())
    result = world.travel(ctx, legs, 1)

    units = [m for batch in folded for m in batch if isinstance(m, dict) and m.get("shift") not in ((0, 0), 0, None)]
    expected = [m for m in result.moves if isinstance(m, dict) and m.get("shift") not in ((0, 0), 0, None)]
    assert result.bumps >= 1 and len(result.moves) > 2 and world.pos == (10, 8)
    assert [m["dir"] for m in units] == [m["dir"] for m in expected]


def test_wall_bumped_mid_route_is_recorded_and_the_next_attempt_goes_around_it(tmp_path):
    from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); (world, ctx), legs = _walled_leg(mem, GridEnv())
    result = world.travel(ctx, legs, 1)

    assert result.status == "done" and result.bumps >= 1 and world.pos == (10, 8) and ctx.env.player == (10, 8)
    assert "RIGHT" in mem.get("tiles", "room@8,8")["blocked"]


def test_three_bumps_end_the_travel_blocked_with_bumps_three(tmp_path, monkeypatch):
    from PIL import Image; from agent.skills import SkillResult; from agent import world as world_module; from tests.agent.fakes import GridEnv

    frame, calls = Image.new("RGB", (8, 8), "red"), []
    monkeypatch.setattr(world_module, "approach", lambda *_a, **_k: calls.append(1) or SkillResult("blocked", "route blocked", 0, 0, frame, frame, (0, 0), False, [], failure={"stage": "route", "reason": "route blocked", "direction": "RIGHT"}))
    mem = Memory(tmp_path); place(mem, "room", "room"); world, ctx = _route_world(mem, GridEnv())
    result = world.travel(ctx, [{"place": "room", "dirs": ["RIGHT", "RIGHT"], "push": None, "target": {"kind": "screen", "tile": (10, 8)}}], 1)

    assert result.status == "blocked" and result.failure["bumps"] == 3 and len(calls) == 3


def test_crossing_failure_from_the_exit_tile_marks_that_tile_pushed(tmp_path, monkeypatch):
    from agent.skills import SkillResult; from agent import world as world_module; from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room")
    env = GridEnv(); original_frame = env._frame
    def no_warp_frame():
        door, env.door = env.door, (-5, -5)
        try: original_frame()
        finally: env.door = door
    env._frame = no_warp_frame
    def approach(ctx, *_a, **_k):
        ctx.env.tick(); start = ctx.odometer.seq; before = ctx.env.screenshot(); moves = []
        for _ in range(2):
            at = ctx.odometer.seq; ctx.env.press("RIGHT"); moves.append({"dir": "RIGHT", "shift": (-16, 0), "at": at})
        after = ctx.env.screenshot()
        assert ctx.odometer.pixels_since(start, ctx.odometer.seq) == (-32, 0)
        return SkillResult("blocked", "exit not crossed", 2, 0, before, after, (0, 0), False, moves, failure={"stage": "crossing", "reason": "exit not crossed", "direction": "RIGHT"})
    monkeypatch.setattr(world_module, "approach", approach)
    world, ctx = _route_world(mem, env)
    result = world.travel(ctx, [{"place": "room", "dirs": ["RIGHT", "RIGHT"], "push": "RIGHT"}], 1)
    world.failed_push(result, "room", (8, 8), "RIGHT", 2)

    assert result.failure["stage"] == "crossing" and world.pos == (10, 8)
    assert "RIGHT" in mem.get("tiles", "room@9,8")["pushed"] and "RIGHT" not in (mem.get("tiles", "room@10,8") or {}).get("pushed", [])


def test_scene_cut_mid_route_ends_the_leg_as_before(tmp_path, monkeypatch):
    from agent.skills import SkillResult; from agent import world as world_module; from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room"); place(mem, "hall", "hall")
    mem.upsert("tiles", {"id": "room@10,8", "place": "room", "x": 10, "y": 8, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"}})
    env = GridEnv(); original_frame = env._frame
    def no_warp_frame():
        door, env.door = env.door, (-5, -5)
        try: original_frame()
        finally: env.door = door
    env._frame = no_warp_frame
    def approach(ctx, *_a, **_k):
        ctx.env.tick(); start = ctx.odometer.seq; before = ctx.env.screenshot(); moves = []
        for _ in range(2):
            at = ctx.odometer.seq; ctx.env.press("RIGHT"); moves.append({"dir": "RIGHT", "shift": (-16, 0), "at": at})
        after = ctx.env.screenshot()
        assert ctx.odometer.pixels_since(start, ctx.odometer.seq) == (-32, 0)
        env._transition = 1; ctx.env.tick(3); after = ctx.env.screenshot()
        return SkillResult("done", "scene cut", 2, 0, before, after, (0, 0), False, moves)
    monkeypatch.setattr(world_module, "approach", approach)
    world, ctx = _route_world(mem, env)
    result = world.travel(ctx, [{"place": "room", "dirs": ["RIGHT", "RIGHT"], "push": None}], 1)

    assert result.status == "done" and result.reason.endswith("scene cut") and world._travel_open and world.pos == (10, 8)


@pytest.mark.parametrize("actor", [True, False])
@pytest.mark.parametrize("key, dx, dy", ROUTE_DIRS)
def test_actor_block_is_not_stored_as_a_wall_but_a_real_wall_is(tmp_path, key, dx, dy, actor):
    from tests.agent.fakes import GridEnv

    mem = Memory(tmp_path); place(mem, "room", "room")
    blocker = (8 + 2 * dx, 8 + 2 * dy)
    env = GridEnv(); env.door, env.npc = (-5, -5), (-6, -6)
    if actor: env.npc = blocker
    else: env.walls.add(blocker)
    world, ctx = _route_world(mem, env)
    if actor: world.actor_tiles = frozenset({blocker})

    world.travel(ctx, [{"place": "room", "dirs": [key] * 3, "push": None}], 1)

    row = mem.get("tiles", f"room@{8 + dx},{8 + dy}")
    assert row is not None and (key in row["blocked"]) is (not actor)


def test_beyond_suffix_counts_in_words_and_drops_zero_parts():
    from agent.world import _beyond_suffix
    assert _beyond_suffix(None) == "" and _beyond_suffix({"unused_exits": 0, "unexplored": 0}) == ""
    assert _beyond_suffix({"unused_exits": 1, "unexplored": 10}) == " (1 unexplored exit, 10 unexplored spots beyond)"
    assert _beyond_suffix({"unused_exits": 0, "unexplored": 1}) == " (1 unexplored spot beyond)"
    assert _beyond_suffix({"unused_exits": 2, "unexplored": 0}) == " (2 unexplored exits beyond)"


def test_view_exit_lines_say_status_and_the_dead_end_line_names_the_through_exit(tmp_path):
    from tests.agent.test_options import chain  # rooms A - B - C; C has an unverified north exit
    mem = Memory(tmp_path); chain(mem)
    mem.upsert("tiles", {"id": "c@1,2", "place": "c", "x": 1, "y": 2, "floor": True, "blocked": ["UP", "DOWN", "LEFT", "RIGHT"], "pushed": [], "exit": None, "step": 1})
    entity(mem, "hatch", "exit: hatch", "c", [1, 2])
    mem.upsert("entities", {**mem.get("entities", "hatch"), "kind": "exit"})
    mem.upsert("relations", {"id": "dn", "s": "hatch", "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 3, "ev": [104, 107], "state": "refuted", "verb": "approach", "step": 107})
    world = World(mem, 16); world.current_place = "b"

    b = world.view("", "b")
    assert "E at (0,0) Room A -> Room A used 1×\n" in b and "E at (1,0) Room C -> Room C used 1× (1 unexplored exit beyond)" in b
    assert "Everything unexplored from here is through: Room C" in b

    world.current_place = "c"
    c = world.view("", "c")
    assert "E at (0,-2) north: possible exit, unverified (never crossed)" in c  # no destination known: no arrow
    assert "E at (1,2) not an exit: hatch (walked into 3×, steps 104–107)" in c and "E at (0,0) Room B -> Room B used 1×" in c
    assert "Everything unexplored from here is through" not in c  # its own unverified exit is unexplored ground

def test_open_screen_edges_count_unseen_cells_past_them_until_seen(tmp_path):
    from PIL import Image, ImageDraw
    from agent.world import _open_edge, open_cells
    frame = Image.new("L", (160, 144), 0); d = ImageDraw.Draw(frame)
    for x in range(0, 160, 4): d.line([(x, 0), (x, 143)], fill=80 + x % 3 * 40)  # textured ground everywhere
    d.rectangle([0, 0, 15, 143], fill=0)  # the left strip is void
    edge = _open_edge(frame, 16)  # 10 x 9 tiles
    assert edge["LEFT"] == [] and edge["RIGHT"] == list(range(9)) and edge["UP"] == edge["DOWN"] == list(range(1, 10))
    mem = Memory(tmp_path); mem.upsert("seen", {"id": "p", "cells": ["5,0"], "open": ["5,0", "5,1"]})
    assert open_cells(mem, "p") == {"5,1"}


def test_explore_dirs_routes_to_open_cells_and_extends_half_a_view(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    mem.upsert("seen", {"id": "room", "cells": [], "open": ["2,0", "0,-3"]})
    mem.upsert("tiles", {"id": "room@1,0", "place": "room", "x": 1, "y": 0, "floor": True, "blocked": ["UP", "DOWN", "LEFT"], "pushed": [], "exit": None, "step": 1})
    world = World(mem, 16); world.current_place, world.pos, world.size = "room", (0, 0), (160, 144)

    routes = world.explore_dirs("room")

    assert routes == {"UP": {"dirs": ["UP"] * 7, "steps": 3, "unseen": 1, "to": (0, -3)},
                      "RIGHT": {"dirs": ["RIGHT"] * 7, "steps": 2, "unseen": 1, "to": (2, 0)}}
    assert "(2 unseen cells)" in world.view("", "room").splitlines()[0]
    assert "unseen by direction: UP 1, RIGHT 1" in world.view("", "room").splitlines()[0]
    assert World(mem, None).explore_dirs("room") == {}


def test_explore_dirs_routes_to_seen_boundary_and_extends(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    floor = {(0, 0), (1, 0), (2, 0)}
    walls = {(x + dx, y + dy) for x, y in floor for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0))} - floor - {(3, 0)}
    for x, y in floor | walls:
        mem.upsert("tiles", {"id": f"room@{x},{y}", "place": "room", "x": x, "y": y,
                             "floor": (x, y) in floor, "blocked": [], "pushed": [], "exit": None, "step": 1})
    world = World(mem, 16); world.current_place, world.pos, world.size = "room", (0, 0), (160, 144)

    routes = world.explore_dirs("room")

    assert routes == {"RIGHT": {"dirs": ["RIGHT"] * 8, "steps": 3, "unseen": 1, "to": (3, 0)}}


def test_explore_dirs_targets_the_unvisited_edge_of_seen_cells(tmp_path):
    from agent.world import unexplored
    mem = Memory(tmp_path); place(mem, "room", "room")
    seen = {f"{x},{y}" for x in range(-1, 3) for y in range(-4, 2)} | {f"0,{y}" for y in range(-6, -4)}
    mem.upsert("seen", {"id": "room", "cells": sorted(seen), "open": ["0,-6", "1,-4"]})
    for y in range(-3, 1):
        mem.upsert("tiles", {"id": f"room@0,{y}", "place": "room", "x": 0, "y": y,
                             "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "room@1,-3", "place": "room", "x": 1, "y": -3,
                         "floor": True, "blocked": ["UP"], "pushed": [], "exit": None, "step": 1})
    world = World(mem, 16); world.current_place, world.pos, world.size = "room", (0, 0), (160, 144)

    assert (1, -4) not in unexplored(mem, "room")
    assert world.explore_dirs("room")["UP"]["to"] == (0, -6)
    mem.upsert("seen", {"id": "room", "cells": sorted(seen), "open": []})
    assert "UP" not in world.explore_dirs("room")


def test_record_view_counts_only_new_seen_cells(tmp_path):
    from PIL import Image

    mem = Memory(tmp_path); place(mem, "room", "room")
    world = World(mem, 16); frame = Image.new("RGB", (160, 144), "black")
    per = {"screen": "world", "entities": []}

    world._record_view(per, frame, False, "room", (160, 144), 1, [.5, .5], {})
    assert world.novel == 90
    world._record_view(per, frame, False, "room", (160, 144), 2, [.5, .5], {})
    assert world.novel == 0
    world.pos = (1, 0)
    world._record_view(per, frame, False, "room", (160, 144), 3, [.5, .5], {})
    assert world.novel == 9
    world._record_view({"screen": "menu", "entities": []}, frame, False, "room", (160, 144), 4, [.5, .5], {})
    assert world.novel == 0


def test_record_view_files_visible_cell_looks_only_when_enabled(tmp_path):
    from PIL import Image
    from agent.memory import Looks

    mem = Memory(tmp_path / "memory"); place(mem, "room", "room")
    frame = Image.new("RGB", (160, 144), "black")
    world = World(mem, 16); world.mem.looks = Looks(tmp_path / "game.jsonl", "run")
    world._record_view({"screen": "world", "entities": []}, frame, False, "room", (160, 144), 1, [.5, .5], {})
    rows = mem.rows("looks")
    assert len(rows) == 89 and all(len(r["look"]) == 16 for r in rows) and mem.get("looks", "room@0,0") is None

    plain = Memory(tmp_path / "plain"); place(plain, "room", "room")
    World(plain, 16)._record_view({"screen": "world", "entities": []}, frame, False, "room", (160, 144), 1, [.5, .5], {})
    assert not plain.rows("looks") and not (tmp_path / "plain" / "looks.jsonl").exists()


def test_world_view_marks_a_learned_wall_look_with_x(tmp_path):
    from agent.memory import Looks

    mem = Memory(tmp_path / "memory"); place(mem, "room", "room")
    mem.looks = Looks(tmp_path / "game.jsonl", "run")
    mem.upsert("looks", {"id": "room@1,0", "place": "room", "x": 1, "y": 0, "look": "wall-look", "step": 1})
    mem.looks.add("wall-look", "wall", "room", (1, 0), 1)
    world = World(mem, 16); world.current_place, world.pos = "room", (0, 0)
    view = world.view("", "room")
    assert "x" in view and ", x looks like a wall" in view


SYMMETRIES = [lambda x, y: (x, y), lambda x, y: (y, x), lambda x, y: (-x, y), lambda x, y: (x, -y),
              lambda x, y: (-x, -y), lambda x, y: (-y, x), lambda x, y: (y, -x), lambda x, y: (-y, -x)]


def _rooms(mem, id, cells):  # open cells are floor; every other tile in the 5x5 box around them is wall
    for x in range(-2, 3):
        for y in range(-2, 3):
            mem.upsert("tiles", {"id": f"{id}@{x},{y}", "place": id, "x": x, "y": y, "floor": (x, y) in cells, "blocked": [], "pushed": [], "exit": None, "step": 1})


def _walk(start, dirs):
    from agent.space import DIRS
    at, visited = start, [start]
    for d in dirs: at = (at[0] + DIRS[d][0], at[1] + DIRS[d][1]); visited.append(at)
    return visited


@pytest.mark.parametrize("T", SYMMETRIES)
def test_plan_detours_around_an_actor_in_the_current_place(tmp_path, T):
    mem = Memory(tmp_path); place(mem, "room", "room")
    _rooms(mem, "room", {T(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)})
    start, goal, actor = T(-1, 0), T(1, 0), T(0, 0)
    world = World(mem, 16); world.current_place = "room"; world.pos = start; world.actor_tiles = frozenset({actor})

    legs = world.plan({"kind": "place", "place": "room", "tile": goal, "id": "room", "stale": False})

    assert legs is not None and len(legs) == 1
    visited = _walk(start, legs[0]["dirs"])
    assert visited[-1] == goal and actor not in visited


@pytest.mark.parametrize("T", SYMMETRIES)
def test_plan_has_no_route_through_a_one_cell_corridor_held_by_an_actor(tmp_path, T):
    mem = Memory(tmp_path); place(mem, "room", "room")
    _rooms(mem, "room", {T(-1, 0), T(0, 0), T(1, 0)})
    world = World(mem, 16); world.current_place = "room"; world.pos = T(-1, 0); world.actor_tiles = frozenset({T(0, 0)})

    assert world.plan({"kind": "place", "place": "room", "tile": T(1, 0), "id": "room", "stale": False}) is None


def test_plan_routes_direct_again_once_the_actor_tiles_are_cleared(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    _rooms(mem, "room", {(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)})
    world = World(mem, 16); world.current_place = "room"; world.pos = (-1, 0); world.actor_tiles = frozenset({(0, 0)})
    target = {"kind": "place", "place": "room", "tile": (1, 0), "id": "room", "stale": False}
    assert len(world.plan(target)[0]["dirs"]) == 4  # around the actor, not straight through it

    world.actor_tiles = frozenset()

    assert world.plan(target)[0]["dirs"] == ["RIGHT", "RIGHT"]


@pytest.mark.parametrize("T", SYMMETRIES)
def test_plan_memory_target_stands_on_a_free_tile_beside_it_and_faces_it(tmp_path, T):
    from agent.space import DIRS
    mem = Memory(tmp_path); place(mem, "room", "room")
    _rooms(mem, "room", {T(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)})
    tile, start, actor = T(1, 0), T(-1, 0), T(0, 0)  # the nearest stand tile is the actor's
    world = World(mem, 16); world.current_place = "room"; world.pos = start; world.actor_tiles = frozenset({actor})

    legs = world.plan({"kind": "memory", "place": "room", "tile": tile, "id": "fern", "stale": False})

    *walk, facing = legs[-1]["dirs"]; visited = _walk(start, walk)
    assert actor not in visited and abs(visited[-1][0] - tile[0]) + abs(visited[-1][1] - tile[1]) == 1
    assert (visited[-1][0] + DIRS[facing][0], visited[-1][1] + DIRS[facing][1]) == tile


def test_beside_routes_to_free_neighbor_of_solid_block(tmp_path):
    from agent.space import DIRS
    mem = Memory(tmp_path); place(mem, "room", "room")
    block = {(x, y) for x in range(1, 4) for y in range(0, 2)}
    free = {(-2, 0), (-1, 0), (0, 0), (1, -1), (2, -1), (3, -1), (1, 2), (2, 2), (3, 2), (4, 0), (4, 1)}
    _rooms(mem, "room", free)
    world = World(mem, 16); world.current_place = "room"; world.pos = (-2, 0)

    found = world.beside("room", world.pos, (1, 0))

    assert found is not None
    stand, facing, dirs = found
    assert stand in free and stand not in block
    assert (stand[0] + DIRS[facing][0], stand[1] + DIRS[facing][1]) == (1, 0)
    assert _walk(world.pos, dirs)[-1] == stand


def test_plan_ignores_actors_of_another_place_on_later_legs(tmp_path):
    from agent.space import mark_exit
    mem = Memory(tmp_path); place(mem, "room", "room"); place(mem, "other", "other")
    _rooms(mem, "room", {(-1, 0), (0, 0)}); _rooms(mem, "other", {(0, 0), (1, 0), (2, 0)})
    mark_exit(mem, "room", (0, 0), "RIGHT", "other", (0, 0), 1)
    world = World(mem, 16); world.current_place = "room"; world.pos = (-1, 0)
    world.actor_tiles = frozenset({(1, 0)})  # a room actor standing where the other-place corridor runs

    legs = world.plan({"kind": "place", "place": "other", "tile": (2, 0), "id": "other", "stale": False})

    assert [leg["place"] for leg in legs] == ["room", "other"]
    assert legs[0]["dirs"] == ["RIGHT"] and legs[1]["dirs"] == ["RIGHT", "RIGHT"]


def test_plan_route_to_an_exit_tile_avoids_an_actor(tmp_path):
    from agent.space import mark_exit
    mem = Memory(tmp_path); place(mem, "room", "room"); place(mem, "other", "other")
    _rooms(mem, "room", {(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)}); _rooms(mem, "other", {(0, 0)})
    mark_exit(mem, "room", (1, 0), "RIGHT", "other", (0, 0), 1)
    world = World(mem, 16); world.current_place = "room"; world.pos = (-1, 0); world.actor_tiles = frozenset({(0, 0)})

    legs = world.plan({"kind": "place", "place": "other", "tile": (0, 0), "id": "other", "stale": False})

    assert legs[0]["exit_tile"] == (1, 0) and (0, 0) not in _walk((-1, 0), legs[0]["dirs"])


def test_travel_leg_that_cuts_into_another_place_is_done_and_left_open_for_reidentification(tmp_path, monkeypatch):
    # m1_hf_checkpoint_1 step 127: a cut on a leg with no expected exit was reported blocked, a false failure for the planner
    from PIL import Image; from agent.skills import Ctx, SkillResult; from agent import world as world_module; from tests.agent.fakes import GridEnv, StubModel

    mem = Memory(tmp_path); place(mem, "out", "Out"); place(mem, "shop", "Shop")
    frame = Image.new("RGB", (8, 8), "red")
    monkeypatch.setattr(world_module, "approach", lambda ctx, *_a, **_k: SkillResult("done", "scene cut", 1, 0, frame, frame, (0, 0), False, [("UP", (0, 0))]))
    world = World(mem, 16); world.current_place = "out"
    result = world.travel(Ctx(GridEnv(), StubModel([]), 16), [{"place": "out", "dirs": ["UP", "RIGHT"], "push": None}], 3)
    assert result.status == "done" and result.failure is None and world._travel_open and world._travel_cut


def _remembered_scene(tmp_path, cells):
    mem = Memory(tmp_path); place(mem, "room", "room")
    _rooms(mem, "room", cells)
    entity(mem, "fern", "fern", "room", (1, 0))  # reachable: stands on (0, 0)
    entity(mem, "rock", "rock", "room", (0, 2))  # every neighbour is wall
    for x, y in ((0, 1), (0, 3), (-1, 2), (1, 2)):
        mem.upsert("tiles", {"id": f"room@{x},{y}", "place": "room", "x": x, "y": y, "floor": False, "blocked": [], "pushed": [], "exit": None, "step": 1})
    world = World(mem, 16); world.current_place, world.pos = "room", (-2, 0)
    return mem, world, {"screen": "world", "text": [], "entities": []}


def test_offered_remembered_rows_resolve_and_plan(tmp_path):
    mem, world, per = _remembered_scene(tmp_path, {(-2, 0), (-1, 0), (0, 0), (1, 0)})
    opts = world.options(per, "room", {}, 5, targets=("thing:fern",))  # an off-screen static thing with no history is offered only as a goal
    labels = [o["label"] for o in opts]

    assert not any(label.startswith("go to rock") for label in labels)
    assert any(label.startswith("go to fern") for label in labels)
    for o in opts:
        if o["action"] and o["action"].get("go_to") not in (None, "unexplored"):  # explore rows target a direction, not a ref
            assert world.reachable(o["action"]["go_to"], per, "room")


def test_remembered_actor_row_says_when_it_was_last_seen(tmp_path):
    mem, world, per = _remembered_scene(tmp_path, {(-2, 0), (-1, 0), (0, 0), (1, 0)})
    world.size, world.pa = (16, 16), (.5, .5)  # one-tile view at (-2, 0): Guide is truly off-screen
    mem.upsert("entities", {"id": "guide", "name": "Guide", "kind": "npc", "place": "room", "tile": [1, 0], "status": "remembered", "tier": "interest", "last": 2})

    row = next(o for o in world.options(per, "room", {}, 9) if o.get("ref") == "thing:guide")

    assert ", last seen 7 steps ago" in row["label"]


def test_actor_in_the_way_keeps_a_remembered_row(tmp_path):
    mem, world, per = _remembered_scene(tmp_path, {(-2, 0), (-1, 0), (0, 0), (1, 0)})
    world.actor_tiles = frozenset({(-1, 0)})  # the only corridor is held by an actor

    assert any(o["label"].startswith("go to fern") for o in world.options(per, "room", {}, 5, targets=("thing:fern",)))
    assert world.reachable("thing:fern", per, "room") and world.actor_tiles == frozenset({(-1, 0)})


def test_actor_absent_from_current_view_is_gone_but_static_thing_is_remembered(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    for ident, name, kind in (("guide", "Guide", "npc"), ("sign", "Sign", "object")):
        mem.upsert("entities", {"id": ident, "name": name, "kind": kind, "place": "room", "tile": [0, 0], "status": "remembered", "tier": "interest", "last": 1})
    world = World(mem, 16)
    per = {"screen": "world", "text": [], "entities": [{"name": "Player", "kind": "player", "at": [.5, .5]}]}
    world.before(per, "room", None, (160, 144), 5)
    rows = world.options(per, "room", {}, 5)

    assert mem.get("entities", "guide")["status"] == "gone"
    assert mem.get("entities", "sign")["status"] == "remembered"
    assert not any(o.get("ref") == "thing:guide" and o["tool"] == "go_to" for o in rows)


def test_dialogue_overlay_does_not_mark_in_view_actor_gone(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "room")
    mem.upsert("entities", {"id": "guide", "name": "Guide", "kind": "npc", "place": "room", "tile": [0, 0], "status": "remembered", "tier": "interest", "last": 1})
    world = World(mem, 16)
    per = {"screen": "world", "text": [], "entities": [{"name": "Player", "kind": "player", "at": [.5, .5]}, {"name": "Text box", "kind": "dialogue_box", "at": [.5, .9]}]}

    world.before(per, "room", None, (160, 144), 5)

    assert mem.get("entities", "guide")["status"] == "remembered"


def test_tied_exit_names_are_no_ref_but_list_candidates_and_route_through_their_door(tmp_path):
    mem = Memory(tmp_path); place(mem, "p_tiled", "tiled room"); place(mem, "p_room", "room")
    entity(mem, "e_exit_stairs", "exit: stairs", "p_tiled", (4, -4)); entity(mem, "e_exit_stairs_2", "exit: stairs", "p_room", (0, 0))
    mem.upsert("tiles", {"id": "p_tiled@3,-4", "place": "p_tiled", "x": 3, "y": -4, "floor": True, "blocked": [], "pushed": ["RIGHT"],
                         "exit": {"dir": "RIGHT", "to": "p_room", "arrive": [0, 0], "src": "observed"}, "step": 1})
    world = World(mem, 16)

    assert world.ref("exit: stairs") is None
    assert world.ties("exit: stairs") == [("thing:e_exit_stairs", "tiled room"), ("thing:e_exit_stairs_2", "room")]
    assert world.ties("door") == []
    assert world.exit_route("thing:e_exit_stairs") == ("place:p_tiled", "place:p_room")
    assert world.exit_route("thing:e_exit_stairs_2") is None


def test_unconfident_thing_label_shows_its_candidates(tmp_path):
    mem = Memory(tmp_path); place(mem, "room", "Room"); world = World(mem, 16)
    mem.upsert("entities", {"id": "t", "name": "ball", "kind": "object", "place": "room", "tile": [1, 1], "recent_names": ["ball", "sphere", "table", "ball", "sphere"]})
    assert world.label("thing:t") == "ball (or: sphere, table)"
    mem.upsert("entities", {"id": "t", "name": "ball", "kind": "object", "place": "room", "tile": [1, 1], "recent_names": ["ball", "ball", "table", "ball", "ball", "sphere"]})
    assert world.label("thing:t") == "ball"
    mem.upsert("entities", {"id": "t", "name": "ball", "kind": "object", "place": "room", "tile": [1, 1], "called": "OAK", "recent_names": ["ball", "sphere"]})
    assert world.label("thing:t") == "OAK"


@pytest.mark.parametrize("final_view", [False, True])
def test_forced_cut_folds_destination_once(tmp_path, monkeypatch, final_view):
    from agent import world as world_module
    mem = Memory(tmp_path); place(mem, "source", "Source"); place(mem, "dest", "Dest")
    world = World(mem, 16)
    source, dest = camera(), camera(2); odo = world.odometer; world._odo_screen = {"screen": "world"}
    seq, x, y = 0, 100, 100; odo(source(x, y), seq); seq, x, y = feed(odo, source, seq, x, y, 16, 160)
    from PIL import Image
    seq += 1; odo(Image.new("RGB", (160, 144), "white"), seq); seq += 1; odo(dest(200, 280), seq)
    seq, x, y = feed(odo, dest, seq, 200, 280, 0, -80)
    frame = dest(x, y)
    skill = SimpleNamespace(moves=[{"dir": "RIGHT", "shift": (-16, 0), "at": 0}], after=frame, arrival_frame=frame, scroll=(0, 0))
    spatial = world.prepare(skill, "source", True, 2)
    assert spatial == {"cut_dir": "RIGHT", "exit_key": (1, 10, "RIGHT")}
    mem.arrival = ("dest", (4, 2) if final_view else (4, 7), "view" if final_view else "observed")
    folded, real_fold = [], world_module.fold
    monkeypatch.setattr(world_module, "fold", lambda mem, place, pos, moves, *a, **k: (folded.extend(moves), real_fold(mem, place, pos, moves, *a, **k))[1])
    world.after(skill, "source", "dest", True, 2)
    assert world.pos == (4, 2) and spatial["exit_key"] == (1, 10, "RIGHT")
    assert [m["dir"] for m in folded if isinstance(m, dict) and m.get("shift") == (0, 16)] == ["UP"] * 5
    world.mark_exit("source", spatial["cut_dir"], "dest", world.pos, 2)
    assert mem.get("tiles", "source@1,10")["exit"]["arrive"] == [4, 7]
    folded_n, pos = len(folded), world.pos
    assert world.odometer_start(seq, "dest", 3, {"screen": "world"}) is False and world.pos == pos == (4, 2) and len(folded) == folded_n


def _odo_world(tmp_path, pid="room"):
    mem = Memory(tmp_path); place(mem, pid, pid); world = World(mem, 16); world.current_place = pid; world._odo_screen = {"screen": "world"}
    return mem, world


def test_pixels_since_is_bounded_non_consuming_and_take_exposes_bounds(tmp_path):
    _, world = _odo_world(tmp_path); odo = world.odometer; frame = camera(); odo(frame(100, 100), 0)
    seq, x, y = feed(odo, frame, 0, 100, 100, 16, 0)
    assert odo.pixels_since(0, seq - 2) == (-8, 0)
    assert odo.pixels_since(0, seq) == (-16, 0)
    seg = odo.take(through=seq)[0]
    assert (seg["start"], seg["end"]) == (0, seq) and sum(m[1] for m in seg["moves"]) == -16


def test_world_rounds_the_running_pixel_sum_at_tie_and_emits_unit_moves(tmp_path, monkeypatch):
    from agent import world as world_module
    _, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    emitted, fold = [], world_module.fold
    monkeypatch.setattr(world_module, "fold", lambda mem, place, pos, moves, *a, **k: (emitted.extend(moves), fold(mem, place, pos, moves, *a, **k))[1])
    seq, x, y = feed(world.odometer, frame, 0, 100, 100, 24, 0)
    for segment in world._odometer_drain(seq, "room", 1): world._odometer_fold(segment, "room", 1)
    assert world.pos == (2, 0) and [m["shift"] for m in emitted] == [(-16, 0)] * 2


def test_bump_at_seq_follows_camera_motion_from_that_frame(tmp_path):
    _, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, x, y = feed(world.odometer, frame, 0, 100, 100, 16, 0)
    bump = {"dir": "DOWN", "shift": (0, 0), "at": seq}
    segment = world._odometer_drain(seq, "room", 1, [bump])[0]
    world._odometer_fold(segment, "room", 1)
    assert world.pos == (1, 0) and bump["_tile"] == (1, 0)


def test_odometer_no_response_becomes_silent_then_unconfirmed_wall(tmp_path):
    mem, world = _odo_world(tmp_path); frame = camera(); seq = 0; world.odometer(frame(100, 100), seq)
    for step in (1, 2):
        seq += 1; world.odometer(frame(100, 100), seq)
        move = {"dir": "UP", "shift": None, "no_response": True}
        after = frame(100, 100); after.info["frame_seq"] = seq
        skill = SimpleNamespace(moves=[move], after=after, arrival_frame=after, scroll=(0, 0))
        world.prepare(skill, "room", False, step); world.after(skill, "room", "room", False, step)
        world.before({"screen": "world", "entities": []}, "room", "room", (160, 144), step + 1)
        row = mem.get("tiles", "room@0,0")
        if step == 1: assert row.get("silent") == {"UP": 1} and row["blocked"] == []
        else: assert row["blocked"] == ["UP"] and "UP" not in row.get("firm", [])


def test_odometer_no_response_is_at_the_moved_to_tile(tmp_path):
    mem, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, _, _ = feed(world.odometer, frame, 0, 100, 100, 16, 0)
    move = {"dir": "UP", "shift": None, "no_response": True, "at": seq}
    after = frame(116, 100); after.info["frame_seq"] = seq
    skill = SimpleNamespace(moves=[move], after=after, arrival_frame=after, scroll=(0, 0))
    world.prepare(skill, "room", False, 1); world.after(skill, "room", "room", False, 1)
    world.before({"screen": "world", "entities": []}, "room", "room", (160, 144), 2)
    assert world.pos == (1, 0) and move["_tile"] == (1, 0)
    assert mem.get("tiles", "room@1,0").get("silent") == {"UP": 1}


def test_subtile_remainder_crosses_drains_once(tmp_path):
    _, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, x, y = feed(world.odometer, frame, 0, 100, 100, 8, 0)
    for segment in world._odometer_drain(seq, "room", 1): world._odometer_fold(segment, "room", 1)
    assert world.pos == (0, 0) and world._odo_px == (-8, 0)
    seq, x, y = feed(world.odometer, frame, seq, x, y, 8, 0)
    for segment in world._odometer_drain(seq, "room", 2): world._odometer_fold(segment, "room", 2)
    assert world.pos == (1, 0)


def test_start_drain_folds_think_time_motion_before_world_projection(tmp_path):
    _, world = _odo_world(tmp_path); world.odometer_begin(); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, _, _ = feed(world.odometer, frame, 0, 100, 100, 16, 0)
    assert world.odometer_start(seq, "room", 3, {"screen": "world"}) is False
    assert world.pos == (1, 0) and world.odometer_trace()["px"] == [-16, 0]


def test_multi_cut_folds_first_and_last_segments_and_counts_middle(tmp_path, monkeypatch):
    mem, world = _odo_world(tmp_path); place(mem, "dest", "Dest"); source, dest = camera(), camera(3); odo = world.odometer
    seq, x, y = 0, 100, 100; odo(source(x, y), seq); seq, x, y = feed(odo, source, seq, x, y, 16, 0)
    from PIL import Image
    seq += 1; odo(Image.new("RGB", (160, 144), "white"), seq); seq += 1; odo(dest(200, 200), seq)
    seq, x, y = feed(odo, dest, seq, 200, 200, 16, 0)
    seq += 1; odo(Image.new("RGB", (160, 144), "white"), seq); seq += 1; odo(source(300, 300), seq)
    seq, x, y = feed(odo, source, seq, 300, 300, 0, -16)
    skill = SimpleNamespace(moves=[], after=SimpleNamespace(info={"frame_seq": seq}), arrival_frame=source(x, y), scroll=(0, 0))
    world.prepare(skill, "room", True, 4); monkeypatch.setattr(world, "_locate", lambda place, frame: (4, 7)); world._odo_screen = {"screen": "world"}
    world.after(skill, "room", "dest", True, 4)
    assert world.pos == (4, 6) and world.odometer_trace()["dropped"] == 1


def test_battle_style_full_band_motion_is_dropped_but_world_dialogue_folds(tmp_path):
    mem, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, x, y = feed(world.odometer, frame, 0, 100, 100, 32, 0)
    world._odo_screen = {"screen": "battle"}
    for segment in world._odometer_drain(seq, "room", 1): world._odometer_fold(segment, "room", 1)
    assert world.pos == (0, 0) and world.odometer_trace()["dropped"] == 1
    world.odometer_begin(); world._odo_screen = {"screen": "dialogue"}
    seq, x, y = feed(world.odometer, frame, seq, x, y, 16, 0)
    for segment in world._odometer_drain(seq, "room", 2): world._odometer_fold(segment, "room", 2)
    assert world.pos == (1, 0)


def test_odometer_trace_logs_each_segment_fate(tmp_path):
    _, world = _odo_world(tmp_path); world.odometer_begin(); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, x, y = feed(world.odometer, frame, 0, 100, 100, 16, 0)
    for segment in world._odometer_drain(seq, "room", 1): world._odometer_fold(segment, "room", 1)
    [seg] = world.odometer_segments()
    assert seg["fate"] == "folded" and seg["frames"] > 0 and seg["cut"] is None and seg["px"] == [-16, 0]
    world.odometer_begin(); world._odo_screen = {"screen": "battle"}
    seq, x, y = feed(world.odometer, frame, seq, x, y, 32, 0)
    for segment in world._odometer_drain(seq, "room", 2): world._odometer_fold(segment, "room", 2)
    [seg] = world.odometer_segments()
    assert seg["fate"] == "dropped" and seg["px"] == [-32, 0]


def test_gap_after_restore_does_not_move_position(tmp_path):
    _, world = _odo_world(tmp_path); world.pos = (3, 4); world.restore({"pos": [3, 4]})
    world.odometer(None, 1)
    for segment in world._odometer_drain(1, "room", 1): world._odometer_fold(segment, "room", 1)
    assert world.pos == (3, 4) and world.odometer_trace()["gap"]


def test_travel_plans_next_leg_from_the_drained_tile(tmp_path, monkeypatch):
    from agent import world as world_module
    from agent.skills import Ctx, SkillResult
    from tests.agent.fakes import GridEnv
    mem, world = _odo_world(tmp_path); env = GridEnv(); world.tracker.wrap(env); world.pa = [.5, .5]; ctx = Ctx(env, SimpleNamespace(calls=0), 16, odometer=world.odometer)
    frame = camera(); seq = 0; x = y = 100; world.odometer(frame(x, y), seq); seen = []
    def step(ctx, target, player, *, exit_target=None, route=None):
        nonlocal seq, x, y
        seen.append(world.pos); direction = route["dirs"][0]; dx, dy = (16, 0) if direction == "RIGHT" else (0, -16)
        seq, x, y = feed(world.odometer, frame, seq, x, y, dx, dy)
        image = frame(x, y); image.info["frame_seq"] = seq
        return SkillResult("done", "", 1, 0, image, image, (-dx, -dy), False, [{"dir": direction, "shift": (-dx, -dy), "at": seq - 4}])
    monkeypatch.setattr(world_module, "approach", step)
    result = world.travel(ctx, [{"place": "room", "dirs": ["RIGHT"], "push": None}, {"place": "room", "dirs": ["UP"], "push": None}], 1)
    assert result.status == "done" and seen == [(0, 0), (1, 0)] and world.pos == (1, -1)


def test_three_tile_agent_route_uses_odometer_once(tmp_path, monkeypatch):
    from agent import world as world_module
    mem = Memory(tmp_path); place(mem, "room", "room"); env = GridEnv(); env.door, env.npc = (-5, -5), (-6, -6)
    world, ctx = _route_world(mem, env); folded, real = [], world_module.fold
    monkeypatch.setattr(world_module, "fold", lambda mem, place, pos, moves, *a, **k: (folded.extend(moves), real(mem, place, pos, moves, *a, **k))[1])
    result = world.travel(ctx, [{"place": "room", "dirs": ["RIGHT"] * 3, "push": None}], 1)
    units = [move for move in folded if isinstance(move, dict) and move.get("shift") not in ((0, 0), 0, None)]
    assert result.status == "done" and result.inputs == 3 and world.pos == (11, 8)
    assert [move["dir"] for move in units] == ["RIGHT"] * 3


def test_route_cut_on_exit_tile_counts_crossing_and_lands_at_arrival(tmp_path, monkeypatch):
    from agent import world as world_module
    from agent.skills import Ctx, SkillResult
    from tests.agent.fakes import GridEnv
    mem, world = _odo_world(tmp_path, "source"); place(mem, "dest", "Dest")
    mem.upsert("tiles", {"id": "source@1,0", "place": "source", "x": 1, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "RIGHT", "to": "dest", "arrive": [4, 7], "src": "observed"}, "step": 1})
    env = GridEnv(); world.tracker.wrap(env); world.pa = [.5, .5]; ctx = Ctx(env, SimpleNamespace(calls=0), 16, odometer=world.odometer)
    source, dest = camera(), camera(4); seq = 0; x = y = 100; world.odometer(source(x, y), seq)
    def cut_step(ctx, target, player, *, exit_target=None, route=None):
        nonlocal seq, x, y
        seq, x, y = feed(world.odometer, source, seq, x, y, 16, 0)
        from PIL import Image
        seq += 1; world.odometer(Image.new("RGB", (160, 144), "white"), seq); seq += 1; world.odometer(dest(200, 200), seq)
        image = dest(200, 200); image.info["frame_seq"] = seq
        return SkillResult("done", "scene cut", 1, 0, image, image, (-16, 0), True, [{"dir": "RIGHT", "shift": (-16, 0), "at": 0}], arrival_frame=image)
    monkeypatch.setattr(world_module, "approach", cut_step)
    result = world.travel(ctx, [{"place": "source", "dirs": ["RIGHT"], "push": None, "expect": "dest", "exit_tile": (1, 0)}], 2)
    assert result.status == "done" and world.pos == (4, 7), (result.status, result.reason, world.pos, world._travel_push, world._travel_segments)
    assert mem.get("crossings", "source@1,0>dest")["n"] == 1


def test_r17_walk_keeps_motion_after_600_frame_settle_cap(tmp_path):
    from PIL import Image
    from agent.loop import Loop
    from agent.observer import wait_settled
    from tests.agent.fakes import StubModel

    class Walk(GridEnv):
        def __init__(self, source, dest): super().__init__(); self.source, self.dest, self.mode, self.x, self.i = source, dest, "still", 100, 0
        def screenshot(self):
            if self.mode == "post" and self.i == 1: return Image.new("RGB", (160, 144), "white")
            image = self.source if self.mode in ("still", "pre") else self.dest
            return image.crop((self.x, 0, self.x + 160, 144))
        def _frame(self):
            if self.mode == "pre": self.x += 1
            elif self.mode == "post":
                if self.i == 0: self.i = 1
                elif self.i == 1: self.i, self.x = 2, 100
                else: self.x += 1
            elif self.mode == "late": self.x += 1

    mem = Memory(tmp_path); place(mem, "source", "Source"); place(mem, "dest", "Dest")
    rng = __import__("numpy").random.default_rng(44)
    source = Image.fromarray(rng.integers(0, 256, (144, 900, 3), dtype="uint8")); dest = Image.fromarray(rng.integers(0, 256, (144, 900, 3), dtype="uint8"))
    env, model = Walk(source, dest), StubModel([]); loop = Loop(env, model, mem, "grid"); world = loop.world
    world.current_place, world.pos, world._odo_screen = "source", (0, 0), {"screen": "world"}; world._locate = lambda *_: (0, 0)
    odo = world.odometer; odo.tile_px = world.tile_px = 16; odo(env.screenshot(), 0)
    env.mode = "pre"; odo.held = "A"; env.tick(16); odo.held = None; env.mode = "still"
    before = env.screenshot(); before.info["frame_seq"] = env._frame_seq
    skill = SimpleNamespace(moves=[{"dir": "RIGHT", "shift": (-16, 0), "at": 0}], after=SimpleNamespace(info={"frame_seq": env._frame_seq}), before=before, arrival_frame=None, scroll=(0, 0), blank_seen=True)
    world.prepare(skill, "source", True, 1); env.mode, env.i = "post", 0
    settled = wait_settled(env, max_frames=600, quiet=2)
    assert settled.waited == 600 and not settled.settled
    world._odo_screen = {"screen": "world"}; world._odo_through = odo.seq; world.after(skill, "source", "dest", True, 1)
    assert world.pos == (37, 0)
    env.mode = "late"; env.tick(26); world.odometer_begin(); world.odometer_start(odo.seq, "dest", 2, {"screen": "world"})
    assert world.pos == (39, 0) and model.calls == loop.model.calls == 0


def test_after_holds_post_cut_motion_for_reidentified_place(tmp_path, monkeypatch):
    import agent.world as world_module
    from PIL import Image
    mem, world = _odo_world(tmp_path); source, dest = camera(31), camera(32); odo = world.odometer
    x, y, seq = 300, 300, 0; odo(source(x, y), seq)
    seq, x, y = feed(odo, source, seq, x, y, 16, 0)
    seq, x, y = feed(odo, source, seq, x, y, 0, 176)
    seq, x, y = feed(odo, source, seq, x, y, 0, -16)
    seq += 1; odo(Image.new("RGB", (160, 144), "white"), seq)
    seq += 1; odo(dest(300, 300), seq)
    seq, dx, dy = feed(odo, dest, seq, 300, 300, 0, -128)
    after = dest(dx, dy); after.info["frame_seq"] = seq
    folded, real_fold = [], world_module.fold
    def record(mem, place, pos, moves, *args, **kwargs):
        if place == "room": folded.append([m["dir"] for m in moves])
        return real_fold(mem, place, pos, moves, *args, **kwargs)
    monkeypatch.setattr(world_module, "fold", record)
    skill = SimpleNamespace(moves=[], after=after, arrival_frame=after, scroll=(0, 0))

    world.prepare(skill, "room", True, 2, through=seq)
    assert world.pos == (1, 10) and folded == [["RIGHT", *(["DOWN"] * 11), "UP"]]
    folded_before_after = len(folded); world._odo_screen = {"screen": "dialogue"}
    world.after(skill, "room", "room", True, 2)

    assert world.pos == (1, 10) and len(folded) == folded_before_after
    assert world._held["src"] == "room" and world._held["tile"] == (1, 10) and world._held["dir"] == "UP"
    assert sum(move[2] for seg in world._held["segments"] for move in seg["moves"]) == 128


def test_new_place_resolves_held_motion_and_marks_both_exit_sides(tmp_path):
    mem, world = _odo_world(tmp_path); place(mem, "dest", "Destination")
    world.pos = (1, 10); world._locate = lambda *_: (0, 0)
    world._held = {"src": "room", "tile": (1, 10), "dir": "UP", "segments": [
        {"start": 20, "end": 20, "moves": [(20, 0, 128, False)], "frames": 1, "uncertain": 0, "cut": None, "gap": False, "_screen": {"screen": "dialogue"}, "_place": "room"},
    ]}

    world.before({"screen": "world", "entities": []}, "dest", "room", (160, 144), 3)

    assert world.pos == (0, 0) and world._held is None
    assert mem.get("tiles", "room@1,10")["exit"] == {"dir": "UP", "to": "dest", "arrive": [0, 8], "src": "observed"}
    assert mem.get("tiles", "dest@0,8")["exit"] == {"dir": "DOWN", "to": "room", "arrive": [1, 10], "src": "inferred"}
    assert mem.get("crossings", "room@1,10>dest")["n"] == 1


def test_same_place_resolution_flushes_held_motion_into_source(tmp_path):
    mem, world = _odo_world(tmp_path); world.pos = (0, 8)
    segment = {"start": 1, "end": 1, "moves": [(1, 0, 16, False)], "frames": 1, "uncertain": 0, "cut": None, "gap": False, "_screen": {"screen": "dialogue"}, "_place": "room"}
    world._held = {"src": "room", "tile": (0, 8), "dir": "UP", "segments": [segment]}
    expected_root = tmp_path / "expected"; expected_root.mkdir(); expected_mem, expected = _odo_world(expected_root); expected.pos = (0, 8)
    expected._odometer_fold(dict(segment), "room", 3)

    world.before({"screen": "world", "entities": []}, "room", "room", (160, 144), 3)

    assert world.pos == expected.pos == (0, 7) and world._held is None
    assert mem.rows("tiles") == expected_mem.rows("tiles")


def test_odometer_drain_appends_held_segments_without_folding(tmp_path):
    mem, world = _odo_world(tmp_path); world.pos = (0, 8)
    world._held = {"src": "room", "tile": (0, 8), "dir": "UP", "segments": []}
    frame = camera(42); odo = world.odometer; odo(frame(100, 100), 0); seq, _, _ = feed(odo, frame, 0, 100, 100, 0, 16)
    world._odo_screen = {"screen": "dialogue"}; world.odometer_start(seq, "room", 2, {"screen": "dialogue"}, "room")

    assert len(world._held["segments"]) == 1 and world.pos == (0, 8)
    assert not mem.rows("tiles") and world._odo_stats["dropped"] == 0


def test_held_checkpoint_round_trips_and_skips_unseen_cross(tmp_path):
    from PIL import Image
    mem, world = _odo_world(tmp_path); place(mem, "dest", "Destination"); world._locate = lambda *_: (0, 0)
    world.pos = (1, 10)
    world._held = {"src": "room", "tile": (1, 10), "dir": "UP", "segments": [
        {"start": 20, "end": 20, "moves": [(20, 0, 128, False)], "frames": 1, "uncertain": 0, "cut": None, "gap": False, "arrival": Image.new("RGB", (2, 2)), "raw_image": Image.new("RGB", (2, 2)), "pixels": {"raw": __import__("numpy").zeros((2, 2))}, "_screen": {"screen": "dialogue"}, "_place": "room"},
    ]}
    world._prepared = (None, "room", 2, "UP", (1, 10), (0, 8)); world.mark_exit("room", "UP", "dest", (0, 8), 2)
    state = json.loads(json.dumps(world.state()))
    segment = state["held"]["segments"][0]
    assert "arrival" not in segment and "frame" not in segment and "raw_image" not in segment and segment["pixels"] == {}
    replay = World(mem, 16); replay._locate = lambda *_: (0, 0); replay.restore(state)

    replay.unseen_cross("room", "dest", 3)
    assert not mem.rows("crossings")
    replay.before({"screen": "world", "entities": []}, "dest", "room", (160, 144), 4)
    assert replay.pos == (0, 0) and mem.get("crossings", "room@1,10>dest")["n"] == 1


def _hold_silent(world, place_id, tile, step):
    world._silent.append({"place": place_id, "tile": tile, "dir": "DOWN", "step": step, "actors": []})


def test_held_no_response_dialogue_drops_evidence(tmp_path):
    mem, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    for step in (1, 2):
        seq, _, _ = feed(world.odometer, frame, step - 1, 100, 100, 0, 0)
        move = {"dir": "DOWN", "shift": None, "no_response": True, "at": seq}
        after = frame(100, 100); after.info["frame_seq"] = seq
        skill = SimpleNamespace(moves=[move], after=after, arrival_frame=after, scroll=(0, 0))
        world.prepare(skill, "room", False, step); world.after(skill, "room", "room", False, step)
        assert len(world._silent) == 1
        world.before({"screen": "dialogue", "entities": []}, "room", "room", (160, 144), step + 1)
        row = mem.get("tiles", "room@0,0")
        assert world._silent == [] and (row is None or not row.get("silent") and not row["blocked"] and not row.get("walled"))


def test_held_no_response_world_resolves_silent_then_blocked(tmp_path):
    mem, world = _odo_world(tmp_path); _hold_silent(world, "room", (0, 0), 1)
    world.before({"screen": "world", "entities": []}, "room", "room", (160, 144), 2)
    assert mem.get("tiles", "room@0,0")["silent"] == {"DOWN": 1}
    _hold_silent(world, "room", (0, 0), 2)
    world.before({"screen": "world", "entities": []}, "room", "room", (160, 144), 3)
    assert mem.get("tiles", "room@0,0")["blocked"] == ["DOWN"]


def test_silent_checkpoint_restores_and_resolves(tmp_path):
    mem, world = _odo_world(tmp_path); _hold_silent(world, "room", (0, 0), 1)
    import json
    state = json.loads(json.dumps(world.state()))
    restored = World(mem, 16); restored.restore(state)
    other_mem, uninterrupted = _odo_world(tmp_path / "uninterrupted")
    uninterrupted.restore(state)
    per = {"screen": "world", "entities": []}
    restored.before(per, "room", "room", (160, 144), 2)
    uninterrupted.before(per, "room", "room", (160, 144), 2)
    expected = mem.get("tiles", "room@0,0")
    assert other_mem.get("tiles", "room@0,0") == expected and expected["silent"] == {"DOWN": 1}
    old_state = dict(state); old_state.pop("silent")
    restored.restore(old_state); assert restored._silent == []


def test_silent_checkpoint_drops_evidence_on_dialogue(tmp_path):
    mem, world = _odo_world(tmp_path); _hold_silent(world, "room", (0, 0), 1)
    restored = World(mem, 16); restored.restore(world.state())
    restored.before({"screen": "dialogue", "entities": []}, "room", "room", (160, 144), 2)
    assert mem.get("tiles", "room@0,0") is None and restored._silent == []


def test_silent_cut_resolves_at_original_place(tmp_path):
    mem, world = _odo_world(tmp_path); place(mem, "next", "next")
    _hold_silent(world, "room", (0, 0), 1)
    world.before({"screen": "world", "entities": []}, "next", "room", (160, 144), 2)
    assert mem.get("tiles", "room@0,0")["silent"] == {"DOWN": 1}
    assert mem.get("tiles", "next@0,0") is None


def test_travel_no_response_followed_by_dialogue_drops_evidence(tmp_path, monkeypatch):
    import agent.world as world_module
    from agent.skills import SkillResult
    mem, world = _odo_world(tmp_path); frame = camera(); world.odometer(frame(100, 100), 0)
    seq, _, _ = feed(world.odometer, frame, 0, 100, 100, 0, 0)
    after = frame(100, 100); after.info["frame_seq"] = seq
    move = {"dir": "DOWN", "shift": None, "no_response": True, "at": seq}
    result = SkillResult("blocked", "no response", 1, 0, after, after, (0, 0), False, [move], arrival_frame=after)
    monkeypatch.setattr(world_module, "approach", lambda *args, **kwargs: result)
    from tests.agent.fakes import GridEnv, StubModel
    from agent.skills import Ctx
    env = GridEnv(); ctx = Ctx(env, StubModel([]), 16, self_spot=(.5, .5), odometer=world.odometer)
    travel = world.travel(ctx, [{"place": "room", "dirs": ["DOWN"], "push": None}], 1)
    assert travel.status == "blocked" and travel.reason == "travel: leg 1 no response" and len(world._silent) == 1
    world.before({"screen": "dialogue", "entities": []}, "room", "room", (160, 144), 2)
    assert not world._silent and not any(row.get("silent") or row.get("blocked") or row.get("walled") for row in mem.rows("tiles"))
