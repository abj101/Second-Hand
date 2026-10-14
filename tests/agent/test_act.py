import re
from types import SimpleNamespace

from agent.act import REPLY, SYSTEM, act

BUTTONS = ["UP", "DOWN", "LEFT", "RIGHT", "A", "B", "START", "SELECT"]
PERCEPTION = {"screen": "a room", "text": ["Go north"], "entities": [{"name": "door", "kind": "exit", "at": [0.5, 0.2]}]}


class Capture:
    def __init__(self, data): self.reply, self.args = SimpleNamespace(data=data), None
    def ask(self, *args, **kwargs): self.args = (args, kwargs); return self.reply


def run(data, **kwargs):
    model = Capture(data)
    return act(model, None, PERCEPTION, kwargs.get("notebook", ""), "none", kwargs.get("flags", []), "A: press", {}, BUTTONS, kwargs.get("places", [])), model


def test_option_reply_parsed():
    op = {"op": "set", "section": "goals", "key": "g1", "value": {"text": "find door"}}
    out, _ = run({"place": "  Hall  ", "notebook": [op, "junk"], "action": {"thing": 0, "verb": "approach"}, "text": None, "expect": "place_changes", "why": "the door leads on"})
    assert (out.place, out.ops, out.action, out.expect, out.why) == ("Hall", [op], {"thing": 0, "verb": "approach"}, "place_changes", "the door leads on")


def test_go_to_buttons_and_garbage():
    def action(raw): return run({"action": raw, "expect": "bogus", "notebook": []})[0].action
    assert action({"go_to": "door"}) == {"go_to": "door"}
    assert action({"buttons": ["up", "a"]}) == {"buttons": ["UP", "A"]}
    assert len(action({"buttons": ["UP"] * 10})["buttons"]) == 8
    assert action({"buttons": ["UP", "ZAP"]}) is None
    assert action({"thing": 9, "verb": "approach"}) is None
    assert action({"thing": True, "verb": "approach"}) is None
    assert action({"thing": 0, "verb": "advance"}) is None
    assert action({"i": 0}) is None
    assert action({"verb": "advance"}) == {"verb": "advance"}
    out, _ = run({"expect": "bogus"})
    assert out.expect == "screen_changes" and out.action is None and out.ops == []
    out = act(Capture(None), None, PERCEPTION, "", "none", [], "", {}, BUTTONS)
    assert (out.action, out.ops, out.expect) == (None, [], "screen_changes")


def test_prompt_has_numbered_things_screen_rules_and_no_game_words():
    _, model = run({}, notebook="Goals: reach the hall", flags=["stuck twice"])
    user = model.args[0][1]
    assert "Goals: reach the hall" in user and "stuck twice" in user and "0: door (exit) at [0.5, 0.2]" in user
    assert "Options:" not in user and "use advance, select or cancel" in REPLY
    assert "with the text in \"text\"" in REPLY and "suggested names" in REPLY and "Never pick letter buttons one by one" in REPLY
    assert not re.search(r"pok[eé]mon|\bred\b|\bblue\b|\brival\b", (SYSTEM + REPLY + user).casefold())
    assert "Known places: none" in user
    assert '"op":"focus","status":"done"|"blocked"' in REPLY and '"section":"hints"|"lessons"' in REPLY and '"goals"' not in REPLY
    assert "Place values" not in REPLY and '"places"' not in REPLY


def test_prompt_includes_at_most_twelve_known_places():
    _, model = run({}, places=[f"P{i}" for i in range(13)])
    assert "Known places: " + ", ".join(f"P{i}" for i in range(12)) in model.args[0][1]


def test_type_shapes_the_model_actually_sends():
    for raw, top in (({"type": "RED"}, None), ({"verb": "type", "text": "RED"}, None), ({"verb": "type"}, "RED")):
        out, _ = run({"action": raw, "text": top, "notebook": [], "expect": "screen_changes"})
        assert out.action == {"verb": "type"} and out.text == "RED"


OPTIONS = [
    {"n": 1, "tool": "approach", "label": "approach door", "action": {"thing": 0, "verb": "approach"}, "notes": []},
    {"n": 2, "tool": "go_to", "label": "go to Hall", "action": {"go_to": "Hall"}, "notes": ["leads to Hall (used 2×, last step 5)", "refuted: wall"]},
    {"n": 3, "tool": "buttons", "label": "free input (any of: UP, A)", "action": None, "notes": []},
]


def run_with(data, options=OPTIONS):
    model = Capture(data)
    return act(model, None, PERCEPTION, "", "none", [], "A: press", {}, BUTTONS, options=options), model


def test_option_number_maps_to_its_action_and_sets_option():
    out, _ = run_with({"action": {"i": 2}, "notebook": [], "expect": "place_changes"})
    assert (out.action, out.option) == ({"go_to": "Hall"}, 2)
    out, _ = run_with({"action": {"i": 1}, "notebook": []})
    assert (out.action, out.option) == ({"thing": 0, "verb": "approach"}, 1)
    out.action["thing"] = 9  # a copy: the option list is not changed
    assert OPTIONS[0]["action"] == {"thing": 0, "verb": "approach"}


