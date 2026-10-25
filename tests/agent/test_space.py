import pytest

from agent.memory import Looks, Memory
from agent.space import DIRS, exit_to, fold, frontier, mark_exit, outward, route, tile_of
from agent.world import World


def test_push_dirs_prioritizes_wall_then_neighbour_exit_then_unknown_floor(tmp_path):
    from agent.space import push_dirs
    mem = Memory(tmp_path)
    for pos in ((0, 0), (-1, 0), (1, 0), (0, -1)): tile(mem, "room", pos)
    assert push_dirs(mem, "room", (0, 0))[0] == "DOWN"
    tile(mem, "room", (1, 0), exit={"dir": "RIGHT", "src": "inferred"})
    assert push_dirs(mem, "room", (0, 0))[:2] == ["RIGHT", "DOWN"]
    assert push_dirs(mem, "room", (0, 0), "left wall")[:3] == ["LEFT", "RIGHT", "DOWN"]
    tile(mem, "room", (0, 0), pushed=["RIGHT"], blocked=["DOWN"])
    assert set(push_dirs(mem, "room", (0, 0))) == {"UP", "LEFT"}
    tile(mem, "room", (0, 0), held=["UP"])
    assert "UP" not in push_dirs(mem, "room", (0, 0))
    tile(mem, "room", (0, 0), pushed=["RIGHT", "LEFT"], blocked=["DOWN", "UP"])
    assert push_dirs(mem, "room", (0, 0)) == []


def test_plan_exit_with_no_push_directions_has_no_route(tmp_path):
    mem = Memory(tmp_path); mem.upsert("places", {"id": "room", "screen": "world", "state": "place"})
    tile(mem, "room", (0, 0), blocked=list(DIRS))
    world = World(mem, 16); world.current_place = "room"
    assert world.plan({"kind": "exit", "place": "room", "tile": (0, 0)}) is None


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
    fold(blank, "hall", (0, 0), [("RIGHT", (0, 0))], 16, 2)
    assert len((tmp_path / "blank" / "tiles.jsonl").read_text().splitlines()) == len(lines)


def test_actor_zero_motion_records_held_without_a_wall(tmp_path):
    from types import SimpleNamespace

    mem = Memory(tmp_path); mem.upsert("places", {"id": "hall", "screen": "world", "state": "place"})  # the loop always has the place row (match_place mints it)
    world = World(mem, 16)
    per = {"screen": "world", "entities": [
        {"kind": "player", "at": [.45, .5]}, {"kind": "NPC: shopkeeper", "at": [.55, .5]},
    ]}
    world.before(per, "hall", None, (160, 144), 1)
    world.prepare(SimpleNamespace(moves=[("RIGHT", (0, 0))]), "hall", False, 1)
    assert world.actor_tiles == frozenset({(1, 0)})
    source = mem.get("tiles", "hall@0,0")
    assert source["held"] == ["RIGHT"] and source["blocked"] == []
    assert mem.get("tiles", "hall@1,0") is None and mem.rows("looks") == []

    blank = Memory(tmp_path / "blank")
    fold(blank, "hall", (0, 0), [("RIGHT", (0, 0))], 16, 1)
    assert blank.get("tiles", "hall@0,0")["blocked"] == ["RIGHT"]


def test_successful_move_clears_held_direction(tmp_path):
    mem = Memory(tmp_path)
    tile(mem, "hall", (0, 0), held=["RIGHT"])

    assert fold(mem, "hall", (0, 0), [("RIGHT", (-16, 0))], 16, 2) == (1, 0)
    assert mem.get("tiles", "hall@0,0")["held"] == []


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
    mem.get("tiles", "hall@0,0")["firm"] = list(DIRS)
    assert route(mem, "hall", (0, 0), (2, 0)) is None


