import base64, json

import pytest
from PIL import Image

import agent.model as model_module
from agent.model import ModelCancelled, ModelUnavailable, Ollama, _b64, load_image_ref


class StubResponse:
    def __init__(self, status=200, content='{"ok":true}', headers=None):
        self.status_code, self.text, self.headers = status, content, headers or {}

    def json(self):
        return {"message": {"content": self.text}, "prompt_eval_count": 3, "eval_count": 2}


def test_cancelled_model_drops_late_response_and_has_no_side_effects(tmp_path, monkeypatch):
    from threading import Event
    cancelled, logs, posts = Event(), [], []
    client = Ollama("stub", cancel_event=cancelled, cache_dir=tmp_path / "cache", image_dir=tmp_path / "images", log=logs.append)
    def post(payload):
        posts.append(payload); cancelled.set()
        return StubResponse()
    monkeypatch.setattr(client, "_post", post)
    with pytest.raises(ModelCancelled): client.ask("s", "u", [Image.new("RGB", (2, 2))])
    with pytest.raises(ModelCancelled): client.ask("s", "u2")
    assert len(posts) == 1 and client.calls == 0 and not logs
    assert not list(tmp_path.iterdir())


def test_cancel_interrupts_retry_backoff_without_another_request(monkeypatch):
    from threading import Event, Thread
    cancelled, waiting, stopped, errors = Event(), Event(), Event(), []
    class ObservedEvent:
        def is_set(self): return cancelled.is_set()
        def wait(self, seconds): waiting.set(); return cancelled.wait(seconds)
    client = Ollama("stub", cancel_event=ObservedEvent())
    posts = []
    monkeypatch.setattr(client, "_post", lambda p: (posts.append(p) or StubResponse(429)))
    def ask():
        try: client.ask("s", "u")
        except ModelCancelled as error: errors.append(error)
        finally: stopped.set()
    worker = Thread(target=ask, daemon=True); worker.start()
    assert waiting.wait(1)
    cancelled.set()
    assert stopped.wait(1)
    worker.join(1)
    assert len(posts) == 1 and len(errors) == 1


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
    assert len(logs) == 1
    assert {key: logs[0][key] for key in ("role", "model", "prompt_tokens", "output_tokens", "retries", "ok", "cached")} == \
        {"role": "perceiver", "model": "stub", "prompt_tokens": 3, "output_tokens": 2, "retries": 1, "ok": True, "cached": False}


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


SECRET = "sentinel-secret-7f3a"


def _sink(path):
    def write(row):
        with path.open("a", encoding="utf-8") as stream: stream.write(json.dumps(row) + "\n")
    return write


def test_call_record_for_success_and_no_schema(tmp_path, monkeypatch):
    rows = []
    client = Ollama("stub", log=rows.append, cache_dir=tmp_path / "cache", image_dir=tmp_path / "images")
    monkeypatch.setattr(client, "_post", lambda payload: StubResponse())
    image, schema = Image.new("RGB", (2, 3), "red"), {"type": "object"}
    reply = client.ask("sys", "usr", [image], schema, role="act")
    client.ask("sys", "usr2", role="skill")
    first, second = rows

    assert (first["call"], second["call"]) == (1, 2)
    assert first["role"] == "act" and first["model"] == "stub" and first["ok"] is True and first["error"] is None
    assert (first["system"], first["user"], first["schema"]) == ("sys", "usr", schema)
    assert first["response"] == reply.text == '{"ok":true}'
    assert first["key"] == client._key("sys", "usr", [image], schema)
    assert first["cache"] == "miss" and first["posts"] == 1 and first["retries"] == 0
    assert first["params"]["format"] == "schema" and first["params"]["think"] is False
    assert first["params"]["keep_alive"] == "10m" and first["params"]["num_predict"] == 4096
    sha = model_module._image_sha(image)
    assert first["images"] == [{"sha": sha, "mode": "RGB", "size": [2, 3], "path": f"{sha}.png"}]
    assert second["params"]["format"] == "json" and second["schema"] is None and second["images"] == []


def test_call_record_counts_posts_and_retries(monkeypatch):
    rows = []
    monkeypatch.setattr(model_module.time, "sleep", lambda _: None)
    client = Ollama("stub", log=rows.append)
    responses = iter([StubResponse(500), StubResponse()])
    monkeypatch.setattr(client, "_post", lambda payload: next(responses))

    client.ask("s", "u")

    assert rows[0]["posts"] == 2 and rows[0]["retries"] == 1 and rows[0]["ok"] is True


def test_call_record_for_malformed_reply(monkeypatch):
    rows = []
    client = Ollama("stub", log=rows.append)
    monkeypatch.setattr(client, "_post", lambda payload: StubResponse(content="not json"))

    reply = client.ask("s", "u")

    assert reply.data is None and reply.text == "not json"
    assert rows[0]["ok"] is False and rows[0]["response"] == "not json" and rows[0]["error"]["type"] == "MalformedReply"


