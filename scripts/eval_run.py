"""Summarize one agent run and evaluate the Pokémon M1 gate."""
import json
import re
import struct
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


def _execution(trace):
    """Execution success per action, from the trace's skill status. A pending row (observation not settled yet) is neither a success
    nor a failure; a settled row resolves to its action (action_step when it resolves an earlier pending action, else its own step);
    each action counts once. Verdicts are not used: a hit verdict on a failed execution is a failure."""
    actions, pending = {}, 0
    for row in trace:
        if row.get("observation_status") == "pending": pending += 1; continue
        if "observation_status" in row: status = row.get("execution")  # settled
        else: status = (row.get("skill") or {}).get("status") if isinstance(row.get("skill"), dict) else None  # legacy trace: no execution field
        if status is None: continue
        actions.setdefault(row.get("action_step") or row.get("step"), status)
    done = sum(status == "done" for status in actions.values())
    return {"done": done, "failed": len(actions) - done, "total": len(actions), "pending_rows": pending, "rate": rate(done, len(actions))}

def _no_effect_streak(trace):
    longest = streak = 0
    for row in trace:
        if (row.get("outcome") or {}).get("effects") == ["no_change"]:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    return longest


def _health(result, world_maps):
    places = result.get("place_ids_per_map")
    p3 = ({mid: len((places or {}).get(mid, [])) for mid in sorted(world_maps)}
          if world_maps else None)
    tile = (result.get("spatial") or {}).get("tile_accuracy_per_visit", {}).get("accuracy")
    repeats = result.get("repeat_actions")
    values = (
        ("self_spot_accuracy", result.get("self_spot_accuracy"), .95),
        ("self_spot_coverage", result.get("self_spot_coverage"), .95),
        ("cut_self_loops", result.get("cut_self_loops"), 0),
        ("place_ids_per_map", p3, 1),
        ("screen_places", result.get("screen_places"), 0),
        ("tile_accuracy", tile, .95),
        ("no_effect_streak_max", result.get("no_effect_streak_max"), 5),
        ("repeat_actions_max_length", max((len(r.get("steps", [])) for r in repeats), default=0)
         if repeats is not None else None, 5),
    )
    health = []
    for field, value, limit in values:
        if field == "place_ids_per_map":
            green = all(count == limit for count in value.values()) if value else None
        elif value is None or (field == "cut_self_loops" and result.get("no_effect_streak_max") is None):
            green = None
        elif field in ("cut_self_loops", "screen_places"):
            green = value == limit
        elif field in ("self_spot_accuracy", "self_spot_coverage", "tile_accuracy"):
            green = value >= limit
        else:
            green = value <= limit
        health.append({"field": field, "value": value, "green": green})
    return health


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


def _p3b_metrics(trace, truth_rows):
    recent = 0
    for row in trace:
        flags = (row.get("act") or {}).get("flags") or []
        recent += "recent:" in flags if isinstance(flags, str) else any("recent:" in str(flag) for flag in flags)
    return {"cut_self_loops": sum(r.get("scene_cut") is True and r.get("place") is not None and
                                  r.get("next_place") == r.get("place") for r in trace),
            "recent_line_steps": recent}


def _frame_size(run, row):
    try:
        data = (run / row["frame"]).read_bytes()[:24]
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
    except (KeyError, OSError, TypeError):
        pass
    return None


