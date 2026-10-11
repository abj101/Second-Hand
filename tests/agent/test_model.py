import base64

import pytest
from PIL import Image

import agent.model as model_module
from agent.model import ModelUnavailable, Ollama


class StubResponse:
    def __init__(self, status=200, content='{"ok":true}', headers=None):
        self.status_code, self.text, self.headers = status, content, headers or {}

    def json(self):
        return {"message": {"content": self.text}, "prompt_eval_count": 3, "eval_count": 2}


def test_retry_logging_and_small_png(monkeypatch):
    sleeps, logs, sent = [], [], []
    monkeypatch.setattr(model_module.time, "sleep", sleeps.append)
    client = Ollama("stub", log=logs.append)
    responses = iter([StubResponse(429, headers={"Retry-After": "7"}), StubResponse()])
    def post(payload):
        sent.append(payload)
        return next(responses)
    monkeypatch.setattr(client, "_post", post)

    reply = client.ask("system", "user", [Image.new("RGB", (2, 2))], role="perceiver")

    assert reply.data == {"ok": True} and client.calls == 1
    assert sleeps == [7.0] and len(sent) == 2
    assert base64.b64decode(sent[0]["messages"][1]["images"][0]).startswith(b"\x89PNG")
    assert logs == [{"role": "perceiver", "model": "stub", "ms": logs[0]["ms"],
                     "prompt_tokens": 3, "output_tokens": 2, "retries": 1, "ok": True, "cached": False}]


def test_retry_after_wait_is_capped_and_timeout_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(model_module.time, "sleep", sleeps.append)
    client = Ollama("stub")
    responses = iter([StubResponse(429, headers={"Retry-After": "1000"}), StubResponse()])
    monkeypatch.setattr(client, "_post", lambda _: next(responses))

    assert client.ask("s", "u").data == {"ok": True}
    assert sleeps == [300]

    errors = iter([model_module.httpx.ConnectTimeout("timeout"), StubResponse()])
    def post(_):
        result = next(errors)
        if isinstance(result, Exception): raise result
        return result
    monkeypatch.setattr(client, "_post", post)
    assert client.ask("s", "u2").data == {"ok": True}
    assert sleeps == [300, 5]


def test_retry_cap_logs_failure(monkeypatch):
    logs = []
    monkeypatch.setattr(model_module, "MAX_WAIT_S", 5)
    monkeypatch.setattr(model_module.time, "sleep", lambda _: None)
    client = Ollama("stub", log=logs.append)
    monkeypatch.setattr(client, "_post", lambda _: StubResponse(429))

    with pytest.raises(ModelUnavailable):
        client.ask("s", "u")

    assert client.calls == 0
    assert len(logs) == 1 and logs[0]["ok"] is False and logs[0]["retries"] == 1


def test_cache_hits_and_image_bytes_change_key(tmp_path, monkeypatch):
    logs, sent = [], []
    client = Ollama("stub", cache_dir=tmp_path, log=logs.append)
    monkeypatch.setattr(client, "_post", lambda payload: (sent.append(payload) or StubResponse()))
    first = Image.new("RGB", (1, 1), "red")
    client.ask("s", "u", [first])
    client.ask("s", "u", [first.copy()])
    client.ask("s", "u", [Image.new("RGB", (1, 1), "blue")])

    assert len(sent) == 2 and client.calls == 2
    assert [row["cached"] for row in logs] == [False, True, False]


def test_none_data_is_not_cached(tmp_path, monkeypatch):
    client = Ollama("stub", cache_dir=tmp_path)
    sent = []
    monkeypatch.setattr(client, "_post", lambda payload: (sent.append(payload) or StubResponse(content="no json")))

    assert client.ask("s", "u").data is None
    assert client.ask("s", "u").data is None
    assert len(sent) == 2 and client.calls == 2
