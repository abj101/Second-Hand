# Executor brief: Avatar round r18-fix, two LV18 defects (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `f283d2e`. Diagnosis: `.tmp/r18/lv18/report.md`, `.tmp/r18/lv18/fix-proposal.md`. Read those two first; this brief is self-contained otherwise.

## 1. Why
LV18 (`runs/m1_r18_odo`, resumed from `m1_r17_boot` checkpoint 65) showed the odometer measures correctly (step 69 px `[-16,-32]` = 1R+10D−8U; one RAM−tile offset in the lab), but two defects broke the result:
- **D1, no-response presses never reach `fold`.** A no-response press is `{"dir", "shift": None, "no_response": True}` (`tests/agent/test_skills.py:587`). `_odometer_drain` files it as a bump (`agent/world.py:365`), but `_odometer_fold` keeps only bumps whose shift is `(0,0)` (`agent/world.py:401`), so `space.fold`'s silent/wall branch (`agent/space.py:15-18`) never runs. LV18: 6× "no response UP" at p_place_5 (4,-1), the row stays `blocked:[]`, no `silent`; 17 no-response steps vs 1 in r17. Before r18, `World.prepare` passed every skill move to `fold`.
- **D2, a cut whose after-screen is dialogue folds the destination's motion into the source place.** r18 drains the whole scripted walk, so step 69's after-frame is the end-of-walk dialogue (`runs/m1_r18_odo/frames/000069_after.png`). `match_place` keeps `prev` on a non-world screen (`agent/memory.py:209-212`) and the loop sets `cut_carry` (`agent/loop.py:334`), but `World.after`'s no-place-change branch folds the post-cut segments into `prev` (the contract's "dialogue/menu over a world place folds" rule). Result: 24 floor tiles written into Pallet (p_place_4) at step 69, including the 8-up lab walk at (7,5)…(7,-3). At step 88 the deferred match mints the lab at (0,0) (`agent/memory.py:234-241`), `World.before` resets pos, no exit/crossing/entrance is recorded, the measured walk is lost, and no bottom-door exit is ever offered.

## 2. Design (audited; implement exactly this; stop and ask if it cannot work)

### 2.1 F1: no-response bumps reach fold (`agent/world.py`)
In `_odometer_fold`'s `kind == 1` branch, also append the move when `move.get("no_response") and move.get("dir") in DIRS` (shift None). `_promote` stays bump-only (shift `(0,0)`). Nothing else changes; `space.fold` already records `silent` on the first step and an unconfirmed wall on a later step.

### 2.2 F2: hold post-cut motion until the place is re-identified (`agent/world.py`, `agent/loop.py` only if needed)
One new World field, `self._held = None`, else `{"src": place, "tile": (x, y), "dir": str | None, "segments": [...]}`.
1. **Open (in `after()`, no-place-change branch).** When `scene_cut` is true, `next_place == place`, and the after-screen (`self._odo_screen`, set by the loop just before `after()`) is not `"world"`: fold the segments up to and including the first cut-bearing segment into `place` as today. Then set `_held = {"src": place, "tile": self.pos, "dir": <prepared cut_dir, else the last unit direction the pre-cut fold returned>, "segments": <every segment after it>}`. The travel branch of `after()` is unchanged.
2. **Divert (in `_odometer_fold`, the single writer).** While `_held` is set, append the segment to `_held["segments"]` and return None: no fold, not counted as dropped. This catches `odometer_start` drains on steps 70–87 too.
3. **Resolve to a new place (in `before()`, `prior and place != prior` branch, when `_held["src"] == prior`).**
   - `located = self._locate(place, frame)`.
   - `delta` = the held segments' summed px, turned into tiles with the same rounding `_odometer_fold` uses: `round(-px / tile_px)` per axis.
   - `arrival = located − delta`; `self.pos = arrival`; `self._odo_px = (0, 0)`.
   - Clear `_held` first, then fold each held segment re-tagged `_place = place`, so pos ends at `located`.
   - If `dir` is set: `space.mark_exit(mem, src, tile, dir, place, arrival, step)` (both sides, the inferred way back included) and `self._cross(src, tile, place, arrival, step)`, exactly once.
   - LV18 expectation: lab entrance (0,8) DOWN, player (0,0), RAM door (5,11) ↔ tile (0,8).
   - If `_held["src"] != prior` (should not happen), clear `_held`, fold nothing, and add its segments to the trace's `dropped`.
