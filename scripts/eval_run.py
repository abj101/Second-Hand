"""Summarize one agent run and evaluate the Pokémon M1 gate."""
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TACTICAL = re.compile(
    r"\(\s*-?\d+\s*,\s*-?\d+\s*\)|"
    r"\b(?:door|exit|stairs|mat|rug|gate)\w*\s*#\d|"
    r"\b(?:press|move|walk|go|step|head)\s+(?:up|down|left|right|north|south|east|west)\b",
    re.I,
)

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
        ("drift_fix_off_world", result.get("drift_fix_off_world"), 0),
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
    plans = [(i, r) for i, r in enumerate(trace)
             if isinstance(r.get("plan"), dict) and r["plan"]]
    if not plans: plans = [(i, r) for i, r in enumerate(trace) if r.get("wake")]
    notebook = {}
    try: notebook = json.loads((run / "memory" / "notebook.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): pass
    quests = notebook.get("quests") if isinstance(notebook, dict) else None
    if isinstance(quests, list):
        done_plans = {step for q in quests if isinstance(q, dict) and q.get("status") in {"done", "closed"}
                      for step in (q.get("plan_step"), q.get("opened"), q.get("closed_step"), q.get("closed"))
                      if isinstance(step, int)}
        history = _history(run)
        for row in history:
            if row.get("op") != "quests": continue
            value = row.get("value", [])
            if isinstance(value, dict): value = [value]
            if isinstance(value, list):
                done_plans.update(step for q in value if isinstance(q, dict) and q.get("status") in {"done", "closed"}
                                  for step in (q.get("plan_step"), q.get("opened"), q.get("closed"))
                                  if isinstance(step, int))
    else:
        done_plans = {t.get("plan_step") for t in replay(run / "memory" / "tasks.jsonl")
                      if t.get("status") == "done" and isinstance(t.get("plan_step"), int)}
    completed = sum(row.get("step") in done_plans for _, row in plans)
    return {"doors_verified": len(verified - {None}),
            "patterns_promoted": sum(r.get("state") == "promoted" for r in replay(run / "memory" / "patterns.jsonl")),
            "battles": battles, "moves_selected": moves,
            "repeat_sequences": {"length": 2, "count": sum(n > 1 for n in repeats.values())},
            "reflection_usefulness": {"plan_calls": len(plans), "goals_completed": completed}}


def _history(run):
    path = run / "memory" / "notebook.history"
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError):
        return []


def _lifecycle(run):
    """Replay trigger rules and summarize notebook history without creating a model."""
    from agent.tasks import Stuck

    run = Path(run)
    trace = sorted(read(run / "trace.jsonl"), key=lambda row: row.get("step", 0))
    history = _history(run)
    notebook = {}
    try: notebook = json.loads((run / "memory" / "notebook.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): pass
    notebook = notebook if isinstance(notebook, dict) else {}
    facts = notebook.get("facts", {})
    facts = facts if isinstance(facts, dict) else {}

    def quest_key(q, fallback=None):
        return str(q.get("key") or q.get("id") or fallback or "") if isinstance(q, dict) else ""

    def is_heard(q):
        if not isinstance(q, dict): return False
        if q.get("source") == "heard": return True
        why = q.get("why", [])
        refs = re.findall(r"[\w.-]+", " ".join(map(str, why)) if isinstance(why, list) else str(why))
        return any(isinstance(facts.get(ref), dict) and facts[ref].get("source") == "heard" for ref in refs)

    # A history log row is the notebook's actual note() shape. Its text is an
    # approximation of a flushed dialogue event; a trace trigger is explicit.
    def is_dialogue_log(row):
        text = str(row.get("text", "")).strip()
        return row.get("op") == "log" and bool(re.match(r"^(?:text\s+at\s+.+|.+\s+at\s+.+):\s*\".*\"\s*$", text, re.I))

    text_steps = {r.get("step") for r in history if is_dialogue_log(r) and isinstance(r.get("step"), int)}
    closed_steps = {r.get("step") for r in history if r.get("op") == "check" and isinstance(r.get("step"), int)}
    # v1 set-goal rows store the identifier in row.key; v2 stores full snapshots.
    for r in history:
        value = r.get("value")
        records = value if isinstance(value, list) else [value]
        if r.get("op") == "set" and r.get("section") == "goals": records = [value]
        if r.get("op") in {"quests", "set", "migrate_quest"} and any(
                isinstance(q, dict) and q.get("status") in {"closed", "done", "completed", "dropped"}
                for q in records):
            if isinstance(r.get("step"), int): closed_steps.add(r["step"])

    calls, marked_text, seen_world = [], set(), []
    pending = {}  # kind -> True: the loop's event queue (T5 and T4 wait at most once); consumed whole by one call
    log_events = sorted(s for s in text_steps if isinstance(s, int))
    next_log = 0
    last_call_step = None
    stuck = Stuck()
    for row in trace:
        step, screen = row.get("step"), row.get("screen")
        plan_row = row.get("plan") if isinstance(row.get("plan"), dict) else {}
        trigger = str(plan_row.get("trigger") or row.get("trigger") or "")
        explicit_text = trigger.casefold().startswith("new game text")
        while next_log < len(log_events) and isinstance(step, int) and log_events[next_log] <= step:
            log_step = log_events[next_log]
            next_log += 1
            if log_step not in marked_text:
                marked_text.add(log_step)
                pending["T1"] = True  # text is never progress (QR.1)
        if explicit_text and step not in marked_text:
            marked_text.add(step)
            pending["T1"] = True
        if step in closed_steps or str(row.get("quest_event", "")).casefold() == "closed":
            stuck.progress()
            pending["T3"] = True

        place = row.get("place")
        if screen == "world" and place and place not in seen_world:
            seen_world.append(place)
            if len(seen_world) > 1: pending["T2"] = True  # the first place is where the run starts, not an arrival
        replay_row = dict(row)
        if not replay_row.get("verified_edge"):
            outcome, basis = row.get("outcome") or {}, (row.get("outcome") or {}).get("basis") or {}
            if "place_changes" in outcome.get("effects", []) and basis.get("place_changes") == "pixel":
                replay_row["verified_edge"] = True
        if stuck.update(replay_row) == "no_progress": pending["T4"] = True
        if step == 1: pending["T5"] = True

        floor_ok = last_call_step is None or not isinstance(step, int) or step - last_call_step >= 3
        ready = [k for k in ("T3", "T1", "T2", "T5", "T4") if k in pending and (k != "T1" or screen == "world")]
        if isinstance(step, int) and ready and floor_ok and step != last_call_step:
            names = {"T3": "T3 quest closed", "T1": "T1 new game text", "T2": "T2 first world place", "T5": "T5 empty first step", "T4": "T4 no progress"}
            calls.append({"step": step, "trigger": names[ready[0]], "kinds": [k for k in ("T3", "T1", "T2", "T5", "T4") if k in pending]})
            last_call_step = step
            pending.clear()

    counts = Counter(str(row.get("step")) for row in trace
                     if isinstance(row.get("plan"), dict) and row.get("step") is not None)
    plan_calls = {step: n for step, n in sorted(counts.items(), key=lambda x: int(x[0]) if x[0].isdigit() else -1)}

    opened, closed, parked, dropped_hist = set(), set(), set(), set()
    for r in history:
        op = r.get("op")
        value = r.get("quest") if op == "migrate_quest" else r.get("value")
        records = value if isinstance(value, list) else [value]
        if op == "set" and r.get("section") == "goals":
            records = [value]
        elif op not in {"quests", "migrate_quest", "set"}: records = []  # fact/belief rows are not quests
        for q in records:
            if not is_heard(q): continue
            key = quest_key(q, r.get("key") if op == "set" else None)
            if not key: continue
            opened.add(key)
            status = str(q.get("status", "active")).casefold()
            if status in {"closed", "done", "completed"}: closed.add(key)
            elif status in {"parked", "paused", "suspended"}: parked.add(key)
            elif status == "dropped": dropped_hist.add(key)
        if op == "check" and r.get("key"):
            key = str(r["key"])
            if key in opened: closed.add(key)
    final_quests = notebook.get("quests")
    if isinstance(final_quests, list):
        final_quest_rows = [(q, None) for q in final_quests]
    elif isinstance(notebook.get("goals"), dict):
        final_quest_rows = [(q, key) for key, q in notebook["goals"].items()]
    else:
        final_quest_rows = []
    final_quest_states = {}
    for q, fallback_key in final_quest_rows:
        key = quest_key(q, fallback_key)
        if is_heard(q) or key in opened:
            if key:
                opened.add(key)
                final_quest_states[key] = str(q.get("status", "active")).casefold()
    still_open = {k for k, status in final_quest_states.items()
                  if status not in {"closed", "done", "completed", "dropped", "parked", "paused", "suspended"}}
    final_statuses = {quest_key(q, fallback): str(q.get("status", "active")).casefold()
                      for q, fallback in final_quest_rows if isinstance(q, dict) and quest_key(q, fallback)}
    final_open = {k for k, status in final_statuses.items()
                  if status not in {"closed", "done", "completed", "dropped", "parked", "paused", "suspended"}}
    final_closed = {k for k, status in final_statuses.items() if status in {"closed", "done", "completed"}}
    dropped = {k for k, status in final_quest_states.items() if status == "dropped"}
    closed |= {k for k, status in final_quest_states.items() if status in {"closed", "done", "completed"}}
    parked |= {k for k, status in final_quest_states.items() if status in {"parked", "paused", "suspended"}}
    dropped |= dropped_hist
    still_open -= closed | parked | dropped
    dropped -= closed
    lost = opened - closed - parked - still_open - dropped  # a heard quest dropped with a reference is dropped, not lost
    parked -= closed

    tactical = re.compile(r"#\d|\(\s*-?\d+\s*,\s*-?\d+\s*\)|^(?:press|move|walk|go)\s+(?:up|down|left|right)\b", re.I)
    labels = notebook.get("labels", {})
    button_names = list(labels) if isinstance(labels, dict) else list(labels) if isinstance(labels, list) else []
    button_re = re.compile(r"\b(?:" + "|".join(re.escape(str(b)) for b in button_names if str(b)) + r")\b", re.I) if button_names else None
    quests = final_quests if isinstance(final_quests, list) else [dict(v, key=k) for k, v in notebook.get("goals", {}).items()
                                                                    if isinstance(v, dict)] if isinstance(notebook.get("goals"), dict) else []
    matches = sorted({quest_key(q) for q in quests if isinstance(q, dict) and quest_key(q) and
                      (tactical.search(str(q.get("text", ""))) or button_re and button_re.search(str(q.get("text", ""))))})

    created = {}
    for r in history:
        value = r.get("quest") if r.get("op") == "migrate_quest" else r.get("value")
        for q in value if isinstance(value, list) else [value]:
            if isinstance(q, dict) and r.get("op") in {"quests", "migrate_quest"} and quest_key(q): created[quest_key(q)] = str(q.get("text", ""))
    created.update({quest_key(q): str(q.get("text", "")) for q in quests if isinstance(q, dict) and quest_key(q)})
    door = sorted(k for k, text in created.items() if _TACTICAL.search(text) or button_re and button_re.search(text))
    per_door = {"count": len(door), "of": len(created), "keys": door}

    # A quest is a false closure only when a history snapshot marks it closed
    # and the final notebook still leaves that same quest open.
    closed_in_history = set()
    note_closed = set()
    notes = notebook.get("notes", [])
    note_rows = [(str(n.get("text", "")), n.get("step")) for n in notes if isinstance(n, dict)]
    note_rows += [(str((r.get("value") or {}).get("text", "")), r.get("step"))
                  for r in history if r.get("op") == "planner_note" and isinstance(r.get("value"), dict)]
    for r in history:
        value = r.get("quest") if r.get("op") == "migrate_quest" else r.get("value")
        for q in value if isinstance(value, list) else [value]:
            if isinstance(q, dict) and str(q.get("status", "")).casefold() in {"closed", "done", "completed"}:
                key = quest_key(q, r.get("key") if r.get("op") == "set" else None)
                if key: closed_in_history.add(key)
    for note, _ in note_rows:
        if re.search(r"\b(?:clos(?:e|ed)|done|complet(?:e|ed)|finish(?:ed)?|fulfill(?:ed)?|drop(?:ped)?|obsolete)\b", note, re.I):
            note_closed.update(key for key in final_statuses if re.search(rf"\b{re.escape(key)}\b", note, re.I))
    said_closed = closed_in_history | note_closed
    said_closed_open = sorted(said_closed & final_open)
    actually_closed = sorted(final_closed)

    # S14 requires explicit add operations and an explicit target (or check arg).
    def target(q):
        if not isinstance(q, dict): return None
        check = q.get("check") if isinstance(q.get("check"), dict) else {}
        value = q.get("target") or check.get("arg")
        return str(value).strip().casefold() if value else None

    targets_by_key, closed_targets, readds = {}, set(), []
    add_rows = [r for r in history if r.get("op") == "add"]
    for r in sorted(history, key=lambda row: row.get("step", 0)):
        value = r.get("quest") if r.get("op") == "migrate_quest" else r.get("value")
        quests = value if isinstance(value, list) else [value]
        for q in quests:
            if not isinstance(q, dict): continue
            key, qtarget = quest_key(q), target(q)
            if key and qtarget: targets_by_key[key] = qtarget
            status = str(q.get("status", "")).casefold()
            if status in {"closed", "done", "completed"} and qtarget:
                closed_targets.add(qtarget)
        if r.get("op") == "check" and r.get("key") in targets_by_key:
            closed_targets.add(targets_by_key[r["key"]])
        if r.get("op") == "add":
            q = r.get("quest") if isinstance(r.get("quest"), dict) else value
            qtarget = target(q)
            if qtarget and qtarget in closed_targets:
                readds.append({"step": r.get("step"), "target": qtarget})
    final_target_rows = [q for q, _ in final_quest_rows]
    target_rows = sum(target(q) is not None for q in final_target_rows)
    history_targets = 0
    for r in history:
        value = r.get("quest") if r.get("op") == "migrate_quest" else r.get("value")
        for q in value if isinstance(value, list) else [value]:
            history_targets += isinstance(q, dict) and target(q) is not None

    belief_revisions = []
    seen_revisions = set()
    for r in history:
        if r.get("op") not in {"belief", "migrate_fact"}: continue
        value = r.get("fact") if r.get("op") == "migrate_fact" else r.get("value")
        value = value if isinstance(value, dict) else {}
        key = r.get("key") or value.get("key")
        for rev in value.get("history", []) if isinstance(value.get("history"), list) else []:
            if not isinstance(rev, dict): continue
            item = (str(key or ""), rev.get("step"), rev.get("from_text"), rev.get("reason"))
            if key and item not in seen_revisions:
                seen_revisions.add(item)
                belief_revisions.append({"key": str(key), "step": rev.get("step"),
                                         "from_text": rev.get("from_text"), "reason": rev.get("reason", "")})
    # v1 recordings may have no revision history; retain value transitions as a fallback.
    if not belief_revisions:
        latest = {}
        for r in history:
            if r.get("op") not in {"belief", "migrate_fact"}: continue
            value = r.get("fact") if r.get("op") == "migrate_fact" else r.get("value")
            value = value if isinstance(value, dict) else {}
            key = r.get("key") or value.get("key")
            if key and key in latest and latest[key] != value:
                belief_revisions.append({"key": str(key), "step": r.get("step"), "reason": value.get("reason", "")})
            if key: latest[key] = value

    state_ops = {"quests", "belief", "fact", "check", "chapter"}
    changed_steps = {str(r.get("step")) for r in history if r.get("op") in state_ops}
    stall_no_change = 0
    for row in trace:
        p = row.get("plan")
        if not isinstance(p, dict) or not (re.search(r"no.progress|\bT4\b", str(p.get("trigger", "")), re.I) or any(isinstance(e, dict) and e.get("kind") == "T4" for e in p.get("events") or [])): continue
        if isinstance(p.get("ops"), list):
            unchanged = not any(isinstance(r, dict) and r.get("result") == "ok" for r in p["ops"])
        elif "quests_changed" in p or "beliefs_changed" in p:
            unchanged = not p.get("quests_changed") and not p.get("beliefs_changed")
        else:
            unchanged = str(row.get("step")) not in changed_steps
        stall_no_change += bool(unchanged)
    truth = {t.get("step"): t for t in read(run / "truth.jsonl")}
    purity, closures = None, None
    if truth:  # trace step S is the action; its after-state is truth step S+1
        maps = defaultdict(set)
        for row in trace:
            place = row.get("next_place") or row.get("place")
            actual = truth.get((row.get("step") or 0) + 1) or {}
            if row.get("screen") == "world" and place and actual.get("map") is not None: maps[place].add(map_id(actual["map"]))
        mixed = {place: sorted(ids) for place, ids in sorted(maps.items()) if len(ids) > 1}
        purity = {"places": len(maps), "impure": len(mixed), "purity": rate(len(maps) - len(mixed), len(maps)), "spanning": mixed}
        closures = [{"key": r.get("key"), "step": r.get("step"), "map": map_id(t["map"]), "check": str(r.get("evidence", ""))}
                    for r in history if r.get("op") == "check" and str(r.get("evidence", "")).startswith("check reach")
                    and isinstance(r.get("step"), int) and (t := truth.get(r["step"] + 1)) and t.get("map") is not None]
    text_source = "trace plan trigger metadata + notebook.history note() dialogue-log approximation"
    return {"trigger_replay": {"calls": calls, "count": len(calls), "text_event_steps": sorted(s for s in marked_text if isinstance(s, int)),
                               "text_event_source": text_source},
            "truth_join": {"place_purity": purity, "reach_closure_maps": closures},
            "history_metrics": {"plan_calls_per_step": plan_calls,
                                "heard_leads": {"opened": len(opened), "closed": len(closed),
                                                "parked": len(parked), "still_open": len(still_open),
                                                "dropped": len(dropped), "silently_lost": len(lost)},
                                "quests_per_door": per_door,
                                "said_closed_but_open": {"count": len(said_closed_open),
                                                         "keys": said_closed_open,
                                                         "actually_closed": len(actually_closed)},
                                "finished_target_readds": {"count": len(readds), "rows": readds,
                                                           "available": bool(add_rows and (target_rows or history_targets))},
                                "rule4_quest_matches": matches, "belief_revisions": belief_revisions,
                                "stall_calls_changed_nothing": stall_no_change}}


def _scorecard(run, trace, truth_rows, entity_rows, calls, lifecycle):
    """Offline RCR scorecard. Null values mean the recorded run lacks evidence."""
    from agent.memory import normalize_kind
    from agent.world import ACTOR_KINDS

    truth = {r.get("step"): r for r in truth_rows}
    actor_evidence = {str(r["id"]) for r in entity_rows if r.get("id") and
                      normalize_kind(r.get("kind")) in ACTOR_KINDS}
    actor_ids, ambiguous_ids = defaultdict(set), defaultdict(set)
    actor_observations, sprite_rows, joined_frames, proximity_frames = Counter(), 0, 0, 0
    actor_ids_seen = set()
    for row in trace:
        actual = truth.get(row.get("step"), {})
        sprites = actual.get("sprites")
        if row.get("screen") != "world" or not isinstance(sprites, list) or not sprites:
            continue
        if any(isinstance(sprite, dict) and isinstance(sprite.get("slot"), int) and sprite["slot"] != 0
               for sprite in sprites):
            sprite_rows += 1
        matched = proximity_matched = False
        size = _frame_size(run, row)
        if not size or size[0] <= 0 or size[1] <= 0:
            continue
        for entity in row.get("entities") or []:
            if not isinstance(entity, dict) or not entity.get("id"):
                continue
            entity_id = str(entity["id"])
            is_actor = normalize_kind(entity.get("kind")) in ACTOR_KINDS or entity_id in actor_evidence
            if is_actor: actor_ids_seen.add(entity_id)
            at = entity.get("at")
            if not (isinstance(at, (list, tuple)) and len(at) == 2):
                continue
            try: point = (float(at[0]) * size[0], float(at[1]) * size[1])
            except (TypeError, ValueError): continue
            candidates = []
            for sprite in sprites:
                if not isinstance(sprite, dict): continue
                try:
                    slot = int(sprite["slot"])
                    xy = (float(sprite["x"]) + 8, float(sprite["y"]) + 8)
                except (KeyError, TypeError, ValueError): continue
                distance = max(abs(point[0] - xy[0]), abs(point[1] - xy[1]))
                if distance <= 12: candidates.append((distance, slot))
            if candidates:
                _, slot = min(candidates)
                if slot == 0 or actual.get("map") is None: continue
                sprite_key = (map_id(actual["map"]), slot)
                proximity_matched = True
                if is_actor:
                    actor_ids[sprite_key].add(entity_id)
                    actor_observations[sprite_key] += 1
                    matched = True
                else:
                    ambiguous_ids[sprite_key].add(entity_id)
        joined_frames += matched
        proximity_frames += proximity_matched
    s1_available = bool(actor_ids)
    matched_actor_ids = set().union(*actor_ids.values()) if actor_ids else set()
    s1 = {"available": s1_available,
          "ids_per_sprite": {f"{mid}:slot-{slot}": {"ids": sorted(ids), "count": len(ids),
                                                      "matched_observations": actor_observations[(mid, slot)]}
                             for (mid, slot), ids in sorted(actor_ids.items())} if s1_available else None,
          "ambiguous_proximity_ids_per_sprite": {
              f"{mid}:slot-{slot}": sorted(ids)
              for (mid, slot), ids in sorted(ambiguous_ids.items())} or None,
          "unmatched_actor_ids": sorted(actor_ids_seen - matched_actor_ids),
          "max_ids": max((len(ids) for ids in actor_ids.values()), default=None),
          "truth_sprite_rows": sprite_rows,
          "actor_matched_trace_rows": joined_frames,
          "proximity_matched_trace_rows": proximity_frames,
          "coverage": rate(proximity_frames, sprite_rows),
          "definition": "Same-step truth sprites joined to trace entities within 12 px Chebyshev distance. Only ACTOR_KINDS or IDs historically observed as actors count as confirmed; nearby other entities remain ambiguous. Truth slot 0 is excluded after nearest-sprite matching."}

    target_truth = sorted((r for r in truth_rows if map_id(r.get("map")) == "0x00" and
                           isinstance(r.get("map_h"), int) and r["map_h"] > 0 and
                           isinstance(r.get("map_w"), int) and r["map_w"] > 0),
                          key=lambda r: r.get("step", 0))[:30]
    first_target_step = target_truth[0].get("step") if target_truth else None
    last_target_step = target_truth[-1].get("step") if target_truth else None
    arrival_step = first_target_step - 1 if isinstance(first_target_step, int) else None
    incoming = next((r for r in trace if r.get("step") == arrival_step), None)
    target_place = ((incoming.get("next_place") or incoming.get("place")) if incoming else None)
    if not target_place and isinstance(first_target_step, int):
        first_row = next((r for r in trace if r.get("step") == first_target_step), None)
        target_place = (first_row.get("place") or first_row.get("next_place")) if first_row else None
    entities = replay_rows(entity_rows)
    minted = [r for r in entities if target_place and r.get("place") == target_place and
              isinstance(r.get("first"), int) and arrival_step <= r["first"] <= last_target_step]
    s2_available = bool(len(target_truth) == 30 and target_place and (run / "memory" / "entities.jsonl").exists())
    s2 = {"available": s2_available, "world_steps": len(target_truth) if s2_available else None,
          "records": len(minted) if s2_available else None,
          "target_place": target_place if s2_available else None,
          "birth_window": [arrival_step, last_target_step] if s2_available else None,
          "incoming_observation_included": bool(s2_available and incoming and incoming.get("next_place") == target_place),
          "definition": "Distinct entity IDs first seen in the place mapped to map 0, from the incoming action through the 30th valid map-0 truth row. Uses truth after-state S+1; map-0 rows without dimensions are excluded, and pending trace rows count."}

    duplicate_count, deduped = _entity_duplicates(entity_rows)
    same_tile = Counter()
    for row in deduped:
        tile = row.get("tile")
        if not (isinstance(tile, (list, tuple)) and len(tile) == 2): continue
        name = re.sub(r"(?:#\d+)+$", "", str(row.get("name", "")).casefold()).strip()
        if name and str(row.get("kind", "")).casefold() != "player":
            same_tile[(row.get("place"), name, tuple(tile))] += 1
    duplicate_groups = {key: count for key, count in same_tile.items() if count > 1}
    s3_available = any(isinstance(r.get("tile"), (list, tuple)) and len(r["tile"]) == 2 for r in deduped)
    s3 = {"available": s3_available,
          "duplicate_records": sum(n - 1 for n in duplicate_groups.values()) if s3_available else None,
          "duplicate_groups": len(duplicate_groups) if s3_available else None,
          "tiles": len({(key[0], key[2]) for key in duplicate_groups}) if s3_available else None,
          "entity_duplicate_proxy": duplicate_count if s3_available else None,
          "manual_reference": "MASTER §5 records 2 duplicate tiles ×2; this name/tile history proxy may differ from that inspected count.",
          "definition": "Final entity history proxy grouped by place, normalized name, and exact stored tile; repeated rows beyond the first are duplicates. This may differ from manually identified duplicate tiles."}

    history_metrics = lifecycle["history_metrics"]
    s5 = history_metrics["said_closed_but_open"]
    s5 = {"available": (run / "memory" / "notebook.history").exists() and
                       (run / "memory" / "notebook.json").exists(),
          **s5, "definition": "Explicit planner notes or history snapshots name a quest as closed/dropped, compared with all final notebook quest statuses. Note matches are a lower bound requiring the exact quest key."}

    truth_by_step = {r.get("step"): r for r in truth_rows}
    action_failures = [r for r in trace if r.get("execution") in {"failed", "blocked"} or
                       isinstance(r.get("skill"), dict) and r["skill"].get("status") in {"failed", "blocked"}]
    no_movement = []
    unknown_unsettled = 0
    for row in action_failures:
        before, after = truth_by_step.get(row.get("step")), truth_by_step.get((row.get("step") or 0) + 1)
        if before is None or after is None or any(before.get(k) is None or after.get(k) is None for k in ("map", "x", "y")):
            continue
        if (before["map"], before["x"], before["y"]) == (after["map"], after["x"], after["y"]):
            no_movement.append(row)
    for row in trace:
        failed = row.get("execution") in {"failed", "blocked"} or isinstance(row.get("skill"), dict) and row["skill"].get("status") in {"failed", "blocked"}
        if not failed and (row.get("observation_status") == "timed_out" or isinstance(row.get("skill"), dict) and row["skill"].get("settled") is False):
            before, after = truth_by_step.get(row.get("step")), truth_by_step.get((row.get("step") or 0) + 1)
            if before is not None and after is not None and all(before.get(k) is not None and after.get(k) is not None for k in ("map", "x", "y")) and (before["map"], before["x"], before["y"]) == (after["map"], after["x"], after["y"]):
                unknown_unsettled += 1
    truth_joined = [r for r in action_failures
                    if all(truth_by_step.get(step) is not None and
                           all(truth_by_step[step].get(k) is not None for k in ("map", "x", "y"))
                           for step in (r.get("step"), (r.get("step") or 0) + 1))]
    s6_available = bool(truth_rows and any("execution" in r or isinstance(r.get("skill"), dict) for r in trace) and
                        (not action_failures or truth_joined))
    s6 = {"available": s6_available,
          "action_failures": len(action_failures) if truth_rows else None,
          "truth_joined_failures": len(truth_joined) if s6_available else None,
          "real_failures_no_movement": len(no_movement) if s6_available else None,
          "not_scored_failed": sum(r.get("verdict") not in {"failed", "miss"} for r in no_movement) if s6_available else None,
          "unsettled_no_movement_unknown": unknown_unsettled,
          "steps": [r.get("step") for r in no_movement],
          "definition": "Action/skill failed or blocked, and truth map/x/y are unchanged from step S to S+1; verdict failed or legacy miss counts as scored failed. Unsettled no-movement rows are reported separately."}

    act_calls = [c for c in calls if c.get("role") == "act"]
    plan_calls = [c for c in calls if c.get("role") in {"plan", "planner"}]
    act_users = [len(str(c["user"])) for c in act_calls if isinstance(c.get("user"), str)]
    plan_users = [len(str(c["user"])) for c in plan_calls if isinstance(c.get("user"), str)]
    act_combined = [len(str(c.get("system", ""))) + len(str(c.get("user", ""))) for c in act_calls
                    if "system" in c or "user" in c]
    plan_combined = [len(str(c.get("system", ""))) + len(str(c.get("user", ""))) for c in plan_calls
                     if "system" in c or "user" in c]
    s8 = {"act_user_max_chars": max(act_users, default=None),
          "planner_user_max_chars": max(plan_users, default=None),
          "act_system_plus_user_max_chars": max(act_combined, default=None),
          "planner_system_plus_user_max_chars": max(plan_combined, default=None),
          "available": bool(act_users or plan_users),
          "definition": "Primary values count recorded user prompt text; combined system plus user maxima are also reported."}

    unsettled = [r for r in trace if r.get("observation_status") in {"pending", "timed_out", "unsettled"} or
                 isinstance(r.get("skill"), dict) and r["skill"].get("settled") is False]
    s11 = {"available": bool(trace), "rows": len(unsettled) if trace else None,
           "pending": sum(r.get("observation_status") == "pending" for r in unsettled) if trace else None,
           "timed_out": sum(r.get("observation_status") == "timed_out" for r in unsettled) if trace else None,
           "definition": "Trace rows pending, timed out, explicitly unsettled, or whose skill settlement flag is false."}
    s14 = history_metrics["finished_target_readds"]
    return {"S1_ids_per_real_npc": s1, "S2_pallet_records_first_30": s2,
            "S3_same_tile_duplicates": s3, "S5_false_closures": s5,
            "S6_failures_not_scored_failed": s6, "S8_prompt_max_chars": s8,
            "S11_unsettled_rows": s11, "S14_finished_target_readds": s14}


def evaluate(run, *, from_step=None):
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
        if key[1] and row.get("verdict") in ("miss", "surprise", "failed", "no_change"):
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
            "drift_fix_off_world": (sum(row.get("drift_fix") is not None and row.get("screen") != "world" for row in trace)
                                    if (run / "trace.jsonl").exists() else None),
            "probe_unmeasured_world_steps": sum(r.get("screen") == "world" and
                (r.get("probe") or {}).get("presses", 0) > 0 for r in trace),
            "no_effect_streak_max": _no_effect_streak(trace) if (run / "trace.jsonl").exists() and trace else None}
    lifecycle = _lifecycle(run)
    result["rcr_scorecard"] = _scorecard(run, full_trace, truth_rows, entity_rows,
                                        read(run / "calls.jsonl"), lifecycle)
    world_maps = {map_id(actual["map"]) for row in trace if row.get("screen") == "world" and
                  not (row.get("probe") or {}).get("presses", 0)
                  if (actual := truth.get(row.get("step"), {})).get("map") is not None}
    result["health"] = _health(result, world_maps)
    result["from_step"] = from_step
    return result


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "--lifecycle":
        print(json.dumps(_lifecycle(args[1]), indent=2, ensure_ascii=False))
        raise SystemExit(0)
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
    if len(paths) != 1 or flags - {"--no-write", "--health"}:
        raise SystemExit("usage: eval_run.py runs/<id> [--no-write] [--health] [--from-step N]")
    result = evaluate(paths[0], from_step=from_step)
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
