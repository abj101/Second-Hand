import pytest

from agent.memory import Memory
from agent.space import mark_exit
from tests.agent.test_options import INPUTS, at, room, setup, tile

OBSERVED = (2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
INFERRED = (2, 1, {"dir": "DOWN", "to": "hall", "arrive": [1, 0], "src": "inferred"})  # a guessed way back, one tile from the observed door
HALL = {"kind": "place", "place": "hall", "tile": (0, 0), "id": "hall", "stale": False}


@pytest.mark.parametrize("misses", [1, 2])
def test_crossed_exit_is_demoted_after_two_misses_and_crossing_resets_it(tmp_path, monkeypatch, misses):
    from agent import world as module
    from agent.skills import Ctx, SkillResult
    from tests.agent.fakes import GridEnv, StubModel
    mem = Memory(tmp_path); rooms_with(mem, [OBSERVED]); world, per = setup(mem, [])
    env = GridEnv(); frame = env.screenshot(); ctx = Ctx(env, StubModel([]), 16)
    monkeypatch.setattr(module, "approach", lambda *_a, **_k: SkillResult("blocked", "exit not crossed", 1, 0, frame, frame, (-16, 0), False, [{"dir": "RIGHT", "shift": (-16, 0)}], failure={"stage": "crossing", "reason": "exit not crossed"}))
    leg = {"place": "room", "dirs": [], "push": "RIGHT", "expect": "hall", "exit_tile": (2, 0)}
    for step in range(misses):
        world.pos = (2, 0); world.travel(ctx, [leg], step + 2)
    assert mem.get("crossings", "room@2,0>hall")["miss"] == misses and mem.get("tiles", "room@2,0")["pushed"] == ["RIGHT"]
    assert world._exits("room", {"room", "hall"})[0]["status"] == ("refuted" if misses == 2 else "verified")
    assert ("hall" in world._paths("room")) == (misses < 2)
    assert any(o["label"].startswith("go through") for o in world.options(per, "room", {}, 5, inputs=INPUTS))
    world._cross("room", (2, 0), "hall", (0, 0), 6)
    assert mem.get("crossings", "room@2,0>hall")["miss"] == 0 and world._exits("room", {"room", "hall"})[0]["status"] == "verified"


def test_exit_record_carries_perceived_wall(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: doorway", "kind": "exit", "at": at(2, 0), "placement": "bottom wall"}])
    assert world._exits("room", {"room"})[0]["placement"] == "bottom wall"


def rooms_with(mem, doors):  # only the room and hall places, then the room's door tiles in the given order (insertion order is row order)
    mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    mem.upsert("places", {"id": "hall", "name": "Hall", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    for x, y, exit in doors: tile(mem, x, y, exit)


@pytest.mark.parametrize("doors", [(OBSERVED, INFERRED), (INFERRED, OBSERVED)], ids=["observed-first", "inferred-first"])
def test_inferred_duplicate_of_an_observed_door_is_one_exit_and_display_and_travel_pick_the_observed_one(tmp_path, doors):
    mem = Memory(tmp_path); rooms_with(mem, doors)
    world, per = setup(mem, []); world.pos = (0, 0)
    rec = world._exits("room", {"room", "hall"})
    assert len(rec) == 1 and (rec[0]["tile"], rec[0]["dir"]) == ((2, 0), "RIGHT")
    assert (rec[0]["arrive"], rec[0]["src"]) == ([0, 0], "observed")  # fails before: the record has no arrive or src
    rows = [o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("go through")]
    assert len(rows) == 1 and "crossed 1× → Hall" in " ".join(rows[0]["notes"])
    legs = world.plan(HALL)
    assert [(leg["exit_tile"], leg["push"], leg["arrive"], leg["expect"]) for leg in legs[:1]] == [((2, 0), "RIGHT", (0, 0), "hall")]


def test_two_doors_to_one_destination_far_apart_stay_two_exits_and_plan_uses_one_of_them(tmp_path):
    mem = Memory(tmp_path); rooms_with(mem, [(2, 0, OBSERVED[2]), (-2, -2, {"dir": "UP", "to": "hall", "arrive": [0, 1], "src": "observed"})])
    world, per = setup(mem, []); world.pos = (0, 0)
    labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("go through")]
    assert len(labels) == 2 and labels[0].startswith("go through Hall,") and labels[1].startswith("go through Hall,")
    legs = world.plan(HALL)
    assert (legs[0]["exit_tile"], legs[0]["push"], legs[0]["arrive"]) in {((2, 0), "RIGHT", (0, 0)), ((-2, -2), "UP", (0, 1))}


@pytest.mark.parametrize("doors", [(OBSERVED, INFERRED), (INFERRED, OBSERVED)], ids=["observed-first", "inferred-first"])
def test_unreachable_preferred_door_falls_back_to_another_door_of_the_same_exit(tmp_path, doors):
    mem = Memory(tmp_path); rooms_with(mem, doors)
    world, per = setup(mem, []); world.pos = (0, 0); world.actor_tiles = frozenset({(2, 0)})  # an actor holds the observed door: no route to it
    legs = world.plan(HALL)
    assert legs is not None and [(leg["exit_tile"], leg["push"], leg["arrive"]) for leg in legs[:1]] == [((2, 1), "DOWN", (1, 0))]


def test_unverified_thing_next_to_a_verified_exit_is_that_exit_once(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, [{"name": "exit: door", "kind": "exit", "at": at(2, 0)}, {"name": "exit: stairs", "kind": "exit", "at": at(2, 1)}])
    labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS)]
    assert sum(l.startswith("go through") for l in labels) == 1
    assert not any("stairs" in l for l in labels)
    view = world.view("the stairs and the door", "room")
    assert "E at (2,0) door -> Hall used 1×" in view
    assert sum(l.startswith("Thing ") and " door " in l for l in view.splitlines()) == 1 and not any("stairs" in l for l in view.splitlines())


def test_unverified_exit_three_tiles_away_stays_a_separate_possible_exit(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, [{"name": "exit: stairs", "kind": "exit", "at": at(-1, 0)}])
    labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS)]
    assert any(l.startswith("go through Hall,") for l in labels) and any(l.startswith("walk onto stairs,") for l in labels)


