import argparse, json, re, shutil
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
from agent.observer import for_model, wait_settled
from agent.perceive import perceive
from agent.world import _is_exit
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
    ap=argparse.ArgumentParser(); ap.add_argument("--frames",default=",".join(map(str,FRAMES))); ap.add_argument("--max-calls",type=int); ap.add_argument("--out",type=Path,default=OUT)
    ap.add_argument("--om",choices=("places","cases"),help="OM replay eval (no --max-calls needed)"); ap.add_argument("--live",action="store_true",help="--om cases: spend the requests"); ap.add_argument("--dry",action="store_true",help="--om cases: no requests (the default)"); ap.add_argument("--steps",help="--om cases: comma-separated case steps to run (default all)")
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
MODEL_CACHE, BUDGET = Path(".cache/model"), Path("runs/e3_budget.json")
SCREEN_TYPES = ("world", "dialogue", "menu", "keyboard", "battle", "title", "other")
PERCEIVER_MISSES = 3  # more cache misses than this and the replay no longer matches the recording
FAKE_EXIT = "exit: doorway"  # m1_gp5's 2F doorway: walking into it changes nothing (RAM truth: map 38 stays put)
DECISION_GATE, OLD_GATE = 6, 3  # of 8 cases
MAX_OPTIONS, MAX_PROMPT_TOKENS = 10, 1700  # OM2 section 2 size gate: options excluding the free-buttons row; act prompt tokens = len/4
FLOOR_OF_MAP = {37: "1F", 38: "2F"}  # truth.map of the m1_gp5 house (evaluation only; agent/ never reads truth)
EXIT_ROWS = ("exit ", "possible exit ", "not an exit")  # option labels of exit records: verified, unverified, refuted

class OmAbort(RuntimeError): pass  # the replay cannot continue: exit 2

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
    def _post(self, payload):
        if self.live_step is None: raise OmAbort("a network call outside the live case step")
        if self.counter["requests"] >= OM_CAP: raise run_agent.CallCap("OM request cap")
        run_agent._ledger(Path("runs/m1_om"), None, OM_CAP)  # the M1 bucket of the persistent ledger
        self.counter["requests"] += 1
        return self._post_orig(payload)
    def ask(self, system, user, images=(), schema=None, *, role=""):
        if role in ("perceiver", "skill"):  # skill: menu/keyboard reads, cached like the perceiver's
            if (MODEL_CACHE / f"{self.real._key(system, user, images, schema)}.json").is_file(): return self.real.ask(system, user, images, schema, role=role)
            if role == "perceiver":
                self.misses += 1; self.miss_steps.append(self.step)
                if self.misses > PERCEIVER_MISSES: raise OmAbort(f"{self.misses} perceiver cache misses (limit {PERCEIVER_MISSES}), last at step {self.step}: the replay no longer matches the cache")
            else: self.skill_misses += 1  # the recording stored no reply here either (uncached = no data), so no data is the replay
            return Reply("", None, 0.0, None, None)  # not stored: .cache/model is never written by a miss
        if role == "plan":
            return Reply("", {"goals": self.rec.goal_ops.get(self.step, []), "focus": self.rec.focus.get(self.step) or "", "why": "", "lesson": None}, 0.0, None, None)
        if role == "act":
            if self.step in self.found:  # the act prompt at a case step; the raw text also goes to scratch files for analysis
                self.found[self.step]["prompt"] = system + "\n" + user
                d = Path(".tmp/om/prompts_om3_1"); d.mkdir(parents=True, exist_ok=True)
                (d / f"{self.step}_system.txt").write_text(system, encoding="utf-8"); (d / f"{self.step}_user.txt").write_text(user, encoding="utf-8")
            if self.live_step == self.step: return self.real.ask(system, user, images, schema, role=role)
            rec = self.rec.trace.get(self.step, {}); recorded = rec.get("act") or {}; pred = rec.get("pred") or {}
            return Reply("", {"place": recorded.get("place") or "", "notebook": self.rec.act_ops.get(self.step, []), "action": recorded.get("action"),
                              "text": (rec.get("choice") or {}).get("text"), "expect": pred.get("effect") if pred.get("basis") == "guess" and pred.get("effect") else "screen_changes",
                              "why": recorded.get("why") or ""}, 0.0, None, None)
        raise OmAbort(f"unexpected model role {role!r}")
    def _act(self, model, image, perception, notebook, facts, flags, controls, prefs, buttons, places=(), options=None):
        """Stands in for agent.loop.act. At a case step the options are recorded; a live case step also makes the real act
        call (network only for that call), and then the step continues on the recorded reply, so the loop executes the recorded action.
        act() writes nothing, so the extra call leaves memory as the recording has it."""
        args = (model, image, perception, notebook, facts, flags, controls, prefs, buttons)
        if self.step in self.cases:
            found = self.found[self.step] = {"options": options, "perception": perception, "act": None, "prompt": "", "exits": self._exits_now(perception)}
            if self.step in self.live_steps:
                self.live_step = self.step
                try: found["act"] = act(*args, places=places, options=options)
                finally: self.live_step = None
        return act(*args, places=places, options=options)
    def _exits_now(self, perception):
        """Exit records of the current place from the inputs World.options gives _exits (for the dry exit-truth gate)."""
        world, live = self.loop.world, {p["id"] for p in self.loop.mem.rows("places") if p.get("state") != "merged"}
        things = [(i, e, world.entity_ids.get(i)) for i, e in enumerate(perception.get("entities") or []) if e.get("name") and _is_exit(e)]
        return world._exits(self.loop.place, live, things)
    def run(self, last):
        if self.out.exists(): shutil.rmtree(self.out)
        env = PyBoyEnv(find_rom(), state=str(self.rec.copy / "checkpoint" / "000000" / "pyboy.state")); env.tile_px = 16  # the boot state run_agent saved
        press, keys = env.press, []
        def logged_press(*args, **kwargs):
            keys.append(args[0] if args else kwargs.get("key")); return press(*args, **kwargs)
        env.press = logged_press
        mem = Memory(self.out / "memory"); self.loop = loop = Loop(env, self, mem, "pokemon_red")
        real_act, loop_mod.act = loop_mod.act, self._act
        try:
            for s in range(1, last + 1):
                wait_settled(env); self.step = loop.step_no + 1; keys.clear()  # run_agent settles before reading truth; step() settles again
                row = loop.step()
                self._check(s, row, keys, mem)
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

