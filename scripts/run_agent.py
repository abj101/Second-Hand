"""Checkpointed Pokémon Red runner for the E3 agent loop."""
import argparse, json, shutil, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_pyboy import PyBoyEnv, find_rom
from agent.loop import Loop
from agent.memory import Memory
from agent.model import ModelUnavailable, Ollama
from agent.observer import wait_settled
from scripts.truth_pokered import read
class CallCap(RuntimeError): pass
def _ledger(run, stage, cap):
    import msvcrt
    e4 = "e4" in str(run).lower(); path = Path("runs/e4_budget.json" if e4 else "runs/e3_budget.json"); path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            if e4:
                data = json.loads(path.read_text()) if path.exists() else {"total": 0}
                if data["total"] >= 600: raise CallCap("persistent call budget exhausted")
                data["total"] += 1
                tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); tmp.replace(path)
                return
            data = json.loads(path.read_text()) if path.exists() else {"total": 0, "smoke": 0, "m1": 0}
            bucket = "m1" if "m1" in str(run).lower() else "smoke"
            base = {"a":180,"b":300,"p":30,"q":60,"c":546}.get(stage)
            key, limit = (f"stage_{stage}", min(cap, base)) if stage and base else (None, None)
            if data["total"] >= 1500 or data[bucket] >= (1450 if bucket == "m1" else 50) or (key and data.get(key,0) >= limit): raise CallCap("persistent call budget exhausted")
            data["total"] += 1; data[bucket] += 1
            if key: data[key] = data.get(key,0)+1
            tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); tmp.replace(path)
        finally: lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
def _truncate(path, step):
    if path.exists(): path.write_text("".join(line for line in path.read_text().splitlines(True)
        if int(json.loads(line).get("step", 0)) <= step))
def _checkpoint(env, loop, run):
    root = run / "checkpoint" / f"{loop.step_no:06d}"; root.mkdir(parents=True, exist_ok=True)
    env.save(str(root / "pyboy.state")); shutil.copytree(loop.mem.root, root / "memory", dirs_exist_ok=True)
    (root / "harness.json").write_text(json.dumps(loop.state(), default=str))
    return root
def _boot_reset(run, rewind):
    for p in (run / "memory").glob("*"): p.unlink()
    base=(run / "checkpoint").resolve()
    if base.exists():
        for p in base.iterdir():
            if p.is_dir() and p.name.isdigit() and p.resolve().parent == base: shutil.rmtree(p)
    for name in ("trace.jsonl", "chooser.jsonl", "calls.jsonl", "truth.jsonl", "presses.jsonl"):
        if rewind: _truncate(run / name, 0)
        elif (run / name).exists(): (run / name).unlink()