4. **Resolve to the same place.** On a world screen with `place == _held["src"]` (a fade inside one room), clear `_held` and fold the held segments into `src`, which is today's behaviour, just later.
5. **No double crossing.** `World.unseen_cross` returns early while `_held` is set (it runs in the loop before `before()`).
6. **Checkpoint.** `state()`/`restore()` carry `_held`, with segments serialised without image fields (`arrival` and any frame or ndarray); a restore inside the dialogue resolves the same way.
7. No new gate, filter, option row, module or game word. `match_place` is unchanged: "dialogue is never a place" stays.

### 2.3 Contract amendments (the user approves them by issuing this brief; the orchestrator commits them in wave 0)
Append under `## world` in `docs/plans/contracts.md`, after the r18 `after` comment:
```
        # r18-fix (approved 2026-10-24): when a cut's after-screen is not "world" and the place match is deferred
        # (cut_carry), segments after the first cut are held, not folded into the old place, until the next world screen
        # re-identifies the place. A new place resolves at arrival = located − held tiles, then folds the held segments
        # from there and records the crossing and both exit sides once (unseen_cross is skipped while held); the same
        # place folds them as before. A no-response press (shift None) reaches space.fold like a bump (silent, then an
        # unconfirmed wall), as before r18. World.state carries the held segments without frames.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| F1 | §2.1 + test | agent/world.py, tests/agent/test_world.py | wave 0 | gpt-6-luna HIGH |
| F3 | Gate script T18F (uncommitted), written red from §2 before F2 lands | `.tmp/r18fix/g/t18f.py` only | wave 0 | gpt-6-luna HIGH |
| F2 | §2.2 + tests | agent/world.py, agent/loop.py (only if needed), tests/agent/test_world.py, tests/agent/test_loop.py | F1 merged | gpt-6-luna HIGH |

Run order: wave 0 → F1 and F3 in parallel → merge F1 → F2 (from merged F1; F3's script is run red on merged F1 for D2, green for D1) → G18F → LV18b once. F1 and F2 share `world.py`/`test_world.py`, so they never run in parallel.

### F1
- **Test** (`test_world.py`, follow `test_bump_at_seq_follows_camera_motion_from_that_frame`): two no-response UP bumps on two steps through `prepare`/`after` with odometer segments; assert the tile gets `silent["UP"]` after step 1 and `"UP" in blocked` after step 2. Also one no-response bump after an odometer move lands on the moved-to tile.
- **Pre-approved assertion changes:** none. If an existing test asserted that no-response leaves tiles untouched under the odometer path, stop and report it.

### F3 (gate T18F; zero model requests)
`.tmp/r18fix/g/t18f.py`: build World + Loop on `GridEnv`/`StubModel` (pattern: `.tmp/r18/g/t18_o2.py`). Two scenarios:
1. **D1:** a world screen and two steps each pressing UP into a wall with no response. Assert the tile's `blocked` contains UP after the second step.
2. **D2:** a non-direction press starts a scripted walk of 1R+10D, an instant/fade cut, then 8U, ending on a perceived dialogue screen; the next 2 steps are dialogue; then a world screen of a new, unseen place.
   - The source place gains tiles only on the pre-cut path, ending at the exit tile; none from the post-cut 8U walk.
   - New place pos (0,0), entrance exit tile (0,8) with dir DOWN, exactly one crossing source@exit>new.
   - Re-running from a checkpoint saved during the dialogue gives the same result.
Print PASS/FAIL per assertion. Expected: D1 red before F1 and green after; D2 red until F2.

### F2
- **Tests** (`test_world.py`; `test_loop.py` only if a loop change is made):
  1. the LV18 shape as a unit (segments pre-cut 1R+10D, cut, post-cut 8U; after-screen dialogue; `next_place == place`): no source tiles after the cut; then `before()` with a new place gives pos (0,0), entrance (0,8) DOWN, one crossing;
  2. the deferred match returns the same place: the held segments fold into the source, giving the same tiles as today's immediate fold;
  3. drains on intermediate dialogue steps go into `_held`, never into the source;
  4. `state()`/`restore()` round-trip with `_held` set, then resolve gives the same result;
  5. `unseen_cross` adds nothing while held; the crossing count is exactly 1.
- **Pre-approved assertion changes:** `test_world_golden.py` may differ only on steps where a cut ended on a non-world screen (`tile` values and tiles/crossings written after that cut); the orchestrator alone regenerates it with `W0_REGEN=1`. Any other failing assertion: stop, revert, report.

## 4. Gates
| Gate | Pass | Command |
|---|---|---|
| T-F1 | suite green; T18F D1 PASS | `pytest -n auto -q`; `python .tmp/r18fix/g/t18f.py` |
| T-F2 | suite green; T18F D1 + D2 PASS | same |
| G18F | merged HEAD: full suite green (baseline 885 passed, 1 skipped + new tests); T18F all PASS; T18-O2 (`.tmp/r18/g/t18_o2.py`) and T18-O3 (`scripts/check_odometer.py --run runs/m1_r17_boot`) unchanged | orchestrator |

## 5. LV18b (one live run, orchestrator only, after G18F)
- **Ledger:** 10033 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap 200 calls; **stop if the ledger would pass 10233**.
- **Viewer** up (`curl -s http://127.0.0.1:8765/state`), else `python scripts/watch_run.py --no-browser` in the background.
- **Run:** copy `runs/m1_r17_boot` to `runs/m1_r18b_odo` (never resume the original), then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18b_odo --resume --from-step 65 --steps 60 --max-calls 200`.
- **Metrics** (truth[N+1] vs tile[N], world-screen steps; reuse `.tmp/r18/lv18/a_metrics.py`, `c_regress.py`):
  1. one RAM−tile offset per (place, map) visit;
  2. step 69: Pallet's step-69 tiles end at the exit tile, and the post-cut column LV18 wrote at p_place_4 (7,5)…(7,-3) is absent; one crossing Pallet→lab; the lab's entrance exit tile maps to RAM (5,11);
  3. the agent leaves the lab through the bottom door within 15 world steps after it is free to move, or the report says why (options offered, choices, skill results);
  4. walls recorded on repeated no-response; no-response streaks vs LV18 (17) and r17 (1);
  5. odometer totals: uncertain, gap, dropped.
- LV18b informs; it does not gate the commits.

## 6. Standing rules (copy into every lane block)
1. Ponytail (full): reuse > stdlib > minimal code. Match the dense one-line style. Mark known ceilings `# ponytail:`. Deletion over addition.
2. Run `codegraph explore "<symbols>"` (or the codegraph MCP) before grep/Read. Summarise big outputs; never paste whole files back.
3. Python: `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`. Always pytest with `-n auto` (pyproject default). Read pytest's own summary line.
4. agent/: no game words, no RAM reads (`tests/agent/test_boundaries.py`); no new gates, filters, hidden option rows or modules; follow the contract text in `docs/plans/contracts.md` (r18 + §2.3) exactly. Never edit `contracts.md`; propose changes in the report.
5. Off-limits, never staged or edited: `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `runs/e3_budget.json`; everything under `docs/plans/` in lanes; `runs/` is read-only; never junctions or symlinks; never link `runs/` or `Rom/` into worktrees; never `git worktree remove --force`.
6. Zero model requests in lanes. Never run `scripts/run_agent.py`. Offline PyBoy loads of checkpoint states are allowed, read-only, `window="null"`.
7. Assertions: change an existing assertion only if §3 pre-approves it, and report it before/after. Any other failure: stop the WP, revert its uncommitted edits, report. Never set `W0_REGEN` (orchestrator only).
8. Git, in your worktree only: one commit per WP, `<WP> <one-line result>`, one-line body, your `Co-Authored-By:` trailer; stage explicit paths, never `git add -A`; never push, rebase, amend or switch branches.
9. Scratch to `C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r18fix\<WP>\`; gate scripts to `.tmp\r18fix\g\`. Both uncommitted.

## 7. Orchestrator protocol
**Tier:** you are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, one WP each. Never another model. You review diffs, run gates, merge, regenerate goldens and run LV18b; subagents never do.

### Wave 0 (main checkout)
1. `git status`. Expected: modified `scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`; untracked `baseline_eval.txt`, `scripts/frames_combo.json`, `docs/plans/execute-r18-fix.md`, plus `.tmp/` content. Anything else: stop and ask.
2. Apply §2.3 to `docs/plans/contracts.md`; commit it with `docs/plans/execute-r18-fix.md` as `docs: r18-fix plan and approved contract amendments`.
3. Run the suite; baseline **885 passed, 1 skipped**. Record the ledger.

### Wave 1: F1 and F3 in parallel
- Worktrees `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r18fF1` (`-b r18f/F1`) and `...\r18fF3` (`-b r18f/F3`, scratch only; no commit expected), both from the wave-0 commit.
- F1: review the diff stays in owned files; run T-F1; one repair round with exact failing output, stop if it fails twice; merge `--no-ff`.
- F3: copy `t18f.py` into the main checkout's `.tmp/r18fix/g/`; run it on merged F1: D1 must PASS and D2 FAIL. If D1 fails, or D2 passes, review the script against §2 before going on.

### Wave 2: F2
- Worktree `r18fF2` from merged F1. Review, run T-F2, merge `--no-ff`.
- **G18F** on the merged HEAD. Regenerate the golden only within F2's allowed class, review the data diff, `git add -f tests/agent/data/w0_golden`, commit `F2 golden: held post-cut motion`.

### LV18b
Per §5.

### Final report
Save to `.tmp/r18fix/report.md` and give it to the user: each WP (commit, tests added, assertions changed before/after, lines ±), the gate table with commands and numbers, the LV18b metrics vs LV18 and r17, deviations, proposed contract changes, open issues. Commit `docs/plans/handoff-notes.md` (under 80 lines, current as of r18-fix) as `docs: r18-fix result`.

### Stop and ask the user when
- a §2 design point cannot work as written (in particular: the cut-bearing segment index in `after()` does not separate pre- from post-cut motion on the LV18 shape);
- any non-pre-approved assertion fails after one repair;
- a golden diff falls outside the allowed class;
- T18-O2 or T18-O3 results change;
- the ledger would pass 10233;
- `git status` differs from wave 0's expectation;
- a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### F1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix, WP F1 (no-response presses reach space.fold). Worktree: <WORKTREE> (branch r18f/F1 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix.md §1, §2.1, §2.3, §3 F1, §6; agent/world.py _odometer_drain and _odometer_fold; agent/space.py fold; tests/agent/test_space.py no-response tests (around line 333).
Build exactly §2.1: one extra branch in _odometer_fold's kind == 1 handling. Add the §3 F1 tests in tests/agent/test_world.py.
Pre-approved assertion changes: none. Any failing existing assertion: stop, revert uncommitted edits, report.
Rules: [paste §6 rules 1-9 verbatim]
Done when: suite green; one commit "F1 no-response presses reach fold"; report the diff per file, tests added, and the suite summary line.
```

