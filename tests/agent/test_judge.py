import io
import json
import time

from agent.judge import Judge

READY = json.dumps({"ready": True}) + "\n"


class _Process:
    def __init__(self, lines=(), block=False):
        self.stdin, self.stdout, self.dead = io.StringIO(), self, False
        self.lines, self.block = iter([READY, *lines]), block

    def poll(self):
        return 1 if self.dead else None

    def readline(self):
        if (line := next(self.lines, None)) is not None:
            return line
        if self.block:
            time.sleep(0.2)
        return ""

    def kill(self):
        self.dead = True

    def wait(self, timeout=None):
        return 0


def test_rank_reuses_process_and_returns_answer(monkeypatch):
    process = _Process([json.dumps({"answers": {"action": {"choice": "a"}}}) + "\n"] * 2)
    monkeypatch.setattr("agent.judge.subprocess.Popen", lambda *a, **k: process)
    judge = Judge()
    assert judge.rank({"x": 1}, {"q": {}}) == {"answers": {"action": {"choice": "a"}}}
    assert judge.rank({"x": 2}, {"q": {}}) == {"answers": {"action": {"choice": "a"}}}
    assert len(process.stdin.getvalue().splitlines()) == 2


def test_rank_returns_none_and_restarts_after_worker_error(monkeypatch):
    processes = iter([_Process([json.dumps({"error": "bad input"}) + "\n"]),
                      _Process([json.dumps({"answers": {}}) + "\n"])])
    monkeypatch.setattr("agent.judge.subprocess.Popen", lambda *a, **k: next(processes))
    judge = Judge()
    assert judge.rank({}, {}) is None
    assert judge.rank({}, {}) == {"answers": {}}


def test_rank_kills_worker_on_timeout(monkeypatch):
    process = _Process(block=True)
    monkeypatch.setattr("agent.judge.subprocess.Popen", lambda *a, **k: process)
    assert Judge(timeout_s=0.01).rank({}, {}) is None
    assert process.dead


def test_failed_startup_disables_judge_without_respawning(monkeypatch):
    spawned = []
    def popen(*a, **k):
        process = _Process(); process.lines = iter([json.dumps({"error": "no cuda"}) + "\n"]); spawned.append(process); return process
    monkeypatch.setattr("agent.judge.subprocess.Popen", popen)
    judge = Judge()
    assert judge.rank({}, {}) is None and judge.rank({}, {}) is None
    assert len(spawned) == 1 and spawned[0].dead
