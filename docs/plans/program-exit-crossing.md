# Program: exits that are never stepped through (from `runs/m1_live_150`)

Written 2026-10-19. This is a new program, not an RCR amendment. It builds on `aaf5617`, the stairs ping-pong fix. The architecture, the vocabulary, the design rules D1-D9 and the offline playbook are in [rcr/PHASE-E-handoff.md](rcr/PHASE-E-handoff.md) §1, §2 and §5; read those first. **No live run is part of this program.** One is decided after the offline gate.

## 1. What happened in `m1_live_150`

The run was a clean boot of 150 steps on `aaf5617`, using 111 calls. The ledger is at 4737; the caps in `scripts/run_agent.py` were raised to 5115/5065 by the user, and that edit is not committed.

- **S42:** reached the bedroom (`p_place_2`).
- **S62-S123:** 10 moves between floors.
- **S68-S113:** quest q3, "Investigate the PC", used 45 steps. That was the planner's choice, not a harness bug.
- **S124-S129:** talked to Mom; quest q5 "Locate PROF. OAK next door" opened with target `thing:e_door_mat`.
- **S132:** the act picked "go to door mat, 1 right, 5 down". Presses were `DOWN×5, RIGHT`, which put the player on the mat. It was not crossed.
- **S133-S150:** the act picked "go to door mat, here" **18 times**. Each time: `go_to exit:-5,6`, `inputs=0`, `presses.jsonl` `keys: []`, verdict `failed`. The row showed "did nothing N×", but no other row leaves the house, so the model kept picking it.

Phase E (`m1_rcr_phase_e`) step 135 was the same bug. It was misdiagnosed there as only a missing failure record.

## 2. Harness bugs, in priority order

### B1. An exit that has never been crossed is walked to but never stepped through (root of the loop)

- **Path:**
  1. `World.options` (agent/world.py ~995-998) gives an exit with no known destination the action `{"go_to": exit_ref(x)}`, labelled "go to …".
  2. `Loop.step` (agent/loop.py ~171-178) passes it to `World.resolve`, which returns `kind: "exit"` (world.py ~647-652).
  3. `World.plan` (world.py ~1094-1113) then treats it like any tile. Its final leg is `route(...)` to the tile with `"push": None, "expect": None`.
  4. If the player is already on the tile, `dirs == []`: 0 inputs, and the step reports "done".
- **The working pattern already exists.** The on-screen path in `Loop._approach_route` (loop.py ~399-415) ends an exit approach with a push: the verified door direction, else `space.outward(mem, place, tile)`. The `go_to` path to an exit has no push.
- **Root fix (one function):**
  - In `World.plan`, when `target["kind"] == "exit"`, the final leg pushes through the exit. Use the exit record's door direction if one is known, else `outward(self.mem, p, tile)`.
  - Mark the leg as a crossing so `World.travel` runs its existing crossing check. With no scene cut, the result must be `blocked / exit not crossed` (the same `_crossing` failure as everywhere else), not `done`.
  - Don't add a second crossing routine; reuse what `_approach_route` and `travel` already do.
- **Acceptance:**
  - A test in `tests/agent/test_world.py` or `test_exits.py`: the player stands on an exit tile with no destination; `plan(resolve("exit:x,y"))` returns a final leg that has at least one input, ending with the outward push.
  - A loop-level test: picking that row sends at least one key.
  - Offline on the run's own memory, from `runs/m1_live_150/checkpoint/000125` restored like `scripts/eval_act.py` `_plan_checkpoint`: with `pos=(-5,6)` in `p_place_3`, `plan(resolve("exit:-5,6", per, "p_place_3"))` has a non-empty final `dirs` ending in `DOWN`.

### B2. The exit-quest closure rule in `aaf5617` is narrowed the wrong way

