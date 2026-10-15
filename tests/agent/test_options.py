import json
from types import SimpleNamespace

from agent.loop import Loop
from agent.memory import Memory
from agent.space import mark_exit
from agent.world import OPTION_CAP, World, fail_key
from tests.agent.fakes import GridEnv, StubModel

INPUTS = GridEnv().inputs()
BUTTONS = f"free buttons: {', '.join(INPUTS)}"


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
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "inferred"})
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
    menu = [{"name": "fight", "kind": "menu_item", "at": [.3, .4], "selectable": True}, {"name": "bag", "kind": "menu_item", "at": [.3, .6], "selectable": True}, {"name": "box", "kind": "dialogue_box", "at": [.5, .9]}]
    world, per = setup(mem, menu, screen="menu")
    opts = world.options(per, "room", {}, 5, inputs=INPUTS)
    assert [(o["tool"], o["label"]) for o in opts] == [("select", "select fight"), ("select", "select bag"), ("advance", "advance"), ("cancel", "cancel"), ("buttons", BUTTONS)]
    assert opts[0]["action"] == {"thing": 1, "verb": "select"} and opts[2]["action"] == {"verb": "advance"} and opts[-1]["action"] is None
    kb = Memory(tmp_path / "kb"); room(kb)
    world, per = setup(kb, [{"name": "keyboard", "kind": "keyboard", "at": [.5, .7]}, {"name": "letters", "kind": "menu_item", "at": [.4, .4], "selectable": True}], screen="keyboard")
    labels = [o["label"] for o in world.options(per, "room", {}, 5, inputs=INPUTS)]
    assert labels == ["select letters", "advance", "cancel", "type text into the field", BUTTONS]


def labels_on(tmp_path, entities, screen):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, entities, screen=screen)
    return [o["label"] for o in world.options(per, "room", {}, 5, inputs=INPUTS)]


def test_dialogue_over_furniture_and_npc_offers_no_select(tmp_path):
    labels = labels_on(tmp_path, [{"name": "television", "kind": "furniture", "at": [.2, .3]}, {"name": "dialogue_box", "kind": "dialogue_box", "at": [.5, .9]},
                                  {"name": "girl", "kind": "npc", "at": [.8, .4]}], "dialogue")
    assert not any(label.startswith("select") for label in labels)
    assert labels == ["advance", "cancel", BUTTONS]


def test_dialogue_offers_select_only_for_flagged_yes_and_no(tmp_path):
    labels = labels_on(tmp_path, [{"name": "yes", "kind": "menu_item", "at": [.3, .4], "selectable": True}, {"name": "no", "kind": "menu_item", "at": [.3, .6], "selectable": True},
                                  {"name": "television", "kind": "furniture", "at": [.8, .3]}, {"name": "dialogue_box", "kind": "dialogue_box", "at": [.5, .9]}], "dialogue")
    assert labels == ["select yes", "select no", "advance", "cancel", BUTTONS]


def test_decorative_sprites_and_unflagged_items_are_not_choices_on_menus(tmp_path):
    menu = labels_on(tmp_path / "menu", [{"name": "pokeball sprite", "kind": "menu_item", "at": [.8, .5]}, {"name": "fight", "kind": "menu_item", "at": [.3, .4], "selectable": True},
                                         {"name": "bag", "kind": "menu_item", "at": [.3, .6], "selectable": True}], "menu")
    title = labels_on(tmp_path / "title", [{"name": "logo", "kind": "logo", "at": [.5, .2]}, {"name": "start", "kind": "menu_item", "at": [.5, .6], "selectable": True}], "title")
    battle = labels_on(tmp_path / "battle", [{"name": "enemy sprite", "kind": "monster", "at": [.7, .3]}, {"name": "fight", "kind": "menu_item", "at": [.3, .6], "selectable": True},
                                             {"name": "run", "kind": "menu_item", "at": [.3, .8], "selectable": True}], "battle")
    assert menu == ["select fight", "select bag", "advance", "cancel", BUTTONS]  # the sprite is not a choice
    assert title == ["select start", "advance", "cancel", BUTTONS]
    assert battle == ["select fight", "select run", "advance", "cancel", BUTTONS]  # the enemy sprite is not a choice


