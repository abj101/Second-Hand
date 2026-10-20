"""Replay a recorded run with zero requests and compare identity to RAM truth."""
import argparse, json, shutil, statistics, sys, tempfile
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_pyboy import PyBoyEnv, find_rom
from agent.memory import Memory, normalize_kind
from agent.observer import wait_settled
from agent.world import ACTOR_KINDS, World
from scripts.truth_pokered import read, sprites
from agent.act import act as ask_act
from scripts.eval_plan import notebook_at
from types import SimpleNamespace

CENTER = (8, 5)

def _jsonl(path): return [json.loads(l) for l in Path(path).read_text(encoding="utf8").splitlines() if l.strip()]

def _per(row): return {"screen": row.get("screen"), "text": row.get("text", []), "entities": row.get("entities", [])}

def replay(run, steps=None, first=51, baseline=False, callback=lambda *args: None):
    """Replay checkpointed inputs; callback(step, row, perception, truth, env, world)."""
    src = Path("runs") / run
    trace = {r["step"]: r for r in _jsonl(src / "trace.jsonl")}
    presses = {r["step"]: r["keys"] for r in _jsonl(src / "presses.jsonl")}
    env = PyBoyEnv(find_rom(), state=str(src / "checkpoint" / f"{max(first - 1, 0):06d}" / "pyboy.state")); env.tile_px = 16
    world = World(Memory(Path(tempfile.mkdtemp())), 16); tracker = world.tracker
    world.self_spot, world._self_from_motion = [72 / 160, 65 / 144], True
    tracker.wrap(env)
    if baseline: tracker.near, tracker.gone, tracker.moved = (lambda at, used=(): None), set, set
    cps = {int(d.name) for d in (src / "checkpoint").iterdir()}; prior, off = None, []
    last = steps or max(trace)
    try:
        for s in range(first, last + 1):
            if s - 1 in cps:
                env.load(str(src / "checkpoint" / f"{s - 1:06d}" / "pyboy.state")); tracker.clear()
            wait_settled(env); truth = read(env); row = trace.get(s); per = _per(row or {})
            callback(s, row, per, truth, env, world)
            for key in presses.get(s, []): env.press(key, 16); wait_settled(env)
            nxt = next(iter(_jsonl_truth(src, s + 1)), None)
            if nxt and (nxt["map"], nxt["x"], nxt["y"]) != (read(env)["map"], read(env)["x"], read(env)["y"]): off.append(s + 1)
            if row and row.get("screen") == "world": prior = row.get("place")
    finally: env.close()
    return world, trace, off, first, last

_TRUTH = {}
def _jsonl_truth(src, step):
    if src not in _TRUTH: _TRUTH[src] = {r["step"]: r for r in _jsonl(src / "truth.jsonl")}
    return [_TRUTH[src][step]] if step in _TRUTH[src] else []

def _self_index(per, world):
    spot = world.self_spot
    points = [(i, e.get("at")) for i, e in enumerate(per.get("entities", [])) if isinstance(e.get("at"), list) and len(e["at"]) == 2]
    return min(points, key=lambda p: max(abs(p[1][j] - spot[j]) for j in (0, 1)))[0] if points and spot else None