def replay_cases(cases, live, counter):
    """One replay per run; returns (case, replay) pairs. live: each case step makes its one real act call, the replay goes on."""
    results = []
    for run in dict.fromkeys(c["run"] for c in cases):
        rec = stage(run)
        mine = [c for c in cases if c["run"] == run]
        steps = {c["step"] for c in mine}  # one replay per run
        rep = Replay(rec, rec.copy / "om_cases", cases=steps, live=steps if live else (), counter=counter); rep.run(max(steps))
        results += [(c, rep) for c in mine]
    return results

def dry_report(cases, counter):
    """The dry replay (no live step, so no request): prints each case's options, prompt size and gate rows.
    Returns (gate rows, right count of the recorded arm)."""
    rows, old_right = [], 0
    for c, rep in replay_cases(cases, False, counter):
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

def live_cases(cases, counter, before, old_right):
    """The live replay: one real act call per case step (at most OM_CAP requests), then the decision-point gates."""
    new_right = 0
    for c, rep in replay_cases(cases, True, counter):
        s, f = c["step"], rep.found.get(c["step"]) or {}; opts, res = f.get("options") or [], f.get("act")
        if res is None: print(f"\ncase {c['run']} step {s} new arm: no act reply"); continue
        if res.option is not None and 1 <= res.option <= len(opts): tool, label = opts[res.option - 1]["tool"], opts[res.option - 1]["label"]
        else: tool, label = action_option(res.action, entities=(f.get("perception") or {}).get("entities", []))
        new_ok = is_right(c, tool, label); new_right += new_ok
        print(f"\ncase {c['run']} step {s} new arm: option {res.option} {tool} {label!r} -> {'right' if new_ok else 'wrong'}  why: {res.why[:120]}")
    n = len(cases)
    print(f"\nDECISION POINTS: options arm right {new_right}/{n} (gate >= {DECISION_GATE})")
    print(f"OLD ARM right {old_right}/{n} (gate <= {OLD_GATE})")
    print(f"requests={counter['requests']} (cap {OM_CAP}); ledger before total={before.get('total')} m1={before.get('m1')} after total={_budget().get('total')} m1={_budget().get('m1')}")
    ok = new_right >= DECISION_GATE and old_right <= OLD_GATE and counter["requests"] <= OM_CAP
    print(f"CASES GATE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1

def om_cases(live, steps=None):
    """Dry first always (0 requests). --live runs the live replay only when every dry gate passes."""
    cases = json.loads(OM_CASES.read_text(encoding="utf-8")); cases = [c for c in cases if not steps or str(c["step"]) in steps.split(",")]; counter = {"requests": 0}; before = _budget()
    rows, old_right = dry_report(cases, counter)
    dry_ok = dry_summary(rows, len(cases))
    if not live:
        print(f"DRY: old arm right (recorded actions) {old_right}/{len(cases)}")
        print(f"requests={counter['requests']} (cap {OM_CAP}); ledger before total={before.get('total')} m1={before.get('m1')} after total={_budget().get('total')} m1={_budget().get('m1')}")
        print(f"CASES GATE: {'PASS' if dry_ok else 'FAIL'} (dry)")
        return 0 if dry_ok else 1
    if not dry_ok:
        print("DRY GATES FAILED: live measure not run")
        return 1
    return live_cases(cases, counter, before, old_right)

def om_main(a, ap):
    sys.stdout.reconfigure(encoding="utf-8")  # option labels carry arrows; a redirected cp1252 stdout cannot print them
    if a.live and a.dry: ap.error("--live and --dry are exclusive")
    if a.om == "places" and a.live: ap.error("--live applies to --om cases")
    try: return om_places() if a.om == "places" else om_cases(a.live, a.steps)
    except OmAbort as e: print(f"ABORT: {e}", flush=True); return 2
    except run_agent.CallCap as e: print(f"CallCap: {e}", flush=True); return 3
    except ModelUnavailable as e: print(f"model_unavailable={e}", flush=True); return 4

if __name__=="__main__": raise SystemExit(main())
