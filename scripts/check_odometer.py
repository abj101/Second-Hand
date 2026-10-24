"""Offline per-frame Odometer check against RAM from saved checkpoints."""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_pyboy import PyBoyEnv, find_rom
from agent.observer import _blank, wait_settled
from agent.track import Odometer

MAIN = Path("C:/Users/Abj1/Documents/GitHub/Avatar")
RAM = {"map": 0xD35E, "y": 0xD361, "x": 0xD362}

def read_ram(env): return {key: env.ram(addr) for key, addr in RAM.items()}

def _tiles(moves, tile_px):
    px = [sum(m[i] for m in moves) for i in (1, 2)]
    return px, [round(-v / tile_px) for v in px]

def _ram_path(records, start, end, map_id):
    path, prev = [], records[start]
    for seq in range(start + 1, end + 1):
        cur = records[seq]
        if cur["map"] == map_id and (cur["x"], cur["y"]) != (prev["x"], prev["y"]): path.append({"seq": seq, "delta": [cur["x"] - prev["x"], cur["y"] - prev["y"]], "pos": [cur["x"], cur["y"]]})
        prev = cur
    return path

def _same_map_end(records, start, end, map_id):
    return next((seq - 1 for seq in range(start + 1, end + 1) if records[seq]["map"] != map_id), end)

def _compare_segment(segment, start, previous, records, tile_px, flip=None, arrival=None):
    lo, hi = segment["start"], segment["end"]
    if lo is None or hi is None: return None
    moves = segment["moves"]; px, odo = _tiles(moves, tile_px)
    if flip and lo <= flip["seq"] <= hi:
        base = records[max(start, lo - 1)]; end, endpoint, role = flip["seq"], flip["xy"], "map-flip-source"
        last_same = records[flip["last_same_seq"]]
        delta = [endpoint[i] - (base["x"], base["y"])[i] for i in (0, 1)]
        flip_step = [endpoint[i] - (last_same["x"], last_same["y"])[i] for i in (0, 1)]
        adjacent = sum(map(abs, flip_step)) in (0, 1)
        ram = delta; truth_end = endpoint; valid = adjacent
    elif arrival:
        base = {"x": arrival["xy"][0], "y": arrival["xy"][1], "map": arrival["map"]}; end = _same_map_end(records, arrival["seq"], hi, base["map"])
        endpoint, role = [base["x"], base["y"]], "arrival-write" if arrival["write_seq"] is not None else "flip-fallback"
        if end < arrival["seq"]: return None
        ram_end = records[end]; ram = [ram_end[a] - base[a] for a in ("x", "y")]; truth_end = [ram_end["x"], ram_end["y"]]; valid = True
        flip_step = None
    else:
        base = records[previous]; end = _same_map_end(records, max(previous, lo - 1), hi, base["map"])
        if end < max(previous, lo - 1) + 1: return None
        endpoint, role = [records[end]["x"], records[end]["y"]], "within-map"
        ram_end = records[end]; ram = [ram_end[a] - base[a] for a in ("x", "y")]; truth_end = [ram_end["x"], ram_end["y"]]; valid = True; flip_step = None
    agree = valid and odo == ram
    check = {"endpoint": role, "map": base["map"], "map_end": base["map"], "endpoint_frame_map": records[end]["map"], "frames": [lo, end], "ram_start": [base["x"], base["y"]],
             "ram_end": list(truth_end), "odo_px": px, "odo_tiles": odo, "ram_tiles": ram, "flip_step": flip_step, "flip_adjacent": valid if flip_step is not None else None, "agree": agree}
    if not agree:
        check.update({"residual": [ram[i] - odo[i] for i in (0, 1)], "ram_path": _ram_path(records, max(previous, lo - 1), end, base["map"]), "moves": [[m[0], m[1], m[2]] for m in moves]})
    return check

def _map_flip(records, start, end):
    source = records[start]["map"]
    seq = next((q for q in range(start + 1, end + 1) if records[q]["map"] != source), None)
    if seq is None: return None, None
    prior = next(q for q in range(seq - 1, start - 1, -1) if records[q]["map"] == source)
    xy = [records[seq]["x"], records[seq]["y"]]
    blank = next((q for q in range(seq + 1, end + 1) if records[q]["blank"]), None)
    nonblank = next((q for q in range((blank or seq) + 1, end + 1) if not records[q]["blank"]), end)
    write = next((q for q in range(seq + 1, nonblank + 1) if [records[q]["x"], records[q]["y"]] != xy), None)
    arrival = {"seq": write if write is not None else seq, "xy": [records[write if write is not None else seq]["x"], records[write if write is not None else seq]["y"]],
               "map": records[write if write is not None else seq]["map"], "write_seq": write, "nonblank_seq": nonblank}
    return {"seq": seq, "xy": xy, "source_map": source, "dest_map": records[seq]["map"], "last_same_seq": prior,
            "last_same_xy": [records[prior]["x"], records[prior]["y"]], "arrival": arrival}, arrival

def _world_type(screen, place, world_places):
    return screen == "world" or screen in ("dialogue", "menu") and world_places.get(place) == "world"

