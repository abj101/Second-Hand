import argparse, json
from collections import Counter, defaultdict
from pathlib import Path
import sys
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.act import act
from agent.brief import GENERAL, defaults, load_brief
from agent.env_pyboy import _INPUTS
from agent.model import ModelUnavailable, Ollama
from agent.notebook import Notebook
from agent.observer import for_model
from agent.perceive import perceive
from scripts import run_agent

FRAMES = (6,12,44,46,48,51,53,56,62,67,68,70,76,85,94)
OUT, FRAME_DIR, TRUTH = Path(".tmp/p2"), Path("runs/m1/frames"), Path("runs/m1/truth.jsonl")
NOTE = "On a visible editable text field with its on-screen keyboard, expose one type affordance for the whole field; dialogue questions alone are advance, preset name menus are select. Never list letter buttons separately. A visible modal dialogue or selection menu blocks world movement; offer only its applicable advance, select, or cancel actions until it closes. Previous-place memory is reference only; include details only if visible now."

def save(summary, out):
    tmp=out/"summary.tmp"; tmp.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8"); tmp.replace(out/"summary.json")

def metrics(rows, requested, requests=0):
    places=defaultdict(Counter); goals=set(); mom=set(); worlds=[]; earlier=[]; goal_ops=0
    for r in rows:
        d=r.get("act_parsed") or {}; step=r["step"]; text=[str(t).casefold() for t in r.get("text",[])]
        if r.get("screen")=="world" and r.get("truth_map") is not None:
            worlds.append(r); places[str(r["truth_map"])][str(d.get("place") or "").casefold()]+=1
        for op in r.get("notebook_ops",[]):
            if op.get("op")!="set" or op.get("section")!="goals": continue
            v=op.get("value") if isinstance(op.get("value"),dict) else {}; e=v.get("evidence"); e=e.casefold() if isinstance(e,str) else ""; key=op.get("key"); key=key.strip()[:40] if isinstance(key,str) else ""
            if key: goal_ops+=1
            if key and e and any(e in t for t in earlier+text): goals.add(key)
            if key and step in (51,52,53) and e and any(e in t for t in text): mom.add(key)
        earlier.extend(text)
    maps={k:{"names":dict(c),"dominant":c.most_common(1)[0][0],"dominant_count":c.most_common(1)[0][1],"n":sum(c.values()),"purity":c.most_common(1)[0][1]/sum(c.values()),"dominant_named":bool(c.most_common(1)[0][0])} for k,c in places.items()}
    owners=defaultdict(set)
    for m,x in maps.items():
        for n in x["names"]:
            if n: owners[n].add(m)
    good=sum(bool((r.get("act_parsed") or {}).get("action")) for r in rows)
    collisions={k:sorted(v) for k,v in owners.items() if len(v)>1}; purity_ok=all(x["dominant_named"] and x["purity"]>=.8 for x in maps.values()) and not collisions
    return {"requested_frames":len(requested),"completed_frames":len(rows),"action":{"valid":good,"n":len(requested),"completed_n":len(rows)},"evidenced_goals":len(goals),"goal_evidence":{"valid":len(goals),"goal_ops":goal_ops},"mom_dialogue_goals":len(mom),"world_place_purity":{"maps":maps,"n_world_frames":len(worlds),"name_map_collisions":collisions},"gate_metrics":{"action_14_of_15":good>=14 and len(requested)>=15,"evidenced_goals_at_least_2":len(goals)>=2,"mom_dialogue_goal_at_least_1":len(mom)>=1,"world_map_purity_at_least_80_percent":purity_ok,"p2_requests_at_most_40":requests<=40}}

def table(rows,out):
    lines=["| Step | Truth map | Screen text | Place named | Action | Why | Goals added |","|---:|---:|---|---|---|---|---|"]
    for r in rows:
        a=r.get("act_parsed") or {}; text=" ".join(r["text"]).replace("|","\\|")[:80]
        cells=[str(r["step"]),str(r.get("truth_map","")),text,a.get("place",""),json.dumps(a.get("action"),ensure_ascii=False),str(a.get("why","")).replace("|","\\|"),", ".join(o.get("key","") for o in r.get("notebook_ops",[]) if o.get("section")=="goals")]
        lines.append("| "+" | ".join(cells)+" |")
    (out/"table.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

def report(summary, rows, steps, status, out):
    m=metrics(rows,steps,int(summary.get("requests",0)))
    save({**summary,**m,"status":status},out)

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument("--frames",default=",".join(map(str,FRAMES))); ap.add_argument("--max-calls",type=int,required=True); ap.add_argument("--out",type=Path,default=OUT); a=ap.parse_args(argv)
    if a.max_calls<1: ap.error("--max-calls must be positive")
    steps=[int(s) for s in a.frames.split(",") if s.strip()]
    if not steps: ap.error("--frames must contain a step")
    out=a.out; out.mkdir(parents=True,exist_ok=True); rp=out/"rows.jsonl"; sp=out/"summary.json"
    rows=[json.loads(x) for x in rp.read_text(encoding="utf-8").splitlines() if x.strip()] if rp.exists() else []
    summary=json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {"requests":0}; done={r["step"] for r in rows if r.get("complete")}
    truth={r["step"]:r for r in (json.loads(x) for x in TRUTH.read_text(encoding="utf-8").splitlines())}; brief=load_brief("pokemon_red"); prefs=defaults("pokemon_red")
    ctx=GENERAL+"\n"+brief+"\n"+NOTE; notebook=Notebook(out/"notebook"); model=Ollama("gemma4:31b-cloud",cache_dir=Path(".cache/model")); invocation=[0]; raw=[None]; post,ask=model._post,model.ask
    def guarded(payload):
        if invocation[0]>=a.max_calls or int(summary.get("requests",0))>=40: raise run_agent.CallCap("P2 request cap")
        run_agent._ledger(Path("runs/m1_p2"),None,40); invocation[0]+=1; summary["requests"]=int(summary.get("requests",0))+1; save(summary,out); return post(payload)
    def capture(*args,**kwargs):
        reply=ask(*args,**kwargs)
        if kwargs.get("role")=="act": raw[0]={"text":reply.text,"data":reply.data}
        return reply
    model._post,model.ask=guarded,capture; status="complete"
    try:
        for step in steps:
            if step in done: continue
            with Image.open(FRAME_DIR/f"{step:06d}.png") as im: frame=for_model(im); per,preply=perceive(model,frame,ctx)
            raw[0]=None; result=act(model,frame,per,notebook.render(),"none",[],brief,prefs,list(_INPUTS)); notebook.apply(result.ops,step)
            r={"step":step,"screen":per["screen"],"text":per["text"],"entities_count":len(per["entities"]),"affordances":per["affordances"],"perceive_raw":{"text":preply.text,"data":preply.data},"act_reply":{"raw":raw[0],"parsed":result.__dict__},"act_parsed":result.__dict__,"notebook_ops":result.ops,"truth_map":truth.get(step,{}).get("map"),"complete":True}
            rows.append(r)
            with rp.open("a",encoding="utf-8") as f: f.write(json.dumps(r,ensure_ascii=False)+"\n")
            done.add(step); report(summary,rows,steps,status,out)
    except run_agent.CallCap: status="capped"
    except ModelUnavailable: status="unavailable"
    report(summary,rows,steps,status,out); table(rows,out)
    return 3 if status=="capped" else 4 if status=="unavailable" else 0

if __name__=="__main__": raise SystemExit(main())