def test_out_of_range_and_bool_option_numbers_are_none():
    for raw in ({"i": 0}, {"i": 4}, {"i": True}, {"i": "2"}):
        out, _ = run_with({"action": raw, "notebook": []})
        assert (out.action, out.option) == (None, None), raw


def test_option_without_action_falls_through_to_free_shapes():
    out, _ = run_with({"action": {"i": 3, "buttons": ["up", "a"]}, "notebook": []})
    assert (out.action, out.option) == ({"buttons": ["UP", "A"]}, None)
    out, _ = run_with({"action": {"i": 3}, "notebook": []})
    assert (out.action, out.option) == (None, None)


def test_free_shapes_still_parse_with_options_present():
    assert run_with({"action": {"go_to": "door"}, "notebook": []})[0].action == {"go_to": "door"}
    assert run_with({"action": {"thing": 0, "verb": "approach"}, "notebook": []})[0].action == {"thing": 0, "verb": "approach"}
    assert run_with({"action": {"buttons": ["UP"]}, "notebook": []})[0].action == {"buttons": ["UP"]}
    assert run_with({"action": {"verb": "advance"}, "notebook": []})[0].action == {"verb": "advance"}


def test_prompt_lists_options_with_notes_and_no_things_list():
    out, model = run_with({"action": {"i": 1}, "notebook": []})
    user, system = model.args[0][1], model.args[0][0]
    assert "Options:\n1. approach door\n2. go to Hall — leads to Hall (used 2×, last step 5); refuted: wall\n3. buttons free input (any of: UP, A)" in user
    assert "Things on screen:" not in user and "0: door (exit)" not in user
    assert '"action": {"i": n} to pick one option by number (preferred)' in user and "'info'" not in user
    assert "The option notes are what already happened here" in system and "free buttons option" in system and "The option notes" not in SYSTEM


def test_nineteen_thing_room_prompt_fits_the_budget(tmp_path):
    from agent.memory import Memory
    from agent.world import OPTION_CAP, World
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "room", "name": "Room", "screen": "world", "state": "place", "first": 1, "last": 1, "edges": []})
    for x in range(-2, 3):
        for y in range(-2, 3): mem.upsert("tiles", {"id": f"room@{x},{y}", "place": "room", "x": x, "y": y, "floor": True, "blocked": [], "pushed": [], "exit": None, "step": 1})
    pos = lambda x, y: [(88 + 16 * x) / 160, (72 + 16 * y) / 144]  # GridEnv geometry: player centre (88, 72), tiles 16 px
    spots = [(x, y) for y in range(-2, 3) for x in range(-2, 3) if (x, y) not in ((0, 0), (2, 0), (-1, 0))]
    things = [{"name": f"lamp {n}", "kind": "furniture", "at": pos(*spots[n])} for n in range(17)] + [{"name": "npc", "kind": "npc", "at": pos(-1, 0)}, {"name": "exit: door", "kind": "exit", "at": pos(2, 0)}]
    per = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": pos(0, 0)}, *things]}
    world = World(mem, 16); world.current_place = "room"; world.before(per, "room", None, (160, 144), 1)
    options = world.options(per, "room", {}, 1, inputs=tuple(BUTTONS))
    assert len(options) - 1 <= OPTION_CAP
    facts = world.view("", "room", compact=True)
    model = Capture({"action": {"i": 1}, "notebook": []})
    act(model, None, PERCEPTION, "Goals: reach the hall", facts, [], "A: press", {}, BUTTONS, options=options)
    system, user = model.args[0][0], model.args[0][1]
    assert len(system + user) / 4 <= 1700  # ponytail: chars/4 estimate, not the tokenizer
    assert all(word in user for word in ("place", "notebook", "action", "text", "expect", "why", '"i"', '"thing"', '"go_to"', '"buttons"', "advance", "focus", "tried", "hints", "lessons"))


def test_option_line_prints_the_tool_word_once():
    options = [{"n": 1, "tool": "approach", "label": "approach npc", "action": {"thing": 0, "verb": "approach"}, "notes": []},
               {"n": 2, "tool": "go_to", "label": "go to Hall", "action": {"go_to": "Hall"}, "notes": []}]
    _, model = run_with({"action": {"i": 1}, "notebook": []}, options=options)
    user = model.args[0][1]
    assert "1. approach npc\n2. go to Hall" in user and "approach approach" not in user


