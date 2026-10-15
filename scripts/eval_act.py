import argparse, hashlib, io, json, pickle, re, shutil, subprocess
import math
from copy import deepcopy
from collections import Counter, defaultdict
from pathlib import Path
import sys
from PIL import Image, ImageChops
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent.loop as loop_mod
from agent.act import act
from agent.brief import GENERAL, defaults, load_brief
from agent.env_pyboy import PyBoyEnv, _INPUTS, find_rom
from agent.loop import Loop
from agent.memory import Memory
from agent.model import ModelUnavailable, Ollama, Reply
from agent.notebook import Notebook
from agent import plan as plan_mod
from agent.observer import for_model, wait_settled
from agent.perceive import perceive
from agent.space import DIRS
from agent.world import _is_exit
from scripts import run_agent
from scripts.truth_pokered import read as truth_read

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
    ap=argparse.ArgumentParser(); ap.add_argument("--frames",default=",".join(map(str,FRAMES))); ap.add_argument("--max-calls",type=int); ap.add_argument("--out",type=Path,default=OUT)
    ap.add_argument("--run",default="m1_decide",help="--om truth/talk/plan: the recorded run"); ap.add_argument("--exact-exit",action="store_true",help="--om truth: ablate fix 2 (exact exit lookup)"); ap.add_argument("--strip-place",action="store_true",help="--om truth: a run recorded before the perceiver's place field"); ap.add_argument("--from-step",type=int,help="--om branch: checkpoint step; --om plan: planner checkpoint step"); ap.add_argument("--tries",type=int,help="--om plan: independent planner calls (1-8)"); ap.add_argument("--pick",help="--om branch: text of the option label to pick"); ap.add_argument("--om",choices=("places","cases","outcomes","truth","branch","talk","plan","reliability"),help="OM replay eval (no --max-calls needed); reliability: HF5 primitive + continuation check of --run at --from-step"); ap.add_argument("--live",action="store_true",help="--om cases: spend the requests; --om plan: spend up to --tries"); ap.add_argument("--dry",action="store_true",help="--om cases: no requests (the default)"); ap.add_argument("--steps",help="--om cases: comma-separated case steps to run (default all)"); ap.add_argument("--fresh",action="store_true",help="--om cases/outcomes: rebuild the replay snapshots"); ap.add_argument("--arm",choices=("options","decide"),default="options",help="--om cases: decide = text-only act that answers {why, i}")
    a=ap.parse_args(argv)
    if a.om: return om_main(a, ap)
    if a.max_calls is None: ap.error("the following arguments are required: --max-calls")
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

# ---- OM.6 replay eval: replays runs/m1_gp5 and runs/m1_gp2 with the current agent code, from boot (memory must be rebuilt
# by the new code, so the recorded checkpoint memory is not used). Recorded plan and act replies keep the emulator on the
# recorded path; perceiver replies come from .cache/model only. RAM truth (truth.jsonl) is read here, never by agent/.
OM_CASES, OM_CAP, OM_MODEL = Path("scripts/om_cases.json"), 10, "gemma4:31b-cloud"  # OM2 section 7: one live measure, at most 10 requests
DECIDE = False  # --arm decide: case steps use the text-only {why, i} act (act.py _decide); its retries and follow-ups raise the cap to 16
MODEL_CACHE, BUDGET = Path(".cache/model"), Path("runs/e3_budget.json")
SCREEN_TYPES = ("world", "dialogue", "menu", "keyboard", "battle", "title", "other")
PERCEIVER_MISSES = 3  # more cache misses than this and the replay no longer matches the recording
FAKE_EXIT = "exit: doorway"  # m1_gp5's 2F doorway: walking into it changes nothing (RAM truth: map 38 stays put)
DECISION_GATE, OLD_GATE = 6, 3  # of 8 cases
MAX_OPTIONS, MAX_PROMPT_TOKENS = 10, 1700  # OM2 section 2 size gate: options excluding the free-buttons row; act prompt tokens = len/4
FLOOR_OF_MAP = {37: "1F", 38: "2F"}  # truth.map of the m1_gp5 house (evaluation only; agent/ never reads truth)
EXIT_ROWS = ("exit ", "possible exit ", "not an exit")  # option labels of exit records: verified, unverified, refuted

class OmAbort(RuntimeError): pass  # the replay cannot continue: exit 2
class OmIncompatible(OmAbort): pass  # a legacy replay met perception the cache does not hold: incompatible, inconclusive, exit 2
class OmStop(Exception): pass  # outcome mode: the scripted action's presses are done; stop before any perceiver call on the new frame

def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

def _budget():
    return json.loads(BUDGET.read_text(encoding="utf-8")) if BUDGET.exists() else {}

def _same_frame(a, b):
    if not (a.exists() and b.exists()): return False
    with Image.open(a) as x, Image.open(b) as y:
        return x.size == y.size and ImageChops.difference(x.convert("RGB"), y.convert("RGB")).getbbox() is None

def action_option(action, entities=(), target=""):
    """(tool, label) of an act action in option terms: a free shape (go_to, thing, buttons, a bare verb) or an option row."""
    action = action if isinstance(action, dict) else {}
    if "go_to" in action: return "go_to", str(action["go_to"])
    if "buttons" in action: return "buttons", " ".join(str(b) for b in action["buttons"])
    if "thing" in action:
        n = action["thing"]
        return str(action.get("verb") or ""), (entities[n].get("name", "") if type(n) is int and 0 <= n < len(entities) else target)
    return str(action.get("verb") or "none"), ""

def hits(entry, tool, label):
    """A right/wrong entry matches an option (or a recorded action) by a tool regex and a label regex."""
    return re.fullmatch(entry.get("tool", ".*"), tool) is not None and re.search(entry["label_re"], label, re.IGNORECASE) is not None

def is_right(case, tool, label):
    return any(hits(e, tool, label) for e in case["right"])

def stage(run):
    """runs/<run> copied once to .tmp/om/<run>; every replay reads and writes the copy only."""
    dst = Path(".tmp/om") / run
    if not dst.exists(): shutil.copytree(Path("runs") / run, dst)
    return Recording(dst)

class Recording:
    """A recorded run: the trace and key presses to check against, and the notebook ops the stubs replay."""
    def __init__(self, copy):
        self.copy = copy
        self.trace = {r["step"]: r for r in _jsonl(copy / "trace.jsonl")}
        self.presses = {r["step"]: r["keys"] for r in _jsonl(copy / "presses.jsonl")}
        self.goal_ops, self.focus, self.act_ops = defaultdict(list), {}, defaultdict(list)
        for r in _jsonl(copy / "memory" / "notebook.history"):
            s, op = r.get("step"), r.get("op")
            row = {k: v for k, v in r.items() if k != "step"}
            if op in ("set", "remove") and r.get("section") == "goals": self.goal_ops[s].append(row)  # the planner's
            elif op == "focus" and "status" not in r: self.focus[s] = r.get("key")  # the planner's focus
            elif op in ("focus", "tried") or (op in ("set", "remove") and r.get("section") in ("hints", "lessons")): self.act_ops[s].append(row)  # the act's

