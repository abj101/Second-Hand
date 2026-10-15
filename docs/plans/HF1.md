# HF1: Can the executor route around a known actor without corrupting the map?

Status: done 2026-10-14 (offline gate passed). Depends on: none. Requests: 0.

Read the [program](program-harness-reliability.md), [handoff](HF-handoff.md), and shared contracts 1–2. Use the program paste block with phase HF1. Expected impact: highest; complexity: medium.

## Entry and evidence

- Capture git revision/status and hashes of protected evidence; preserve pre-existing runner/test budget edits. Run the existing suite once to establish the baseline; record failures without fixing unrelated code.
- Read `World.before/plan/travel/prepare/after`, `route/fold`, `approach/_step/SkillResult`, and `Loop._unexplored`/the on-screen `go_to` route branch. CodeGraph query: `World.before World.plan World.travel route fold approach Loop._unexplored`.
- Checkpoint125 has player `(0,-5)` and actor `(0,-4)`. Existing `route` has no occupancy input; `fold` correctly avoids storing an actor as a wall. Preserve that distinction.

## Work packages and ownership

All workers use the selected profile's HIGH worker tier. Run **A -> B -> C**; overlapping source/test files make parallel writers unsafe.

| WP | Owns | Deliverable and targeted command |
|---|---|---|
| A | `agent/space.py`, `tests/agent/test_space.py` | Optional occupancy in BFS, including fallback search. `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_space.py -q -p no:cacheprovider` |
| B | `agent/world.py`, `agent/loop.py`, `tests/agent/test_world.py`, `tests/agent/test_loop.py` | Thread current-place occupancy through every active route caller, including exploration, visible target routing, and travel. `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_world.py tests/agent/test_loop.py -q -p no:cacheprovider` |
| C | `agent/skills.py`, `agent/world.py`, `tests/agent/test_skills.py`, `tests/agent/test_world.py` | Preserve route-blocked stage/location in travel aggregation; account movement once. `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_skills.py tests/agent/test_world.py -q -p no:cacheprovider` |

Each card means: read its listed symbols/callers, introduce its failing case first, implement only that behavior, run its tests, and return changed paths + evidence. No fixed detour directions or game-specific coordinates in runtime code.

## Required behavior and tests

1. **Detour:** generic grid, actor on shortest route, open side corridor. Returned route never enters actor tile and reaches the same destination. Include translations/rotation of the geometry so DOWN-specific patches fail.
2. **No detour:** occupied one-cell corridor/goal returns no feasible route; executor sends no known-invalid movement. Do not relabel the exit as false or mark a permanent wall.
3. **Actor moves:** fresh observation clears previous occupancy; normal direct routing becomes possible. Actors in a different place/stale stored actors do not block this route.
4. **Approach/interact actor:** route to a free adjacent tile; preserve facing and confirm. Do not accidentally ban talking to actors or use an occupied goal exception that restores the collision.
5. **Every caller:** test World travel, unexplored movement (including its appended frontier step), on-screen `go_to`, and exit push. Greedy `approach`/`interact` must still stop promptly on an unexpected obstruction; no claim that this phase implements full moving-object tracking.
6. **Unexpected mid-route blockage:** preserve partial displacement/inputs, return route-stage failure, then re-observe before replanning. A blocked route before reaching an exit is not an exit-crossing failure. Cap and scene-cut behavior remain unchanged.
7. **Integration:** GridEnv route -> skill -> world update; actor collision never writes a permanent wall; static wall learning still works. Travel and Loop must not fold the same moves twice.

## Gate

- All seven behavior groups above and their targeted tests pass, followed by the program's full-suite command (including boundaries).
- Read-only calculation over a copied checkpoint125 memory shows the first route segment avoids the actor; record selected route and actor set. This proves routing, not autonomous gameplay or successful emulator exit.
- No cloud requests; ledger and protected-run hashes unchanged. No required test may be skipped/xfail.
- Failure: return to the owning worker with the failing evidence; no prompt or model changes to make the route pass.

## Result and handoff

- Baseline / preserved dirty changes: a3695e9 on feat/lean-agent. Full suite before: 353 passed, 1 failed (`test_loop.py::test_stage_b_cap_override_keeps_phase_and_m1_limits`, "DID NOT RAISE CallCap"; sits beside the pre-existing budget-only edits in `scripts/run_agent.py`/`tests/agent/test_run_agent_budget.py`, which were left untouched; cause not investigated). Dirty runner/test-budget diff and `.claude/` preserved.
- Files changed: `agent/space.py`, `agent/world.py`, `agent/loop.py`, `agent/skills.py`, tests `test_space/world/loop/skills.py`. Orchestrator Sonnet 5.5 -> Haiku HIGH workers (A, B, C in order).
- Failing then passing: A 9 new tests failed (`TypeError ... occupied`) -> `test_space.py` 18 passed. B 29/32 new failed (routes through actor / corridor returned a route) -> `test_world+test_loop+test_space` 167 passed, 1 baseline failure. C 18 new failed (no `failure` attr; "exit mismatch" instead of "route blocked") -> `test_skills+test_world` 131 passed. 8 symmetry/rotation variants for detour/corridor/beside cases. Wall-vs-actor and no-double-fold tests passed on baseline (regression guards, not red-first).
- Full suite after: `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider` -> 422 passed, 1 failed (same baseline failure), exit 1. `test_boundaries.py`: 3 passed. No skips/xfail added.
- Additive schema: `route(..., *, occupied=frozenset())`; `SkillResult.failure: dict|None=None` (last field; `stage` route|crossing, `reason`, `direction`; travel adds `place`, `tile`, optional `blocker_id`); `_result(..., failure=None)`; `World.occupied(place)`, `World.beside(place,pos,tile)` (memory stand-tile search moved out of `plan`); `Loop._screen_route`. Route-blocked expect leg returns `leg n route blocked` (no `mark_exit`/`_cross`/`_travel_open`). `_travel_result(..., failure=None)`.
- Checkpoint125 read-only calc (copy under `.tmp/hf/HF1/mem`, place p_place_5, start (0,-5), actor set {(0,-4)}): to exit tiles (0,0)/(0,-1)/(-1,0) the old routes enter (0,-4); new routes are `LEFT,DOWN,DOWN,DOWN,DOWN,DOWN,RIGHT`, `LEFT,DOWN,DOWN,DOWN,DOWN,RIGHT`, `LEFT,DOWN,DOWN,DOWN,DOWN,DOWN` and avoid it. Proves routing only.
- Requests: 0; ledger untouched. Protected `runs/m1_ip` and `m1_decide3` trace hashes unchanged; no `runs/` changes.
- Caveats: the final facing press into an actor still reports skill status blocked/`route blocked` (travel treats it as done via `_facing_target`); crossing failures carry no direction/blocker_id; `_travel_result`'s explicit `failure` argument is unused. Greedy approach unchanged. Live checkpoint resume was not run.
- Gate: PASSED offline (with the one pre-existing baseline failure). Next: HF2.
