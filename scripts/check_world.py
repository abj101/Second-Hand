"""Read-only PyBoy checks for World Memory travel from checkpoint 94."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.env_pyboy import PyBoyEnv
from agent.memory import Memory, _base, normalize_kind
from agent.skills import Ctx
from agent.space import tile_of
from agent.world import ACTOR_KINDS, World
from scripts.eval_run import map_id, read as eval_read, replay
from scripts.truth_pokered import read

RUN = ROOT / "runs/m1"
CHECKPOINT = RUN / "checkpoint/000094"
SCRATCH = ROOT / ".tmp/w2"
VIEW_SCRATCH = ROOT / ".tmp/w3"
MAP_LABELS = {0x26: "2F", 0x25: "1F", 0x00: "Town"}


class StubModel:
    calls = 0

    def ask(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("World travel must not call the model")


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def setup(mode: str):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=f"{mode}-", dir=SCRATCH))
    shutil.copy2(CHECKPOINT / "pyboy.state", root / "pyboy.state")
    shutil.copy2(CHECKPOINT / "harness.json", root / "harness.json")
    shutil.copytree(CHECKPOINT / "memory", root / "memory")
    harness = json.loads((root / "harness.json").read_text(encoding="utf-8"))
    env = PyBoyEnv(state=str(root / "pyboy.state"))
    model = StubModel()
    ctx = Ctx(env, model, tile_px=harness["tile_px"], move_frames=harness["move_frames"])
    mem = Memory(root / "memory")
    world = World(mem, ctx.tile_px)
    world.restore(harness)
    place = harness["place"]
    perception = harness["perception"]
    world.current_place = place
    size = env.screenshot().size
    player = next((e for e in perception.get("entities", []) if e.get("kind", "").casefold() == "player"), None)
    world.pa = world._anchor(perception, size) if player else None
    tiles = {i: tile_of(e["at"], world.pa, world.pos, size, world.tile_px)
             for i, e in enumerate(perception.get("entities", []))
             if world.pa and e.get("kind", "").casefold() != "player" and isinstance(e.get("at"), list)}
    world.actor_tiles = frozenset(tiles[i] for i, e in enumerate(perception.get("entities", []))
                                  if i in tiles and normalize_kind(e.get("kind")) in ACTOR_KINDS)
    world.entities = [{"id": world.entity_ids.get(i), "name": e.get("name"), "kind": e.get("kind"), "at": e.get("at")}
                      for i, e in enumerate(perception.get("entities", []))]
    trace = rows(RUN / "trace.jsonl")
    truth = rows(RUN / "truth.jsonl")
    return root, env, model, ctx, mem, world, place, perception, harness, trace, truth


def view_check(memory_source=None) -> int:
    memory_source = Path(memory_source) if memory_source else CHECKPOINT / "memory"
    checkpoint = memory_source.parent
    VIEW_SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="view-", dir=VIEW_SCRATCH))
    shutil.copytree(memory_source, root / "memory")
    harness = json.loads((checkpoint / "harness.json").read_text(encoding="utf-8"))
    mem = Memory(root / "memory")
    world = World(mem, harness.get("tile_px"))
    world.restore(harness)
    place = harness["place"]
    world.current_place = place
    for row in mem.rows("places"):
        if row.get("state") != "merged": world._tiers({}, row["id"], {})
    view = world.view("", place)
    print(view)
    again = world.view("", place)
    labels = [world.place_label(p["id"]) for p in mem.rows("places") if p.get("state") != "merged"]
    resolved = world.resolve("world [p_world]", {}, place)
    deterministic = view == again
    source_is_default = memory_source.resolve() == (CHECKPOINT / "memory").resolve()
    target_section = view.partition("Place: world [p_world]")[2].split("\nPlace:", 1)[0]
    target_has_exit = "  Exit " in target_section
    owner, shelf_counts = None, {}
    for line in view.splitlines():
        if line.startswith("Things here: "): owner = line.removeprefix("Things here: ")
        elif line.startswith("Things: "): owner = line.removeprefix("Things: ")
        elif "shelf" in line.casefold() and line.lstrip().startswith(("Thing", "Landmark")):
            shelf_counts[owner] = shelf_counts.get(owner, 0) + 1
    shelves_unique = all(owner is not None and count <= 1 for owner, count in shelf_counts.items())
    print(f"chars={len(view)} deterministic={deterministic} labels={len(labels)} unique_labels={len(set(labels))} "
          f"resolve={resolved} model_calls=0 scratch={root} start={place}")
    return 0 if (len(view) <= 2000 and view == again and "Here:" in view and "map:" in view
                 and "Place:" in view and len(labels) == len(set(labels)) and shelves_unique
                 and (not source_is_default or ("world [p_world_2]" in view and "world [p_world]" in view
                      and target_has_exit and resolved and resolved.get("kind") == "place" and resolved.get("id") == "p_world"))) else 1


def place_maps(mem: Memory, trace: list[dict], truth: list[dict]) -> dict[int, str]:
    live_worlds = {row["id"] for row in mem.rows("places")
                   if row.get("state") != "merged" and row.get("screen") == "world"}
    truth_by_step = {row["step"]: row for row in truth}
    evidence: dict[int, Counter] = {}
    for row in trace:
        sample = truth_by_step.get(row.get("step"))
        if sample and row.get("place") in live_worlds:
            evidence.setdefault(sample["map"], Counter())[row["place"]] += 1
    return {map_id: counts.most_common(1)[0][0] for map_id, counts in evidence.items()}


def live_graph(mem: Memory):
    places = {p["id"]: p for p in mem.rows("places") if p.get("state") != "merged"}
    links = {}
    for tile in mem.rows("tiles"):
        place = tile.get("place")
        edge = tile.get("exit") or {}
        if place in places and edge.get("to") in places:
            links.setdefault(place, []).append((tile, edge))
    return places, links


def path_exists(links: dict, start: str, goal: str) -> bool:
    todo, seen = [start], {start}
    while todo:
        place = todo.pop(0)
        if place == goal:
            return True
        for _, edge in links.get(place, ()):
            if edge["to"] not in seen:
                seen.add(edge["to"])
                todo.append(edge["to"])
    return False


def graph(mem: Memory, trace: list[dict], truth: list[dict]) -> int:
    places, links = live_graph(mem)
    maps = place_maps(mem, trace, truth)
    for place in places.values():
        print(f"place {place['id']} name={place.get('name')} aliases={place.get('aliases', [])} screen={place.get('screen')}")
    for src, exits in links.items():
        for tile, edge in exits:
            print(f"exit {src}@({tile['x']},{tile['y']}) {edge.get('dir')} -> {edge['to']} arrive={edge.get('arrive')}")
    by_label = {label: maps.get(map_id) for map_id, label in MAP_LABELS.items()}
    chain = [by_label[label] for label in ("2F", "1F", "Town")]
    ok = all(place in places for place in chain) and len(set(chain)) == 3 and all(
        path_exists(links, src, dst) for src, dst in zip(chain, chain[1:])
    )
    print("chain " + " -> ".join(f"{label}:{place or 'missing'}" for label, place in zip(("2F", "1F", "Town"), chain)))
    return 0 if ok else 1


def place_target(mem: Memory, place: str) -> dict | None:
    live = {row["id"] for row in mem.rows("places") if row.get("state") != "merged"}
    incoming = [row for row in mem.rows("tiles") if row.get("place") in live
                and (row.get("exit") or {}).get("to") == place]
    tile = (incoming[0].get("exit") or {}).get("arrive") if incoming else next(
        ([row["x"], row["y"]] for row in mem.rows("tiles") if row.get("place") == place and row.get("floor") is True), None
    )
    return {"kind": "place", "place": place, "tile": tuple(tile), "id": place, "stale": False} if tile else None


def report_leg_factory(env, legs, results):
    from agent import world as world_module

    original = world_module.approach
    index = 0

    def measured(ctx, *args, **kwargs):
        nonlocal index
        leg = legs[index]
        result = original(ctx, *args, **kwargs)
        state = read(env)
        destination = leg.get("expect") or (leg.get("target") or {}).get("place") or leg["place"]
        results.append(result)
        print(f"leg {index + 1} from={leg['place']} to={destination} inputs={result.inputs} reason={result.reason or result.status} truth map={state['map']:#04x} x={state['x']} y={state['y']}")
        index += 1
        return result

    world_module.approach = measured
    return original


def travel_check(bundle) -> int:
    root, env, model, ctx, mem, world, start, perception, harness, trace_rows, truth_rows = bundle
    maps = place_maps(mem, trace_rows, truth_rows)
    target_place = maps.get(0x00)
    target = place_target(mem, target_place) if target_place else None
    resolved = world.resolve("world", perception, start)
    print(f"resolve world (informational): {resolved}")
    if not target:
        print("travel failed: no recorded place target for truth map 0x00")
        return 1
    legs = world.plan(target)
    if not legs:
        print(f"travel failed: no plan from {start} to {target_place}")
        return 1
    ctx.max_inputs = 80
    reported = []
    from agent import world as world_module
    original = report_leg_factory(env, legs, reported)
    try:
        result = world.travel(ctx, legs, harness["step"], SimpleNamespace(path=RUN / "trace.jsonl"))
    finally:
        world_module.approach = original
    state = read(env)
    print(f"travel status={result.status} reason={result.reason or 'done'} inputs={result.inputs} travel_calls=1 model_calls={model.calls} truth map={state['map']:#04x} x={state['x']} y={state['y']} scratch={root}")
    return 0 if state["map"] == 0x00 and result.inputs <= 80 and model.calls == 0 and len(reported) == len(legs) else 1


def truth_offset(place: str, trace: list[dict], truth: list[dict]) -> tuple[int, int, int, int] | None:
    truth_by_step = {row["step"]: row for row in truth}
    for row in trace:
        if row.get("next_place") != place or not isinstance(row.get("tile"), list):
            continue
        frame_truth = truth_by_step.get(row.get("step", 0) + 1)
        if frame_truth and frame_truth["map"] == 0x25:
            return (frame_truth["x"] - row["tile"][0], frame_truth["y"] - row["tile"][1],
                    row["step"], frame_truth["step"])
    return None


def fetch_check(bundle) -> int:
    root, env, model, ctx, mem, world, start, perception, harness, trace_rows, truth_rows = bundle
    places, _ = live_graph(mem)
    one_f = place_maps(mem, trace_rows, truth_rows).get(0x25)
    if not one_f or one_f not in places:
        print("fetch failed: no recorded place for truth map 0x25")
        return 1
    exits = {(row["x"], row["y"]) for row in mem.rows("tiles") if row.get("place") == one_f and row.get("exit")}
    remembered = [row for row in mem.rows("entities") if row.get("place") == one_f and row.get("tile")
                  and row.get("status") != "gone" and row.get("kind", "").casefold() != "exit"
                  and tuple(row["tile"]) not in exits]
    if not remembered:
        print(f"fetch failed: no remembered entity with a non-exit tile in {one_f}")
        return 1
    entity = max(remembered, key=lambda row: row.get("seen", 0))
    target = world.resolve(entity["name"], perception, start)
    print(f"fetch selected id={entity['id']} name={entity['name']} place={one_f} tile={entity['tile']} seen={entity.get('seen', 0)} resolved={target}")
    if not target or target.get("kind") != "memory" or target.get("place") != one_f or target.get("id") != entity["id"]:
        print("fetch failed: name did not resolve to the selected remembered place/entity")
        return 1
    alignment = truth_offset(one_f, trace_rows, truth_rows)
    if alignment is None:
        print("fetch failed: no matching 1F trace/frame truth coordinate")
        return 1
    offset = alignment[:2]
    print(f"fetch coordinate offset={offset} trace_step={alignment[2]} truth_step={alignment[3]}")
    expected = (entity["tile"][0] + offset[0], entity["tile"][1] + offset[1])
    legs = world.plan(target)
    if not legs:
        print("fetch failed: no plan to selected entity")
        return 1
    ctx.max_inputs = 60
    reported = []
    from agent import world as world_module
    original = report_leg_factory(env, legs, reported)
    try:
        result = world.travel(ctx, legs, harness["step"], SimpleNamespace(path=RUN / "trace.jsonl"))
    finally:
        world_module.approach = original
    state = read(env)
    distance = abs(state["x"] - expected[0]) + abs(state["y"] - expected[1])
    print(f"fetch status={result.status} reason={result.reason or 'done'} inputs={result.inputs} model_calls={model.calls} truth map={state['map']:#04x} x={state['x']} y={state['y']} target_truth={expected} distance={distance} scratch={root}")
    return 0 if state["map"] == 0x25 and distance <= 1 and result.inputs <= 60 and model.calls == 0 and len(reported) == len(legs) else 1


def merge_check() -> int:
    run = ROOT / "runs/m1"
    source = run / "checkpoint/000094/memory"
    root = (ROOT / ".tmp/w1").resolve()
    memory = root / "memory"
    root.mkdir(parents=True, exist_ok=True)
    if memory.exists():
        memory.resolve().relative_to(root)
        shutil.rmtree(memory)
    shutil.copytree(source, memory)
    places = {r["id"]: r for r in eval_read(memory / "places.jsonl") if r.get("id")}
    trace = eval_read(run / "trace.jsonl")
    truth = {r.get("step"): r for r in eval_read(run / "truth.jsonl")}
    counts, samples = defaultdict(Counter), defaultdict(lambda: defaultdict(list))
    for row in trace:
        place, actual = row.get("next_place") or row.get("place"), truth.get((row.get("step") or 0) + 1, {})
        if place not in places or places[place].get("state") in {"merged", "candidate"} or actual.get("map") is None:
            continue
        mid = map_id(actual["map"])
        counts[place][mid] += 1
        tile = row.get("tile")
        try:
            if isinstance(tile, list) and len(tile) == 2:
                samples[place][mid].append((int(actual["x"]) - int(tile[0]), int(actual["y"]) - int(tile[1])))
        except (KeyError, TypeError, ValueError):
            pass
    mapped = {place: min(count, key=lambda mid: (-count[mid], mid)) for place, count in counts.items()}
    entities = [e for e in replay(memory / "entities.jsonl")
                if e.get("place") in places and places[e["place"]].get("state") != "merged"]
    ids, pairs = sorted(mapped), []
    for i, src in enumerate(ids):
        for dst in ids[i + 1:]:
            if mapped[src] == mapped[dst]:
                pairs.append((src, dst, mapped[src]))
    eligible = correct = wrong = 0
    print("pair | eligible landmark pairs | computed offset | truth offset | verdict")
    for src, dst, mid in pairs:
        left = [e for e in entities if e.get("place") == src and isinstance(e.get("tile"), list) and len(e["tile"]) == 2]
        right = [e for e in entities if e.get("place") == dst and isinstance(e.get("tile"), list) and len(e["tile"]) == 2]
        bases = lambda records: {_base(e.get("name", "")) for e in records}
        n = len(bases(left) & bases(right))
        eligible += n >= 2
        a, b = samples[src][mid], samples[dst][mid]
        measured = bool(a and b)
        truth_offset = [round(median(p[i] for p in a)) - round(median(p[i] for p in b)) for i in (0, 1)] if measured else None
        if memory.exists():
            memory.resolve().relative_to(root)
            shutil.rmtree(memory)
        shutil.copytree(source, memory)
        mem = Memory(memory)
        merged = World(mem, None).merge(src, dst, 95)
        computed = (mem.get("places", src) or {}).get("offset") if merged else None
        verdict = "rejected"
        if merged and measured and computed == truth_offset:
            correct += 1
            verdict = "correct"
        elif merged:
            wrong += 1
            verdict = "wrong/unmeasured"
        print(f"{src}->{dst} | {n} | {computed} | {truth_offset} | {verdict}")
    if not eligible:
        print("no eligible pair")
    if wrong or eligible and not correct:
        return 1
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["merge"]:
        return merge_check()
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=("graph", "travel", "fetch", "view"))
    parser.add_argument("memory", nargs="?", help="optional memory directory for the view check")
    args = parser.parse_args(argv)
    if args.memory and args.check != "view": parser.error("memory path is only supported for the view check")
    if not CHECKPOINT.is_dir():
        parser.error(f"missing checkpoint: {CHECKPOINT}")
    if args.check == "view":
        return view_check(args.memory)
    bundle = setup(args.check)
    try:
        if args.check == "graph":
            root, env, model, ctx, mem, world, *_rest, trace_rows, truth_rows = bundle
            print(f"scratch={root}")
            return graph(mem, trace_rows, truth_rows)
        return travel_check(bundle) if args.check == "travel" else fetch_check(bundle)
    finally:
        bundle[1].close()


if __name__ == "__main__":
    raise SystemExit(main())