def _default(run, steps, baseline, first):
    seq, owner, errs, static_tiles, floor, map0_places = defaultdict(list), {}, [], defaultdict(list), {}, set()
    def on_step(s, row, per, truth, env, world):
        if not row or row.get("screen") != "world": return
        if not world.mem.get("places", row["place"]): world.mem.upsert("places", {"id": row["place"], "screen": "world", "state": "place", "first": s, "last": s, "visits": 1})  # match_place mints it in the live loop
        pos = (truth["x"], truth["y"]); world.pos = pos; seen_at = world._seen_at; world._seen_at = lambda place: pos
        try: world.before(per, row["place"], on_step.prior, (160, 144), s)
        finally: world._seen_at = seen_at
        world.pos = pos; on_step.prior = row["place"]
        mid, ents = truth["map"], per["entities"]
        if mid == 0: map0_places.add(row["place"])
        self_i = _self_index(per, world)
        centres = {x["slot"]: (x["x"] + CENTER[0], x["y"] + CENTER[1]) for x in sprites(env) if x.get("slot")}
        static_count, nearest = defaultdict(int), {}  # nearest: slot -> (px distance, id) of the one perception closest to that sprite this frame
        for i, e in enumerate(ents):
            if i == self_i or not isinstance(e.get("at"), list) or len(e["at"]) != 2: continue
            xy, ident = (e["at"][0] * 160, e["at"][1] * 144), world.entity_ids.get(i)
            if centres:
                d, slot = min((max(abs(xy[0] - x), abs(xy[1] - y)), k) for k, (x, y) in centres.items())
                if d <= 12 and ident and d < nearest.get(slot, (13, None))[0]: nearest[slot] = (d, ident)  # a chair beside the sprite is not the sprite
            tile = world.entity_tiles.get(ident) if ident else None
            if tile:
                record = world.mem.get("entities", ident) or {}
                if normalize_kind(record.get("kind")) not in ACTOR_KINDS:
                    static_tiles[ident].append(tuple(tile)); static_count[tuple(tile)] += 1
        for slot, (_, ident) in nearest.items(): seq[(mid, slot)].append(ident); owner[ident] = (slot, mid)
        if mid == 0:
            for tile, n in static_count.items(): floor[tile] = max(floor.get(tile, 0), n)
        for ident, track in world.tracker.tracks.items():
            if track["status"] == "live" and track["actor"] and track["own"] != (0, 0) and ident in owner and owner[ident][0] in centres:
                slot, _ = owner[ident]; x, y = centres[slot]
                errs.append(max(abs(track["x"] - x), abs(track["y"] - y)))
    on_step.prior = None
    world, trace, off, first, last = replay(run, steps, first, baseline, on_step)
    mem = world.mem
    recs = {key: len(set(ids)) for key, ids in seq.items()}
    switches = {key: sum(a != b for a, b in zip(ids, ids[1:])) for key, ids in seq.items()}
    ghosts = [r["id"] for r in mem.rows("entities") if r.get("place") == world.current_place and r.get("kind") in ACTOR_KINDS and r.get("status", "remembered") != "gone" and r["id"] not in world.entity_ids.values()]
    records0 = [r for r in mem.rows("entities") if r.get("place") in map0_places and r.get("tile")]
    mints = _jsonl(mem.root / "mints.jsonl") if (mem.root / "mints.jsonl").exists() else []
    bad_mints = [m for m in mints if any(n.get("tile_d") == 0 and not n.get("used") for n in m.get("near", []))]
    jitter = [max((max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a in tiles for b in tiles), default=0) for tiles in static_tiles.values()]
    jitter.sort(); near2 = sum(min((n["tile_d"] for n in m.get("near", []) if n.get("tile_d") is not None), default=-1) == 2 for m in mints)
    print(f"run={run} steps={first}..{last} baseline={baseline} requests=0 replay diverged from recorded truth at {len(off)} steps (first {off[:3]})")
    print(f"records per (map, slot)={recs}")
    print(f"id switches per (map, slot)={switches} (total {sum(switches.values())})")
    print(f"map 0 records with tile={len(records0)} floor={sum(floor.values())}")
    print(f"S3 same-class unused same-tile mints={len(bad_mints)}")
    print(f"static projection jitter tiles: records={len(jitter)} p50={jitter[int(.50 * (len(jitter) - 1))] if jitter else 0} p95={jitter[int(.95 * (len(jitter) - 1))] if jitter else 0} max={max(jitter, default=0)}; nearest same-class distance 2 mints={near2}")
    print(f"ghosts in {world.current_place}: {len(ghosts)} {ghosts}")
    if errs: print(f"tracker error px over {len(errs)} frames: mean {statistics.mean(errs):.2f} p95 {sorted(errs)[int(.95 * len(errs))]:.1f} max {max(errs):.1f}")
    return {"switches": switches, "records": recs, "ghosts": len(ghosts), "errs": errs, "floor": sum(floor.values()), "mints": len(bad_mints), "jitter": jitter}

def _places(run, steps, first):
    place_ids, prior = defaultdict(set), {"recorded": None, "replay": None, "row": None}
    def on_step(s, row, per, truth, env, world):
        if row:
            pid = world.mem.match_place(per, s, prev=prior["replay"], cut=row.get("place") != prior["recorded"] or bool((prior["row"] or {}).get("scene_cut")), frame=env.screenshot(), tile_px=16)
            w, h = truth.get("map_w"), truth.get("map_h")
            if row.get("screen") == "world" and isinstance(truth.get("map"), int) and isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                place_ids[truth["map"]].add(pid)
            prior.update(recorded=row.get("place"), replay=pid, row=row)
    world, _, _, _, _ = replay(run, steps, first, callback=on_step)
    print(f"place ids per RAM map={ {m: sorted(ids) for m, ids in sorted(place_ids.items())} }")
    return {m: sorted(ids) for m, ids in place_ids.items()}

