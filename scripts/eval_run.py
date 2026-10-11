"""Summarize one agent run and evaluate the Pokémon M1 gate."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

MAPS = {"0x26": "Red's house 2F", "0x25": "Red's house 1F",
        "0x00": "Pallet Town", "0x28": "Oak's lab"}
EDGES = (("0x26", "0x25"), ("0x25", "0x00"), ("0x00", "0x28"))


def read(path):
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        return []


def replay(path):
    return list({r.get("id"): r for r in read(path) if r.get("id")}.values())


def map_id(value):
    if isinstance(value, int):
        return f"0x{value:02x}"
    try:
        return f"0x{int(str(value), 0):02x}"
    except ValueError:
        return str(value).casefold()


def rate(n, d):
    return n / d if d else None


def evaluate(run):
    run = Path(run)
    trace, truth = read(run / "trace.jsonl"), {r.get("step"): r for r in read(run / "truth.jsonl")}
    places = {r["id"]: r for r in read(run / "memory" / "places.jsonl") if r.get("id")}
    step_places = defaultdict(Counter)
    for row in trace:
        t = truth.get(row.get("step"))
        pid = row.get("place")
        if t and pid in places and places[pid].get("state") == "place" and t.get("map") is not None:
            step_places[pid][map_id(t["map"])] += 1
    mapping = {}
    for pid, counts in step_places.items():
        total = sum(counts.values())
        dominant, n = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0]
        mapping[pid] = {"map": dominant, "purity": n / total, "observations": total,
                        "map_counts": dict(counts)}
    matched = {mid: next(iter(sorted((pid for pid, m in mapping.items()
                          if m["map"] == mid and m["purity"] >= .9),
                          key=lambda pid: (-mapping[pid]["observations"], pid))), None) for mid in MAPS}
    purity = {mid: mapping[pid]["purity"] if pid else None for mid, pid in matched.items()}
    distinct_places = [pid for pid in matched.values() if pid]

    valid_edges = set()
    for row in trace:
        outcome = row.get("outcome") or {}
        basis = outcome.get("basis") or {}
        pixel_cut = row.get("scene_cut") is True and any(
            effect in outcome.get("effects", []) and basis.get(effect) == "pixel"
            for effect in ("place_changes", "screen_changes"))
        if not pixel_cut:
            continue
        src = row.get("place")
        dst = row.get("next_place") or row.get("transition_to") or outcome.get("to")
        has_ok_edge = any(e.get("to") == dst and e.get("ok", 0) >= 1
                          for e in places.get(src, {}).get("edges", []))
        before, after = truth.get(row.get("step")), truth.get(row.get("step", -1) + 1)
        if has_ok_edge and src in mapping and dst in mapping and before and after:
            valid_edges.add((map_id(before.get("map")), map_id(after.get("map"))))
    edges_found = {f"{a}->{b}": ((a, b) in valid_edges or (b, a) in valid_edges)
                   for a, b in EDGES}

    verdict_rows = [r for r in trace if r.get("verdict") in {"hit", "partial", "surprise", "miss"}]
    def accuracy(rows):
        return rate(sum(r.get("verdict") == "hit" for r in rows), len(rows))
    buckets = {}
    for row in verdict_rows:
        start = ((int(row.get("step", 1)) - 1) // 50) * 50 + 1
        buckets.setdefault(start, []).append(row)
    skills = [r.get("skill") or {} for r in trace if isinstance(r.get("skill"), dict)]
    done = sum(s.get("status") == "done" for s in skills)
    elapsed = [float(r["ms"]) for r in trace if isinstance(r.get("ms"), (int, float))]
    calls_path = run / "calls.jsonl"
    calls = read(calls_path)
    has_call_log = calls_path.exists()
    call_count = len(calls) if has_call_log else sum(int(r.get("calls") or 0) for r in trace)
    cloud_calls = sum(1 for c in calls if c.get("ok") and not c.get("cached") and
                      (str(c.get("model", "")).endswith("-cloud") or ":cloud" in str(c.get("model", "")))) if has_call_log else None
    cache_hits = sum(c.get("cached") is True for c in calls) if has_call_log else None
    http_attempts = sum(c["requests"] if type(c.get("requests")) is int else
                        0 if c.get("cached") else 1 + int(c.get("retries") or 0)
                        for c in calls) if has_call_log else None
    retries = sum(int(c.get("retries") or 0) for c in calls if not c.get("cached")) if has_call_log else None
    call_roles = Counter(c.get("role") for c in calls if c.get("role"))
    reused = [r for r in trace if isinstance(r.get("perception_reused"), bool)]
    choices = [r.get("choice") or {} for r in trace if isinstance(r.get("choice"), dict)]
    no_call = sum(c.get("called") is False for c in choices)

    failed = set()
    repeated = 0
    for row in trace:
        choice = row.get("choice") or {}
        key = (row.get("place"), row.get("frame_hash"), str(choice.get("verb", "")).casefold(),
               str(choice.get("target", "")).casefold())
        if key[1] and key in failed:
            repeated += 1
        if key[1] and row.get("verdict") in ("miss", "surprise"):
            failed.add(key)
    chooser = read(run / "chooser.jsonl")
    shown = used = 0
    for row in chooser:
        ids = set(row.get("view_ids", []))
        choice = row.get("choice") or {}
        reported = set(choice.get("used", []))
        shown += len(ids)
        used += len(ids & reported)
    relations = replay(run / "memory" / "relations.jsonl")
    claims = [r for r in relations if r.get("src") == "model"]
    poisoned = sum(r.get("state") == "refuted" for r in claims)
    party_steps = [step for step, row in truth.items() if (row.get("party") or 0) >= 1]
    ordered_truth = sorted(truth.values(), key=lambda r: r.get("step", -1), reverse=True)
    player = next((r.get("player_name") for r in ordered_truth if r.get("player_name")), None)
    rival = next((r.get("rival_name") for r in ordered_truth if r.get("rival_name")), None)
    knowledge = replay(run / "memory" / "knowledge.jsonl")
    game_text = sum(r.get("src") == "game_text" for r in knowledge)
    m1 = {"m1_starter": bool(party_steps), "steps_to_starter": max(0, min(party_steps) - 1) if party_steps else None,
          "place_purity": purity, "places_matched": len(set(distinct_places)) == 4,
          "boundaries_pass": None,
          "place_mapping": {MAPS[mid]: {"place": matched[mid], "purity": purity[mid]} for mid in MAPS},
          "edges_found": edges_found, "knowledge_game_text": game_text,
          "player_name": player, "rival_name": rival,
          "player_name_match": str(player or "").casefold() == "hand",
          "rival_name_match": str(rival or "").casefold() == "blue"}
    m1["m1_gate_pass"] = (m1["m1_starter"] and m1["steps_to_starter"] <= 600 and
                           m1["places_matched"] and all(p is not None and p >= .9 for p in purity.values()) and
                           all(edges_found.values()) and game_text >= 10 and
                           m1["player_name_match"] and m1["rival_name_match"])
    return {**m1,
            "execution_success": {"done": done, "total": len(skills), "rate": rate(done, len(skills))},
            "prediction_accuracy": {"overall": accuracy(verdict_rows),
                                    "by_50_steps": {f"{k}-{k+49}": accuracy(v) for k, v in sorted(buckets.items())}},
            "coverage": {"confirmed_places": len(mapping), "affordance_types": sorted({
                (r.get("choice") or {}).get("verb") for r in trace if (r.get("choice") or {}).get("verb")})},
            "cost": {"total_asks": call_count, "calls_per_step": rate(call_count, len(trace)),
                     "total_asks_per_step": rate(call_count, len(trace)),
                     "uncached_cloud_successful_asks": cloud_calls,
                     "uncached_cloud_asks_per_step": rate(cloud_calls, len(trace)) if cloud_calls is not None else None,
                     "cache_hits": cache_hits, "cache_hit_share": rate(cache_hits, len(calls)) if cache_hits is not None else None,
                     "http_attempts": http_attempts, "retries": retries,
                     "escalations": call_roles.get("escalate", 0) if has_call_log else None,
                     "time_per_step_seconds": rate(sum(elapsed), len(elapsed)) / 1000 if elapsed else None,
                     "perception_reuse_rate": rate(sum(r["perception_reused"] for r in reused), len(reused)),
                     "no_call_choice_share": rate(no_call, len(choices))},
            "repeated_mistakes": {"count": repeated, "rate": rate(repeated, len(trace))},
            "retrieval_use": {"used_shown_ids": used, "shown_ids": shown, "rate": rate(used, shown)},
            "poison_rate": {"refuted_model_claims": poisoned, "model_claims": len(claims),
                            "rate": rate(poisoned, len(claims))}}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: eval_run.py runs/<id>")
    result = evaluate(sys.argv[1])
    (Path(sys.argv[1]) / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
