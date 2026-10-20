# R4. Feedback and control loop (Phase A approved 10-15; Phase B packet in §7)

Evidence: `runs/m1_bedroom_live`. Truth join trace S ↔ truth S+1. All numbers come from offline replay or emulator-only probes. No model calls were made. Scratch: the session scratchpad (`r4_rows.txt`, `settle_*.py`).

## 1. Mechanism: the missing invariant

**Invariant missing: an action's result is not measured by what it *revealed or changed in the world*.** Each step is scored by a local prediction match (`verdict`) and by the skill's own success test, and nothing ties the two to progress. So:

- **Results are scored wrong in both directions.**
  - 14 of 18 `surprise` rows were real failures with an unchanged position (S55, 65, 67, 69, 71, 73, 78, 81, 83-85, 87, 102, 105). The cause is VLM entity churn in `predict.py:79-81`.
  - 6 `hit`s changed nothing.
  - S100 walked 6 tiles north, (2,14)→(3,8), revealing new rows, and was recorded as a failure ("exit not crossed"). The "exit: north" target is a VLM label at the top screen edge. A scrolling map has no cut there, so the crossing stage (`skills.py:97-145`) fails by definition.
- **Steps never settle where the water is visible (root cause found).** `wait_settled` (`observer.py:65`, both the stepped and the new continuous path; 3 `deque(maxlen=quiet)` sites) calls a frame quiet only if its hash recurs within the last `quiet`=24 frames. Pallet water changes every ~21 frames, and its state cycle is ~149 frames.
  - Emulator probe from checkpoint 100, walking south: y=8-9 settles in 60-84 frames; y≥10 times out at 600 frames, every time.
  - This explains all 15 unsettled rows: S93-105 (y≥9), plus intro S3-6.
  - Each timeout costs 600 frames per press. The HF hold machinery added 6 pending rows and 3 no-call retry rows (S94, S104).
- **Granularity is one tile.**
  - `frontier()` (`space.py:59`) is the edge of the *walked* tiles. `_unexplored` adds one step past it, so every explore walks onto one new tile and stops (`_unexplored`, now `loop.py:486`). 22 of 26 explore picks pressed once.
  - 8 explore picks did not move at all (S78, 81, 83-85, 87: route blocked on the first press).
  - Each pick costs a ~5.3k-char act call plus perception. 75 calls in S76-105 for 18 distinct tiles.
- **Two walkers.**
  - On-screen `{"thing": i}` options (all on-screen exits) skip the tile route (`loop.py:221`). They use the greedy walker (`skills.py:97-135`) plus a 6-press side-step push.
  - S67, 68, 71, 72: 30 inputs each, a zero-net RIGHT,UP,RIGHT,LEFT,UP,LEFT cycle against a wall.
  - Wall "exits" are never refuted, because `_failed_exit` (`world.py:198-204`) needs a floor tile at the exit itself.
- **Nobody reacts to "no progress" in time, and when someone did, it was the wrong level.** Git: GP had no failure trigger. `1fd572d` added unthrottled "urgent" replans, and `e5420d3` added "new failure". In m1_hf_checkpoint_1, 24 of 41 plan calls were single-failure urgents (quest-state.md:140). Dedup was one slot keyed on churning ids, so every failure looked new. The planner cannot fix a tactical failure and answered with button-level goals. `266681e` removed it all, which leaves only T4 at 20 steps (fired S99).
  - Oscillation cause: **a strategic reactor was wired to a tactical, noisy signal.** It was not the reaction itself.

## 2. Solution

One novelty measure, one walker, one settle rule. Tactical failure goes to the act model, stall goes to the planner.

