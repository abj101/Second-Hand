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
    assert "Options:\n1. approach approach door\n2. go_to go to Hall — leads to Hall (used 2×, last step 5); refuted: wall\n3. buttons free input (any of: UP, A)" in user
    assert "Things on screen:" not in user and "0: door (exit)" not in user
    assert '"action": {"i": n} to pick one option by number (preferred)' in user and "any 'info' line" in user
    assert "The option notes are what already happened here" in system and "The option notes" not in SYSTEM


def test_prompt_without_options_is_unchanged():
    _, model = run({"action": {"i": 1}, "notebook": []})
    user, system = model.args[0][1], model.args[0][0]
    assert "Things on screen:\n0: door (exit) at [0.5, 0.2]" in user and "Options:" not in user
    assert system == SYSTEM and "pick one option" not in user and "Reply JSON" in user and '"action": {"thing": n,' in user
