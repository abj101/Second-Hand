"""Summarize one agent run and evaluate the Pokémon M1 gate."""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


def _position(row):
    try:
        return int(row["x"]), int(row["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _tile_metrics(trace, truth):
    visits, visit_no, prior = {}, Counter(), None
    for row in trace:
        after_place = row.get("next_place") or row.get("place")
        actual = truth.get((row.get("step") or 0) + 1, {})
        mid = map_id(actual["map"]) if actual.get("map") is not None else None
        key = (after_place, mid)
        if key != prior:
            visit_no[key] += 1
        prior = key
        if row.get("screen") != "world" or mid is None:
            continue
        tpos = _position(actual)
        tile = row.get("tile")
        visit = (after_place, mid, visit_no[key])
        samples = visits.setdefault(visit, [])
        if tpos is None or not (isinstance(tile, (list, tuple)) and len(tile) == 2):
            samples.append((row.get("step", 0), None))
            continue
        try:
            tile = (int(tile[0]), int(tile[1]))
        except (TypeError, ValueError):
            samples.append((row.get("step", 0), None))
            continue
        samples.append((row.get("step", 0), (tile, tpos)))

    rows = []
    for (place, mid, number), samples in visits.items():
        measured = [(step, sample) for step, sample in samples if sample is not None]
        offset = ([measured[0][1][1][0] - measured[0][1][0][0],
                   measured[0][1][1][1] - measured[0][1][0][1]] if measured else None)

        def score(selected):
            valid = [sample for _, sample in selected if sample is not None]
            correct = sum((tile[0] + offset[0], tile[1] + offset[1]) == actual
                          for tile, actual in valid) if offset else 0
            return {"correct": correct, "total": len(selected) if valid else 0,
                    "measured": len(valid),
                    "world_steps": len(selected), "unmeasured": len(selected) - len(valid),
                    "accuracy": rate(correct, len(selected)) if valid else None}

        all_steps = score(samples)
        resumed = score([(step, sample) for step, sample in samples if step > 54])
        rows.append({"place": place, "map": mid, "visit": number, **all_steps,
                     "post_resume": resumed, "offset": offset})

    def totals(selected):
        correct = sum(r[selected]["correct"] if selected else r["correct"] for r in rows)
        total = sum(r[selected]["total"] if selected else r["total"] for r in rows)
        measured = sum(r[selected]["measured"] if selected else r["measured"] for r in rows)
        world_steps = sum(r[selected]["world_steps"] if selected else r["world_steps"] for r in rows)
        unmeasured = sum(r[selected]["unmeasured"] if selected else r["unmeasured"] for r in rows)
        return {"accuracy": rate(correct, total), "correct": correct, "total": total,
                "measured": measured, "world_steps": world_steps, "unmeasured": unmeasured}

    rates = [r["accuracy"] for r in rows if r["accuracy"] is not None]
    return {**totals(None), "visits": rows,
            "visits_measured": sum(r["measured"] > 0 for r in rows),
            "worst_visit_accuracy": min(rates, default=None),
            "post_resume": totals("post_resume")}


def _entity_duplicates(rows):
    entities = replay_rows(rows)
    recurring = {"menu_item", "move", "item", "option"}
    groups = []
    for row in entities:
        name = re.sub(r"(?:#\d+)+$", "", str(row.get("name", "")).casefold()).strip()
        if not name or str(row.get("kind", "")).casefold() == "player":
            continue
        kind = str(row.get("kind", "")).casefold()
        tile = row.get("tile")
        if kind in recurring:
            identity = ("global", kind, name)
        else:
            identity = (row.get("place"), name)
        matched = False
        for group in groups:
            if group[0] != identity:
                continue
            old_tile = group[1]
            if kind in recurring or (isinstance(tile, (list, tuple)) and len(tile) == 2 and
                    isinstance(old_tile, (list, tuple)) and len(old_tile) == 2 and
                    max(abs(int(tile[i]) - int(old_tile[i])) for i in (0, 1)) <= 1):
                group[2].append(row)
                matched = True
                break
        if not matched:
            groups.append([identity, tile, [row]])
    return sum(len(group[2]) - 1 for group in groups), entities


def replay_rows(rows):
    return list({r.get("id"): r for r in rows if r.get("id")}.values())


def _known_exit_reuse(trace, truth_rows, tile_history):
    truth = {r.get("step"): r for r in truth_rows}
    place_maps, known_tiles, total, reused, unmeasured = {}, {}, 0, 0, 0
    for row in sorted(trace, key=lambda r: r.get("step", 0)):
        step = row.get("step", 0)
        for tile in tile_history:
            if tile.get("id") and isinstance(tile.get("step"), int) and tile["step"] < step:
                known_tiles[tile["id"]] = tile
        before_place = row.get("place")
        after_place = row.get("next_place") or before_place
        actual = truth.get(step + 1, {})
        mid = map_id(actual["map"]) if actual.get("map") is not None else None
        cut_exit = row.get("exit")
        if row.get("scene_cut") is True and not isinstance(cut_exit, (list, tuple)):
            unmeasured += 1
        elif row.get("scene_cut") is True and len(cut_exit) == 3:
            try:
                x, y, direction = int(cut_exit[0]), int(cut_exit[1]), str(cut_exit[2]).upper()
            except (TypeError, ValueError):
                x = y = None
                direction = ""
            tile = next((r for r in known_tiles.values() if r.get("place") == before_place and
                         r.get("x") == x and r.get("y") == y), None)
            edge = (tile or {}).get("exit") or {}
            expected = edge.get("to")
            if edge.get("dir") == direction and expected in place_maps:
                total += 1
                reused += after_place == expected
        if after_place and mid is not None:
            place_maps.setdefault(after_place, mid)
    return {"known_exit_revisits": total, "reused": reused, "mismatches": total - reused,
            "unmeasured_transitions": unmeasured,
            "pass": reused == total if total else None}


def _e4_metrics(run, trace, truth_rows):
    from agent.memory import normalize_kind
    kinds = {r["id"]: normalize_kind(r.get("kind")) for r in replay(run / "memory" / "entities.jsonl")}
    verified = {c.get("id") for r in trace if (c := r.get("choice") or {}).get("verb") == "approach" and
                kinds.get(c.get("id")) in {"door", "exit"} and "place_changes" in (o := r.get("outcome") or {}).get("effects", []) and
                (o.get("basis") or {}).get("place_changes") == "pixel"}
    truth_rows = sorted(truth_rows, key=lambda x: x.get("step", 0))
    battles = sum(r.get("battle") == 2 and (i == 0 or truth_rows[i-1].get("battle") != 2) for i, r in enumerate(truth_rows))
    moves = sum((r.get("choice") or {}).get("verb") == "select" and
                 (truth := next((t for t in truth_rows if t.get("step") == r.get("step")), {})).get("battle") == 2
                 for r in trace)
    action = lambda r: (r.get("place"), str((r.get("choice") or {}).get("verb", "")).casefold(),
                        str((r.get("choice") or {}).get("target", "")).casefold())
    observed = lambda r: (tuple(sorted(e for e in (r.get("outcome") or {}).get("effects", [])
                        if ((r.get("outcome") or {}).get("basis") or {}).get(e) == "pixel")),
                        str((r.get("outcome") or {}).get("to") or ""))
    ordered = sorted(trace, key=lambda x: x.get("step", 0))
    repeats = Counter((action(a), action(b), observed(a), observed(b)) for a, b in zip(ordered, ordered[1:]) if action(a)[1] and action(b)[1] and observed(a)[0] and observed(b)[0])
    plans = [(i, r) for i, r in enumerate(trace) if r.get("wake")]
    done_plans = {t.get("plan_step") for t in replay(run / "memory" / "tasks.jsonl") if t.get("status") == "done" and isinstance(t.get("plan_step"), int)}
    completed = sum(row.get("step") in done_plans for _, row in plans)
    return {"doors_verified": len(verified - {None}),
            "patterns_promoted": sum(r.get("state") == "promoted" for r in replay(run / "memory" / "patterns.jsonl")),
            "battles": battles, "moves_selected": moves,
            "repeat_sequences": {"length": 2, "count": sum(n > 1 for n in repeats.values())},
            "reflection_usefulness": {"plan_calls": len(plans), "goals_completed": completed}}


def _repredict(run, trace, hide_memory=False):
    from tempfile import TemporaryDirectory
    from agent.memory import Memory, normalize_kind
    from agent.predict import PRED, predict, verdict

    history = read(run / "memory" / "entities.jsonl")
    chooser = {r.get("step"): r.get("choice") or {} for r in read(run / "chooser.jsonl")}
    counts, verified, excluded = {"repeat": [0, 0, 0], "first": [0, 0, 0]}, set(), 0
    with TemporaryDirectory() as temp:
        mem = Memory(Path(temp))
        for row in sorted(trace, key=lambda x: x.get("step", 0)):
            step, choice = row.get("step", 0), row.get("choice") or {}
            for entity in history:
                if entity.get("last", entity.get("first", 0)) <= step:
                    mem.upsert("entities", entity)
            eid, verb = choice.get("id"), str(choice.get("verb") or "").casefold()
            entity = mem.get("entities", eid) or {}
            key = (normalize_kind(entity.get("kind")), verb)
            was_repeat = key in verified
            learned = predict(mem, {"verb": verb}, eid, row.get("place")) if eid else None
            logged = row.get("pred") or {}
            saved = chooser.get(step, {})
            guess = logged if logged.get("basis") == "guess" else (
                {"basis": "guess", "effect": saved.get("effect"), "detail": saved.get("detail")} if saved.get("effect") else None)
            outcome = row.get("outcome") or {}
            pixel = [e for e in outcome.get("effects", [])
                     if (outcome.get("basis") or {}).get(e) == "pixel"]
            pred = learned or guess
            actual = {"effects": pixel, "to": outcome.get("to")}
            if not key[0] or not verb or not guess or not guess.get("effect") or not pixel: excluded += 1
            else:
                selected = guess if hide_memory else pred
                result, baseline = verdict(selected, actual), verdict(guess, actual)
                slot = counts["repeat" if was_repeat else "first"]
                slot[0] += result == "hit"; slot[1] += baseline == "hit"; slot[2] += 1
            if eid and key[0] and verb and pixel: verified.add(key)
            if eid:
                for effect in pixel:
                    detail = outcome.get("to") if effect == "place_changes" else outcome.get("detail") or ""
                    if effect == "place_changes" and detail: mem.upsert("places", {"id": detail, "state": "place"})
                    if effect in PRED:
                        mem.relate(eid, PRED[effect], str(detail or ""), src="observed", step=step,
                                   hit=True, verb=verb, pattern_id=pred.get("id") if pred and pred.get("effect") == effect and pred.get("basis") == "pattern" else None)
            effect = (pred or {}).get("effect")
            if eid and effect in PRED and pixel and verdict(pred, actual) != "hit":
                mem.relate(eid, PRED[effect], str(pred.get("detail") or ""), src="observed", step=step,
                           hit=False, verb=verb, pattern_id=pred.get("id") if pred.get("basis") == "pattern" else None)
    first, repeat = counts["first"], counts["repeat"]
    n = repeat[2] + first[2]
    return {"acc_repeat": rate(repeat[1] if hide_memory else repeat[0], repeat[2]),
            "acc_first": rate(first[1] if hide_memory else first[0], first[2]),
            "n": n, "n_repeat": repeat[2], "n_first": first[2],
            "excluded": excluded, "coverage": rate(n, len(trace))}


def evaluate(run, *, repredict=False, hide_memory=False):
    run = Path(run)
    trace, truth_rows = read(run / "trace.jsonl"), read(run / "truth.jsonl")
    truth = {r.get("step"): r for r in truth_rows}
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
    entity_rows = read(run / "memory" / "entities.jsonl")
    duplicates, unique_entities = _entity_duplicates(entity_rows)
    new_duplicates, _ = _entity_duplicates([r for r in unique_entities
                                             if isinstance(r.get("first"), int) and r["first"] > 54])
    legacy_duplicates, _ = _entity_duplicates([r for r in unique_entities
                                                if isinstance(r.get("first"), int) and r["first"] <= 54])
    tile_accuracy = _tile_metrics(trace, truth)
    exit_reuse = _known_exit_reuse(trace, truth_rows, read(run / "memory" / "tiles.jsonl"))
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
    result = {**m1,
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
                            "rate": rate(poisoned, len(claims))},
            "spatial": {"tile_accuracy_per_visit": tile_accuracy,
                        "entity_duplicates": {"total": duplicates,
                                              "legacy": legacy_duplicates,
                                              "new_after_resume": new_duplicates},
                        "known_exit_id_reuse": exit_reuse},
            **_e4_metrics(run, trace, truth_rows)}
    if repredict:
        result.update(_repredict(run, trace, hide_memory))
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    flags = {arg for arg in args if arg.startswith("--")}
    paths = [arg for arg in args if not arg.startswith("--")]
    if len(paths) != 1 or flags - {"--repredict", "--hide-memory"} or ("--hide-memory" in flags and "--repredict" not in flags):
        raise SystemExit("usage: eval_run.py runs/<id> [--repredict [--hide-memory]]")
    result = evaluate(paths[0], repredict="--repredict" in flags, hide_memory="--hide-memory" in flags)
    (Path(paths[0]) / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
