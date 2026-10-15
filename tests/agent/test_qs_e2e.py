"""QR.6: the quest-state loop end to end with a scripted model and the fake grid env (no requests)."""
import json
from types import SimpleNamespace

from agent import loop as module
from agent.loop import Loop
from agent.memory import Memory
from tests.agent.fakes import GridEnv

SCENE = {"screen": "world", "text": [], "entities": [{"name": "player", "kind": "player", "at": [.5, .5]}]}
EMPTY_PLAN = {"focus": "", "quests": [], "beliefs": [], "facts": [], "note": "", "why": "nothing new"}
CHAPTER = {"summary": "The guide was met at the lab.", "ruled_out": [], "open_leads": []}


class Scripted:
    """Answers by role: plan replies come from a queue (then an empty plan), chapter calls get a valid chapter."""
    def __init__(self, plans=()): self.plans, self.queue, self.calls, self.roles = [], list(plans), 0, []

    def ask(self, system, user, images=(), schema=None, *, role=""):
        self.calls += 1; self.roles.append(role)
        if role == "plan":
            self.plans.append(user)
            return SimpleNamespace(data=self.queue.pop(0) if self.queue else EMPTY_PLAN, text="")
        return SimpleNamespace(data=CHAPTER if role == "chapter" else {}, text="")


def scene(monkeypatch):
    monkeypatch.setattr(module, "perceive", lambda *_: (SCENE, SimpleNamespace(data={})))
    monkeypatch.setattr(module, "act", lambda *a, **k: SimpleNamespace(place="", ops=[], action=None, option=None, expect="screen_changes", why="idle", text=None))


class StillEnv(GridEnv):  # nothing is ever pressed, so one rendered frame serves every step
    shot = None
    def screenshot(self):
        StillEnv.shot = StillEnv.shot or super().screenshot()
        return StillEnv.shot.copy()


def new_loop(tmp_path, model):
    loop = Loop(StillEnv(), model, Memory(tmp_path / "memory"), "grid")
    loop.world.self_spot, loop.world._self_from_motion = [.5, .5], True
    return loop


def talk(loop, text):  # a dialogue screen, then the world: the conversation flushes into the log and queues T1
    step = loop.step_no + 1
    loop._learn([text], [], step, loop.place, "dialogue"); loop._learn([], [], step, loop.place, "world")


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


def test_heard_quest_closes_by_check_and_compacts_across_restore(tmp_path, monkeypatch):
    scene(monkeypatch)
    heard_plan = {"focus": "", "beliefs": [], "note": "A guide waits at the lab.", "why": "heard a lead",
                  "facts": [{"key": "guide_lead", "text": "A guide waits at the lab.", "from": "MOM", "source": "heard"}],
                  "quests": [{"key": "meet_guide", "text": "Meet the guide at the lab", "why": ["guide_lead"], "done_when": "the guide speaks",
                              "status": "open", "check": {"kind": "speak", "arg": "Guide"}}]}
    model = Scripted([heard_plan])
    loop = new_loop(tmp_path, model)
    loop.step()  # T5: nothing known yet, an empty plan
    talk(loop, "MOM: Go and meet the guide at the lab.")
    for _ in range(4):  # T1 plans once the floor passes
        loop.step()
        if loop.notebook.data["quests"]: break
    quest = loop.notebook.data["quests"][0]
    assert quest["key"] == "meet_guide" and quest["check"] == {"kind": "speak", "arg": "Guide"} and loop.notebook._heard_quest(quest)

    talk(loop, "GUIDE: Welcome to the lab.")  # the speaker's line, after the quest opened
    loop.step()  # run_checks closes it; a heard quest closing compacts: one chapter call
    assert loop.notebook.data["quests"][0]["status"] == "done" and model.roles.count("chapter") == 1
    assert [e["kind"] for e in loop.events].count("T3") == 1
    chapters = loop.notebook.data["chapters"]
    assert chapters[0]["summary"] == CHAPTER["summary"] and loop.notebook.data["notes"] == []

    state = json.loads(json.dumps(loop.state()))  # a checkpoint restore mid-run, with the notebook reloaded from disk
    resumed = new_loop(tmp_path, model)
    resumed.restore(state)
    assert resumed.events == loop.events and resumed.notebook.data["quests"] == loop.notebook.data["quests"]
    assert resumed.notebook.data["chapters"] == chapters and resumed.notebook.data["notes"] == loop.notebook.data["notes"]

    before = len(model.plans)
    for _ in range(4):
        resumed.step()
        if len(model.plans) > before: break
    assert len(model.plans) == before + 1 and "T3" not in [e["kind"] for e in resumed.events]
    assert "quest meet_guide closed" in model.plans[-1] and CHAPTER["summary"] in model.plans[-1]  # the next planner prompt holds the chapter
