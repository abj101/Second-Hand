# Avatar harness round r6: replay fallback, static-cell avoidance, full run, regression set rebuilt

**Summary (6 lines)**
1. **F1 (eval):** the `--om points` replay calls a case INCONCLUSIVE when the first step after a checkpoint has no recorded perceiver call (cases 3, 5, 6). The live run *reused* its previous perception there, so nothing was recorded. The fix: when the recorded perceiver replies for a step run out, reuse the most recent earlier recorded perceiver reply, as the live run did.
2. **S1 (agent):** `space.route` treats every unseen cell as walkable, and only actors are obstacles. B2 measured that 42 of 46 failed moves walked into an unseen cell. B3's counterfactual says that avoiding cells where a perceived non-actor, non-exit thing sits would have avoided 17 of 46. S1 adds those cells to the first ("strict") routing pass; the existing second pass is the fallback when no route avoids them.
3. **L (live):** one full 150-step run from boot, `runs/m1_r6_live_150`, with checkpoints every 5 steps (D1 is already in `run_agent.py`). Hard cap 400 asks.
4. **R (eval):** rebuild the 10-case `scripts/points.json` from that run, so each case replays at most 4 steps. A3 dry must reach at most 1 INCONCLUSIVE, then score it live with fresh, uncached asks (at most 20).
5. Only S1 changes `agent/`. F1, R and the fresh-live flag are eval-only.
6. **Ledger:** 6332 / 7854, so 1522 asks are left. This round spends at most 420.

| id | title | files | risk | asks | approved |
|---|---|---|---|---|---|
| F1 | Points replay reuses the last recorded perception when a step has none | `scripts/eval_act.py`, `tests/agent/test_om_eval.py` | low (eval) | 0 | yes |
| F2 | `--live` can bypass the model response cache (fresh asks) | `scripts/eval_act.py`, `tests/agent/test_om_eval.py` | low (eval) | 0 | yes |
| S1 | Routing avoids cells of perceived static things in its first pass | `agent/space.py`, `tests/agent/test_space.py` | med | 0 | yes |
| L | Full 150-step live run from boot | none (run data only) | - | ≤ 400 | yes |
| R1 | Rebuild `scripts/points.json` from `m1_r6_live_150` (10 cases) | `scripts/points.json` | none | 0 | yes |
| R2 | A3 dry + A4 fresh live on the rebuilt set; B1 audit on the new run | none (report) | none | ≤ 20 | yes |

Order: F1 → F2 → (re-run A3 on the old cases) → S1 → L → R1 → R2.

---

## Context (no repo access assumed)

Repo `Avatar`, branch `feat/lean-agent`, HEAD `39b1074` (r5: A1 `7d97629`, A2 `356ed72`, B1 `39b1074`). Interpreter `.venv/Scripts/python.exe`; tests `-m pytest -n auto -q` (709 passed, 1 skipped). Rules: root-cause, reuse, no new abstractions, no new mechanical gates, options are never removed, no game names in `agent/`.

