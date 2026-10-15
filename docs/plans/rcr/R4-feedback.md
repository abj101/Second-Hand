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

Status: not started · Branch: `feat/lean-agent` · Orchestrator: Sonnet; every WP worker: Haiku (set `model` explicitly; never Opus). Cloud calls: **0** (StubModel, cached frames, ROM-only probes).

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
| S6 real failures not scored failed | 0 of 14; S100 = `progress`; 0 `surprise` | `pytest tests/agent/test_predict.py -k m1` |
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
4. R4.6
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
  - Plus `moved` (truth S vs S+1), which is eval-only and never read by agent code.
  - Record the one-off extraction command in §7.9.
- **Tests:**
  - S6: for the 18 `surprise` rows, compute `result(status, effects − entity_*, novel = scroll ≠ 0 or next_place ≠ place)`. The 14 unmoved rows give `failed`, S100 gives `progress`, and no row gives `surprise`.
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

#### R4.6 Delete prediction
- **Change:**
  - `predict.py` keeps `EFFECTS` (R3 still imports it until its WP), `PRED`, `effects()` without `entity_appears`/`entity_disappears`, and `result()`. Delete `predict` and `verdict`.
  - `memory.py`: delete `relate()`'s `pattern_id` parameter, the pattern accounting, the `patterns` rows, and `conf` if no caller is left.
  - Delete `test_patterns.py`. Keep `test_predict.py` for `effects` and `result`.
- **Precondition:** S0 has removed `eval_run._repredict`/`prediction_accuracy`. Check that `grep -n "predict import\|verdict(" scripts agent` finds only users of `result`.
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
  - R4-only: `agent/tasks.py`, `agent/predict.py`, `agent/skills.py`, `agent/world.py` (after R1's commit), `agent/memory.py` (after R1's commit), `tests/agent/test_predict.py`, `test_tasks.py`, `test_skills.py`, `test_world.py`, `test_options.py`, `test_exits.py`, `test_space.py`, `test_notebook.py`, `test_om_eval.py`, `test_harness_reliability.py`, deleted `test_patterns.py`, `tests/agent/data/r4_m1_rows.jsonl`, `tests/agent/data/w0_golden/act/trace.jsonl` (`-f`).
  - LIVE: `agent/loop.py`, `agent/observer.py`, `tests/agent/test_loop.py`, `tests/agent/test_observer.py`, `tests/agent/test_run_provenance.py`.
