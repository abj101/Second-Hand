from __future__ import annotations

import httpx
import pytest

from loop.model.gateway import VlmGateway


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
