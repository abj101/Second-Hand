from __future__ import annotations

import json
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.memory import Memory, _base
from agent.space import tile_of


def dupes() -> None:
    run = ROOT / "runs/m1_long"
    scratch_root = ROOT / ".tmp"
    scratch_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-track-", dir=scratch_root) as scratch:
        memory_root = Path(scratch) / "memory"
        shutil.copytree(run / "checkpoint/000025/memory", memory_root)
        mem = Memory(memory_root)
        for line in (run / "trace.jsonl").open(encoding="utf-8"):
            row = json.loads(line)
            if not 38 <= row.get("step", 0) <= 70:
                continue
            entities = row.get("entities", [])
            player = next((e for e in entities if e.get("kind", "").casefold() == "player"), None)
            tiles = ({i: tile_of(e["at"], player["at"], tuple(row.get("tile", (0, 0))), (160, 144), 16)
                      for i, e in enumerate(entities)
                      if player and e.get("kind", "").casefold() != "player" and e.get("at")}
                     if player else None)
            mem.observe({"entities": entities}, row["place"], row["step"], tiles)

        grouped = defaultdict(list)
        for entity in mem.rows("entities"):
            if 38 <= entity.get("last", 0) <= 70:
                grouped[_base(entity.get("name", ""))].append(entity)
        for base, entities in sorted(grouped.items()):
            details = ", ".join(f"{e['name']}@{tuple(e['tile']) if e.get('tile') else '?'}" for e in entities)
            print(f"{base}: {len(entities)} row(s) [{details}]")


def self_track() -> None:
    from math import hypot

    from agent.env_pyboy import PyBoyEnv, find_rom
    from agent.skills import Ctx, press
    from agent.world import World
    from scripts.truth_pokered import read as truth_read
    from tests.agent.fakes import StubModel

    for run, step in (("m1_long", 70), ("m1", 54)):
        checkpoint = ROOT / "runs" / run / "checkpoint" / f"{step:06d}"
        scratch_root = ROOT / ".tmp"
        scratch_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="check-track-self-", dir=scratch_root) as scratch:
            memory_root = Path(scratch) / "memory"
            shutil.copytree(checkpoint / "memory", memory_root)
            harness = json.loads((checkpoint / "harness.json").read_text(encoding="utf-8"))
            env = PyBoyEnv(find_rom(), state=str(checkpoint / "pyboy.state"))
            try:
                ctx = Ctx(env, StubModel([]), tile_px=harness.get("tile_px") or 16,
                          move_frames=harness.get("move_frames", 16))
                world = World(Memory(memory_root), ctx.tile_px)
                world.restore(harness)
                for index, key in enumerate(("LEFT", "DOWN", "RIGHT", "UP", "LEFT"), 1):
                    before = env.screenshot()
                    result = press(ctx, key)
                    raw = truth_read(env)
                    player = next((s for s in raw["sprites"] if s["slot"] == 0), None)
                    truth = (player["x"] + 8, player["y"] + 8) if player else None
                    per = {"screen": "world", "entities": ([{"kind": "player", "at": [truth[0] / 160, truth[1] / 144]}] if truth else [])}
                    world.observe_after(per, result.after, True, world.current_place or "check", (160, 144), step + index,
                                        before=before, skill=result)
                    spot = result.self_at
                    distance = hypot(spot[0] * 160 - truth[0], spot[1] * 144 - truth[1]) if spot and truth else None
                    move = result.moves[-1] if result.moves else {}
                    print(f"{run} {key}: scroll={move.get('shift')} self_at={spot} heat_box={ctx._last_residual_box} "
                          f"heat_coverage={ctx._last_residual_box_coverage:.3f} lock={world.self_spot} "
                          f"truth_sprite0={truth} distance={distance}")
                    if spot is None:
                        print(f"  residual heat box: {ctx._last_residual_box} (no compact box; move scroll={move.get('shift')})")
            finally:
                env.close()


def probe_track() -> None:
    from math import hypot

    from agent.env_pyboy import PyBoyEnv, find_rom
    from agent.skills import Ctx
    from agent.world import World
    from scripts.truth_pokered import read as truth_read
    from tests.agent.fakes import StubModel

    checkpoint = ROOT / "runs/m1_long/checkpoint/000070"
    scratch_root = ROOT / ".tmp"
    scratch_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-track-probe-", dir=scratch_root) as scratch:
        memory_root = Path(scratch) / "memory"
        shutil.copytree(checkpoint / "memory", memory_root)
        harness = json.loads((checkpoint / "harness.json").read_text(encoding="utf-8"))
        env = PyBoyEnv(find_rom(), state=str(checkpoint / "pyboy.state"))
        try:
            ctx = Ctx(env, StubModel([]), tile_px=harness.get("tile_px") or 16,
                      move_frames=harness.get("move_frames", 16))
            ctx.step = 71
            world = World(Memory(memory_root), ctx.tile_px)
            world.restore(harness); world.current_place = harness.get("place") or "check"
            world.self_spot, world._self_from_motion = None, False
            result = world.probe(ctx)
            raw = truth_read(env)
            player = next((s for s in raw["sprites"] if s["slot"] == 0), None)
            truth = (player["x"] + 8, player["y"] + 8) if player else None
            spot = world.self_spot
            distance = hypot(spot[0] * 160 - truth[0], spot[1] * 144 - truth[1]) if spot and truth else None
            print(f"probe presses={result['presses']} locked={result['locked']} self_spot={spot} "
                  f"truth_sprite0={truth} distance={distance}")
        finally:
            env.close()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["dupes"]:
        dupes()
    elif sys.argv[1:] == ["self"]:
        self_track()
    elif sys.argv[1:] == ["probe"]:
        probe_track()
    else:
        raise SystemExit("usage: check_track.py dupes|self|probe")