def _self_spot_metrics(run, trace, truth_rows):
    truth = {r.get("step"): r for r in truth_rows}
    world_steps = locked = correct = measured = 0
    for row in trace:
        if row.get("screen") != "world":
            continue
        world_steps += 1
        if row.get("self_locked") is not True:
            continue
        locked += 1
        spot = row.get("self_spot")
        actual = truth.get((row.get("step") or 0) + 1, {})
        sprite = next((s for s in actual.get("sprites", [])
                       if isinstance(s, dict) and s.get("slot") == 0), None)
        size = _frame_size(run, row)
        if (not isinstance(spot, (list, tuple)) or len(spot) != 2 or not sprite or
                not size or size[0] <= 0 or size[1] <= 0):
            continue
        try:
            dx = float(spot[0]) * size[0] - (float(sprite["x"]) + 8)
            dy = float(spot[1]) * size[1] - (float(sprite["y"]) + 8)
        except (KeyError, TypeError, ValueError):
            continue
        correct += (dx * dx + dy * dy) ** .5 <= 8
        measured += 1
    return {"self_spot_accuracy": rate(correct, measured),
            "self_spot_coverage": rate(locked, world_steps),
            "self_spot_correct": correct, "self_spot_measured": measured,
            "self_locked_world_steps": locked, "world_steps": world_steps}


def _false_walls(trace, tile_rows, from_step=None):
    steps = {}
    for row in tile_rows:
        if isinstance(row.get("step"), int):
            for direction in row.get("blocked", []):
                steps.setdefault((row.get("place"), row.get("x"), row.get("y"), direction), []).append(row["step"])
    dirs = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
    crossed = set()
    ordered = sorted((r for r in trace if r.get("screen") == "world"), key=lambda r: r.get("step", -1))
    for before, after in zip(ordered, ordered[1:]):
        if (before.get("place") != after.get("place") or
                after.get("step") != before.get("step", -2) + 1 or
                before.get("scene_cut") or after.get("scene_cut") or
                before.get("drift_fix") or after.get("drift_fix") or
                (before.get("probe") or {}).get("presses", 0) or
                (after.get("probe") or {}).get("presses", 0) or
                before.get("next_place") not in (None, before.get("place")) or
                after.get("next_place") not in (None, after.get("place"))):
            continue
        if from_step is not None and after.get("step", 0) < from_step:
            continue
        a, b = before.get("tile"), after.get("tile")
        if not (isinstance(a, (list, tuple)) and len(a) == 2 and
                isinstance(b, (list, tuple)) and len(b) == 2):
            continue
        try:
            dx, dy = int(b[0]) - int(a[0]), int(b[1]) - int(a[1])
            ax, ay = int(a[0]), int(a[1])
        except (TypeError, ValueError):
            continue
        if not dx and not dy:
            continue
        if dx and dy:
            continue
        direction = "RIGHT" if dx > 0 else "LEFT" if dx < 0 else "DOWN" if dy > 0 else "UP"
        sx, sy = dirs[direction]
        for n in range(max(abs(dx), abs(dy))):
            key = (after["place"], ax + sx * n, ay + sy * n, direction)
            if any(step < after["step"] for step in steps.get(key, ())):
                crossed.add(key)
    return len(crossed)


