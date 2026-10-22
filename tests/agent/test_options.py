import json
from types import SimpleNamespace

from agent.loop import Loop
from agent.memory import Memory
from agent.space import mark_exit
from agent.world import World, fail_key
from tests.agent.fakes import GridEnv, StubModel

INPUTS = GridEnv().inputs()
BUTTONS = f"free buttons: {', '.join(INPUTS)}"


def test_uncrossed_exit_rows_offer_each_push_and_keep_tried_facts(tmp_path):
    from agent.space import DIRS, push_failed
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: doorway", "kind": "exit", "at": at(2, 0), "placement": "bottom wall"}])
    rows = [o for o in world.options(per, "room", {}, 2, inputs=INPUTS) if o["ref"] == "exit:2,0"]
    assert len(rows) == 4 and rows[0]["action"]["push"] == "DOWN" and all(o["state"] == "new" and o["notes"] == ["exit, never crossed"] for o in rows)
    assert all("(bottom wall)" in o["label"] and f"then press {o['action']['push']}: " in o["label"] and o["intent_key"].endswith(f">{o['action']['push']}") for o in rows)
    push_failed(mem, "room", (2, 0), "LEFT", 3)
    row = mem.get("tiles", "room@2,0"); mem.upsert("tiles", {**row, "blocked": ["RIGHT"]})
    rows = [o for o in world.options(per, "room", {}, 4, inputs=INPUTS) if o["ref"] == "exit:2,0"]
    assert len(rows) == 4 and [o["state"] for o in rows] == ["new", "new", "failed", "failed"]
    assert next(o for o in rows if o["action"]["push"] == "LEFT")["notes"][1] == "pressed LEFT: moved, no exit"
    assert next(o for o in rows if o["action"]["push"] == "RIGHT")["notes"][1] == "pressed RIGHT: blocked"
    for direction in DIRS: push_failed(mem, "room", (2, 0), direction, 5)
    rows = [o for o in world.options(per, "room", {}, 6, inputs=INPUTS) if o["ref"] == "exit:2,0"]
    assert len(rows) == 1 and rows[0]["state"] == "failed" and rows[0]["label"] == "go to doorway, 2 right" and rows[0]["notes"][0].startswith("exit failed: blocked ")


def test_uncrossed_exit_direction_facts_come_from_tile_memory(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: doorway", "kind": "exit", "at": at(2, 2), "placement": "bottom wall"}])
    tile(mem, 3, 2, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "inferred"})
    rows = {o["action"]["push"]: o for o in world.options(per, "room", {}, 2, inputs=INPUTS) if o["ref"] == "exit:2,2"}
    assert rows["DOWN"]["label"].endswith(": toward the bottom wall, onto ground not yet seen")
    assert rows["UP"]["label"].endswith(": back onto floor you walked")
    assert rows["RIGHT"]["label"].endswith(": onto a known exit to Hall")
    assert all(o["notes"] == ["exit, never crossed"] for o in rows.values())


def test_explicit_push_controls_screen_and_remembered_exit_routes(tmp_path):
    mem = Memory(tmp_path); room(mem)
    entity = {"name": "exit: doorway", "kind": "exit", "at": at(2, 0), "placement": "left wall"}
    world, per = setup(mem, [entity]); loop = Loop(GridEnv(), StubModel([]), mem, "grid"); loop.world = world; loop.tile_px = 16
    _, route, refused = loop._approach_route("room", entity, per["entities"][0], lambda _: (2, 0), "", push="DOWN")
    assert not refused and route["push"] == "DOWN"
    target = world.resolve("exit:2,0", {"screen": "world", "entities": []}, "room")
    assert world.plan(target, push="DOWN")[-1]["push"] == "DOWN"


def at(dx, dy):  # GridEnv(wander=True) geometry: the player's centre is (88, 72), tiles are 16 px
    return [(88 + 16 * dx) / 160, (72 + 16 * dy) / 144]


def player(): return {"name": "player", "kind": "player", "at": at(0, 0)}