def _exits(run):
    src = Path("runs") / run; live_dir = src / "memory"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "memory"; root.mkdir()
        if live_dir.exists(): shutil.copytree(live_dir, root, dirs_exist_ok=True)
        (root / "crossings.jsonl").unlink(missing_ok=True)
        mem = Memory(root); world = World(mem, 16)
        for row in _jsonl(src / "trace.jsonl"):
            exit_at, place, dest = row.get("exit"), row.get("place"), row.get("next_place")
            if exit_at and place and dest and dest != place:
                world._count(place, tuple(exit_at[:2]), dest, row.get("step", 0))
        live = {p["id"] for p in mem.rows("places") if p.get("state") != "merged" and p.get("screen") == "world"}
        exits = world._exits("p_place_3", live)
        crossings = {r["id"] for r in mem.rows("crossings")}
        phantom = sum(x.get("status") == "verified" and not any(any(cid.startswith(f"p_place_3@{d['tile'][0]},{d['tile'][1]}>") for cid in crossings) for d in x.get("doors", [])) for x in exits)
        print(json.dumps({"p_place_3 exits (tile, status, label)": [(x.get("tile"), x.get("status"), x.get("label")) for x in exits]}))
        print(f"phantom verified exits without departure={phantom}")
        return exits

def capped(model, cap, charge=None):
    """R3.7: every network request of model (retries and follow-ups too) is charged to the M1 ledger and refused beyond cap; cache hits never post."""
    from scripts import run_agent
    charge = charge or (lambda: run_agent._ledger(Path("runs/m1_rcr_r3_live"), None, cap))
    post, model.requests = model._post, 0
    def guarded(payload):
        if model.requests >= cap: raise run_agent.CallCap(f"R3.7 request cap {cap}")
        charge(); model.requests += 1
        return post(payload)
    model._post = guarded
    return model