class Replay:
    """The model and act/plan hook for one replay. Perceiver calls are served from .cache/model (a miss is counted, never
    requested); plan and act replies come from the recording; only the act call of a live case step reaches the network."""
    def __init__(self, rec, out, *, cases=(), live=(), counter):
        self.rec, self.out, self.cases, self.live_steps, self.counter = rec, out, set(cases), set(live), counter
        self.real = Ollama(OM_MODEL, cache_dir=MODEL_CACHE); self._post_orig = self.real._post; self.real._post = self._post
        self.calls, self.misses, self.skill_misses, self.step, self.live_step, self.loop = 0, 0, 0, 0, None, None
        self.found, self.rows, self.refuted = {}, {}, {}
        self.drift = {"presses": None, "frames": None, "trace": [], "place": []}
        self.miss_steps = []
        self.script, self.armed, self.env, self.before, self.after = None, False, None, None, None  # outcome mode: (step, act reply data)
        self.snap_dir, self.snap_steps = None, ()  # run(): save a snapshot before each of these steps under snap_dir
    def _post(self, payload):
        if self.live_step is None: raise OmAbort("a network call outside the live case step")
        if self.counter["requests"] >= OM_CAP: raise run_agent.CallCap("OM request cap")
        run_agent._ledger(Path("runs/m1_om"), None, OM_CAP)  # the M1 bucket of the persistent ledger
        self.counter["requests"] += 1
        return self._post_orig(payload)
    def ask(self, system, user, images=(), schema=None, *, role=""):
        if self.armed and role == "perceiver": raise OmStop()  # the new frame's perception is not cached: never ask
        if role in ("perceiver", "skill"):  # skill: menu/keyboard reads, cached like the perceiver's
            if (MODEL_CACHE / f"{self.real._key(system, user, images, schema)}.json").is_file(): return self.real.ask(system, user, images, schema, role=role)
            if role == "perceiver":
                self.misses += 1; self.miss_steps.append(self.step)
                if self.misses > PERCEIVER_MISSES: raise OmIncompatible(f"{self.misses} perceiver cache misses (limit {PERCEIVER_MISSES}), first at step {self.miss_steps[0]}, last at step {self.step}: the old replay no longer matches the cache")
            else: self.skill_misses += 1  # the recording stored no reply here either (uncached = no data), so no data is the replay
            return Reply("", None, 0.0, None, None)  # not stored: .cache/model is never written by a miss
        if role == "plan":
            return Reply("", {"goals": self.rec.goal_ops.get(self.step, []), "focus": self.rec.focus.get(self.step) or "", "why": "", "lesson": None}, 0.0, None, None)
        if role == "act":
            if self.step in self.found and not self.found[self.step]["prompt"]:  # the first act prompt at a case step; the raw text also goes to scratch files for analysis
                self.found[self.step]["prompt"] = system + "\n" + user
                d = Path(".tmp/om/prompts_om3_1"); d.mkdir(parents=True, exist_ok=True)
                (d / f"{self.step}_system.txt").write_text(system, encoding="utf-8"); (d / f"{self.step}_user.txt").write_text(user, encoding="utf-8")
            if self.script and self.script[0] == self.step: self.armed = True; self.before = truth_read(self.env); return Reply("", self.script[1], 0.0, None, None)
            if self.live_step == self.step: return self.real.ask(system, user, images, schema, role=role)
            rec = self.rec.trace.get(self.step, {}); recorded = rec.get("act") or {}; pred = rec.get("pred") or {}
            was = rec.get("option") or {}; action = recorded.get("action") or {}
            head = lambda o: (o.get("tool"), str(o.get("label", "")).split(" (")[0])  # counts in labels change; the head names the option
            options = getattr(self, "options_now", None) or []
            matches = [o for o in options if head(o) == head(was)] if getattr(self, "by_option", False) and was else []
            if len(matches) > 1 and isinstance(action.get("thing"), int):
                matches = [o for o in matches if (o.get("action") or {}).get("thing") == action["thing"]]
            # Called names can change a generic recorded label. Cached perception keeps entity indices stable.
            if not matches and getattr(self, "by_option", False) and was and isinstance(action.get("thing"), int):
                matches = [o for o in options if o.get("tool") == was.get("tool") and (o.get("action") or {}).get("thing") == action["thing"]]
            picked = matches[0]["n"] if len(matches) == 1 else None
            return Reply("", {"place": recorded.get("place") or "", "notebook": self.rec.act_ops.get(self.step, []), "action": {**(recorded.get("action") or {}), "i": picked} if picked else recorded.get("action"),  # a row with no ready action (free buttons, name lists) falls back to the recorded shape
                              "text": (rec.get("choice") or {}).get("text"), "expect": pred.get("effect") if pred.get("basis") == "guess" and pred.get("effect") else "screen_changes",
                              "why": recorded.get("why") or ""}, 0.0, None, None)
        raise OmAbort(f"unexpected model role {role!r}")
    def _act(self, model, image, perception, notebook, facts, flags, controls, prefs, buttons, places=(), options=None, decide=False):
        """Stands in for agent.loop.act. At a case step the options are recorded; a live case step also makes the real act
        call (network only for that call), and then the step continues on the recorded reply, so the loop executes the recorded action.
        act() writes nothing, so the extra call leaves memory as the recording has it."""
        args = (model, image, perception, notebook, facts, flags, controls, prefs, buttons); self.options_now = options
        if self.step in self.cases:
            found = self.found[self.step] = {"options": options, "perception": perception, "act": None, "prompt": "", "exits": self._exits_now(perception)}
            if self.step in self.live_steps:
                self.live_step = self.step
                try: found["act"] = act(*args, places=places, options=options, decide=DECIDE)
                finally: self.live_step = None
            elif DECIDE: act(*args, places=places, options=options, decide=True)  # dry: records the decide prompt (the recorded reply has no "i")
        return act(*args, places=places, options=options)
    def _exits_now(self, perception):
        """Exit records of the current place from the inputs World.options gives _exits (for the dry exit-truth gate)."""
        world, live = self.loop.world, {p["id"] for p in self.loop.mem.rows("places") if p.get("state") != "merged"}
        things = [(i, e, world.entity_ids.get(i)) for i, e in enumerate(perception.get("entities") or []) if e.get("name") and _is_exit(e)]
        return world._exits(self.loop.place, live, things)
    def _env(self):
        env = PyBoyEnv(find_rom(), state=str(self.rec.copy / "checkpoint" / "000000" / "pyboy.state")); env.tile_px = 16  # the boot state run_agent saved
        return env
    def snapshot(self, d):
        """Emulator state + the whole Loop object graph (env and this Replay stand in as persistent ids) + the out dir (memory, trace)."""
        d.mkdir(parents=True); self.env.save(str(d / "emu.state")); buf = io.BytesIO(); pk = pickle.Pickler(buf)
        pk.persistent_id = lambda o: "env" if o is self.env else "rep" if o is self else None
        pk.dump(self.loop); (d / "loop.pkl").write_bytes(buf.getvalue()); shutil.copytree(self.out, d / "out")
    def restore(self, d):
        shutil.rmtree(self.out, ignore_errors=True); shutil.copytree(d / "out", self.out); self.env.load(str(d / "emu.state"))
        un = pickle.Unpickler(io.BytesIO((d / "loop.pkl").read_bytes())); un.persistent_load = lambda pid: self.env if pid == "env" else self
        self.loop = un.load()
        # Older replay snapshots predate IP's persisted conversation fields.
        if not hasattr(self.loop, "conversation"): self.loop.conversation = {"place": None, "lines": []}
        if not hasattr(self.loop, "text_for_trigger"): self.loop.text_for_trigger = ""
    def branch(self, d, s, script=None):
        """Run step s alone from the snapshot d. script (step, act reply data): execute that reply, stop when its presses are done."""
        self.restore(d); self.step, self.script, self.armed, self.before, self.after, self.misses = s, script, False, None, None, 0; self.found.pop(s, None)
        self.loop.decide = DECIDE  # the decide arm's loop drops the image and the red-box flag
        real, loop_mod.act = loop_mod.act, self._act
        try:
            try: self.loop.step()
            except OmStop: pass
            if self.armed: self.after = truth_read(self.env)
        finally: loop_mod.act = real
    def run(self, last):
        if self.out.exists(): shutil.rmtree(self.out)
        env = self._env()
        press, keys = env.press, []
        def logged_press(*args, **kwargs):
            keys.append(args[0] if args else kwargs.get("key")); return press(*args, **kwargs)
        env.press = logged_press; self.env = env
        mem = Memory(self.out / "memory"); self.loop = loop = Loop(env, self, mem, "pokemon_red")
        real_act, loop_mod.act = loop_mod.act, self._act
        try:
            for s in range(1, last + 1):
                wait_settled(env); self.step = loop.step_no + 1; keys.clear()  # run_agent settles before reading truth; step() settles again
                if self.step in self.snap_steps: self.snapshot(self.snap_dir / str(self.step))
                row = loop.step()
                self._check(s, row, keys, mem)
                if getattr(self, "stop_when", None) and self.stop_when(s, row): break
        finally:
            loop_mod.act = real_act; env.close()
    def _check(self, s, row, keys, mem):
        self.rows[s] = row
        d, old = self.drift, self.rec.trace.get(s, {})
        if d["presses"] is None and keys != self.rec.presses.get(s, keys): d["presses"] = s
        if d["frames"] is None and not _same_frame(self.out / "frames" / f"{s:06d}.png", self.rec.copy / "frames" / f"{s:06d}.png"): d["frames"] = s
        mine = ((row.get("choice") or {}).get("target"), row.get("verdict"))
        theirs = ((old.get("choice") or {}).get("target"), old.get("verdict"))
        if mine != theirs: d["trace"].append((s, mine, theirs))
        if row.get("place") != old.get("place"): d["place"].append((s, row.get("place"), old.get("place")))  # naming differs by design; counted, not a drift
        ids = {e["id"] for e in mem.rows("entities") if re.fullmatch(re.escape(FAKE_EXIT) + r"(#\d+)?", e.get("name") or "")}
        for r in mem.rows("relations"):
            if r.get("s") in ids and r.get("p") == "does_nothing" and r.get("state") == "refuted" and r["s"] not in self.refuted:
                self.refuted[r["s"]] = {"step": s, "con": r.get("con"), "sup": r.get("sup"), "ev": list(r.get("ev") or [])}