def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--game", default="pokemon_red"); ap.add_argument("--steps", type=int, required=True)
    ap.add_argument("--max-calls", type=int, required=True); ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--resume", action="store_true"); ap.add_argument("--act-text", action="store_true"); ap.add_argument("--from-step", type=int); ap.add_argument("--stage", choices="abpqc")
    ap.add_argument("--stage-b-cap", type=int, choices=(180, 300), default=180)
    a = ap.parse_args(argv); run = a.run
    if a.steps < 1 or a.max_calls < 1: ap.error("steps and max-calls must be positive")
    if "m1" in str(run).lower() and a.steps > 600: ap.error("m1 runs are limited to 600 cumulative steps")
    if run.exists() and not a.resume and (run / "trace.jsonl").exists(): ap.error("run exists; use --resume")
    run.mkdir(parents=True, exist_ok=True); cp = None; boot = not a.resume
    if a.resume:
        paths = sorted((run / "checkpoint").glob("*/harness.json"))
        paths = [p for p in paths if a.from_step is None or int(p.parent.name) <= a.from_step]
        if not paths:
            if a.from_step != 0: ap.error("no matching checkpoint")
            boot = True; _boot_reset(run, True)
        else:
            cp = paths[-1].parent; state = json.loads((cp / "harness.json").read_text()); boot = False
            base=(run / "checkpoint").resolve(); [shutil.rmtree(p) for p in base.iterdir() if p.is_dir() and p.name.isdigit() and p.resolve().parent == base and int(p.name) > int(cp.name)]
            if "m1" in str(run).lower() and int(state.get("step",0))+a.steps > 600: ap.error("m1 runs are limited to 600 cumulative steps")
            for name in ("trace.jsonl", "chooser.jsonl", "calls.jsonl", "truth.jsonl", "presses.jsonl"): _truncate(run / name, int(cp.name))
            for file in (run / "memory").glob("*"): file.unlink()
            shutil.copytree(cp / "memory", run / "memory", dirs_exist_ok=True)
    elif boot: _boot_reset(run, False)
    env = PyBoyEnv(find_rom(), state=str(cp / "pyboy.state") if cp else None); env.tile_px = 16; invocation = [0]; active_step = [1]; logged_requests = [0]
    def log(row):
        n=invocation[0]-logged_requests[0]; logged_requests[0]=invocation[0]
        with (run / "calls.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps({**row, "step": active_step[0], "requests":n})+"\n")
    model = Ollama("gemma4:31b-cloud", cache_dir=Path(".cache/model"), log=log); post = model._post
    def guarded(payload):
        stage_cap={"a":180,"b":a.stage_b_cap,"p":30,"q":60,"c":546}.get(a.stage,a.max_calls)
        cap=min(a.max_calls,stage_cap)
        if invocation[0] >= cap: raise CallCap("invocation call cap")
        _ledger(run, a.stage, stage_cap); invocation[0] += 1; return post(payload)
    model._post = guarded; loop = Loop(env, model, Memory(run / "memory"), a.game, **({"act_text": True} if a.act_text else {}))
    if cp: loop.restore(state)
    press = env.press; failed = [False]; keys = []
    def guarded_press(*args, **kwargs):
        keys.append(args[0] if args else kwargs.get("key"))
        try: return press(*args, **kwargs)
        except BaseException: failed[0] = True; raise
    env.press = guarded_press
    try:
        if boot: wait_settled(env); _checkpoint(env, loop, run)
        for _ in range(a.steps):
            (run / "frames").mkdir(exist_ok=True); wait_settled(env); truth = {"step": loop.step_no+1, **read(env)}
            with (run / "truth.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps(truth)+"\n")
            active_step[0]=loop.step_no+1; old_calls, old_requests = model.calls, invocation[0]
            keys.clear()
            try: row = loop.step()
            except Exception as e:
                if failed[0]: loop.cached = loop.cached_hash = None; cp=_checkpoint(env, loop, run); print(f"input_error={e}; checkpoint={cp}",flush=True); return 0
                raise
            with (run / "presses.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps({"step": row["step"], "keys": keys})+"\n")
            choice = row.get("choice") or {}
            print(f"step={row['step']} place={row['place']} task={row['task']} choice={choice.get('verb')}:{choice.get('target')} verdict={row['verdict']} uncached={model.calls-old_calls} requests={invocation[0]-old_requests}", flush=True)
            if loop.step_no % 25 == 0: _checkpoint(env, loop, run)
            truth=read(env)
            if a.stage == "a" and str(truth.get("player_name","")).casefold()=="hand" and str(truth.get("rival_name","")).casefold()=="blue" and truth.get("map")==0x26 and not truth.get("text_box"): break
            if a.stage in ("b", "p", "q") and truth.get("map")==0x00 or a.stage == "c" and truth.get("party",0)>=1: break
        with (run / "truth.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps({"step":loop.step_no+1,**read(env)})+"\n")
        _checkpoint(env, loop, run); return 0
    except KeyboardInterrupt:
        if failed[0]: loop.cached = loop.cached_hash = None
        cp=_checkpoint(env, loop, run); print(f"interrupted; checkpoint={cp}",flush=True); return 0
    except CallCap as e: cp=_checkpoint(env, loop, run); print(f"call_cap={e}; checkpoint={cp}",flush=True); return 3
    except ModelUnavailable as e: cp=_checkpoint(env, loop, run); print(f"model_unavailable={e}; checkpoint={cp}",flush=True); return 4
    finally: env.close()

if __name__ == "__main__": raise SystemExit(main())
