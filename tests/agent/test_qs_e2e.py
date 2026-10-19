"""QR.6: the quest-state loop end to end with a scripted model and the fake grid env (no requests)."""
import json
from types import SimpleNamespace

from agent import loop as module
from agent.loop import Loop
from agent.memory import Memory
from tests.agent.fakes import GridEnv

SCENE = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}
EMPTY_PLAN = {"ops": [], "why": "nothing new"}


class Scripted:
    """Answers plan calls from a queue, then returns an empty plan."""
    def __init__(self, plans=()): self.plans, self.queue, self.calls, self.roles = [], list(plans), 0, []

    def ask(self, system, user, images=(), schema=None, *, role=""):
        self.calls += 1; self.roles.append(role)
        if role == "plan":
            self.plans.append(user)
            return SimpleNamespace(data=self.queue.pop(0) if self.queue else EMPTY_PLAN, text="")
        return SimpleNamespace(data={}, text="")


def scene(monkeypatch):
    monkeypatch.setattr(module, "perceive", lambda *_: (SCENE, SimpleNamespace(data={})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(action=None, option=None, why="idle", text=None))


class StillEnv(GridEnv):  # nothing is ever pressed, so one rendered frame serves every step
    shot = None
    def screenshot(self):
        StillEnv.shot = StillEnv.shot or super().screenshot()
        return StillEnv.shot.copy()


def new_loop(tmp_path, model):
    loop = Loop(StillEnv(), model, Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    return loop


def test_planner_calls_are_bounded_and_events_never_lost(tmp_path, monkeypatch):
    scene(monkeypatch)
    loop, produced = new_loop(tmp_path, Scripted()), []
    real = loop._event
    def spy(kind, text, step):
        before = len(loop.events); real(kind, text, step)
        if len(loop.events) > before: produced.append(loop.events[-1].copy())
    loop._event = spy
    rows = []
    for n in range(1, 201):
        loop._learn([f"The sign says line {n}."], [], loop.step_no + 1, loop.place, "world")  # game text every step
        if n % 7 == 0:  # a new place every 7 steps
            pid = f"room{n}"
            loop.mem.upsert("places", {"id": pid, "name": f"Room {n}", "screen": "world", "state": "place", "visits": 1})
            loop._moved(loop.place or "start", pid, "door", loop.step_no + 1)
        rows.append(loop.step())
    plans = [r for r in rows if r.get("plan")]
    steps = [r["step"] for r in plans]
    assert 3 <= len(plans) <= 67 and all(b - a >= 3 for a, b in zip(steps, steps[1:]))
    consumed = [e for r in plans for e in r["plan"]["events"]]
    assert sorted(map(json.dumps, produced), key=str) == sorted(map(json.dumps, consumed + loop.events), key=str)  # nothing lost: consumed or still waiting
    assert {e["kind"] for e in consumed} >= {"T1", "T2", "T5"}


def test_targeted_quest_closes_by_id_after_checkpoint_restore(tmp_path, monkeypatch):
    scene(monkeypatch)
    model = Scripted()
    loop = new_loop(tmp_path, model)
    loop.mem.upsert("places", {"id": "lab", "name": "Lab", "screen": "world", "state": "place", "visits": 1})
    loop.notebook.heard(0, "MOM", "house", "Go to the lab.")
    loop.notebook.apply_ops([{"op": "add", "key": "meet_guide", "text": "Meet the guide at the lab",
                             "why": ["t1"], "target": "place:lab"}], 1, "heard a lead")
    state = json.loads(json.dumps(loop.state()))
    resumed = new_loop(tmp_path, model)
    resumed.restore(state)
    resumed.mem.match_place = lambda *args, **kwargs: "lab"
    resumed._plan_trigger = lambda per: None

    row = resumed.step()

    quest = resumed.notebook.data["quests"][0]
    assert quest["key"] == "meet_guide" and quest["target"] == "place:lab"
    assert quest["status"] == "done" and quest["closed"] == 1
    assert [event["kind"] for event in resumed.events] == ["T3"]
    assert row["verdict"] == "progress" and resumed.stuck.progress_n == 0
    assert model.plans == []
