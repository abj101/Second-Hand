from __future__ import annotations

import pytest

from avatar.config import load_config
from avatar.models import configured_models


@pytest.fixture(autouse=True)
def _clear_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AVATAR_MODEL", raising=False)
    monkeypatch.delenv("AVATAR_LOCAL_MODEL", raising=False)


def test_configured_models_local_list() -> None:
    cfg = {
        "models": {
            "local": [
                {"id": "qwen3-vl:4b", "provider": "ollama"},
                {"id": "gemma3:4b-it-qat", "provider": "ollama"},
            ],
            "cloud": [],
            "locked": [],
        }
    }
    assert configured_models(cfg) == [
        ("qwen3-vl:4b", "ollama"),
        ("gemma3:4b-it-qat", "ollama"),
    ]


def test_configured_models_legacy_dict() -> None:
    cfg = {"models": {"local": {"id": "qwen2.5vl:7b", "provider": "ollama"}}}
    assert configured_models(cfg) == [("qwen2.5vl:7b", "ollama")]


def test_configured_models_skips_empty_cloud() -> None:
    cfg = {
        "models": {
            "local": [{"id": "qwen3-vl:8b", "provider": "ollama"}],
            "cloud": [],
        }
    }
    assert configured_models(cfg) == [("qwen3-vl:8b", "ollama")]


def test_configured_models_filter_substring() -> None:
    cfg = {
        "models": {
            "local": [
                {"id": "qwen3-vl:4b", "provider": "ollama"},
                {"id": "qwen3-vl:8b", "provider": "ollama"},
                {"id": "qwen2.5vl:7b", "provider": "ollama"},
            ]
        }
    }
    assert configured_models(cfg, only="qwen3-vl:8b") == [("qwen3-vl:8b", "ollama")]
    assert configured_models(cfg, only=["qwen3-vl"]) == [
        ("qwen3-vl:4b", "ollama"),
        ("qwen3-vl:8b", "ollama"),
    ]


def test_configured_models_filter_explicit_override() -> None:
    cfg = {"models": {"local": [{"id": "qwen3-vl:4b", "provider": "ollama"}]}}
    assert configured_models(cfg, only="gemma3") == [("gemma3", "ollama")]


def test_configured_models_env_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVATAR_MODEL", "gemma3:4b")
    cfg = {
        "models": {
            "local": [
                {"id": "qwen3-vl:4b", "provider": "ollama"},
                {"id": "gemma3:4b-it-qat", "provider": "ollama"},
            ]
        }
    }
    assert configured_models(cfg) == [("gemma3:4b-it-qat", "ollama")]


def test_configured_models_locked_wins() -> None:
    cfg = {
        "models": {
            "local": [{"id": "qwen3-vl:4b", "provider": "ollama"}],
            "locked": [{"id": "qwen3-vl:8b", "provider": "ollama"}],
        }
    }
    assert configured_models(cfg) == [("qwen3-vl:8b", "ollama")]


def test_configured_models_infers_ollama_cloud_provider() -> None:
    cfg = {
        "models": {
            "local": [{"id": "qwen3.5:4b-instruct-q4_K_M", "provider": "ollama"}],
            "cloud": [{"id": "gemma4:31b-cloud"}],
            "locked": [],
        }
    }
    assert configured_models(cfg) == [
        ("qwen3.5:4b-instruct-q4_K_M", "ollama"),
        ("gemma4:31b-cloud", "ollama"),
    ]


def test_experiment_yaml_phase1_local_roster() -> None:
    rows = configured_models(load_config())
    ids = [model for model, _ in rows]
    assert ids == ["qwen3.5:4b-q4_K_M"]
    assert "qwen3.5:4b" not in ids
    assert all(provider == "ollama" for _, provider in rows)
    brain = configured_models(load_config(), role="brain")
    assert brain == [("qwen3.5:4b-q4_K_M", "ollama")]
    cloud = configured_models(load_config(), only="gemma4:31b-cloud")
    assert cloud == [("gemma4:31b-cloud", "ollama")]