- `Notebook._close_crossed` (agent/notebook.py) closes an exit-target quest only for a crossing at a step `s` with `opened - 1 <= s < set_at`.
- **A crossing after the target was set closes nothing**, and the docstring claims D5 requires that. D5 says the opposite: it counts events *after* the target was set. The approved amendment *added* the earlier window ("crossings since the quest opened"); it did not remove the later one.
- Once B1 lets the agent go through the door mat, q5 would stay open, and the ping-pong comes back on the other side of the door.
- `test_exit_quest_closes_only_on_crossings_before_its_target_was_set` (tests/agent/test_notebook.py ~278-290) encodes the bug: it asserts the quest stays open after a crossing at step 8.
- **Root fix:**
  - The window is every crossing at `s >= opened - 1`, with no upper bound.
  - Run the check both when a target is set (`apply_ops`, as now) and on each new move in `observe`, so a later crossing closes the quest on the step it happens.
  - Fix the docstring. Rewrite the wrong test so the step-8 crossing closes the quest, and keep the case "crossing the other way (step 7) closes nothing".
  - `scripts/eval_plan.py` replays the same rule (it calls the same notebook code; check it does not keep its own copy).
- **Note:** `World.exit_route` returns `None` until the exit's destination is known. That is fine: the first real crossing records the destination, and the crossing that taught it is in `data["moves"]`.

### B3. The goal line says "done when: door mat speaks to you" for an exit target

- `Notebook.render_focus` (notebook.py ~276-280) words every `thing:` target as "X speaks to you". For an exit thing, closure is now a crossing (B2), so the act prompt gives a false finish condition.
- **Root fix:**
  - Give the Notebook one injected predicate from World that says whether a ref is an exit. Use `memory._is_exit` on the entity row, not names, and inject it the way `ref`, `canon`, `label`, `ties` and `route` are injected (loop.py ~44, scripts/eval_plan.py `notebook_at`).
  - `render_focus` then words an exit target as "(done when: you go through X)". `_evidence` words a `crossed` closure as "went through X".
- **Acceptance:** `tests/agent/test_notebook.py` render_focus tests cover the thing, exit and place wording. The golden `plan` and flag fields may change; regenerate with `W0_REGEN=1`.

### B4. The menu offers "check X, here" for a thing on the player's own tile, and the skill refuses it

- Seen at S123 (`check desk, here` → `blocked / target is the player`, 0 inputs) and in Phase E at S87.
- `skills.on_player` is documented as "the one screen test … World.options does not offer it". `World.options` applies it only to exits (world.py ~995), not to on-screen thing rows.
- **Root fix:** apply the same `on_player` test where `World.options` builds on-screen thing rows (actors and things), so a thing under the player gets no interact or check row. This follows the rule "every row executable" (D7). Don't add a new test function; reuse `on_player`.
- **Acceptance:**
  - A test in `tests/agent/test_options.py`: a thing perceived at the player's position gets no row, and a thing one tile away still does.
  - `check_tracks m1_live_150 --menu --first 1` reports 0 rows that don't execute.

### Logged, not in this program

- S66 `travel: leg 1 exit mismatch` (stairs, `p_place_3` (-1,0)): one case, cause unexamined.
- The planner spent 45 steps on a PC quest.
- The act never tried the free-buttons row (`DOWN` would have crossed). That is model behaviour; the row was offered correctly.

## 3. Order and gate

Do these as one change each, and measure each one (D9).

1. **B1, then B2, then B3, then B4.** After each: `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` is green.
2. **Golden:** `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py`. Only the plan, quest, verdict and flag fields may change.
3. **Offline gate, 0 calls:**
   - `<py> scripts/eval_plan.py --run runs/m1_live_150 --check all`, `--run runs/m1_rcr_phase_e` and `--run runs/m1_rcr_post_r3` all exit 0. Phase E q2 still closes at S62 (`crossed`); post_r3 q5 does not close.
   - `<py> scripts/check_tracks.py m1_live_150 --menu --first 1`: duplicates 0, max act prompt ≤ 2,000, and 0 rows that don't execute.
   - The B1 checkpoint check above shows a final push of `DOWN` at (-5,6).
   - `rg -ni 'oak|mom|pallet|pokemon|bedroom|kitchen|rug|door mat' agent/` adds no hits.