def test_room_lists_exits_things_explore_and_free_input_in_rule_order_with_notes(tmp_path):
    mem, world, per = scene(tmp_path)
    opts = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert [(o["n"], o["tool"], o["label"]) for o in opts] == [
        (1, "go_to", "possible exit UP (unverified, never crossed)"),
        (2, "approach", "exit door → Hall (used 2×, last step 40)"),
        (3, "approach", "not an exit: fake (walked into 3×, steps 104–107)"),
        (4, "approach", "approach npc"),
        (5, "interact", "interact with npc"),
        (6, "explore", opts[5]["label"]),
        (7, "buttons", BUTTONS)]
    assert opts[0]["action"] == {"go_to": "Cave"} and opts[1]["action"] == {"thing": 1, "verb": "approach"} and opts[6]["action"] is None
    assert opts[5]["label"].startswith("walk to an unexplored part of this place (") and opts[5]["action"] == {"go_to": "unexplored"}
    assert opts[2]["notes"] == ["refuted: not working: walked into 3×, steps 104–107"]  # refuted, still offered
    assert opts[4]["notes"] == ["talked; same text as before (step 90)"]
    assert opts[0]["notes"] == [] and opts[1]["notes"] == []
    assert not any(o["tool"] == "go_to" and o["label"].startswith("go to") for o in opts)  # Hall and Cave are exits, not extra go_to rows


def test_on_screen_exit_that_is_also_a_tile_exit_appears_once(tmp_path):
    mem, world, per = scene(tmp_path)
    opts = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert sum("door" in o["label"] for o in opts) == 1 and sum("Hall" in o["label"] for o in opts) == 1


SPOTS = [(x, y) for y in (-1, 1, 2) for x in (-2, -1, 0, 1, 2)] + [(-2, -2)]  # room tiles clear of the exits (2,0), (0,-2) and the npc (-1,0)


def names_in(opt): return opt["label"].split(": ", 1)[1].split(", ")