### F3 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix, WP F3 (offline gate script T18F). Worktree: <WORKTREE> (branch r18f/F3 from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix.md §1, §2 (all), §3 F3, §6; .tmp/r18/g/t18_o2.py (pattern); tests/agent/fakes.py (GridEnv, StubModel); agent/loop.py Loop.step; agent/world.py before/prepare/after/_odometer_fold.
Write .tmp/r18fix/g/t18f.py with the two §3 F3 scenarios. It must make zero model requests, print PASS/FAIL per assertion, and exit non-zero on any FAIL. It must run against the current tree (expected: D1 FAIL, D2 FAIL) without editing agent/ or tests/.
Rules: [paste §6 rules 1-9 verbatim]
Done when: the script runs and fails only on the defect assertions; report the script path, its output on <BASE>, and why each FAIL is the defect and not the harness.
```

### F2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix, WP F2 (hold post-cut motion until the place is re-identified). Worktree: <WORKTREE> (branch r18f/F2 from <BASE>, which includes F1). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix.md §1, §2.2, §2.3, §3 F2, §6; .tmp/r18/lv18/fix-proposal.md; agent/world.py before, prepare, after, _odometer_drain, _odometer_fold, unseen_cross, _locate, _cross, state, restore; agent/space.py mark_exit; agent/memory.py match_place; agent/loop.py lines around the before-match (odometer_start, match_place, unseen_cross, world.before) and after-match (match_place, world.after, cut_carry).
Build exactly §2.2 (open in after(), divert in _odometer_fold, resolve in before(), same-place flush, unseen_cross skip, state/restore). match_place stays unchanged. Add the 5 §3 F2 tests. Run .tmp/r18fix/g/t18f.py from the main checkout's .tmp against your worktree (PYTHONPATH=<WORKTREE>); D1 and D2 must PASS.
Pre-approved assertion changes are exactly §3 F2 (golden only, left to the orchestrator). Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste §6 rules 1-9 verbatim]
Done when: suite green except a golden diff in the allowed class (reported); T18F all PASS; one commit "F2 hold post-cut motion until the place is re-identified"; report the diff per file, tests added, golden diff summary, and any §2.2 point you could not do as written.
```