def _p3_metrics(run, trace, truth_rows):
    paths = {name: run / "memory" / f"{name}.jsonl"
             for name in ("places", "seen", "knowledge", "entities")}
    has_trace, has_truth = (run / "trace.jsonl").exists(), (run / "truth.jsonl").exists()
    has_places, has_knowledge = paths["places"].exists(), paths["knowledge"].exists()
    places = {r.get("id"): r for r in read(paths["places"]) if r.get("id")} if has_places else {}
    trace_steps = {r.get("step") for r in trace} if has_trace else set()
    screen_kinds = {"dialogue_box", "dialogue_screen", "menu"}
    screen_places = (len({r["id"] for r in read(paths["places"]) if r.get("id")
                          if r.get("first") in trace_steps and
                          ((r.get("screen") is not None and str(r["screen"]).casefold() != "world") or
                           str(r.get("name", "")).casefold() in screen_kinds)})
                     if has_trace and trace and has_places else None)
    live_trace = ([r for r in trace if r.get("place") in places and places[r["place"]].get("state") != "merged"]
                  if has_trace and has_places else [])
    truth = {r.get("step"): r for r in truth_rows}
    world_rows = [r for r in live_trace if r.get("screen") == "world"]

    place_ids = defaultdict(set)
    for row in world_rows if has_trace and has_truth and has_places else ():
        if (row.get("probe") or {}).get("presses", 0):
            continue  # Probe may have changed place after the runner's before-action truth sample.
        actual = truth.get(row.get("step")) or {}
        if actual.get("map") is not None:
            place_ids[map_id(actual["map"])].add(row["place"])

    seen_in_bounds = None
    if paths["seen"].exists() and has_trace and has_truth and has_places:
        sizes = {map_id(r["map"]): (r.get("map_h"), r.get("map_w")) for r in truth_rows
                 if r.get("map") is not None and isinstance(r.get("map_h"), int) and isinstance(r.get("map_w"), int)}
        visits = _tile_metrics(live_trace, truth)
        seen_by_place = {r.get("id"): r.get("cells", []) for r in read(paths["seen"]) if r.get("id")}
        by_map = defaultdict(lambda: [0, 0])
        for visit in visits["visits"]:
            dims, cells, offset = sizes.get(visit["map"]), seen_by_place.get(visit["place"]), visit["offset"]
            if not dims or not cells or not offset:
                continue
            height, width = dims
            inside, total = by_map[visit["map"]]
            for cell in cells:
                try:
                    x, y = map(int, cell.split(","))
                except (AttributeError, ValueError):
                    continue
                total += 1
                inside += 0 <= x + offset[0] < 2 * width and 0 <= y + offset[1] < 2 * height
            by_map[visit["map"]] = [inside, total]
        total_inside = sum(v[0] for v in by_map.values())
        total_cells = sum(v[1] for v in by_map.values())
        if total_cells:
            seen_in_bounds = {"inside": total_inside, "total": total_cells,
                              "rate": rate(total_inside, total_cells),
                              "by_map": {mid: {"inside": v[0], "total": v[1], "rate": rate(*v)}
                                         for mid, v in sorted(by_map.items())}}

    knowledge = read(paths["knowledge"]) if has_knowledge else []
    new_text_steps = {r.get("step") for r in knowledge if r.get("src") == "game_text"}
    streak = streak_max = 0
    prior_map = prior_step = None
    for row in sorted(trace, key=lambda r: r.get("step", -1)) if has_trace and has_truth and has_knowledge else ():
        step = row.get("step")
        actual = truth.get(step) or {}
        mid = map_id(actual["map"]) if actual.get("map") is not None else None
        if mid is None or (prior_map is not None and
                           (mid != prior_map or not isinstance(step, int) or step != prior_step + 1)):
            streak = 0
        if mid is not None:
            streak = 0 if step in new_text_steps else streak + 1
            streak_max = max(streak_max, streak)
        prior_map, prior_step = mid, step

    entity_rows = read(paths["entities"]) if paths["entities"].exists() else None
    interest_duplicates = None
    if entity_rows is not None and has_places and any("tier" in r for r in entity_rows):
        interest = [r for r in replay_rows(entity_rows)
                    if r.get("tier") == "interest" and r.get("place") in places
                    and places[r["place"]].get("state") != "merged"]
        groups = []
        for row in interest:
            name = re.sub(r"(?:#\d+)+$", "", str(row.get("name", "")).casefold()).strip()
            if not name:
                continue
            kind, tile = str(row.get("kind", "")).casefold(), row.get("tile")
            identity = (row.get("place"), kind, name)
            group = next((g for g in groups if g[0] == identity and
                          isinstance(tile, (list, tuple)) and len(tile) == 2 and
                          isinstance(g[1], (list, tuple)) and len(g[1]) == 2 and
                          max(abs(int(tile[i]) - int(g[1][i])) for i in (0, 1)) <= 1), None)
            if group:
                group[2].append(row)
            else:
                groups.append([identity, tile, [row]])
        duplicate_rows = [row for _, _, rows in groups if len(rows) > 1 for row in rows[1:]]
        interest_duplicates = {"total": len(duplicate_rows),
                               "new_after_74": sum(isinstance(r.get("first"), int) and r["first"] > 74
                                                   for r in duplicate_rows)}

    game_text = [str(r.get("text", "")) for r in knowledge
                 if r.get("src") == "game_text" and isinstance(r.get("text"), str) and r["text"].strip()]
    notebook_path = run / "memory" / "notebook.json"
    goals_with_evidence = None
    if notebook_path.exists() and has_knowledge:
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        goals_with_evidence = []
        for key, goal in (notebook.get("goals") or {}).items():
            evidence = goal.get("evidence") if isinstance(goal, dict) else None
            if not isinstance(evidence, str) or not evidence.strip():
                continue
            fragments = [evidence.strip(), *re.findall(r"[\"“](.+?)[\"”]", evidence)]
            quote = next((text for text in game_text for fragment in sorted(fragments, key=len, reverse=True)
                          if len(fragment) >= 8 and fragment.casefold() in text.casefold()), None)
            if quote:
                goals_with_evidence.append({"key": key, "evidence": quote})

    entity_by_id = {r.get("id"): r for r in entity_rows or [] if r.get("id")}
    def planned_place(row):
        act = row.get("act") or {}
        action = act.get("action") or {}
        target = action.get("go_to")
        if not isinstance(target, str):
            return None
        choice_id = (row.get("choice") or {}).get("id")
        if choice_id in places and places[choice_id].get("state") != "merged":
            return choice_id
        entity = entity_by_id.get(choice_id)
        if entity and entity.get("place") in places and places[entity["place"]].get("state") != "merged":
            return entity["place"]
        match = next((p["id"] for p in places.values() if p.get("state") != "merged" and
                      target.casefold() in {str(x).casefold() for x in (p.get("id"), p.get("name"), *(p.get("aliases") or [])) if x}), None)
        return match

    travel_cases = []
    for row in live_trace:
        skill = row.get("skill") or {}
        target_place = planned_place(row)
        onscreen_ids = {e.get("id") for e in row.get("entities", []) if isinstance(e, dict)}
        is_memory_go_to = target_place and (row.get("choice") or {}).get("id") not in onscreen_ids
        if str(skill.get("reason", "")).casefold().startswith("travel") or is_memory_go_to:
            arrived = bool(target_place and skill.get("status") == "done" and
                           (row.get("next_place") or row.get("place")) == target_place)
            travel_cases.append({"step": row.get("step"), "frame": row.get("frame"),
                                 "why": (row.get("act") or {}).get("why"),
                                 "planned_place": target_place,
                                 "arrived": arrived})

    repeat_trace = ([r for r in trace if places.get(r.get("place"), {}).get("state") != "merged"]
                    if has_places else trace)
    repeated_actions = []
    run_rows, prior_action = [], None
    for row in sorted(repeat_trace, key=lambda r: r.get("step", -1)) if has_trace else ():
        choice = row.get("choice") or {}
        action = (str(choice.get("verb", "")).casefold(), str(choice.get("target", "")).casefold())
        if (not action[0] or row.get("step") != (run_rows[-1].get("step", -2) + 1 if run_rows else None)
                or action != prior_action):
            if len(run_rows) >= 3:
                repeated_actions.append({"verb": prior_action[0], "target": prior_action[1],
                                         "steps": [r.get("step") for r in run_rows],
                                         "frames": [r.get("frame") for r in run_rows],
                                         "why": [(r.get("act") or {}).get("why") for r in run_rows]})
            run_rows = []
        if action[0]:
            run_rows.append(row)
        prior_action = action
    if len(run_rows) >= 3:
        repeated_actions.append({"verb": prior_action[0], "target": prior_action[1],
                                 "steps": [r.get("step") for r in run_rows],
                                 "frames": [r.get("frame") for r in run_rows],
                                 "why": [(r.get("act") or {}).get("why") for r in run_rows]})

    reentries, visited, left = [], set(), {}
    ordered_truth = sorted(truth_rows, key=lambda r: r.get("step", -1)) if has_truth and has_trace and has_places else []
    trace_by_step = {r.get("step"): r for r in live_trace}
    previous = None
    for actual in ordered_truth:
        mid, step = map_id(actual["map"]) if actual.get("map") is not None else None, actual.get("step")
        if step not in trace_by_step:
            previous = None
            continue
        if mid is not None and previous and step == previous[0] + 1 and mid != previous[1]:
            left[previous[1]] = previous[0]
            if mid in visited and mid in left and step - left[mid] <= 5:
                row = trace_by_step.get(step, {})
                reentries.append({"map": mid, "left_step": left[mid], "step": step,
                                  "frame": row.get("frame"), "why": (row.get("act") or {}).get("why")})
        if mid is not None:
            visited.add(mid)
            previous = (step, mid)
        else:
            previous = None

    return {"place_ids_per_map": ({mid: sorted(ids) for mid, ids in sorted(place_ids.items())}
                                  if has_trace and has_truth and has_places else None),
            "screen_places": screen_places,
            "seen_in_bounds": seen_in_bounds,
            "stuck_streak_max": streak_max if has_trace and has_truth and has_knowledge else None,
            "travel": ({"started": len(travel_cases), "arrived": sum(c["arrived"] for c in travel_cases),
                        "cases": travel_cases} if has_trace and has_places else None),
            "notebook_goals_with_evidence": goals_with_evidence,
            "repeat_actions": repeated_actions if has_trace else None,
            "reentries": reentries if has_truth and has_trace and has_places else None,
            "interest_entity_duplicates": interest_duplicates}


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