def test_option_lines_print_each_tool_word_once_and_keep_remembered_labels():
    options = [{"n": 1, "tool": "approach", "label": "approach npc", "action": {"thing": 0, "verb": "approach"}, "notes": []},
               {"n": 2, "tool": "interact", "label": "interact with npc", "action": {"thing": 0, "verb": "interact"}, "notes": []},
               {"n": 3, "tool": "go_to", "label": "go to computer desk (remembered, seen 30 steps ago)", "action": {"go_to": "computer desk"}, "notes": []},
               {"n": 4, "tool": "select", "label": "select OK", "action": {"thing": 0, "verb": "select"}, "notes": []},
               {"n": 5, "tool": "explore", "label": "walk to an unexplored part of this place (0 spots lead anywhere unseen)", "action": {"go_to": "unexplored"}, "notes": []},
               {"n": 6, "tool": "buttons", "label": "free buttons: UP", "action": None, "notes": []},
               {"n": 7, "tool": "advance", "label": "advance", "action": {"verb": "advance"}, "notes": []}]
    _, model = run_with({"action": {"i": 1}, "notebook": []}, options=options)
    lines = model.args[0][1].split("Options:\n", 1)[1].split("\n")[:7]
    assert lines[2] == "3. go to computer desk (remembered, seen 30 steps ago)"
    for line, o in zip(lines, options):
        body = line.split(". ", 1)[1]
        w = o["tool"].replace("_", " ")
        assert len(re.findall(rf"\b{w}\b", body.split(" (")[0])) == 1  # the tool word appears once


def test_prompt_without_options_is_unchanged():
    _, model = run({"action": {"i": 1}, "notebook": []})
    user, system = model.args[0][1], model.args[0][0]
    assert "Things on screen:\n0: door (exit) at [0.5, 0.2]" in user and "Options:" not in user
    assert system == SYSTEM and "pick one option" not in user and "Reply JSON" in user and '"action": {"thing": n,' in user


def test_why_and_expect_nested_in_action_are_read_and_top_level_wins():
    out, _ = run_with({"action": {"i": 2, "why": "the hall is through the door", "expect": "place_changes"}, "notebook": []})
    assert (out.action, out.option, out.why, out.expect) == ({"go_to": "Hall"}, 2, "the hall is through the door", "place_changes")
    out, _ = run_with({"why": "top", "expect": "text_appears", "action": {"i": 1, "why": "nested", "expect": "place_changes"}, "notebook": []})
    assert (out.why, out.expect, out.option) == ("top", "text_appears", 1)
    out, _ = run_with({"why": "", "expect": "bogus", "action": {"i": 1, "why": "nested", "expect": "place_changes"}, "notebook": []})
    assert (out.why, out.expect) == ("nested", "place_changes")
    out, _ = run({"action": {"thing": 0, "verb": "approach", "why": "x", "expect": "bogus"}, "notebook": []})
    assert (out.why, out.expect, out.action) == ("x", "screen_changes", {"thing": 0, "verb": "approach"})


class Queue:
    def __init__(self, *datas): self.datas, self.calls = list(datas), []
    def ask(self, system, user, images, schema, role=""): self.calls.append((user, images, schema, role)); return SimpleNamespace(data=self.datas.pop(0))


def decide(*datas, options=OPTIONS):
    model = Queue(*datas)
    return act(model, "IMAGE", PERCEPTION, "Goals: reach the hall", "facts", [], "A: press", {"name": "RED"}, BUTTONS, options=options, decide=True), model


def test_decide_is_text_only_and_answers_only_an_option_number():
    out, model = decide({"why": "the hall is the goal", "i": 2})
    user, images, schema, role = model.calls[0]
    assert (out.action, out.option, out.why, out.place, out.ops) == ({"go_to": "Hall"}, 2, "the hall is the goal", "", [])
    assert images == [] and role == "act" and schema["properties"]["i"]["enum"] == [1, 2, 3] and schema["additionalProperties"] is False
    assert "2. go to Hall — leads to Hall" in user and '"i"' in user and "buttons" not in schema["properties"]


def test_decide_retries_once_on_a_free_form_reply_then_gives_up():
    out, model = decide({"action": {"buttons": ["LEFT"]}, "why": "explore"}, {"why": "ok", "i": "1"})
    assert out.action == {"thing": 0, "verb": "approach"} and len(model.calls) == 2 and "invalid" in model.calls[1][0]
    out, model = decide({"i": 9}, {"buttons": ["LEFT"]})
    assert (out.action, out.option) == (None, None) and len(model.calls) == 2


def test_decide_follow_ups_are_enums_for_buttons_names_and_typing():
    out, model = decide({"why": "w", "i": 3}, {"buttons": ["UP", "ZAP"]}, {"buttons": ["UP", "A"]})
    assert out.action == {"buttons": ["UP", "A"]} and model.calls[1][2]["properties"]["buttons"]["items"]["enum"] == BUTTONS
    names = [{"n": 1, "tool": "go_to", "label": "look at or go to a thing here: vent ×3, desk", "action": None, "notes": []}]
    out, model = decide({"why": "w", "i": 1}, {"name": "vent"}, options=names)
    assert out.action == {"go_to": "vent"} and model.calls[1][2]["properties"]["name"]["enum"] == ["vent", "desk"]
    typing = [{"n": 1, "tool": "type", "label": "type text into the field", "action": {"verb": "type"}, "notes": []}]
    out, model = decide({"why": "w", "i": 1}, {"text": " RED "}, options=typing)
    assert (out.action, out.text) == ({"verb": "type"}, "RED") and "RED" in model.calls[1][0]