def test_call_record_for_unavailable_model(monkeypatch):
    rows = []
    monkeypatch.setattr(model_module, "MAX_WAIT_S", 5)
    monkeypatch.setattr(model_module.time, "sleep", lambda _: None)
    client = Ollama("stub", log=rows.append)
    monkeypatch.setattr(client, "_post", lambda payload: StubResponse(429))

    with pytest.raises(ModelUnavailable):
        client.ask("s", "u", role="plan")

    row = rows[0]
    assert row["error"]["type"] == "ModelUnavailable" and row["ok"] is False and row["response"] is None
    assert row["posts"] == 2 and row["retries"] == 1 and row["role"] == "plan"


def test_call_record_for_interrupted_call(monkeypatch):
    rows = []
    client = Ollama("stub", log=rows.append)
    def interrupt(payload): raise KeyboardInterrupt
    monkeypatch.setattr(client, "_post", interrupt)

    with pytest.raises(KeyboardInterrupt):
        client.ask("s", "u")

    assert rows[0]["error"]["type"] == "KeyboardInterrupt" and rows[0]["ok"] is False and rows[0]["posts"] == 1


def test_call_record_for_cache_hit(tmp_path, monkeypatch):
    rows, sent = [], []
    client = Ollama("stub", cache_dir=tmp_path, log=rows.append)
    monkeypatch.setattr(client, "_post", lambda payload: (sent.append(payload) or StubResponse()))

    first = client.ask("s", "u", role="act")
    again = client.ask("s", "u", role="act")

    assert len(sent) == 1 and again.text == first.text
    hit = rows[1]
    assert hit["cache"] == "hit" and hit["posts"] == 0 and hit["cached"] is True and hit["response"] == first.text
    assert hit["key"] == rows[0]["key"] and hit["call"] == 2


def test_logging_leaves_keys_posts_and_calls_unchanged(tmp_path, monkeypatch):
    image, results = Image.new("RGB", (1, 1), "red"), []
    for name, log in (("plain", None), ("logged", lambda row: None)):
        cache = tmp_path / name
        client = Ollama("stub", cache_dir=cache, log=log)
        sent = []
        monkeypatch.setattr(client, "_post", lambda payload, sent=sent: (sent.append(payload) or StubResponse()))
        client.ask("s", "u", [image], {"type": "object"})
        client.ask("s", "u", [image], {"type": "object"})
        results.append((client.calls, len(sent), sorted(p.name for p in cache.iterdir())))

    key = Ollama("stub")._key("s", "u", [image], {"type": "object"})
    assert results[0] == results[1] == (1, 1, [f"{key}.json"])


def test_unwritable_log_does_not_fail_the_call(monkeypatch, capsys):
    def broken(row): raise OSError("disk full")
    client = Ollama("stub", log=broken)
    monkeypatch.setattr(client, "_post", lambda payload: StubResponse())

    assert client.ask("s", "u").data == {"ok": True}
    assert client.ask("s", "u2").data == {"ok": True}

    assert client.log_failed is True and client.calls == 2
    assert capsys.readouterr().err.count("call log write failed") == 1


def test_image_refs_dedupe_resolve_and_verify(tmp_path, monkeypatch):
    rows, images_dir = [], tmp_path / "images"
    client = Ollama("stub", log=rows.append, image_dir=images_dir)
    monkeypatch.setattr(client, "_post", lambda payload: StubResponse())
    red = Image.new("RGB", (2, 2), "red")

    client.ask("s", "u", [red, red.copy()], role="perceiver")
    client.ask("s", "u2", [red], role="perceiver")

    assert len(list(images_dir.glob("*.png"))) == 1
    ref = rows[0]["images"][0]
    assert rows[0]["images"][1] == ref == rows[1]["images"][0] and ref["path"] == f"{ref['sha']}.png"
    loaded = load_image_ref(images_dir, ref)
    assert loaded.mode == "RGB" and loaded.tobytes() == red.tobytes()
    assert _b64(red) not in json.dumps(rows)

    Image.new("RGB", (2, 2), "blue").save(images_dir / ref["path"])
    with pytest.raises(ValueError, match="does not match"): load_image_ref(images_dir, ref)
    (images_dir / ref["path"]).write_bytes(b"junk")
    with pytest.raises(ValueError, match="not a readable image"): load_image_ref(images_dir, ref)


def test_secrets_stay_out_of_call_records_and_image_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", f"http://user:{SECRET}@127.0.0.1:9")
    calls = tmp_path / "calls.jsonl"
    client = Ollama("stub", log=_sink(calls), cache_dir=tmp_path / "cache", image_dir=tmp_path / "images")
    monkeypatch.setattr(client, "_post", lambda payload: StubResponse(headers={"Authorization": f"Bearer {SECRET}", "X-Trace": SECRET}))

    client.ask("s", "u", [Image.new("RGB", (2, 2), "red")], role="act")

    assert SECRET not in calls.read_text(encoding="utf-8")
    assert all(SECRET.encode() not in path.read_bytes() for path in (tmp_path / "images").iterdir())