def room(mem):
    mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "first": 1, "last": 1})
    for pid, name in (("hall", "Hall"), ("cave", "Cave")): mem.upsert("places", {"id": pid, "name": name, "screen": "world", "state": "place", "first": 1, "last": 1})
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
    blocked = mem.get("tiles", "room@0,2")
    mem.upsert("tiles", {**blocked, "blocked": ["RIGHT"]})
    world, per = setup(mem, [{"name": "exit: door", "kind": "exit", "at": at(2, 0)}, {"name": "npc", "kind": "npc", "at": at(-1, 0)},
                             {"name": "exit: fake", "kind": "exit", "at": at(1, 2)}])
    i = ids(world, per)
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
    assert [o["ref"] for o in opts[:-1]] == [None, None, "modal:advance", "modal:cancel"]
    assert [o["intent_key"] for o in opts[:-1]] == ["room|select|fight", "room|select|bag", "room|advance|modal:advance", "room|cancel|modal:cancel"]
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


def test_leads_to_history_note_on_thing_option_from_source_and_destination(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "building", "kind": "building", "at": at(1, 0)}])
    ident = ids(world, per)["building"]
    mem.upsert("relations", {"id": "building-hall", "s": ident, "p": "leads_to", "o": "hall", "src": "observed", "sup": 3, "con": 1, "ev": [10, 20, 30], "state": "live", "step": 30})
    mem.upsert("relations", {"id": "building-hall-other", "s": ident, "p": "seen_at", "o": "hall", "src": "observed", "sup": 4, "con": 0, "ev": [11, 21, 31, 32], "state": "live", "step": 32})

    def building_row(place):
        return next(row for row in world.options(per, place, {}, 31, inputs=INPUTS) if row["ref"] == f"thing:{ident}")

    assert "leads to Hall 3×" in building_row("room")["notes"]
    assert "leads to Hall 3×" in building_row("hall")["notes"]


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
    tools = [o["tool"] for o in opts]
    assert tools == ["go_to", "explore", "explore", "explore", "explore", "go_to", "go_to", "approach", "interact", "approach", "buttons"]
    assert [o["state"] for o in opts] == ["new"] * 7 + ["worked"] * 2 + ["failed", "new"]  # state groups: new, worked, failed; free last
    assert opts[0]["label"].startswith("go to UP,") and opts[7]["label"].startswith("go through door,")
    assert opts[9]["label"].startswith("go to fake,")
    assert opts[8]["label"].startswith("talk to npc,") and opts[8]["notes"] == ['talked step 91: "Hi"']
    assert opts[9]["notes"][0].startswith("exit failed: blocked ")
    assert opts[0]["action"] == {"go_to": "place:cave"} and opts[7]["action"] == {"thing": 1, "verb": "approach"}
    assert all(o["label"].startswith("explore ") and o["action"]["dir"] for o in opts[1:5])
    assert opts[-1]["action"] is None and all(o["ref"] for o in opts[:-1])


def test_on_screen_exit_that_is_also_a_tile_exit_appears_once(tmp_path):
    mem, world, per = scene(tmp_path)
    opts = world.options(per, "room", {}, 120, inputs=INPUTS)
    assert sum((o["ref"] or "").startswith("exit:") and "door" in o["label"] for o in opts) == 1


SPOTS = [(x, y) for y in (-1, 1, 2) for x in (-2, -1, 0, 1, 2)] + [(-2, -2)]  # room tiles clear of the exits (2,0), (0,-2) and the npc (-1,0)


