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
from scripts.run_agent import append_manifest, flag_manifest, manifest_record, resolve_modes, source_fingerprint

MANIFEST_KEYS = {"invocation", "t", "resume", "start_checkpoint", "from_step", "steps", "max_calls", "stage",
                 "stage_b_cap", "model", "modes", "source", "provenance", "notes"}


class _Response:
    status_code, headers = 200, {}
    def __init__(self, text): self.text = text
    def json(self): return {"message": {"content": self.text}, "prompt_eval_count": 1, "eval_count": 1}


def _append_jsonl(path, row):
    with path.open("a", encoding="utf-8") as stream: stream.write(json.dumps(row) + "\n")


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                          cwd=cwd, capture_output=True, check=True, text=True)


def test_fresh_run_without_flags_is_false_and_silent():
    assert resolve_modes(None, None, None) == ({"decide": False, "act_text": False}, [])


def test_resume_restores_saved_modes_when_no_flags_given():
    assert resolve_modes({"decide": True, "act_text": False}, None, None, resume=True) == ({"decide": True, "act_text": False}, [])


def test_explicit_flag_that_conflicts_with_saved_mode_is_rejected():
    with pytest.raises(ValueError, match="--no-decide"):
        resolve_modes({"decide": True, "act_text": False}, False, None, resume=True)


def test_legacy_checkpoint_resume_with_unspecified_flag_notes_it():
    modes, notes = resolve_modes(None, True, None, resume=True)
    assert modes == {"decide": True, "act_text": False}
    assert len(notes) == 1 and notes[0].startswith("act_text")


def test_legacy_checkpoint_with_explicit_flags_has_no_note():
    assert resolve_modes(None, True, False, resume=True) == ({"decide": True, "act_text": False}, [])


def test_resume_conflict_exits_before_any_input_or_env(tmp_path, monkeypatch):
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
                             stage_b_cap=180, model="m", modes={"decide": False, "act_text": True}, source=source)
    first = append_manifest(tmp_path, record); second = append_manifest(tmp_path, record)

    lines = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["invocation"] for line in lines] == [1, 2] == [first["invocation"], second["invocation"]]
    assert set(lines[0]) == MANIFEST_KEYS
    assert lines[0]["source"] == {"head": "abc", "dirty": {"agent/x.py": "00"}} and lines[0]["provenance"] == "complete"
    assert manifest_record(resume=True, start_checkpoint="000005", from_step=5, steps=1, max_calls=1, stage=None, stage_b_cap=180,
                           model="m", modes={"decide": True, "act_text": False},
                           source={**source, "notes": ["git unavailable"]})["provenance"] == "incomplete"

    flag_manifest(tmp_path, 2, "calls.jsonl write failed")
    lines = [json.loads(line) for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert lines[0]["provenance"] == "complete"
    assert lines[1]["provenance"] == "incomplete" and lines[1]["notes"] == ["calls.jsonl write failed"]


def test_source_fingerprint_without_git_is_incomplete(tmp_path):
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
                                         stage_b_cap=180, model="stub", modes={"decide": True, "act_text": False},
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


PERCEPTION = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [0.5, 0.5]}], "affordances": []}
PLAN = {"goals": [], "focus": "", "why": "nothing new"}
ACTION = {"place": "", "notebook": [], "action": None, "text": None, "expect": "screen_changes", "why": "test"}


def _record(**overrides):
    fields = {"resume": False, "start_checkpoint": None, "from_step": None, "steps": 1, "max_calls": 5, "stage": None, "stage_b_cap": 180,
              "model": "stub", "modes": {"decide": False, "act_text": False}, "source": {"head": "h", "dirty": {}, "notes": []}, **overrides}
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


def test_resume_hands_the_checkpoint_modes_to_the_loop_and_records_them(tmp_path, monkeypatch):
    built, Saved = _spy_runner(monkeypatch)
    run = tmp_path / "run"
    run_agent._checkpoint(Saved(), Loop(GridEnv(), StubModel([]), Memory(run / "memory"), "grid", decide=True), run)

    assert run_agent.main(["--resume", "--steps", "1", "--max-calls", "5", "--run", str(run)]) == 0

    kwargs, loop = built[-1]
    assert kwargs == {"decide": True} and loop.provenance_incomplete is False and loop.invocation == 1
    manifest = json.loads((run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert manifest["modes"] == {"decide": True, "act_text": False} and manifest["provenance"] == "complete"


def test_resume_from_a_checkpoint_without_modes_is_incomplete_everywhere(tmp_path, monkeypatch):
    built, Saved = _spy_runner(monkeypatch)
    run = tmp_path / "run"
    root = run_agent._checkpoint(Saved(), Loop(GridEnv(), StubModel([]), Memory(run / "memory"), "grid"), run)
    state = json.loads((root / "harness.json").read_text(encoding="utf-8")); state.pop("modes")
    (root / "harness.json").write_text(json.dumps(state), encoding="utf-8")

    assert run_agent.main(["--resume", "--steps", "1", "--max-calls", "5", "--run", str(run)]) == 0

    _, loop = built[-1]
    assert loop.provenance_incomplete is True
    manifest = json.loads((run / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert manifest["provenance"] == "incomplete" and any("no modes" in note for note in manifest["notes"])
    harness = json.loads((run / "checkpoint" / "000001" / "harness.json").read_text(encoding="utf-8"))
    assert "modes" not in harness and harness["provenance"]["incomplete"] is True