def test_same_look_bump_avoids_wall_and_walking_makes_it_mixed(tmp_path):
    mem = Memory(tmp_path / "memory"); mem.looks = Looks(tmp_path / "game.jsonl", "run")
    for x in range(3):
        for y in (-1, 0, 1): tile(mem, "hall", (x, y), floor=None if (x, y) == (1, 0) else True)
    mem.upsert("looks", {"id": "hall@1,0", "place": "hall", "x": 1, "y": 0, "look": "wall-look", "step": 1})
    mem.upsert("looks", {"id": "hall@1,1", "place": "hall", "x": 1, "y": 1, "look": "wall-look", "step": 1})
    mem.learn_look("hall", (1, 0), "wall", 2)
    assert route(mem, "hall", (0, 0), (2, 0))[0] != "RIGHT"
    mem.learn_look("hall", (1, 1), "floor", 3)
    assert mem.looks.says("wall-look") == "mixed"
    assert route(mem, "hall", (0, 0), (2, 0))[0] == "RIGHT"


def test_look_wall_fallback_keeps_a_route_when_it_is_the_only_corridor(tmp_path):
    mem = Memory(tmp_path / "memory"); mem.looks = Looks(tmp_path / "game.jsonl", "run")
    for x in range(-1, 4):
        for y in range(-1, 2): tile(mem, "hall", (x, y), floor=None if y == 0 and 0 <= x <= 2 and x != 1 else False)
    tile(mem, "hall", (1, 0), floor=None)
    mem.upsert("looks", {"id": "hall@1,0", "place": "hall", "x": 1, "y": 0, "look": "wall-look", "step": 1})
    mem.learn_look("hall", (1, 0), "wall", 2)
    assert route(mem, "hall", (0, 0), (2, 0)) == ["RIGHT", "RIGHT"]


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


def test_inferred_reverse_never_overwrites_an_observed_exit_and_recrossing_keeps_it(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "west", (1, 2), "RIGHT", "east", (0, 2), 9)  # east@0,2 LEFT is only a guess
    mark_exit(mem, "east", (0, 2), "LEFT", "west", (1, 2), 10)  # the way back is crossed: observed, and must not be replaced by the guess
    assert exit_to(mem, "east", (0, 2), "LEFT")["src"] == "observed"
    assert exit_to(mem, "west", (1, 2), "RIGHT")["src"] == "observed"
    mark_exit(mem, "west", (1, 2), "RIGHT", "east", (0, 2), 11)  # re-crossing the first way does not downgrade the way back
    assert exit_to(mem, "east", (0, 2), "LEFT")["src"] == "observed"


def test_route_and_frontier_never_cross_known_exit(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)
    for pos in [(1, 0), (1, -1), (1, -2), (0, -2)]: tile(mem, "town", pos)
    assert route(mem, "town", (0, 0), (0, -2))[0] != "UP"  # detours (4 steps), never UP through the exit
    assert ((0, 0), "UP") not in frontier(mem, "town")


def test_route_never_steps_onto_observed_exit_tile(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)
    for pos in [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]: tile(mem, "town", pos)
    path = route(mem, "town", (0, 1), (0, -1))
    assert path is not None and (0, 0) not in _walk((0, 1), path)


def test_route_can_reach_observed_exit_tile_when_it_is_the_goal(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)
    assert route(mem, "town", (0, 1), (0, 0)) == ["UP"]


def test_route_can_enter_inferred_exit_tile_on_the_way_to_another_goal(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)
    tile(mem, "house", (0, 0))
    tile(mem, "house", (0, 2))
    path = route(mem, "house", (0, 0), (0, 2))
    assert path is not None and (0, 1) in _walk((0, 0), path)


