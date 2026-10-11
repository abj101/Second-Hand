import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent.loop import Loop
from agent.memory import Memory
from tests.agent.fakes import GridEnv, StubModel
from agent.model import ModelUnavailable

def reply(data): return type("Reply", (), {"data": data})()
def perception(*, two=False, press=False):
    affs = ([{"verb":"press","target":"A","at":[500,500]}] if press else
        [{"verb":"approach","target":"player","at":[500,500]}] * (2 if two else 1))
    return {"screen":"room","text":[],"entities":[{"kind":"player","name":"Hand","at":[500,500]}],"affordances":affs}

def test_twenty_steps_have_contract_keys_and_reuse(tmp_path):
    model=StubModel([reply(perception()), *[reply({"tasks":[]}) for _ in range(10)]]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    rows=[loop.step() for _ in range(20)]
    keys={"step","t","place","screen","task","frame","options","choice","pred","used","skill","outcome","verdict","calls","ms","shortcut"}
    assert all(keys <= row.keys() for row in rows)
    assert model.calls == 7 and rows[1]["perception_reused"] and rows[1]["calls"] == 0

def test_chooser_log_only_when_model_called(tmp_path):
    root=tmp_path/"memory"; loop=Loop(GridEnv(),StubModel([reply(perception())]),Memory(root),"grid")
    loop.step(); assert not (tmp_path/"chooser.jsonl").exists()
    model=StubModel([reply(perception(two=True)),reply({"i":0,"effect":"screen_changes"})])
    Loop(GridEnv(),model,Memory(tmp_path/"other"/"memory"),"grid").step()
    assert len((tmp_path/"other"/"chooser.jsonl").read_text().splitlines()) == 1

def test_restore_and_parse_failure(tmp_path):
    model=StubModel([reply(perception(two=True)),reply(None)])
    loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid"); row=loop.step()
    assert row["choice"]["called"]
    restored=Loop(loop.env,model,loop.mem,"grid"); restored.restore(loop.state())
    assert restored.state()["step"] == loop.state()["step"] and restored.cached == loop.cached

def test_perceiver_guides_whole_name_entry(tmp_path):
    class CapturesPrompt(StubModel):
        def ask(self, *args, **kwargs): self.prompt=args[1]; return super().ask(*args, **kwargs)
    model=CapturesPrompt([reply(perception())]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    loop._see(loop.env.screenshot(),None)
    assert "visible editable text field with its on-screen keyboard" in model.prompt
    assert "dialogue questions alone are advance" in model.prompt and "preset name menus are select" in model.prompt
    assert "A visible modal dialogue or selection menu blocks world movement" in model.prompt
    assert "doors, stairs, ladders, passages, or walkable floor continuing offscreen" in model.prompt and "never invent exits across solid walls" in model.prompt
    assert "Previous-place memory is reference only" in model.prompt

def test_malformed_parse_gets_one_repair_and_resumes(tmp_path):
    class Captures(StubModel):
        def ask(self,*a,**k):
            if k.get("role") == "perceiver": self.perceiver_prompts = getattr(self,"perceiver_prompts",[]) + [a[1]]
            return super().ask(*a,**k)
    model=Captures([reply({"wrong_root":True}),reply(None),reply(perception())]); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    for _ in range(8): loop.step()
    assert loop.state()["repair_pending"] and loop.state()["repair_used"]
    restored=Loop(loop.env,model,loop.mem,"grid"); restored.restore(loop.state()); row=restored.step()
    assert row["options"] == 1 and not restored.state()["repair_pending"]
    assert len(model.perceiver_prompts) == 2 and "previous JSON was empty or malformed" in model.perceiver_prompts[-1]

def test_string_text_is_valid_perception(tmp_path):
    data=perception(); data["text"]="A visible dialogue line"
    loop=Loop(GridEnv(),StubModel([reply(data)]),Memory(tmp_path/"memory"),"grid")
    parsed,_,_,_=loop._see(loop.env.screenshot(),None)
    assert parsed["text"] == ["A visible dialogue line"] and not loop.parse_failed

def test_failed_parse_repair_is_not_repeated(tmp_path):
    class AlwaysMalformed(StubModel):
        def __init__(self): super().__init__([]); self.prompts=[]
        def ask(self,*a,**k):
            self.calls+=1
            if k.get("role")=="perceiver": self.prompts.append(a[1]); return reply({"wrong_root":True})
            return reply(None)
    model=AlwaysMalformed(); loop=Loop(GridEnv(),model,Memory(tmp_path/"memory"),"grid")
    for _ in range(8): loop.step()
    loop.step(); assert loop.state()["repair_used"] and len(model.prompts)==2
    loop.step(); assert len(model.prompts)==2

def test_old_checkpoint_invalidates_cached_perception(tmp_path):
    model=StubModel([reply(perception()),reply(perception())]); env=GridEnv(); loop=Loop(env,model,Memory(tmp_path/"memory"),"grid")
    frame=env.screenshot(); loop._see(frame,None); old=loop.state(); old.pop("perception_version")
    restored=Loop(env,model,loop.mem,"grid"); restored.restore(old)
    assert restored.cached is None
    assert not restored._see(frame,None)[2] and model.calls == 2

def test_resume_preserves_cut_if_post_perception_fails(tmp_path):
    class CutEnv(GridEnv):
        def press(self,key,frames=8,on_frame=None): self.scene=1
    class FailsAfterChoice(StubModel):
        def ask(self,*a,**k):
            if self.calls==2: self.calls+=1; raise ModelUnavailable("offline")
            return super().ask(*a,**k)
    env=CutEnv(); mem=Memory(tmp_path/"memory")
    loop=Loop(env,FailsAfterChoice([reply(perception(press=True)),reply({"i":0,"effect":"place_changes"})]),mem,"grid")
    with pytest.raises(ModelUnavailable): loop.step()
    assert loop.state()["pending_cut"]
    town=perception(); town["screen"]="town"
    resumed=Loop(env,StubModel([reply(town)]),mem,"grid"); resumed.restore(loop.state())
    assert mem.get("places",resumed.step()["place"])["state"] == "candidate"

def test_tracked_approach_scroll_does_not_split_but_real_cuts_do(tmp_path, monkeypatch):
    from agent import loop as loop_module
    from agent.observer import cut as observer_cut

    def run_case(root, verb="approach", reason="", blank=False):
        env = GridEnv(); before = env.screenshot()
        start = perception(two=True)
        for aff in start["affordances"]: aff["verb"] = verb
        destination = perception(); destination["screen"] = "other room"
        destination["entities"].append({"kind":"object", "name":"new landmark", "at":[.2,.2]})
        if verb == "interact": destination["text"] = ["Visible dialogue"]
        model = StubModel([reply(start), reply({"i":0,"effect":"place_changes"}), reply(destination)])
        mem = Memory(root/"memory"); loop = Loop(env, model, mem, "grid")

        def move(ctx, aff, per, text=None):
            env.scene = 1; after = env.screenshot()
            assert observer_cut(before, after)
            return SimpleNamespace(status="done", reason=reason, inputs=7, calls=0,
                                   before=before, after=after, scroll=(112,0), blank_seen=blank)

        monkeypatch.setattr(loop_module, "run_affordance", move)
        return loop, mem, loop.step()

    loop, mem, row = run_case(tmp_path/"tracked")
    assert row["scene_cut"] is False and row["next_place"] == row["place"]
    assert "place_changes" not in row["outcome"]["effects"]
    assert not mem.get("places", row["place"])["edges"]

    for name, verb, reason, blank in (("skill", "approach", "scene cut", False),
                                      ("blank", "approach", "", True),
                                      ("interact", "interact", "", False)):
        loop, mem, row = run_case(tmp_path/name, verb, reason, blank)
        assert row["scene_cut"] is True and row["next_place"] != row["place"]
        assert mem.get("places", row["next_place"])["state"] == "candidate"
        assert len(mem.get("places", row["place"])["edges"]) == 1

def test_runner_press_exception_and_truncation(tmp_path, monkeypatch):
    from scripts import run_agent
    class Broken(GridEnv):
        def press(self,*a,**k): raise RuntimeError("focus lost")
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    model=StubModel([reply(perception(press=True)),reply({"i":0,"effect":"screen_changes"})]); model._post=lambda p: None
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k: Broken())
    monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**kw:model)
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    monkeypatch.setattr(run_agent,"read",lambda env:{"map":1})
    run=tmp_path/"run"
    assert run_agent.main(["--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert (run/"checkpoint/000000/harness.json").exists()
    path=tmp_path/"rows.jsonl"; path.write_text('{"step":1}\n{"step":2}\n')
    run_agent._truncate(path,1); assert len(path.read_text().splitlines()) == 1

def test_runner_call_cap_and_unavailable_checkpoint(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None); monkeypatch.setattr(run_agent,"read",lambda env:{})
    class Posts(StubModel):
        def ask(self,*a,**k): self._post({}); return super().ask(*a,**k)
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    model=Posts([reply(perception(press=True)),reply({"i":0,"effect":"screen_changes"})]); model._post=lambda p:None
    assert run_agent.main(["--steps","1","--max-calls","1","--run",str(tmp_path/"cap")]) == 3
    assert (tmp_path/"cap/checkpoint/000000/harness.json").exists()
    class Offline(StubModel):
        def ask(self,*a,**k): raise ModelUnavailable("offline")
    model=Offline([]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    assert run_agent.main(["--steps","1","--max-calls","5","--run",str(tmp_path/"offline")]) == 4
    assert (tmp_path/"offline/checkpoint/000000/harness.json").exists()

def test_budget_ledger_is_independent_and_survives_truncation(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); path=Path("runs/e3_budget.json"); path.parent.mkdir()
    path.write_text(json.dumps({"total":501,"m1":501,"smoke":0,"stage_a":1}))
    run_agent._ledger(Path("runs/m1"),"a",180)
    data=json.loads(path.read_text()); assert (data["total"],data["m1"],data["stage_a"]) == (502,502,2)
    rows=Path("runs/m1/calls.jsonl"); rows.parent.mkdir(exist_ok=True); rows.write_text('{"step":1}\n{"step":2}\n')
    run_agent._truncate(rows,1); assert json.loads(path.read_text())["total"] == 502
    path.write_text(json.dumps({"total":600,"m1":600,"smoke":0,"stage_a":180}))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"a",180)

def test_stage_b_cap_override_keeps_phase_and_m1_limits(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); path=Path("runs/e3_budget.json"); path.parent.mkdir()
    original={"total":500,"m1":500,"smoke":0,"stage_b":180}
    path.write_text(json.dumps(original))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",180)
    assert json.loads(path.read_text()) == original
    run_agent._ledger(Path("runs/m1"),"b",300)
    data=json.loads(path.read_text())
    assert (data["total"],data["m1"],data["stage_b"]) == (501,501,181)
    rows=Path("runs/m1/calls.jsonl"); rows.parent.mkdir(exist_ok=True); rows.write_text('{"step":1}\n')
    run_agent._truncate(rows,0); assert json.loads(path.read_text()) == data
    for _ in range(119): run_agent._ledger(Path("runs/m1"),"b",300)
    data=json.loads(path.read_text()); assert data["stage_b"] == 300
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == data
    phase_exhausted={**data,"total":1000,"stage_b":181}
    path.write_text(json.dumps(phase_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == phase_exhausted
    m1_exhausted={**data,"m1":950,"stage_b":181}
    path.write_text(json.dumps(m1_exhausted))
    with pytest.raises(run_agent.CallCap): run_agent._ledger(Path("runs/m1"),"b",300)
    assert json.loads(path.read_text()) == m1_exhausted

def test_stage_b_override_reaches_invocation_guard(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    class Model(StubModel):
        def __init__(self): super().__init__([])
        def _post(self,payload): self.calls+=1
    class ManyRequests:
        def __init__(self,env,model,mem,game): self.env,self.model,self.mem=env,model,mem; self.step_no=0
        def step(self):
            for _ in range(181): self.model._post({})
            self.step_no+=1
            return {"step":self.step_no,"place":"","task":"","choice":{},"verdict":""}
        def state(self): return {"step":self.step_no}
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"Loop",ManyRequests); monkeypatch.setattr(run_agent,"read",lambda env:{})
    monkeypatch.setattr(run_agent,"_ledger",lambda *a:None); monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:Model())
    common=["--steps","1","--max-calls","300","--stage","b"]
    assert run_agent.main([*common,"--run",str(tmp_path/"default")]) == 3
    assert run_agent.main([*common,"--stage-b-cap","300","--run",str(tmp_path/"override")]) == 0

def test_from_step_zero_rebuilds_boot_and_preserves_ledger(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); run=tmp_path/"runs/m1"; (run/"checkpoint/000025/memory").mkdir(parents=True)
    (run/"checkpoint/000025/harness.json").write_text('{"step":25}')
    (run/"memory").mkdir(); (run/"memory/places.jsonl").write_text('{"id":"old"}\n')
    for name in ("trace.jsonl","chooser.jsonl","calls.jsonl","truth.jsonl"): (run/name).write_text('{"step":25}\n')
    budget=tmp_path/"runs/e3_budget.json"; budget.parent.mkdir(exist_ok=True); budget.write_text('{"total":33,"m1":33,"smoke":0}')
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"read",lambda env:{"map":1})
    model=StubModel([reply(perception())]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model)
    assert run_agent.main(["--resume","--from-step","0","--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert (run/"checkpoint/000000/harness.json").exists() and not (run/"checkpoint/000025").exists()
    assert not list((run/"checkpoint/000000/memory").glob("*.jsonl"))
    assert [json.loads(x)["step"] for x in (run/"trace.jsonl").read_text().splitlines()] == [1]
    assert json.loads(budget.read_text())["total"] == 33

def test_resume_prunes_checkpoints_after_selected_step(tmp_path, monkeypatch):
    from scripts import run_agent
    monkeypatch.chdir(tmp_path); run=tmp_path/"runs/m1"
    for n in (10,25):
        cp=run/f"checkpoint/{n:06d}"; (cp/"memory").mkdir(parents=True); (cp/"harness.json").write_text(json.dumps({"step":n,"place":None,"stuck":{}})); (cp/"pyboy.state").write_text("state")
    (run/"trace.jsonl").parent.mkdir(parents=True,exist_ok=True); (run/"trace.jsonl").write_text("".join(json.dumps({"step":n})+"\n" for n in range(1,26)))
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom"); monkeypatch.setattr(run_agent,"read",lambda env:{})
    model=StubModel([reply(perception())]); model._post=lambda p:None; monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model); monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    assert run_agent.main(["--resume","--from-step","10","--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert not (run/"checkpoint/000025").exists() and (run/"checkpoint/000011").exists()
    assert [json.loads(x)["step"] for x in (run/"trace.jsonl").read_text().splitlines()] == list(range(1,12))

def test_keyboard_interrupt_writes_checkpoint(tmp_path, monkeypatch):
    from scripts import run_agent
    class Saved(GridEnv):
        def save(self,path): Path(path).write_text("state")
        def close(self): pass
    class Interrupted(StubModel):
        def ask(self,*a,**k): raise KeyboardInterrupt
    monkeypatch.setattr(run_agent,"PyBoyEnv",lambda *a,**k:Saved()); monkeypatch.setattr(run_agent,"find_rom",lambda:"rom")
    monkeypatch.setattr(run_agent,"read",lambda env:{}); model=Interrupted([]); model._post=lambda p:None
    monkeypatch.setattr(run_agent,"Ollama",lambda *a,**k:model); monkeypatch.setattr(run_agent,"_ledger",lambda *a:None)
    run=tmp_path/"interrupted"
    assert run_agent.main(["--steps","1","--max-calls","5","--run",str(run)]) == 0
    assert (run/"checkpoint/000000/harness.json").exists()
