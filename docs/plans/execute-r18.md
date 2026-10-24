# Executor brief: Avatar round r18, per-frame camera odometer (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Status: not started. Branch `feat/lean-agent`, base HEAD `24712d6` plus this brief and the r18 amendments to `docs/plans/contracts.md` (approved by the user on 2026-10-24, already written into the file: search for "r18 (approved 2026-10-24)").

## 1. Why (the bug this round removes)

In `runs/m1_r17_boot`, one A press (step 69) set off a scripted sequence. The game walked the player 1 right and 10 down to a door, warped into a building, then walked the player 8 tiles up. The agent recorded 5 tiles up. Every later route in that building aimed 3 rows too high, so the agent's "go back out" option ended mid-room (steps 104/109/117 "no response").

A headless replay from `checkpoint/000065` reproduced the run exactly. It showed that the pixels were measured correctly, but the measurement was not kept:
- The step's end settle (`skills.py` press -> `_settled` -> `wait_settled`, `max_frames=600`) never went quiet, because the screen changed every other frame. It returned at the cap mid-walk, with 78 px of the 128 px walk.
- `_forced` (`skills.py:234-241`) turned only that settle into moves.
- The loop's after-settle (`loop.py:291`) measured the other 50 px, and `_settle` (`loop.py:294`) used only its frame.
- The same loss happens in every other settle whose scroll nobody reads: the press start settle, the before settle, and the frames emulated while the model thinks. In live mode only 120 frames are kept (`env_pyboy.py:229`); the tracker then clears (`track.py:89-91`).

The root cause is that position is attributed per settle and per skill, instead of being measured once on every frame. The fix gives position one source, a per-frame camera odometer that counts motion the same way whoever caused it.

## 2. Design (audited; the lanes implement exactly this; stop and ask if it cannot work)

### 2.1 Odometer (agent/track.py, class `Odometer`, no new module)
- **Input:** every emulated frame, in order, from the new optional `Env.listen(fn)` (contract: env). It is called on the emulator thread in live mode, and on the caller thread headless.
  - Fallback for an env without `listen`: the tracker's `wrap` feeds `press` on_frame and every frame of `tick(n)`. Frames between calls are a gap.
  - `fn(None, seq)` = discontinuity (load, restore, generation change). It closes the current segment with `gap=True`.
- **Per frame:**
  - Identical bytes: skip.
  - `_blank` (reuse `observer._blank`): close the segment with `cut="blank"`, then wait for the next non-blank frame. That frame becomes the new segment's baseline and its `arrival` frame (a copy).
  - Otherwise, measure the camera shift with ONE shared helper extracted from `wait_settled.motion()`: `observer.frame_shift(prev, cur, max_shift) -> (dx, dy) | None | "cut"`.
    - It crops to `_moving_rows`. A band under half the height counts as no camera motion (sprites, text).
    - It uses `scroll` on the crop. If that returns None, it retries once at `max(4, tile_px)` when `tile_px` is known, the same rule as `_step`.
    - If still None: it returns `"cut"` when `changed > 0.5`, else None.
    - `wait_settled` must call the same helper, with no behaviour change.
  - `"cut"` closes the segment with `cut="jump"` and starts a new one with this frame as arrival.
  - None adds 1 to `uncertain` and adds no motion. It is never treated as zero motion silently: the count goes to the trace.
- **Held key:** `Tracker.wrap`'s `tracked_press` sets `odometer.held = key` while a press runs, else None. Each nonzero frame entry is `(seq, dx, dy, held)`. "Forced" motion is any entry whose `held` is not a direction key. This replaces the `forced` flag, and the label is generic.
- **State and API:**
  - It holds pixels only, and knows nothing about tiles except the optional `tile_px` used for the retry.
  - A lock guards the state, because the live writer and the agent reader are on different threads.
  - `odometer.seq` is the last frame seq seen.
  - `odometer.take(through=None) -> list[dict]` drains every closed segment plus the open one up to `through`. Each segment is `{"moves": [(seq, dx, dy, held)], "cut": None|"blank"|"jump", "gap": bool, "arrival": Image|None, "frames": int, "uncertain": int}`.
  - `odometer.reset()` drops everything. `Loop.invalidate_live_observation` and restore call it.
