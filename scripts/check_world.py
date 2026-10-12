import shutil, sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.memory import Memory
from agent.world import World, _base
from scripts.eval_run import map_id, read, replay

def main():
    if sys.argv[1:] != ["merge"]: raise SystemExit("usage: check_world.py merge")
    run = Path("runs/m1")
    source = run / "checkpoint/000094/memory"
    root = Path(".tmp/w1").resolve()
    memory = root / "memory"
    root.mkdir(parents=True, exist_ok=True)
    if memory.exists(): memory.resolve().relative_to(root); shutil.rmtree(memory)
    shutil.copytree(source, memory)
    places = {r["id"]: r for r in read(memory / "places.jsonl") if r.get("id")}
    trace, truth = read(run / "trace.jsonl"), {r.get("step"): r for r in read(run / "truth.jsonl")}
    counts, samples = defaultdict(Counter), defaultdict(lambda: defaultdict(list))
    for row in trace:
        place, actual = row.get("next_place") or row.get("place"), truth.get((row.get("step") or 0) + 1, {})
        if place not in places or places[place].get("state") in {"merged", "candidate"} or actual.get("map") is None: continue
        mid = map_id(actual["map"])
        counts[place][mid] += 1
        tile = row.get("tile")
        try: samples[place][mid].append((int(actual["x"]) - int(tile[0]), int(actual["y"]) - int(tile[1]))) if isinstance(tile, list) and len(tile) == 2 else None
        except (KeyError, TypeError, ValueError): pass
    mapped = {p: min(c, key=lambda m: (-c[m], m)) for p, c in counts.items()}
    entities = replay(memory / "entities.jsonl")
    entities = [e for e in entities if e.get("place") in places and places[e["place"]].get("state") != "merged"]
    ids, pairs = sorted(mapped), []
    for i, src in enumerate(ids):
        for dst in ids[i + 1:]:
            if mapped[src] == mapped[dst]: pairs.append((src, dst, mapped[src]))
    eligible, correct, wrong = 0, 0, 0
    print("pair | eligible landmark pairs | computed offset | truth offset | verdict")
    for src, dst, mid in pairs:
        left = [e for e in entities if e.get("place") == src and isinstance(e.get("tile"), list) and len(e["tile"]) == 2]
        right = [e for e in entities if e.get("place") == dst and isinstance(e.get("tile"), list) and len(e["tile"]) == 2]
        bases = lambda rows: {_base(e.get("name", "")) for e in rows}
        n = len(bases(left) & bases(right))
        eligible += n >= 2
        a, b = samples[src][mid], samples[dst][mid]
        measured = bool(a and b)
        truth_offset = [round(median(p[i] for p in a)) - round(median(p[i] for p in b)) for i in (0, 1)] if measured else None
        if memory.exists(): memory.resolve().relative_to(root); shutil.rmtree(memory)
        shutil.copytree(source, memory)
        mem = Memory(memory)
        merged = World(mem, None).merge(src, dst, 95)
        computed = (mem.get("places", src) or {}).get("offset") if merged else None
        verdict = "rejected"
        if merged and measured and computed == truth_offset: correct += 1; verdict = "correct"
        elif merged: wrong += 1; verdict = "wrong/unmeasured"
        print(f"{src}->{dst} | {n} | {computed} | {truth_offset} | {verdict}")
    if not eligible: print("no eligible pair")
    if wrong or eligible and not correct: raise SystemExit(1)
if __name__ == "__main__":
    main()
