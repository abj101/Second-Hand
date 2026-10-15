"""Checkpointed Pokémon Red runner for the E3 agent loop."""
import argparse, hashlib, json, shutil, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_pyboy import PyBoyEnv, find_rom
from agent.loop import Loop
from agent.memory import Memory
from agent.model import ModelUnavailable, Ollama
from agent.observer import wait_settled
from scripts.truth_pokered import read
class CallCap(RuntimeError): pass
MODEL = "gemma4:31b-cloud"
MAX_HASHED_BYTES = 5 * 1024 * 1024
TOTAL_CAP, M1_CAP = 3665, 3615  # persistent e3 ledger caps; raised only with user approval
def _flag(name, value):
    dashed = name.replace("_", "-"); return f"--{dashed}" if value else f"--no-{dashed}"
def resolve_modes(saved: dict | None, decide: bool | None, act_text: bool | None, *, resume: bool = False) -> tuple[dict, list[str]]:
    """A resume restores the checkpoint's modes; an explicit flag must agree with them. Returns (modes, notes)."""
    modes, notes = {}, []
    for name, value in (("decide", decide), ("act_text", act_text)):
        known = saved.get(name) if saved else None
        if known is not None:
            if value is not None and value != known: raise ValueError(f"checkpoint was saved with {name}={known}; {_flag(name, value)} conflicts with it")
            modes[name] = known
        else:
            modes[name] = bool(value)
            if resume and value is None: notes.append(f"{name} not recorded in the checkpoint; resumed with {name}=False (unspecified)")
    return modes, notes
def source_fingerprint(root: Path = Path(".")) -> dict:
    """Git HEAD and a sha256 per modified or untracked file; notes name what could not be recorded."""
    git = lambda *args: subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout.decode("utf-8", "replace")
    try:
        top, head = Path(git("rev-parse", "--show-toplevel").strip()), git("rev-parse", "HEAD").strip()
        status = git("status", "--porcelain", "-z", "--untracked-files=all").split("\0")
    except (OSError, subprocess.CalledProcessError) as error:
        return {"head": None, "dirty": {}, "notes": [f"git unavailable: {error}"]}
    dirty, notes, entries = {}, [], iter(status)
    for entry in entries:
        if len(entry) < 4: continue
        rel = entry[3:]
        if entry[0] in "RC": next(entries, None)  # a rename or copy names its source in the next field
        path = top / rel
        if rel.startswith(("runs/", ".cache/", ".tmp/")) or "frames" in Path(rel).parts: dirty[rel] = "skipped"
        elif not path.is_file(): dirty[rel] = "deleted"
        elif path.stat().st_size > MAX_HASHED_BYTES: dirty[rel] = "skipped"; notes.append(f"{rel} is larger than 5 MB and was not hashed")
        else: dirty[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"head": head, "dirty": dirty, "notes": notes}
def manifest_record(*, resume, start_checkpoint, from_step, steps, max_calls, stage, stage_b_cap, model, modes, source, notes=()) -> dict:
    notes = [*notes, *source["notes"]]
    return {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "resume": resume, "start_checkpoint": start_checkpoint,
            "from_step": from_step, "steps": steps, "max_calls": max_calls, "stage": stage, "stage_b_cap": stage_b_cap,
            "model": model, "modes": modes, "source": {"head": source["head"], "dirty": source["dirty"]},
            "provenance": "incomplete" if notes else "complete", "notes": notes}
