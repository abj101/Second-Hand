"""Run provenance: modes on resume, invocation manifest, source fingerprint, rebuildable calls."""
import json, shutil, subprocess
from pathlib import Path

import pytest
from PIL import Image

from agent.loop import Loop
from agent.memory import Memory
from agent.model import Ollama, _b64, load_image_ref
from scripts import run_agent
from tests.agent.fakes import GridEnv, StubModel
from scripts.run_agent import append_manifest, flag_manifest, manifest_record, source_fingerprint

MANIFEST_KEYS = {"invocation", "t", "resume", "start_checkpoint", "from_step", "steps", "max_calls", "stage",
                 "stage_b_cap", "model", "modes", "dirty", "source", "provenance", "notes"}


class _Response:
    status_code, headers = 200, {}
    def __init__(self, text): self.text = text
    def json(self): return {"message": {"content": self.text}, "prompt_eval_count": 1, "eval_count": 1}


def _append_jsonl(path, row):
    with path.open("a", encoding="utf-8") as stream: stream.write(json.dumps(row) + "\n")


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, check=True, text=True)


def test_removed_mode_flag_exits_before_any_input_or_env(tmp_path, monkeypatch):
    run = tmp_path / "run"; checkpoint = run / "checkpoint" / "000005"; checkpoint.mkdir(parents=True)
    (checkpoint / "harness.json").write_text(json.dumps({"modes": {"decide": True, "act_text": False}}))
    (run / "trace.jsonl").write_text('{"step": 5}\n')
    def no_env(*args, **kwargs): raise AssertionError("env must not start")
    monkeypatch.setattr(run_agent, "PyBoyEnv", no_env)

    with pytest.raises(SystemExit):
        run_agent.main(["--resume", "--steps", "1", "--max-calls", "1", "--run", str(run), "--no-decide"])

    assert (run / "trace.jsonl").read_text() == '{"step": 5}\n' and not (run / "manifest.jsonl").exists()


def test_manifest_has_listed_keys_and_one_line_per_invocation(tmp_path):
    source = {"head": "abc", "dirty": {"agent/x.py": "00"}, "notes": []}
    record = manifest_record(resume=False, start_checkpoint=None, from_step=None, steps=3, max_calls=9, stage="a",
                             stage_b_cap=180, model="m", modes={"headless": True}, source=source)
    first = append_manifest(tmp_path, record); second = append_manifest(tmp_path, record)

    lines = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["invocation"] for line in lines] == [1, 2] == [first["invocation"], second["invocation"]]
    assert set(lines[0]) == MANIFEST_KEYS
    assert lines[0]["source"] == {"head": "abc", "dirty": {"agent/x.py": "00"}} and lines[0]["provenance"] == "complete"
    assert lines[0]["modes"] == {"headless": True} and lines[0]["dirty"] is True
    clean = manifest_record(resume=False, start_checkpoint=None, from_step=None, steps=1, max_calls=1, stage=None, stage_b_cap=180,
                            model="m", modes={"headless": False}, source={**source, "dirty": {"runs/x": "skipped"}})
    assert clean["dirty"] is False
    assert manifest_record(resume=True, start_checkpoint="000005", from_step=5, steps=1, max_calls=1, stage=None, stage_b_cap=180,
                           model="m", modes={"headless": False},
                           source={**source, "notes": ["git unavailable"]})["provenance"] == "incomplete"

    flag_manifest(tmp_path, 2, "calls.jsonl write failed")
    lines = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lines[0]["provenance"] == "complete"
    assert lines[1]["provenance"] == "incomplete" and lines[1]["notes"] == ["calls.jsonl write failed"]


def test_source_fingerprint_without_git_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))  # a basetemp inside a repo must not count as git
    fingerprint = source_fingerprint(tmp_path)

    assert fingerprint["head"] is None and fingerprint["dirty"] == {}
    assert fingerprint["notes"] and fingerprint["notes"][0].startswith("git unavailable")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_source_fingerprint_hashes_dirty_content(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "tracked.py").write_text("a = 1\n"); _git(tmp_path, "add", "tracked.py"); _git(tmp_path, "commit", "-q", "-m", "init")
    clean = source_fingerprint(tmp_path)
    assert clean["head"] and clean["dirty"] == {} and clean["notes"] == []

    (tmp_path / "tracked.py").write_text("a = 2\n")
    first = source_fingerprint(tmp_path)["dirty"]["tracked.py"]
    (tmp_path / "tracked.py").write_text("a = 3\n")
    assert source_fingerprint(tmp_path)["dirty"]["tracked.py"] != first

    (tmp_path / "new").mkdir(); (tmp_path / "new" / "x.txt").write_text("hi")
    (tmp_path / "runs").mkdir(); (tmp_path / "runs" / "out.txt").write_text("r")
    dirty = source_fingerprint(tmp_path)["dirty"]
    assert len(dirty["new/x.txt"]) == 64 and dirty["runs/out.txt"] == "skipped"