def menu(run, steps=None, first=1, dump=None, live=(), cap=20, model=None):
    """Offline (0 requests): per recorded step, the act prompt and its option rows; checks duplicates, name lists, unacted rows, failed-before-new order."""
    src = Path("runs") / run; trace = {r["step"]: r for r in _jsonl(src / "trace.jsonl")}
    if dump: Path(dump).mkdir(parents=True, exist_ok=True)
    last, per_step, fails, st, picks = steps or max(trace), [], {}, {"prior": None}, []
    if live and model is None:
        from agent.model import Ollama; from scripts.run_agent import MODEL
        model = capped(Ollama(MODEL, cache_dir=Path(".cache/model")), cap)
    def on_step(s, row, per, truth, env, world):
        if not row: return
        place = row.get("place")
        if place and not world.mem.get("places", place): world.mem.upsert("places", {"id": place, "screen": row.get("screen"), "state": "place", "first": s, "last": s, "visits": 1})  # match_place mints it in the live loop
        if place and row.get("screen") == "world" and world.mem.get("places", place).get("screen") != "world":  # a recorded id first seen on a title screen is a map once a map frame shows it
            world.mem.upsert("places", {**world.mem.get("places", place), "screen": "world"})
        pos = (truth["x"], truth["y"]); world.pos = pos; seen_at = world._seen_at; world._seen_at = lambda place: pos
        try: world.before(per, place, st["prior"], (160, 144), s)
        finally: world._seen_at = seen_at
        world.pos = pos; st["prior"] = place
        nb = notebook_at(src, s)
        focus = f"{nb.render_focus(world.label)}\nHere: {world.place_label(place)}"
        target = next((q.get("target") for q in nb.data["quests"] if q.get("status") == "open" and q.get("target")), None)
        try: options = world.options(per, place, fails, s, inputs=list(env.inputs()), targets=(target,) if target else ())
        except TypeError as e:  # a crash in agent/world.py options is reported as a failure, never patched here
            per_step.append({"step": s, "rows": 0, "dups": [], "names": [], "noaction": 0, "chars": 0, "order": 0, "crash": str(e)[:120]})
            print(f"{s} CRASH options: {str(e)[:120]}"); return
        sent = []
        class Capture:
            def ask(self, system, user, images=(), schema=None, *, role=""):
                sent.append(system + user); return SimpleNamespace(data={"why": "x", "i": 1})
        ask_act(Capture(), per, focus, options, list(env.inputs()), "")  # controls only reach the buttons follow-up, not this prompt
        labels = [o["label"] for o in options]
        dups = [l for l in labels if labels.count(l) > 1]
        names = [l for l in labels if any(sep in l.partition(",")[0] for sep in (" or ", " and ", "/"))]
        order_bad, failed_seen = False, False
        if row.get("screen") == "world":
            for o in options:  # ponytail: rule as stated for R4 (free buttons excluded)
                if o["tool"] == "buttons": continue
                failed_seen |= o["state"] == "failed"; order_bad |= o["state"] == "new" and failed_seen
        stat = {"step": s, "rows": len(options), "dups": dups, "names": names, "noaction": sum(o["tool"] != "buttons" and o["action"] is None for o in options),
                "chars": len(sent[0]) if sent else 0, "order": int(order_bad)}
        per_step.append(stat)
        print(f"{s} {stat['rows']} {len(dups)} {len(names)} {stat['chars']} {stat['order']}")
        if dump:
            (Path(dump) / f"{s}.txt").write_text(sent[0] if sent else "", encoding="utf8")
            (Path(dump) / f"{s}.json").write_text(json.dumps([{k: o.get(k) for k in ("n", "label", "state", "tool", "intent_key")} for o in options]), encoding="utf8")
        if s in live:  # R3.7: the same prompt, once through the real (cached, capped) model
            new = any(o["state"] == "new" and o["tool"] != "buttons" for o in options)
            try:
                res = ask_act(model, per, focus, options, list(env.inputs()), "")
                o = options[res.option - 1] if res.option else {}
                picks.append({"step": s, "n": res.option, "label": o.get("label"), "state": o.get("state"), "new": new, "why": res.why})
            except Exception as e: picks.append({"step": s, "error": f"{type(e).__name__}: {str(e)[:100]}", "new": new})
            p = picks[-1]; print(f"live {s} pick {p.get('n')} [{p.get('state')}] {p.get('label') or p.get('error')} | {str(p.get('why', ''))[:100]}")
        intent, result = (row.get("choice") or {}).get("intent"), row.get("verdict")
        if intent and result in ("failed", "no_change"):  # ponytail: fails from trace verdicts only; Loop.fails also holds planner history
            f = fails.setdefault(intent, {"n": 0}); f.update(n=f["n"] + 1, why=f"verdict {result}", step=s)
        elif intent: fails.pop(intent, None)
    _, _, _, first, last = replay(run, steps, first, callback=on_step)
    in_range = [s for s in trace if first <= s <= last]
    ok = [p for p in per_step if "crash" not in p]; rows = [p["rows"] for p in ok]; chars = max((p["chars"] for p in ok), default=0)
    dup, name, noact = (sum(len(p[k]) if isinstance(p[k], list) else p[k] for p in ok) for k in ("dups", "names", "noaction"))
    bad_order = [p["step"] for p in ok if p["order"]]; crashes = [p["step"] for p in per_step if "crash" in p]
    for p in ok:
        for l in p["dups"] + p["names"]: print(f"  offender step {p['step']}: {l}")
    print(f"summary steps {len(per_step)}/{len(in_range)} rows max={max(rows, default=0)} mean={statistics.mean(rows) if rows else 0:.1f} max_chars={chars} dup={dup} namelist={name} noaction={noact} crashes={len(crashes)} {crashes[:10]} order_steps={len(bad_order)} {bad_order[:10]}")
    code = int(chars > 2000 or bool(dup or name or noact or crashes or bad_order))
    if live:
        s4 = [p["step"] for p in picks if p.get("state") == "failed" and p["new"]]
        print(f"live steps {len(picks)} requests {getattr(model, 'requests', '?')} (cap {cap}) failed-while-new picks {len(s4)} {s4} errors {sum('error' in p for p in picks)}")
    print(f"exit {code}")
    return {"steps": per_step, "exit": code, "picks": picks}

def check(run, steps=None, baseline=False, first=51, places=False, exits=False):
    if exits: return _exits(run)
    if places: return _places(run, steps, first)
    return _default(run, steps, baseline, first)

if __name__ == "__main__":
    a = argparse.ArgumentParser(); a.add_argument("run"); a.add_argument("--steps", type=int); a.add_argument("--first", type=int, default=51, help="a checkpoint step + 1"); a.add_argument("--baseline", action="store_true"); a.add_argument("--places", action="store_true"); a.add_argument("--exits", action="store_true"); a.add_argument("--menu", action="store_true", help="offline act-prompt check, 0 requests"); a.add_argument("--dump", help="write each step's act prompt to DIR/<step>.txt"); a.add_argument("--live", action="store_true", help="R3.7: send the --only steps' prompts to the real model"); a.add_argument("--only", default="", help="comma-separated steps for --live"); a.add_argument("--cap", type=int, default=20, help="request cap for --live, retries included")
    a = a.parse_args()
    if a.menu: raise SystemExit(menu(a.run, a.steps, a.first, a.dump, {int(x) for x in a.only.split(",") if x} if a.live else (), a.cap)["exit"])
    check(a.run, a.steps, a.baseline, a.first, a.places, a.exits)