def test_explore_dirs_routes_around_observed_exit_tile(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("seen", {"id": "town", "cells": [], "open": ["7,4"]})
    for x in range(6, 9):
        for y in range(4, 7): tile(mem, "town", (x, y))
    mark_exit(mem, "town", (7, 5), "UP", "house", (0, 1), 1)
    world = World(mem, 16); world.current_place, world.pos, world.size = "town", (7, 6), (160, 144)
    routes = world.explore_dirs("town")
    assert "UP" not in routes or routes["UP"]["dirs"][0] != "UP"


def test_successful_move_clears_blocked_direction(tmp_path):
    mem = Memory(tmp_path)
    tile(mem, "hall", (0, 0), blocked=["RIGHT"])

    assert fold(mem, "hall", (0, 0), [("RIGHT", (-16, 0))], 16, 2) == (1, 0)
    assert mem.get("tiles", "hall@0,0")["blocked"] == []


def test_inferred_exit_is_avoided_by_routes_unless_it_is_the_only_way_but_stays_in_the_frontier(tmp_path):
    mem = Memory(tmp_path)
    mark_exit(mem, "town", (0, 0), "UP", "house", (0, 1), 1)  # house (0,1) DOWN is the guessed way back out

    assert route(mem, "house", (0, 1), (0, 2)) == ["LEFT", "DOWN", "RIGHT"]
    mem.upsert("tiles", {**mem.get("tiles", "house@0,1"), "blocked": ["LEFT", "RIGHT", "UP"]})  # walled in: the guess is the only way
    assert route(mem, "house", (0, 1), (0, 2)) == ["DOWN"]
    assert ((0, 1), "DOWN") in frontier(mem, "house")


def _walk(start, path):
    cells = [start]
    for d in path: cells.append((cells[-1][0] + DIRS[d][0], cells[-1][1] + DIRS[d][1]))
    return cells


def _band(mem, place, start, goal):
    (x0, x1), (y0, y1) = sorted((start[0], goal[0])), sorted((start[1], goal[1]))
    for x in range(x0 - 1, x1 + 2):
        for y in range(y0 - 1, y1 + 2): tile(mem, place, (x, y))


ROOM_CASES = [  # 3-wide open room around a straight line, each direction and offset
    ((0, 1), (4, 1), (2, 1)),
    ((4, 1), (0, 1), (2, 1)),
    ((1, 0), (1, 4), (1, 2)),
    ((1, 4), (1, 0), (1, 2)),
    ((10, 7), (14, 7), (12, 7)),
]


@pytest.mark.parametrize("start, goal, actor", ROOM_CASES)
def test_route_detours_around_actor_in_open_room(tmp_path, start, goal, actor):
    mem = Memory(tmp_path)
    _band(mem, "room", start, goal)
    path = route(mem, "room", start, goal, occupied=frozenset({actor}))
    assert path is not None and len(path) == 6  # straight is 4; one side step out and one back
    cells = _walk(start, path)
    assert actor not in cells and cells[-1] == goal


def test_route_is_none_when_actor_fills_one_wide_corridor(tmp_path):
    mem = Memory(tmp_path)
    for x in range(-1, 6):
        tile(mem, "hall", (x, -1), floor=False); tile(mem, "hall", (x, 1), floor=False)
    tile(mem, "hall", (-1, 0), floor=False); tile(mem, "hall", (5, 0), floor=False)
    for x in range(5): tile(mem, "hall", (x, 0))
    assert route(mem, "hall", (0, 0), (4, 0)) == ["RIGHT"] * 4
    assert route(mem, "hall", (0, 0), (4, 0), occupied=frozenset({(2, 0)})) is None  # strict fallback keeps the actor out too


def test_route_is_none_when_goal_is_occupied(tmp_path):
    mem = Memory(tmp_path)
    _band(mem, "room", (0, 1), (4, 1))
    assert route(mem, "room", (0, 1), (4, 1), occupied=frozenset({(4, 1)})) is None
    assert route(mem, "room", (0, 1), (4, 1), False, occupied=frozenset({(4, 1)})) is None


def test_route_goes_straight_again_once_actor_leaves(tmp_path):
    mem = Memory(tmp_path)
    _band(mem, "room", (0, 1), (4, 1))
    assert route(mem, "room", (0, 1), (4, 1), occupied=frozenset({(2, 1)})) != ["RIGHT"] * 4
    assert route(mem, "room", (0, 1), (4, 1), occupied=frozenset()) == ["RIGHT"] * 4


def test_start_in_occupied_is_still_routable(tmp_path):
    mem = Memory(tmp_path)
    _band(mem, "room", (0, 1), (4, 1))
    assert route(mem, "room", (0, 1), (4, 1), occupied=frozenset({(0, 1)})) == ["RIGHT"] * 4


def test_route_detours_around_static_thing(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("entities", {"id": "rock", "place": "room", "kind": "rock", "tile": [2, 0]})
    path = route(mem, "room", (0, 0), (4, 0))
    assert path is not None and len(path) == 6
    assert (2, 0) not in _walk((0, 0), path)


def test_static_thing_in_only_corridor_uses_fallback(tmp_path):
    mem = Memory(tmp_path)
    for x in range(-1, 6):
        tile(mem, "hall", (x, -1), floor=False); tile(mem, "hall", (x, 1), floor=False)
    tile(mem, "hall", (-1, 0), floor=False); tile(mem, "hall", (5, 0), floor=False)
    mem.upsert("entities", {"id": "sign", "place": "hall", "kind": "sign", "tile": [2, 0]})
    assert route(mem, "hall", (0, 0), (4, 0)) == ["RIGHT"] * 4


def test_static_thing_at_goal_is_reachable(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("entities", {"id": "sign", "place": "room", "kind": "sign", "tile": [4, 0]})
    assert route(mem, "room", (0, 0), (4, 0)) == ["RIGHT"] * 4


@pytest.mark.parametrize("entity", [
    {"kind": "NPC: guard"}, {"kind": "exit"}, {"kind": "door", "name": "exit: doorway"},
    {"kind": "dialogue box"}, {"kind": "rock", "state": "merged"},
    {"kind": "rock", "status": "gone"}, {"kind": "rock", "place": "elsewhere"},
])
def test_route_ignores_non_static_or_inactive_entities(tmp_path, entity):
    mem = Memory(tmp_path)
    mem.upsert("entities", {"id": "thing", "place": "room", "tile": [2, 0], **entity})
    assert route(mem, "room", (0, 0), (4, 0)) == ["RIGHT"] * 4


def test_failed_push_is_recorded_on_departure_and_next_plan_tries_another_direction(tmp_path):
    from agent.skills import Ctx, press
    from agent.space import push_failed
    from tests.agent.fakes import GridEnv, StubModel
    mem = Memory(tmp_path); mem.upsert("places", {"id": "room", "screen": "world", "state": "place"})
    tile(mem, "room", (0, 0)); tile(mem, "room", (-1, 0))
    env = GridEnv(); world = World(mem, 16); world.tracker.wrap(env); env.tick()
    frame = env.screenshot(); mem.remember_view("room", (-1, 0), frame); per = env.perception()
    world.before(per, "room", "source", frame.size, 1, frame)
    target = {"kind": "exit", "place": "room", "tile": (0, 0), "exit": {"placement": "left wall"}}
    assert world.plan(target)[-1]["push"] == "LEFT"
    ctx = Ctx(env, StubModel([]), 16, odometer=world.odometer)
    right = press(ctx, "RIGHT"); world.prepare(right, "room", False, 2)
    start = world.pos
    skill = press(ctx, "LEFT"); world.prepare(skill, "room", False, 3)
    skill.failure = {"stage": "crossing", "push": "LEFT"}
    world.failed_push(skill, "room", start, "LEFT", 3)
    assert mem.get("tiles", "room@0,0")["pushed"] == ["LEFT"] and mem.get("tiles", "room@-1,0")["pushed"] == []
    assert world.plan(target)[-1]["push"] != "LEFT"
    before = (tmp_path / "tiles.jsonl").read_text()
    push_failed(mem, "room", (0, 0), "LEFT", 3)
    assert (tmp_path / "tiles.jsonl").read_text() == before
    mark_exit(mem, "room", (0, 0), "LEFT", "hall", (0, 0), 4)
    assert Memory(tmp_path).get("tiles", "room@0,0")["pushed"] == []


def test_blocked_push_keeps_only_blocked_evidence(tmp_path):
    from types import SimpleNamespace
    from agent.space import push_dirs
    mem = Memory(tmp_path); mem.upsert("places", {"id": "room", "screen": "world", "state": "place"}); world = World(mem, 16)
    world.before({"screen": "world", "entities": []}, "room", None, (160, 144), 1)
    skill = SimpleNamespace(failure={"stage": "crossing", "push": "LEFT"}, moves=[{"dir": "LEFT", "shift": (0, 0)}])
    world.prepare(skill, "room", False, 1); world.failed_push(skill, "room", (0, 0), "LEFT", 1)
    assert mem.get("tiles", "room@0,0")["blocked"] == ["LEFT"] and mem.get("tiles", "room@0,0")["pushed"] == []
    assert "LEFT" not in push_dirs(mem, "room", (0, 0))


def test_once_bumped_walls_allow_fallback_and_later_bumps_become_firm(tmp_path, monkeypatch):
    from agent import space
    mem = Memory(tmp_path)
    fold(mem, "room", (0, 0), [(d, (0, 0)) for d in DIRS], 16, 1)
    original = space.route; fallbacks = []
    def fallback(*args, **kw):
        fallbacks.append(args[4]); return original(*args, **kw)
    monkeypatch.setattr(space, "route", fallback)
    assert original(mem, "room", (0, 0), (0, 2)) == ["DOWN", "DOWN"] and fallbacks == [False]
    fold(mem, "room", (0, 0), [("DOWN", (0, 0))], 16, 1)
    assert mem.get("tiles", "room@0,0").get("firm", []) == []
    fold(mem, "room", (0, 0), [("DOWN", (0, 0))], 16, 2)
    assert Memory(tmp_path).get("tiles", "room@0,0")["firm"] == ["DOWN"]
    assert original(mem, "room", (0, 0), (0, 2), False)[0] != "DOWN"
    fold(mem, "room", (0, 0), [("DOWN", (0, -16))], 16, 3)
    row = Memory(tmp_path).get("tiles", "room@0,0")
    assert "DOWN" not in row["blocked"] and row["firm"] == []


def test_wall_confirmed_only_by_rebump_after_it_was_written(tmp_path):
    mem = Memory(tmp_path)
    fold(mem, "room", (0, 0), [("DOWN", (0, 0))], 16, 1)
    fold(mem, "room", (0, 0), [("RIGHT", (0, 0)), ("DOWN", (0, 0))], 16, 2)
    row = mem.get("tiles", "room@0,0")
    assert row["firm"] == ["DOWN"] and "RIGHT" in row["blocked"]


def test_one_no_response_records_silent_direction_without_wall(tmp_path):
    mem = Memory(tmp_path)
    fold(mem, "room", (0, 0), [{"dir": "LEFT", "shift": None, "no_response": True}], 16, 1)
    row = Memory(tmp_path).get("tiles", "room@0,0")
    assert row["blocked"] == [] and row["silent"] == {"LEFT": 1}


def test_no_response_on_a_later_step_records_unconfirmed_wall(tmp_path):
    mem = Memory(tmp_path); moves = [{"dir": "LEFT", "shift": None, "no_response": True}]
    fold(mem, "room", (0, 0), moves, 16, 1); fold(mem, "room", (0, 0), moves, 16, 2)
    row = Memory(tmp_path).get("tiles", "room@0,0")
    assert row["blocked"] == ["LEFT"] and "LEFT" not in row.get("firm", [])


def test_two_no_responses_in_one_step_do_not_record_wall(tmp_path):
    mem = Memory(tmp_path); moves = [{"dir": "LEFT", "shift": None, "no_response": True}]
    fold(mem, "room", (0, 0), moves * 2, 16, 1)
    row = Memory(tmp_path).get("tiles", "room@0,0")
    assert row["blocked"] == [] and row["silent"] == {"LEFT": 1}


def test_successful_move_clears_silent_and_wall_evidence(tmp_path):
    mem = Memory(tmp_path); moves = [{"dir": "LEFT", "shift": None, "no_response": True}]
    fold(mem, "room", (0, 0), moves, 16, 1); fold(mem, "room", (0, 0), moves, 16, 2)
    assert fold(mem, "room", (0, 0), [("LEFT", (16, 0))], 16, 3) == (-1, 0)
    row = Memory(tmp_path).get("tiles", "room@0,0")
    assert row["blocked"] == [] and row["walled"] == {} and row["silent"] == {}