def test_call_is_rebuilt_from_manifest_call_record_and_image_refs(tmp_path, monkeypatch):
    run = tmp_path / "run"; run.mkdir()
    sent = []
    client = Ollama("stub", log=lambda row: _append_jsonl(run / "calls.jsonl", row), image_dir=run / "images")
    monkeypatch.setattr(client, "_post", lambda payload: (sent.append(payload) or _Response('{"decision": "go"}')))
    images = [Image.new("RGB", (3, 2), "red"), Image.new("L", (4, 4), 7)]
    schema = {"type": "object", "properties": {"decision": {"type": "string"}}}
    system, user = "You decide.\n", "Frame: é"
    reply = client.ask(system, user, images, schema, role="act")
    append_manifest(run, manifest_record(resume=False, start_checkpoint=None, from_step=None, steps=1, max_calls=1, stage=None,
                                         stage_b_cap=180, model="stub", modes={"headless": True},
                                         source={"head": "h", "dirty": {}, "notes": []}))

    # Rebuild from only the manifest line, the call record and the image refs.
    manifest_line = json.loads((run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    record = json.loads((run / "calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
    rebuilt = [load_image_ref(run / "images", ref) for ref in record["images"]]

    assert manifest_line["provenance"] == "complete" and manifest_line["model"] == record["model"]
    assert record["system"] == system and record["user"] == user and record["schema"] == schema
    assert record["key"] == Ollama(record["model"])._key(record["system"], record["user"], rebuilt, record["schema"])
    for original, copy in zip(images, rebuilt):
        assert (copy.mode, copy.size, copy.tobytes()) == (original.mode, original.size, original.tobytes())
    payload = sent[0]
    assert payload["messages"][0]["content"] == record["system"]
    assert payload["messages"][1]["content"] == record["user"]
    assert payload["messages"][1]["images"] == [_b64(image) for image in rebuilt]
    assert payload["format"] == record["schema"]
    assert payload["options"] == {key: value for key, value in record["params"].items() if key not in ("format", "think", "keep_alive")}
    assert record["response"] == reply.text


PERCEPTION = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [0.5, 0.5]}]}
PLAN = {"goals": [], "focus": "", "why": "nothing new"}
ACTION = {"place": "", "notebook": [], "action": None, "text": None, "expect": "screen_changes", "why": "test"}


def _record(**overrides):
    fields = {"resume": False, "start_checkpoint": None, "from_step": None, "steps": 1, "max_calls": 5, "stage": None, "stage_b_cap": 180,
              "model": "stub", "modes": {"headless": False}, "source": {"head": "h", "dirty": {}, "notes": []}, **overrides}
    return manifest_record(**fields)


def test_one_step_is_rebuilt_from_run_files_alone(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    sent, returned = [], []
    def post(payload):
        properties = payload["format"].get("properties", {}) if isinstance(payload["format"], dict) else {}
        body = PERCEPTION if "screen" in properties else PLAN if "goals" in properties else ACTION
        text = json.dumps(body); sent.append(payload); returned.append(text)
        return _Response(text)
    model = Ollama("stub", cache_dir=tmp_path / "cache", image_dir=run / "images", log=lambda row: _append_jsonl(run / "calls.jsonl", row))
    model._post = post
    loop = Loop(GridEnv(), model, Memory(run / "memory"), "grid")
    loop.invocation = append_manifest(run, _record())["invocation"]

    loop.step()

    manifest = json.loads((run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    row = json.loads((run / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])
    records = {r["call"]: r for r in (json.loads(line) for line in (run / "calls.jsonl").read_text(encoding="utf-8").splitlines())}
    assert manifest["provenance"] == "complete" and row["invocation"] == manifest["invocation"] == 1
    assert row["call_ids"] == list(range(1, model.seq + 1)) == sorted(records) and len(sent) == model.seq
    for index, call_id in enumerate(row["call_ids"]):
        record = records[call_id]
        images = [load_image_ref(run / "images", ref) for ref in record["images"]]
        assert Ollama(record["model"])._key(record["system"], record["user"], images, record["schema"]) == record["key"]
        assert [(image.mode, list(image.size)) for image in images] == [(ref["mode"], ref["size"]) for ref in record["images"]]
        payload = sent[index]
        assert payload["messages"][0]["content"] == record["system"] and payload["messages"][1]["content"] == record["user"]
        assert payload["messages"][1]["images"] == [_b64(image) for image in images] and payload["format"] == record["schema"]
        assert record["response"] == returned[index]


def test_checkpoint_provenance_follows_the_manifest_and_the_loop(tmp_path):
    class Saved(GridEnv):
        def save(self, path): Path(path).write_text("state")
    run = tmp_path / "run"; run.mkdir(); env = Saved()
    loop = Loop(env, Ollama("stub"), Memory(run / "memory"), "grid")
    loop.invocation = append_manifest(run, _record())["invocation"]
    def provenance(): return json.loads((run_agent._checkpoint(env, loop, run) / "harness.json").read_text(encoding="utf-8"))["provenance"]

    assert provenance() == {"invocation": 1, "manifest": "manifest.jsonl", "last_call": 0, "incomplete": False}
    flag_manifest(run, 1, "calls.jsonl write failed")
    assert provenance()["incomplete"] is True

    legacy = Loop(env, StubModel([]), Memory(run / "memory"), "grid"); legacy.invocation = 2; legacy.restore({"step": 0})
    root = run_agent._checkpoint(env, legacy, run); state = json.loads((root / "harness.json").read_text(encoding="utf-8"))
    assert "modes" not in state and state["provenance"]["incomplete"] is True


def _spy_runner(monkeypatch):
    built = []
    class Saved(GridEnv):
        def save(self, path): Path(path).write_text("state")
        def close(self): pass
    class Spy(Loop):
        def __init__(self, *args, **kwargs): super().__init__(*args, **kwargs); built.append((kwargs, self))
        def step(self): self.step_no += 1; return {"step": self.step_no, "place": "", "task": "", "choice": {}, "verdict": ""}
    model = StubModel([]); model._post = lambda payload: None
    monkeypatch.setattr(run_agent, "PyBoyEnv", lambda *a, **k: Saved()); monkeypatch.setattr(run_agent, "find_rom", lambda: "rom")
    monkeypatch.setattr(run_agent, "read", lambda env: {}); monkeypatch.setattr(run_agent, "_ledger", lambda *a: None)
    monkeypatch.setattr(run_agent, "Loop", Spy); monkeypatch.setattr(run_agent, "Ollama", lambda *a, **k: model)
    return built, Saved


def test_resume_ignores_checkpoint_modes_and_records_only_runtime_mode(tmp_path, monkeypatch):
    built, Saved = _spy_runner(monkeypatch)
    run = tmp_path / "run"
    loop = Loop(GridEnv(), StubModel([]), Memory(run / "memory"), "grid")
    run_agent._checkpoint(Saved(), loop, run)
    harness = run / "checkpoint" / "000000" / "harness.json"
    state = json.loads(harness.read_text(encoding="utf-8")); state["modes"] = {"decide": True, "act_text": True}
    harness.write_text(json.dumps(state), encoding="utf-8")

    assert run_agent.main(["--headless", "--resume", "--steps", "1", "--max-calls", "5", "--run", str(run)]) == 0

    kwargs, loop = built[-1]
    assert kwargs == {"looks": Path("runs/looks/pokemon_red.jsonl")} and loop.provenance_incomplete is False and loop.invocation == 1
    manifest = json.loads((run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert manifest["modes"] == {"headless": True} and "decide" not in manifest["modes"] and "act_text" not in manifest["modes"] and manifest["provenance"] == "complete"


def test_resume_from_a_checkpoint_without_modes_is_incomplete_everywhere(tmp_path, monkeypatch):
    built, Saved = _spy_runner(monkeypatch)
    run = tmp_path / "run"
    root = run_agent._checkpoint(Saved(), Loop(GridEnv(), StubModel([]), Memory(run / "memory"), "grid"), run)
    state = json.loads((root / "harness.json").read_text(encoding="utf-8")); state.pop("modes", None); state.pop("provenance"); state.pop("provenance_incomplete")
    (root / "harness.json").write_text(json.dumps(state), encoding="utf-8")

    assert run_agent.main(["--headless", "--resume", "--steps", "1", "--max-calls", "5", "--run", str(run)]) == 0

    _, loop = built[-1]
    assert loop.provenance_incomplete is True
    manifest = json.loads((run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert manifest["provenance"] == "incomplete" and any("legacy" in note for note in manifest["notes"])
    harness = json.loads((run / "checkpoint" / "000001" / "harness.json").read_text(encoding="utf-8"))
    assert "modes" not in harness and harness["provenance"]["incomplete"] is True


def test_live_checkpoint_records_state_and_observation_frames_separately(tmp_path):
    from types import SimpleNamespace
    frame = SimpleNamespace(generation="new", frame_seq=70, captured_monotonic=12.0)
    class Saved(GridEnv):
        def save_snapshot(self): return SimpleNamespace(frame=frame, state=b"serialized")
        def save(self, path): raise AssertionError("live capture must be one atomic request")
    run = tmp_path / "run"; env = Saved()
    loop = Loop(env, StubModel([]), Memory(run / "memory"), "grid")
    loop.observation_stamp = {"generation": "new", "frame_seq": 40, "captured_monotonic": 11.5}
    root = run_agent._checkpoint(env, loop, run)
    state = json.loads((root / "harness.json").read_text())
    assert (root / "pyboy.state").read_bytes() == b"serialized"
    assert state["runtime"]["state_frame_seq"] == 70
    assert state["runtime"]["observation"]["frame_seq"] == 40


def test_truth_uses_one_coherent_ram_batch():
    from types import SimpleNamespace
    from scripts.truth_pokered import read
    batches = []
    class Env:
        def ram(self, address): raise AssertionError("must not read a moving emulator byte by byte")
        def read_ram(self, addresses):
            batches.append(addresses)
            return SimpleNamespace(values=dict.fromkeys(addresses, 0), frame=SimpleNamespace(generation="g", frame_seq=17, captured_monotonic=1.0))
    truth = read(Env())
    assert len(batches) == 1 and truth["frame_seq"] == 17 and truth["generation"] == "g"
    assert truth["x"] == 0 and len(truth["sprites"]) == 0


def test_continuous_runner_keeps_owner_on_caller_and_work_on_worker():
    from threading import Event, get_ident
    from types import SimpleNamespace
    owner_id, calls = get_ident(), []
    class Host:
        env = SimpleNamespace()
        def __init__(self): self.stopped = Event()
        def run(self, publish):
            calls.append(("owner", get_ident())); self.stopped.wait(2)
        def stop(self): self.stopped.set()
    class Publisher:
        def publish(self, frame): pass
        def close(self, **kwargs): calls.append(("closed", get_ident()))
    def work(env, cancel): calls.append(("work", get_ident())); return 3
    assert run_agent.run_continuous(Host(), Publisher(), work) == 3
    assert dict(calls)["owner"] == dict(calls)["closed"] == owner_id
    assert dict(calls)["work"] != owner_id


def test_continuous_runner_cancels_waiting_model_on_window_close():
    from threading import Event
    entered, ended = Event(), Event()
    class Host:
        env = object()
        def run(self, publish): assert entered.wait(1)  # native close
        def stop(self): pass
    class Publisher:
        def publish(self, frame): pass
        def close(self, **kwargs): pass
    def work(env, cancel):
        entered.set(); assert cancel.wait(2); ended.set(); return 0
    assert run_agent.run_continuous(Host(), Publisher(), work) == 0
    assert ended.is_set()


def test_normal_runner_selects_visible_owner_and_records_runtime_mode(tmp_path, monkeypatch):
    from threading import Event, get_ident
    from types import SimpleNamespace
    import agent.env_pyboy as env_module
    import scripts.watch_run as viewer
    main_thread, calls = get_ident(), []
    class Host:
        def __init__(self, *args, **kwargs):
            calls.append(("constructed", get_ident())); self.env = SimpleNamespace(); self.stopped = Event()
        def run(self, publish):
            calls.append(("owner", get_ident())); assert self.stopped.wait(2)
        def stop(self): self.stopped.set()
    class Publisher:
        def __init__(self, run): pass
        def publish(self, frame): pass
        def close(self, **kwargs): pass
    def play(*args): calls.append(("agent", get_ident())); return 0
    monkeypatch.setattr(env_module, "LivePyBoyHost", Host)
    monkeypatch.setattr(viewer, "FramePublisher", Publisher)
    monkeypatch.setattr(run_agent, "find_rom", lambda: "rom")
    monkeypatch.setattr(run_agent, "_play", play)
    monkeypatch.setattr(run_agent, "PyBoyEnv", lambda *a, **k: pytest.fail("normal runner must be visible"))
    run = tmp_path / "run"
    assert run_agent.main(["--steps", "1", "--max-calls", "1", "--run", str(run)]) == 0
    assert dict(calls)["constructed"] == dict(calls)["owner"] == main_thread
    assert dict(calls)["agent"] != main_thread
    assert json.loads((run / "manifest.jsonl").read_text())["modes"]["headless"] is False
