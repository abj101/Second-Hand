from dataclasses import fields
from types import SimpleNamespace

from agent.act import Act, SYSTEM, act


BUTTONS = ["UP", "DOWN", "LEFT", "RIGHT", "A", "B"]
PER = {"screen": "dialogue", "text": ["Which way?"]}
OPTIONS = [
    {"n": 1, "tool": "go_to", "label": "go to Hall, 1 left", "action": {"go_to": "hall"}, "notes": ["crossed 2×"]},
    {"n": 2, "tool": "buttons", "label": "free buttons", "action": None, "notes": []},
    {"n": 3, "tool": "type", "label": "type a name", "action": {"verb": "type"}, "notes": []},
]


class Queue:
    def __init__(self, *data):
        self.data, self.calls = list(data), []

    def ask(self, system, user, images, schema, *, role):
        self.calls.append((system, user, images, schema, role))
        return SimpleNamespace(data=self.data.pop(0))


def run(*data, options=OPTIONS):
    model = Queue(*data)
    out = act(model, PER, "Goal: reach Hall", options, BUTTONS, "A: confirm")
    return out, model


def test_prompt_has_focus_screen_text_and_rows_only():
    _, model = run({"why": "the exit leads on", "i": 1})
    user = model.calls[0][1]
    assert user.startswith("Goal: reach Hall\nScreen: dialogue\nText on screen: Which way?")
    assert "1. go to Hall, 1 left — crossed 2×" in user
    assert all(term not in user for term in ("Flags", "Known facts", "Map:", "Other open quests"))


def test_number_and_string_number_return_a_copy_of_the_action():
    for n in (1, "1"):
        out, _ = run({"why": "the goal is there", "i": n})
        assert (out.action, out.option) == ({"go_to": "hall"}, 1)
        out.action["go_to"] = "changed"
        assert OPTIONS[0]["action"] == {"go_to": "hall"}


def test_invalid_reply_retries_once_then_returns_none():
    out, model = run({"i": 0}, {"i": 9})
    assert (out.action, out.option, len(model.calls)) == (None, None, 2)
    assert "invalid" in model.calls[1][1]


def test_buttons_followup_is_an_enum_and_includes_controls():
    out, model = run({"why": "try the buttons", "i": 2}, {"buttons": ["LEFT", "A"]})
    _, prompt, _, schema, role = model.calls[1]
    assert out.action == {"buttons": ["LEFT", "A"]}
    assert schema["properties"]["buttons"]["items"]["enum"] == BUTTONS
    assert "Controls:\nA: confirm" in prompt and role == "act"


def test_type_followup_is_bounded_and_has_no_preferences():
    out, model = run({"why": "enter the name", "i": 3}, {"text": "x" * 60})
    prompt, schema = model.calls[1][1], model.calls[1][3]
    assert out.action == {"verb": "type"} and len(out.text) <= 40
    assert schema["properties"]["text"]["maxLength"] == 40
    assert "Preferences" not in prompt


def test_act_shape_and_system_are_generic():
    assert [field.name for field in fields(Act)] == ["action", "text", "why", "option"]
    assert len(SYSTEM.split(". ")) == 4
    assert all(word not in SYSTEM.casefold() for word in ("pokemon", "oak", "mom", "rug"))


def test_default_choices_clause_is_sent_and_changes_act_cache_key():
    from agent.model import Ollama
    _, model = run({"why": "choose the offered name", "i": 1})
    clause = "; pick a preset or default name or setting when offered instead of typing your own"
    system, user, images, schema, role = model.calls[0]
    assert clause in system and role == "act"
    cache = Ollama("test")
    assert cache._key(system, user, images, schema) != cache._key(system.replace(clause, ""), user, images, schema)


def test_system_wall_clause_stays_in_the_third_sentence():
    assert "; to go through an exit on a wall, step onto it and press toward that wall" in SYSTEM.split(". ")[2]


def test_system_declines_optional_naming_offers():
    assert "answer no when asked whether to give or change a name or nickname" in SYSTEM and "end key" in SYSTEM
    assert len(SYSTEM.split(". ")) == 4 and len(SYSTEM) <= 2000


def test_world_menu_headers_mark_state_changes_and_modal_menus_get_none():
    opts = [
        {"n": 1, "tool": "go_to", "label": "go to Hall", "action": {"go_to": "hall"}, "notes": [], "state": "new"},
        {"n": 2, "tool": "interact", "label": "check desk", "action": {"thing": 0, "verb": "interact"}, "notes": [], "state": "new"},
        {"n": 3, "tool": "interact", "label": "check vent", "action": {"thing": 1, "verb": "interact"}, "notes": ["checked"], "state": "failed"},
        {"n": 4, "tool": "buttons", "label": "free buttons", "action": None, "notes": [], "state": "new"},
    ]
    model = Queue({"why": "x", "i": 1})
    act(model, {"screen": "world", "text": []}, "Goal", opts, BUTTONS, "A")
    assert model.calls[0][1].split("\n")[3:-1] == ["Not tried here:", "1. go to Hall", "2. check desk", "Failed here:", "3. check vent — checked", "4. free buttons"]
    model = Queue({"why": "x", "i": 1})
    act(model, {"screen": "dialogue", "text": []}, "Goal", opts, BUTTONS, "A")
    assert "here:" not in model.calls[0][1]
