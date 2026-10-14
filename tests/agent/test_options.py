import json

from agent.loop import Loop
from agent.memory import Memory
from agent.world import World, fail_key
from tests.agent.fakes import GridEnv, StubModel

INPUTS = GridEnv().inputs()
BUTTONS = f"free input (any of: {', '.join(INPUTS)})"


def at(dx, dy):  # GridEnv(wander=True) geometry: the player's centre is (88, 72), tiles are 16 px
    return [(88 + 16 * dx) / 160, (72 + 16 * dy) / 144]


def player(): return {"name": "player", "kind": "player", "at": at(0, 0)}


def room(mem):
    mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    for pid, name in (("hall", "Hall"), ("cave", "Cave")): mem.upsert("places", {"id": pid, "name": name, "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    for x in range(-2, 3):
        for y in range(-2, 3): tile(mem, x, y)


def tile(mem, x, y, exit=None):
    mem.upsert("tiles", {"id": f"room@{x},{y}", "place": "room", "x": x, "y": y, "floor": True, "blocked": [], "pushed": [], "exit": exit, "step": 1})


def setup(mem, entities, screen="world"):
    world = World(mem, 16); world.current_place = "room"
    per = {"screen": screen, "text": [], "entities": [player(), *entities]}
    world.before(per, "room", None, (160, 144), 1)
    return world, per


def ids(world, per): return {e["name"]: world.entity_ids[i] for i, e in enumerate(per["entities"]) if i in world.entity_ids and e.get("name")}


def scene(tmp_path):
    """Room with a used exit (door, to Hall), an unused tile exit (UP, to Cave), an NPC and a refuted fake exit."""
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "observed"})
    world, per = setup(mem, [{"name": "exit: door", "kind": "exit", "at": at(2, 0)}, {"name": "npc", "kind": "npc", "at": at(-1, 0)},
                             {"name": "exit: fake", "kind": "exit", "at": at(1, 2)}])
    i = ids(world, per)
    mem.upsert("places", {**mem.get("places", "room"), "edges": [{"verb": "approach", "via": "exit: door", "to": "hall", "n": 2, "ok": 2}]})
    mem.upsert("relations", {"id": "lt", "s": i["exit: door"], "p": "leads_to", "o": "hall", "src": "observed", "sup": 2, "con": 0, "ev": [30, 40], "state": "live", "step": 40})
    mem.upsert("relations", {"id": "dn", "s": i["exit: fake"], "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 3, "ev": [104, 105, 107], "state": "refuted", "verb": "buttons", "step": 107})
    mem.upsert("relations", {"id": "say", "s": i["npc"], "p": "says", "o": "Hi", "src": "model", "sup": 0, "con": 0, "ev": [], "state": "live", "why": "says the same as before (step 90)", "step": 91})
    return mem, world, per


def test_modal_screen_offers_its_choices_then_free_input_and_no_go_to(tmp_path):
    mem = Memory(tmp_path); room(mem)
    menu = [{"name": "fight", "kind": "menu_item", "at": [.3, .4]}, {"name": "bag", "kind": "menu_item", "at": [.3, .6]}, {"name": "box", "kind": "dialogue_box", "at": [.5, .9]}]
    world, per = setup(mem, menu, screen="menu")
    opts = world.options(per, "room", {}, 5, inputs=INPUTS)
    assert [(o["tool"], o["label"]) for o in opts] == [("select", "select fight"), ("select", "select bag"), ("advance", "advance"), ("cancel", "cancel"), ("buttons", BUTTONS)]
    assert opts[0]["action"] == {"thing": 1, "verb": "select"} and opts[2]["action"] == {"verb": "advance"} and opts[-1]["action"] is None
    kb = Memory(tmp_path / "kb"); room(kb)
    world, per = setup(kb, [{"name": "keyboard", "kind": "keyboard", "at": [.5, .7]}, {"name": "letters", "kind": "menu_item", "at": [.4, .4]}], screen="keyboard")
    labels = [o["label"] for o in world.options(per, "room", {}, 5, inputs=INPUTS)]
    assert labels == ["select letters", "advance", "cancel", "type text into the field", BUTTONS]


def test_room_lists_exits_things_explore_and_free_input_in_rule_order_with_notes(tmp_path):
    mem, world, per = scene(tmp_path)
    opts = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert [(o["n"], o["tool"], o["label"]) for o in opts] == [
        (1, "go_to", "exit UP: leads to Cave (never used)"),
        (2, "approach", "exit door: leads to Hall (used 2×, last step 40)"),
        (3, "approach", "exit fake: never used"),
        (4, "approach", "approach npc"),
        (5, "interact", "interact with npc"),
        (6, "explore", opts[5]["label"]),
        (7, "buttons", BUTTONS)]
    assert opts[0]["action"] == {"go_to": "Cave"} and opts[1]["action"] == {"thing": 1, "verb": "approach"} and opts[6]["action"] is None
    assert opts[5]["label"].startswith("explore the nearest untried edge (") and "untried" in opts[5]["label"] and opts[5]["action"] == {"go_to": "unexplored"}
    assert opts[2]["notes"] == ["refuted: not working: walked into 3×, steps 104–107"]  # refuted, still offered
    assert opts[4]["notes"] == ["talked; same text as before (step 90)"]
    assert opts[0]["notes"] == [] and opts[1]["notes"] == []
    assert not any(o["tool"] == "go_to" and o["label"].startswith("go to") for o in opts)  # Hall and Cave are exits, not extra go_to rows


def test_on_screen_exit_that_is_also_a_tile_exit_appears_once(tmp_path):
    mem, world, per = scene(tmp_path)
    opts = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert sum("door" in o["label"] for o in opts) == 1 and sum("Hall" in o["label"] for o in opts) == 1


def test_overflow_keeps_exits_and_things_and_summarises_the_rest(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "observed"})
    for n in range(15): mem.upsert("entities", {"id": f"crate{n}", "name": f"crate {n}", "kind": "object", "place": "room", "tile": [-2, n % 5 - 2], "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [{"name": "npc", "kind": "npc", "at": at(-1, 0)}])
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    info = opts[-1]
    assert len(opts) == 13 and info["tool"] == "info" and info["action"] is None and info["notes"] == []
    dropped = info["label"].split(": ", 1)[1].split(", ")
    assert len(dropped) == 9 and all(name.startswith("crate ") for name in dropped)
    assert not any(o["label"].startswith(f"go to {name} ") for o in opts for name in dropped)
    assert sum(o["label"].startswith("go to crate") for o in opts) == 6
    labels = [o["label"] for o in opts]
    assert "approach npc" in labels and "interact with npc" in labels and "exit UP: leads to Cave (never used)" in labels and "exit RIGHT: leads to Hall (never used)" in labels
    assert opts[-2]["tool"] == "buttons"


def test_options_are_deterministic_and_write_nothing(tmp_path):
    mem, world, per = scene(tmp_path)
    stores = ("places", "tiles", "entities", "relations", "seen", "views", "knowledge")
    snapshot = lambda: json.dumps([mem.rows(s) for s in stores], sort_keys=True)
    before = snapshot()
    first = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert world.options(per, "room", {}, 120, inputs=INPUTS) == first and snapshot() == before


def test_free_input_carries_this_places_button_failures_only(tmp_path):
    mem, world, per = scene(tmp_path)
    fails = {fail_key("room", "buttons", "UP A", INPUTS): {"n": 2, "why": "wall", "step": 70, "act": "buttons UP A"},
             fail_key("room", "buttons", "A", INPUTS): {"n": 1, "why": "no change", "step": 90, "act": "buttons A"},
             fail_key("other", "buttons", "UP A", INPUTS): {"n": 5, "why": "wall", "step": 99, "act": "buttons UP A"},
             fail_key("room", "approach", "npc", INPUTS): {"n": 1, "why": "wall", "step": 95, "act": "approach npc"}}
    opts = world.options(per, "room", fails, 120, inputs=INPUTS)
    assert opts[-1]["label"] == BUTTONS and opts[-1]["notes"] == ["tried here 1×, last: no change", "tried here 2×, last: wall"]  # newest first; other place and approach rows excluded


def test_fail_notes_use_the_loop_failure_key_and_mark_cycles(tmp_path):
    mem, world, per = scene(tmp_path)
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "loop"), "grid")
    assert loop._fail_key("room", "approach", "npc") == fail_key("room", "approach", "npc", INPUTS)
    fails = {fail_key("room", "approach", "npc", INPUTS): {"n": 2, "why": "wall", "step": 50, "act": "approach npc"},
             fail_key("room", "approach", "Cave", INPUTS): {"n": 2, "why": "leads back to Cave: cycle", "step": 60}}
    by = {o["label"]: o for o in world.options(per, "room", fails, 120, inputs=INPUTS)}
    assert by["approach npc"]["notes"] == ["tried here 2×, last: wall", "talked; same text as before (step 90)"]
    assert by["interact with npc"]["notes"][0] == "tried here 2×, last: wall"  # same key: the loop's targets ignore the verb
    assert by["exit UP: leads to Cave (never used)"]["notes"] == ["part of a back-and-forth cycle (step 60)"]
    assert by["exit fake: never used"]["notes"][0].startswith("refuted: not working")
