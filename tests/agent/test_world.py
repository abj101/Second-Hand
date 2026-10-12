from agent.memory import Memory
from agent.world import World


def place(mem, id, name, first=1):
    mem.upsert("places", {"id": id, "name": name, "screen": "room", "state": "place", "first": first, "last": first, "edges": []})


def entity(mem, id, name, place, tile, seen=2):
    mem.upsert("entities", {"id": id, "name": name, "kind": "object", "place": place, "tile": tile, "seen": seen})


def test_merge_shifts_unions_redirects_and_collapses(tmp_path):
    mem = Memory(tmp_path); place(mem, "src", "room"); place(mem, "dst", "Hall"); place(mem, "from", "Start")
    entity(mem, "s1", "Oak", "src", [1, 1]); entity(mem, "s2", "Oak", "src", [1, 2], 3)
    entity(mem, "s3", "Pine", "src", [2, 1]); entity(mem, "d1", "Oak", "dst", [5, 6], 4)
    entity(mem, "d2", "Pine", "dst", [6, 6])
    mem.upsert("tiles", {"id": "src@1,1", "place": "src", "x": 1, "y": 1, "floor": True, "blocked": ["UP"], "pushed": [], "exit": None, "step": 1})
    mem.upsert("tiles", {"id": "dst@5,6", "place": "dst", "x": 5, "y": 6, "floor": False, "blocked": [], "pushed": ["LEFT"], "exit": {"dir": "UP", "to": "from", "arrive": [0, 0]}, "step": 1})
    mem.upsert("tiles", {"id": "from@0,0", "place": "from", "x": 0, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": {"dir": "DOWN", "to": "src", "arrive": [1, 1]}, "step": 1})
    mem.upsert("places", {**mem.get("places", "from"), "edges": [{"verb": "enter", "via": "door", "to": "src"}]})
    mem.upsert("relations", {"id": "r1", "s": "src", "p": "leads_to", "o": "from", "state": "live"})
    world = World(mem, 16); world.current_place, world.pos = "src", (3, 4)
    assert world.merge("src", "dst", 8)
    assert mem.get("places", "src")["offset"] == [4, 5] and world.pos == (7, 9)
    tile = mem.get("tiles", "dst@5,6")
    assert tile["floor"] is True and tile["blocked"] == ["UP"] and tile["pushed"] == ["LEFT"]
    assert mem.get("tiles", "from@0,0")["exit"]["to"] == "dst" and mem.get("tiles", "from@0,0")["exit"]["arrive"] == [5, 6]
    assert mem.get("places", "from")["edges"][0]["to"] == "dst" and mem.get("relations", "r1")["s"] == "dst"
    assert mem.get("entities", "d1")["seen"] == 9 and mem.get("entities", "s2")["place"] == "src"
    replay = Memory(tmp_path)
    assert replay.get("places", "src")["state"] == "merged" and replay.get("places", "src")["into"] == "dst"
    assert replay.resolve("Oak", "dst", (5, 6)) == "d1"
    assert replay.observe({"entities": [{"name": "Oak", "kind": "object"}]}, "dst", 9, {0: (5, 6)}) == {0: "d1"}


def test_name_refusal_alias_flag_and_new_arrival(tmp_path):
    mem = Memory(tmp_path); place(mem, "a", "Room", 1); place(mem, "b", "Room B", 1)
    entity(mem, "a1", "Oak", "a", [0, 0]); entity(mem, "a2", "Oak#2", "a", [1, 0])
    entity(mem, "b1", "Oak", "b", [4, 4]); entity(mem, "b2", "Oak#2", "b", [5, 4])
    world = World(mem, 16)
    assert world.name("a", "room b", 3) == "a"
    assert mem.get("places", "a")["aliases"] == ["room b"] and world.merge_pending == "room b"
    place(mem, "new", "screen", 5); place(mem, "old", "Known", 1)
    assert world.name("new", "known", 5) == "old" and mem.get("places", "new")["into"] == "old"