def om_places():
    counter = {"requests": 0}; before = _budget()
    rec = stage("m1_gp5"); last = max(rec.trace)
    rep = Replay(rec, rec.copy / "om_places", counter=counter); rep.run(last)
    loop, mem, world = rep.loop, rep.loop.mem, rep.loop.world
    places = [p for p in mem.rows("places") if p.get("state") != "merged"]
    print(f"places after replaying m1_gp5 steps 1-{last} with the new code ({len(places)} places):")
    for p in sorted(places, key=lambda p: p["id"]): print(f"  {p['id']:<12} screen={p.get('screen')!s:<8} label={world.place_label(p['id'])!r} aliases={p.get('aliases', [])} visits={p.get('visits', 0)}")
    bad_screen = [p["id"] for p in places if p.get("screen") in ("dialogue", "menu", "title")]
    # the names the model sees: each label without its own [id] suffix, plus the aliases stored for the place
    names = [(p["id"], n) for p in places for n in [re.sub(r"\s*\[[^\]]*\]$", "", world.place_label(p["id"])), *p.get("aliases", [])]]
    bad_label = sorted({f"{n} ({i})" for i, n in names if n.casefold() in SCREEN_TYPES})
    gate_places = not bad_screen and not bad_label
    print(f"gate places: dialogue/menu/title places = {len(bad_screen)} (must be 0); known-place names or aliases equal to a screen type = {bad_label or 0} (must be 0): {'PASS' if gate_places else 'FAIL'}")
    refuted = [r for r in mem.rows("relations") if r.get("p") == "does_nothing" and r.get("o") == "" and r.get("state") == "refuted"]
    print(f"refuted things (does_nothing, state refuted): {len(refuted)}")
    for r in refuted:
        name = (mem.get("entities", r["s"]) or {}).get("name", r["s"])
        print(f"  {name} ({r['s']}) con={r.get('con')} sup={r.get('sup')}{world._notes(r['s'])}")
    fake_ids = [e["id"] for e in mem.rows("entities") if re.fullmatch(re.escape(FAKE_EXIT) + r"(#\d+)?", e.get("name") or "")]
    fake = {i: rep.refuted[i] for i in fake_ids if i in rep.refuted}
    shown = [line for line in world.view(loop._goal_text(), loop.place).splitlines() if FAKE_EXIT in line and "not working" in line]
    for i, info in fake.items(): print(f"fake exit {FAKE_EXIT!r} ({i}): refuted at step {info['step']} after {info['con']} failed attempts (sup={info['sup']})")
    if not fake: print(f"fake exit {FAKE_EXIT!r}: never refuted")
    print("rendered evidence: " + (" | ".join(shown) if shown else "none"))
    gate_fake = bool(fake) and all(info["con"] <= 3 for info in fake.values()) and bool(shown)
    print(f"gate fake exit refuted by the 3rd failed attempt with evidence rendered: {'PASS' if gate_fake else 'FAIL'}")
    d = rep.drift
    print(f"drift vs recording: first press divergence step={d['presses']}, first frame divergence step={d['frames']}, steps whose choice/verdict differ={len(d['trace'])}" + (f" (first: step {d['trace'][0][0]} new {d['trace'][0][1]} old {d['trace'][0][2]})" if d["trace"] else ""))
    print(f"place names differing from the recording (naming is new code's, counted only)={len(d['place'])}")
    print(f"perceiver cache misses={rep.misses} at steps {rep.miss_steps}; skill reads with no stored reply={rep.skill_misses}; requests={counter['requests']} (cap {OM_CAP}); ledger before total={before.get('total')} m1={before.get('m1')} after total={_budget().get('total')} m1={_budget().get('m1')}")
    gate_requests = counter["requests"] <= OM_CAP
    print(f"gate requests <= {OM_CAP}: {'PASS' if gate_requests else 'FAIL'}")
    ok = gate_places and gate_fake and gate_requests
    print(f"PLACES GATE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1

def dry_gates(d):
    """OM2 section 2 dry gates for one case, from recorded data, no requests. d: {"case": its om_cases.json entry, "options": the
    option list at its step, "prompt": the act system+user text, "exits": World._exits records of its place}.
    Returns (gate, ok, detail) rows: right present, exit truth, size."""
    c, opts, exits = d["case"], d.get("options") or [], d.get("exits") or []
    tag, label_re = f"step {c['step']}", c["right"][0]["label_re"]  # 2F cases: the stairs; 1F cases: the door mat / rug
    present = any(is_right(c, o["tool"], o["label"]) for o in opts)
    exit_opts = [o for o in opts if o["label"].startswith(EXIT_ROWS)]
    why, floor = [], FLOOR_OF_MAP.get(c["truth"]["map"])
    if floor == "2F":  # the stairs are the one verified exit; the other exit rows are possible exit / not an exit by construction
        verified = [o for o in exit_opts if o["label"].startswith("exit ")]
        if len(verified) != 1: why.append(f"{len(verified)} verified exits (need the stairs only)")
        elif not re.search(label_re, verified[0]["label"], re.IGNORECASE): why.append(f"the verified exit is not the stairs: {verified[0]['label']!r}")
        elif " beyond)" not in verified[0]["label"]: why.append("the stairs label has no beyond-summary")
        if len(verified) == 1 and "everything unexplored from here is through this exit" not in (verified[0].get("notes") or []): why.append("the stairs are not named as the way to everything unexplored")
        why += [f"explore label reports unseen spots: {o['label']!r}" for o in opts if o["tool"] == "explore" and "(0 spots lead anywhere unseen)" not in o["label"]]
    elif floor == "1F":  # the door mat / rug is an unused (unverified) exit
        rug = [o for o in exit_opts if re.search(label_re, o["label"], re.IGNORECASE)]
        if not rug: why.append("no exit row for the door mat / rug")
        elif any(not o["label"].startswith("possible exit ") for o in rug): why.append(f"the door mat / rug is not an unused exit: {rug[0]['label']!r}")
    else: why.append(f"no floor for truth map {c['truth']['map']}")
    dups = [k for k, v in Counter((x["dest"], tuple(x["tile"])) for x in exits if x.get("tile") is not None).items() if v > 1]
    if dups: why.append(f"two exit records with the same destination and tile: {dups}")
    non_free = [o for o in opts if not (o["tool"] == "buttons" and o.get("action") is None)]  # the free-buttons row is not counted
    tokens, size = len(d.get("prompt") or "") / 4, []
    if len(non_free) > MAX_OPTIONS: size.append(f"options over {MAX_OPTIONS}")
    if tokens > MAX_PROMPT_TOKENS: size.append(f"prompt over {MAX_PROMPT_TOKENS} tokens")
    return [("right present", present, f"{tag}: " + ("a right option is listed" if present else "no right option is listed")),
            ("exit truth", not why, f"{tag}: " + ("; ".join(why) or "ok")),
            ("size", not size, f"{tag}: {len(non_free)} options, prompt {tokens:.0f} tokens" + (f" ({'; '.join(size)})" if size else ""))]

def dry_summary(rows, n):
    """Prints the DRY GATES line for the (gate, ok, detail) rows of n cases; True when every gate passes on every case."""
    got = {g: sum(ok for name, ok, _ in rows if name == g) for g in ("right present", "exit truth", "size")}
    ok = all(v == n for v in got.values())
    print(f"\nDRY GATES: right present {got['right present']}/{n}, exit truth {got['exit truth']}/{n}, size {got['size']}/{n}: {'PASS' if ok else 'FAIL'}")
    return ok

def snap_base(run):
    """.tmp/om/snapshots/<run>/<key>: the key hashes what determines a snapshot (recording, agent/ code, this script, perceiver cache)."""
    h, git = hashlib.sha256(), lambda *a: subprocess.run(["git", *a], capture_output=True).stdout
    for f in ("trace.jsonl", "presses.jsonl"): h.update((Path(".tmp/om") / run / f).read_bytes())
    for part in (git("rev-parse", "HEAD:agent"), git("diff", "HEAD", "--", "agent"), Path(__file__).read_bytes(), "".join(sorted(p.name for p in MODEL_CACHE.iterdir())).encode()): h.update(part)
    return Path(".tmp/om/snapshots") / run / h.hexdigest()[:12]

def ready(run, cases, live, counter, fresh=False):
    """A Replay for the run with its emulator open and every snapshot of the run's case steps present (replayed from boot once, if not)."""
    rec, base = stage(run), snap_base(run); every = {c["step"] for c in json.loads(OM_CASES.read_text(encoding="utf-8")) if c["run"] == run} | {c["step"] for c in cases}
    if fresh or not all((base / str(s) / "loop.pkl").exists() for s in every):
        shutil.rmtree(base.parent, ignore_errors=True); builder = Replay(rec, rec.copy / "om_branch", cases=(), counter=counter); builder.snap_dir, builder.snap_steps = base, every; builder.run(max(every))
    rep = Replay(rec, rec.copy / "om_branch", cases={c["step"] for c in cases}, live={c["step"] for c in cases} if live else (), counter=counter)
    rep.env, rep.base = rep._env(), base
    return rep

def replay_cases(cases, live, counter, fresh=False):
    """(case, replay) pairs; each case step is run alone from its snapshot. live: the step makes its one real act call."""
    results = []
    for run in dict.fromkeys(c["run"] for c in cases):
        mine = [c for c in cases if c["run"] == run]; rep = ready(run, mine, live, counter, fresh)
        try:
            for c in mine: rep.branch(rep.base / str(c["step"]), c["step"])
        finally: rep.env.close()
        results += [(c, rep) for c in mine]
    return results

def dry_report(cases, counter, fresh=False):
    """The dry replay (no live step, so no request): prints each case's options, prompt size and gate rows.
    Returns (gate rows, right count of the recorded arm)."""
    rows, old_right = [], 0
    for c, rep in replay_cases(cases, False, counter, fresh):
        s, f, old = c["step"], rep.found.get(c["step"]) or {}, rep.rec.trace.get(c["step"], {})
        old_tool, old_label = action_option((old.get("act") or {}).get("action"), target=(old.get("choice") or {}).get("target") or "")
        old_ok = is_right(c, old_tool, old_label); old_right += old_ok
        opts = f.get("options") or []
        print(f"\ncase {c['run']} step {s}  truth map={c['truth']['map']} ({c['truth']['x']},{c['truth']['y']})  {c['situation']}")
        print(f"  old arm: {old_tool} {old_label!r} -> {'right' if old_ok else 'wrong'}  (recorded why: {str((old.get('act') or {}).get('why'))[:100]})")
        for o in opts: print(f"  option {o['n']:>2}: {o['tool']} {o['label']!r}{'  [right]' if is_right(c, o['tool'], o['label']) else ''}" + (f" — {'; '.join(o['notes'])}" if o.get("notes") else ""))
        if not opts: print("  no options recorded at this step")
        gates = dry_gates({"case": c, "options": opts, "prompt": f.get("prompt") or "", "exits": f.get("exits") or []})
        print(f"  act prompt: {len(f.get('prompt') or '') / 4:.0f} tokens (len/4)")
        for g, ok, detail in gates: print(f"  gate {g}: {'PASS' if ok else 'FAIL'}  {detail}")
        rows += gates
    return rows, old_right

def live_cases(cases, counter, before, old_right, fresh=False):
    """The live replay: one real act call per case step (at most OM_CAP requests), then the decision-point gates."""
    new_right = 0
    for c, rep in replay_cases(cases, True, counter, fresh):
        s, f = c["step"], rep.found.get(c["step"]) or {}; opts, res = f.get("options") or [], f.get("act")
        if res is None: print(f"\ncase {c['run']} step {s} new arm: no act reply"); continue
        if res.option is not None and 1 <= res.option <= len(opts): tool, label = opts[res.option - 1]["tool"], opts[res.option - 1]["label"]
        else: tool, label = action_option(res.action, entities=(f.get("perception") or {}).get("entities", []))
        new_ok = is_right(c, tool, label); new_right += new_ok
        print(f"\ncase {c['run']} step {s} new arm: option {res.option} {tool} {label!r} -> {'right' if new_ok else 'wrong'}  why: {res.why[:120]}")
    n = len(cases)
    print(f"\nDECISION POINTS: {'decide' if DECIDE else 'options'} arm right {new_right}/{n} (gate >= {DECISION_GATE})")
    print(f"OLD ARM right {old_right}/{n} (gate <= {OLD_GATE})")
    print(f"requests={counter['requests']} (cap {OM_CAP}); ledger before total={before.get('total')} m1={before.get('m1')} after total={_budget().get('total')} m1={_budget().get('m1')}")
    ok = new_right >= DECISION_GATE and old_right <= OLD_GATE and counter["requests"] <= OM_CAP
    print(f"CASES GATE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1

def om_cases(live, steps=None, fresh=False):
    """Dry first always (0 requests). --live runs the live replay only when every dry gate passes."""
    cases = json.loads(OM_CASES.read_text(encoding="utf-8")); cases = [c for c in cases if not steps or str(c["step"]) in steps.split(",")]; counter = {"requests": 0}; before = _budget()
    rows, old_right = dry_report(cases, counter, fresh)
    dry_ok = dry_summary(rows, len(cases))
    if not live:
        print(f"DRY: old arm right (recorded actions) {old_right}/{len(cases)}")
        print(f"requests={counter['requests']} (cap {OM_CAP}); ledger before total={before.get('total')} m1={before.get('m1')} after total={_budget().get('total')} m1={_budget().get('m1')}")
        print(f"CASES GATE: {'PASS' if dry_ok else 'FAIL'} (dry)")
        return 0 if dry_ok else 1
    if not dry_ok:
        print("DRY GATES FAILED: live measure not run")
        return 1
    return live_cases(cases, counter, before, old_right)  # snapshots exist after the dry pass

# ---- outcome scoring: run an action from the replayed state at a case step, read RAM truth when its presses are done (0 requests)
RIGHT_MAP = {37: 0, 38: 37}  # the map a "right" exit leads to, per map (1F -> outside, 2F -> 1F)
LIVE_REPLIES = {  # cached act replies (.cache/model file prefixes) of the live runs, by case step
    "OM3 live": {82: "ef82b2", 102: "eedd62", 103: "6551ae", 110: "f187b1", 113: "4e942e", 115: "e23e98", 122: "dd4f14", 137: "5c021c"},
    "OM3.2 live": {82: "35fb04", 103: "44185e", 137: "c201e6"}}

def classify(before, after, goal_map, goal_tiles):
    """crossed_right | crossed_other | closer | no_move | other, from RAM truth {map,x,y} before and after one action."""
    if after["map"] != before["map"]: return "crossed_right" if after["map"] == goal_map else "crossed_other"
    if (after["x"], after["y"]) == (before["x"], before["y"]): return "no_move"
    dist = lambda t: min((abs(t["x"] - x) + abs(t["y"] - y) for x, y in goal_tiles), default=None)
    return "closer" if goal_tiles and dist(after) < dist(before) else "other"

def goal_tiles(run_names, from_map, to_map):
    """Tiles of from_map where the recorded runs' RAM truth is followed by to_map (the row before each crossing)."""
    out = set()
    for run in run_names:
        rows = _jsonl(Path(".tmp/om") / run / "truth.jsonl")
        out |= {(a["x"], a["y"]) for a, b in zip(rows, rows[1:]) if a["map"] == from_map and b["map"] == to_map}
    return out

def om_outcomes(steps=None, fresh=False):
    cases = [c for c in json.loads(OM_CASES.read_text(encoding="utf-8")) if not steps or str(c["step"]) in steps.split(",")]
    counter, before = {"requests": 0}, _budget(); runs = sorted({c["run"] for c in cases} | {"m1_gp2", "m1_gp5"}); [stage(r) for r in runs]
    reps = {r: ready(r, [c for c in cases if c["run"] == r], False, counter, fresh) for r in dict.fromkeys(c["run"] for c in cases)}
    print("case | action (source) | outcome | before (map,x,y) -> after (map,x,y)")
    for c in cases:
        s, rep = c["step"], reps[c["run"]]
        rep.branch(rep.base / str(s), s); opts = rep.found[s]["options"]  # the options this step offers
        right = next((o["n"] for o in opts if is_right(c, o["tool"], o["label"])), None)
        todo = [(src, json.loads((next(MODEL_CACHE.glob(f"{p[s]}*.json"))).read_text(encoding="utf-8"))["data"]) for src, p in LIVE_REPLIES.items() if s in p]
        todo.append((f"right option {right}", {"action": {"i": right}, "why": "", "expect": "screen_changes"}))
        for src, data in todo:
            rep.branch(rep.base / str(s), s, (s, data))
            b, a = rep.before, rep.after
            if b is None or a is None: print(f"{s} | {src} | NO RESULT (armed={rep.armed}) |"); continue
            act_txt = json.dumps(data["action"]); tiles = goal_tiles(runs, b["map"], RIGHT_MAP.get(b["map"]))
            print(f"{s} | {src} {act_txt} | {classify(b, a, RIGHT_MAP.get(b['map']), tiles)} | ({b['map']},{b['x']},{b['y']}) -> ({a['map']},{a['x']},{a['y']})", flush=True)
    for r in reps.values(): r.env.close()
    print(f"requests={counter['requests']}; ledger before={before}; after={_budget()}; unchanged={before == _budget()}")
    return 0

def _old_perceiver():
    """Recordings made before the perceiver's place field (m1_gp2/gp5, m1_decide) were cached under the prompt without it."""
    import agent.perceive as P
    P.PROMPT, P.SCHEMA = P.PROMPT.replace(P.PLACE_LINE, ""), {**P.SCHEMA, "properties": {k: v for k, v in P.SCHEMA["properties"].items() if k != "place"}}

def om_truth(run, no_names=False, exact_exit=False):
    """Replays a recorded run with today's code (recorded decisions, cached perception, 0 requests) and checks place identity
    against RAM truth: each agent place should cover one game map. Stops where the replay leaves the perceiver cache."""
    if no_names: _old_perceiver()
    if exact_exit:  # ablation: the exit lookup as before fix 2 (exact tile and direction only), so the recording holds longer
        real = Memory.match_place
        def exact(self, per, step, *, exit=None, **kw):
            edge = (self.get("tiles", f"{kw.get('prev')}@{exit[0]},{exit[1]}") or {}).get("exit") if exit else None
            return real(self, per, step, exit=exit if edge and edge.get("dir") == exit[2] else None, **kw)
        Memory.match_place = exact
    counter, before = {"requests": 0}, _budget(); rec = stage(run); last = max(rec.trace)
    rep = Replay(rec, rec.copy / "om_truth", cases=range(1, last + 1), counter=counter); seen, check = [], rep._check; rep.by_option = True
    def checked(s, row, keys, mem):
        check(s, row, keys, mem); seen.append((s, row.get("next_place"), truth_read(rep.env)["map"]))
        if seen[-2:-1] and seen[-2][2] == 0 and (rep.found.get(s) or {}).get("options"):  # this step started outside (map 0): what was offered
            print(f"step {s} outside ({rep.loop.world.place_label(row.get('place'))}):")
            for o in rep.found[s]["options"][:4]: print(f"  option {o['n']}: {o['tool']} {o['label']!r}" + (f" — {'; '.join(o['notes'])}" if o["notes"] else ""))
    rep._check = checked
    try: rep.run(last)
    except OmAbort as e: print(f"replay stopped: {e}")
    maps, places = defaultdict(Counter), defaultdict(set)
    for s, pid, m in seen:
        if pid and (rep.loop.mem.get("places", pid) or {}).get("screen") == "world": maps[pid][m] += 1; places[m].add(pid)
    for pid, c in sorted(maps.items()): print(f"  {pid:<12} label={rep.loop.world.place_label(pid)!r:<28} maps={dict(c)}")
    mixed = [pid for pid, c in maps.items() if len(c) > 1]
    print(f"steps replayed={len(seen)} of {last}; first press divergence={rep.drift['presses']}; perceiver misses={rep.misses}")
    print(f"MIXED places (cover >1 map)={mixed or 0}; ids per map={ {m: len(v) for m, v in sorted(places.items())} }")
    print(f"requests={counter['requests']}; ledger unchanged={before == _budget()}")
    return 0 if not mixed else 1

def om_branch(run, step, pick):
    """Steps from a run_agent checkpoint with today's code (0 requests): cached perception, an empty plan, and per step the option whose label
    contains the next comma-separated `pick` ("" = print the options and stop); prints each step's options and RAM truth. Stops at a perceiver cache miss."""
    picks = pick.split(",")
    cp = Path("runs") / run / "checkpoint" / f"{step:06d}"; out = Path(".tmp/om") / f"{run}_branch_{step}"
    shutil.rmtree(out, ignore_errors=True); shutil.copytree(cp / "memory", out / "memory")
    env = PyBoyEnv(find_rom(), state=str(cp / "pyboy.state")); env.tile_px = 16; real = Ollama(OM_MODEL, cache_dir=MODEL_CACHE)
    shown = {"log": []}
    class Stub:
        calls = 0
        def ask(self, system, user, images=(), schema=None, *, role=""):
            if role in ("perceiver", "skill"):
                if (MODEL_CACHE / f"{real._key(system, user, images, schema)}.json").is_file(): return real.ask(system, user, images, schema, role=role)
                if role == "perceiver": raise OmStop()
                return Reply("", None, 0.0, None, None)
            if role == "plan": return Reply("", {"goals": [], "focus": "", "why": "", "lesson": None}, 0.0, None, None)
            opts, want = shown["options"], picks[len(shown["log"])]
            for o in opts: print(f"  option {o['n']}: {o['tool']} {o['label']!r} -> {o['action']}" + (f" — {'; '.join(o['notes'])}" if o["notes"] else ""))
            o = next((o for o in opts if want and want.casefold() in o["label"].casefold()), None)
            if o is None: raise OmStop()
            shown["log"].append(o["label"]); return Reply("", {"place": "", "notebook": [], "action": {"i": o["n"]}, "why": "branch", "expect": "place_changes"}, 0.0, None, None)
    loop = Loop(env, Stub(), Memory(out / "memory"), "pokemon_red"); loop.restore(json.loads((cp / "harness.json").read_text()))
    real_act = loop_mod.act
    def act(*args, options=None, **kw): shown["options"] = options; return real_act(*args, options=options, **kw)
    loop_mod.act = act; first = truth_read(env)
    try:
        for n in range(len(picks)):
            before = truth_read(env); print(f"step {loop.step_no + 1}: truth (map,x,y)=({before['map']},{before['x']},{before['y']})"); loop.step()
            after = truth_read(env); print(f"  picked {shown['log'][-1]!r} -> truth ({after['map']},{after['x']},{after['y']})")
    except OmStop: print("  stopped (no pick, or the new frame's perception is not cached)")
    finally: loop_mod.act = real_act
    last = truth_read(env); env.close(); print("requests=0")
    return 0 if last["map"] != first["map"] else 1

def _dialogue_entries(notebook):
    return [e for e in notebook.log if re.match(r"[^:]+ at .+: \"", str(e.get("text", "")))]

def om_talk(run):
    counter = {"requests": 0}; rec = stage(run); last = max(rec.trace)
    rep = Replay(rec, rec.copy / "om_talk", cases=range(1, last + 1), counter=counter); rep.by_option = True
    sentence = "PROF. OAK, next door, is looking for you."
    def complete(step, row):
        target = next((e for e in _dialogue_entries(rep.loop.notebook)
                       if sentence in str(e.get("text", "")) and str(e.get("text", "")).startswith("MOM at ")), None)
        places = {e.get("place") for e in rep.loop.mem.rows("entities") if str(e.get("called", "")).casefold() == "mom"}
        options = (rep.found.get(step) or {}).get("options", [])
        return bool(target and step > int(target.get("step", 0)) and row.get("place") in places
                    and any(o.get("tool") == "interact" and o.get("label", "").casefold().startswith("interact with mom") for o in options))
    rep.stop_when = complete
    try: rep.run(last)
    except OmIncompatible: raise  # inconclusive, not a verdict on the talk: om_main reports exit 2
    except OmAbort as e: print(f"replay stopped: {e}")
    loop = rep.loop
    if loop is None: return 1
    entries = _dialogue_entries(loop.notebook)
    target = next((e for e in entries if sentence in str(e.get("text", "")) and str(e.get("text", "")).startswith("MOM at ")), None)
    print("Dialogue notebook entries:")
    for e in entries: print(f"  step {e.get('step')}: {e.get('text')}")
    called = [e for e in loop.mem.rows("entities") if e.get("called")]
    print("Entities with called names:")
    for e in called: print(f"  {e.get('id')}: {e.get('called')} (perceived {e.get('name')}; place {e.get('place')})")
    start = int(target.get("step", 0)) if target else 0
    places = {e.get("place") for e in called if str(e.get("called", "")).casefold() == "mom"}
    later = next(((s, rep.found[s]["options"]) for s in sorted(rep.found)
                  if target and s > start and rep.rows.get(s, {}).get("place") in places
                  and any(o.get("tool") == "interact" and o.get("label", "").casefold().startswith("interact with mom") for o in rep.found[s]["options"])), None)
    if later:
        s, options = later; print(f"First later step at a called entity's place with an interact option: {s}")
        for o in options: print(f"  option {o['n']}: {o['tool']} {o['label']!r} -> {o.get('action')}")
    else: print("No later interact option found at a called entity's place.")
    print(f"requests={counter['requests']}")
    return 0 if target and any(str(e.get("called", "")).casefold() == "mom" for e in called) and later else 1

def _om_scratch(name):
    root = Path(".tmp/om").resolve(); target = (root / name).resolve()
    if target == root or root not in target.parents: raise ValueError(f"unsafe OM scratch path: {target}")
    return target

def _plan_checkpoint(run, step):
    cp = Path("runs") / run / "checkpoint" / f"{step:06d}"
    out = _om_scratch(f"{run}_plan_{step}")
    shutil.rmtree(out, ignore_errors=True); shutil.copytree(cp / "memory", out / "memory")
    env = PyBoyEnv(find_rom(), state=str(cp / "pyboy.state")); env.tile_px = 16
    loop = Loop(env, None, Memory(out / "memory"), "pokemon_red")
    loop.restore(json.loads((cp / "harness.json").read_text(encoding="utf-8")))
    return loop, env

def _plan_prompt(trigger, notebook, view, screen_text, recent):
    return "\n".join((f"Trigger: {trigger}", f"Goal tree (focus: {notebook.focus or 'none'}):\n{notebook.render()}",
                       f"Log (what already happened):\n{notebook.render_log(40)}", f"Map and memory:\n{view or 'none'}",
                       f"Text on screen now: {'; '.join(str(t) for t in screen_text) or 'none'}",
                       f"Recent actions: {'; '.join(recent) or 'none'}", plan_mod.REPLY))

def _plan_recent(run, step):
    rows = sorted((r for s, r in stage(run).trace.items() if s <= step), key=lambda r: r["step"])[-5:]
    return [f"{(r.get('choice') or {}).get('verb')} {(r.get('choice') or {}).get('target') or ''} -> {r.get('verdict')}" for r in rows]

def _independent_notebook(source, root):
    notebook = Notebook(root)
    notebook.data, notebook.focus, notebook.log = deepcopy(source.data), source.focus, deepcopy(source.log)
    return notebook

def om_plan(run, step, tries, live=False):
    if not 1 <= tries <= 8: raise ValueError("--tries must be between 1 and 8")
    loop, env = _plan_checkpoint(run, step)
    try:
        per = loop.prev or {}
        recent = _plan_recent(run, step)
        view = loop.world.view(loop._goal_text(), loop.place)
        if step == 50:
            dialogue = _dialogue_entries(loop.notebook)
            last = str((dialogue or loop.notebook.log or [{"text": ""}])[-1].get("text", ""))
            trigger = f"new game text: {last}"
        else: trigger = f"arrived somewhere new: {loop.world.place_label(loop.place)}"
        prompt = _plan_prompt(trigger, loop.notebook, view, per.get("text", []), recent)
        if not live:
            before = _budget()
            print(f"SYSTEM:\n{plan_mod.SYSTEM}\n\nUSER:\n{prompt}\n\nJSON SCHEMA:\n{json.dumps(plan_mod.SCHEMA, ensure_ascii=False)}")
            print(f"requests=0; ledger before={before}; after={_budget()}; unchanged={before == _budget()}")
            return 0

        before = _budget(); counter = {"requests": 0}; model = Ollama(OM_MODEL, cache_dir=None); post = model._post
        def guarded(payload):
            if counter["requests"] >= tries: raise run_agent.CallCap("planner request cap")
            run_agent._ledger(Path("runs/m1_om"), None, 8)
            counter["requests"] += 1
            return post(payload)
        model._post = guarded
        successes = 0; target = re.compile(r"oak|next door", re.I)
        print(f"ledger before={before}; tries={tries}; request cap={tries}")
        for n in range(1, tries + 1):
            trial = _independent_notebook(loop.notebook, Path(".tmp/ip") / f"{run}_plan_{step}_{n}")
            try:
                result = plan_mod.plan(model, trigger, trial, view, per.get("text", []), recent)
                trial.apply(result.ops, step); trial.set_focus(result.focus or trial.focus, step)
                path = trial.path(); path_text = " > ".join(str(trial.data["goals"].get(k, {}).get("text", "")) for k in path)
                print(f"try {n}: facts={json.dumps(result.facts, ensure_ascii=False)}; goals={json.dumps(result.ops, ensure_ascii=False)}; focus={result.focus!r}; path={path_text!r}; why={result.why!r}")
                if step == 50:
                    heard_goal = any((op.get("value") or {}).get("source") == "heard" and re.search(r"oak", str((op.get("value") or {}).get("text", "")), re.I) for op in result.ops)
                    heard_fact = any(re.search(r"oak", str(f.get("text", "")), re.I) and re.search(r"next door", str(f.get("text", "")), re.I) for f in result.facts)
                    successes += bool(heard_goal and heard_fact)
                else: successes += bool(target.search(path_text))
            except (run_agent.CallCap, ModelUnavailable) as e: print(f"try {n}: failed: {e}")
        print(f"successes={successes}/{tries}; requests={counter['requests']}; ledger before={before}; after={_budget()}")
        return 0 if successes >= math.ceil(2 * tries / 3) else 1
    finally: env.close()

# ---- --om reliability (HF5 WP B): one production Loop step, from a copy of runs/<run>/checkpoint/<step>, executes the recorded
# choice (the act is scripted to it). Perception, skill reads and any continuation act come from .cache/model only; a miss stops
# that part (never requested, never refilled). RAM truth is read here, for the assertions only; agent/ never sees it.
HF5 = Path(".tmp/hf/HF5")  # scratch: a copy of each checkpoint; runs/ and .cache/ are only read
RELIABILITY_CONTINUATION = 3  # autonomous steps after the primitive

class CacheMiss(Exception):  # a model call the cache does not hold: nothing is requested and the step stops here
    def __init__(self, role, step): super().__init__(f"{role} cache miss at step {step}"); self.role, self.step = role, step
class Unexecuted(Exception): pass  # the primitive could not run the recorded choice

def _hf5_path(name):
    root = HF5.resolve(); target = (root / name).resolve()
    if target == root or root not in target.parents: raise ValueError(f"unsafe HF5 scratch path: {target}")
    return target

def _ram_actors(ram):
    """Tiles of the non-player sprites, relative to the player (16 px tiles; sprite slot 0 is the player)."""
    player = next((s for s in ram["sprites"] if s["slot"] == 0), None)
    if player is None: raise Unexecuted("RAM shows no player sprite")
    return {(round((s["x"] - player["x"]) / 16), round((s["y"] - player["y"]) / 16)) for s in ram["sprites"] if s["slot"] != 0}

def _primitive_checks(before, after, skill, leg, pos0, keys):
    """The primitive's RAM-truth assertions: [(name, passed, detail)]. Route and first press are judged against the actor tiles
    RAM shows; the reported failure stage against where RAM shows the walk ended."""
    if "exit_tile" not in leg: raise Unexecuted("the recorded route has no exit tile to judge the stage against")
    actors = _ram_actors(before)
    route = [pos0]
    for d in leg.get("dirs") or []: route.append((route[-1][0] + DIRS[d][0], route[-1][1] + DIRS[d][1]))
    held = {(pos0[0] + dx, pos0[1] + dy) for dx, dy in actors}
    hit = [t for t in route[1:] if t in held]
    first_offset = DIRS.get(keys[0]) if keys else None  # a button press (A, B, START) walks nowhere
    into = first_offset is not None and first_offset in actors
    moved = (before["map"], before["x"], before["y"]) != (after["map"], after["x"], after["y"])
    crossed = after["map"] != before["map"]
    here, exit_rel = (after["x"] - before["x"], after["y"] - before["y"]), (leg["exit_tile"][0] - pos0[0], leg["exit_tile"][1] - pos0[1])
    expected = None if crossed else "crossing" if here == exit_rel else "route"
    reported = (skill.failure or {}).get("stage")
    return [
        ("route excludes the actor tile", not hit, f"route {route[1:]}; actor tiles {sorted(held)}; overlap {hit or 'none'}"),
        ("first press is not into the actor", not into, f"first press {keys[0] if keys else 'none'}; actor offsets {sorted(actors)}"),
        ("position changes vs before", moved, f"(map,x,y) {(before['map'], before['x'], before['y'])} -> {(after['map'], after['x'], after['y'])}"),
        ("reported failure stage matches RAM", reported == expected and (expected is not None or skill.status == "done"),
         f"reported {reported!r} ({skill.status}: {skill.reason!r}); RAM expects {expected!r}"),
    ]

def _rel_finish(primitive, continuation, counter, before):
    after = _budget(); unchanged = before == after
    print(f"ledger total {before.get('total')} -> {after.get('total')}; m1 {before.get('m1')} -> {after.get('m1')}")
    print(f"ledger unchanged: {'yes' if unchanged else 'no'}")
    print(f"reliability: primitive={primitive} continuation={continuation} requests={counter['requests']} ledger_unchanged={'yes' if unchanged else 'no'}")
    if counter["requests"] or not unchanged: return 2  # the zero-request contract broke: this run is no evidence
    if primitive == "FAIL": return 1
    return 0 if primitive == "PASS" and continuation.startswith("COMPLETED") else 2  # a cache miss or an unfinished continuation is no pass

def om_reliability(run, step):
    counter, before = {"requests": 0}, _budget()
    cp, first = Path("runs") / run / "checkpoint" / f"{step:06d}", step + 1
    trace, presses_file = Path("runs") / run / "trace.jsonl", Path("runs") / run / "presses.jsonl"
    rec = next((r for r in _jsonl(trace) if r.get("step") == first), None) if trace.exists() else None
    missing = [n for n in ("memory", "pyboy.state", "harness.json") if not (cp / n).exists()] + ([] if rec else [f"trace row {first}"])
    if missing: print(f"missing checkpoint evidence in {cp}: {', '.join(missing)}"); return _rel_finish("INCONCLUSIVE", "NOT RUN", counter, before)
    rec_action, rec_skill = (rec.get("act") or {}).get("action"), rec.get("skill") or {}
    presses = next((r["keys"] for r in _jsonl(presses_file) if r.get("step") == first), []) if presses_file.exists() else []
    print(f"recorded step {first}: place {rec.get('place')!r}, choice {(rec.get('choice') or {}).get('verb')} {(rec.get('choice') or {}).get('target')!r}, "
          f"action {rec_action}, skill {rec_skill.get('status')} ({rec_skill.get('reason')!r}), presses {presses}")
    try:
        work = _hf5_path(f"{run}_{step:06d}")
        shutil.rmtree(work, ignore_errors=True); shutil.copytree(cp / "memory", work / "memory")
        for name in ("pyboy.state", "harness.json"): shutil.copy2(cp / name, work / name)
        env = PyBoyEnv(find_rom(), state=str(work / "pyboy.state")); env.tile_px = 16
    except (OSError, ValueError) as e:
        print(f"cannot start the replay: {e}"); return _rel_finish("INCONCLUSIVE", "NOT RUN", counter, before)
    real = Ollama(OM_MODEL, cache_dir=MODEL_CACHE)
    def no_http(payload):
        counter["requests"] += 1; raise OmAbort("HTTP attempted by the reliability replay")
    real._post = no_http
    st = {"phase": "primitive", "loop": None, "options": [], "captured": [], "travel": None, "keys": []}
    def cached(system, user, images, schema): return (MODEL_CACHE / f"{real._key(system, user, images, schema)}.json").is_file()
    class Stub:
        calls = seq = 0
        def ask(self, system, user, images=(), schema=None, *, role=""):
            if role == "plan": return Reply("", {"goals": [], "focus": "", "why": "", "lesson": None}, 0.0, None, None)  # not perception: no planner call
            if role not in ("perceiver", "skill", "act"): raise Unexecuted(f"unexpected model role {role!r}")
            if st["phase"] == "primitive" and role == "act":  # the recorded choice, by its action on the offered options
                n = next((o["n"] for o in st["options"] if o.get("action") == rec_action), None)
                if n is None: raise Unexecuted(f"the recorded choice {rec_action} is not offered at step {first}")
                return Reply("", {"place": "", "notebook": [], "action": {"i": n}, "text": None, "expect": "screen_changes", "why": "reliability: the recorded choice"}, 0.0, None, None)
            if not cached(system, user, images, schema): raise CacheMiss(role, st["loop"].step_no + 1)
            return real.ask(system, user, images, schema, role=role)
    real_act = loop_mod.act
    def act(*args, options=None, **kwargs):
        st["options"] = options or []; return real_act(*args, options=options, **kwargs)
    loop_mod.act = act
    env_press = env.press
    def logged_press(key, *args, **kwargs): st["keys"].append(key); return env_press(key, *args, **kwargs)
    env.press = logged_press
    loop = Loop(env, Stub(), Memory(work / "memory"), "pokemon_red"); loop.restore(json.loads((work / "harness.json").read_text(encoding="utf-8")))
    st["loop"] = loop
    real_travel = loop.world.travel
    def travel(ctx, legs, step_no, trace=None):
        st["travel"] = {"legs": legs, "pos0": tuple(loop.world.pos)}; return real_travel(ctx, legs, step_no, trace)
    loop.world.travel = travel
    def capture(name):
        real_fn = getattr(loop, name)
        def wrapped(s, skill, *args, **kwargs): st["captured"].append(skill); return real_fn(s, skill, *args, **kwargs)
        setattr(loop, name, wrapped)
    capture("_settle"); capture("_hold_action")
    primitive, continuation, reason, checks, missed = "INCONCLUSIVE", "NOT RUN", None, None, None
    try:
        ram_before = truth_read(env)
        print(f"RAM before step {first}: (map,x,y)=({ram_before['map']},{ram_before['x']},{ram_before['y']}); actor offsets {sorted(_ram_actors(ram_before))}")
        try: loop.step()
        except CacheMiss as miss: missed = miss
        ram_after = truth_read(env)
        if missed and not st["captured"]: reason = f"{missed.role} cache miss at step {missed.step} before the route ran: the restored frame's perception is not cached under the current prompt"
        elif not st["captured"] or not st["travel"]: reason = "the recorded choice ran no travel route"
        else:
            checks = _primitive_checks(ram_before, ram_after, st["captured"][0], st["travel"]["legs"][0], st["travel"]["pos0"], st["keys"])
            if ram_after["map"] != ram_before["map"]: print(f"extra evidence (not asserted): map {ram_before['map']} -> {ram_after['map']}")
        if checks is None: print(f"primitive INCONCLUSIVE: {reason}")
        else:
            for name, ok, detail in checks: print(f"  {'PASS' if ok else 'FAIL'} {name}: {detail}")
            primitive = "PASS" if all(ok for _, ok, _ in checks) else "FAIL"
        if missed and checks is not None:  # the after-frame's perception missed: the primitive stands, the continuation stops here
            continuation = f"INCONCLUSIVE(step {missed.step})"; print(f"continuation stopped: {missed.role} cache miss on the after-frame of step {missed.step}")
        elif checks is not None:
            st["phase"] = "continuation"
            for _ in range(RELIABILITY_CONTINUATION):
                n = loop.step_no + 1
                try: loop.step()
                except CacheMiss as miss: continuation = f"INCONCLUSIVE(step {miss.step})"; print(f"continuation stopped: {miss.role} cache miss at step {miss.step}"); break
                except (Unexecuted, OmAbort) as error: continuation = f"INCONCLUSIVE(step {n})"; print(f"continuation stopped at step {n}: {error}"); break
            else: continuation = f"COMPLETED(steps {first + 1}-{first + RELIABILITY_CONTINUATION})"
    except (Unexecuted, OmAbort) as error:
        reason = str(error); print(f"primitive INCONCLUSIVE: {reason}")
    finally:
        loop_mod.act = real_act; env.close()
    return _rel_finish(primitive, continuation, counter, before)

def om_main(a, ap):
    global DECIDE, OM_CAP
    sys.stdout.reconfigure(encoding="utf-8")  # option labels carry arrows; a redirected cp1252 stdout cannot print them
    if a.arm == "decide": DECIDE, OM_CAP = True, 16
    if a.live and a.dry: ap.error("--live and --dry are exclusive")
    if a.om in ("talk", "plan") and not re.fullmatch(r"[A-Za-z0-9_-]+", a.run): ap.error("--run must be a run name")
    if a.om == "talk":
        if a.live: ap.error("--om talk is a zero-request replay")
    if a.om == "plan":
        if a.from_step is None: ap.error("--om plan requires --from-step")
        if a.tries is None or not 1 <= a.tries <= 8: ap.error("--om plan requires --tries from 1 to 8")
    if a.om == "places" and a.live: ap.error("--live applies to --om cases")
    if a.om == "reliability":
        if a.from_step is None: ap.error("--om reliability requires --from-step")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", a.run): ap.error("--run must be a run name")
    if a.om in ("places", "cases", "outcomes"): _old_perceiver()  # their recordings predate the place field
    try:
        if a.om == "talk": return om_talk(a.run)
        if a.om == "plan": return om_plan(a.run, a.from_step, a.tries, a.live)
        if a.om == "reliability": return om_reliability(a.run, a.from_step)
        return om_branch(a.run, a.from_step, a.pick) if a.om == "branch" else om_truth(a.run, no_names=a.strip_place, exact_exit=a.exact_exit) if a.om == "truth" else om_places() if a.om == "places" else om_outcomes(a.steps, a.fresh) if a.om == "outcomes" else om_cases(a.live, a.steps, a.fresh)
    except OmIncompatible as e: print(f"incompatible old replay / inconclusive: {e}", flush=True); return 2
    except OmAbort as e: print(f"ABORT: {e}", flush=True); return 2
    except run_agent.CallCap as e: print(f"CallCap: {e}", flush=True); return 3
    except ModelUnavailable as e: print(f"model_unavailable={e}", flush=True); return 4

if __name__=="__main__": raise SystemExit(main())
