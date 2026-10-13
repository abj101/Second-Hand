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
    assert "use type with the text" in REPLY and "Never pick letter buttons one by one" in REPLY
    assert not re.search(r"pok[eé]mon|\bred\b|\bblue\b|\brival\b", (SYSTEM + REPLY + user).casefold())
    assert "Known places: none" in user
    assert '"section":"goals"|"hints"|"lessons"' in REPLY
    assert "Place values" not in REPLY and '"places"' not in REPLY


def test_prompt_includes_at_most_twelve_known_places():
    _, model = run({}, places=[f"P{i}" for i in range(13)])
    assert "Known places: " + ", ".join(f"P{i}" for i in range(12)) in model.args[0][1]