def append_manifest(run: Path, record: dict) -> dict:
    path = run / "manifest.jsonl"; count = len(path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0
    row = {"invocation": count + 1, **record}
    with path.open("a", encoding="utf8") as f: f.write(json.dumps(row) + "\n")
    return row
def _replace(tmp, path):  # Windows: a scanner or sandbox filter can hold the fresh temp file briefly (same as Notebook._save)
    for wait in (0, .05, .1, .2, .4, .8):
        time.sleep(wait)
        try: return tmp.replace(path)
        except PermissionError:
            if wait == .8: raise
def flag_manifest(run: Path, invocation: int, note: str) -> None:
    path = run / "manifest.jsonl"; rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["invocation"] == invocation: row["provenance"], row["notes"] = "incomplete", [*row["notes"], note]
    tmp = path.with_suffix(".tmp"); tmp.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"); tmp.replace(path)
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
                tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); _replace(tmp, path)
                return
            data = json.loads(path.read_text()) if path.exists() else {"total": 0, "smoke": 0, "m1": 0}
            bucket = "m1" if "m1" in str(run).lower() else "smoke"
            base = {"a":180,"b":300,"p":30,"q":60,"c":546}.get(stage)
            key, limit = (f"stage_{stage}", min(cap, base)) if stage and base else (None, None)
            if data["total"] >= TOTAL_CAP or data[bucket] >= (M1_CAP if bucket == "m1" else 50) or (key and data.get(key,0) >= limit): raise CallCap("persistent call budget exhausted")
            data["total"] += 1; data[bucket] += 1
            if key: data[key] = data.get(key,0)+1
            tmp = path.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); _replace(tmp, path)
        finally: lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
def _truncate(path, step):
    if path.exists(): path.write_text("".join(line for line in path.read_text().splitlines(True)
        if int(json.loads(line).get("step", 0)) <= step))
def _manifest_flagged(run: Path, invocation) -> bool:
    path = run / "manifest.jsonl"
    if invocation is None or not path.exists(): return False
    return any(row.get("invocation") == invocation and row.get("provenance") == "incomplete" for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()))
def _provenance_block(loop, run) -> dict:
    model, invocation = getattr(loop, "model", None), getattr(loop, "invocation", None)
    # log_failed: a call record went missing in this invocation, possibly after the manifest was last flagged
    incomplete = bool(getattr(loop, "provenance_incomplete", False) or _manifest_flagged(run, invocation) or getattr(model, "log_failed", False))
    return {"invocation": invocation, "manifest": "manifest.jsonl", "last_call": getattr(model, "seq", None), "incomplete": incomplete}
