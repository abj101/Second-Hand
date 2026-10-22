"""Offline movement-failure audit over a recorded run."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.world import World

ROOT = Path(__file__).resolve().parents[1]
ACTOR_KINDS = {"npc", "character", "person", "player"}
DIRS = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _indexed(rows: list[dict], key: str = "step") -> dict[int, dict]:
    return {int(row[key]): row for row in rows if row.get(key) is not None}


def _intent(choice: dict) -> tuple[str, str]:
    parts = str(choice.get("intent") or "").split("|")
    target = parts[-1] if len(parts) > 2 else ""
    kind, _, value = target.partition(":")
    return kind, value


def _memory(root: Path, step: int) -> tuple[dict[str, dict[str, dict]], int]:
    checkpoints = sorted((root / "checkpoint").glob("[0-9]*"))
    candidates = [(int(p.name), p) for p in checkpoints if p.name.isdigit() and int(p.name) <= step]
    cp_step, cp = candidates[-1] if candidates else (0, root / "checkpoint" / "000000")
    state = {store: {r["id"]: r for r in _jsonl(cp / "memory" / f"{store}.jsonl")
                     if isinstance(r.get("id"), str)} for store in ("tiles", "entities", "seen")}
    for store, stamp_key in (("tiles", "step"), ("entities", "last"), ("seen", "step")):
        latest: dict[str, tuple[int, int, dict]] = {}
        for index, row in enumerate(_jsonl(root / "memory" / f"{store}.jsonl")):
            stamp = row.get(stamp_key, row.get("step", row.get("first")))
            if not isinstance(row.get("id"), str) or not isinstance(stamp, int) or not cp_step < stamp <= step:
                continue
            candidate = (stamp, index, row)
            if row["id"] not in latest or candidate[:2] >= latest[row["id"]][:2]:
                latest[row["id"]] = candidate
        state[store].update({ident: item[2] for ident, item in latest.items()})
    return state, cp_step


def _offsets(trace: list[dict], truth: dict[int, dict]) -> dict[tuple[str, int], dict]:
    samples: dict[tuple[str, int], Counter] = {}
    for row in trace:
        step, tile = row.get("step"), row.get("tile")
        before = truth.get(int(step)) if step is not None else None
        after = truth.get(int(step) + 1) if step is not None else None
        if row.get("screen") != "world" or not before or not after or before.get("map") != after.get("map") or not isinstance(tile, list) or len(tile) != 2:
            continue
        if not all(isinstance(after.get(k), int) for k in ("map", "x", "y")):
            continue
        place = row.get("next_place") or row.get("place")
        if not isinstance(place, str):
            continue
        key = (place, after["map"])
        samples.setdefault(key, Counter())[(tile[0] - after["x"], tile[1] - after["y"])] += 1
    out = {}
    for key, counts in samples.items():
        offset, n = counts.most_common(1)[0]
        out[key] = {"offset": list(offset), "samples": sum(counts.values()), "matching": n,
                    "confidence": n / sum(counts.values())}
    return out


def _actor_sprites(after: dict, blocked_ram: list[int] | None) -> list[dict]:
    if blocked_ram is None or not isinstance(after.get("sprites"), list):
        return []
    sprites = [s for s in after["sprites"] if isinstance(s, dict) and isinstance(s.get("x"), int) and isinstance(s.get("y"), int)]
    player = next((s for s in sprites if s.get("slot") == 0), None)
    if not player:
        return []
    found = []
    for sprite in sprites:
        if sprite.get("slot") == 0:
            continue
        relative = [round((sprite["x"] - player["x"]) / 16), round((sprite["y"] - player["y"]) / 16)]
        cell = [after["x"] + relative[0], after["y"] + relative[1]]
        if cell == blocked_ram:
            found.append({"slot": sprite.get("slot"), "cell": cell, "relative_to_player": relative,
                          "player_anchor_slot": 0})
    return found


def _entity_evidence(rows: dict[str, dict], place: str,
                     cell: list[int] | None) -> tuple[list[dict], list[dict]]:
    if cell is None:
        return [], []
    found = []
    for row in rows.values():
        if row.get("place") != place or row.get("tile") != cell:
            continue
        kinds = {str(row.get("kind", "")).casefold(), *(str(k).casefold() for k in row.get("kinds", []))}
        evidence = {"id": row.get("id"), "name": row.get("name"), "kind": row.get("kind"), "kinds": sorted(kinds)}
        found.append((bool(kinds & ACTOR_KINDS), evidence))
    return ([e for actor, e in found if actor], [e for actor, e in found if not actor])


def _classify(cell: list[int] | None, place: str, failure: dict, tiles: dict[str, dict],
              entities: dict[str, dict], ram_actors: list[dict]) -> tuple[str, dict]:
    tile = next((r for r in tiles.values() if r.get("place") == place and [r.get("x"), r.get("y")] == cell), None) if cell is not None else None
    actor_entities, static_entities = _entity_evidence(entities, place, cell)
    evidence = {"tile": tile, "static_entities": static_entities, "actor_entities": actor_entities,
                "ram_actors": ram_actors}
    if cell is None:
        return ("crossing" if failure.get("stage") == "crossing" else "other"), evidence
    if tile is None:
        return "unknown_cell", evidence
    if static_entities:
        return "static_thing", evidence
    if actor_entities or ram_actors:
        return "actor", evidence
    if tile.get("floor") is True:
        return "known_floor", evidence
    if failure.get("stage") == "crossing":
        return "crossing", evidence
    return "other", evidence


def _target_kind(choice: dict) -> tuple[str, str]:
    kind, target = _intent(choice)
    if kind in {"exit", "explore", "thing", "place"}:
        return kind, target
    return "other", target


class _MemoryView:
    def __init__(self, state: dict[str, dict[str, dict]]):
        self.state = state

    def rows(self, store: str) -> list[dict]:
        return list(self.state.get(store, {}).values())

    def get(self, store: str, ident: str) -> dict | None:
        return self.state.get(store, {}).get(ident)


def _explore_target(root: Path, row: dict, truth: dict[int, dict], offsets: dict,
                    step: int) -> tuple[list[int] | None, list[int] | None, str | None]:
    kind, suffix = _intent(row.get("choice") or {})
    direction = suffix.rpartition(":")[2].upper() if kind == "explore" else ""
    before = truth.get(step, {})
    offset = offsets.get((row.get("place"), before.get("map")))
    if direction not in DIRS or not offset or not all(isinstance(before.get(k), int) for k in ("x", "y")):
        return None, None, "missing direction, RAM position, or place/map offset"
    start = [before["x"] + offset["offset"][0], before["y"] + offset["offset"][1]]
    state, _ = _memory(root, step - 1)
    world = World(_MemoryView(state), 16)
    world.pos, world.current_place, world.size = tuple(start), row.get("place"), (160, 144)
    actor_rows = [e for e in state["entities"].values()
                  if e.get("place") == row.get("place") and isinstance(e.get("tile"), list)
                  and {str(e.get("kind", "")).casefold(), *(str(k).casefold() for k in e.get("kinds", []))} & ACTOR_KINDS]
    world.actor_tiles = frozenset(tuple(e["tile"]) for e in actor_rows)
    target = (world.explore_dirs(row.get("place") or {}).get(direction) or {}).get("to")
    if target is None:
        return None, None, f"no frontier offered for {direction} in reconstructed memory"
    agent_target = list(target)
    ram_target = [agent_target[0] - offset["offset"][0], agent_target[1] - offset["offset"][1]]
    return agent_target, ram_target, None


def _ledger_hash() -> str | None:
    path = ROOT / "runs" / "e3_budget.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def audit(run: str, start: int | None = None, end: int | None = None, *, run_root: Path | None = None) -> dict:
    ledger_before = _ledger_hash()
    root = Path(run_root) if run_root is not None else ROOT / "runs" / run
    if not root.is_dir() or (run_root is None and ROOT / "runs" not in root.resolve().parents):
        raise ValueError(f"run not found under runs/: {run}")
    trace = _jsonl(root / "trace.jsonl")
    truth_rows = _jsonl(root / "truth.jsonl")
    truth = _indexed(truth_rows)
    presses: dict[int, list[dict]] = {}
    for row in _jsonl(root / "presses.jsonl"):
        if isinstance(row.get("step"), int):
            presses.setdefault(row["step"], []).append(row)
    offsets = _offsets(trace, truth)
    failed = []
    explores = []
    movement = [r for r in trace if r.get("screen") == "world" and (r.get("choice") or {}).get("verb") == "approach"
                and (start is None or r.get("step", 0) >= start) and (end is None or r.get("step", 0) <= end)]
    previous_target, previous_step, streak = None, None, 0
    for row in movement:
        step = int(row.get("step", 0))
        choice = row.get("choice") or {}
        kind, target = _target_kind(choice)
        if kind == "explore":
            frontier_agent, frontier_ram, frontier_reason = _explore_target(root, row, truth, offsets, step)
            before = truth.get(step, {})
            seen_later = (any(t.get("step", 0) > step and t.get("map") == before.get("map")
                              and (t.get("x"), t.get("y")) == tuple(frontier_ram)
                              for t in truth_rows) if frontier_ram else None)
            explores.append({"step": step, "target_kind": kind, "direction": target.rpartition(":")[2].upper(),
                             "frontier_agent": frontier_agent, "frontier_ram": frontier_ram,
                             "observed_later": seen_later,
                             "frontier_reason": frontier_reason})
        if row.get("execution") == "done":
            previous_target, previous_step, streak = None, step, 0
            continue
        skill = row.get("skill") or {}
        failure = skill.get("failure") or {}
        direction = str(failure.get("direction", "")).upper()
        direction_source = "skill.failure.direction" if direction in DIRS else None
        press_rows = presses.get(step, [])
        keys = [key for p in press_rows for key in (p.get("keys") or [])]
        if direction not in DIRS and failure.get("stage") == "crossing":
            direction = next((str(key).upper() for key in reversed(keys) if str(key).upper() in DIRS), "")
            if direction:
                direction_source = "last directional presses key (inferred)"
        delta = DIRS.get(direction)
        before, after = truth.get(step, {}), truth.get(step + 1, {})
        same_map = before.get("map") == after.get("map") and isinstance(before.get("map"), int)
        after_place = row.get("next_place") or row.get("place")
        offset = offsets.get((after_place, after.get("map")))
        ram_after = [after.get("x"), after.get("y")] if same_map and all(isinstance(after.get(k), int) for k in ("x", "y")) else None
        blocked_ram = ([ram_after[0] + delta[0], ram_after[1] + delta[1]] if ram_after and delta else None)
        blocked_agent = ([blocked_ram[0] + offset["offset"][0], blocked_ram[1] + offset["offset"][1]]
                         if blocked_ram and offset else None)
        failure_tile = failure.get("tile")
        recorded_after = row.get("tile") if isinstance(row.get("tile"), list) else None
        if blocked_agent and recorded_after and offset:
            expected = [after["x"] + offset["offset"][0], after["y"] + offset["offset"][1]]
            trace_position_matches = expected == recorded_after
        else:
            trace_position_matches = None
        ram_actors = _actor_sprites(after, blocked_ram)
        state, cp_step = _memory(root, step)
        cls, evidence = _classify(blocked_agent, str(row.get("place", "")), failure,
                                  state["tiles"], state["entities"], ram_actors)
        if blocked_agent is None and failure.get("stage") == "crossing":
            cls = "crossing"
        press_rows = presses.get(step, [])
        keys = [key for p in press_rows for key in (p.get("keys") or [])]
        intent = str(choice.get("intent") or "")
        if intent == previous_target and previous_step == step - 1:
            streak += 1
        else:
            streak = 1
        previous_target, previous_step = intent, step
        failed.append({"step": step, "intent": intent, "target_kind": kind, "target": target,
                       "class": cls, "blocked_agent": blocked_agent, "blocked_ram": blocked_ram,
                       "source_agent_after": recorded_after, "source_ram_after": ram_after,
                       "failure_source_tile": failure_tile, "direction_source": direction_source,
                       "trace_position_matches_truth_plus_offset": trace_position_matches,
                       "reason": skill.get("reason", ""), "failure_stage": failure.get("stage"),
                       "direction": direction or None, "route_goal": None,
                       "target_evidence": {"intent_target": target, "choice_target": choice.get("target"), "choice_at": choice.get("at")},
                       "memory_checkpoint": cp_step, "memory_source": "checkpoint snapshot + stamped journal updates",
                       "memory_limit": "a later mutation can hide an earlier value when no checkpoint captured it",
                       "mode_offset": offset, "offset_confidence": offset.get("confidence") if offset else None,
                       "press_events": len(press_rows), "key_entries": len(keys), "keys": keys,
                       "skill_inputs": skill.get("inputs"), "same_target_failure_streak": streak,
                       "evidence": evidence})
    classes, targets = {}, {}
    for key, group in (("class", failed), ("target_kind", failed)):
        counts = {}
        for row in group:
            name = row[key]
            item = counts.setdefault(name, {"failed_rows": 0, "wasted_press_events": 0, "wasted_key_entries": 0})
            item["failed_rows"] += 1
            item["wasted_press_events"] += row["press_events"]
            item["wasted_key_entries"] += row["key_entries"]
        if key == "class": classes = counts
        else: targets = counts
    ledger_after = _ledger_hash()
    return {"run": run, "from": start, "to": end, "movement_rows": len(movement),
            "failed_rows": len(failed), "rows": failed, "class_counts": classes,
            "target_counts": targets, "explores": explores,
            "offsets": {f"{place}|{map_id}": value for (place, map_id), value in offsets.items()},
            "memory_note": "Memory is reconstructed from the nearest earlier checkpoint plus step-stamped journal updates; a later mutation can hide an earlier value when no checkpoint captured it.",
            "requests": 0, "ledger_unchanged": ledger_before == ledger_after,
            "ledger_sha256_before": ledger_before, "ledger_sha256_after": ledger_after}


def _print(report: dict) -> None:
    print("step | intent | class | blocked cell (agent) | blocked cell (RAM) | reason")
    for row in report["rows"]:
        print(f"{row['step']} | {row['intent']} | {row['class']} | {row['blocked_agent']} | {row['blocked_ram']} | {row['reason']}")
    print("\nclasses (failed rows / wasted key entries):")
    for name, counts in sorted(report["class_counts"].items()):
        print(f"{name} | {counts['failed_rows']} | {counts['wasted_key_entries']}")
    print("\ntarget kinds (failed rows / wasted key entries):")
    for name, counts in sorted(report["target_counts"].items()):
        print(f"{name} | {counts['failed_rows']} | {counts['wasted_key_entries']}")
    print("\nexplore frontiers:")
    for row in report["explores"]:
        print(f"{row['step']} | {row['frontier_ram']} | observed later={row['observed_later']} | {row['frontier_reason'] or ''}")
    print(f"\nrequests={report['requests']}; ledger unchanged={report['ledger_unchanged']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("--from", dest="start", type=int)
    parser.add_argument("--to", dest="end", type=int)
    parser.add_argument("--json", dest="json_path", help="also write structured output under .tmp/")
    args = parser.parse_args()
    report = audit(args.run, args.start, args.end)
    _print(report)
    if args.json_path:
        path = Path(args.json_path)
        scratch = (ROOT / ".tmp").resolve()
        path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(scratch):
            parser.error("--json output must be under .tmp/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
