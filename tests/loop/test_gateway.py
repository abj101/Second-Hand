from __future__ import annotations

import httpx
import pytest

from loop.model.gateway import EpisodeThread, VlmGateway
from loop.os.capture import EncodedFrame


def _response(payload: dict[str, object]) -> httpx.Response:
    request = httpx.Request("POST", "http://localhost:11434/api/chat")
    return httpx.Response(200, json=payload, request=request)


def test_complete_text_accepts_per_call_timeout_and_collects_ollama_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        del args
        seen.append(kwargs["timeout"])
        return _response({
            "message": {"content": '{"ok":true}'}, "prompt_eval_count": 24, "eval_count": 7,
            "load_duration": 11, "prompt_eval_duration": 22, "eval_duration": 33, "total_duration": 44,
        })

    monkeypatch.setattr(httpx, "post", fake_post)
    call = VlmGateway().complete_text(system="s", user="u", timeout_s=2.5)
    assert isinstance(seen[0], httpx.Timeout)
    assert seen[0].read == 2.5  # type: ignore[union-attr]
    assert call.parsed == {"ok": True}
    assert call.prompt_tokens == 24
    assert call.output_tokens == 7
    assert call.load_duration_ns == 11
    assert call.prompt_eval_duration_ns == 22
    assert call.eval_duration_ns == 33
    assert call.total_duration_ns == 44
    assert call.image_supplied is False
    assert call.metrics["prompt_tokens"] == 24


def test_complete_text_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        VlmGateway().complete_text(system="s", user="u", timeout_s=0)


def test_complete_text_without_history_is_system_plus_user(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[dict[str, object]]] = []

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        del args
        seen.append(kwargs["json"]["messages"])  # type: ignore[index]
        return _response({"message": {"content": '{"ok":true}'}})

    monkeypatch.setattr(httpx, "post", fake_post)
    VlmGateway().complete_text(system="s", user="u")
    assert seen[0] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_complete_json_history_attaches_images_only_to_current_user(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[dict[str, object]]] = []

    def fake_post(*args: object, **kwargs: object) -> httpx.Response:
        del args
        seen.append(kwargs["json"]["messages"])  # type: ignore[index]
        return _response({"message": {"content": '{"ok":true}'}})

    monkeypatch.setattr(httpx, "post", fake_post)
    frame = EncodedFrame(
        data=b"abc", mime="image/jpeg", width=1, height=1,
        jpeg_quality=90, longest_side=1, original_width=1, original_height=1,
    )
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": '{"ok":true}'},
    ]
    VlmGateway().complete_json(system="s", user="now", frames=[frame], history=history)
    messages = seen[0]
    assert messages[0] == {"role": "system", "content": "s"}
    assert messages[1] == {"role": "user", "content": "old"}
    assert messages[2] == {"role": "assistant", "content": '{"ok":true}'}
    current = messages[-1]
    assert current["role"] == "user"
    assert current["content"] == "now"
    assert current.get("images") == ["YWJj"]
    assert "images" not in messages[1]


def test_episode_thread_compacts_older_turns_and_reports_telemetry() -> None:
    thread = EpisodeThread(recent=2, max_chars=10_000)
    for index in range(4):
        thread.commit(f"u{index}", '{"i":%d}' % index)
    stats = thread.telemetry()
    assert stats["turn_count"] == 4
    assert stats["compaction_count"] >= 1
    assert stats["context_chars"] == thread.context_chars()
    assert stats["sent_chars"] == 0
    contents = [row["content"] for row in thread.messages()]
    assert any(row.startswith("Compacted prior accepted state: ") for row in contents)
    assert '{"i":3}' in contents
    assert thread.telemetry()["sent_chars"] == thread.context_chars()
    thread.reset()
    assert thread.telemetry() == {
        "turn_count": 0, "context_chars": 0, "sent_chars": 0, "compaction_count": 0,
    }


def test_episode_thread_total_input_budget_reports_sent_history() -> None:
    thread = EpisodeThread(recent=3, max_chars=40)
    thread.commit("llm1", '{"n":0,"pad":"' + ("x" * 20) + '"}')
    thread.commit("llm1", '{"n":1}')
    assert thread.messages(system="s" * 25, user="u" * 20) == []
    assert thread.telemetry()["sent_chars"] == 0
    replay = thread.messages()
    assert replay
    assert thread.telemetry()["sent_chars"] <= 40
    assert thread.telemetry()["sent_chars"] == sum(len(row["content"]) for row in replay)
    assert any('{"n":1}' in row["content"] for row in replay)


def test_stage_call_metrics_reports_utilization() -> None:
    from loop.model.gateway import ModelCall, stage_call_metrics

    call = ModelCall("{}", {}, 3.0, "fake", prompt_tokens=1536, output_tokens=12)
    row = stage_call_metrics(call, num_ctx=2048)
    assert row["prompt_tokens"] == 1536
    assert row["output_tokens"] == 12
    assert row["latency_ms"] == 3.0
    assert row["num_ctx"] == 2048
    assert row["utilization"] == 0.75
    assert stage_call_metrics(None, num_ctx=2048)["utilization"] is None