def _e4_metrics(run, trace, truth_rows, from_step=None):
    from agent.memory import normalize_kind
    kinds = {r["id"]: normalize_kind(r.get("kind")) for r in replay(run / "memory" / "entities.jsonl")}
    verified = {c.get("id") for r in trace if (c := r.get("choice") or {}).get("verb") == "approach" and
                kinds.get(c.get("id")) in {"door", "exit"} and "place_changes" in (o := r.get("outcome") or {}).get("effects", []) and
                (o.get("basis") or {}).get("place_changes") == "pixel"}
    truth_rows = sorted(truth_rows, key=lambda x: x.get("step", 0))
    battles = sum(r.get("battle") == 2 and (i == 0 or truth_rows[i-1].get("battle") != 2) and
                  (from_step is None or r.get("step", 0) >= from_step)
                  for i, r in enumerate(truth_rows))
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


def evaluate(run, *, repredict=False, hide_memory=False, from_step=None):
    run = Path(run)
    full_trace, truth_rows = read(run / "trace.jsonl"), read(run / "truth.jsonl")
    trace = [row for row in full_trace if from_step is None or row.get("step", 0) >= from_step]
    truth = {r.get("step"): r for r in truth_rows}
    places = {r["id"]: r for r in read(run / "memory" / "places.jsonl") if r.get("id")}
    step_places = defaultdict(Counter)
    for row in full_trace:
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
    for row in full_trace:
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
    elapsed = [float(r["ms"]) for r in trace if isinstance(r.get("ms"), (int, float))]
    calls_path = run / "calls.jsonl"
    calls = read(calls_path)
    has_call_log = calls_path.exists()
    call_steps_known = from_step is None or all(isinstance(c.get("step"), int) for c in calls)
    selected_calls = ([c for c in calls if c.get("step", 0) >= from_step]
                      if from_step is not None and has_call_log and call_steps_known else calls)
    selected_call_log = has_call_log and call_steps_known
    call_count = (len(selected_calls) if selected_call_log else
                  sum(int(r.get("calls") or 0) for r in trace) if from_step is not None or not has_call_log else len(calls))
    cloud_calls = sum(1 for c in selected_calls if c.get("ok") and not c.get("cached") and
                      (str(c.get("model", "")).endswith("-cloud") or ":cloud" in str(c.get("model", "")))) if selected_call_log else None
    cache_hits = sum(c.get("cached") is True for c in selected_calls) if selected_call_log else None
    http_attempts = sum(c["requests"] if type(c.get("requests")) is int else
                        0 if c.get("cached") else 1 + int(c.get("retries") or 0)
                        for c in selected_calls) if selected_call_log else None
    retries = sum(int(c.get("retries") or 0) for c in selected_calls if not c.get("cached")) if selected_call_log else None
    call_roles = Counter(c.get("role") for c in selected_calls if c.get("role")) if selected_call_log else Counter()
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
    p3 = _p3_metrics(run, trace, truth_rows)
    tile_rows = read(run / "memory" / "tiles.jsonl")
    exit_reuse = _known_exit_reuse(trace, truth_rows, tile_rows)
    spot_metrics = _self_spot_metrics(run, trace, truth_rows)
    false_walls = _false_walls(full_trace, tile_rows, from_step)
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
            "execution_success": _execution(trace),
            "prediction_accuracy": {"overall": accuracy(verdict_rows),
                                    "by_50_steps": {f"{k}-{k+49}": accuracy(v) for k, v in sorted(buckets.items())}},
            "coverage": {"confirmed_places": len(mapping), "affordance_types": sorted({
                (r.get("choice") or {}).get("verb") for r in trace if (r.get("choice") or {}).get("verb")})},
            "cost": {"total_asks": call_count, "calls_per_step": rate(call_count, len(trace)),
                     "total_asks_per_step": rate(call_count, len(trace)),
                     "uncached_cloud_successful_asks": cloud_calls,
                     "uncached_cloud_asks_per_step": rate(cloud_calls, len(trace)) if cloud_calls is not None else None,
                     "cache_hits": cache_hits, "cache_hit_share": rate(cache_hits, len(selected_calls)) if cache_hits is not None else None,
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
            **_e4_metrics(run, trace, truth_rows, from_step), **_p3b_metrics(trace, truth_rows), **p3,
            **spot_metrics, "false_walls": false_walls,
            "probe_unmeasured_world_steps": sum(r.get("screen") == "world" and
                (r.get("probe") or {}).get("presses", 0) > 0 for r in trace),
            "no_effect_streak_max": _no_effect_streak(trace) if (run / "trace.jsonl").exists() and trace else None}
    if repredict:
        result.update(_repredict(run, trace, hide_memory))
    world_maps = {map_id(actual["map"]) for row in trace if row.get("screen") == "world" and
                  not (row.get("probe") or {}).get("presses", 0)
                  if (actual := truth.get(row.get("step"), {})).get("map") is not None}
    result["health"] = _health(result, world_maps)
    result["from_step"] = from_step
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    flags, paths, from_step = set(), [], None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--from-step":
            i += 1
            try:
                from_step = int(args[i])
                if from_step < 0: raise ValueError
            except (IndexError, ValueError):
                raise SystemExit("--from-step requires a non-negative integer")
        elif arg.startswith("--"):
            flags.add(arg)
        else:
            paths.append(arg)
        i += 1
    if len(paths) != 1 or flags - {"--repredict", "--hide-memory", "--no-write", "--health"} or ("--hide-memory" in flags and "--repredict" not in flags):
        raise SystemExit("usage: eval_run.py runs/<id> [--repredict [--hide-memory]] [--no-write] [--health] [--from-step N]")
    result = evaluate(paths[0], repredict="--repredict" in flags, hide_memory="--hide-memory" in flags,
                      from_step=from_step)
    if "--no-write" not in flags:
        (Path(paths[0]) / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if "--health" in flags:
        print("| Field | Value | Green |")
        print("|---|---:|:---:|")
        for row in result["health"]:
            value = json.dumps(row["value"], ensure_ascii=False) if isinstance(row["value"], (dict, list)) else row["value"]
            print(f"| {row['field']} | {value if value is not None else 'unknown'} | "
                  f"{str(row['green']).lower() if row['green'] is not None else 'unknown'} |")
    else:
        print(json.dumps(result, indent=2))