def test_each_thing_has_an_action_duplicate_pair_groups_and_actor_has_one_row(tmp_path):
    items = [{"name": f"thing {n}", "kind": "object", "at": at(*SPOTS[n])} for n in range(8)]
    items += [{"name": "crate", "kind": "object", "at": at(1, 1)}, {"name": "crate", "kind": "object", "at": at(2, 1)},
              {"name": "npc", "kind": "npc", "at": at(-1, 0)}]
    world, per = setup(Memory(tmp_path), items)
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    labels = [o["label"] for o in opts]
    crate = [o for o in opts if "crate" in o["label"]]
    actors = [o for o in opts if o["label"].startswith("talk to npc")]  # no anchor in this scene: no tile, so no distance is printed
    assert len(crate) == 1 and "nearest of 2" in crate[0]["label"] and crate[0]["action"] == {"thing": 9, "verb": "interact"}
    assert len(actors) == 1 and actors[0]["tool"] == "interact" and actors[0]["action"] == {"thing": 11, "verb": "interact"}
    assert sum(label.startswith("check thing ") for label in labels) == 8
    assert opts[-1]["label"] == BUTTONS
    assert all(row["action"] is not None and "ref" in row for row in opts[:-1])
    assert all("state" in row for row in opts)


def test_on_screen_thing_on_player_tile_has_no_row_but_adjacent_thing_stays_offered(tmp_path):
    world, per = setup(Memory(tmp_path), [
        {"name": "underfoot", "kind": "object", "at": at(0, 0)},
        {"name": "nearby", "kind": "object", "at": at(1, 0)},
    ])
    entity_ids = ids(world, per)

    rows = world.options(per, "room", {}, 5, inputs=INPUTS)
    refs = {row["ref"] for row in rows}

    assert f"thing:{entity_ids['underfoot']}" not in refs
    assert f"thing:{entity_ids['nearby']}" in refs


def test_known_places_each_have_a_concrete_route_row(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)
    world, per = setup(mem, [])
    rows = [o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["ref"] == "place:hall"]
    assert len(rows) == 1 and rows[0]["label"].startswith("go to Hall, via ") and rows[0]["action"] == {"go_to": "place:hall"}


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
    assert opts[-1]["label"] == BUTTONS and opts[-1]["notes"] == ["blocked directions: none", "failed here: UP A x4, DOWN x2"]


def test_fail_notes_use_the_loop_failure_key(tmp_path):
    mem, world, per = scene(tmp_path)
    loop = Loop(GridEnv(), StubModel([]), Memory(tmp_path / "loop"), "grid")
    i = ids(world, per)
    assert loop._fail_key("room", "approach", None, ref=f"thing:{i['npc']}") == fail_key("room", "approach", None, INPUTS, ref=f"thing:{i['npc']}")
    base = {o["label"]: o for o in world.options(per, "room", {}, 120, inputs=INPUTS)}
    fails = {fail_key("room", "interact", "npc", INPUTS, ref=f"thing:{i['npc']}"): {"n": 2, "why": "wall", "step": 50, "act": "interact npc"}}
    by = {o["label"]: o for o in world.options(per, "room", fails, 120, inputs=INPUTS)}
    actor = next(o for o in by.values() if o["label"].startswith("talk to npc,"))
    assert actor["notes"] == ["wall 2× (last step 50)", 'talked step 91: "Hi"']
    fake = next(o for o in by.values() if o["label"].startswith("go to fake,"))
    assert fake["state"] == "failed" and fake["notes"][0].startswith("exit failed: blocked ")


def walk(world, src, dst, direction, step):  # one crossing as the loop reports it: a scene cut after a single press
    skill = SimpleNamespace(moves=[{"dir": direction}], scroll=None)
    world.current_place = src; world.prepare(skill, src, True, step)
    assert world.mem.match_place({"screen": "world", "entities": []}, step, prev=src, cut=True, exit=(*world._prepared[4], direction)) == dst  # the loop identifies the place first; the exit hands over the arrival tile
    world.after(skill, src, dst, True, step); world.current_place = dst


