from types import SimpleNamespace

from agent.plan import Plan, REPLY, SCHEMA, SYSTEM, plan


class StubModel:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def ask(self, system, user, images, schema, *, role=""):
        self.calls.append((system, user, images, schema, role))
        return SimpleNamespace(data=self.data)


def notebook():
    return SimpleNamespace(render=lambda: "RENDERED NOTEBOOK")


def test_ops_and_why_pass_through_with_why_capped_at_200():
    ops = [{"op": "wat", "unvalidated": object()}]
    model = StubModel({"ops": ops, "why": "x" * 250})

    result = plan(model, "new text", notebook(), "map", ["sign"], ["wait"], "heard a clue")

    assert result == Plan(ops, "x" * 200)
    assert result.ops is ops
    assert model.calls[0][0] == SYSTEM
    assert model.calls[0][3] is SCHEMA
    assert model.calls[0][4] == "plan"


def test_non_list_ops_becomes_none():
    result = plan(StubModel({"ops": {"op": "add"}, "why": "because"}), "cycle", notebook(), "", [], [])

    assert result == Plan(None, "because")


def test_prompt_sections_are_ordered_and_have_no_chapter_or_log_history():
    model = StubModel({"ops": [], "why": "steady"})

    plan(model, "arrived", notebook(), "MAP", ["SCREEN"], ["ACTION"], "EVIDENCE")

    prompt = model.calls[0][1]
    sections = (
        "Trigger: arrived", "RENDERED NOTEBOOK", "Evidence since last plan: EVIDENCE",
        "Map and memory: MAP", "Text on screen now: SCREEN", "Recent actions: ACTION", REPLY,
    )
    positions = [prompt.index(section) for section in sections]
    assert positions == sorted(positions)
    assert all(marker not in prompt.casefold() for marker in ("chapter", "notes", "log span"))


def test_system_is_bounded_and_uses_generic_game_language():
    assert SYSTEM.count(".") <= 7
    assert all(term not in SYSTEM.casefold() for term in ("door", "window", "rug", "stairs", "oak", "mom", "next door"))
    assert SCHEMA["required"] == ["ops", "why"]
    assert SCHEMA["properties"]["why"]["maxLength"] == 200
    assert SCHEMA["properties"]["ops"]["items"]["properties"]["target"] == {"type": "string"}