- **Cost:** it runs on the emulator thread, so the mean must be at most 3 ms per frame on 160x144. Identical frames cost almost nothing. Keep int16 arrays; do not copy frames except the arrival.

### 2.2 World integration (agent/world.py, agent/loop.py, agent/skills.py)
- **Single writer.** `World.pos` changes only through `World._odometer_fold(segment, place, step)`:
  - It walks the segment's frame entries and keeps a running pixel sum.
  - The tile delta is `round(-sum / tile_px)` per axis, the camera sign rule from `fold`.
  - Each time the tile delta changes, it emits one unit move `{"dir": d, "shift": -tile_px * unit}`.
  - It merges those unit moves in seq order with the step's press bumps: skill moves with a confident `(0, 0)` shift or `no_response`, each carrying `"at"` = the odometer seq when that press started.
  - It calls the existing `space.fold` once on the merged list, so floors, walls, held, silent and firm behave as before.
  - The sub-tile remainder is kept in `World._odo_px` and carried across drains. It resets on a cut, a relocation, a place change and restore.
  - Skill moves with a nonzero shift are never folded for position again. They stay for self-lock, travel route compare, and cut direction.
- **Drain points** (each drains `through` the frame the code is about to use):
  1. Start of `Loop.step`, before perception of the before-frame: fold into the current place.
     - Any cut found in this drain is folded up to the cut and flagged `odometer.cuts`. The rest is dropped and the trace says so.
     - `# ponytail:` ceiling: a door crossed while the model thinks is re-identified by the existing view matching, not followed.
  2. `World.prepare`: drain through the after-frame.
     - Segment 0 (pre-cut) folds into `place`.
     - `cut_dir` is the direction of the last direction press if the step had one. Otherwise it is the direction of the last unit move in segment 0.
     - The exit tile is pos at the end of segment 0.
  3. `World.after`, on a cut:
     - `_locate(next_place, arrival of the LAST segment)` runs first, then that segment folds from the arrival tile.
     - Middle segments are dropped and counted in the trace.
     - After a gap segment, motion is unknown. Keep pos and rely on drift fix and view matching. Never invent motion.
  4. `World.travel`: drain after each leg, so the next leg plans from the real tile.
- **skills.py:**
  - `_step` adds `"at": ctx.odometer.seq` (the press start) to each move.
  - `Ctx` gains `odometer: Odometer | None = None`, which the loop sets.
  - `_forced` and every caller of it are deleted.
  - `press`, `press_seq` and `wait_until` keep their settles for quiet and arrival only.
  - `approach`'s target offset (`skills.py:176-200`) reads the odometer pixel sum since its own press started, instead of `moved.scroll`.
- **loop.py:**
  - `world_exit` (`loop.py:317`) reads "segment 0 has forced motion" from the drain, not `forced` moves.
  - `_settle` stops discarding motion, because there is nothing left to discard.
  - The trace row gains `"odometer"` (contract: trace).
  - The scene-cut test at `loop.py:277` keeps its meaning. It may use `cuts > 0` from the drain as evidence, in addition to `blank_seen`.

### 2.3 Generality audit (what this does and does not assume)
- **Pixels only:** tile size comes from the existing probe (`tile_px`), and speed is free up to `max(4, tile_px)` px per frame. `# ponytail:` at the retry: faster scrolling is a known ceiling.
- **Static HUD and text bands** are excluded by the moving-band crop. Dialogue boxes that slide in or print text do not create motion.
- **Fade cuts and instant cuts** are both segment boundaries. Screen shake sums to about zero.
- **Who caused it** does not matter to position. The `held` label is kept only as evidence.
- **No game words, no RAM reads, no new gates** in `agent/`. RAM is used only by the offline gate script under `scripts/`.
- **Not covered (stated, not hidden):**
  - fixed-camera games, where the player sprite moves and the camera does not (the contract keeps them out of scope, and the self-sprite term is a later round);
  - parallax backgrounds, where the dominant layer wins;
  - a camera that lags the player, which settles once the camera catches up.

## 3. Work packages

| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| O1 | Odometer core + `Env.listen` + shared `frame_shift` | agent/track.py, agent/env.py, agent/env_pyboy.py, agent/observer.py, tests/agent/fakes.py, tests/agent/test_track.py, tests/agent/test_observer.py, tests/agent/test_env.py, tests/agent/test_realtime_env.py | wave 0 | gpt-6-luna HIGH |
| O2 | World/loop/skills use the odometer as the only position source | agent/world.py, agent/loop.py, agent/skills.py, agent/space.py (only if needed), tests/agent/test_world.py, tests/agent/test_loop.py, tests/agent/test_skills.py, tests/agent/test_space.py, tests/agent/test_predict.py, golden data (orchestrator regenerates) | O1 merged | gpt-6-luna HIGH |
| O3 | Offline truth gate script | scripts/check_odometer.py (new script; scripts may read RAM) | O1 merged | gpt-6-luna HIGH |
| O4 | Cleanup wave: prune what O1/O2 made dead, dedupe | any agent/ and tests/agent/ file, grep proof per cut | O2 + O3 merged, G18 green | gpt-6-luna HIGH |

Run order: wave 0, then O1, then O2 and O3 in parallel worktrees, then G18a (gates on the merged HEAD), then O4, then G18b, then LV18 once.

### O1: Odometer core
- **Create or change:**
  - `Odometer` in `agent/track.py`, per §2.1.
  - `Tracker.wrap` feeds it from `env.listen` when present, else from press on_frame and every frame of `tick(n)`. This also fixes `tracked_tick` only seeing the last frame of `tick(n)`.
  - `PyBoyEnv.listen`: call listeners per emulated frame in `press` and in `tick(n)`. When a listener exists, tick frame by frame; with no listener, keep the cost as it is today.
  - `LivePyBoyHost`: call listeners in `_record_frame` on the host thread, outside the condition lock, and swallow nothing. Raise after the host stops, as `run` does today.
  - `FakeEnv`/`GridEnv` in `tests/agent/fakes.py` implement `listen`.
  - Extract `frame_shift` from `wait_settled.motion()`. `wait_settled` behaviour must not change: every `test_observer.py` test passes unchanged.
- **New tests**, all synthetic, using the `test_track.py` `blocks`/`frame` helpers:
  1. a camera scroll of N frames at 1, 2 and 4 px/frame gives the exact pixel sum;
  2. a static HUD band, plus a dialogue box that slides in and prints text, adds no motion;
  3. a fade to black between two scrolls gives two segments with exact sums, the second with an arrival frame;
  4. an instant cut, with no blank, gives `cut="jump"`;
  5. an ambiguous frame counts as `uncertain` and adds no motion;
  6. `listen(None)` gives `gap=True`;
  7. `take(through=seq)` leaves later frames for the next take;
  8. two threads, one writer and one reader, lose no frames: 10k frames, compare the sums;
  9. `PyBoyEnv.tick(5)` with a listener emits 5 frames;
  10. the `held` label is set during a press and None after it;
  11. **perf:** the mean cost per frame on recorded 160x144 frames is reported (the gate is T18-O1). Not an assertion on timing in CI.
- **Acceptance:**
  - `pytest -n auto -q` is green with the same count + new tests;
  - `.tmp/r18/g/t18_o1_perf.py` reports a mean of at most 3 ms per frame on CONSECUTIVE emulated frames: `.tmp/r18/ref/frames/lag1_*.png` (1570 frames of the step-69 replay: walk, fade, warp, walk). It also prints p95 and max (report-only).
  - Amended 2026-10-24 by the user. The first gate fed `runs/*/frames/*.png`, which are per-STEP snapshots, not consecutive frames. Almost every pair there is a different screen, so every frame took the `tile_px` retry. That made the gate measure a case the odometer never sees.
- **Risks:**
  - Thread lock contention: hold the lock only to append and drain.
  - Do not call listeners while holding the host's condition lock (deadlock).

