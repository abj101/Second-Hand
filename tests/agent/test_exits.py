import pytest

from agent.memory import Memory
from agent.space import mark_exit
from tests.agent.test_options import INPUTS, at, room, setup, tile

OBSERVED = (2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
INFERRED = (2, 1, {"dir": "DOWN", "to": "hall", "arrive": [1, 0], "src": "inferred"})  # a guessed way back, one tile from the observed door
HALL = {"kind": "place", "place": "hall", "tile": (0, 0), "id": "hall", "stale": False}


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
    assert len(labels) == 2 and labels[0].startswith("go through UP,") and labels[1].startswith("go through RIGHT,")
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
    assert any(l.startswith("go through RIGHT,") for l in labels) and any(l.startswith("go to stairs,") for l in labels)


def test_two_verified_exits_with_different_destinations_next_to_each_other_stay_two(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    mark_exit(mem, "room", (2, 1), "DOWN", "cave", (0, 0), 1)
    world, per = setup(mem, [])
    labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("go through")]
    assert len(labels) == 2 and labels[0].startswith("go through RIGHT,") and labels[1].startswith("go through DOWN,")


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
    assert labels[0].startswith("go to UP,") and labels[1].startswith("go through RIGHT,")  # unverified known destination stays a guess


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


def test_crossing_writes_one_row_and_arrival_exit_stays_unverified(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, []); world._cross("room", (2, 0), "hall", (0, 0), 2)
    assert [r["id"] for r in mem.rows("crossings")] == ["room@2,0>hall"]
    arrival = world._exits("hall", {"room", "hall"})
    assert len(arrival) == 1 and arrival[0]["dest"] == "room" and arrival[0]["status"] == "unverified"


def test_wall_neighbour_bump_refutes_exit_tile(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("tiles", {**mem.get("tiles", "room@1,0"), "blocked": ["RIGHT"]})
    world, per = setup(mem, [])
    assert world._refuted("room", (2, 0)) == 1


def test_blocked_outward_push_refutes_floor_exit_tile(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("tiles", {"id": "room@4,0", "place": "room", "x": 4, "y": 0, "floor": True, "blocked": ["RIGHT"], "pushed": [], "exit": None, "step": 1})
    world, per = setup(mem, [])
    assert world._refuted("room", (4, 0)) == 1


def test_departure_crossing_prevents_refutation(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("tiles", {**mem.get("tiles", "room@1,0"), "blocked": ["RIGHT"]})
    world, per = setup(mem, [])
    world._cross("room", (2, 0), "hall", (0, 0), 2)
    assert world._refuted("room", (2, 0)) == 0
