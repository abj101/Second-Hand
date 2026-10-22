"""0-ask: how stable are entity ids over a recorded window, split by sprite kind (static vs character), plus mint quality.
Usage: eval_identity.py <run-basename> --from S --to S [--reload]   (default: one no-reload replay from the first checkpoint)"""
import argparse, sys, tempfile
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.memory import ACTOR_KINDS, Memory, _actor, _is_exit, normalize_kind

CENTER, NEAR = (8, 5), 12  # sprite centre offset in the 160x144 screen; px a perception may sit from "its" sprite

def cause(a, b):
    """Why the nearest-perception id of one sprite slot changed from a to b (dicts with id, actor, hit)."""
    if not a["actor"] and not b["actor"]: return "static object nearest"
    if a["actor"] != b["actor"]: return "nearest flips character/object"
    if b["hit"] and b["hit"] != b["id"]: return "track hit ignored by matcher"
    if b["hit"] and a["hit"] != b["hit"]: return "track re-keyed / new track"
    return "character, no track under it" if not b["hit"] else "other"

class Tally:
    def __init__(self): self.seq, self.where, self.near_mints, self.notile_mints = defaultdict(list), defaultdict(set), 0, 0
    def see(self, key, absxy, info):  # key (map, slot); absxy: the sprite's position in map pixels, constant for a static sprite
        self.where[key].add(tuple(absxy)); self.seq[key].append(info)
    def result(self):
        out = {k: {"switches": 0, "records": {}, "causes": Counter()} for k in ("static", "character")}
        for key, v in self.seq.items():
            kind = out["static" if len(self.where[key]) == 1 else "character"]
            kind["records"][key] = len({i["id"] for i in v})
            for a, b in zip(v, v[1:]):
                if a["id"] != b["id"]: kind["switches"] += 1; kind["causes"][cause(a, b)] += 1
        return out

def replay(run, first, last, callback, reload=False):
    from scripts.check_tracks import _jsonl, _jsonl_truth, _per, replay as reloading
    from agent.env_pyboy import PyBoyEnv, find_rom
    from agent.observer import wait_settled
    from agent.world import World
    from scripts.truth_pokered import read
    if reload: return reloading(run, last, first, callback=callback)[2]
    src = Path("runs") / run
    trace, presses = {r["step"]: r for r in _jsonl(src / "trace.jsonl")}, {r["step"]: r["keys"] for r in _jsonl(src / "presses.jsonl")}
    env = PyBoyEnv(find_rom(), state=str(src / "checkpoint" / f"{max(first - 1, 0):06d}" / "pyboy.state")); env.tile_px = 16
    world = World(Memory(Path(tempfile.mkdtemp())), 16)
    world.self_spot, world._self_from_motion = [72 / 160, 65 / 144], True
    world.tracker.wrap(env); off = []
    try:
        for s in range(first, last + 1):
            wait_settled(env); truth = read(env); row = trace.get(s)
            callback(s, row, _per(row or {}), truth, env, world)
            for key in presses.get(s, []): env.press(key, 16); wait_settled(env)
            nxt = next(iter(_jsonl_truth(src, s + 1)), None)
            if nxt and (nxt["map"], nxt["x"], nxt["y"]) != (read(env)["map"], read(env)["x"], read(env)["y"]): off.append(s + 1)
    finally: env.close()
    return off

def measure(run, first, last, reload=False):
    from scripts.check_tracks import _self_index
    from scripts.truth_pokered import sprites
    tally, prior = Tally(), [None]
    def on_step(s, row, per, truth, env, world):
        if not row or row.get("screen") != "world": return
        if not world.mem.get("places", row["place"]): world.mem.upsert("places", {"id": row["place"], "screen": "world", "state": "place", "first": s, "last": s, "visits": 1})
        pos = (truth["x"], truth["y"]); world.pos = pos; seen_at = world._seen_at; world._seen_at = lambda place: pos
        old = {r["id"]: r for r in world.mem.rows("entities")}
        try: world.before(per, row["place"], prior[0], (160, 144), s)
        finally: world._seen_at = seen_at
        world.pos = pos; prior[0] = row["place"]
        for r in world.mem.rows("entities"):
            if r["id"] in old: continue
            if not r.get("tile"): tally.notile_mints += 1; continue
            if _actor(r) or _is_exit(r.get("kind"), r.get("name")): continue
            if any(o.get("place") == r.get("place") and o.get("tile") and o.get("state") != "merged" and not _actor(o) and not _is_exit(o.get("kind"), o.get("name"))
                   and max(abs(o["tile"][0] - r["tile"][0]), abs(o["tile"][1] - r["tile"][1])) <= 2 for o in old.values()): tally.near_mints += 1
        centres = {x["slot"]: x for x in sprites(env) if x.get("slot")}
        if not centres: return
        hits, self_i, nearest = world._hits(per), _self_index(per, world), {}
        for i, e in enumerate(per["entities"]):
            ident = world.entity_ids.get(i)
            if i == self_i or not ident or not isinstance(e.get("at"), list) or len(e["at"]) != 2: continue
            xy = (e["at"][0] * 160, e["at"][1] * 144)
            d, slot = min((max(abs(xy[0] - x["x"] - CENTER[0]), abs(xy[1] - x["y"] - CENTER[1])), k) for k, x in centres.items())
            if d <= NEAR and d < nearest.get(slot, (NEAR + 1,))[0]:
                nearest[slot] = (d, {"step": s, "id": ident, "actor": normalize_kind(e.get("kind")) in ACTOR_KINDS, "hit": hits.get(i), "name": e.get("name")})
        for slot, (_, info) in nearest.items():
            x = centres[slot]; tally.see((truth["map"], slot), (x["x"] + 16 * truth["x"], x["y"] + 16 * truth["y"]), info)
    off = replay(run, first, last, on_step, reload)
    return tally, off

def report(tally, off):
    res = tally.result()
    for kind, r in res.items():
        print(f"{kind}: switches={r['switches']} max records per slot={max(r['records'].values(), default=0)} causes={dict(r['causes'])}")
    print(f"object mints within 2 tiles of an existing object row={tally.near_mints}")
    print(f"mints with no tile on a world screen={tally.notile_mints}")
    print(f"replay diverged from recorded truth at {len(off)} steps (first {off[:3]})")
    print("requests=0")

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("run"); p.add_argument("--from", dest="first", type=int, required=True)
    p.add_argument("--to", type=int, required=True); p.add_argument("--reload", action="store_true"); a = p.parse_args()
    report(*measure(a.run, a.first, a.to, a.reload))