### O2: World uses the odometer
- **Change:** as in §2.2.
- **Pre-approved assertion changes.** Report each one's before and after. Change the source of motion, not the meaning:
  - `test_skills.py::test_non_direction_press_folds_forced_motion` and `::test_press_sequence_keeps_following_moves_after_cut`: rewrite them to feed frames through the odometer. Same expected tiles.
  - `test_world.py::test_forced_cut_folds_destination_once`: the same pos `(4,2)` and arrive `(4,7)`, fed by odometer segments.
  - `test_world.py::test_every_travelled_move_is_folded_exactly_once` and `::test_route_blocked_travel_folds_each_move_once_and_prepare_does_not_refold`, and `test_skills.py::test_travel_settled_ands_its_legs_and_folds_each_move_once`: the invariant becomes "each tile of motion is folded exactly once". The fold call counts may change.
  - `test_loop.py::test_dialogue_forced_cut_records_only_navigable_crossing` and `::test_directional_world_exit_into_new_place_is_place_changes`: "forced" comes from `held`.
  - `test_world_golden.py`: the orchestrator alone regenerates the golden data with `W0_REGEN=1`. The allowed diff classes are the trace `"odometer"` field and `tile` values on steps where the old settle path lost motion. Any other diff: stop.
  - Every other failing assertion means stop the WP, revert, and report.
