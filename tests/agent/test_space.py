from agent.memory import Memory
from agent.space import exit_to, fold, frontier, mark_exit, outward, route, tile_of
from agent.world import World


def tile(mem, place, pos, **values):
    mem.upsert("tiles", {"id": f"{place}@{pos[0]},{pos[1]}", "place": place, "x": pos[0], "y": pos[1], "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1, **values})


def test_tile_of(tmp_path):
    mem = Memory(tmp_path)
    assert tile_of([.59, .5], [.51, .5], (2, 3), (160, 144), 16) == (2, 3)


def test_fold_walk_blocked_and_none(tmp_path):
    mem = Memory(tmp_path)
    assert fold(mem, "hall", (0, 0), [("RIGHT", (-48, 0)), ("RIGHT", (0, 0)), None], 16, 4) == (3, 0)
    assert [mem.get("tiles", f"hall@{x},0")["floor"] for x in range(4)] == [True] * 4
    assert mem.get("tiles", "hall@3,0")["blocked"] == ["RIGHT"]
    blank = Memory(tmp_path / "blank")
    fold(blank, "hall", (0, 0), [None], 16, 1)
    assert blank.rows("tiles") == []
    fold(blank, "hall", (0, 0), [("RIGHT", (0, 0))], 16, 2)
    lines = (tmp_path / "blank" / "tiles.jsonl").read_text().splitlines()
    fold(blank, "hall", (0, 0), [("RIGHT", (0, 0))], 16, 3)
    assert len((tmp_path / "blank" / "tiles.jsonl").read_text().splitlines()) == len(lines)


def test_actor_zero_motion_does_not_record_a_wall(tmp_path):
    from types import SimpleNamespace

    mem = Memory(tmp_path)
    world = World(mem, 16)
    per = {"screen": "world", "entities": [
        {"kind": "player", "at": [.45, .5]}, {"kind": "NPC: shopkeeper", "at": [.55, .5]},
    ]}
    world.before(per, "hall", None, (160, 144), 1)
    world.prepare(SimpleNamespace(moves=[("RIGHT", (0, 0))]), "hall", False, 1)
    assert world.actor_tiles == frozenset({(1, 0)})
    assert mem.rows("tiles") == []

    blank = Memory(tmp_path / "blank")
    fold(blank, "hall", (0, 0), [("RIGHT", (0, 0))], 16, 1)
    assert blank.get("tiles", "hall@0,0")["blocked"] == ["RIGHT"]


def test_route_detours_around_wall_and_allows_unknown_goal(tmp_path):
    mem = Memory(tmp_path)
    tile(mem, "hall", (0, 0), blocked=["RIGHT"])
    path = route(mem, "hall", (0, 0), (2, 0))
    assert path is not None and path[0] != "RIGHT"
    tile(mem, "hall", (2, 0), floor=False)
    assert route(mem, "hall", (0, 0), (2, 0)) is not None
    tile(mem, "hall", (1, 0), floor=False)
    assert route(mem, "hall", (0, 0), (2, 0))[0] != "RIGHT"
    mem.get("tiles", "hall@0,0")["blocked"] = ["RIGHT", "UP", "DOWN", "LEFT"]
    assert route(mem, "hall", (0, 0), (2, 0)) is None


def test_outward_and_frontier_skip_pushed_and_blocked(tmp_path):
    mem = Memory(tmp_path)
    tile(mem, "hall", (0, 0), blocked=["UP"], pushed=["RIGHT"])
    tile(mem, "hall", (1, 0), floor=False)
    for wall, direction in (("top wall", "UP"), ("bottom wall", "DOWN"), ("left wall", "LEFT"), ("right wall", "RIGHT")):
        assert outward(mem, "hall", (0, 0), wall) == direction
    center = Memory(tmp_path / "center")
    tile(center, "hall", (0, 0)); tile(center, "hall", (2, 0))
    assert outward(center, "hall", (0, 0)) == "LEFT"
    assert frontier(mem, "hall") == [((0, 0), "DOWN"), ((0, 0), "LEFT")]


def test_mark_exit_records_observed_and_inferred_reverse(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "west", (1, 2), "RIGHT", "east", (0, 2), 9)
    mem.upsert("places", {"id": "east", "name": "East Hall"})
    assert exit_to(mem, "west", (1, 2), "RIGHT") == {"dir": "RIGHT", "to": "east", "arrive": [0, 2], "src": "observed"}
    assert exit_to(mem, "east", (0, 2), "LEFT") == {"dir": "LEFT", "to": "west", "arrive": [1, 2], "src": "inferred"}
    assert exit_to(mem, "west", (1, 2), "UP") is None


def test_route_and_frontier_never_cross_known_exit(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)
    for pos in [(1, 0), (1, -1), (1, -2), (0, -2)]: tile(mem, "town", pos)
    assert route(mem, "town", (0, 0), (0, -2))[0] != "UP"  # detours (4 steps), never UP through the exit
    assert ((0, 0), "UP") not in frontier(mem, "town")
def test_successful_move_clears_blocked_direction(tmp_path):
    mem = Memory(tmp_path)
    tile(mem, "hall", (0, 0), blocked=["RIGHT"])

    assert fold(mem, "hall", (0, 0), [("RIGHT", (-16, 0))], 16, 2) == (1, 0)
    assert mem.get("tiles", "hall@0,0")["blocked"] == []


def test_inferred_exit_does_not_block_route_or_frontier(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)

    assert route(mem, "house", (0, 1), (0, 2)) == ["DOWN"]
    assert ((0, 1), "DOWN") in frontier(mem, "house")
