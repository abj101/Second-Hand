import json
from scripts import eval_act

# ---- --om reliability (HF5 WP B): a fake production Loop with scripted RAM, cache-only model, no network

import httpx
from agent import loop as loop_mod
from agent.skills import SkillResult

OPTIONS = [{"n": 1, "tool": "go_to", "label": "exit: door", "action": {"go_to": "exit: door"}},
           {"n": 2, "tool": "go_to", "label": "exit door mat → outside house", "action": {"go_to": "outside house"}}]
PLAYER = {"slot": 0, "picture": 1, "y": 60, "x": 64}
ACTOR_BELOW = {"slot": 1, "picture": 17, "y": 76, "x": 64}  # one tile below the player (RAM truth only)
SCRIPT = {}

def ram(map_, x, y, sprites=(PLAYER, ACTOR_BELOW)): return {"map": map_, "x": x, "y": y, "sprites": list(sprites)}

class FakeEnv:
    tile_px = None
    def __init__(self): self.pressed = []
    def press(self, key, *args, **kwargs): self.pressed.append(key)
    def close(self): pass

class FakeWorld:
    def __init__(self, env): self.env, self.pos = env, (0, -5)
    def travel(self, ctx, legs, step, trace=None):
        SCRIPT["legs"] = legs
        for key in SCRIPT["keys"]: self.env.press(key)
        return SCRIPT["skill"]

class FakeLoop:  # step 126 is the primitive; steps 127+ are the continuation (no model reads unless scripted)
    def __init__(self, env, model, mem, game):
        self.model, self.step_no, self.world = model, 125, FakeWorld(env)
    def restore(self, harness): pass
    def _settle(self, s, skill, after_set, start, calls):  # the after-frame's perception, as Loop._settle makes it
        if SCRIPT["post_perceive"]: self.model.ask("system", "after frame", [], None, role="perceiver")
        return {"step": self.step_no + 1}
    _hold_action = _settle
    def step(self):
        n = self.step_no + 1
        if n in SCRIPT["raise_at"]: raise SCRIPT["raise_at"][n]
        if n == 126 and SCRIPT["perceive"]: self.model.ask("system", "frame", [], None, role="perceiver")
        if n > 126:
            if SCRIPT["cont_perceive"]: self.model.ask("system", "frame", [], None, role="perceiver")
            self.step_no = n; return {"step": n}
        loop_mod.act(self.model, {}, "", OPTIONS, [], "")
        skill = self.world.travel(None, SCRIPT["legs"], n)
        self._settle({}, skill, None, 0, 0); self.step_no = n
        return {"step": n}

def fake_act(model, per, focus, options, buttons, controls): return model.ask("system", "act", [], {"properties": {"i": {}}}, role="act").data

def reliability(monkeypatch, tmp_path, *, keys, legs, skill, before, after, perceive=False, post_perceive=False, cont_perceive=False, raise_at=None):
    monkeypatch.chdir(tmp_path)  # runs/, .cache/ and .tmp/ resolve here: no real checkpoint, cache or budget is touched
    cp = tmp_path / "runs" / "fake" / "checkpoint" / "000125"; (cp / "memory").mkdir(parents=True)
    (cp / "pyboy.state").write_bytes(b"state"); (cp / "harness.json").write_text("{}")
    (tmp_path / "runs" / "fake" / "trace.jsonl").write_text(json.dumps({"step": 126, "place": "p5", "act": {"action": {"go_to": "outside house"}},
                                                                       "skill": {"status": "blocked", "reason": "travel: leg 1 exit mismatch"}}) + "\n")
    (tmp_path / "runs" / "e3_budget.json").write_text(json.dumps({"total": 5, "m1": 3}))
    SCRIPT.update(keys=keys, legs=legs, skill=skill, perceive=perceive, post_perceive=post_perceive, cont_perceive=cont_perceive, raise_at=raise_at or {})
    frames = iter([before, after])
    env = FakeEnv()
    monkeypatch.setattr(eval_act, "PyBoyEnv", lambda rom, state=None: env); monkeypatch.setattr(eval_act, "find_rom", lambda: "rom.gb")
    monkeypatch.setattr(eval_act, "Loop", FakeLoop); monkeypatch.setattr(eval_act, "truth_read", lambda env: next(frames, after))
    monkeypatch.setattr(loop_mod, "act", fake_act)
    posts = []
    def http(*args, **kwargs): posts.append(args); raise RuntimeError("HTTP attempted")
    monkeypatch.setattr(httpx, "post", http)
    return posts

def walk_skill(failure=None, status="done"):
    return SkillResult(status, "" if status == "done" else "travel: leg 1 exit mismatch", 12 if status == "done" else 1, 0, None, None, (0, 0), False, failure=failure)

def test_reliability_missing_checkpoint_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert eval_act.main(["--om", "reliability", "--run", "nope", "--from-step", "125"]) == 2
    assert "missing checkpoint evidence" in capsys.readouterr().out

def test_reliability_first_perception_miss_exits_2_with_no_http(monkeypatch, tmp_path, capsys):
    posts = reliability(monkeypatch, tmp_path, keys=[], legs=[], skill=walk_skill(), before=ram(39, 2, 2), after=ram(39, 2, 2), perceive=True)
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 2
    out = capsys.readouterr().out
    assert "perceiver cache miss at step 126" in out and "requests=0" in out and "ledger unchanged: yes" in out and posts == []