def test_two_room_exit_counts_only_departure_crossings(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mark_exit(mem, "room", (2, 0), "RIGHT", "hall", (0, 0), 1)  # the first crossing writes both exits, as Loop does
    world, per = setup(mem, []); world.pos = (2, 0)  # standing on the exit tile
    walk(world, "room", "hall", "RIGHT", 1); walk(world, "hall", "room", "LEFT", 2); walk(world, "room", "hall", "RIGHT", 3)
    room_labels = [o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["tool"] != "buttons"]
    hall_labels = [o["label"] for o in world.options(per, "hall", {}, 3, inputs=INPUTS) if o["tool"] != "buttons"]
    assert any(label.startswith("go through RIGHT,") for label in room_labels) and any(label.startswith("go through LEFT,") for label in hall_labels)
    assert any("crossed 2× → Hall" in note for o in world.options(per, "room", {}, 3, inputs=INPUTS) for note in o["notes"])


def test_perceived_exit_never_crossed_is_unverified_then_refuted_after_two_failed_tries(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: wall", "kind": "exit", "at": at(1, -2)}])  # a floor tile the agent never crossed
    assert any(o["label"].startswith("go to wall,") and "exit, never crossed" in o["notes"] for o in world.options(per, "room", {}, 3, inputs=INPUTS))
    for x, direction in ((0, "RIGHT"), (2, "LEFT")):
        row = mem.get("tiles", f"room@{x},-2")
        mem.upsert("tiles", {**row, "blocked": [direction]})
    assert any(o["label"].startswith("go to wall,") and o["state"] == "failed" for o in world.options(per, "room", {}, 4, inputs=INPUTS))


def test_inferred_exit_next_to_a_verified_one_is_listed_once(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 2, 1, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "inferred"})
    world, per = setup(mem, [])
    assert sum(o["label"].startswith("go through") for o in world.options(per, "room", {}, 3, inputs=INPUTS)) == 1


def chain(mem):
    """Rooms A - B - C in a line. Every floor edge is blocked or an exit, so no place has untried edges of its own. C has an unused exit (north)."""
    for pid, name in (("a", "Room A"), ("b", "Room B"), ("c", "Room C")):
        mem.upsert("places", {"id": pid, "name": name, "screen": "world", "state": "place", "first": 1, "last": 1})
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
    right_a = next(o for o in a if o["label"].startswith("go through RIGHT,"))
    assert right_a["notes"][0] == "everything unexplored from here is through this exit"
    assert any("crossed 1× → Room B" in note and "1 unexplored exit beyond" in note for note in right_a["notes"])
    assert right_a["action"] == {"go_to": "place:b"}
    world.current_place = "b"; b = world.options(per, "b", {}, 3, inputs=INPUTS)
    left_b = next(o for o in b if o["label"].startswith("go through LEFT,"))
    right_b = next(o for o in b if o["label"].startswith("go through RIGHT,"))
    assert left_b["notes"][0].startswith("crossed 1× → Room A")
    assert right_b["notes"][0] == "everything unexplored from here is through this exit"


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
        mem.upsert("places", {"id": pid, "name": name, "screen": "world", "state": "place", "first": 1, "last": 1})
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
    exit_row = next(o for o in opts if o["label"].startswith("go through RIGHT,"))
    assert exit_row["notes"][0] == "everything unexplored from here is through this exit"
    assert not any(o["tool"] == "explore" for o in opts)  # all unseen routes are blocked, so only the exit remains
    assert "Everything unexplored from here is through: RIGHT" in world.view("", "a")


def test_each_explore_direction_has_a_ref_and_concrete_action(tmp_path, monkeypatch):
    world = World(two_rooms(tmp_path, []), 16); world.current_place = "a"
    world.explore_dirs = lambda place: {"UP": {"dirs": ["UP", "UP"], "to": [0, -2]}, "RIGHT": {"dirs": ["RIGHT"], "to": [2, 0]}}
    per = {"screen": "world", "text": [], "entities": [player()]}
    rows = [o for o in world.options(per, "a", {}, 3, inputs=INPUTS) if o["tool"] == "explore"]
    assert [o["label"] for o in rows] == ["explore UP, 2 steps to unseen ground", "explore RIGHT, 1 steps to unseen ground"]
    assert [o["ref"] for o in rows] == ["explore:a:up", "explore:a:right"]
    assert [o["action"] for o in rows] == [{"go_to": "unexplored", "dir": "UP"}, {"go_to": "unexplored", "dir": "RIGHT"}]


def test_remembered_things_have_individual_rows_and_gone_things_are_hidden(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, 0, -2, {"dir": "UP", "to": "cave", "arrive": [0, 0], "src": "observed"})
    for n in range(2): mem.upsert("entities", {"id": f"hall{n}", "name": f"hall thing {n}", "kind": "furniture", "place": "hall", "tile": [0, n], "status": "remembered", "last": 1, "seen": 1})
    mem.upsert("entities", {"id": "gonebox", "name": "old box", "kind": "furniture", "place": "room", "tile": [1, 1], "status": "gone", "last": 1, "seen": 1})
    names = ["vent", "cabinet", "PC", "desk", "shelf#4", "lamp"]
    world, per = setup(mem, [{"name": n, "kind": "furniture", "at": at(*SPOTS[i])} for i, n in enumerate(names)])
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    things = [o for o in opts if o["tool"] == "interact"]
    assert len(things) == 6 and {o["label"].split(",")[0].removeprefix("check ") for o in things} == set(names)
    assert all(o["action"]["verb"] == "interact" and o["ref"].startswith("thing:") for o in things)


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
    assert interact["label"].startswith("talk to MOM,")
    assert interact["action"] == {"thing": 1, "verb": "interact"}
    assert interact["notes"] == ["blocked 1× (last step 0)"]
    assert world.resolve("mom", per, "room")["id"] == ident


def test_off_screen_exit_to_a_known_place_travels_through_its_verified_tile(tmp_path):
    mem, world, per = scene(tmp_path)
    off = {**per, "entities": [player()]}; world.before(off, "room", "room", (160, 144), 2)
    row = next(o for o in world.options(off, "room", {}, 2, inputs=INPUTS) if o["ref"] == "place:hall")
    assert row["action"] == {"go_to": "place:hall"}  # not the door sprite's projected tile (m1_decide2: stairs walked into 16x)


def test_selected_exit_counter_follows_the_exit_through_renames_and_reordering(tmp_path):
    mem, world, per = scene(tmp_path)
    key = next(o for o in world.options(per, "room", {}, 120, inputs=INPUTS) if o["label"].startswith("go through door"))["intent_key"]
    fails = {key: {"n": 1, "why": "route blocked", "step": 5, "act": "approach exit: door"}}
    assert next(o for o in world.options(per, "room", fails, 6, inputs=INPUTS) if o["intent_key"] == key)["notes"][1] == "route blocked 1× (last step 5)"
    mem.upsert("places", {**mem.get("places", "hall"), "name": "Hallway"})  # the destination is renamed, the screen reordered
    reordered = {**per, "entities": [per["entities"][0], *reversed(per["entities"][1:])]}
    world.before(reordered, "room", "room", (160, 144), 6)
    again = next(o for o in world.options(reordered, "room", fails, 6, inputs=INPUTS) if o["intent_key"] == key)
    assert "door" in again["label"] and "Hallway" in again["notes"][0] and again["notes"][1] == "route blocked 1× (last step 5)"
    assert not any("route blocked" in n for o in world.options(reordered, "room", fails, 6, inputs=INPUTS) if o["intent_key"] != key for n in o["notes"])


def test_selected_exit_travels_through_its_own_door_not_another_to_the_same_place(tmp_path):
    mem = Memory(tmp_path); room(mem)
    tile(mem, 2, 0, {"dir": "RIGHT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    tile(mem, -2, 0, {"dir": "LEFT", "to": "hall", "arrive": [0, 0], "src": "observed"})
    world, per = setup(mem, []); world.pos = (0, 0)
    row = next(o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["label"].startswith("go through LEFT,"))
    assert row["exit"] == {"tile": [-2, 0], "to": "hall"}
    assert world.plan(world.resolve("Hall", per, "room"), exit=row["exit"])[0]["exit_tile"] == (-2, 0)


def test_exit_on_the_players_own_tile_is_crossed_by_its_verified_push_or_not_offered(tmp_path):
    # m1_hf_checkpoint_1 steps 128-130, 136: a door mat under the player was offered as approach, which refuses "target is the player"
    mat = [{"name": "exit: door mat", "kind": "exit", "at": at(0, 0)}]
    mem = Memory(tmp_path / "unverified"); room(mem)
    world, per = setup(mem, mat); world.pos = (0, 0)
    assert not any(o["tool"] == "approach" and "door mat" in o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS))
    mem = Memory(tmp_path / "verified"); room(mem); tile(mem, 0, 0, {"dir": "DOWN", "to": "hall", "arrive": [0, 0], "src": "observed"})
    world, per = setup(mem, mat); world.pos = (0, 0)
    row = next(o for o in world.options(per, "room", {}, 3, inputs=INPUTS) if o["ref"] == "exit:0,0")
    assert row["action"] == {"go_to": "place:hall"} and row["exit"] == {"tile": [0, 0], "to": "hall"}


def test_exit_with_no_tile_perceived_on_the_player_is_not_offered_as_approach(tmp_path):
    # m1_hf_checkpoint_2 steps 128-130: the door mat had no tile, so the tile test missed and approach refused "target is the player"
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "exit: door mat", "kind": "exit", "at": at(0, 0)}]); world.pos = (0, 0)
    world.entity_tiles = {}; [mem.upsert("entities", {**e, "tile": None}) for e in mem.rows("entities")]  # perceived, never projected to a tile
    assert not any(o["tool"] == "approach" and "door mat" in o["label"] for o in world.options(per, "room", {}, 3, inputs=INPUTS))