def test_selected_models_toggles_cloud() -> None:
    from argparse import Namespace

    from avatar.experiments.common import selected_models

    cfg = load_config()
    default = selected_models(cfg, Namespace(skip_models=False, model=None))
    assert default == [("qwen3.5:4b-q4_K_M", "ollama")]
    cloud = selected_models(cfg, Namespace(skip_models=False, model=["gemma4"]))
    assert cloud == [("gemma4:31b-cloud", "ollama")]


def test_configured_models_skips_brain_role() -> None:
    cfg = {
        "models": {
            "locked": [
                {"id": "qwen3-vl:4b-instruct-q4_K_M", "provider": "ollama", "role": "eyes"},
                {"id": "qwen3.5:4b", "provider": "ollama", "role": "brain"},
            ]
        }
    }
    assert configured_models(cfg) == [("qwen3-vl:4b-instruct-q4_K_M", "ollama")]
    assert configured_models(cfg, role="brain") == [("qwen3.5:4b", "ollama")]


def test_make_eyes_and_brain_roles() -> None:
    from avatar.models import make_brain, make_eyes

    cfg = {
        "temperature": 0.1,
        "max_tokens": 64,
        "ollama": {"num_ctx": 4096, "brain_num_ctx": 2048, "keep_alive": "5m"},
        "models": {
            "local": [
                {"id": "qwen3.5:4b-instruct-q4_K_M", "provider": "ollama"},
            ],
            "cloud": [
                {"id": "gemma4:31b-cloud", "provider": "ollama"},
            ],
            "locked": [
                {"id": "qwen3.5:4b-instruct-q4_K_M", "provider": "ollama", "role": "eyes"},
                {
                    "id": "qwen3.5:4b-instruct-q4_K_M",
                    "provider": "ollama",
                    "role": "brain",
                    "fallback": "phi4-mini",
                    "num_ctx": 2048,
                },
            ]
        },
    }
    eyes = make_eyes(cfg)
    brain = make_brain(cfg)
    assert eyes.model == "qwen3.5:4b-instruct-q4_K_M"
    assert eyes.num_ctx == 4096
    assert brain.model == "qwen3.5:4b-instruct-q4_K_M"
    assert brain.num_ctx == 2048
    assert make_brain(cfg, model_id="phi4-mini").model == "phi4-mini"
    toggled = make_eyes(cfg, model_id="gemma4:31b-cloud")
    assert toggled.model == "gemma4:31b-cloud"


class _FakeMessage:
    def __init__(self, **fields: object) -> None:
        self._fields = fields
        for key, value in fields.items():
            setattr(self, key, value)

    def model_dump(self) -> dict:
        return dict(self._fields)


def test_loads_json_empty_and_object() -> None:
    from avatar.models import _loads_json

    assert _loads_json("") is None
    assert _loads_json("{}") is None
    assert _loads_json('{"screen_kind":"gameplay"}') == {"screen_kind": "gameplay"}
    assert _loads_json('noise {"score": 4} trailing') == {"score": 4}


def test_parse_message_uses_reasoning_when_content_empty() -> None:
    from avatar.models import _parse_message

    text, parsed = _parse_message(
        _FakeMessage(content="", reasoning='{"screen_kind":"gameplay","score":0}')
    )
    assert parsed == {"screen_kind": "gameplay", "score": 0}
    assert "screen_kind" in text


def test_parse_message_empty_thinking_is_unparsed() -> None:
    from avatar.models import _parse_message

    text, parsed = _parse_message(
        _FakeMessage(content="", reasoning="The board has two tiles...")
    )
    assert parsed is None
    assert "two tiles" in text


def test_parse_act_coerces_string_actions() -> None:
    from avatar.experiments.common import parse_act

    act = parse_act({"actions": ["swipe_right", {"name": "swipe_left"}], "invoke_gsm": "place_piece"})
    assert [step.name for step in act.actions] == ["swipe_right", "swipe_left"]
    assert act.invoke_gsm == "place_piece"


