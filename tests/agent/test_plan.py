import json
from types import SimpleNamespace

from agent.plan import Plan, SYSTEM, chapter, plan


class StubModel:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def ask(self, system, user, images, schema, *, role=""):
        self.calls.append((system, user, schema, role))
        return SimpleNamespace(data=self.data)


def notebook():
    data = {
        "facts": {"heard-place": {"key": "heard-place", "text": "A place was mentioned."}},
        "beliefs": {"route": {"key": "route", "confidence": "mid", "status": "held"}},
        "quests": [{"key": "visit", "text": "Visit the mentioned place.", "why": "heard-place", "status": "open"}],
        "notes": [{"text": "The place is not mapped yet."}],
        "chapters": [],
    }
    return SimpleNamespace(
        data=data,
        log=[{"step": 2, "text": "New information arrived."}],
        render=lambda: json.dumps(data, ensure_ascii=False),
    )


def test_parse_full_reply_and_keep_only_supported_check():
    model = StubModel({
        "quests": [
            {"key": "visit", "text": "Visit the mentioned place.", "why": "heard-place", "status": "open",
             "check": {"kind": "reach", "arg": "place:mentioned"}},
            {"key": "explore", "check": {"kind": "exit", "arg": "north"}},
        ],
        "beliefs": [{"key": "route", "confidence": "mid", "status": "revised"}],
        "facts": [{"key": "heard-place", "text": "The place was mentioned."}],
        "note": "The new text confirms the lead; pursue it first and expect to map it.",
        "why": "The heard fact supports the first quest.",
    })
    result = plan(model, "new text", notebook(), "memory", ["A sign"], ["wait"], evidence="New information arrived.")

    assert isinstance(result, Plan)
    assert result.quests[0]["check"] == {"kind": "reach", "arg": "place:mentioned"}
    assert "check" not in result.quests[1]
    assert result.beliefs[0]["confidence"] == "mid"
    assert result.facts[0]["key"] == "heard-place"
    assert result.note.startswith("The new text")
    assert model.calls[0][3] == "plan"
    assert "Persisted notebook state:" in model.calls[0][1]
    assert "Current chapter log span:" in model.calls[0][1]


def test_accepted_chapter_and_current_log_span_reach_next_plan_prompt():
    state = notebook()
    state.data["chapters"].append({"from": 1, "to": 5, "summary": "Accepted chapter summary: the old lead was resolved."})
    state.log = [{"step": 5, "text": "Before the chapter."}, {"step": 6, "text": "After the chapter."}]
    model = StubModel({})

    plan(model, "new evidence", state, "", [], [])

    prompt = model.calls[0][1]
    assert "Accepted chapter summary: the old lead was resolved." in prompt
    assert "After the chapter." in prompt
    assert "Before the chapter." not in prompt


def test_missing_fields_parse_to_empty_values():
    result = plan(StubModel({}), "cycle", notebook(), "", [], [])

    assert result == Plan([], [], [], "", "")


def test_parser_drops_bad_keys_and_invalid_belief_confidence():
    model = StubModel({
        "quests": [{"key": "ok", "status": "open"}, {"x" * 41: "bad"}],
        "beliefs": [{"key": "bad", "confidence": "certain"}],
        "facts": [{"key": "x" * 41, "text": "bad"}],
        "note": "n" * 450,
        "why": "because",
    })
    result = plan(model, "cycle", notebook(), "", [], [])

    assert result.quests == [{"key": "ok", "status": "open"}]
    assert result.beliefs == []
    assert result.facts == []
    assert len(result.note) == 400


def test_chapter_reply_is_validated_and_bounded():
    model = StubModel({"summary": "s" * 700, "ruled_out": ["old idea"], "open_leads": ["new lead"]})

    result = chapter(model, notebook())

    assert result == {"summary": "s" * 600, "ruled_out": ["old idea"], "open_leads": ["new lead"]}
    assert model.calls[0][3] == "chapter"
    assert "Current notes:" in model.calls[0][1]
    assert "Current chapter log span:" in model.calls[0][1]
    assert "Closed quests:" in model.calls[0][1]
    assert "Held beliefs and open quests" in model.calls[0][1]
    assert chapter(StubModel({"summary": "bad", "ruled_out": "bad", "open_leads": []}), notebook()) is None


def test_prompt_contains_strategy_rules_and_omits_removed_tree_and_exit_rules():
    prompt = SYSTEM.casefold()

    for removed in ("work backwards", "leaf is the focus", "another exit", "when the trigger is an arrival", "done_when"):
        assert removed not in prompt
    for required in (
        "outcomes in the game world", "never name buttons, directions, coordinates, or a specific door",
        "every quest must cite why", "beliefs are hypotheses", "supports or contradicts", "refute them with a reason",
        "keep refuted ideas ruled out", "fact and outranks beliefs", "before it is mapped", "on a stall",
        "revising a belief or reordering quests", "full ordered quest list", "no more than five open quests",
        "first open quest now", "at most 400 characters", "what changed in your understanding", "what you expect next",
    ):
        assert required in prompt