def test_menu_check_counts_rows_and_flags_duplicate_labels(tmp_path, monkeypatch):
    import scripts.check_tracks as ct
    run = tmp_path / "runs" / "r"; run.mkdir(parents=True)
    trace = [{"step": 1, "place": "p", "screen": "world", "choice": {"intent": "p|x"}, "verdict": "failed"},
             {"step": 2, "place": "p", "screen": "world", "choice": None, "verdict": "hit"},
             {"step": 3, "place": "p", "screen": "world", "choice": None, "verdict": "hit"}]
    (run / "trace.jsonl").write_text("\n".join(json.dumps(r) for r in trace), encoding="utf8")
    seen = []
    class World:
        pos, mem = None, Memory(tmp_path / "mem")
        def _seen_at(self, place): return None
        def before(self, *args): pass
        def label(self, ref, place=None): return "x"
        def place_label(self, place): return "Room"
        def here(self, place): return self.place_label(place)
        def options(self, per, place, fails, step, inputs=(), targets=()):
            seen.append((step, {k: dict(v) for k, v in fails.items()}))
            talk = {"tool": "approach", "label": "talk to Mom", "action": {"go_to": "mom"}, "state": "new"}
            return [dict(talk, n=1), dict(talk, n=2), {"n": 3, "tool": "buttons", "label": "free", "action": {"buttons": ["A"]}, "state": "new"}]
    def fake_replay(run, steps=None, first=1, baseline=False, callback=None):
        world, env = World(), SimpleNamespace(inputs=lambda: ("A",))
        for row in trace: callback(row["step"], row, {"screen": "world", "text": [], "entities": []}, {"x": 0, "y": 0}, env, world)
        return world, {}, [], first, 3
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ct, "replay", fake_replay)
    monkeypatch.setattr(ct, "notebook_at", lambda run, step: SimpleNamespace(render_focus=lambda label: "Goal: (none)", data={"quests": []}))
    out = ct.menu("r")
    assert [s["rows"] for s in out["steps"]] == [3, 3, 3]
    assert out["steps"][0]["dups"] == ["talk to Mom", "talk to Mom"]
    assert seen[1][1] == {"p|x": {"n": 1, "why": "verdict failed", "step": 1}}
    assert out["exit"] == 1