1. **Result replaces verdict.** `result ∈ {progress, changed, no_change, failed}` is computed from code-owned evidence only:
   - `progress`: any of new seen cells (`_record_view`'s `added`, `world.py:228`), a first world place, a first verified edge, or a quest closed.
   - `failed`: skill blocked/failed with no progress.
   - `no_change`: pixel no change.
   - `changed`: anything else.
   - Novelty wins over the skill's own success test. S100 becomes `progress`.
   - Entity lists never enter the result. Text stays out of progress (QS-R F3 kept) because it already raises T1.
2. **Stall is counted in decisions, not steps.** `Stuck` keeps one counter: act decisions since the last `progress`. T4 fires at `K·2^u` decisions, where `u` = consecutive stall replans that changed nothing (reset by progress). K is set by the replay (§7 R4.2). Until R2 lands, `u` reads today's `_apply_plan` result; R2's wiring WP then removes `stall_unchanged` and the injected `explore_untried_exit` quest and feeds `u` from `apply_ops` (zero `ok` = changed nothing).
3. **Tactical failure stays at the act level.** The planner is never called for a single failure. The per-intent `fails` record (R1 key) plus R3 presentation is the retry/fallback loop, like a behaviour-tree fallback or Voyager's per-task retry. The planner sees failures only inside the T4 evidence packet. No new gate: every option stays pickable.
4. **Explore walks until something new is seen.**
   - `_unexplored(place, dir=None)` routes to the nearest reachable cell at the *seen* boundary (`open_cells`/unseen, not the walked frontier). It then continues straight while presses move, until a cut, a block, or a ~half-screen cap.
   - One explore option per open direction is offered (R3 decides the rows). This also absorbs screen-edge pseudo-exits ("exit: north/east", "water edge").
5. **One walker.**
   - Every on-screen approach uses the tile route that `go_to` already uses (`_screen_route`). The greedy walk runs only before `tile_px` is known, to calibrate it.
   - Exit push = the route's single push press. The 6-press side-step sequence is deleted.
   - R1 owns the refuted-exit rule (`_failed_exit`). R4 guarantees every blocked push lands in the tile's `blocked` via `fold`, which R1's rule reads.
6. **Settle:** recurrence memory is decoupled from the quiet run: `deque(maxlen=256)`, `quiet` 24 unchanged.
   - Probe: water views settle in 143-480 frames; intro/bedroom/house are unchanged (25/24/63 frames).
   - The hold/resolve machinery is then deleted. On a genuine timeout the loop acts on the latest frame, and the row keeps `observation_status: timed_out`.

Why this is root-level: it fixes the one measurement every consumer reads (act notes, stall, planner evidence, trace). It also removes the step-sized units that forced patches (pending rows, relook, cycle flags, fallback quest).

Rejected, one line each:
- Per-failure replan, even deduped per intent: the planner can't fix tactics (GP/HF evidence).
- Keep verdict and filter entity churn: a filter on noise, and still measures prediction, not progress.
- Raise or lower the 20-step T4 only: wrong unit and wrong novelty.
- Lower `quiet` to 16: breaks on any fast-interval long-cycle animation.
- A learned animation mask: more code than a longer deque.

## 3. Deletions (approx. lines)

- `predict.py`: `predict()` and `verdict()`, ~45. `effects()` loses `entity_appears/disappears`, ~6 (model-basis, read only by verdict).
- `loop.py`:
  - pending/hold machinery: `_resolve`, `_degraded`, `_hold_action`, `_pre_hold`, `_hold_row`, `_summary`, `_skill_from`, `pending`/`unsettled` state and the `resolved` paths, ~75
  - `relook`/`reperception_used`, ~6 (1 call in the run)
  - `reported` (written, never read), ~4
  - `cycle_flag` + `moves` cycle detection, ~12 (subsumed by stall; exit "beyond" notes show where an exit leads)
  - `stall_unchanged` + fallback quest, ~12 (R2's wiring WP, after R4)
  - pred/contradiction relation writes, ~8
  - `failure_cause`/`cause`, ~10 (R3, with the act flags it feeds)
  - `_migrate`/`_old_events`, ~25 (ruled: dropped)
- `tasks.py`: `n`, `same_action`, `repetitions`, `hash`, `action`, ~35 (only stall remains, ~15 lines).
- `skills.py`: exit side-step sequence, ~8. The greedy walker is limited to the uncalibrated case; full deletion is possible later.
- `memory.py`: `relate()` pattern accounting, `patterns` rows and `conf`, ~45 (R4, after R1's hunks). `Memory.view` is R1's.
- `act.py`: `expect` and the EFFECTS import (R3, with the free path).
- `eval_run.py`: `prediction_accuracy` and `_repredict`, ~60 (coordinator S0; a dependency of R4.7).
- Net: `loop.py` 656 (with the live-view edits) → ~480 before R1-R3 hunks. Step structure: `settle → observe → maybe_plan → decide → resolve intent → execute → settle → observe → result → record`.

## 4. Interfaces

- **R4 gives:**
  - `result` + `reason` per row (trace key `verdict` kept, domain changed) and `fails[intent] = {n, why, step, stage}`
  - `World.explore_dirs(place) -> {dir: route}`
  - T4 with backoff and the evidence packet's "failed attempts / tried" lines, from results
  - `observer.wait_settled` memory window
- **R4 needs from R1:** a stable intent key `(place, verb, target id)` and stable exit/thing ids, because fails and result notes hang on them. R1 owns `match_place`/naming hunks inside `step()`. I need the identity calls to stay single call sites.
- **R4 needs from R2:** the planner consumes T4 text plus the evidence packet unchanged in shape. `_apply_plan`'s stall-unchanged return (quests/beliefs changed?) feeds the backoff `u`. The fallback-quest injection is deleted, so R2 should confirm.
- **R4 needs from R3:**
  - present `fails` / last result per option (failed rows)
  - merge screen-edge pseudo-exits in scrolling places into explore-direction rows
  - drop `expect` from the act schema
  - drop the `recent:` run-summary and `verdict` act flags, or rebuild them from `result`
- **Contract changes:**
  - trace: `verdict` key kept (ruled Q5) with domain `progress|changed|no_change|failed|unobservable`; `pred` null, `used` []; `observation_status` ∈ `settled|timed_out` (no `pending`; HF-5 replaced)
  - predict: `predict`/`verdict` removed
  - tasks: `Stuck.update` returns only `no_progress`; state drops same_action/repetitions
  - observer: `wait_settled(..., memory=256)`
  - skills: exit push is one press

## 5. Scorecard effect and offline verification

- **Moves:**
  - steps from house 1F to Pallet (fewer 30-input bumps; refuted wall exits)
  - failed-option re-picks: with R3, the results are no longer hidden as `surprise`
  - act calls per new tile (75 calls / 18 tiles in S76-105)
  - unsettled rows (15 → ~0)
  - plan calls stay ≤1 per K decisions in stalls
- **Offline checks:**
  1. Settle probe script → assertion test with a recorded frame sequence. No ROM in CI, so frames are cached.
  2. Replay `m1_bedroom_live` trace rows through the new `result` function (frames + perception cached). Expect S100 = progress, the 14 unchanged-position surprises = failed, and 0 `surprise` labels.
  3. Re-run `Stuck` over the replayed results. Report the T4 steps (expect a first fire in S64-73 and one in Pallet after the frontier is exhausted, not S99).
  4. A grid-env test: explore from a seed reveals ≥ a screen of cells in 1 decision.
  5. Grid-env test: a wall exit is refuted after one blocked push.
  6. `pytest -n auto` with the stub model.

## 6. Risks and open questions for the coordinator

1. K=8 with backoff is a guess. The replay in check 3 sets it, so approve the method, not the number.
2. Progress from seen cells means a pure wanderer never stalls until the place is seen. Intended (exploration is progress), but the planner then wakes later in open areas.
3. Deleting the hold machinery reverses HF contract 5. Evidence: in this run, all 15 hold/timeout rows came from the settle bug.
4. Old-checkpoint migration (`_migrate`, `_old_events`): keep or drop? RCR changes state shape anyway.
5. The `verdict` key is kept with a new domain to spare `watch_run.py` (dirty in the working tree) and eval scripts. Or rename to `result`?
6. Continuous live view (uncommitted, `docs/plans/continuous-live-view.md`): `wait_settled` gained a wall-clock path (deadline `max_frames/60` s), so the water bug now costs 10 s per settle and per press. The fix (memory window) applies to all 3 deque sites. Result scoring uses the skill's own before/after frames, so observation age during model calls does not affect it. Phase B must not touch the live-view hunks.
7. The arrival-tile phantom exit (`world.py:536,554`) is exit evidence but labelled R4 in the audit. I suggest R1 (exit identity) take it.

Rulings (coordinator, 10-15):
- Q1: method approved; K comes from the replay.
- Q2: accepted.
- Q3: hold machinery deleted; HF-5 is replaced at the gate.
- Q4: migrations dropped.
- Q5: the `verdict` key is kept with the new domain.
- Q6: live-view hunks untouched. Superseded by the user: Phase C builds on the uncommitted live-view edits and preserves every hunk.
- Q7: R1 owns arrival credit and `_failed_exit`.

## 7. Phase B packet: R4 implementation

Status: **gate passed in the working tree (648 tests)** · Branch: `feat/lean-agent` · Orchestrator: this session; every worker explicitly `gpt-6-luna`, reasoning **HIGH** (user override). Cloud calls: **0** (StubModel, cached frames, ROM-only probes). LIVE files stay unstaged; final evidence is in §7.9.

### Paste block

    You are the orchestrator for RCR section R4 (feedback and control loop), repo
    C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent.
    1. Read in order: CLAUDE.md, docs/plans/handoff-notes.md, docs/plans/rcr/MASTER.md (§2, §3, §7),
       docs/plans/contracts.md (#observer #skills #trace #predict #tasks), docs/plans/rcr/R4-feedback.md.
    2. Check the entry criteria in R4-feedback.md §7.1. If any fails, stop and report.
    3. Run the WPs in §7.5 run order. Delegate each to a Haiku subagent; paste the CLAUDE.md rules
       (ponytail full, codegraph first, headroom) and the WP card. Run each WP's acceptance yourself.
    4. Stay in the files a card lists. Each WP re-reads its files before editing; the working tree carries the
       user's uncommitted live-view edits. Never stage, revert or edit those hunks (MASTER §2, §7.1 below).
       Never edit contracts.md: put the proposal in §7.9 for the coordinator.
    5. Run the gate (§7.2), fill §7.9, report net lines per file. Commit only the §7.9 commit list plus
       docs/plans/rcr/R4-feedback.md, message "R4: <gate result>". Never `git add` a file marked LIVE in that
       list unless the coordinator has asked the user. Never `git add -A`, never push.

### 7.1 Entry criteria
- Build on the user's uncommitted continuous-live-view edits (user decision, MASTER §3). They carry hunks in `agent/loop.py`, `observer.py`, `model.py`, `track.py`, `env_pyboy.py` and in `tests/agent/test_loop.py`, `test_observer.py`, `test_track.py`, `test_watch_run.py`, `test_model.py`, `test_run_provenance.py`. Before each WP, save `git diff -- <its files>` to `.tmp/r4/live-<wp>.diff`; after it, every live-view hunk must still be present.
- Coordinator S0 has landed (scorecard in `eval_run.py`).
- R1's hunks in `memory.py`, `world.py` and `loop.py` have landed (MASTER §3). R4.1-R4.3 touch only R4 files and may start at once.
- Present: `.venv-agent`, `Rom/*.gb`, `runs/m1_bedroom_live` (trace, truth, `checkpoint/000100/pyboy.state`).
- Every shell: `New-Item -ItemType Directory -Force .tmp/r4/tmp | Out-Null; $env:TEMP = "$PWD\.tmp\r4\tmp"; $env:TMP = $env:TEMP`.
- Python: `.venv-agent/Scripts/python.exe -B`. Pytest flags: `-q -p no:cacheprovider -n auto`.
- `LIVE` (run in every WP's acceptance, both must exit 0):
  - `pytest tests/agent/test_realtime_env.py -q -p no:cacheprovider -n auto`
  - `pytest tests/agent/test_observer.py tests/agent/test_track.py tests/agent/test_watch_run.py tests/agent/test_loop.py tests/agent/test_model.py -k live -q -p no:cacheprovider -n auto`
- Baseline (10-15, before any R4 edit): the second command passes 12/12. The first had 1 flaky failure under `-n auto` (`test_state_load_changes_generation_and_wakes_frame_waiter`), which passed serially 2/2 and alone. If only that test fails, rerun it once with `-n 0`, and record it in §7.9 when it passes there.
- Live-view code R4 must keep working:
  - `Loop.invalidate_live_observation` (reads `pending`, `unsettled`, `reperception_used`);
  - the stale-observation path in `step()`, which writes a no-input `_hold_row(..., observation_status="stale")`;
  - `_observation_fields`, `_live_observation_stale`, the `observation_stamp` in the action state;
  - continuous `wait_settled` (`snapshot`/`wait_frame`, wall-clock deadline);
  - `ModelCancelled` propagating out of `step()` through the `except BaseException: ...; raise` handlers.

### 7.2 Gate
| Metric | Pass | Command |
|---|---|---|
| S6 real failures not scored failed | 0 of 13 (23 surprises; `moved` from the before-frame step); S100, S105 = `progress`; 0 `surprise` | `pytest tests/agent/test_predict.py -k m1` |
| S11 settle | synthetic 8-state/21-frame cycle settles on both env paths; ROM probe at y≥10 settles (must run, not skip) | `pytest tests/agent/test_observer.py -rs` (no `SKIPPED` line) |
| K | fire steps for K∈{6,8,10,12} recorded in §7.9; chosen K = largest that fires inside S64-73 | `pytest tests/agent/test_tasks.py -k replay` |
| Explore granularity | one decision walks ≥ 4 tiles and adds ≥ one view row of seen cells | `pytest tests/agent/test_loop.py -k explore` |
| One walker | on-screen exit on a wall: inputs ≤ route+1, no side-steps, `blocked` holds the push dir | `pytest tests/agent/test_skills.py tests/agent/test_loop.py -k "walker or push"` |
| Hold removed | an unsettled after-frame gives one normal row, `observation_status: timed_out`, no `pending` row | `pytest tests/agent/test_loop.py -k timed_out` |
| Suite | green | `pytest tests/agent -q -p no:cacheprovider -n auto` |
| Lines | R4 hunks net negative; report per file (planned ≈ -250) | `git diff --stat` over the R4 commits |

- **If S6 or S11 fails:** stop and report. Do not tune thresholds.
- **If K never fires in S64-73:** record all four fire lists and keep K=8 for the coordinator to rule.

### 7.3 Read list
- `agent/loop.py`: `step`, `_settle`, `_unexplored`, `_screen_route`, the hold methods, `state`/`restore`.
- `agent/tasks.py`, `agent/predict.py`.
- `agent/skills.py`: `approach`.
- `agent/observer.py`: `wait_settled`.
- `agent/world.py`: `_record_view`, `open_cells`, `unseen_edges`, `occupied`, the explore row in `options`.
- `agent/space.py`: `route`, `fold`, `frontier`.
- `agent/memory.py`: `relate`, `conf`.
- Use `codegraph_explore` with these names first.

### 7.4 Contracts
- **Uses:** contracts.md #observer #skills #trace #predict #tasks; MASTER §7 rows R1→R4, R2→R4.
- **Provides (proposal in §7.9):**
  - the trace `verdict` domain;
  - `predict.result()` in place of `predict`/`verdict`;
  - `Stuck` with backoff;
  - `wait_settled(memory=256)`;
  - exit push is one press;
  - HF-5 replaced;
  - `World.explore_dirs`.

### 7.5 Work packages

| WP | Goal | Owns files (net lines) | Depends on | Tier |
|---|---|---|---|---|
| R4.1 | settle memory window | `observer.py` (±3), `test_observer.py` (+35) | — | Haiku |
| R4.2 | `result()`, `Stuck` with backoff, m1 fixture | `predict.py` (+12), `tasks.py` (60→≤30), `test_predict.py`, `test_tasks.py`, `tests/agent/data/r4_m1_rows.jsonl` | — | Haiku |
| R4.3 | one push press | `skills.py` (-8), `test_skills.py` | — | Haiku |
| R4.4 | `World.explore_dirs` and novelty count | `world.py` (+30), `test_world.py` | R1 world.py | Haiku |
| R4.5a | delete the hold machinery | `loop.py` (-80), `test_loop.py` | R1 loop.py, R4.1 | Haiku |
| R4.5b | delete relook, `reported`, cycle detection, migrations | `loop.py` (-50), `test_loop.py`; `test_notebook.py`/`test_om_eval.py` only where they call loop migrations | R4.5a | Haiku |
| R4.5c | one walker and explore wiring | `loop.py` (-15), `test_loop.py` | R4.3, R4.4, R4.5b | Haiku |
| R4.5d | result and Stuck wiring; golden regen | `loop.py` (-20), `test_loop.py`, `tests/agent/data/w0_golden/act/trace.jsonl` | R4.2, R4.5c | Haiku |
| R4.6 | delete `predict`/`verdict`/patterns | `predict.py` (-45), `memory.py` (-45), `test_predict.py`, `test_patterns.py` (delete) | R4.5d, R1 memory.py, S0 removed `_repredict` | Haiku |
| R4.7 | gate, contract proposal, Result | `R4-feedback.md` | all | orchestrator |

**Run order:**
1. R4.1 ‖ R4.2 ‖ R4.3
2. R4.4
3. R4.5a → R4.5b → R4.5c → R4.5d. The `loop.py` WPs run one at a time.
4. R4.6s → R4.6
5. R4.7

R2's `loop.py` wiring WP may start once R4.5d lands; its slot is `_after_outcome` and the `stuck:` branch after `_apply_plan` (MASTER §3).

#### R4.1 Settle memory window
- **Goal:** a slow background cycle (water: a change every ~21 frames, period ~149) no longer blocks settling.
- **Change:**
  - `wait_settled(env, *, quiet=24, max_frames=600, memory=256)`.
  - All three `deque(..., maxlen=quiet)` sites use `maxlen=max(quiet, memory)`: the continuous path, its generation reset, and the stepped path.
  - Nothing else; the live-view logic around them stays as is.
- **Tests:**
  - A fake env whose frame cycles through 8 states, switching every 21 ticks, on both the `tick/screenshot` and the `snapshot/wait_frame` paths. It settles with defaults and does not settle with `memory=24`.
  - A ROM probe, skipped only when `Rom/*.gb` or the checkpoint is missing: load `runs/m1_bedroom_live/checkpoint/000100/pyboy.state` headless, press DOWN ×3 (y≥10), assert `settled`.
- **Acceptance:** `pytest tests/agent/test_observer.py tests/agent/test_skills.py tests/agent/test_type_text.py -rs -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.2 Result and Stuck
- **Goal:** one novelty-based result, and a stall counted in decisions with backoff.
- **Change `predict.py`:** add `result(status, effects, novel) -> str`. The checks run in this order:
  1. `novel` gives `progress`.
  2. Status `blocked` or `failed` gives `failed`.
  3. `effects == ["no_change"]` gives `no_change`.
  4. Anything else gives `changed`.

  Leave `predict`/`verdict` in place; R4.6 removes them.
- **Change `tasks.py` (`Stuck`):**
  - Keep `progress_n`, `seen_places`, `seen_edges`; add `u`. Delete `n`, `action`, `hash`, `same_action`, `repetitions`.
  - `update(row)` on a world screen: a first place, a first edge or `row["verdict"] == "progress"` calls `progress()`; anything else adds 1. Off the world screen it is unchanged: `fresh_text is False` adds 1.
  - It fires `"no_progress"` at `progress_n >= STALL * 2**u`, then resets `progress_n`.
  - `progress()` zeroes `progress_n` and `u`. `replanned(changed)` sets `u = 0 if changed else min(u + 1, 3)`.
  - `STALL` is a module knob carrying `# ponytail: set from one run (m1_bedroom_live: the largest K that still fires inside S64-73); a knob, not re-tuned per run`.
  - State keys: `progress_n`, `seen_places`, `seen_edges`, `u`.
- **Fixture:** `tests/agent/data/r4_m1_rows.jsonl`, one line per trace row of `runs/m1_bedroom_live`.
  - Fields: `step`, `screen`, `place`, `next_place`, `verified_edge`, `scroll`, `skill.status`, `outcome.effects`, `verdict`.
  - Plus `moved`, which is eval-only and never read by agent code: truth at the row's before-frame step (the number in `frame`, e.g. `frames/000103.png` → 103) vs truth at S+1. A resolved-hold row (S105, before-frame 103) scores the S103 action, not S105.
  - Record the one-off extraction command in §7.9.
- **Tests:**
  - S6: for all 23 `surprise` rows, compute `result(status, effects − entity_*, novel = scroll ≠ 0 or next_place ≠ place)`. The 13 unmoved rows give `failed`, S100 and S105 give `progress`, and no row gives `surprise`. (Amended 10-15 after the first attempt: the packet's "18/14" counted from S55 and scored S105 by its own step; S105 resolves the held S103 action, LEFT DOWN, which moved (3,9)→(2,10).)
  - Replay: run `Stuck` over every row with that result. Record the fire steps for K∈{6,8,10,12} and assert the chosen K's list.
  - Unit tests: backoff, cap, reset on progress.
- **Acceptance:** `pytest tests/agent/test_predict.py tests/agent/test_tasks.py -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.3 One push press
- **Goal:** an exit push is one press, and blocked pushes are recorded.
- **Change `approach`:**
  - Delete the side-step loop (`side = ...` and its `for` loop).
  - With a route and an exit target, press `route["push"]` once. Without a route, the greedy path's own single push (`pushed`) stays.
  - Every press stays in `moves`, so `World.prepare` → `fold` records a `(0, 0)` push in `blocked`.
- **Tests:** a route to a wall exit takes `len(dirs) + 1` inputs, and its last move is `(push, (0, 0))`. Update the side-step tests to the new count.
- **Acceptance:** `pytest tests/agent/test_skills.py tests/agent/test_exits.py tests/agent/test_space.py -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.4 `World.explore_dirs` and the novelty count
- **Goal:** explore walks to the edge of what has been seen and keeps going, and the world reports how much of each view is new.
- **Change `world.py`:**
  - Add `explore_dirs(place) -> {dir: {"dirs": [...], "to": (x, y)}}`.
  - Candidates are `open_cells` plus unseen cells next to a seen floor cell. Group them by their dominant direction from `self.pos`.
  - For each direction, take the shortest `route(..., occupied=self.occupied(place))` to a candidate. Then append that direction ×(half the view span in that axis, from `self.size // tile_px`; default 4).
  - Return `{}` when there is no `tile_px`.
  - In `_record_view`, set `self.novel = len(added)` on every world view, and 0 otherwise. `Loop` reads it after `observe_after`.
- **Tests:** on a seeded grid place, `explore_dirs` gives a route through the seen boundary plus the extension. `novel` counts only cells not seen before.
- **Acceptance:** `pytest tests/agent/test_world.py tests/agent/test_options.py -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.5a Delete the hold machinery
- **Change `loop.py`:**
  - Delete `_resolve`, `_degraded`, `_hold_action`, `_pre_hold`, `_summary` and `_skill_from`.
  - **Keep `_hold_row`.** The live stale-observation path uses it as its no-input row writer; it becomes that path's only caller.
  - Stop *using* `self.pending` and `self.unsettled`. Keep both as inert attributes in `__init__` (`None`, `0`) with `# ponytail: read only by invalidate_live_observation (live view); remove with it`, so the live-view hunk runs unchanged and its test keeps passing.
  - Delete the `resolved` parameter and its branches in `_settle`.
  - Remove the two early returns in `step()` (`_resolve`, `_pre_hold`), and these fields from `state`/`restore`.
  - `step()` always calls `_settle` with the latest frame. The row keeps `observation_status` as `settled` or `timed_out`.
  - Keep every live-view line (`observation_stamp`, `observation_age_ms`, `frame_stamp`, the `"observation_stamp"` key in the action state, `_observation_fields` in rows). If a live-view hunk calls a deleted symbol, stop and report.
- **Tests:** replace the ~10 pending/unsettled tests (`test_loop.py` ~1743-2068, plus `test_live_resume_invalidation_clears_pending...`) with one test: an unsettled after-frame gives one row, `timed_out`, with a normal tail.
- **Also owns (added 10-15):** `tests/agent/test_harness_reliability.py`, case 5 only (`crossing_run` and its test). With no hold, step 1 is the whole action row. Checkpoint after that row, restore into a new Loop, and drop the `while loop.pending` loop. Assert one row with `observation_status` in (`settled`, `timed_out`) and `execution: done`. Keep the checks that the keys are `["RIGHT", "RIGHT"]` in both runs with no replay, that `fails == {}`, that the crossings are equal and `[1]`, and that pos/place/player are equal after restore. Delete `resumed.pending is None` and rename the test to drop "pending".
- **Acceptance:** `pytest tests/agent/test_loop.py tests/agent/test_harness_reliability.py tests/agent/test_run_provenance.py -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.5b Delete relook, `reported`, cycle detection and migrations
- **Change `loop.py`:**
  - Delete `relook` and `_see`'s `relook` parameter with its prompt line. `reperception_used` stays only as an inert `__init__` attribute (the live-view invalidation resets it), with the same `ponytail:` comment as in R4.5a.
  - Delete `self.reported`.
  - Delete the A↔B cycle block in `_moved` and `cycle_flag`. Keep `self.moves`, which R2's evidence reads.
  - Delete `_migrate` and `_old_events`; `restore()` reads current keys only.
  - Remove the `self.stuck.n` reads (the "no effect xN" prefix of the recent flag).
  - Otherwise leave `_apply_plan`, `_evidence` and the act flags untouched; they belong to R2 and R3.
- **Tests:**
  - Delete the tests for these: relook, the three cycle tests near `test_loop.py` 1140-1206, and migrations.
  - `test_options.py::test_fail_notes..._mark_cycles` keeps only its fail-note part.
- **Acceptance:** `pytest tests/agent/test_loop.py tests/agent/test_options.py tests/agent/test_notebook.py tests/agent/test_om_eval.py -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.5c One walker and explore wiring
- **Change `loop.py`:**
  - The on-screen `"thing"` approach path builds its route with the same `_screen_route(place, t, outward(...), held)` block as `go_to`. Factor that block out once and call it from both. Greedy runs only when `t` is None (no `tile_px` or no player).
  - `_unexplored(place, d=None)` returns `explore_dirs(place)[d]`, or the shortest route when no `d` is given. Delete the old frontier+1 body.
  - The go_to-unexplored dispatch reads `action.get("dir")` from R3's per-direction rows (`{"go_to": "unexplored", "dir": d}`) and passes it to `_unexplored(place, d)`.
- **Tests:**
  - On GridEnv, approaching an on-screen wall exit sends ≤ route+1 inputs and records `blocked`.
  - One explore decision walks ≥ 4 tiles.
  - An explore row with `dir` walks that direction's route (`explore_dirs(place)[dir]["dirs"]` equals the pressed keys).
  - The appended straight presses stop at the first blocked press (`approach` with a route already returns `route blocked` on the first `(0, 0)`). An extension that hits a wall after 1 tile sends at most route + 2 inputs.
  - Update the `test_go_to_unexplored_*` tests.
- **Repair (added 10-15, planner, verified offline):**
  - **Cause:** `space.route` never crosses a recorded exit edge. A routed approach to an on-screen exit label therefore detours away from the very exit it was used to cross. In `test_grid_loop_reuses_two_spatial_exits_between_floors`, the 2F label sits 2 tiles past the departure tile (8,9)↓. The current "label exactly 1 tile beyond the departure tile" match misses it, so the third decision walks to (0,4) and comes back `blocked`.
  - **Fix:** in `_approach_route`'s recorded-exit loop, replace `if (vx, vy) != (dx, dy): continue` with `if direction not in DIRS or max(abs(vx - dx), abs(vy - dy)) > 1: continue`. A recorded exit matches when its crossed tile is within 1 tile of the label, the same tolerance as `unseen_cross`. The `direction not in DIRS` guard skips tiles with no exit.
  - **Evidence:** with only this change patched in memory (scratch `r45c_patch.py`), the test passes: 4 decisions alternate p_place_2/p_place and `progress_n == 2`. The full suite gives 654 passed, 1 failed; the one failure is `test_world_golden`, which also fails without the patch and is regenerated in R4.5d.
- **Acceptance:** `pytest tests/agent/test_loop.py tests/agent/test_world.py -q -p no:cacheprovider -n auto`; `LIVE`.

#### R4.5d Result and Stuck wiring
- **Change `loop.py`:**
  - After `observe_after`, compute `novel = self.world.novel > 0 or verified_edge or (next_place != place and first visit)`.
  - With a choice, `v = result(skill.status, outcome effects, novel)`; without one, `unobservable`.
  - The row gets `verdict = v`, `pred = None`, `used = []`.
  - `failed = v in ("failed", "no_change")` replaces the old status/miss/no_change test.
  - Delete the `predict()` calls, `preds`, the contradiction `relate`, and the `verdict miss/surprise` flag.
  - After a `stuck:` plan, call `self.stuck.replanned(result["quests_changed"] or result["beliefs_changed"])`.
- **Golden:** regenerate `w0_golden/act/trace.jsonl` (authorized). A field-by-field diff may change only `verdict`, `pred`, `used` and `observation_status`. Commit it with `git add -f`.
- **Acceptance:** the full suite, `pytest tests/agent -q -p no:cacheprovider -n auto` (includes `LIVE`).

#### R4.6s S0 cleanup of retired prediction metrics (added 10-15; S0 never landed it)
- **Owns:** `scripts/eval_run.py`, `tests/agent/test_eval_run.py`. Haiku. Runs right before R4.6.
- **Change `eval_run.py`:**
  - Delete `_repredict`, the `repredict`/`hide_memory` parameters of `evaluate`, the `--repredict`/`--hide-memory` flags and their usage text.
  - Delete `verdict_rows`, `accuracy`, the 50-step `buckets` and the `prediction_accuracy` key.
  - The repeated-failure key (`row.get("verdict") in ("miss", "surprise")`) also accepts the new domain: `("miss", "surprise", "failed", "no_change")`.
  - Keep the S6 scorer and every other scorecard metric unchanged.
- **Tests:** delete `test_e4_metrics_and_offline_repredict_preserve_chooser_guess` and the `prediction_accuracy` assertion; keep the rest.
- **Acceptance:** `pytest tests/agent/test_eval_run.py -q -p no:cacheprovider -n auto`; `grep -n "agent.predict import" scripts` finds nothing that imports `predict` or `verdict`.

#### R4.6 Delete prediction
- **Change:**
  - `predict.py` keeps `EFFECTS` (R3 still imports it until its WP), `PRED`, `effects()` without `entity_appears`/`entity_disappears`, and `result()`. Delete `predict` and `verdict`.
  - `memory.py`: delete `relate()`'s `pattern_id` parameter, the pattern accounting, the `patterns` rows, and `conf` if no caller is left.
  - Delete `test_patterns.py`. Keep `test_predict.py` for `effects` and `result`.
- **Precondition:** R4.6s has removed `eval_run._repredict`/`prediction_accuracy`. Check that `grep -n "predict import\|verdict(" scripts agent` finds only users of `result`.
- **Acceptance:** the full suite (includes `LIVE`).

### 7.6 Test layers
| Layer | Gates R4? | Command |
|---|---|---|
| Unit (StubModel, GridEnv) | yes | `pytest tests/agent -q -p no:cacheprovider -n auto` |
| Replay (m1 fixture rows) | yes: S6, K | `pytest tests/agent/test_predict.py tests/agent/test_tasks.py -k "m1 or replay"` |
| PyBoy (ROM, no model) | yes: S11 probe | `pytest tests/agent/test_observer.py -rs` |
| Live-view regression (no model) | yes, every WP | `LIVE` (§7.1) |
| Live game run | no (MASTER §6: one run after R1, one after R3) | — |

### 7.7 Budgets
- **Lines (net, R4 hunks):** loop.py ≈ -165, tasks.py -30, predict.py -33, memory.py -45, skills.py -8, world.py +30, observer.py 0. Total ≈ -250. Report the actual numbers; this is not a cap.
- **Cloud calls:** 0.

### 7.8 Out of scope
- `act.py`, `notebook.py`, `plan.py` and the option rows (R3, R2).
- `_failed_exit`, arrival credit and ids (R1).
- `_apply_plan`'s fallback quest, `_evidence` and the T3 wiring (R2, after R4.5d).
- `eval_run.py` (S0).
- The live-view hunks, plus the frozen `env*.py`, `model.py`, `track.py`, `run_agent.py` and `watch_run.py`.
- Live-mode settle cost near water (143-480 frames is 2-8 s of real time per press at normal speed; today it times out at 10 s). Report it in §7.9; a shorter per-press settle in `skills._step` is a follow-up, not R4.

### 7.9 Result (filled in by the orchestrator)
**Current result: PASS — 648 tests, exit 0.** The final completion entry below supersedes the historical stopped attempts; shared LIVE changes remain unstaged.

- Gate numbers for each §7.2 row; the K fire lists for 6/8/10/12; the chosen K.
- Net lines per file; deviations; the fixture extraction command.
- S6 caveat: the fixture test uses a proxy for novelty (`scroll ≠ 0 or next_place ≠ place`), because the baseline run has no `world.novel`. The coordinator re-checks S6 on the real wiring with S0.
- **Contract proposal for the coordinator:**
  - the trace `verdict` domain, and null `pred`/`used`;
  - `observation_status` without `pending` (HF-5 replaced);
  - `predict.result`;
  - `Stuck.replanned` and the backoff;
  - `wait_settled(memory=256)`;
  - exit push = one press;
  - `World.explore_dirs`.
- **Commit list** (gate): commit R4-only files freely. Files marked LIVE still carry the user's live-view hunks; never `git add` them unless the coordinator has asked the user.
  - R4-only: `agent/tasks.py`, `agent/predict.py`, `agent/skills.py`, `agent/world.py` (after R1's commit), `agent/memory.py` (after R1's commit), `tests/agent/test_predict.py`, `test_tasks.py`, `test_skills.py`, `test_world.py`, `test_options.py`, `test_exits.py`, `test_space.py`, `test_notebook.py`, `test_om_eval.py`, `test_harness_reliability.py`, deleted `test_patterns.py`, `scripts/eval_run.py`, `test_eval_run.py`, `tests/agent/data/r4_m1_rows.jsonl`, `tests/agent/data/w0_golden/act/trace.jsonl` (`-f`).
  - LIVE: `agent/loop.py`, `agent/observer.py`, `tests/agent/test_loop.py`, `tests/agent/test_observer.py`, `tests/agent/test_run_provenance.py`.

#### Attempt result — 2026-10-15: STOPPED, gate not passed

Entry criteria passed on `feat/lean-agent`, HEAD `4c755cb`: S0 `d83bd9a` and R1 `d787836` plus subsequent R1 fixes are present; the venv, ROM, trace/truth and checkpoint 000100 exist. Root ran all acceptance commands reported below, using `.venv-agent/Scripts/python.exe -B -m pytest` and the TEMP/TMP setup in §7.1. No cloud model calls or live game run were made. The ROM test is an emulator-only probe.

Only R4.1–R4.3 were attempted. Each worker was explicitly spawned as **gpt-6-luna, reasoning high**, with the CLAUDE rules and its WP card. R4.4–R4.6 did not start. §7.2 requires stopping when S6 fails, so no thresholds, novelty rules or replay cohorts were changed to force a pass. The full gate and runtime integration are incomplete: in particular, the new `Stuck` no longer provides the fields still read by the not-yet-updated loop. This working tree is a partial implementation, not a runnable completed R4.

Acceptance and repair history:

- **R4.1:** `test_observer.py test_skills.py test_type_text.py -rs -q -p no:cacheprovider -n auto` initially exited 1: **61 passed, 1 failed**. The synthetic eight-state fixture was uniform and therefore blank. One same-tier repair changed only that fixture to seeded textured frames, keeping its eight states and 21-frame cadence. Root's rerun exited 0: **62 passed**, no skips, 19.79 s. Logs: `.tmp/r4/R4.1-accept.log`, `R4.1-repair-accept.log`.
- **R4.2:** `test_predict.py test_tasks.py -q -p no:cacheprovider -n auto` exited 1: **13 passed, 1 failed**, 2.16 s. The S6 test requires 18 surprise rows; the raw trace and 105-row fixture both contain **23**. One same-tier repair investigation found no extraction defect and made no further edits. Among all 23 surprises there are 14 truth-confirmed unmoved rows, but only 13 score `failed`; S105 scores `progress`. The 18 surprises from S55 onward have the same 14/13 discrepancy. No arbitrary S55 cutoff was added. Logs: `.tmp/r4/R4.2-accept.log`, `replay-evidence.log`.
- **R4.3:** `test_skills.py test_exits.py test_space.py -q -p no:cacheprovider -n auto` exited 1: **77 passed, 1 failed**, 15.70 s. Failure: `test_space.py::test_actor_zero_motion_does_not_record_a_wall`, expected `actor_tiles == {(1, 0)}`, actual empty. One same-tier repair investigation found the test constructs its move directly, never calling the changed skill. It omits a world-screen `hall` place record, so `World._tiles` returns no tiles. Repair would require files outside R4.3's card; none were edited. Log: `.tmp/r4/R4.3-accept.log`.
- **LIVE:** both commands were run at entry and after each WP: **11 realtime tests passed**, **12 live-filtered tests passed**, every command exit 0. R4.1 uses the `batch1-live[-realtime].log` pair; R4.2 and R4.3 have their named `*-live[-realtime].log` pairs in `.tmp/r4`. No serial flaky-test exception was needed.
- No WP reached two failed acceptance runs; the user-authorized extra same-tier attempt was therefore not triggered. No tier escalation or layered fixes were used.

Gate rows (§7.2):

- **S6 — FAIL:** one of 14 unmoved rows is not scored failed. S100 is `progress`; the result classifier emits no `surprise` labels. S105 is truth-unmoved, `skill.status = done`, `scroll = [16, -16]`; the specified proxy sets `novel = True`, so the prescribed priority returns `progress`. The separate 23-versus-18 cohort mismatch fails acceptance before its 14-failed assertion. This does not establish that S105 was a real tactical failure; its status/scroll and truth disagree. Coordinator ruling is required; do not relabel it from truth in agent code.
- **S11 — PASS:** root additionally ran `test_observer.py -rs -q -p no:cacheprovider -n auto`: **13 passed**, exit 0, 2.18 s, **no skips** (`.tmp/r4/gate-S11.log`). The synthetic cycle passes both env paths with memory 256 and fails to settle with memory 24. The ROM probe loads checkpoint 100, presses DOWN three times, asserts RAM y ≥ 10 for eval only, and settles.
- **K — PASS, provisional until wiring:** `test_tasks.py -k replay -q -p no:cacheprovider -n auto`: **1 passed**, exit 0, 2.09 s (`gate-K.log`). Root's direct replay printed K=6 **[70]**, K=8 **[72]**, K=10 **[]**, K=12 **[]** (`replay-evidence.log`). Chosen **K=8**, the largest firing inside S64–73. These lists use the packet's proxy, not future `world.novel`.
- **Explore granularity — NOT RUN:** R4.4/R4.5c not started.
- **One walker — INCOMPLETE:** R4.3's single routed push and blocked-move tests pass inside the 62-test R4.1 run; the full R4.3 acceptance fails as above, and loop routing/fold integration in R4.5c has not been implemented or measured.
- **Hold removed — NOT RUN:** R4.5a not started.
- **Full suite — NOT RUN:** stopped on S6 and the out-of-scope R4.3 acceptance failure; no green suite is claimed.
- **Lines — partial only:** agent hunks net **−31**; all attempted code/tests/fixture hunks net **+227**; this Result adds **69** documentation lines, total **+296**. No R4 commits exist.

R4-only line deltas against the saved entry working tree (additions/deletions/net; excludes the user's edits):

- `agent/observer.py`: +4/−4/**0** (signature plus three deque sites).
- `agent/predict.py`: +7/−0/**+7**.
- `agent/tasks.py`: +16/−50/**−34**.
- `agent/skills.py`: +5/−9/**−4**.
- `tests/agent/test_observer.py`: +53/−0/**+53**.
- `tests/agent/test_predict.py`: +28/−1/**+27**.
- `tests/agent/test_tasks.py`: +62/−91/**−29**.
- `tests/agent/test_skills.py`: +10/−8/**+2**.
- `tests/agent/data/r4_m1_rows.jsonl`: +105/−0/**+105** (ignored by `data/`; remains on disk, unstaged).

Preservation: `.tmp/r4/starting.diff` and `live-R4.1.diff` through `live-R4.4.diff` contain entry diffs (R4.4 was not started). Root reconstructed the original working-tree lines from HEAD plus `starting.diff`: all **17** pre-existing modified tracked files match their entry contents, except the four authorized R4.1 observer replacements and inserted observer test coverage. Every original observer-test line remains in order. See `.tmp/r4/preservation-lines.log`. `contracts.md` is untouched. Headroom compression was used; its local proxy was unreachable, with local compression/storage still available.

Fixture extraction command (105 projected rows; truth is eval-only):

```powershell
New-Item -ItemType Directory -Force .tmp/r4/tmp | Out-Null
$env:TEMP = "$PWD\.tmp\r4\tmp"; $env:TMP = $env:TEMP
@'
import json
from pathlib import Path
run = Path("runs/m1_bedroom_live")
trace = [json.loads(s) for s in (run / "trace.jsonl").read_text(encoding="utf8").splitlines()]
truth = {r["step"]: r for r in (json.loads(s) for s in (run / "truth.jsonl").read_text(encoding="utf8").splitlines())}
out = []
for r in trace:
    a, b = truth.get(r["step"]), truth.get(r["step"] + 1)
    moved = None if not a or not b else (a["map"], a["x"], a["y"]) != (b["map"], b["x"], b["y"])
    out.append({"step": r["step"], "screen": r.get("screen"), "place": r.get("place"),
                "next_place": r.get("next_place"), "verified_edge": r.get("verified_edge", False),
                "scroll": r.get("scroll", [0, 0]), "skill": {"status": (r.get("skill") or {}).get("status")},
                "outcome": {"effects": (r.get("outcome") or {}).get("effects", [])},
                "verdict": r.get("verdict"), "moved": moved})
Path("tests/agent/data/r4_m1_rows.jsonl").write_text(
    "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in out), encoding="utf8")
print(f"wrote {len(out)} rows; surprises={sum(r['verdict'] == 'surprise' for r in out)}")
'@ | .venv-agent/Scripts/python.exe -B -
```

**Contract proposal:** keep the §7.4/§7.9 proposal pending; this attempt provides only the tested `wait_settled(memory=256)`, standalone `predict.result`, standalone `Stuck.replanned`/backoff, and the single routed push. Do not approve a completed trace/HF-5/`World.explore_dirs` contract yet: those changes are unimplemented. Final proposed `used` is `[]`, `pred` is null. The S6 proxy caveat above must be resolved and then rechecked on real wiring with S0.

**Commit/staging result:** no files staged, no commit, no push. Gate failed. All R4 changes remain reviewable in the working tree. LIVE files remain unstaged: `agent/loop.py`, `agent/observer.py`, `tests/agent/test_loop.py`, `tests/agent/test_observer.py`, `tests/agent/test_run_provenance.py`; all other user live-view edits likewise remain unstaged. Only observer and its test received R4 edits in that LIVE list. Follow-up F1 remains: the larger window still costs about 2–8 seconds per press near water in real-time mode; this attempt did not measure live wall-clock cost or change the per-press settle policy.

#### Resumed attempt — user rulings and acceptance history

The user requested continuation and explicitly ruled: **use 13 confirmed failures for S6; report S105 separately**. The corrected replay test keeps all 105 rows, covers all 23 surprises, checks the 13 truth-unmoved blocked/failed rows, S100 progress, and the S105 disagreement. Root acceptance: **14 passed**, exit 0 (`.tmp/r4/R4.2-resume-accept.log`). The coordinator separately landed `c560dc6`, seeding the world place in `test_space.py`; root R4.3 acceptance then passed **78 tests**, exit 0 (`R4.3-resume-accept.log`).

The user also explicitly authorized two narrow scope amendments: (1) an **S0 cleanup WP** owning `scripts/eval_run.py` and `tests/agent/test_eval_run.py`, removing the retired prediction replay/metric and CLI flags; root acceptance **43 passed**, exit 0, and the baseline evaluator with `--no-write` exited 0; (2) **R4.5a's final same-tier repair** may edit `tests/agent/test_harness_reliability.py`, preserving its checkpoint/no-duplicate-input checks while replacing pending-resolution expectations with one normal timed-out row. No other WP file boundary is relaxed.

Intermediate dependency acceptance is recorded rather than hidden: R4.4 initially **134 passed / 2 failed**, R4.5a initially **64 passed / 79 failed**, all failures at the removed `Stuck.n` interface. The prescribed R4.5b deletions resolve that dependency without compatibility code; R4.4 then passed **136 tests**. R4.5b initially **207 passed / 3 failed** on call counts retaining the deleted relook; one same-tier repair corrected only those obsolete counts, retaining malformed-JSON repair behavior checks, and root acceptance passed **210 tests**. R4.5a's second acceptance was **140 passed / 1 failed**, the obsolete harness pending assertion. Its third attempt is the user-authorized final same-tier repair, with both failure outputs supplied to the worker. Logs are `.tmp/r4/R4.4-accept.log`, `R4.4-wired-accept.log`, `R4.5a-accept.log`, `R4.5a-wired-accept.log`, `R4.5b-accept.log`, and `R4.5b-repair-accept.log`.

#### Final resumed Result — STOPPED after R4.5c's final allowed attempt

**Gate remains failed. No staging, commit or push.** R4.5d and R4.6 were not started; no golden was regenerated. Result scoring/backoff are still not wired into the loop, and prediction/pattern deletion is incomplete. The S0 prerequisite cleanup is applied and tested in the working tree, but is not committed. The previous attempt's partial-runtime warning still applies.

**R4.5c acceptance history (all run by root, same original command):**

- Initial: **186 passed / 38 failed**, exit 1, 87.09 s (`.tmp/r4/R4.5c-accept.log`). Most failures were a removed but still-used `DIRS` import; six approach stubs lacked the new route keyword; the new four-press explore fixture also encountered its default door after two presses.
- One repair restored `DIRS`, updated the stubs, and moved the fixture door out of the path while retaining the four-tile/new-row assertions. Acceptance: **223 passed / 1 failed**, exit 1, 79.51 s (`R4.5c-repair-accept.log`).
- **User-authorized extra final attempt: gpt-6-luna, reasoning HIGH**, with failure output supplied. The worker diagnosed recorded-exit avoidance in the shared router and added a narrow loop-only route to the recorded departure tile plus its single recorded push when the visible target is exactly one tile beyond it. Normal route exit avoidance and R1 identity code were not changed. **This candidate did not resolve the regression.** Final acceptance: **223 passed / 1 failed**, exit 1, **56.29 s** (`R4.5c-final-accept.log`).
- The remaining failure is `tests/agent/test_loop.py::test_grid_loop_reuses_two_spatial_exits_between_floors`, line 329: the four decisions must alternate the same two place IDs, but `ids[0]` is `p_place_2` and `ids[2]` is `p_place`. The reuse assertion was not weakened. No further fix, threshold change or attempt followed.

R4.5a's user-authorized final harness repair preserves the uninterrupted/restored input sequence, one counted crossing and matching identity checks, and now asserts one normal `timed_out`/`done` action row. Its final acceptance and revalidation were affected by R4.5c: **103 passed / 41 failed** before the `DIRS` repair, then **143 passed / 1 failed**, exit 1, 79.93 s (`R4.5a-final-accept.log`, `R4.5a-final-revalidation.log`). The sole latter failure is the same two-floor loop test above; the repaired harness test passed. No additional R4.5a repair was made. Its full acceptance is therefore not claimed green.

**Latest gate evidence:**

- **S6:** the user-approved 13-failure test passed in the recorded 14-test acceptance. **Not yet validated against the coordinator's later before-frame join amendment:** the fixture still joins truth S/S+1 and the test still asserts S105 unmoved. The amended packet joins S105's before-frame S103 to S106 and identifies real movement. The old S105-disagreement claim above is historical, not the corrected interpretation. Regenerate the fixture and update that assertion under the current packet before claiming S6. Real `world.novel` loop wiring is also unimplemented.
- **S11:** 13 observer tests passed with the synthetic cycle on both paths and the ROM y ≥ 10 probe executed, no skips. Observer code is unchanged since that result.
- **K:** 6 → [70], 8 → [72], 10 → [], 12 → []; chosen **8**. Replay test passed; loop backoff wiring is unimplemented.
- **Explore:** the final R4.5c acceptance passes its directed GridEnv test asserting exactly four presses, four tiles walked, the direction's route, and at least one view row of newly seen cells; extension/block stopping tests also pass. These tests are among the 223 passes, not a claim that the whole WP passed.
- **One walker:** routed wall-exit test passes with its push recorded as blocked and inputs bounded by route plus one. The two-floor reuse regression above keeps the WP/gate failed.
- **Hold removal:** the normal timed-out-tail test and the repaired checkpoint/no-duplicate-input harness test pass in the recorded acceptances. Full harness acceptance remains blocked by the two-floor regression.
- **Suite:** not run after the final failure; no completed R4/full-suite claim. R4.5d/R4.6 remain pending.
- **LIVE:** every executed WP's live commands passed. Latest post-final-fix commands: realtime **11 passed**, live-filtered **12 passed**, both exit 0 (`R4.5c-final-live-realtime.log`, `R4.5c-final-live.log`). No flaky-test serial exception was needed.

**Final line deltas, excluding the user's entry edits** (net; additions/deletions in `.tmp/r4/resume-final-lines.json`): `agent/observer.py` **0**, `predict.py` **+7**, `tasks.py` **−34**, `skills.py` **−4**, `world.py` **+19**, `loop.py` **−76** — agent subtotal **−88**. Tests: `test_observer.py` **+53**, `test_predict.py` **+31**, `test_tasks.py` **−29**, `test_skills.py` **+2**, `test_world.py` **+44**, `test_loop.py` **−210**, `test_options.py` **−2**, `test_harness_reliability.py` **−4**, fixture **+105**. Authorized S0 cleanup: `scripts/eval_run.py` **−67**, `tests/agent/test_eval_run.py` **−9**. All listed code/test/fixture changes total **−174**. This document's Result block adds **108 lines**, for an orchestrator subtotal **−66**, excluding concurrent coordinator packet amendments. There are no R4 commits to diff.

**Preservation and commit list:** all ten saved live functions/tests compare exactly to `.tmp/r4/protected-live-functions.json`; the live additions in the loop remain apart from the explicitly scheduled legacy migration/cycle deletions. Other pre-existing modified tracked files match the original entry snapshot except the coordinator's independently updated MASTER R1 status row, which was retained untouched. The coordinator's `c560dc6` test-space fix is also retained. `contracts.md` is unchanged; the Git index is empty. Root ran `git diff --check` successfully. The fixture remains ignored/on disk and unstaged. All LIVE files remain unstaged, including `agent/loop.py`, `agent/observer.py`, `tests/agent/test_loop.py`, `tests/agent/test_observer.py`, and `tests/agent/test_run_provenance.py`; every other user live-view file is also unstaged. The user-authorized S0 cleanup adds only its two files to the eligible future commit list. No file is staged while the gate is red.

**Contract proposal:** still pending, with the same proposed domains/interfaces above; only implemented/tested pieces can be reviewed. Do not treat the trace/result/backoff integration, prediction deletion or complete R4 contract as landed. Cloud model calls **0**; no live game run. The real-time water-settle cost remains the unmeasured F1 follow-up, not a change in this attempt.

**Concurrent packet amendments retained:** the final diff audit found coordinator changes outside this Result: S6 before-frame truth attribution, the harness ownership addition, and the new R4.6s card. They were not overwritten. Besides the corrected fixture/S105 assertion above, R4.6s's newly specified repeated-failure metric support for `failed`/`no_change` is still pending; the already-tested S0 cleanup removed only the retired prediction features. No further code edits were made after the final allowed R4.5c attempt failed. Future work must read the current packet rather than the original card text. The historical fixture extraction command above describes what was actually run and must be amended for before-frame attribution on continuation.

#### Gate completion after the coordinator repair — PASS

The user resumed the task after the coordinator supplied the verified R4.5c repair. It was documented but not applied on disk: the first recheck still reported **223 passed / 1 failed** (`.tmp/r4/R4.5c-patched-accept.log`, pre-patch despite its filename). A worker applied the packet's exact direction guard and Chebyshev-1 match around the crossed tile. Root then ran R4.5c acceptance: **224 passed**, exit 0, 53.25 s (`R4.5c-packetfix-accept.log`). The same worker tier was retained throughout: each new spawn explicitly selected **gpt-6-luna / HIGH**; no tier escalation occurred.

R4.5d, R4.6s and R4.6 are now implemented. The loop scores the action's post-observation novelty, uses one result domain, records null predictions/empty used lists, and reports stall-plan changes to backoff. The settled before-frame is recorded **after the live stale guard, before input**, using the existing recorder, so already-visible cells cannot make a blocked action look like progress. The positive observed relations remain; prediction calls, contradictions, entity-list effects and pattern accounting are removed. `conf` remains because `test_memory.py` directly imports and tests relation confidence. No shim for retired `predict` or `verdict` was added. R4.6s retains legacy repeated-failure labels and also accepts `failed`/`no_change`.

**Root-run acceptance and repair evidence:**

- R4.2's before-frame amendment: `test_predict.py test_tasks.py -q -p no:cacheprovider -n auto` — **14 passed**, exit 0, 2.69 s (`R4.2-before-frame-accept.log`).
- R4.5d first full suite: **639 passed / 19 failed**, exit 1, 82.10 s (`R4.5d-accept.log`). Most failures exposed the before-view novelty baseline bug; three new backoff assertions also exposed list-valued change results. One same-tier repair primed the before-view recorder and passed `bool(quests_changed or beliefs_changed)` to backoff.
- Repair-focused loop/harness run: **124 passed / 4 failed**, exit 1, 51.38 s (`R4.5d-repair-focused.log`). The four remaining expectations were obsolete: a no-novelty blocked action expected progress, a tried packet expected `miss`, dialogue advancement expected `hit`, and the exit-history harness counted the first discovery walk as a failure. The **extra final gpt-6-luna / HIGH attempt** received both failure outputs and corrected the two owned loop assertions. The user separately authorized the two harness-case updates: preserve the first discovery as progress, repeat the stationary blocked attempt before checking failure notes, and retain rename/checkpoint/input assertions. No further production fix was layered on that final attempt.
- R4.6s: `test_eval_run.py -q -p no:cacheprovider -n auto` — **43 passed**, exit 0, 12.46 s (`R4.6s-final-accept.log`); `scripts/eval_run.py runs/m1_bedroom_live --no-write` — **exit 0** (`R4.6s-final-baseline.log`).
- **Final joint R4.5d/R4.6 acceptance and suite gate:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` — **648 passed**, **exit 0**, **76.13 s**, no skips (`.tmp/r4/R4.5d-R4.6-final-accept.log`). This also covers the repaired R4.5a harness, all earlier WP components and the golden comparison. A whitespace-only R4.6 diff-check repair removed an extra trailing blank line; no semantic changes followed the final suite.
- **Latest explicit LIVE commands:** realtime **11 passed**, exit 0, 3.94 s; live-filtered **12 passed**, exit 0, 3.77 s (`R4.6-live-realtime.log`, `R4.6-live.log`). Earlier WP live commands also passed as recorded above. No flaky-test serial exception was needed.

**Final §7.2 gate rows (all commands run by root):**

- **S6 PASS:** **0 of 13** unmoved failures misclassified, **23** surprise inputs, S100 **progress**, S105 **progress**, **0 surprise outputs**. `test_predict.py -k m1` passed **1 test**, exit 0 (`gate-final-S6.log`). The fixture uses truth at the trace's before-frame step through truth at row S+1: S105 resolves S103's action and moved `(3,9) → (2,10)`. The original S105 discrepancy is resolved, not filtered out.
- **S11 PASS:** `test_observer.py -rs` with the standard flags passed **13 tests**, exit 0, **no skips** (`gate-final-S11.log`). The 8-state/21-frame cycle settles on both paths with memory 256 and does not with 24; the ROM/checkpoint probe presses DOWN three times, asserts y ≥ 10 for eval only, and settles. The larger recurrence window leaves `quiet=24` unchanged.
- **K PASS:** K=6 **[70]**, K=8 **[72]**, K=10 **[]**, K=12 **[]**; chosen **8**, the largest firing inside S64–73. `test_tasks.py -k replay` passed **1 test**, exit 0 (`gate-final-K.log`); root independently printed the complete lists in `gate-final-replay-evidence.log`.
- **Explore PASS:** `test_loop.py -k explore` passed **4 tests**, exit 0 (`gate-final-explore.log`), including four directed presses/four tiles and at least one view row of newly seen cells; extension presses stop at the first block.
- **One walker PASS:** `test_skills.py test_loop.py -k "walker or push"` passed **6 tests**, exit 0 (`gate-final-walker.log`); wall-exit inputs are bounded by route plus one, and the push direction is recorded in blocked evidence. The two-floor identity regression also passes in the final suite.
- **Hold removed PASS:** `test_loop.py -k timed_out` passed **1 test**, exit 0 (`gate-final-timed_out.log`): one normal timed-out row/tail, no pending action row. The live-only stale row writer and inert invalidation attributes remain by design.
- **Suite PASS:** **648 passed**, exit 0, as above.
- **Lines PASS:** agent hunks net **−193** against the saved entry working tree, excluding the original live-view edits. All listed code/test/data changes net **−412**, excluding this document. This is the actual partial-sharing footprint, not the planned −250 estimate.

The S6 fixture still uses the approved **novelty proxy** (scroll or place change); it is not a live-run measurement of `world.novel`. Actual loop novelty is exercised by the GridEnv and failure-counter tests, including the before-frame baseline correction. S9 live model-call savings and real-time water-settle wall-clock cost were not measured. No live game run or cloud model call was made. F1 (2–8 seconds per water-view press in real-time mode) remains a follow-up; no per-press settle policy was added.

**User-approved golden deviations:** the user explicitly expanded the original four-trace-field limit to the expected relook-removal changes: remove the old `recent: no effect xN;` act-flag prefix, set S5 `perception_reused` true, and retire the extra `e_door_2` relook observation (latest `last` 5→2, `seen` 3→2). The user then approved one source-view baseline row in `memory/seen.jsonl` at step 1 (90 cells, 38 open-edge cells). Root compared the candidate field by field and wrote **only** `w0_golden/act/trace.jsonl`, `memory/entities.jsonl`, and `memory/seen.jsonl`; other golden files remained unchanged (`R4.5d-golden-approved.log`). These two memory files are explicit additions to the eligible gate commit list.

**Final net lines per file**, excluding original user live edits (`.tmp/r4/gate-final-lines.json` records additions/deletions):

- Agent: `observer.py` **0**, `predict.py` **−48**, `tasks.py` **−34**, `skills.py` **−4**, `world.py` **+19**, `loop.py` **−80**, `memory.py` **−46**.
- Tests: `test_observer.py` **+53**, `test_predict.py` **−15**, `test_tasks.py` **−29**, `test_skills.py` **+2**, `test_world.py` **+44**, `test_loop.py` **−194**, `test_options.py` **−2**, `test_harness_reliability.py` **−1**, deleted `test_patterns.py` **−108**, `test_eval_run.py` **−7**.
- Eval/data: `scripts/eval_run.py` **−67**, `r4_m1_rows.jsonl` **+105**, golden trace **0**, golden entities **−1**, golden seen **+1**. This packet is **+202 net lines**, including concurrent coordinator amendments and the attempt history; the combined code/test/data/document net is **−210**. LIVE-shared deletions remain outside the commit as required.

**Current fixture extraction command** (supersedes the historical S/S+1 command):

```powershell
New-Item -ItemType Directory -Force .tmp/r4/tmp | Out-Null
$env:TEMP = "$PWD\.tmp\r4\tmp"; $env:TMP = $env:TEMP
@'
import json
from pathlib import Path
run = Path('runs/m1_bedroom_live')
trace = [json.loads(s) for s in (run / 'trace.jsonl').read_text(encoding='utf8').splitlines()]
truth = {r['step']: r for r in (json.loads(s) for s in (run / 'truth.jsonl').read_text(encoding='utf8').splitlines())}
out = []
for r in trace:
    before, after = truth.get(int(Path(r['frame']).stem)), truth.get(r['step'] + 1)
    moved = None if not before or not after else (before['map'], before['x'], before['y']) != (after['map'], after['x'], after['y'])
    out.append({'step': r['step'], 'screen': r.get('screen'), 'place': r.get('place'),
                'next_place': r.get('next_place'), 'verified_edge': r.get('verified_edge', False),
                'scroll': r.get('scroll', [0, 0]), 'skill': {'status': (r.get('skill') or {}).get('status')},
                'outcome': {'effects': (r.get('outcome') or {}).get('effects', [])},
                'verdict': r.get('verdict'), 'moved': moved})
Path('tests/agent/data/r4_m1_rows.jsonl').write_text(
    ''.join(json.dumps(r, separators=(',', ':')) + '\n' for r in out), encoding='utf8')
print(f'wrote {len(out)} rows; surprises={sum(r["verdict"] == "surprise" for r in out)}')
'@ | .venv-agent/Scripts/python.exe -B -
```

**Contract proposal for the coordinator (contracts.md is unchanged):**

- Trace `verdict`: `progress | changed | no_change | failed | unobservable`; `pred = null`, `used = []`. Novelty wins over skill failure; otherwise blocked/failed wins, then pixel no-change, then changed. Entity-list churn does not enter result scoring.
- Normal action `observation_status`: `settled | timed_out`; HF-5 pending action/resolve rows are retired. The existing live stale-observation no-input row still uses `stale`; its writer/invalidation contract is retained separately.
- `predict.result(status, effects, novel) -> str` replaces the removed prediction/verdict functions. `effects` no longer emits entity appearance/disappearance. `EFFECTS`/`PRED` remain for the pending R3 schema and observed relations.
- `Stuck`: only `progress_n`, `seen_places`, `seen_edges`, `u`; stall at `8 * 2**u` decisions, capped u=3, reset by progress; `replanned(changed)` controls backoff. Until R2, the call uses today's quests/beliefs change result. R2 must replace that with zero/nonzero successful `apply_ops` results and remove the old stall fallback.
- `wait_settled(..., memory=256)` decouples recurrence history from `quiet=24` on all three deque sites.
- Routed exits get one push, with every press recorded; on-screen approaches share the tile router. Recorded departure edges are matched with the coordinator's crossed-tile tolerance.
- `World.explore_dirs(place) -> {direction: {dirs, to}}`; the before-action view establishes the seen baseline, and `_record_view` supplies the post-action novelty count.

**Preservation and gate commit scope:** all ten protected live function/test snapshots match exactly; the other original modified tracked files are unchanged apart from the independently updated MASTER R1 row, which was retained (`gate-final-audit.log`). The Git index was empty before gate staging. Live-view functions and observation fields are preserved; the packet's separate legacy migration/cycle code is retired. The latest suite validates cancellation/stamps/staleness. Files with live work **must remain unstaged**: `agent/loop.py`, `agent/observer.py`, `tests/agent/test_loop.py`, `tests/agent/test_observer.py`, `tests/agent/test_run_provenance.py`, plus all other original live-view files. MASTER, handoff notes and live-view documentation are not part of this commit.

Eligible changed files for gate commit `R4: gate PASS (648 tests)`: `agent/tasks.py`, `agent/predict.py`, `agent/skills.py`, `agent/world.py`, `agent/memory.py`; `tests/agent/test_predict.py`, `test_tasks.py`, `test_skills.py`, `test_world.py`, `test_options.py`, `test_harness_reliability.py`, deleted `test_patterns.py`; the user-authorized `scripts/eval_run.py` and `tests/agent/test_eval_run.py`; the fixture and three approved golden files named above (force-add exact paths); and this packet. The gate validates the **combined working tree**; the eligible commit alone deliberately excludes the LIVE-shared implementation and cannot be advertised as a clean-checkout suite result. No push is authorized or performed. Stop after the commit and this Result; R2/R3 and a live run are not started.

## 9. Phase D amendment (10-19): fixes from the post-R3 live run

Source: `runs/m1_rcr_post_r3` (3db80df), MASTER §8 "Phase D (10-19)". Uncommitted, in `agent/loop.py` and `tests/agent/test_loop.py` only. Suite **629 passed, 1 skipped** (`-n auto`); LIVE 11 + 12 passed.

- **A. A zero-input option is a failure** (dead loop S144-150: `go_to thing:e_house` executed nothing, scored `unobservable`, same prompt 7×).
  - `agent/loop.py:288`: `idle` = an option was picked, 0 inputs were sent, and the skill still says `done`. Such a step scores `failed`. Blocked and refused steps keep their own reason.
  - `:316`: the failure's why is "did nothing". `:323-324`: when the pick never became an action (choice None), the fail is recorded under the option's own `intent_key`, kept in the step state as `pick_key`. The menu's notes then change.
  - Tests: `test_a_picked_option_that_sends_no_input_lands_in_fails`, `test_an_option_whose_action_never_resolves_lands_in_fails_under_its_own_key`.
- **B1. T5 "no open quest" fires once per no-quest period** (17 plan calls on S1-S49).
  - `:43`: a `t5_armed` flag that starts False; nothing heard means nothing a plan could cite. `:441`: T1, T2 or T3 arms it. `:482`: T5 fires only when armed, then disarms. `:487`/`:508`: saved in state and restore.
  - Test: `test_no_open_quest_fires_once_until_a_new_event`, which replaces the "every 3 steps" test.
- **B2. Dialogue becomes citable off the world screen** (Oak's intro was only heard at S53).
  - `:355`: a conversation ends when its screen kind or place changes, e.g. dialogue → menu, not only → world. `:365` records the screen kind.
  - Menu, title and keyboard text stays knowledge only.
  - Test: `test_dialogue_ends_when_its_screen_kind_changes`.
- **Deviation accepted by the coordinator:** T5 re-arms on any new T1/T2/T3, so it can fire again whenever new text arrives. The original acceptance was "at most once on S1-S52".
- **Offline evidence:** replay of S1-S52 through the real `_learn`/`_plan_trigger` with cached perceiver replies (scratch `replay_t5.py`).
  - Now: 2 plan calls, S17 (1 t-line) and S35 (2 t-lines), against 2 new-text events.
  - HEAD: 18 calls, all with 0 t-lines.
- **Golden:** I regenerated `w0_golden/act/trace.jsonl`. It differs from HEAD only in the `plan` fields: the empty T5 plans at S1 and S7 are gone, and S2 now plans on its T2.
- **Open for the next live run:**
  - intro plan calls ≤ new-text events;
  - none end with the "none heard yet" error;
  - no option repeats after scoring "did nothing".
