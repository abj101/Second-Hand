import json, os, shutil
from pathlib import Path
from types import SimpleNamespace
from agent.loop import Loop
from agent.memory import Memory
from tests.agent.fakes import GridEnv, StubModel
DATA = Path(__file__).parent / "data" / "w0_golden"
def golden(root, episode):
    actual = {}
    for path in sorted((root / "memory").glob("*.jsonl")):
        actual[f"memory/{path.name}"] = sorted(map(json.loads, path.read_text().splitlines()), key=lambda r: r.get("id", ""))
    actual["trace.jsonl"] = [{k:v for k,v in r.items() if k not in {"t","ms","entities","scroll"}} for r in map(json.loads, (root / "trace.jsonl").read_text().splitlines())]
    target = DATA / episode
    if os.getenv("W0_REGEN") == "1":
        shutil.rmtree(target, ignore_errors=True)
        for name, rows in actual.items():
            path = target / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":"))+"\n" for r in rows))
    else:
        expected = {p.relative_to(target).as_posix(): list(map(json.loads, p.read_text().splitlines())) for p in target.rglob("*.jsonl")}
        assert actual == expected
def test_act_eight_steps(tmp_path, monkeypatch):
    from agent import loop as module
    env = GridEnv()
    def perceive(*_):
        per = {"screen":"world","text":[],
               "entities":[{"name":"player","kind":"player","at":[.5,.5]}, {"name":"door","kind":"exit","at":[.7,.5]}]}
        return per, SimpleNamespace(data=per)
    monkeypatch.setattr(module, "perceive", perceive)
    replies = [{"action":{"go_to":"door"}}, {"action":{"buttons":["RIGHT"]}}, *([{"action":None}] * 6)]
    model = StubModel([SimpleNamespace(data=r) for r in replies])
    loop = Loop(env, model, Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    rows = [loop.step() for _ in range(8)]
    assert rows[0]["choice"]["target"] == "door" and env.scene == 1 and rows[1]["choice"]["verb"] == "buttons" and env.player == (3, 8)
    assert len(rows) == model.calls == 8
    golden(tmp_path, "act")