- **New tests:**
  1. the r17 bug as a unit:
     - a scripted walk crosses a fade;
     - the settle cap (`max_frames`) ends inside the post-cut walk;
     - the remaining frames arrive before the next step;
     - pos after the next drain equals the full walk;
  2. a 3-tile agent route gives pos +3 exactly (no double count with the skill's own shifts);
  3. a bump after an odometer move is recorded on the tile where it happened (seq merge);
  4. a sub-tile remainder carried across two drains gives one tile, not zero or two;
  5. motion during "think time" (frames between steps) is folded before the next perception;
  6. a multi-cut step folds only the first and last segments and flags the middle;
  7. a gap after restore does not move pos;
  8. travel legs plan from the drained tile.
- **Acceptance:**
  - the suite is green;
  - T18-O2: `.tmp/r18/g/t18_o2.py` builds World + Loop on `GridEnv` with a scripted forced walk across a cut ending past a 600-frame settle, and asserts the pos. It makes zero model requests.

### O3: Offline truth gate
- **`scripts/check_odometer.py --run runs/<run> [--from N --to M]`**, for each checkpoint window:
  - It loads `checkpoint/NNNNNN/pyboy.state` headless (`window="null"`), attaches an `Odometer` via `listen`, and replays `presses.jsonl` keys with the loop's settle order.
  - Use `wait_settled` between steps, as `.tmp/r18/ref/replay_r18.py` proved works: copy its approach, do not import from the scratch dir.
  - It reads RAM (map, x, y) per frame through `PyBoyEnv.ram`, which is allowed in scripts only.
  - It compares, per step and per segment, the odometer tile delta (with `tile_px=16` passed as an argument, never hard-coded in agent/) against the RAM delta within the same map. A segment that starts at a map change is compared from the first frame with the new map's warp coordinates.
  - It writes `.tmp/r18/g/<run>_odometer.json` and prints the agreement.
  - It never writes inside `runs/`.
- **Acceptance (T18-O3):**
  - on `runs/m1_r17_boot`, steps 66 to 70: step 69 gives pre-cut `(+1,+10)` and post-cut `(0,-8)`;
  - over all checkpoint windows of `m1_r17_boot`, and of a second run that has checkpoints and `presses.jsonl` (the lane lists its candidates and picks one with the most map changes), step agreement is at least 99%;
  - every disagreement is listed with its step, segment and frames.

### O4: Cleanup wave (dead code and duplication)
- Every cut needs `git grep` output proving zero readers, pasted in the report. Delete or adapt only the tests that exercise removed code.
- **Candidates, each checked:**
  - `skills._forced` (if O2 left any trace);
  - `Settle.tail`, `Settle.after_scroll`, `Settle.last_dir`, `Settle.scroll` (keep any field with a reader);
  - move keys `forced`/`after_cut` and their readers;
  - `World.after`'s `"scroll"` return key (no reader today);
  - the fold-of-nonzero-skill-shift code in `prepare`/`after`/`travel`/`failed_push`;
  - the duplicated per-frame camera `scroll(…, 2)` in `Tracker.update`: feed it the odometer's shift for the same frame if the semantics are identical; else leave it, with a one-line reason;
  - Tracker live catch-up code (`_catch_up`/`_consume_live`/`dropped`) if `listen` makes it unreachable for live envs (keep the fallback for envs without `listen`);
  - stale comments that mention settle-attributed motion.
- **Report:**
  - lines added and removed per file (report-only, no budget);
  - the suite count before and after.
- **Acceptance:** the suite is green, and T18-O1/O2/O3 all re-pass on the O4 HEAD (G18b).

## 4. Gates
| Gate | Pass | Command |
|---|---|---|
| T18-O1 | O1 tests green; mean ≤ 3 ms/frame on `.tmp/r18/ref/frames` (consecutive frames) | `pytest -n auto -q`; `python .tmp/r18/g/t18_o1_perf.py` |
| T18-O2 | O2 tests green; scripted cut walk pos exact | `python .tmp/r18/g/t18_o2.py` |
| T18-O3 | step 69 = (+1,+10) then (0,-8); ≥ 99% step agreement on 2 runs | `python scripts/check_odometer.py --run runs/m1_r17_boot` and the second run |
| G18a/G18b | merged HEAD: full suite green, T18-O1..O3 re-run | orchestrator |
| LV18 | see §5 | orchestrator |

## 5. LV18 (one live run, orchestrator only, after G18b)
- **Ledger:** 9927 / 17854 at plan time. Read `runs/e3_budget.json` before. The cap for LV18 is 200 calls; stop if the ledger would pass 10227.
- **The viewer must be up:** `curl -s http://127.0.0.1:8765/state`. If it is down, start `python scripts/watch_run.py --no-browser` in the background.
- **Run:**
  - copy `runs/m1_r17_boot` to `runs/m1_r18_odo` (never resume the original: resume truncates in place);
  - then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18_odo --resume --from-step 65 --steps 60 --max-calls 200`.
- **Metrics,** from the trace and truth with a scratch script (truth[N+1] vs tile[N]):
  1. within each place visit, the offset (RAM − tile) takes exactly 1 value;
  2. after the walk-in at about step 69, the tile of the building's entrance maps to RAM y = 11;
  3. the agent leaves the building through the bottom door within 15 world steps after it is free to move, or the report says why;
  4. trace `"odometer"` shows `uncertain` and `gap` counts.
- LV18 informs; it does not gate the commits.

## 6. Standing rules (copy into every lane block; they are already in the blocks below)
1. Ponytail (full): reuse > stdlib > minimal code. Match the dense one-line style. Mark known ceilings `# ponytail:`. Deletion over addition.
2. Run `codegraph explore "<symbols>"` (or the codegraph MCP) before grep/Read. Summarise big outputs; never paste whole files back.
3. Python: `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`. Always run pytest with `-n auto` (the default in pyproject addopts). Read pytest's own summary line.
4. agent/:
   - no game words, and no RAM reads (`tests/agent/test_boundaries.py`);
   - no new gates, filters or hidden option rows;
   - no new modules (`Odometer` lives in `track.py`);
   - follow the r18 contract text in `docs/plans/contracts.md` exactly. Never edit `contracts.md`; propose changes in the report.
5. Off-limits, never staged or edited:
   - `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `runs/e3_budget.json`;
   - everything under `docs/plans/` in lanes;
   - `runs/` is read-only;
   - never junctions or symlinks.
   - Worktrees: never link `runs/` or `Rom/` into them, and never `git worktree remove --force`.
6. Zero model requests in lanes. Never run `scripts/run_agent.py`. Offline PyBoy loads of checkpoint states are allowed, read-only, with `window="null"`.
7. Assertions: change an existing assertion only if §3 pre-approves it, and report it before and after. Any other failure: stop the WP, revert its uncommitted edits, report. Never set `W0_REGEN` (orchestrator only).
8. Git, in your worktree only:
   - one commit per WP: `<WP> <one-line result>`, with a one-line body and your `Co-Authored-By:` trailer;
   - stage explicit paths, never `git add -A`;
   - never push, rebase, amend or switch branches.
9. Scratch goes to `C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r18\<WP>\`; gate scripts to `.tmp\r18\g\`. Both are uncommitted.

## 7. Orchestrator protocol
**Tier:** you are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, with one WP per subagent. Never another model. You review diffs, run the gates, merge, regenerate goldens and run LV18; subagents never do.

**Spawning:** give each subagent its §8 block verbatim. Fill in `<WORKTREE>` and `<BASE>` (the commit hash), and replace `[paste §6 rules 1-9 verbatim]` with the full text of §6.

### Wave 0 (main checkout)
1. `git status`. Expected:
   - modified: `scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `docs/plans/contracts.md`;
   - untracked: `baseline_eval.txt`, `scripts/frames_combo.json`, `docs/plans/execute-r18.md`.
2. Commit only `docs/plans/contracts.md` and `docs/plans/execute-r18.md` as `docs: r18 odometer plan and approved contract amendments`.
3. Run the suite. The baseline is **860 passed, 1 skipped**. If the user's watch_run edits change this, record it and continue only if every failure is in `test_watch_run.py`.
4. Record the ledger (`runs/e3_budget.json`).

### Wave 1: O1
- Worktree: `git worktree add C:\Users\Abj1\Documents\GitHub\Avatar-wt\r18O1 -b r18/O1`.
- Give the subagent the O1 block. When it reports:
  - check the diff stays in its owned files and that `wait_settled` behaviour is unchanged;
  - run T18-O1 yourself;
  - allow one repair round with the exact failing output, and stop if it fails twice.
- Merge `--no-ff` into `feat/lean-agent`.

### Wave 2: O2 and O3 in parallel
- Worktrees `r18O2` and `r18O3`, branched from the merged O1.
- Review each like O1. O2 and O3 must not touch each other's files.
- Merge both, then **G18a**: the full suite and T18-O1..O3 on the merged HEAD.
- Regenerate the golden data only within the O2 allowed diff classes. Review the data diff, then `git add -f tests/agent/data/w0_golden` and commit `O2 golden: odometer field and corrected tiles`.

### Wave 3: O4
- Worktree `r18O4`. Review the grep proofs, merge, then **G18b**.

### LV18
- Per §5.

### Final report
Save it to `.tmp/r18/report.md` and give it to the user. It contains:
- each WP: commit, tests added, assertions changed (before and after), lines added and removed;
- the gate table with the commands and numbers;
- the LV18 metrics;
- deviations and proposed contract changes;
- open issues: fixed-camera support, the settle that never goes quiet (`wait_settled` cap reached on animated screens), and anything else found.

Also rewrite `docs/plans/handoff-notes.md` (under 80 lines, fixed headings, current as of r18; it is stale since 2026-10-16) and commit it with the report summary as `docs: r18 result`.

### Stop and ask the user when
- a design point in §2 cannot work as written;
- any non-pre-approved assertion fails after one repair;
- a golden diff falls outside the allowed classes;
- T18-O3 agreement is under 99% after one repair;
- perf is over 3 ms/frame after one repair;
- the ledger would pass 10227;
- `git status` differs from wave 0's expectation;
- a worktree refuses removal without `--force`.

## 8. Lane paste blocks

### O1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18, WP O1 (odometer core). Repo worktree: <WORKTREE> (branch r18/O1 from <BASE>). Main checkout (read-only runs/, Rom/): C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18.md §1, §2.1, §2.3, §3 O1, §6 (in the main checkout); docs/plans/contracts.md sections env, observer, skills, space, world, trace (search "r18 (approved 2026-10-24)").
Build exactly §2.1 and the O1 card: Odometer in agent/track.py; Env.listen (agent/env.py Protocol doc, agent/env_pyboy.py PyBoyEnv + LivePyBoyHost); Tracker.wrap feeds the odometer (listen when present, else press on_frame + every frame of tick(n)); observer.frame_shift extracted from wait_settled.motion() with NO behaviour change; tests/agent/fakes.py envs implement listen. Write the 11 O1 tests. Write .tmp/r18/g/t18_o1_perf.py (mean ms per frame over consecutive runs/m1_r17_boot/frames/*.png pairs, read-only).
Do not touch agent/world.py, agent/loop.py, agent/skills.py (O2 owns them); the odometer is attached but not yet used for position.
Rules: [paste §6 rules 1-9 verbatim]
Done when: pytest -n auto -q green (report the summary line, expected 860 + new passed, 1 skipped); every existing test_observer.py test passes unchanged; perf script prints mean ≤ 3 ms/frame; one commit "O1 odometer core: every emulated frame measured once"; report: diff summary per file, tests added, perf number, anything in §2.1 you could not do as written.
```

### O2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18, WP O2 (World uses the odometer as the only position source). Worktree: <WORKTREE> (branch r18/O2 from <BASE>, which includes O1). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18.md §1, §2 (all), §3 O2, §6; docs/plans/contracts.md r18 notes (env, observer, skills, space, world, trace); the O1 code in agent/track.py (Odometer API).
Build exactly §2.2: World._odometer_fold as the single writer of World.pos (running-sum rounding, remainder in World._odo_px, unit moves merged in seq order with press bumps via "at", then space.fold once); the four drain points; skills._step "at" stamp, Ctx.odometer, delete _forced and its callers, approach target offset from the odometer; loop world_exit from held-label forced motion; trace "odometer" field. Non-zero skill shifts are never folded for position.
Pre-approved assertion changes are exactly the list in §3 O2; report each before/after. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN; if test_world_golden.py fails only in the allowed classes (trace "odometer" field; tile values on steps where the old settle path lost motion), report the diff and leave the golden to the orchestrator.
Write the 8 O2 tests and .tmp/r18/g/t18_o2.py (GridEnv + World + Loop, scripted forced walk across a cut ending past a 600-frame settle, zero model requests, asserts pos).
Do not touch O3's scripts/check_odometer.py.
Rules: [paste §6 rules 1-9 verbatim]
Done when: suite green except a golden diff in allowed classes (reported); t18_o2.py passes; one commit "O2 world position from the odometer only"; report: diff per file, tests added, assertions changed before/after, golden diff summary, any §2.2 point you could not do as written.
```

### O3 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18, WP O3 (offline truth gate). Worktree: <WORKTREE> (branch r18/O3 from <BASE>, which includes O1). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar (runs/ read-only, absolute paths).
Read first: CLAUDE.md; docs/plans/execute-r18.md §1, §2.1, §3 O3, §6; the O1 Odometer API in agent/track.py; scripts/run_agent.py (checkpoint and presses.jsonl format; checkpoint N = state after step N); C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r18\ref\replay_r18.py (a proven headless replay of steps 66-70; copy its approach, never import from scratch).
Write scripts/check_odometer.py exactly per the O3 card: per checkpoint window, headless PyBoyEnv (window "null") from checkpoint/NNNNNN/pyboy.state, Odometer attached via listen, replay presses.jsonl with the loop's settle order, per-frame RAM (map, x, y) via PyBoyEnv.ram (scripts only), compare odometer tile deltas (tile_px argument, default 16) with RAM deltas per step and per segment; a post-cut segment is compared from the first frame showing the new map's warp coordinates. Output .tmp/r18/g/<run>_odometer.json + printed agreement + every disagreement (step, segment, frames). Never write in runs/.
Pick the second run: list runs/* with checkpoint/ and presses.jsonl, choose the one with the most map changes in truth.jsonl, say why.
Rules: [paste §6 rules 1-9 verbatim]
Done when: on runs/m1_r17_boot step 69 reports pre-cut (+1,+10) and post-cut (0,-8); agreement ≥ 99% on both runs (or a list of every disagreement with a frame-level reason if not); one commit "O3 offline odometer truth gate"; report the numbers and runtime.
```

### O4 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18, WP O4 (cleanup wave). Worktree: <WORKTREE> (branch r18/O4 from <BASE>, the merged O1+O2+O3 HEAD that passed G18a). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18.md §2, §3 O4, §6; git log -5 and the O1/O2 diffs (git diff 24712d6..HEAD -- agent/).
Prune what r18 made dead and remove duplication, per the O4 candidate list. For EVERY cut paste the git grep proving zero readers before deleting. Keep any field or path that still has a reader and say which reader. Delete or adapt only tests that exercise removed code (list them). Do not change behaviour: the suite count may drop only by tests of removed code.
Rules: [paste §6 rules 1-9 verbatim]
Done when: pytest -n auto -q green; python .tmp/r18/g/t18_o1_perf.py, .tmp/r18/g/t18_o2.py and scripts/check_odometer.py --run runs/m1_r17_boot all re-pass; one commit "O4 prune settle-attributed motion and duplicates"; report: each cut with its grep proof, lines added/removed per file, suite counts before/after.
```