def test_fifteen_remembered_things_share_one_row_and_stay_reachable(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "observed"})
    for n in range(15): mem.upsert("entities", {"id": f"crate{n}", "name": f"crate {n}", "kind": "object", "place": "room", "tile": [-2, n % 5 - 2], "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [{"name": "npc", "kind": "npc", "at": at(-1, 0)}])
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    labels = [o["label"] for o in opts]
    assert len(opts) - 1 <= OPTION_CAP and opts[-1]["label"] == BUTTONS
    assert set(labels[:2]) == {"exit UP → Cave (used 1×)", "exit RIGHT → Hall (used 1×)"}  # exits first
    assert "approach npc" in labels and "interact with npc" in labels
    thing = opts[-2]
    assert thing["tool"] == "go_to" and thing["action"] is None and thing["notes"] == []
    assert thing["label"].startswith("look at or go to a thing here: ") and sorted(names_in(thing)) == ["crate ×15"]
    assert not any(label.startswith("go to crate") for label in labels)


def test_twelve_plain_things_one_character_two_exits(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "observed"})
    lamps = [{"name": f"lamp {n}", "kind": "furniture", "at": at(*SPOTS[n])} for n in range(12)]
    world, per = setup(mem, [{"name": "npc", "kind": "npc", "at": at(-1, 0)}, *lamps])
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    labels = [o["label"] for o in opts]
    assert len(opts) <= OPTION_CAP + 1 and opts[-1]["label"] == BUTTONS  # free buttons last
    assert set(labels[:2]) == {"exit UP → Cave (used 1×)", "exit RIGHT → Hall (used 1×)"}
    assert "approach npc" in labels and "interact with npc" in labels
    things = [o for o in opts if o["label"].startswith("look at or go to a thing here: ")]
    assert len(things) == 1 and things[0]["tool"] == "go_to" and things[0]["action"] is None
    assert sorted(names_in(things[0])) == ["lamp ×12"]
    assert not any("lamp" in label for label in labels if label != things[0]["label"])


def test_a_thing_with_history_keeps_its_own_option(tmp_path):
    mem = Memory(tmp_path); room(mem)
    lamps = [{"name": f"lamp {n}", "kind": "furniture", "at": at(*SPOTS[n])} for n in range(12)]
    world, per = setup(mem, [*lamps, {"name": "sign", "kind": "furniture", "at": at(*SPOTS[12])}, {"name": "vase", "kind": "furniture", "at": at(*SPOTS[13])}])
    i = ids(world, per)
    mem.upsert("relations", {"id": "dn", "s": i["sign"], "p": "does_nothing", "o": "", "src": "observed", "sup": 0, "con": 0, "ev": [], "state": "live", "verb": "approach", "step": 5})  # talked-to or walked-into history, any state
    fails = {fail_key("room", "approach", None, INPUTS, ref=f"thing:{i['vase']}"): {"n": 1, "why": "wall", "step": 4, "act": "approach vase"}}
    opts = world.options(per, "room", fails, 9, inputs=INPUTS)
    by = {o["label"]: o for o in opts}
    assert by["approach sign"]["action"] == {"thing": 13, "verb": "approach"}  # index 0 is the player
    assert by["approach vase"]["notes"] == ["tried here 1×, last: wall"]
    things = [o for o in opts if o["label"].startswith("look at or go to a thing here: ")]
    assert sorted(names_in(things[0])) == ["lamp ×12"]  # the sign and the vase are not in the shared row
    assert "sign" not in names_in(things[0]) and "vase" not in names_in(things[0])
    assert len(opts) - 1 <= OPTION_CAP


def test_known_places_over_the_cap_share_one_row_and_none_is_lost(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    for n in range(10):
        mem.upsert("places", {"id": f"t{n}", "name": f"Town {n}", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
        mem.upsert("tiles", {"id": f"t{n}@0,0", "place": f"t{n}", "x": 0, "y": 0, "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
        mark_exit(mem, "hall", (n, 0), "DOWN", f"t{n}", (0, 0), 1)
    world, per = setup(mem, [])
    opts = world.options(per, "room", {}, 3, inputs=INPUTS)
    labels = [o["label"] for o in opts]
    shared = [o for o in opts if o["label"].startswith("go to another known place: ")]
    assert len(shared) == 1 and shared[0]["tool"] == "go_to" and shared[0]["action"] is None
    assert sorted(names_in(shared[0])) == sorted(f"Town {n}" for n in range(10))
    assert not any(label.startswith("go to Town") for label in labels)
    assert len(opts) - 1 <= OPTION_CAP and opts[-1]["label"] == BUTTONS


def test_options_are_deterministic_and_write_nothing(tmp_path):
    mem, world, per = scene(tmp_path)
    stores = ("places", "tiles", "entities", "relations", "seen", "views", "knowledge")
    snapshot = lambda: json.dumps([mem.rows(s) for s in stores], sort_keys=True)
    before = snapshot()
    first = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert world.options(per, "room", {}, 120, inputs=INPUTS) == first and snapshot() == before


def test_free_input_carries_this_places_button_failures_only(tmp_path):
    mem, world, per = scene(tmp_path)
    fails = {fail_key("room", "buttons", "DOWN", INPUTS): {"n": 2, "why": "wall", "step": 70, "act": "buttons DOWN"},
             fail_key("room", "buttons", "UP A", INPUTS): {"n": 4, "why": "no change", "step": 90, "act": "buttons UP A"},
             fail_key("other", "buttons", "UP A", INPUTS): {"n": 5, "why": "wall", "step": 99, "act": "buttons UP A"},
             fail_key("room", "approach", "npc", INPUTS): {"n": 1, "why": "wall", "step": 95, "act": "approach npc"}}
    opts = world.options(per, "room", fails, 120, inputs=INPUTS)
    assert opts[-1]["label"] == BUTTONS and opts[-1]["notes"] == ["failed here: UP A x4, DOWN x2"]  # one note, newest first; other place and approach rows excluded


def test_fail_notes_use_the_loop_failure_key_and_mark_cycles(tmp_path):
    mem, world, per = scene(tmp_path)
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "loop"), "grid")
    i = ids(world, per)
    assert loop._fail_key("room", "approach", None, ref=f"thing:{i['npc']}") == fail_key("room", "approach", None, INPUTS, ref=f"thing:{i['npc']}")
    base = {o["label"]: o for o in world.options(per, "room", {}, 120, inputs=INPUTS)}
    fails = {base["approach npc"]["intent_key"]: {"n": 2, "why": "wall", "step": 50, "act": "approach npc"},
             base["possible exit UP (unverified, never crossed)"]["intent_key"]: {"n": 2, "why": "leads back to Cave: cycle", "step": 60}}
    by = {o["label"]: o for o in world.options(per, "room", fails, 120, inputs=INPUTS)}
    assert by["approach npc"]["notes"] == ["tried here 2×, last: wall", "talked; same text as before (step 90)"]
    assert by["interact with npc"]["notes"] == ["talked; same text as before (step 90)"]  # an interaction failure is not an approach failure
    assert by["possible exit UP (unverified, never crossed)"]["notes"] == ["part of a back-and-forth cycle (step 60)"]
    assert by["not an exit: fake (walked into 3×, steps 104–107)"]["notes"][0].startswith("refuted: not working")


def walk(world, src, dst, direction, step):  # one crossing as the loop reports it: a scene cut after a single press
    skill = SimpleNamespace(moves=[{"dir": direction}], scroll=None)
    world.current_place = src; world.prepare(skill, src, True, step); world.after(skill, src, dst, True, step); world.current_place = dst


def test_two_room_exit_is_verified_on_both_sides_and_counts_every_crossing(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)  # the first crossing writes both exits, as Loop does
    world, per = setup(mem, []); world.pos = (2, 0)  # standing on the exit tile
    walk(world, "room", "hall", "RIGHT", 1); walk(world, "hall", "room", "LEFT", 2); walk(world, "room", "hall", "RIGHT", 3)
    room_labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["tool"] != "buttons"]
    hall_labels = [o["label"] for o in world.options(per, "hall", {}, 3, inputs=INPUTS) if o["tool"] != "buttons"]
    assert "exit RIGHT → Hall (used 3×, last step 3) (4 unexplored spots beyond)" in room_labels and "exit LEFT → Room (used 3×, last step 3) (19 unexplored spots beyond)" in hall_labels


def test_perceived_exit_never_crossed_is_unverified_then_refuted_after_two_failed_tries(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: wall", "kind": "exit", "at": at(1, -2)}])  # a floor tile the agent never crossed
    assert "possible exit wall (unverified, never crossed)" in [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS)]
    mem.add_edge("room", "approach", "exit: wall", "room", False); mem.add_edge("room", "approach", "exit: wall", "room", False)
    assert "not an exit: wall (walked into 2×)" in [o["label"] for o in world.options(per, "room", {}, 4, inputs=INPUTS)]


def test_inferred_exit_next_to_a_verified_one_is_listed_once(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 2, 1, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "inferred"})
    world, per = setup(mem, [])
    assert [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if "Hall" in o["label"]] == ["exit RIGHT → Hall (used 1×)"]


def chain(mem):
    """Rooms A - B - C in a line. Every floor edge is blocked or an exit, so no place has untried edges of its own. C has an unused exit (north)."""
    for pid, name in (("a", "Room A"), ("b", "Room B"), ("c", "Room C")):
        mem.upsert("places", {"id": pid, "name": name, "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    def cell(place, x, y, blocked, exit=None): mem.upsert("tiles", {"id": f"{place}@{x},{y}", "place": place, "x": x, "y": y, "floor": True, "blocked": blocked, "pushed": [], "exit": exit, "step": 1})
    cell("a", 0, 0, ["UP", "DOWN", "LEFT"], {"dir": "RIGHT", "to": "b", "arrive": [0, 0], "src": "observed"})
    cell("b", 0, 0, ["UP", "DOWN"], {"dir": "LEFT", "to": "a", "arrive": [0, 0], "src": "observed"})
    cell("b", 1, 0, ["UP", "DOWN"], {"dir": "RIGHT", "to": "c", "arrive": [0, 0], "src": "observed"})
    cell("c", 0, 0, ["UP", "DOWN", "RIGHT"], {"dir": "LEFT", "to": "b", "arrive": [1, 0], "src": "observed"})
    mem.upsert("entities", {"id": "north", "name": "exit: north", "kind": "exit", "place": "c", "tile": [0, -2], "status": "remembered", "last": 1, "seen": 1})


def test_beyond_counts_what_lies_through_each_exit_and_finds_the_dead_end(tmp_path):
    mem = Memory(tmp_path); chain(mem)
    world = World(mem, 16)
    assert world.beyond("a") == {"exits": {(0, 0): {"unused_exits": 1, "unexplored": 0, "places": ["Room B", "Room C"]}}, "dead_end": True, "through": (0, 0)}
    b = world.beyond("b")  # both directions: back to A is fully explored, forward to C holds the unused exit
    assert b["exits"] == {(0, 0): {"unused_exits": 0, "unexplored": 0, "places": ["Room A"]}, (1, 0): {"unused_exits": 1, "unexplored": 0, "places": ["Room C"]}}
    assert b["dead_end"] and b["through"] == (1, 0)
    assert world.beyond("c")["dead_end"] is False  # its own unused exit is unexplored ground here


def test_exit_labels_say_what_lies_beyond_and_the_dead_end_note_comes_first(tmp_path):
    mem = Memory(tmp_path); chain(mem)
    world = World(mem, 16); per = {"screen": "world", "text": [], "entities": [player()]}
    world.current_place = "a"; a = world.options(per, "a", {}, 3, inputs=INPUTS)
    right_a = next(o for o in a if o["label"].startswith("exit RIGHT"))
    assert right_a["label"] == "exit RIGHT → Room B (used 1×) (1 unexplored exit beyond)"
    assert right_a["notes"] == ["everything unexplored from here is through this exit"] and right_a["action"] == {"go_to": "Room B"}
    world.current_place = "b"; b = world.options(per, "b", {}, 3, inputs=INPUTS)
    left_b = next(o for o in b if o["label"].startswith("exit LEFT"))
    right_b = next(o for o in b if o["label"].startswith("exit RIGHT"))
    assert left_b["label"] == "exit LEFT → Room A (used 1×)" and left_b["notes"] == []  # nothing unexplored that way: no suffix, no note
    assert right_b["label"] == "exit RIGHT → Room C (used 1×) (1 unexplored exit beyond)" and right_b["notes"] == ["everything unexplored from here is through this exit"]


def test_view_says_when_everything_unexplored_is_through_one_exit(tmp_path):
    mem = Memory(tmp_path); chain(mem)
    world = World(mem, 16); world.current_place = "a"
    assert "Everything unexplored from here is through: RIGHT" in world.view("", "a")
    world.current_place = "c"
    assert "Everything unexplored from here is through:" not in world.view("", "c")


def two_rooms(tmp_path, seen):
    """Room A's only untried edge is UP (its exit RIGHT leads to Room B, which has unseen edges). seen: A's seen cells."""
    mem = Memory(tmp_path)
    for pid, name in (("a", "Room A"), ("b", "Room B")):
        mem.upsert("places", {"id": pid, "name": name, "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    def cell(place, x, y, blocked, exit=None): mem.upsert("tiles", {"id": f"{place}@{x},{y}", "place": place, "x": x, "y": y, "floor": True, "blocked": blocked, "pushed": [], "exit": exit, "step": 1})
    cell("a", 0, 0, ["DOWN", "LEFT"], {"dir": "RIGHT", "to": "b", "arrive": [0, 0], "src": "observed"})
    cell("b", 0, 0, ["UP"], {"dir": "LEFT", "to": "a", "arrive": [0, 0], "src": "observed"})
    mem.upsert("seen", {"id": "a", "cells": seen, "step": 1})
    return mem


def test_untried_edge_into_a_seen_cell_is_not_unexplored_so_the_dead_end_fires(tmp_path):
    world = World(two_rooms(tmp_path, ["0,-1"]), 16); world.current_place = "a"
    assert world.beyond("a")["dead_end"] is True and world.beyond("a")["through"] == (0, 0)
    per = {"screen": "world", "text": [], "entities": [player()]}
    opts = world.options(per, "a", {}, 3, inputs=INPUTS)
    exit_row = next(o for o in opts if o["label"].startswith("exit RIGHT"))
    assert exit_row["notes"][0] == "everything unexplored from here is through this exit"
    explore = next(o for o in opts if o["tool"] == "explore")
    assert explore["label"] == "walk to an unexplored part of this place (0 spots lead anywhere unseen)" and explore["action"] == {"go_to": "unexplored"}
    assert "Everything unexplored from here is through: RIGHT" in world.view("", "a")


def test_explore_label_counts_only_untried_edges_into_unseen_cells(tmp_path):
    per = {"screen": "world", "text": [], "entities": [player()]}
    world = World(two_rooms(tmp_path / "x", ["0,-1"]), 16); world.current_place = "a"
    assert next(o for o in world.options(per, "a", {}, 3, inputs=INPUTS) if o["tool"] == "explore")["label"].endswith("(0 spots lead anywhere unseen)")
    world = World(two_rooms(tmp_path / "y", []), 16); world.current_place = "a"  # its one untried edge is now unseen
    assert next(o for o in world.options(per, "a", {}, 3, inputs=INPUTS) if o["tool"] == "explore")["label"] == "walk to an unexplored part of this place (1 spot leads somewhere unseen)"


def test_thing_row_lists_only_this_place_and_groups_numbered_names(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "observed"})
    for n in range(2): mem.upsert("entities", {"id": f"hall{n}", "name": f"hall thing {n}", "kind": "furniture", "place": "hall", "tile": [0, n], "status": "remembered", "last": 1, "seen": 1})
    mem.upsert("entities", {"id": "gonebox", "name": "old box", "kind": "furniture", "place": "room", "tile": [1, 1], "status": "gone", "last": 1, "seen": 1})
    names = ["vent", "vent 1", "vent 2", "cabinet#7", "cabinet#6", "cabinet", "PC", "desk", "shelf#4", "lamp 0", "lamp 1", "lamp 2"]
    world, per = setup(mem, [{"name": n, "kind": "furniture", "at": at(*SPOTS[i])} for i, n in enumerate(names)])
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    things = [o for o in opts if o["label"].startswith("look at or go to a thing here: ")]
    assert len(things) == 1 and len(opts) - 1 <= OPTION_CAP
    assert sorted(names_in(things[0])) == ["PC", "cabinet ×3", "desk", "lamp ×3", "shelf#4", "vent ×3"]
    assert not any(x in things[0]["label"] for x in ("vent 1", "hall thing", "old box"))


def test_grouped_label_resolves_like_its_name(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "vent", "kind": "furniture", "at": at(*SPOTS[0])}])
    assert world.resolve("vent", per, "room") is not None and world.resolve("vent ×3", per, "room") == world.resolve("vent", per, "room")


def test_called_actor_option_uses_called_label_and_resolves_case_insensitively(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "person", "kind": "npc", "at": at(*SPOTS[0])}])
    ident = world.entity_ids[1]
    mem.call(ident, "MOM", 41)

    fail = {fail_key("room", "interact", None, INPUTS, ref=f"thing:{ident}"): {"n": 1, "why": "blocked"}}
    options = world.options(per, "room", fail, 42, inputs=INPUTS)
    interact = next(option for option in options if option["tool"] == "interact")
    assert interact["label"] == "interact with MOM"
    assert interact["action"] == {"thing": 1, "verb": "interact"}
    assert interact["notes"] == ["tried here 1×, last: blocked"]
    assert world.resolve("mom", per, "room")["id"] == ident


def test_off_screen_exit_to_a_known_place_travels_through_its_verified_tile(tmp_path):
    mem, world, per = scene(tmp_path)
    off = {**per, "entities": [player()]}; world.before(off, "room", "room", (160, 144), 2)
    row = next(o for o in world.options(off, "room", {}, 2, inputs=INPUTS) if "Hall" in o["label"])
    assert row["action"] == {"go_to": "Hall"}  # not the door sprite's projected tile (m1_decide2: stairs walked into 16x)


def test_selected_exit_counter_follows_the_exit_through_renames_and_reordering(tmp_path):
    mem, world, per = scene(tmp_path)
    key = next(o for o in world.options(per, "room", {}, 120, inputs=INPUTS) if o["label"].startswith("exit door"))["intent_key"]
    fails = {key: {"n": 1, "why": "route blocked", "step": 5, "act": "approach exit: door"}}
    assert next(o for o in world.options(per, "room", fails, 6, inputs=INPUTS) if o["intent_key"] == key)["notes"] == ["tried here 1×, last: route blocked"]
    mem.upsert("places", {**mem.get("places", "hall"), "name": "Hallway"})  # the destination is renamed, the screen reordered
    reordered = {**per, "entities": [per["entities"][0], *reversed(per["entities"][1:])]}
    world.before(reordered, "room", "room", (160, 144), 6)
    again = next(o for o in world.options(reordered, "room", fails, 6, inputs=INPUTS) if o["intent_key"] == key)
    assert again["label"].startswith("exit door → Hallway") and again["notes"] == ["tried here 1×, last: route blocked"]
    assert not any("route blocked" in n for o in world.options(reordered, "room", fails, 6, inputs=INPUTS) if o["intent_key"] != key for n in o["notes"])


def test_selected_exit_travels_through_its_own_door_not_another_to_the_same_place(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, -2, 0, {"dir": "LEFT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    world, per = setup(mem, []); world.pos = (0, 0)
    row = next(o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("exit LEFT"))
    assert row["exit"] == {"tile": [-2, 0], "to": "hall"}
    assert world.plan(world.resolve("Hall", per, "room"), exit=row["exit"])[0]["exit_tile"] == (-2, 0)
