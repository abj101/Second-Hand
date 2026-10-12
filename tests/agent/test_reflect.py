from agent.decide import PlanResult, plan
from tests.agent.fakes import StubModel


def reply(data): return type("Reply", (), {"data": data})()


def test_plan_returns_reflection_with_legacy_task_list_and_caps_tasks():
    class Captures(StubModel):
        def ask(self, *args, **kwargs):
            self.prompt, self.schema = args[1], kwargs["schema"]
            return super().ask(*args, **kwargs)

    data = {"tasks": [
        {"goal": f"Goal {i}", "done_when": [{"new_place": True}]} for i in range(3)
    ], "pattern_names": {"p1": "door transition"}, "notes": "The exit led to a new room."}
    model = Captures([reply(data)])

    result = plan(model, "memory", "surprise", pattern_names={"p1": "old name"})

    assert isinstance(result, PlanResult) and isinstance(result, list) and len(result) == 2
    assert result.pattern_names == {"p1": "door transition"} and result.notes == "The exit led to a new room."
    assert "Existing pattern ids and names: {'p1': 'old name'}" in model.prompt
    assert "pattern_names" in model.schema["properties"] and "notes" in model.schema["properties"]
    assert model.calls == 1