def test_parse_act_rejects_see_shaped_payload() -> None:
    from avatar.experiments.common import parse_act

    act = parse_act(
        {
            "screen_kind": "ui",
            "board": [[None, 2], [2, None]],
            "score": 0,
            "status": "none",
        }
    )
    assert act.actions == []
    assert "see-shaped" in act.reasoning


def test_parse_message_prefers_act_json_over_echoed_see() -> None:
    from avatar.models import _parse_message

    thinking = (
        'See JSON: {"screen_kind":"ui","board":[[2]],"score":0}\n'
        'Final: {"actions":[{"name":"swipe_down"}],"reasoning":"pack corner"}'
    )
    text, parsed = _parse_message(_FakeMessage(content="", reasoning=thinking))
    assert parsed is not None
    assert parsed["actions"][0]["name"] == "swipe_down"
    assert "swipe_down" in text


def test_ollama_chat_payload_disables_think_for_json() -> None:
    from avatar.models import ollama_chat_payload, to_ollama_messages

    messages = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abcd"}},
            ],
        },
    ]
    native = to_ollama_messages(messages)
    assert native[1]["images"] == ["abcd"]
    payload = ollama_chat_payload(
        model="qwen3.5:4b-instruct-q4_K_M",
        messages=messages,
        temperature=0.7,
        max_tokens=1024,
        num_ctx=2048,
        keep_alive="5m",
        json_mode=True,
    )
    assert payload["think"] is False
    assert payload["format"] == "json"
    assert payload["options"]["num_ctx"] == 2048
    assert payload["options"]["num_predict"] == 1024


def test_parse_reflect_nested_verdict_dict() -> None:
    from avatar.eval.reflect import parse_reflect

    result = parse_reflect(
        {
            "visual": "board visible",
            "verdict": {"status": "SUCCESS"},
            "task_design": "ok",
            "next_step": "retry",
        }
    )
    assert result.verdict == "success"
    assert result.visual == "board visible"


def test_parse_reflect_wrapped_reflection() -> None:
    from avatar.eval.reflect import parse_reflect

    result = parse_reflect(
        {
            "reflection": {
                "visual": "gameplay",
                "verdict": "failure",
                "task_design": "clear",
                "action_plan": "weak",
                "execution": "timed out",
                "qa_notes": [],
                "next_step": "slow down",
            }
        }
    )
    assert result.verdict == "failure"
    assert result.visual == "gameplay"
    assert result.action_plan == "weak"
    assert result.next_step == "slow down"


def test_parse_reflect_qa_notes_strings_and_would_file() -> None:
    from avatar.eval.reflect import parse_reflect

    result = parse_reflect(
        {
            "verdict": "partial",
            "qa_notes": [
                "ghost piece missing",
                {"bug": "overlap", "would_file": "yes", "evidence": "pixels"},
            ],
        }
    )
    assert result.verdict == "partial"
    assert [note.title for note in result.qa_notes] == ["ghost piece missing", "overlap"]
    assert result.qa_notes[0].would_file is False
    assert result.qa_notes[1].would_file is True
    assert result.qa_notes[1].evidence == "pixels"


def test_parse_reflect_valid_payload_unchanged() -> None:
    from avatar.eval.reflect import parse_reflect

    result = parse_reflect(
        {
            "visual": "gameplay",
            "verdict": "success",
            "task_design": "clear",
            "action_plan": "ok",
            "execution": "ok",
            "qa_notes": [{"title": "none", "would_file": False, "evidence": "n/a"}],
            "next_step": "ship",
        }
    )
    assert result.verdict == "success"
    assert result.visual == "gameplay"
    assert result.task_design == "clear"
    assert result.action_plan == "ok"
    assert result.execution == "ok"
    assert result.next_step == "ship"
    assert result.qa_notes[0].title == "none"
    assert result.qa_notes[0].would_file is False
    assert result.qa_notes[0].evidence == "n/a"


def test_parse_reflect_unparseable_error() -> None:
    from avatar.eval.reflect import parse_reflect

    empty = parse_reflect(None)
    assert empty.verdict == "failure"
    assert empty.visual == "unparseable"
    errored = parse_reflect({"error": "timeout"})
    assert errored.verdict == "failure"
    assert errored.visual == "unparseable"