def test_menu_replay_counts_only_repeated_novel_blocks_and_success_clears(tmp_path, monkeypatch):
    import scripts.check_tracks as ct
    run = tmp_path / "runs" / "repeat"; run.mkdir(parents=True)
    statuses = ["blocked", "blocked", "blocked", "done", "blocked", "failed", "done"]
    trace = [{"step": s, "place": "p", "screen": "world", "choice": {"intent": "p|x"},
              "verdict": "progress", "execution": status, "skill": {"status": status, "reason": "route blocked"}}
             for s, status in enumerate(statuses, 1)]
    (run / "trace.jsonl").write_text("\n".join(json.dumps(r) for r in trace), encoding="utf8")
    seen = []
    class ReplayWorld:
        mem = Memory(tmp_path / "mem")
        def _seen_at(self, place): return None
        def before(self, *args): pass
        def label(self, ref, place=None): return "Room"
        def here(self, place): return "Room"
        def options(self, per, place, fails, step, inputs=(), targets=()):
            seen.append(dict(fails.get("p|x", {})))
            return [{"n": 1, "tool": "interact", "label": "check box", "action": {"thing": 0, "verb": "interact"}, "state": "failed" if fails else "new"}]
    def replay(run, steps=None, first=1, baseline=False, callback=None):
        world, env = ReplayWorld(), SimpleNamespace(inputs=lambda: ("A",))
        for row in trace: callback(row["step"], row, {"screen": "world", "text": [], "entities": []}, {"x": 0, "y": 0}, env, world)
        return world, {}, [], 1, len(trace)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ct, "replay", replay)
    monkeypatch.setattr(ct, "notebook_at", lambda *a: SimpleNamespace(render_focus=lambda label: "Goal: inspect", data={"quests": []}))
    assert ct.menu("repeat")["exit"] == 0
    assert [f.get("n", 0) for f in seen] == [0, 0, 1, 2, 0, 0, 1]
    assert seen[2]["why"] == "route blocked"