def replay_window(run, checkpoint, stop, presses, trace, world_places, tile_px):
    state = run / "checkpoint" / f"{checkpoint:06d}" / "pyboy.state"
    env = PyBoyEnv(find_rom(), state=str(state)); odo = Odometer(tile_px); records = {}
    def capture(image, seq): records[seq] = {"seq": seq, "blank": _blank(image), **read_ram(env)}
    env.listen(capture); env.listen(odo); odo(image := env.screenshot(), 0); capture(image, 0)
    rows = []
    try:
        wait_settled(env)  # run_agent top-of-loop settle before truth
        for step in range(checkpoint + 1, stop + 1):
            start = odo.seq
            wait_settled(env)  # Loop.step before/perception settle
            # Ponytail: replay recorded keys at the runner's standard 16-frame press length; input durations are not logged.
            for key in presses.get(step, []):
                wait_settled(env)  # skill press start settle
                env.press(key, 16)
                wait_settled(env)  # skill press end settle
            wait_settled(env)  # Loop.step after-settle
            if step < stop: wait_settled(env)  # next run_agent top-of-loop settle; keeps the next boundary exact
            end = odo.seq; drained = odo.take(through=end)
            segments = []; previous = start; cut_seen = False; eligible = []; flip, arrival = _map_flip(records, start, end)
            for si, seg in enumerate(drained):
                after_cut = cut_seen; perception_step = step + 1 if after_cut else step
                row = trace.get(perception_step, {}); screen, place = row.get("screen"), row.get("place")
                folded = len(drained) <= 1 or si in (0, len(drained) - 1)
                world = folded and _world_type(screen, place, world_places)
                raw_px, raw_tiles = _tiles(seg["moves"], tile_px)
                checks = []
                if world:
                    check = _compare_segment(seg, start, previous, records, tile_px, flip if si == 0 else None, arrival if after_cut and flip else None)
                    if check: checks.append(check); eligible.append(check)
                segments.append({"segment": si, "start": seg["start"], "end": seg["end"], "cut": seg["cut"], "gap": seg["gap"], "screen": screen, "place": place,
                                 "folded": folded, "world_type": world, "checks": checks,
                                 **({"raw_odo_px": raw_px, "raw_odo_tiles": raw_tiles, "excluded_reason": "middle segment dropped by World" if not folded else "not world-type"} if not world else {})})
                if seg["end"] is not None: previous = seg["end"]
                cut_seen |= seg["cut"] is not None
            rows.append({"step": step, "frames": [start, end], "ram_start": [records[start]["x"], records[start]["y"]], "ram_end": [records[end]["x"], records[end]["y"]],
                         "map_start": records[start]["map"], "map_end": records[end]["map"],
                         "map_flip": flip, "segments": segments, "eligible_segments": len(eligible), "agree": bool(eligible) and all(c["agree"] for c in eligible)})
    finally: env.close()
    return rows

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run", required=True); ap.add_argument("--from", dest="from_step", type=int); ap.add_argument("--to", dest="to_step", type=int); ap.add_argument("--tile-px", type=int, default=16)
    args = ap.parse_args(); run = Path(args.run); run = run if run.is_absolute() else MAIN / run
    if args.tile_px <= 0: ap.error("--tile-px must be positive")
    checkpoints = sorted(int(p.name) for p in (run / "checkpoint").iterdir() if p.is_dir() and p.name.isdigit())
    presses = {r["step"]: r["keys"] for r in map(json.loads, (run / "presses.jsonl").read_text(encoding="utf8").splitlines()) if r.get("step") is not None}
    trace = {r["step"]: r for r in map(json.loads, (run / "trace.jsonl").read_text(encoding="utf8").splitlines()) if r.get("step") is not None}
    place_rows = (run / "memory" / "places.jsonl").read_text(encoding="utf8").splitlines()
    world_places = {r["id"]: r.get("screen") for r in map(json.loads, place_rows) if r.get("id")}
    rows = []
    for i, checkpoint in enumerate(checkpoints[:-1]):
        stop = checkpoints[i + 1]
        if args.from_step is not None and stop < args.from_step: continue
        if args.to_step is not None: stop = min(stop, args.to_step)
        if stop <= checkpoint: continue
        rows.extend(r for r in replay_window(run, checkpoint, stop, presses, trace, world_places, args.tile_px) if (args.from_step is None or r["step"] >= args.from_step))
        if args.to_step is not None and stop >= args.to_step: break
    checks = [(r["step"], s["segment"], c) for r in rows for s in r["segments"] for c in s["checks"]]
    scored = [r for r in rows if r["eligible_segments"]]; matched = sum(r["agree"] for r in scored)
    disagreements = [{"step": step, "segment": segment, **c} for step, segment, c in checks if not c["agree"]]
    excluded = [{"step": r["step"], **s} for r in rows for s in r["segments"] if not s["world_type"]]
    flips = [{"step": r["step"], **r["map_flip"]} for r in rows if r["map_flip"]]
    report = {"run": run.name, "tile_px": args.tile_px, "steps": len(scored), "total_steps": len(rows), "agreement": matched / len(scored) if scored else None,
              "matched_steps": matched, "map_flip_endpoints": flips, "excluded_segments": excluded, "step_checks": rows, "disagreements": disagreements}
    out = MAIN / ".tmp" / "r18" / "g" / f"{run.name}_odometer.json"; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=2), encoding="utf8")
    rate = report["agreement"] or 0
    print(f"{run.name}: {matched}/{len(scored)} scored steps agree ({rate:.2%}); excluded {len(excluded)} segments; map flips {len(flips)}; wrote {out}")
    for d in disagreements: print(f"DISAGREE step={d['step']} segment={d['segment']} frames={d['frames']} map={d['map']} odo={d['odo_tiles']} RAM={d['ram_tiles']} residual={d['residual']}")
    for x in excluded: print(f"EXCLUDED step={x['step']} segment={x['segment']} frames={[x['start'],x['end']]} screen={x['screen']} place={x['place']} raw_odo={x['raw_odo_tiles']}")
    for c in flips: print(f"MAP-FLIP step={c['step']} frame={c['seq']} source={c['source_map']} xy={c['xy']} last_same={c['last_same_xy']} arrival={c['arrival']['xy']} arrival_frame={c['arrival']['seq']}")
    return 0 if rows and rate >= .99 else 1 if rows else 2

if __name__ == "__main__": raise SystemExit(main())
