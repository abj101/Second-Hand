"""Object-track check against RAM sprite truth, 0 requests and no model: replays a recorded run's key presses on the emulator from its boot state and
feeds each step's recorded perception (trace.jsonl entities) to a fresh World, so the tracker, association and lifecycle run on real frames.
RAM truth (sprite slots, player tile) is evaluation only; agent/ never reads it.   python scripts/check_tracks.py RUN [--steps N] [--baseline]
--baseline turns association, lifecycle and drift exclusion off (pre-OT behaviour) while still measuring where the tracker would put things."""
import argparse, json, statistics, sys, tempfile
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_pyboy import PyBoyEnv, find_rom
from agent.memory import Memory, normalize_kind
from agent.observer import wait_settled
from agent.track import Tracker
from agent.world import ACTOR_KINDS, World
from scripts.truth_pokered import read, sprites

CENTER = (8, 5)  # RAM sprite (x, y) -> visible centre: the live agent's motion-locked self spot was (72, 65) for the player's RAM (64, 60) on every step of m1_ot_live

def _jsonl(path): return [json.loads(l) for l in Path(path).read_text(encoding="utf8").splitlines() if l.strip()]

def check(run, steps=None, baseline=False, first=51):
    src = Path("runs") / run; trace = {r["step"]: r for r in _jsonl(src / "trace.jsonl")}; presses = {r["step"]: r["keys"] for r in _jsonl(src / "presses.jsonl")}
    env = PyBoyEnv(find_rom(), state=str(src / "checkpoint" / f"{max(first - 1, 0):06d}" / "pyboy.state")); env.tile_px = 16
    world = World(Memory(Path(tempfile.mkdtemp())), 16); tracker = world.tracker
    world.self_spot, world._self_from_motion = [72 / 160, 65 / 144], True  # the spot the live loop locks from motion (m1_ot_live); the replay has no skill moves to lock it
    owner, seq, errs, off = {}, defaultdict(list), [], []  # owner: entity id -> real sprite slot; seq: slot -> ids in perception order
    centres = lambda: {s["slot"]: (s["x"] + CENTER[0], s["y"] + CENTER[1]) for s in sprites(env) if s["slot"]}
    real_update = tracker.update
    def update(frame):
        real_update(frame); c = centres()
        for ident, t in tracker.tracks.items():
            # only tracks that locked onto motion: a still one keeps the model's own point, about a tile off
            if t["status"] == "live" and t["actor"] and t["own"] != (0, 0) and ident in owner and owner[ident] in c:
                errs.append(max(abs(t["x"] - c[owner[ident]][0]), abs(t["y"] - c[owner[ident]][1])))
    tracker.update = update; tracker.wrap(env)
    if baseline: tracker.near, tracker.gone, tracker.moved = (lambda at, used=(): None), set, set
    cps = {int(d.name) for d in (src / "checkpoint").iterdir()}; prior, last = None, steps or max(trace)
    for s in range(first, last + 1):
        if s - 1 in cps and (s == first or (s - 1) % 25 == 0): env.load(str(src / "checkpoint" / f"{s - 1:06d}" / "pyboy.state")); tracker.clear()  # key presses alone do not reproduce a run exactly; each checkpoint resets the emulator
        wait_settled(env); t = read(env); row = trace.get(s)
        if row and row["screen"] == "world":
            per = {"screen": "world", "text": [], "entities": [{k: e[k] for k in ("kind", "name", "at")} for e in row["entities"]]}
            world.pos = (t["x"], t["y"]); world.before(per, row["place"], prior, (160, 144), s); prior = row["place"]; c = centres()
            for i, e in enumerate(per["entities"]):
                if i in world.entity_ids and normalize_kind(e["kind"]) in ACTOR_KINDS and c:  # only what the model calls a character can be a sprite; a chair beside one is not
                    d, slot = min((max(abs(e["at"][0] * 160 - x), abs(e["at"][1] * 144 - y)), k) for k, (x, y) in c.items())
                    if d <= 12: owner[world.entity_ids[i]] = slot; seq[slot].append(world.entity_ids[i])
        for key in presses.get(s, []): env.press(key, 16); wait_settled(env)
        nxt = next((r for r in _jsonl_truth(src, s + 1)), None)
        if nxt and (nxt["map"], nxt["x"], nxt["y"]) != (read(env)["map"],) + (read(env)["x"], read(env)["y"]): off.append(s + 1)
    env.close()
    mem = world.mem
    switches = {s: sum(a != b for a, b in zip(ids, ids[1:])) for s, ids in seq.items()}; recs = {s: len(set(ids)) for s, ids in seq.items()}
    ghosts = [r["id"] for r in mem.rows("entities") if r.get("place") == world.current_place and r.get("kind") in ACTOR_KINDS
              and r.get("status", "remembered") != "gone" and r["id"] not in world.entity_ids.values()]
    print(f"run={run} steps={first}..{last} baseline={baseline} requests=0 replay diverged from recorded truth at {len(off)} steps (first {off[:3]})")
    print(f"real sprites={len(seq)} perceptions per sprite={ {s: len(v) for s, v in seq.items()} }")
    print(f"id switches per sprite={switches} (total {sum(switches.values())})")
    print(f"records per sprite={recs} (mean {statistics.mean(recs.values()) if recs else 0:.2f}, target 1.0)")
    print(f"ghosts in {world.current_place}: {len(ghosts)} {ghosts}")
    if errs: print(f"tracker error px over {len(errs)} frames: mean {statistics.mean(errs):.2f} p95 {sorted(errs)[int(.95 * len(errs))]:.1f} max {max(errs):.1f}")
    return {"switches": sum(switches.values()), "records": recs, "ghosts": len(ghosts), "errs": errs}

_TRUTH = {}
def _jsonl_truth(src, step):
    if src not in _TRUTH: _TRUTH[src] = {r["step"]: r for r in _jsonl(src / "truth.jsonl")}
    return [_TRUTH[src][step]] if step in _TRUTH[src] else []

if __name__ == "__main__":
    a = argparse.ArgumentParser(); a.add_argument("run"); a.add_argument("--steps", type=int); a.add_argument("--first", type=int, default=51, help="a checkpoint step + 1"); a.add_argument("--baseline", action="store_true")
    a = a.parse_args(); check(a.run, a.steps, a.baseline, a.first)
