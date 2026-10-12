import json
from pathlib import Path
from PIL import Image
from agent.model import Reply
from scripts import eval_act
from tests.agent.fakes import StubModel

def answer(step):
    text = "All boys leave home some day" if step == 51 else "Visible town"
    screen = "dialogue" if step == 51 else "world"
    per = Reply("per", {"screen":screen,"text":[text],"entities":[],"affordances":[]}, 1, None, None)
    ops = [{"op":"set","section":"goals","key":"leave","value":{"text":"Leave home","evidence":"leave home","source":"heard","status":"active"}}] if step == 51 else []
    data = {"place":"Town","notebook":ops,"action":{"buttons":["A"]},"text":None,"expect":"screen_changes","why":"Continue"}
    return [per, Reply("act",data,1,None,None)]

class Posted(StubModel):
    def _post(self, payload): pass
    def ask(self, *args, **kwargs): self._post({}); return super().ask(*args, **kwargs)

def setup(monkeypatch, tmp_path, steps):
    frames, truth = tmp_path/"frames", tmp_path/"truth.jsonl"; frames.mkdir()
    for s in steps: Image.new("RGB",(160,144)).save(frames/f"{s:06d}.png")
    truth.write_text("".join(json.dumps({"step":s,"map":37,"text_box":s==51})+"\n" for s in steps))
    model=Posted([x for s in steps for x in answer(s)]); ledger=[]
    monkeypatch.setattr(eval_act,"FRAME_DIR",frames); monkeypatch.setattr(eval_act,"TRUTH",truth)
    monkeypatch.setattr(eval_act,"Ollama",lambda *a,**k:model); monkeypatch.setattr(eval_act.run_agent,"_ledger",lambda *a:ledger.append(a))
    return model,ledger

def test_three_frames_metrics_and_ledger(monkeypatch, tmp_path):
    model,ledger=setup(monkeypatch,tmp_path,[51,52,53]); out=tmp_path/"out"
    assert eval_act.main(["--frames","51,52,53","--max-calls","6","--out",str(out)])==0
    s=json.loads((out/"summary.json").read_text()); assert len(ledger)==model.calls==6==s["requests"]
    assert s["action"]=={"valid":3,"n":3,"completed_n":3} and s["evidenced_goals"]==s["mom_dialogue_goals"]==1
    assert s["world_place_purity"]["maps"]["37"]["purity"]==1 and not s["world_place_purity"]["name_map_collisions"]

def test_cap_keeps_completed_frame(monkeypatch, tmp_path):
    _,ledger=setup(monkeypatch,tmp_path,[51,52]); out=tmp_path/"out"
    assert eval_act.main(["--frames","51,52","--max-calls","3","--out",str(out)])==3
    assert len(ledger)==3 and len((out/"rows.jsonl").read_text().splitlines())==1
    assert json.loads((out/"summary.json").read_text())["status"]=="capped"