4. **Commit:** `Exit crossing: <one-line gate result>`. Never push. Leave `scripts/run_agent.py`'s cap edit out of the commit unless the user says otherwise.
5. **Report** to the user with a pass/fail table. Ask before any live run. If approved, run `--steps 150 --max-calls 300 --decide`; the pass bar is "crossed the door mat and reached map 0 before S135, and no row with 0 inputs picked twice in a row".

## 3b. Review of f65b1b3 (2026-10-19, coordinator)

| Item | Verdict | Reason |
|---|---|---|
| B2 "gate fail" (post_r3 q5 closes at S98) | **The gate was wrong, not the code** | In post_r3, q5 targets `thing:e_door_mat`, and at S98 the agent goes through that mat from `p_place_3` to `p_place_4`. Closing with `crossed` at S98 is the event D5 asks for. The old bar ("q5 must not close") guarded against the RCR-era false closure, where walking over the mat counted as "talked". New bar: post_r3 q5 closes at S98 with reason `crossed`, and nothing closes before its crossing. |
| check_tracks exit 1, `order_steps=[2]` | **Pre-existing false positive in the check** | Step 2 is a modal screen (title). `World.options` keeps modal rows in screen order by design, and Phase E's run flagged the same step. Fix the check, not the agent: in `scripts/check_tracks.py` `menu()`, apply the failed-before-new order test only to world-screen steps. |
| B3, B4 | pass | — |
| **B1** | **Defect, must fix before any live run** | See below. |

**B1 defect.** The final exit leg in `World.plan` is `{"dirs": dirs + [push], "push": None, "expect": exit_record.get("to") or "exit", ...}`. In `World.travel`, any leg with `expect` takes the known-door branch:
- `edge = exit_to(..., leg["push"])` gets `push=None`, so `edge` is None and the leg is always a mismatch.
- If the arrival is not recognised (the usual case for a first crossing into a new place), a **successful** crossing returns `blocked "exit mismatch"`. The row is then recorded as failed even though the agent crossed. This repeats the S6 mis-scoring that R4 fixed.
- If the arrival *is* recognised, `mark_exit` writes the exit with `dir=None` and `_cross` uses the fake place id `"exit"`, which corrupts the tiles table.
- A push into a wall comes back as `route blocked` (stage route) instead of `exit not crossed` (stage crossing), because the push is inside `dirs`.

**Root fix.** An exit with no known destination is a *cross attempt*, not a known door:
- The final leg is `{"dirs": dirs, "push": push, "expect": None, "cross": True, "exit_tile": tile, "target": target}`.
- `travel` passes `exit_target="exit"` to `approach` when `leg.get("expect") or leg.get("cross")`, so the skill's existing push step (`route["push"]` with `exit_target`) runs.
- A scene cut already goes down the existing no-`expect` branch: `_travel_open = True`, `done "scene cut"`, and the loop re-identifies the place and records the crossing through `unseen_cross`.
- No cut gives the skill's own `blocked "exit not crossed"` with stage `crossing`.
- Write no new crossing code.

**Tests:**
- `travel` on an unknown-exit leg where the push causes a cut returns `done` with `_travel_open`, and writes no `mark_exit` with a `None` direction.
- Where the push does not cut, it returns `blocked` with failure stage `crossing`.
- Keep B1's plan test, adjusted to the new shape: the push is in `push`, not in `dirs`.

## 4. Hard rules (repeat them in every subagent prompt)

- No game words in `agent/`.
- No new steering gates.
- Fix the shared function.
- Pytest always runs with `-n auto`.
- **Never use git worktrees with symlinks or junctions to `runs/` or `Rom/`.**
- Never delete or overwrite anything in `runs/`.
- Don't change `agent/perceive.py` prompts (the perception cache).
- Line counts are report-only.