def test_unverified_exit_walk_onto_is_failed_only_once_a_push_on_its_tile_failed(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: doorway", "kind": "exit", "at": at(2, 0)}])
    walk = lambda: next(o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("walk onto doorway"))
    assert walk()["state"] == "new"
    mem.upsert("tiles", {**mem.get("tiles", "room@2,0"), "blocked": ["UP"]})
    assert walk()["state"] == "failed"


def test_two_verified_exits_with_different_destinations_next_to_each_other_stay_two(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    mark_exit(mem, "room", (2, 1), "DOWN", "cave", (0, 0), 1)
    world, per = setup(mem, [])
    labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("go through")]
    assert len(labels) == 2 and labels[0].startswith("go through Hall,") and labels[1].startswith("go through Cave,")


def test_same_name_exits_keep_distinct_location_labels(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, -2, 0, {"dir": "LEFT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "cave", "arrive": [0, 0], "src": "observed"})
    world, per = setup(mem, [{"name": "exit: door", "kind": "exit", "at": at(-2, 0)}, {"name": "exit: door", "kind": "exit", "at": at(2, 0)}])
    rows = [o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("go through")]
    assert len(rows) == 2 and rows[0]["label"] != rows[1]["label"]
    assert all(row["ref"].startswith("exit:") for row in rows)


def test_crossing_counts_only_the_departure_tile_when_arrival_is_unknown(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, []); world.pos = (2, 0)
    world._cross("room", (2, 0), "hall", None, 3)
    assert mem.rows("crossings") == [{"id": "room@2,0>hall", "n": 1, "last": 3}]


def test_crossing_with_only_an_arrival_tile_does_not_count(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [])
    world._cross("hall", None, "room", (2, 0), 2)
    assert mem.rows("crossings") == []


def test_unverified_exit_with_another_known_destination_next_to_a_verified_one_stays_separate(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    tile(mem, 2, -1, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "inferred"})  # known to lead to Cave, never crossed
    world, per = setup(mem, [])
    labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if (o["ref"] or "").startswith("exit:")]
    assert labels[0].startswith("go to Cave,") and labels[1].startswith("go through Hall,")  # unverified known destination stays a guess


def test_unseen_cross_counts_nothing_when_no_exit_tile_is_within_one(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, []); world.pos = (0, 0)
    world.unseen_cross("room", "hall", 2)
    assert mem.rows("crossings") == []


def test_unseen_cross_counts_nothing_when_two_exits_to_the_same_place_are_within_one(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    tile(mem, 2, 1, {"dir": "DOWN", "to": "hall", "arrive": [0, 1], "src": "observed"})
    world, per = setup(mem, []); world.pos = (1, 0)
    world.unseen_cross("room", "hall", 2)
    assert mem.rows("crossings") == []


def test_crossing_writes_one_row_and_arrival_exit_counts_partner_crossing(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, []); world._cross("room", (2, 0), "hall", (0, 0), 2)
    assert [r["id"] for r in mem.rows("crossings")] == ["room@2,0>hall"]
    arrival = world._exits("hall", {"room", "hall"})
    assert len(arrival) == 1 and arrival[0]["dest"] == "room" and arrival[0]["uses"] == 1 and arrival[0]["status"] == "verified"


def test_unnamed_loose_exit_without_destination_keeps_its_direction_head(tmp_path):
    mem = Memory(tmp_path); room(mem); world, _ = setup(mem, [])
    exit = world._exits("room", {"room"}, [(0, {"name": "", "kind": "exit"}, None)])[0]
    assert exit["dest"] is None and exit["head"] == exit["dir"] == ""


def test_wall_neighbour_bump_refutes_exit_tile(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("tiles", {**mem.get("tiles", "room@1,0"), "blocked": ["RIGHT"]})
    world, per = setup(mem, [])
    assert world._refuted("room", (2, 0)) == 1


def test_blocked_outward_push_refutes_floor_exit_tile(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("tiles", {"id": "room@4,0", "place": "room", "x": 4, "y": 0, "floor": True, "blocked": ["UP", "DOWN", "LEFT", "RIGHT"], "pushed": [], "exit": None, "step": 1})
    world, per = setup(mem, [])
    assert world._refuted("room", (4, 0)) == 1


def test_departure_crossing_prevents_refutation(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("tiles", {**mem.get("tiles", "room@1,0"), "blocked": ["RIGHT"]})
    world, per = setup(mem, [])
    world._cross("room", (2, 0), "hall", (0, 0), 2)
    assert world._refuted("room", (2, 0)) == 0


def test_floor_exit_is_refuted_only_after_all_pushes_are_tried(tmp_path):
    mem = Memory(tmp_path); room(mem); world, per = setup(mem, [])
    row = {"id": "room@4,0", "place": "room", "x": 4, "y": 0, "floor": True, "blocked": ["RIGHT"], "pushed": [], "exit": None, "step": 1}
    mem.upsert("tiles", row)
    assert world._refuted("room", (4, 0)) == 0
    mem.upsert("tiles", {**row, "pushed": ["UP", "DOWN", "LEFT"]})
    assert world._refuted("room", (4, 0)) == 1


def test_exit_stopped_short_twice_is_refuted_until_reached_and_view_gives_reason(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("entities", {"id": "stairs", "place": "room", "name": "exit: stairs", "kind": "exit", "tile": [2, 0]})
    world, _ = setup(mem, [])
    mem.mark_unreached("room", (2, 0), (1, 0), 2); mem.mark_unreached("room", (2, 0), (1, 0), 3)
    exit = next(e for e in world._exits("room", {"room"}) if e["ident"] == "stairs")
    assert exit["status"] == "refuted" and exit["reason"] == "not reached 2×"
    assert "not an exit: stairs (not reached 2×)" in world.view("", "room")
    mem.mark_reached("room", (2, 0), 4)
    exit = next(e for e in world._exits("room", {"room"}) if e["ident"] == "stairs")
    assert exit["status"] == "unverified"


def test_bottom_exit_requires_known_floor_beyond_it(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("entities", {"id": "south", "place": "room", "name": "exit: south", "kind": "exit", "tile": [0, 1], "placement": "bottom wall"})
    world, _ = setup(mem, [])
    exit = next(e for e in world._exits("room", {"room"}) if e["ident"] == "south")
    assert exit["status"] == "refuted" and exit["reason"] == "ground continues beyond it"
    assert "not an exit: south (ground continues beyond it)" in world.view("", "room")
    mem.upsert("entities", {**mem.get("entities", "south"), "tile": [0, 2]})
    exit = next(e for e in world._exits("room", {"room"}) if e["ident"] == "south")
    assert exit["status"] == "unverified"


def test_successful_crossing_wins_over_short_attempts_and_floor_beyond(tmp_path):
    mem = Memory(tmp_path); room(mem); tile(mem, 0, 1, {"dir": "DOWN", "to": "hall", "arrive": [0, 0], "src": "observed"})
    world, _ = setup(mem, [])
    world._cross("room", (0, 1), "hall", (0, 0), 4)
    mem.mark_unreached("room", (0, 1), (0, 0), 5); mem.mark_unreached("room", (0, 1), (0, 0), 6)
    exit = world._exits("room", {"room", "hall"})[0]
    assert exit["status"] == exit["option_status"] == "verified" and not exit["reason"]