def test_tied_remembered_things_sort_without_comparing_rows(tmp_path):  # regression: equal sort keys used to compare row dicts (TypeError)
    mem = Memory(tmp_path); room(mem)
    for n in range(2): mem.upsert("entities", {"id": f"ghost{n}", "name": "crate", "kind": "person", "place": "room", "tile": [1, 2], "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [])
    opts = world.options(per, "room", {}, 9, inputs=INPUTS)
    assert [o["n"] for o in opts] == list(range(1, len(opts) + 1))
    assert sum("crate" in o["label"] for o in opts) == 1


def test_off_screen_things_with_one_name_are_one_row_to_the_nearest(tmp_path):
    mem = Memory(tmp_path); room(mem)
    for n, tile in enumerate(([2, 2], [-2, 1], [0, 2])): mem.upsert("entities", {"id": f"post{n}", "name": "post", "kind": "person", "place": "room", "tile": tile, "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [])
    rows = [o for o in world.options(per, "room", {}, 9, inputs=INPUTS) if "post" in o["label"]]
    assert len(rows) == 1 and "(nearest of 3)" in rows[0]["label"] and rows[0]["action"] == {"go_to": "thing:post2"}


def test_failed_rows_follow_new_rows_and_free_buttons_stay_last(tmp_path):
    mem = Memory(tmp_path); room(mem)
    world, per = setup(mem, [{"name": "vent", "kind": "furniture", "at": at(*SPOTS[0])}, {"name": "cabinet", "kind": "furniture", "at": at(*SPOTS[1])}])
    ref = f"thing:{ids(world, per)['cabinet']}"
    fails = {fail_key("room", "interact", "cabinet", INPUTS, ref): {"why": "nothing", "n": 2, "step": 5}}
    opts = world.options(per, "room", fails, 9, inputs=INPUTS)
    ranks = [{"new": 0, "worked": 1, "failed": 2}[o["state"]] for o in opts[:-1]]
    assert "failed" in [o["state"] for o in opts] and ranks == sorted(ranks)
    assert opts[-1]["tool"] == "buttons" and [o["n"] for o in opts] == list(range(1, len(opts) + 1))

def test_off_screen_static_thing_with_no_history_has_no_row(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("entities", {"id": "crate1", "name": "crate", "kind": "furniture", "place": "room", "tile": [20, 20], "status": "remembered", "last": 1, "seen": 1})  # outside the 10×9 viewport
    world, per = setup(mem, [])
    assert not [o for o in world.options(per, "room", {}, 9, inputs=INPUTS) if "crate" in o["label"]]


def test_in_view_remembered_static_uses_the_seen_check_option(tmp_path):
    from copy import deepcopy

    mem = Memory(tmp_path); room(mem)
    world, seen_per = setup(mem, [{"name": "shelf", "kind": "furniture", "at": at(1, 0)}])
    ident = world.entity_ids[1]
    seen = next(o for o in world.options(seen_per, "room", {}, 2, inputs=INPUTS) if o.get("ref") == f"thing:{ident}")
    missing_per = {"screen": "world", "text": [], "entities": [player()]}
    world.before(missing_per, "room", "room", (160, 144), 3)
    ids = dict(world.entity_ids); before = deepcopy(missing_per)
    world.history = lambda ref, place=None: {"checked": "nothing"} if ref == f"thing:{ident}" else {}
    rows = [o for o in world.options(missing_per, "room", {}, 3, inputs=INPUTS) if o.get("ref") == f"thing:{ident}"]

    assert len(rows) == 1
    remembered = rows[0]
    assert remembered["label"] == seen["label"] == "check shelf, 1 right"
    assert remembered["action"] == {"thing": f"thing:{ident}", "verb": "interact"}
    assert missing_per == before and world.entity_ids == ids


def test_off_screen_static_thing_with_history_or_failure_keeps_its_row(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("entities", {"id": "crate2", "name": "crate", "kind": "furniture", "place": "room", "tile": [2, 2], "status": "remembered", "last": 1, "seen": 1})
    mem.upsert("entities", {"id": "crate3", "name": "drum", "kind": "furniture", "place": "room", "tile": [1, 2], "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [])
    world.history = lambda ref, place=None: {"checked": "nothing"} if ref == "thing:crate2" else {}
    assert [o for o in world.options(per, "room", {}, 9, inputs=INPUTS) if "crate" in o["label"]]
    failed = {fail_key("room", "approach", "thing:crate3", INPUTS, "thing:crate3"): {"n": 1, "why": "blocked", "step": 1, "act": "approach"}}
    assert [o for o in world.options(per, "room", failed, 9, inputs=INPUTS) if "drum" in o["label"]]


def test_off_screen_actor_with_no_history_keeps_its_row(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("entities", {"id": "mom1", "name": "mom", "kind": "person", "place": "room", "tile": [2, 2], "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [])
    assert [o for o in world.options(per, "room", {}, 9, inputs=INPUTS) if "mom" in o["label"]]


def test_off_screen_static_thing_that_is_the_quest_target_keeps_its_row(tmp_path):
    mem = Memory(tmp_path); room(mem)
    mem.upsert("entities", {"id": "crate4", "name": "crate", "kind": "furniture", "place": "room", "tile": [2, 2], "status": "remembered", "last": 1, "seen": 1})
    world, per = setup(mem, [])
    assert [o for o in world.options(per, "room", {}, 9, inputs=INPUTS, targets=("thing:crate4",)) if "crate" in o["label"]]


def test_live_menu_charges_every_request_and_refuses_beyond_the_cap():
    import pytest
    import scripts.check_tracks as ct
    from scripts.run_agent import CallCap
    class Stub:  # like Ollama: every ask that misses the cache posts once
        def _post(self, payload): return {"i": 1}
        def ask(self, system, user, images=(), schema=None, *, role=""): return SimpleNamespace(data=self._post({"role": role}))
    charged = []
    model = ct.capped(Stub(), 2, charge=lambda: charged.append(1))
    model.ask("s", "u", role="act"); model.ask("s", "u retry", role="act")  # a retry is a request too
    with pytest.raises(CallCap): model.ask("s", "u", role="act")
    assert model.requests == 2 and len(charged) == 2
