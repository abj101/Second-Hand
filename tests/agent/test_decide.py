import pytest

from agent.brief import GENERAL
from agent.decide import (arrival_entrance, known_routes, _conditions, choose, code_choice, escalate, navigation_candidates,
                          next_test, plan, related_target_ids, resolve_target)
from agent.memory import Memory
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


def test_chooser_prompt_has_brief_compact_task_and_no_empty_predictions():
    class PromptModel(StubModel):
        def ask(self, system, user, **kwargs):
            self.prompt = user
            return super().ask(system, user, **kwargs)

    model = PromptModel([type("Reply", (), {"data": {"i": 0, "effect": "screen_changes"}})()])
    choose(model, "Knowledge: Oak is next door", {"affordances": _affordances()},
           {"goal": "Find Oak", "used": 3, "budget": 40, "status": "active", "src": "orchestrator"},
           [None, None], {"name": "Hand"}, controls="Move with arrows; confirm with A.")
    assert GENERAL in model.prompt
    assert "Move with arrows" in model.prompt
    assert "Goal: Find Oak" in model.prompt and "progress: 3/40; budget: 40" in model.prompt
    assert "status" not in model.prompt and "src" not in model.prompt
    assert "Code predictions:" not in model.prompt
    sparse = PromptModel([type("Reply", (), {"data": {"i": 0, "effect": "screen_changes"}})()])
    choose(sparse, "", {"affordances": _affordances()}, {}, [None, {"effect": "text_appears"}], {"name": "Hand"})
    assert "Code predictions: {1: {'effect': 'text_appears'}}" in sparse.prompt


def test_chooser_memory_merges_and_caps_knowledge_and_lists_recorded_edges(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_room", "name": "room", "edges": [
        {"via": "stairs", "to": "p_up", "n": 2, "ok": 1},
    ]})
    for i in range(10):
        mem.know(f"Fact {i} continues", src="game_text", step=i)
    mem.know("PROF. OAK, next", src="game_text", step=11)
    mem.know("door, is looking", src="game_text", step=12)
    mem.know("door, is looking for you.", src="game_text", step=13)
    view, _ = mem.view("chooser", {"place": "p_room"})
    knowledge = [line for line in view.splitlines() if line.startswith("Knowledge:")]
    assert len(knowledge) <= 8
    assert any("PROF. OAK, next" in line for line in knowledge)
    assert any("door, is looking for you." in line for line in knowledge)
    assert "Known exit: stairs → p_up" in view
    assert "Failed test: stairs → p_up" in view


def test_orchestrator_view_reserves_budget_for_all_game_text(tmp_path):
    mem = Memory(tmp_path)
    for i in range(20):
        mem.know(f"Fact {i}: " + "detail " * 20, src="game_text", step=i)
    for i in range(30):
        mem.upsert("entities", {"id": f"e_{i}", "name": "entity " + "detail " * 15,
                                "kind": "object", "place": "p_room", "seen": i})
    view, _ = mem.view("orchestrator", {"place": "p_room"})
    assert all(f"Fact {i}:" in view for i in range(20))


def test_condition_parser_accepts_place_and_new_place():
    assert _conditions(["place_is(outdoor area)", {"new_place": True}]) == [
        {"place_is": "outdoor area"}, {"new_place": True},
    ]
    assert _conditions([{"new_place": False}]) == []


def test_plan_caps_tasks_and_adds_step_limit():
    data = {"tasks": [
        {"goal": "Leave the house", "done_when": [{"new_place": True}]},
        {"goal": "Find Oak", "done_when": [{"text_seen": "Oak"}], "fail_when": [{"steps_over": 25}]},
        {"goal": "Extra", "done_when": [{"entity_known": "player"}]},
    ]}
    model = StubModel([type("Reply", (), {"data": data})()])
    tasks = plan(model, "Knowledge: Oak is next door", "new text", "Explore", "done", "Oak is waiting")
    assert len(tasks) == 2
    assert tasks[0]["done_when"] == [{"new_place": True}]
    assert tasks[0]["fail_when"] == [{"steps_over": 40}]
    assert tasks[1]["fail_when"] == [{"steps_over": 25}]
    assert model.calls == 1


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


def test_navigation_candidates_add_entity_without_affordance_and_use_tile_geometry():
    perception = {"entities": [
        {"kind": "player", "name": "player", "at": [.45, .46]},
        {"kind": "object", "name": "rug", "at": [.2, .93], "placement": "bottom wall"},
        {"kind": "object", "name": "stairs", "at": [.8, .2]},
    ], "affordances": [{"verb": "approach", "target": "stairs", "at": [.8, .2]}]}
    candidates = navigation_candidates(perception, ["stairs_id"], {0: "player_id", 1: "rug_id", 2: "stairs_id"},
        player_at=[.45, .46], tile_frac=(16 / 160, 16 / 144))
    assert [c["id"] for c in candidates] == ["stairs_id", "rug_id"]
    rug = next(c for c in candidates if c["id"] == "rug_id")
    assert rug["affordance"]["verb"] == "approach"
    assert rug["position"] == "2 tiles left, 4 tiles down"
    assert "placement" not in candidates[0]
    assert "placement" not in candidates[0]["affordance"]