def _checkpoint(env, loop, run):
    root = run / "checkpoint" / f"{loop.step_no:06d}"; root.mkdir(parents=True, exist_ok=True)
    env.save(str(root / "pyboy.state")); shutil.copytree(loop.mem.root, root / "memory", dirs_exist_ok=True)
    (root / "harness.json").write_text(json.dumps({**loop.state(), "provenance": _provenance_block(loop, run)}, default=str))
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
    ap.add_argument("--resume", action="store_true"); ap.add_argument("--act-text", action=argparse.BooleanOptionalAction, default=None); ap.add_argument("--decide", action=argparse.BooleanOptionalAction, default=None); ap.add_argument("--from-step", type=int); ap.add_argument("--stage", choices="abpqc")
    ap.add_argument("--stage-b-cap", type=int, choices=(180, 300), default=180)
    a = ap.parse_args(argv); run = a.run
    if a.steps < 1 or a.max_calls < 1: ap.error("steps and max-calls must be positive")
    if "m1" in str(run).lower() and a.steps > 600: ap.error("m1 runs are limited to 600 cumulative steps")
    if run.exists() and not a.resume and (run / "trace.jsonl").exists(): ap.error("run exists; use --resume")
    run.mkdir(parents=True, exist_ok=True); cp = None; boot = not a.resume; modes, notes = resolve_modes(None, a.decide, a.act_text)
    if a.resume:
        paths = sorted((run / "checkpoint").glob("*/harness.json"))
        paths = [p for p in paths if a.from_step is None or int(p.parent.name) <= a.from_step]
        if not paths:
            if a.from_step != 0: ap.error("no matching checkpoint")
            boot = True; _boot_reset(run, True)
        else:
            cp = paths[-1].parent; state = json.loads((cp / "harness.json").read_text()); boot = False
            try: modes, notes = resolve_modes(state.get("modes"), a.decide, a.act_text, resume=True)
            except ValueError as error: ap.error(str(error))
            if "modes" not in state: notes = [*notes, "checkpoint records no modes (written before provenance); resumed as legacy"]
            base=(run / "checkpoint").resolve(); [shutil.rmtree(p) for p in base.iterdir() if p.is_dir() and p.name.isdigit() and p.resolve().parent == base and int(p.name) > int(cp.name)]
            if "m1" in str(run).lower() and int(state.get("step",0))+a.steps > 600: ap.error("m1 runs are limited to 600 cumulative steps")
            for name in ("trace.jsonl", "chooser.jsonl", "calls.jsonl", "truth.jsonl", "presses.jsonl"): _truncate(run / name, int(cp.name))
            for file in (run / "memory").glob("*"): file.unlink()
            shutil.copytree(cp / "memory", run / "memory", dirs_exist_ok=True)
    elif boot: _boot_reset(run, False)
    invocation_row = append_manifest(run, manifest_record(resume=a.resume, start_checkpoint=cp.name if cp else None, from_step=a.from_step,
        steps=a.steps, max_calls=a.max_calls, stage=a.stage, stage_b_cap=a.stage_b_cap, model=MODEL, modes=modes,
        source=source_fingerprint(Path(__file__).resolve().parents[1]), notes=notes))
    env = PyBoyEnv(find_rom(), state=str(cp / "pyboy.state") if cp else None); env.tile_px = 16; invocation = [0]; active_step = [1]; logged_requests = [0]
    def log(row):
        n=invocation[0]-logged_requests[0]; logged_requests[0]=invocation[0]
        with (run / "calls.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps({**row, "step": active_step[0], "requests":n})+"\n")
    model = Ollama(MODEL, cache_dir=Path(".cache/model"), log=log, image_dir=run / "images"); post = model._post
    def guarded(payload):
        stage_cap={"a":180,"b":a.stage_b_cap,"p":30,"q":60,"c":546}.get(a.stage,a.max_calls)
        cap=min(a.max_calls,stage_cap)
        if invocation[0] >= cap: raise CallCap("invocation call cap")
        _ledger(run, a.stage, stage_cap); invocation[0] += 1; return post(payload)
    model._post = guarded; loop = Loop(env, model, Memory(run / "memory"), a.game, **({"act_text": True} if modes["act_text"] else {}), **({"decide": True} if modes["decide"] else {}))
    loop.invocation = invocation_row["invocation"]
    if cp: loop.restore(state)
    press = env.press; failed = [False]; keys = []; clip = []  # clip: every emulated frame of this step's presses, for the viewer
    def guarded_press(key, frames=8, on_frame=None):
        keys.append(key)
        def both(image): clip.append(image); on_frame and on_frame(image)
        try: return press(key, frames, on_frame=both)
        except BaseException: failed[0] = True; raise
    env.press = guarded_press
    try:
        if boot: wait_settled(env); _checkpoint(env, loop, run)
        for _ in range(a.steps):
            (run / "frames").mkdir(exist_ok=True); wait_settled(env); truth = {"step": loop.step_no+1, **read(env)}
            with (run / "truth.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps(truth)+"\n")
            active_step[0]=loop.step_no+1; old_calls, old_requests = model.calls, invocation[0]
            keys.clear(); clip.clear()
            try: row = loop.step()
            except Exception as e:
                if failed[0]: loop.cached = loop.cached_hash = None; cp=_checkpoint(env, loop, run); print(f"input_error={e}; checkpoint={cp}",flush=True); return 0
                raise
            with (run / "presses.jsonl").open("a", encoding="utf8") as f: f.write(json.dumps({"step": row["step"], "keys": keys})+"\n")
            # ponytail: every 2nd frame at 33 ms = real time (GB is ~60 fps); browsers clamp GIF delays under 20 ms
            if len(clip) > 1: clip[0].save(run / "frames" / f"{row['step']:06d}_motion.gif", save_all=True, append_images=clip[2::2], duration=33, loop=0)
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
    finally:
        if getattr(model, "log_failed", False): flag_manifest(run, invocation_row["invocation"], "calls.jsonl write failed; some call records are missing")
        env.close()

if __name__ == "__main__": raise SystemExit(main())
