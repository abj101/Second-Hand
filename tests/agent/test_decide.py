import pytest

from agent.decide import choose, code_choice, escalate
from agent.model import ModelUnavailable
from tests.agent.fakes import StubModel


def _affordances():
    return [
        {"verb": "click", "target": "door", "at": [0.5, 0.5]},
        {"verb": "type", "target": "rival name", "at": [0.5, 0.5]},
    ]


def test_single_non_press_action_skips_model():
    model = StubModel([])
    choice = choose(model, "", {"affordances": [{"verb": "press", "target": "A"}, _affordances()[0]]},
                    {}, [None, None], {"name": "Hand"})
    assert model.calls == 0
    assert (choice.i, choice.called, choice.effect) == (1, False, "screen_changes")


def test_exit_always_expects_place_change():
    aff = {"verb": "approach", "target": "exit: door mat"}
    model = StubModel([type("Reply", (), {"data": {"i": 0, "effect": "screen_changes"}})()])
    choice = choose(model, "", {"affordances": [aff, _affordances()[0]]}, {}, [], {"name": "Hand"})
    assert choice.effect == "place_changes"


def test_invalid_choice_uses_first_untried_and_screen_changes():
    model = StubModel([type("Reply", (), {"data": {"i": 0, "effect": "bogus"}})()])
    view = "Recent: click door → miss\nFailed here: click door"
    choice = choose(model, view, {"affordances": _affordances()}, {}, [], {"name": "Hand"})
    assert choice.i == 1
    assert choice.effect == "screen_changes"


def test_model_unavailable_propagates():
    class OfflineModel(StubModel):
        def ask(self, *args, **kwargs):
            raise ModelUnavailable("offline")

    model = OfflineModel([])
    with pytest.raises(ModelUnavailable):
        choose(model, "", {"affordances": _affordances()}, {}, [], {"name": "Hand"})
    with pytest.raises(ModelUnavailable):
        escalate(model, "", "stuck")


def test_type_text_uses_rival_default_and_used_ids_pass_through():
    model = StubModel([type("Reply", (), {"data": {"i": 1, "effect": "text_appears", "used": ["e_rival"]}})()])
    choice = choose(model, "", {"affordances": _affordances()}, {}, [], {"name": "Hand", "rival": "Blue"})
    assert choice.text == "Blue"
    assert choice.used == ["e_rival"]


def test_missing_default_preset_forces_custom_name_entry():
    affs = [
        {"verb": "select", "target": "NEW NAME", "why": "allows the player to type a custom name"},
        {"verb": "select", "target": "RED", "why": "sets the character name to Red"},
    ]
    model = StubModel([type("Reply", (), {"data": {"i": 1, "effect": "place_changes"}})()])
    choice = choose(model, "", {"text": ["First, what is your name?"], "affordances": affs}, {}, [], {"name": "Hand"})
    assert choice.i == 0
    assert "custom entry" in choice.why


def test_generic_type_affordance_uses_visible_role_context():
    model = StubModel([type("Reply", (), {"data": {"i": 1, "effect": "text_appears"}})()])
    perception = {"text": ["What is his name again?"], "entities": [{"name": "rival", "kind": "character"}],
                  "affordances": [{"verb": "click", "target": "continue"}, {"verb": "type", "target": "name field"}]}
    choice = choose(model, "", perception, {}, [], {"name": "Hand", "rival": "Blue"})
    assert choice.text == "Blue"


def test_prompt_names_defaults_output_and_effect_choices():
    class PromptModel(StubModel):
        def ask(self, system, user, **kwargs):
            self.prompt = user
            return super().ask(system, user, **kwargs)

    model = PromptModel([type("Reply", (), {"data": {"i": 0, "effect": "screen_changes"}})()])
    choose(model, "", {"affordances": _affordances()}, {}, [], {"name": "Hand"})
    assert all(term in model.prompt for term in ("Defaults:", "Prefer untried", "unfamiliar areas", "furniture or menus", "place_changes", "text"))
    assert "Pokémon" not in model.prompt


def test_default_curiosity_overrides_repeat_with_first_untried_action():
    affs = [{"verb": "interact", "target": "console"}, {"verb": "approach", "target": "exit up"}]
    model = StubModel([type("Reply", (), {"data": {"i": 0, "effect": "place_changes", "used": ["e_console"]}})()])
    recent = "Recent: interact console → hit"
    choice = choose(model, recent, {"affordances": affs}, {"src": "default"}, [], {"name": "Hand"})
    assert choice.i == 1
    assert choice.effect == "place_changes"
    assert choice.used == ["e_console"]
    assert "tried action" in choice.why


def test_real_task_may_repeat_a_model_selected_action():
    affs = [{"verb": "interact", "target": "console"}, {"verb": "approach", "target": "exit up"}]
    model = StubModel([type("Reply", (), {"data": {"i": 0, "effect": "place_changes"}})()])
    choice = choose(model, "Recent: interact console → hit", {"affordances": affs}, {"src": "user"}, [], {"name": "Hand"})
    assert choice.i == 0
    assert choice.effect == "place_changes"


def test_code_choice_prefers_untried_then_least_tried():
    affs = _affordances()
    recent = [{"choice": {"verb": "click", "target": "door"}}]
    choice = code_choice({"affordances": affs}, ["e_door", "e_rival"], recent)
    assert choice.i == 1
    assert choice.used == ["e_rival"]


def test_code_choice_handles_empty_affordances():
    choice = code_choice({}, [], [])
    assert (choice.i, choice.effect, choice.used) == (0, "screen_changes", [])


def test_escalate_drops_unknown_condition_kinds():
    data = {"tasks": [
        {"goal": "Find a path", "done_when": ["steps_over(3)", "place_is(outdoor area)", "teleport(x)"],
         "fail_when": [{"entity_known": "wall"}, {"route": "x"}]},
        {"goal": "Invalid", "done_when": [{"route": "x"}]},
    ]}
    model = StubModel([type("Reply", (), {"data": data})()])
    tasks = escalate(model, "", "stuck")
    assert len(tasks) == 1
    assert tasks[0]["done_when"] == [{"steps_over": 3}, {"place_is": "outdoor area"}]
    assert tasks[0]["fail_when"] == [{"entity_known": "wall"}]


def test_escalate_sends_typed_condition_schema():
    class SchemaModel(StubModel):
        def ask(self, *args, **kwargs):
            self.schema = kwargs.get("schema")
            return super().ask(*args, **kwargs)

    model = SchemaModel([type("Reply", (), {"data": {"tasks": []}})()])
    escalate(model, "", "stuck")
    schema = model.schema["properties"]["tasks"]["items"]["properties"]["done_when"]["items"]
    assert schema["anyOf"][0]["properties"]["place_is"]["type"] == "string"
    assert schema["anyOf"][-1]["properties"]["steps_over"]["type"] == "integer"