def test_next_test_filters_tested_ids_and_uses_judge_order():
    candidates = [
        {"id": "stairs_id", "name": "stairs", "kind": "object", "at": [.8, .2], "placement": "middle", "position": "2 tiles up", "distance": 3},
        {"id": "rug_id", "name": "rug", "kind": "object", "at": [.2, .9], "placement": "bottom wall", "position": "2 tiles down", "distance": 2},
    ]
    class Ranker:
        def rank(self, state, questions):
            self.state, self.questions = state, questions
            return {"answers": {"target": {"choice": "stairs"}}}
    ranker = Ranker()
    selected = next_test(ranker, "reach a new place", ["stairs leads to a visited place"], candidates, {"stairs_id"})
    assert selected["id"] == "rug_id"
    assert list(ranker.state["things"]) == ["rug"]
    assert "Which thing should the player walk to next" in ranker.questions["target"]["instructions"]


def test_next_test_uses_edge_placement_then_distance_when_judge_is_unavailable():
    candidates = [
        {"id": "near", "name": "near", "placement": "middle", "distance": 0, "position": "at the player's position"},
        {"id": "edge", "name": "edge", "placement": "bottom wall", "distance": 5, "position": "5 tiles down"},
        {"id": "wall", "name": "wall", "placement": "left wall", "distance": 2, "position": "2 tiles left"},
    ]
    assert next_test(None, "go somewhere", [], candidates, set())["id"] == "wall"

def test_next_test_keeps_missing_placement_unknown():
    class Ranker:
        def rank(self, state, questions):
            assert state["things"]["object"]["placement"] == "unknown placement"
            return None
    candidate = {"id": "object", "name": "object", "at": [.5, .5], "distance": 0}
    assert next_test(Ranker(), "go", [], [candidate], set()) == candidate

def test_next_test_falls_back_on_malformed_ranker_answer():
    class Ranker:
        def rank(self, state, questions): return {"answers": []}
    candidates = [{"id": str(i), "name": str(i), "placement": "middle"} for i in range(2)]
    assert next_test(Ranker(), "go", [], candidates, set()) == candidates[0]


def test_exit_alias_resolves_to_tested_entity_id(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_room", "name": "room", "edges": []})
    stairs = mem.resolve("stairs", "p_room")
    mem.relate(stairs, "leads_to", "p_up", src="observed", step=1, hit=False)
    assert resolve_target(mem, "exit: stairs", "p_room") == stairs
    assert stairs in related_target_ids(mem, "p_room")


def test_exit_entity_alias_is_deduped_and_filtered_with_base_entity(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_room", "name": "room", "edges": []})
    stairs = mem.resolve("stairs", "p_room")
    mem.relate(stairs, "leads_to", "p_up", src="observed", step=1, hit=False)
    per = {"entities": [
        {"name": "stairs", "kind": "object", "at": [.8, .2]},
        {"name": "exit: stairs", "kind": "object", "at": [.8, .2]},
    ], "affordances": [{"verb": "approach", "target": "exit: stairs", "at": [.8, .2]}]}
    candidates = navigation_candidates(per, [stairs], {0: stairs, 1: stairs})
    assert len(candidates) == 1
    assert next_test(None, "reach a new place", [], candidates, related_target_ids(mem, "p_room")) is None


def test_tested_target_alias_does_not_cross_places(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_room", "name": "room", "edges": []})
    mem.upsert("places", {"id": "p_other", "name": "other room", "edges": []})
    other = mem.resolve("stairs", "p_other")
    current = mem.resolve("stairs", "p_room")
    mem.relate(other, "leads_to", "p_up", src="observed", step=1, hit=False)
    assert current not in related_target_ids(mem, "p_room")


def test_known_routes_names_visited_places_and_failed_tests(tmp_path):
    mem = Memory(tmp_path)
    mem.upsert("places", {"id": "p_up", "name": "upstairs", "edges": []})
    stairs, tv = mem.resolve("stairs", "p_room"), mem.resolve("television", "p_room")
    mem.relate(stairs, "leads_to", "p_up", src="inferred", step=1)
    mem.relate(tv, "leads_to", "", src="observed", step=2, hit=False)
    mem.relate(tv, "leads_to", "the player leaves", src="model", step=2)
    assert known_routes(mem) == ["stairs leads to upstairs (already visited)", "television: tested, no new place"]


def test_arrival_entrance_prefers_matching_name_then_nearest_exit():
    per = {"entities": [{"kind": "player", "name": "player", "at": [.5, .5]},
                        {"kind": "exit", "name": "exit: door mat", "at": [.5, .6]},
                        {"kind": "exit", "name": "exit: stairs", "at": [.9, .1]}]}
    assert arrival_entrance(per, "exit: stairs") == "exit: stairs"
    assert arrival_entrance(per, "door") == "exit: door mat"
    assert arrival_entrance(per, "door", reach=0.05) is None