def test_reliability_failed_assertion_exits_1(monkeypatch, tmp_path, capsys):
    # the route and the first press both go into the actor below the player; nothing moves; the failure stage is "route"
    failure = {"stage": "route", "reason": "route blocked", "direction": "DOWN"}
    reliability(monkeypatch, tmp_path, keys=["DOWN"], legs=[{"place": "p5", "dirs": ["DOWN", "DOWN"], "push": None, "expect": "p4", "exit_tile": [0, -3], "arrive": [0, 0]}],
                skill=walk_skill(failure, "blocked"), before=ram(39, 2, 2), after=ram(39, 2, 2))
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 1
    out = capsys.readouterr().out
    assert "FAIL route excludes the actor tile" in out and "FAIL first press is not into the actor" in out and "reliability: primitive=FAIL" in out

def test_reliability_all_assertions_pass_exits_0(monkeypatch, tmp_path, capsys):
    # the route walks LEFT first around the actor, reaches the door mat, and the map changes (extra evidence only)
    legs = [{"place": "p5", "dirs": ["LEFT", "DOWN", "DOWN", "DOWN", "DOWN", "RIGHT"], "push": "DOWN", "expect": "p4", "exit_tile": [0, -1], "arrive": [0, 0]}]
    posts = reliability(monkeypatch, tmp_path, keys=["LEFT", "DOWN", "DOWN", "DOWN", "DOWN", "RIGHT"], legs=legs, skill=walk_skill(), before=ram(39, 2, 2), after=ram(0, 13, 6))
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n  PASS ") == 4 and "reliability: primitive=PASS continuation=COMPLETED(steps 127-129)" in out and "requests=0 ledger_unchanged=yes" in out and posts == []

def test_om_talk_cache_miss_is_incompatible_not_a_verdict(monkeypatch, capsys, tmp_path):
    class MissingReplay(eval_act.Replay):
        def run(self, last):  # four perceiver misses: the replay's limit is three
            self.step = 2
            for n in range(4): self.ask("s", f"unseen {n}", [], None, role="perceiver")
    monkeypatch.setattr(eval_act, "stage", lambda run: type("Rec", (), {"trace": {1: {}, 2: {}, 3: {}}, "copy": tmp_path})())
    monkeypatch.setattr(eval_act, "Replay", MissingReplay)
    assert eval_act.main(["--om", "talk", "--run", "m1_decide3"]) == 2
    out = capsys.readouterr().out
    assert "incompatible old replay / inconclusive" in out and "last at step 2" in out

PASS_LEGS = [{"place": "p5", "dirs": ["LEFT", "DOWN", "DOWN", "DOWN", "DOWN", "RIGHT"], "push": "DOWN", "expect": "p4", "exit_tile": [0, -1], "arrive": [0, 0]}]
PASS_KEYS = ["LEFT", "DOWN", "DOWN", "DOWN", "DOWN", "RIGHT"]

def test_reliability_post_frame_cache_miss_is_inconclusive_exit_2(monkeypatch, tmp_path, capsys):
    # the primitive passes; the after-frame's perception misses the cache: continuation is INCONCLUSIVE, which is not a pass
    reliability(monkeypatch, tmp_path, keys=PASS_KEYS, legs=PASS_LEGS, skill=walk_skill(), before=ram(39, 2, 2), after=ram(0, 13, 6), post_perceive=True)
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 2
    assert "reliability: primitive=PASS continuation=INCONCLUSIVE(step 126) requests=0" in capsys.readouterr().out

def test_reliability_continuation_cache_miss_is_inconclusive_exit_2(monkeypatch, tmp_path, capsys):
    reliability(monkeypatch, tmp_path, keys=PASS_KEYS, legs=PASS_LEGS, skill=walk_skill(), before=ram(39, 2, 2), after=ram(0, 13, 6), cont_perceive=True)
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 2
    out = capsys.readouterr().out
    assert "reliability: primitive=PASS continuation=INCONCLUSIVE(step 127)" in out and "COMPLETED" not in out

def test_reliability_mid_continuation_error_is_not_completed_and_exits_2(monkeypatch, tmp_path, capsys):
    reliability(monkeypatch, tmp_path, keys=PASS_KEYS, legs=PASS_LEGS, skill=walk_skill(), before=ram(39, 2, 2), after=ram(0, 13, 6),
                raise_at={127: eval_act.OmAbort("boom")})
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 2
    out = capsys.readouterr().out
    assert "reliability: primitive=PASS continuation=INCONCLUSIVE(step 127)" in out and "COMPLETED" not in out

def test_reliability_crossing_reported_as_blocked_without_failure_exits_1(monkeypatch, tmp_path, capsys):
    # RAM shows the map changed, so no failure stage is expected; a blocked status with failure None still disagrees with it
    reliability(monkeypatch, tmp_path, keys=PASS_KEYS, legs=PASS_LEGS, skill=walk_skill(None, "blocked"), before=ram(39, 2, 2), after=ram(0, 13, 6))
    assert eval_act.main(["--om", "reliability", "--run", "fake", "--from-step", "125"]) == 1
    out = capsys.readouterr().out
    assert "FAIL reported failure stage matches RAM" in out and "reliability: primitive=FAIL" in out