User WIP, never touched: `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `docs/plans/`. `run_agent.py` already holds the user-approved caps `TOTAL_CAP, M1_CAP = 7854, 7804` and `if loop.step_no % 5 == 0: _checkpoint(...)`.

r5 evidence: `.tmp/r5/` holds `a3.txt`, `a4.txt`, `moves_*.txt|json` and `counterfactual.json`.

---

## F1: replay perception fallback (`scripts/eval_act.py`, the points stub, about line 812)

**Root cause.** The stub indexes recorded calls by `(step, role)` and raises `PointsInconclusive("no recorded perception")` when they run out (line 817). The live loop skips the perceiver when the frame hash equals the cached one (`perception_reuse_rate` 0.72 in r3b). The replayed headless frame differs by a few pixels, so the replay perceives where the live run did not.

The three cases that broke this way (3 → step 26, 5 → step 101, 6 → step 126) are all the first step after a checkpoint.

**Change.** In the exhaustion branch, for `role == "perceiver"` only, return the most recent recorded perceiver reply from any earlier step ≤ the current one (the last row in file order with that role and `step <= current`). Count it as `perception_fallback` and print the count per case. A case with more than 2 fallbacks stays INCONCLUSIVE ("perception fallback > 2"), so a long replay cannot run on stale perception. `skill` and `act` keep their current rules.

**Test.** A stub with recorded perceiver rows for steps 10 and 12 only:
- step 11 returns step 10's reply, with fallback = 1;
- a third fallback in one case raises INCONCLUSIVE.

**Check.** Re-run A3 dry on the existing r5 cases and report the new status of cases 3, 5 and 6. Expected: they get past the checkpoint step. They may then stop on divergence or on "recorded choice not offered"; report what happens.

## F2: fresh live scoring (`scripts/eval_act.py`)

A4 in r5 spent 0 asks because both prompts were cache hits (same system and user, same cache key). Add `--no-cache` to `--om points --live`. With it, the live model is built without its cache (pass whatever `Ollama(...)` uses to disable `cache_dir`, or a fresh empty scratch cache dir under `.tmp/`), and every ask goes through `scripts/run_agent._ledger` as in the r4 micro-check. Test: with `--no-cache`, a stubbed `_post` is called even when a cache file for that key exists.

## S1: routing avoids perceived static cells first (`agent/space.py` `route`, lines 39–53)

**Root cause** (B2, 0 asks, three runs): 42 of 46 failed movement rows (148 of 170 wasted presses) hit a cell that had no tile row. `route` treats such cells as passable. The only obstacles are `blocked` edges, `floor is False` cells, and `occupied` = actor tiles (`World.occupied`, `agent/world.py:1133`). Perceived furniture, signs and rocks never become obstacles, although memory holds their tiles. The town streak (steps 57–67, 11 failures, 34 presses) walked into cells where a signpost and rocks were perceived.

**Counterfactual** (`.tmp/r5/counterfactual.json`): avoiding perceived non-actor, non-exit cells, with the current routing as fallback, would have avoided the measured collision cell in 17 of 46 failed rows (r3b 36, 58, 59, 63, 64, 65, 93, 94, 95, 96, 137; w8 44, 46, 47, 48, 59, 64), which spent 66 presses. Known-floor-first routing avoided 0 and is dropped.

**Change.** `route` already has two passes: `strict` (avoids inferred exits) and a non-strict fallback (line 53). Make the strict pass also skip any cell, other than `goal`, that holds a **static thing** of this place:
- an entity row in `mem.rows("entities")` with `place == place`, a `tile`, and a kind that is not an actor (`ACTOR_KINDS`), not transient (`TRANSIENT`) and not an exit (`_is_exit`);
- excluding merged and gone rows (follow how `World` filters `state` / `status`).

The non-strict pass ignores them as it ignores inferred exits today. So a route through a static cell is used only when nothing else exists: no option disappears, and no gate is added.

Reuse `ACTOR_KINDS`, `TRANSIENT`, `_is_exit` and `normalize_kind` from `agent/memory.py`. First check that importing `agent.memory` from `agent.space` creates no import cycle; if it does, stop and report. Keep the change inside `route`, in the file's style.

**Callers** (all benefit, none changes): `World.plan`, `World.explore_dirs`, `World.beside`, `Loop._screen_route`, `Loop._approach_route`. Check the blast radius with CodeGraph before editing. Tests touching routing: `tests/agent/test_space.py`, `test_world.py` (for example `test_plan_has_no_route_through_a_one_cell_corridor_held_by_an_actor`), `test_loop.py` (go_to, unexplored) and `test_exits.py`. **No existing assertion may change.** If one fails, stop and report the test and the reason.

**Tests to add** (`tests/agent/test_space.py`):
- (a) A static thing on the straight path → the route goes around it.
- (b) A static thing in a one-cell corridor with no other way → the route goes through it (fallback).
- (c) A static thing on the goal cell → the goal is still reachable.
- (d) An actor or exit entity is not treated as static.

**Offline check (0 asks).** Re-run `scripts/eval_moves.py` on r3b, w8 and r4. The classifier reads recorded rows, so its output must be unchanged. Report that, plus the counterfactual list from `.tmp/r5/counterfactual.json` for reference.

## L: full live run from boot

Check that the viewer is up: `curl -s http://127.0.0.1:8765/state > /dev/null`. If it isn't, start it in the background: `.venv/Scripts/python.exe scripts/watch_run.py --no-browser`. Then:

```
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r6_live_150 --steps 150 --max-calls 400 --decide
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_r6_live_150
.venv/Scripts/python.exe scripts/eval_moves.py runs/m1_r6_live_150
```

(The `eval_moves.py` argument form follows what B1 implemented.) A new folder, no `--resume`. If the run stops early (`CallCap` or an error), report the step and keep the data; do not re-run without the user.

**Success targets, each with a measured value:**

| target | r3b (before) | r4 resume | goal |
|---|---|---|---|
| `m1_gate_pass` | false | true (resume) | true from boot |
| `steps_to_starter` | none | 125 | report |
| names | RED / BLUE | RED / BLUE | preset |
| nickname answer | n/a | NO | NO |
| movement rows failed / total | 27 / 36 | 5 / 8 | report; expect fewer `unknown_cell` + `static_thing` |
| eval_moves class table | B2 | B2 | report |
| longest failed movement streak | 11 | 2 | report |
| repeat-talk rows (`says the same as before`) and their verdicts | 10 rows, `changed` | 0 | report; verdicts `failed` from the 2nd repeat |
| furthest truth map after the starter | n/a | 40 (lab) | report (0 = town, 12 = Route 1) |
| asks spent, seconds per step | 254, 5.9 s | 120, 7.5 s | report |

## R1: rebuild `scripts/points.json` from the new run

Ten cases of the same kinds as r5, taken from `m1_r6_live_150`:
1. player name menu;
2. rival name menu;
3. 2F leave by the stairs;
4. 1F leave by the door mat;
5. lab "which POKéMON" (with `plan_re` `BALL` if a plan fired there);
6. the first repeat-talk loop, if any (else the first failed movement in the lab);
7. the step that reaches the ball;
8. the nickname offer;
9. the lab exit after the starter;
10. the first step in town after the starter (or the furthest new map).

Method: for each case, read the recorded act prompt at that step in `calls.jsonl` and copy the option labels into `label_re`. Set `checkpoint` to the largest multiple of 5 that is ≤ step and exists. Verify truth map, x and y. If a situation never happened in the run, say so and pick the nearest equivalent; do not invent one.

Keep the r5 cases: move them to a top-level key `"r5"` only if `om_points` can select them, else leave them in git history and just say so. The new set is the baseline.

## R2: dry, live, audit
1. A3 dry on the new set. **Goal: at most 1 INCONCLUSIVE.** Report the full table with reasons.
2. A4 `--live --no-cache` on the PASS cases. Cap 20 asks. Report each choice right or wrong, and the asks spent.
3. The B1 class table on the new run, next to r5's.

## Report format
- the plan table with sha, suite and status;
- F1 re-run statuses for r5 cases 3, 5, 6;
- the L success-target table;
- the R2 tables;
- asks spent and the final ledger;
- files edited;
- the commands that made model requests, with counts;
- skipped or unverified items;
- surprises.
