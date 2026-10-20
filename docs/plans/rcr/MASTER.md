# RCR: Root-Cause Refactor (master tracker)

Status: **RCR CLOSED 2026-10-19 — Phase E live gate FAIL.** The top-five gate missed zero-input actions (2); S4 reconstructed-menu proxy was 4 (limit 2). Evidence and audit verdicts are in §8. Any further work starts as a new program; no further amendment or live run is authorized by this closure. Branch feat/lean-agent.

**Goal:** remove the four root mechanisms the audit found, and leave the agent *smaller* than before.
**Not in scope:** new features, new gates, game- or label-specific fixes, or tuning the perceiver prompt per label.
**Role of this file:** it tracks the whole program and is the reality check. Any claim about progress needs a row in §8 with evidence.

## 1. Sections

| | Section | Doc | Planning session | Phase | Status |
|---|---|---|---|---|---|
| R1 | Identity and world truth | [R1-identity.md](R1-identity.md) | `rcr-r1-identity` | C done; live evidence limited | Phase E live S1 is **limited**: map `0x25:slot-1` scored `e_chair_2` and `e_person` (2 ids / 41 matched observations), with `e_chair_2` proximity-ambiguous; map `0x00:slot-2` scored `e_fence_post_1` (1 id / 1 observation, a landmark). The `e_person` join could be Mom, but it is not enough to establish her stable identity; the `e_chair_2` proximity join is also ambiguous. S1 therefore remains limited for Pallet NPC/Mom targets; S2 is unavailable because map 0 first appears at step 150. No claim of a live R1 pass. |
| R2 | Quest state and planner contract | [R2-quests.md](R2-quests.md) | `rcr-r2-quests` | C done; live closure evidence passed | Phase E: 0 empty closures of 2 (q1 closed after acknowledgement at step 42; targeted q2 auto-closed on arrival at step 142), S5 false closures 0, S14 re-adds 0, 0 plan calls before first heard t-key and 0 “none heard yet” errors. The full Phase E live gate still fails on menu execution. |
| R3 | Act prompt and option menu | [R3-decide.md](R3-decide.md) | rcr-r3-decide | Phase D closed; live gate fail | Phase E: act prompt max 1,318 user / 1,587 system+user passes ≤2,000; D7 qualifying zero-input dead-loop max is 1. The full live gate **fails**: 2 selected actions sent zero keys (steps 87, 135); S4 reconstructed-menu proxy 4 exceeds 2. |
| R4 | Feedback and control loop | [R4-feedback.md](R4-feedback.md) | `rcr-r4-feedback` | C done; live feedback mixed | Phase E S6: 0 of 24 joined action failures were mis-scored; 9 were real no-movement failures and all were marked failed. Health reports `cut_self_loops=10` and `repeat_actions_max_length=13`; these signals and the two zero-input actions remain open. |

The phases run in this order:
- **A, solution:** the session writes it, then the coordinator reviews it against §4.
- **B, packet:** written only after approval, in `_template.md` shape.
- **C, implement:** Haiku workers do the work; acceptance commands must exit 0, plus `pytest -n auto`.
- **D, measure:** the §5 scorecard on offline replay, then a live run if the user approves the budget.

**What each solution claims (in one line):**
- **R1:** identity comes from position, tracks and pixels; names are only attributes. Exit truth comes from departures and blocked pushes.
- **R2:** notebook = fold(applied ops). Each op returns ok or an error. Closure comes from id events; game text is kept verbatim; every section has a count cap.
- **R3:** one row per real thing, with where it is and what happened last. The context is only the goal and the screen; there is one prompt path.
- **R4:** a result measures novelty (`progress | changed | no_change | failed`). Stall is counted in decisions, with backoff. Tactical failure stays at the act level. Explore walks until something new is seen. One walker. The settle window is fixed.

## 2. File ownership

**Rule:** each section writes and applies its *own* hunks, including in shared files. WPs that touch a shared file run serially in the §3 order. WPs on a section's own files may run in parallel.

| File | Hunks, in landing order |
|---|---|
| `agent/memory.py` | R1 (observe rewrite, places, deletions), then R4 (removes `relate()` pattern accounting and `patterns`, dead once `predict` goes) |
| `agent/track.py`, `locate.py`, `space.py` | R1 |
| `agent/world.py` | R1 (identity, `_exits` truth, `_failed_exit`, arrival credit, `ref/canon/known/history`, loop closure), then R4 (`explore_dirs`, single walker hooks), then R3 (`options`, labels, `view` without `compact`, `here_flag` removal) |
| `agent/loop.py` | R1 (naming 164-168 removed, `add_edge` call removed, speaker `tid`), then R4 (step structure, result, Stuck, hold removal, explore), then R2 (`observe`/`heard` wiring, T3, stall-fallback/chapter/evidence lines removed), then R3 (act call site, act flags removed) |
| `agent/notebook.py` | R2 (data model, `apply_ops`, `observe`, `heard`, render for planner), then R3 (`render_focus`) |
| `agent/plan.py` | R2 |
| `agent/act.py`, `decide.py`, `brief.py` | R3 (removes `expect`/free path; R4 does not touch `act.py`) |
| `agent/tasks.py`, `predict.py`, `skills.py`, `observer.py`, `trace.py`, `model.py` | R4 |
| `agent/perceive.py`, `brief.GENERAL` perceiver text | frozen (deferred item D1) |
| `agent/env*.py`, `scripts/run_agent.py`, `scripts/watch_run.py` | frozen. The user's live-view work is in progress here (§3) |
| `scripts/eval_run.py` | coordinator, WP S0 (scorecard; removes `prediction_accuracy`/`_repredict` when R4 lands) |
| `scripts/eval_plan.py` | R2 (offline planner replay; exposes `notebook_at(run, step)` for R3). `scripts/eval_act.py`, `eval_frames.py`: the section whose API they call |
| `scripts/check_tracks.py` | R1 (the one frame-replay harness: S1-S3, `--places`, `--exits`), then R3 (`--menu`, `--menu --live --cap 20`). No other new replay scripts |
| `docs/plans/contracts.md` | coordinator, merging each section's contract changes at its gate |
| `tests/agent/*` | owner of the file under test. Regenerating goldens (`w0_golden`) is authorised (§10) |

The user's uncommitted work is not part of RCR: the live-view edits in `env_pyboy.py`, `loop.py`, `model.py`, `observer.py`, `track.py`, `run_agent.py`, `watch_run.py`, `truth_pokered.py` and tests, `check_live_view.py`, `test_realtime_env.py`, and `continuous-live-view.md`. Never revert or overwrite those hunks. RCR builds on top of them (§3). They are staged only with the user's OK at a gate.

## 3. Order

1. **Planning:** all four sections plan in parallel. Done for Phase A.
2. **Phase C builds on top of the uncommitted live-view edits** (user, 10-15). It does not wait for a commit.
   - Preserve every live-view hunk: continuous emulation, the wall-clock path in `wait_settled`, `ModelCancelled`, live snapshots in `track.py`, and the `run_agent`/`watch_run` changes.
   - The live-view tests (`test_realtime_env.py`, the `test_live_*` cases in `test_observer.py`, `test_track.py` and `test_watch_run.py`) stay green in every WP's acceptance run.
   - Each WP re-reads its files from the working tree before editing, in case the live-view work changes again.
   - **Gate commits:** `git add` stages whole files. A gate commit that includes a file still carrying uncommitted live-view hunks would sweep them in, so the coordinator asks the user before such a commit.
3. **Implementation:**
   - **S0** first: the scorecard in `eval_run.py`, with a baseline from `m1_bedroom_live` (0 cloud calls).
   - **Sections:** R1, then R4, then R2, then R3, all in the main tree.
   - Inside a section, WPs on disjoint files may run in parallel. R4.1-R4.3 (observer, predict/tasks, skills) may also run alongside R1.
   - **10-16 (user):** the live-view edits and the R1/R4/R2 loop hunks are committed (8fbe5eb). The "never stage LV files" rule is lifted: R3 commits `loop.py`, `test_loop.py` and any other file it edits with its gate, still preserving every live-view hunk (`LV` green).
   - R2 runs only after R4's gate. R2.1 removes symbols that `loop.py` imports, so a parallel run would turn R4's suite red. This corrects an earlier draft of this item.
4. **Gates:** each section commits at its own gate (tests green, offline scorecard). No push.

## 4. Reality check (applied at every review: solution, packet, implementation, measure)

1. **Invariant, not symptom.** It names what was missing and removes it. "Add a check for X" fails this test.
2. **Generic.** It works unchanged for another tile-based game. Code must not mention door, window, rug, Oak, Mom, "next door" or similar.
3. **Deletes more than it adds**, or justifies each addition. Report net lines per file. This is not a budget.
4. **No steering.** No new gate, filter or forced choice. No prompt sentence standing in for a missing structure. Presentation (order, grouping, labels, history shown) is fine. Grouping rows by history is allowed (user, 10-15): every row stays pickable, and it is reverted if S4 does not move.
5. **Upstream first.** Nothing assumes a section that has not landed. Interfaces are in §7.
6. **Evidence.** Every "fixes X" points at a test, a replay number or a run step. "Should help" counts as not done.
7. **No oscillation.** Check `git log -S`. If a previous phase added or removed the same thing, say what is different now.
8. **One change, one measurement.** If the scorecard does not move, revert. Don't layer more on top.

## 5. Scorecard

These numbers are scored from `truth.jsonl` and run files, for eval only; they are never fed to the agent. The baseline is `runs/m1_bedroom_live`. "Offline" means replayable from cached responses or frames. "Live" needs a new run.

| # | Metric | Baseline | Target | Owner | How |
|---|---|---|---|---|---|
| S1 | ids per real NPC (Pallet NPC / Mom) | 7 / 2 | 1 / 1 | R1 | offline (check_tracks replay) |
| S2 | records minted in 30 Pallet steps | 155 | ≈ distinct things | R1 | offline |
| S3 | same-tile duplicate records | 2 tiles × 2 | 0 | R1 | offline |
| S4 | re-pick of a failed option while an untried one exists | 14 / 99 (R3 counts 15) | ≤ 2 | R3 | live (20-call replay proxy) |
| S5 | quests said closed but still open / quests closed | 1 / 0 | 0 / every completed one | R2 | offline |
| S6 | real failures not scored as failed | 14 of 18 `surprise` | 0 | R4 | offline replay of `result` |
| S7 | steps: bedroom to 1F / 1F to Pallet | 14 / 21 | ≤ 5 / ≤ 8 | all | live |
| S8 | act prompt max / planner prompt max (chars) | 5.6k / 10.1k | ≤ 2.0k / R2 sections ≤ 3.5k | R3 / R2 | offline |
| S9 | model calls per new tile (Pallet) / name follow-up calls | 75 / 18 tiles; 11 | lower; 0 | R4 / R3 | live / offline |
| S10 | `agent/*.py` lines | 4162 (+ live-view edits) | net negative (planned about -1100) | all | `wc -l` |
| S11 | unsettled rows | 15 | ~0 | R4 | offline settle probe |
| S12 | planner reply chars | 1.1k-4.0k | ≤ 600 | R2 | offline (op transcription) |
| S13 | `done` on a targeted quest without a memory event | n/a | watch only, never a gate | R2 | live |
| S14 | re-adds of a finished target (an `add` whose target equals a closed quest's target) | n/a | watch only. If it rises, R2's deferred "Done so far" section comes back | R2 | offline (eval_plan `ops`) / live |

S0 builds S1-S3, S5, S6, S8, S11 and S14 into `eval_run.py`, reusing `_entity_duplicates` and `_lifecycle`.

**Generality check (every section):** a word grep on added `agent/` lines must exclude `agent/env_pyboy.py`. Its uncommitted live-view lines contain `window="SDL2"`, verified 10-15, and `door` is a generic exit term in this code. The word list is `mom|oak|pallet|prof|rug|kitchen|bedroom` (plus `next door` and `door mat`).

## 6. Live-run budget

- No live runs during planning.
- Proposed:
  - **one** live run after R1 (S1-S4, S7);
  - **one** after R3 (all metrics);
  - R3's 20-call model replay (S4 proxy).
- Each needs user-approved ledger caps.
- Runs start from a clean boot. Old checkpoints are unsupported after RCR, and their migrations are deleted (R2, R4).

## 7. Interfaces between sections (agreed at Phase A)

| From | To | What |
|---|---|---|
| R1 | all | refs `thing:<id>`, `exit:<x>,<y>` (place-scoped), `place:<id>`; `World.canon`, `known`, `ref(text)` (also parses our printed labels), `place_label` |
| R1 | R2, R3 | `World.history(ref)`: thing → `called`, `talked` (steps plus latest said text), `checked` (text or nothing), `walked_into`, `status`; exit → `status`, `uses` (departures), `dest`, `blocked`; place → `visits`, `first`, `last` |
| R1 | R4 | `crossings` counts departures only; `verified_edge` = first departure; stable intent key (place, verb, ref) |
| R1 | R3, R4 | no screen-edge exit records. Open edges are explore directions (R4 `explore_dirs`, rendered by R3) |
| R2 | R3 | open quests in order; the top quest's text, target label and **cited verbatim game-text lines**. No `done_when` (targets replace checks). Act gets no beliefs, notes or planner `why` |
| R2 | R4 | `apply_ops` results. R4's backoff `u` counts a stall replan as "changed nothing" when it has zero `ok` results |
| R4 | R2 | T1 on new game text; T3 on closure; T4 stall with backoff (`K·2^u` decisions); never a per-failure trigger. Loop calls `notebook.observe(step, place, talked)` after each settled step and `heard()` at dialogue flush |
| R4 | R3 | `result ∈ {progress, changed, no_change, failed}` + reason; `fails[intent] = {n, why, step, stage}`, where a blocked move counts as a failure; `explore_dirs(place)` |
| R3 | — | `act(model, per, focus, options, buttons, controls) -> Act(action, text, why, option)`. Rows carry `ref` and `state` |

## 8. Reality-check log

| Date | Claim | Evidence | Verdict |
|---|---|---|---|
| 10-15 | Matching needs name distance ≤ 1 before tile | `memory.py:269-271`, read by coordinator | true |
| 10-15 | Wall exits can't be refuted | `world.py:198-204` needs `floor is True` | true |
| 10-15 | Explore = nearest frontier plus one step | `loop.py:439-452` | true |
| 10-15 | Replan-on-failure added, then removed by QS | `git show 266681e`. R4: added by 1fd572d/e5420d3 (HF), 24 of 41 plan calls were urgents | true; cause = a strategic reactor on a noisy tactical signal |
| 10-15 | 14 of 99 failed re-picks with untried alternatives | coordinator script over `calls.jsonl` | true |
| 10-15 | `self_spot` is dead | R1: the constant *is* the self key in a camera-follow game (self-location 63/63) | **audit wrong**: keep it (ceiling: non-scrolling games) |
| 10-15 | Outdoor settle failures come from water animation outliving the 24-frame recurrence window | R4 emulator probe from checkpoint 100 (y ≥ 10 times out at 600 frames) | claimed; to be re-proved by the R4 Phase C test |
| 10-15 | R4 gate S6 (0 of 14 mis-scored) | fixture test uses a novelty proxy (scroll or next_place), not `world.novel` | proxy. Re-check with S0 on the real wiring before claiming S6 |
| 10-15 | R4 stall K | the largest K that fires inside S64-73 of one run | a knob fit to one run. Don't re-tune per run; watch it in live runs |
| 10-15 | 13 of 19 planner quest objects restate prior state | R2 transcript analysis | claimed; re-checked by R2 offline check 3 |
| 10-15 | The decide path's dialogue presses come from stale context, not a missing sentence | R3: the S36/S38 why cites world flags | claimed; 2 calls in R3's replay decide it |
| 10-15 | S0 baseline — S1: 6 confirmed ids for the Pallet NPC / 2 for Mom | `rcr_scorecard.S1_ids_per_real_npc`: same-step truth joins for map `0x00`, slot 2 / map `0x25`, slot 1; historical actor evidence confirms ids. Actor matches cover 27/36 sprite-bearing world rows; all proximity matches cover 30/36. Three nearby scenery ids / one nearby chair id remain ambiguous | measured; the earlier 7 includes an unconfirmed id, not a seventh proven sprite match |
| 10-15 | S0 baseline — S2: 155 records in 30 Pallet steps | `S2_pallet_records_first_30`: valid map-0 truth rows S76-S105, place `p_place_4`, entity birth window S75-S105. Includes the incoming action's after-observation and pending rows; excludes dimensionless intro map-0 rows | measured; same-step truth describes the before-frame, while memory births include the after-frame |
| 10-15 | S0 baseline — S3: 20 extra records in 19 name/tile groups at 8 tiles | `S3_same_tile_duplicates`: final records grouped by place, normalized name and exact tile; existing `_entity_duplicates` proxy = 58 | proxy, not the earlier manually inspected 2 tiles × 2; different labels and legitimate co-visible things cannot be resolved from this grouping |
| 10-15 | S0 baseline — S5: 1 quest said closed/dropped but still open / 0 actually closed | `S5_false_closures`: `find_exit_place_2`; explicit-key planner notes compared with all final quest statuses | measured lower bound; paraphrased claims without an exact quest key are not inferred |
| 10-15 | S0 baseline — S6: 13 of 18 confirmed no-movement failures were not scored failed | `S6_failures_not_scored_failed`: all 27 failed/blocked actions have truth S/S+1 joins; 18 leave map/x/y unchanged, and 13 have neither `failed` nor legacy `miss` verdict. Three unsettled no-movement rows without failure evidence remain unknown | measured subset; unlike the earlier 14 count, an unsettled `done` action is not assumed to be a real failure |
| 10-15 | S0 baseline — S8: act prompt max 5,595 / planner prompt max 10,084 chars | `S8_prompt_max_chars`: recorded user prompt text; system-plus-user maxima are 6,158 / 11,298 chars | measured; primary values use the same user-text basis as the prior audit |
| 10-15 | S0 baseline — S11: 15 unsettled rows | `S11_unsettled_rows`: 6 pending + 9 timed out | measured |
| 10-15 | S0 baseline — S14: unavailable | `S14_finished_target_readds`: no explicit `add` operation history in this run; output has `available: false` | not an observed zero; explicit add/target history is required |

### S0 Result (10-15)

- Baseline command, run by the coordinator: `.venv-agent/Scripts/python.exe scripts/eval_run.py runs/m1_bedroom_live --no-write` — **exit 0**, numbers above. Run files were not written; **0 agent cloud calls**.
- Required suite, run by the coordinator: `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider -n auto` — **exit 1: 650 passed, 1 failed** in 87.92 s. `tests/agent/test_world_golden.py::test_act_eight_steps` finds an extra generated `memory/mints.jsonl` missing from the tracked golden. The isolated test reproduces this failure (**exit 1**, 17.05 s) without using `eval_run.py`; the writer in `agent/memory.py` and the golden test/fixture are unchanged by S0 and outside its three-file scope.
- One scorecard repair round used the same **gpt-6-luna, reasoning high** worker, with measured failure output: S1 had counted nearby scenery as NPC ids and S2 had skipped pending rows and the incoming after-observation. After repair, coordinator command `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_eval_run.py -q -p no:cacheprovider -n auto` reports **43 passed**, exit 0 (2.76 s), and the baseline command exits 0. No further repair or tier escalation was attempted.
- **Coordinator, 10-15: gate passed.**
  - The one failure came before RCR. Commit 94780b4 added the `mints.jsonl` writer without regenerating the golden.
  - The golden was regenerated (authorised, §10). It adds only `w0_golden/act/memory/mints.jsonl`.
  - Full suite: **651 passed, 0 failed**. Baseline: exit 0.
  - S0 is committed. Next: R1.
- (orchestrator, before the coordinator's fix) **Gate blocked; no staging, commit or push.** The requested `RCR S0: scorecard + baseline` commit is withheld because the required full suite is not green. Only `scripts/eval_run.py`, `tests/agent/test_eval_run.py` and this §8 were edited for S0.
- Net lines: `scripts/eval_run.py` **+248**, `tests/agent/test_eval_run.py` **+72**, MASTER §8 **+17**. S0 adds the requested offline measurements and one fixture covering the metrics; it changes no agent behavior. Final SHA-256 checks match the starting bytes of all 18 protected live-view files; sections outside MASTER §8 are unchanged, and the Git index is empty.
- Preserved and not staged: `agent/env_pyboy.py`, `agent/loop.py`, `agent/model.py`, `agent/observer.py`, `agent/track.py`; `scripts/run_agent.py`, `scripts/truth_pokered.py`, `scripts/watch_run.py`, `scripts/check_live_view.py`; `tests/agent/test_env.py`, `tests/agent/test_loop.py`, `tests/agent/test_model.py`, `tests/agent/test_observer.py`, `tests/agent/test_run_provenance.py`, `tests/agent/test_track.py`, `tests/agent/test_watch_run.py`, `tests/agent/test_realtime_env.py`; `docs/plans/continuous-live-view.md`; `.claude/`.

### R1 partial attempt (10-15): stopped before gate

- The orchestrator stopped during R1's first batch after the user-authorized final same-tier attempt failed. Every worker spawn explicitly used **gpt-6-luna, reasoning high**; no tier escalation occurred. Details and commands: [R1 §9 Result](R1-identity.md#9-result-filled-in-by-the-orchestrator).
- Entry checks: **47 live-view tests passed**; full suite **651 passed**, exit 0. R1.1 acceptance then failed three times, each **70 passed / 1 failed**, at `test_realtime_env.py:199` (`test_state_load_changes_generation_and_wakes_frame_waiter`). Its suspected closure/generation race is an inference; protected files were not edited.
- R1.4 acceptance, run by the orchestrator after its concurrent patch finished: **160 passed / 1 failed**, exit 1. The remaining failure is `test_world.py:126` calling retired `Memory.resolve`; its live-view tests passed. No later WP or further repair was started.
- R1.7 provisional pre-R1 replay: Mom/Pallet NPC **5/1 IDs**, map-0 records/floor **159/119**, same-tile relabel mints **45**, jitter p50/p95/max **0/2/4**, phantom verified exits **1**. Replay diverged at **23** steps; the final place-report filter change was not reaccepted. These are incomplete harness baselines, not a completed R1 score or S0 replacements.
- **No gate, commit, golden regeneration, push or live run.** Post-R1 S1-S4/S7 are unavailable; `runs/rcr_r1_live` was not launched. **0 agent cloud calls.** R1.2/R1.3/R1.5/R1.6/R1.8 remain unstarted; R4 must still wait for R1's gate.
- All **18 protected live-view files** match their entry SHA-256 hashes; all existing live-view edits survive unstaged. Partial R1 files and Result/handoff edits remain unstaged; index empty. Current partial agent net **-112 lines** (memory -98, world -14), not a gate deletion-ledger entry.

### R1 resumed result (10-15): gate failed, no live run

- The user resolved the protected-test race and stale retired-API assertion, then authorized continuation. Fresh combined entry acceptance: **185 passed**. All workers remained explicitly **gpt-6-luna, reasoning high**. Usage-limit interruptions were resumed only after the user explicitly allowed the workers to finish.
- **G2 failed:** `PY scripts/check_tracks.py m1_bedroom_live --first 51` (exit 0) reports **Mom 2 IDs / Pallet NPC 1**, against **1 / ≤2**. `STATIC_GATE=1` was retained; no elimination logic or threshold tuning was added. The join is proximity-based and replay diverges at **23** steps; do not treat this as proof that both IDs belong to Mom.
- The same command reports **39** map-0 records / floor **69**, **0** same-tile relabel mints, static jitter p50/p95/max **0/2/3**, **11** nearest-distance-2 mints. Corrected pre-R1 wrapper baseline: **139/74**, **76** relabel mints, Mom/Pallet **3/1**, jitter **0/1/3**. The floor depends on matched-record class and changes across implementations, so G4's numeric pass is provisional. Details/logs: [R1 §9](R1-identity.md#9-result-filled-in-by-the-orchestrator).
- **G7:** one reported place ID each for maps **38,37,0**. **Corrected G8 passed:** phantom uses **0**, mat verified (**1 departure**), stairs **unverified with destination retained/routable**, **2** bumped candidate tiles refuted (labels **2× / 1×**). The user corrected G8's earlier contradictory stairs-verification requirement; the departure-only rule was retained. A generic known-destination label was added as explicitly requested.
- Final combined WP acceptance: **354 passed, 1 failed**, exit 1, 86.47 s. The remaining new self regression supplies `frame=None` to `observe_after`, causing `world.py:271` to call `None.tobytes()`. The standalone self probe passes with only the NPC ID. R1.5's final permitted same-tier attempt is complete; no further repair was layered on. **Final G1 full suite: 656 passed, 2 failed**, exit 1, 95.13 s; the other failure is the unregenerated golden (`.tmp/rcr1/final-suite.log`).
- **No gate commit, staging, push, golden regeneration or post-R1 live run.** Live S1-S4/S7 are unavailable; **0 agent cloud calls**. R4 still waits on R1. G10's prohibited-word scan is clear. Agent deltas: memory **-99**, world **+95**, R1-only loop **-4**, total **-8** (report-only, planned ~-210 not achieved).
- **15/18** protected live-view files remain byte-identical to the resumed baseline; the three shared files with R1 changes are `agent/loop.py`, `tests/agent/test_loop.py`, `tests/agent/test_track.py`. All original live-view added lines remain and their live tests are byte-identical. These files stay unstaged; index empty. Contract changes remain a proposal in R1 §9; `contracts.md` is untouched.
- The packet's exact four-file live-view command passed **47/47 before and after** this work; final exit 0, 4.30 s (`.tmp/rcr1/final-live-view-tests.log`).

### R1 finalization after Unblock 2 (10-15): offline gate passed

- Orchestrator rerun `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto`: **658 passed**, exit 0, 104.36 s (`.tmp/rcr1/finalize-suite.log`). The user corrected the self test's missing frame and regenerated the golden; independent gpt-6-luna/high review confirms exactly four changed memory files and **no trace change**.
- G2's former second Mom ID was a nearby chair falsely joined by the harness. The corrected nearest-per-sprite/per-frame join reports **Mom 1 / Pallet NPC 1 ID**, **0 switches**, against corrected original-code **3/1**. Gates/thresholds and world behavior were not changed to produce this pass. The counts cover matched observations, not coverage.
- Final G4–G8 reports: **39 records / floor 69** (≤86.25); **0** same-tile relabel mints; static jitter **0/2/3**, distance-2 mints **11**; one place ID per map **38/37/0**; phantom uses **0**, mat verified (**1 departure**), stairs unverified with destination/routability retained, **2** bumped candidate tiles refuted. Keep the **23-step replay divergence** and record-class-dependent floor caveats. G9 agent delta **-8**; G10 clear. Exact commands: [R1 §9](R1-identity.md#9-result-filled-in-by-the-orchestrator).
- Gate commit **d787836** (`RCR R1: offline gate passes, one ID per NPC and zero relabel mints`) includes only eligible R1 files. R1 hunks in loop.py/test_loop.py/test_track.py and all existing live-view work remain unstaged; no push. `contracts.md` remains unchanged, with its gate-approved proposal in R1 §9.
- First clean-boot live run `runs/rcr_r1_live`: **150 rows / 6 HTTP requests**, runner/scorer exit **0**, **0 world rows**, **147 unobservable verdicts**, **145 stale scene_cut rows**. S1/S2 unavailable; S3 proxy **0** over **0 groups**; S4/S7 unmeasured. Temporary launcher used the approved M1 bucket for the original output name with unchanged caps; ledger **3756/3708/48 → 3762/3714/48** (total/M1/smoke). This run did not establish live identity improvement.
- **Final user-authorized retry:** after one retry was stopped at **5 rows**, the user requested another attempt capped at **80 steps**. `PY scripts/run_agent.py --run runs/m1_rcr_r1_live_retry2 --steps 80 --max-calls 400 --decide` and `PY scripts/eval_run.py runs/m1_rcr_r1_live_retry2` both exit **0**. Viewer HTTP **200**, clean boot, provenance **complete**. **80 rows / 142 HTTP requests**, **212 asks / 70 cache hits / 0 retries**; **22 world rows**, first **S49**; **80/80 settled**, zero stale/pending/timed-out; verdicts **73 hit / 5 miss / 2 surprise**.
- **Final live S1-S4/S7:** S1 unavailable (**0 NPC samples**); S2 unavailable (no Pallet); S3 proxy **0 duplicate records / 0 groups / 0 tile groups**, insufficient coverage for a live duplicate claim; S4 unmeasured (not emitted by evaluator; its **3/80 repeated_mistakes** is a different proxy); S7 unmeasured (neither transition reached). Valid-dimension truth remained map **38**, one place **p_place**. The 80-step cap was respected; no extra run.
- Final retry ledger total/M1/smoke **3786/3736/50 → 3928/3878/50**, **+142** for this invocation, **887** remaining under unchanged persistent caps. User runtime fixes after the gate are fingerprinted in the run; **658 passes** remain evidence for the earlier gate snapshot. All **18** protected hashes are unchanged during the final retry. No runtime code edits, shared-file staging or push. Logs/limitations: [R1 §9](R1-identity.md#9-result-filled-in-by-the-orchestrator).

### R3 partial attempt (10-16): stopped before gate

- Every spawn explicitly used **gpt-6-luna, reasoning high**. R3.2 first acceptance: **100 passed / 3 failed / 2 collection errors**, exit 1, 8.12 s; Loop imported removed brief.defaults. R3.4's owned loop hunk repaired that dependency. Second acceptance: **136 passed / 12 failed**, exit 1, 11.63 s. The user-authorized extra same-tier attempt received measured failure output and ended at **143 passed / 5 failed**, exit 1, 12.55 s. Exact command: `.venv-agent/Scripts/python.exe -B -m pytest -q -p no:cacheprovider -n auto tests/agent/test_options.py tests/agent/test_exits.py tests/agent/test_world.py`. Logs: `.tmp/rcr/r3/r3_2.log`, `r3_2-after-wire.log`, `r3_2-final.log`. **No further source repair.**
- Remaining failures: two tests call startswith on free-buttons ref=None (`test_exits.py:107`, `test_options.py:120`); `test_options.py:112` expects exit: but gets place:cave; `test_options.py:196` misses two-place crossed labels; `test_world.py:629` retains removed seen["places"]. Details: [R3 §9](R3-decide.md#9-result-filled-in-by-the-orchestrator).
- R3.1 `PT tests/agent/test_act.py`: **6 passed**, exit 0, 1.83 s. R3.3 `PT tests/agent/test_notebook.py -k focus`: **4 passed**, exit 0, 1.85 s. Exact packet LV **20 passed** at entry and after each completed acceptance; final exit 0, 3.58 s (`.tmp/rcr/r3/r3_2-final-lv.log`). R3.4 interrupted; full suite/help/grep and offline menu replay were not run. No full-suite pass claimed.
- **S1–S9 and S11–S14:** no post-R3 measurements. Replay coverage **0**; rows/max/mean, prompt maxima, duplicates, missing actions, follow-ups and ordering unmeasured. **S4 unmeasured; R3.6 never applied**, so no grouping keep/revert decision. **S10:** partial agent lines **4063 → 3988**, net **−75** (act −43, brief −7, loop −29, notebook +1, world +3), report-only, not a gate pass. Audit: `.tmp/rcr/r3/final-audit.log`.
- **0 cloud calls; no R3.7 or post-R3 live run.** Pre-approved replay cap20/conditional+2/+20 and clean-boot **150 steps / 400 calls** were not exercised because gate failed. `runs/rcr_r3_live` does not exist. Baseline-only `PY scripts/eval_run.py runs/m1_bedroom_live --no-write` exited 0 (`.tmp/rcr/r3/baseline-eval.log`); it is not post-R3 evidence. Viewer /state HTTP **200**. Ledger unchanged **4027 total / 3977 M1 / 50 smoke**, **788** remaining under approved caps. Smoke exhausted; prior funded-M1 launcher approach would be needed for non-m1 output path; no launcher created or used.
- Preservation: **10/10** protected SHA-256 hashes, **4/4** loop live methods, **2/2** test-loop live functions unchanged; **0** prohibited-word matches in added agent lines, env_pyboy excluded. Initial tree clean; live-view committed at **8fbe5eb**. No uncommitted user LV hunks were swept in. Every partial change including loop/test-loop stays **unstaged**; index empty, no gate commit/push.
- Coordinator migrated additional test_loop/test_trace_brief/test_harness_reliability callers, without full-suite acceptance; worker files stayed in cards. Interrupted R3.4 script edits retain a DECIDE reference after deletion of its definition. R3.5 read-only prep only, check_tracks.py unchanged. `contracts.md` unchanged; provisional contracts and per-file deltas in R3 §9. Final-attempt allowance exhausted; no automatic continuation.

### R3 resumed after overseer fixes (10-16): one assertion still blocks gate

- User authorized continuation and resuming both interrupted workers; all workers retained explicit gpt-6-luna/high tier. Overseer's place-scoped World.label/assertion/DECIDE fixes independently passed **148 tests**, exit0, 11.96s: PT tests/agent/test_options.py tests/agent/test_exits.py tests/agent/test_world.py (.tmp/rcr/r3/resume-r3_2.log). Initial R3.2 failures above are superseded.
- R3.4 fixed _live's removed a.place access with canonical world.place_label(place). Root PT tests/agent: **24 failed / 587 passed**, exit1,83.80s (resume-full-1.log). Coherent script/test migration: **1 failed / 608 passed**, exit1,73.29s (resume-full-2.log). User's extra final same-tier attempt/review received failure output; coordinator applied the recommended existing failure stub in its unowned test. Final full suite: **1 failed / 608 passed**, exit1,110.80s (resume-full-final.log). No further repair.
- Remaining test_loop.py:1488 assertion rejects cause: anywhere in focus; it also matches the required **Because:** heading. Coordinator introduced this test assertion. Failed-row evidence assertion now passes. The suggested line-prefix correction was recorded, not applied.
- Golden W0_REGEN=1 PT tests/agent/test_world_golden.py: **1 passed**, exit0,14.38s; **8 trace rows** changed for numbered replies/follow-ups, **memory unchanged**, eight steps/15 asks; regeneration env cleared. Both eval scripts --help exit0; loop legacy-call grep0matches. Extra root test migrations/removed two obsolete free/image CLI tests are detailed in R3 §9.
- Last completed exact LV: **20 passed**, exit0,15.12s (resume-lv-1.log). Concurrent final LV rerun was interrupted after worker startup, exit1/no test result (resume-lv-final.log); no final exact LV pass claimed. Full suite's sole failure is unrelated to live-view tests.
- **Gate FAIL; 0 cloud calls, no commit/push.** R3.5 read-only prep only; no grouping/model replay/fallback/live run. S1–S9/S11–S14 post-R3 unavailable, coverage0. S10 remains **4063 → 3988, −75 agent**. Current per-file deltas in R3 §9.
- Audit resume-final-audit.log: protected hashes **10/10 unchanged**, loop live methods **3/4 unchanged** (_live canonical-label change required), test-loop live tests **2/2 unchanged**, prohibited words0. Ledger unchanged **4027/3977/50**, 788remaining; runs/rcr_r3_live absent. All changes unstaged/index empty; frozen files/contracts.md untouched. Final retry allowance exhausted; no automatic continuation.

### Baseline live run, post R1/R4/R2 (10-17): `runs/m1_rcr_baseline`, stopped by the user at 74 of 150 steps

- **Setup:** HEAD dc4113f code from a clean scratch worktree with no links, clean boot, `--decide`, using the original runner. 186 requests; ledger 4027 → 4144 (671 left). The ledger file was recreated at 4027/3977/50 after the 10-16 deletion.
- **Holds:**
  - Place identity: 1 place per map (38/37/0).
  - S3: 0.
  - S6: 0 of 4 real failures mis-scored.
  - S11: 0 unsettled.
  - S5: 0, with 1 quest really closed.
  - S14: 0.
- **Fails or open:**
  - S1: Mom 2 ids live (`e_character`, `e_person`), so R1's offline 1 id is not reproduced.
  - S8: act prompt 2,639 chars (R3 has not landed); planner 3,972.
  - S2 and S7 unavailable: Pallet was entered at S51 for 1 step, then the agent walked back inside.
  - Planner fired "no open quest" 6 times on the intro screens (S1-S16), out of 13 plan calls.
  - Directive "Oak next door" heard at S45, not followed: the agent went back to 2F at S59, bounced between floors at S69-72, and repeated failed `approach` on furniture (computer ×3, shelving ×5, cabinet ×3).

- **Plan and map analysis (10-17):**
  - **Oak quest closed by talking to Mom:**
    - The quest target first resolved to the title-screen "Professor Oak" record. Intro records live in the run-start place, which becomes the bedroom, so they are listed as bedroom things.
    - At S39 the planner retargeted the quest to "person" (Mom) "to complete the quest", and `observe` closed it.
    - The closed line says "talked to person", a stale label; the planner then believed it had spoken to Oak.
  - **Directive lost:**
    - Mom's "Oak, next door" (t2, S45) hit the belief cap, which 4 intro trivia beliefs had already filled. No quest was added for it.
    - The planner retargeted 4 times to the nonexistent "desk", getting the same error each time with no valid names listed.
  - **"Explore the tiled room":** "tiled room" is the bedroom's label. At S52 in Pallet, act picked "go to tiled room" and walked home.
  - **Bedroom map shift (−4, +5) at S59:**
    - `memory.py` matched the place through a within-1 exit, but the arrival tile was not carried over.
    - `world.after` uses an exact-tile lookup, which missed; 1F's position had drifted 1 tile.
    - `pos = arrive or (0, 0)` (world.py:375) then re-rooted the bedroom at the stairs. Result: computer ×3, desk ×2, and a wrong reverse exit written.

### Phase D (10-19): R3.7 replay and post-R3 live run `runs/m1_rcr_post_r3` (3db80df, clean boot, 150 steps)

- **R3.7:** 12 requests. S4 proxy 0 of 8; grouping stays and no ablation ran. Controls 1 of 3, but the replay's focus is thin. At S52 the agent no longer walks home.
- **Live run:**
  - 150 rows, 280 HTTP requests: perceiver 87, act 142, plan 38, skill 13. Ledger 4156 → 4436, 379 left.
  - Reached Pallet (map 0) at **S98**; the baseline only stepped out for 1 step. Places: 1 id per map (bedroom p_place_2, 1F p_place_3, Pallet p_place_4).
  - Then it bounced between 1F and Pallet through its own front door (S98-S139). It never found the route north.
- **Scorecard:**

  | Metric | Result | Note |
  |---|---|---|
  | S1 | Pallet NPC **4 ids**, Mom 2 (one is a chair proximity join) | regression vs offline |
  | S2 | **58** Pallet records in the first 30 steps | S0 baseline 155 |
  | S3 | 1 | |
  | S5 | 0 false, 7 closures | |
  | S6 | 0 of 14 mis-scored | |
  | S8 | act **3,008** (> 2,000), planner 4,102 | |
  | S11 | 0 | |
  | S14 | 0 | |
  | repeated mistakes | 1 | |

- **Root causes found (not fixed):**
  1. **Dead-loop at S144-150.**
     - Option 17, "go to house, not on screen", produced `go_to thing:e_house`, then choice None, 0 inputs, `unobservable`.
     - No failure was recorded, so the next prompt was identical. The cached act reply repeated it for 7 steps.
     - Two faults: an off-screen remembered row whose action does not execute (R3 grouping / R1 resolve), and a zero-input step scored `unobservable` instead of `failed` (R4 result).
  2. **Intro planner waste:** 17 plan calls at S1-S49, all `add` errors "why must cite game-text keys, none". The Oak intro became t-lines only at S53. T5 "no open quest" fires on title screens, and R2's cite rule makes `add` impossible there (R2 / R4 trigger).
  3. **Quests close instantly.**
     - The planner sets `target` to the place it is already in, or the thing it stands on, and `observe` closes the quest at once: q2-q4, q6, and q5 via the door mat "talked".
     - `target` is read as "done when here", but the planner uses it as "where to work". R2 contract.
  4. **S8 live:** the Pallet menu has 41 rows: 27 off-screen or remembered rows, walls and hedges perceived as exits, and a "dialogue box" thing. The 1,909-character offline gate did not cover outdoor scenes (R3, plus perception of exits).

### Phase E (2026-10-19): RCR closeout, `runs/m1_rcr_phase_e`

**Verdict: FAIL. RCR is closed.** The live confirmation reached 150 trace rows but failed the top-five pass rule because 2 selected menu actions sent zero inputs. S4 also exceeded its threshold on an explicitly reconstructed-menu proxy. No agent fix or second run follows this result; further work starts as a new program under §8 of the Phase E brief.

| Root cause / metric | `post_r3` baseline | Phase E result | Verdict and evidence |
|---|---:|---:|---|
| D7 same pick, zero inputs, same prompt: longest qualifying run | 7 | 1 | **Pass** (limit ≤1). Two zero-input actions occurred, at steps 87 and 135, but did not form a qualifying repeated streak. |
| D5 empty closures | 5 of 7 | 0 of 2 | **Pass** (limit 0). q1 opened 19 and closed 42 after name acknowledgement/speech; q2 opened 48 and auto-closed by `observe` at 142 when its target place was reached. Neither closed on its opening step or without an arrival/speech event. Confirmed in `memory/notebook.history` and trace events. |
| D6 intro planner waste: plan calls before first heard t-key / “none heard yet” errors | 17 / 17 | 0 / 0 | **Pass** (limits ≤ new-text events / 0). First T1 heard at step 18; first plan call was step 19 and included t1. |
| S8 act prompt max | 3,008 user / 3,277 combined | 1,318 user / 1,587 combined | **Pass** (≤2,000). Planner prompt was 4,037 user / 4,758 combined; maximum planner reply was 551 chars (≤600). |
| Menu rows selected but not executed | ≥1 | 2 | **Fail** (limit 0). Step 87 trace row: choice intent `p_place_3|interact|thing:e_desk_2`, perceived at `[0.42,0.51]`; action `{"thing":6,"verb":"interact"}`; skill `blocked / target is the player / inputs=0`; `presses.jsonl` step 87 has `keys: []`. The on-screen `World.options` thing path offered it without the shared same-tile guard; `skills.interact` rejected it. Step 135 trace row: choice intent `p_place_3|approach|exit:-5,6`, label `go to door mat, here`; action `{"go_to":"exit:-5,6"}`; skill `done / inputs=0`; `presses.jsonl` step 135 has `keys: []`, and verdict is failed. |
| S1 ids per real NPC (Pallet NPC / Mom) | 4 / 2 | map `0x00:slot-2`: 1 ID / 1 observation (`e_fence_post_1`, landmark); map `0x25:slot-1`: 2 IDs / 41 matched observations (`e_chair_2`, `e_person`) | **Limited; no pass claimed.** The `e_chair_2` proximity join is ambiguous; map-0’s single observation is a fence-post landmark, not NPC evidence. The run supplies no adequate Pallet NPC/Mom population sample. Overall scorer max was 2 IDs; actor matched 42/44 truth sprite rows, proximity matched 43/44 (97.7% coverage), with 0 unmatched actor IDs. |
| S2 records minted in first 30 Pallet steps | 58 | unavailable | **Limited.** Map 0 first appears in truth at step 150, so no 30-step Pallet sample exists. |
| S5 false closures | 0 | 0 (2 actually closed) | **Pass.** `summary.json` reports zero false closures. |
| S6 real failures not scored as failed | 0 / 14 | 0 / 9 real no-movement failures | **Pass.** 24/24 action failures joined to truth; 9 were confirmed no-movement failures and all 9 were scored failed; unknown unsettled rows 0. |
| S4 re-picks of a failed row while another untried row exists | not measured live | 4 reconstructed cases | **Fail** (limit ≤2), explicitly a replay proxy, not a live menu capture. Trace picked-state plus reconstructed menus identify steps 2, 113, 136, 137. The `check_tracks --menu --first 1` diagnostic exited 1 on `order_steps=1 [2]`; its remaining report was 150/150 rows, max 12 rows, max 1,111 chars, duplicate 0, no-action 0. Interpret S4 with that limitation. |
| Progress: first step on map 0 (Pallet); left Pallet northward? | S98; no | map 0 first at S150; no later step | **Limit missed; report only.** The run entered map 0 at the final step, so it could not demonstrate northward departure. |

**Gate and run evidence.** The inherited Phase E implementation is `49d8b82`; Item A’s shared-function change and regression test are in `d473f7c`. Step 1’s required suite completed with **630 passed, 1 skipped**. Step 2 regenerated the golden successfully with no golden diff. The previous-run offline gate passed: `eval_plan --check all` exit 0; `check_tracks --menu --first 1` 150/150, max act prompt 1,541, duplicate 0, no-action rows 0. For the Phase E run, `eval_run.py --health` and `eval_plan.py --check all` both exited 0; these exit codes do not mean the live gate passed. Health fields included `cut_self_loops=10` and `repeat_actions_max_length=13`; actual notebook history/events were checked for D5. The run produced 150 trace rows and ended at `p_place_4`. Ledger usage moved from 4,436 to 4,626: **190 spent, 189 total remaining**. The detached process output showed completion through step 150 with no traceback, but the operating-system exit code was not captured.

**Audit symptoms against the 10-15 audit:**

- Repeated mistakes — **improved (number):** D7 qualifying run fell from 7 to 1 (like-for-like). S4 has 4 reconstructed-menu proxy cases, but that count is not comparable to the audit’s 14/99 live denominator and does not establish a like-for-like reduction. The proxy exceeds its limit, and health still reports 13 repeated actions and 10 cut self-loops.
- Pointless actions — **open:** steps 87 and 135 selected actions that sent zero keys; the run reached Pallet only at step 150.
- Not following or prioritizing directives — **open:** the planner recorded Mom’s “next door” text, but q3 was created only at step 150 with the current place as its target; the run ended before it could pursue Professor Oak.
- Plans are not concise — **improved (number):** planner prompt max fell from 10,084 user chars in the baseline to 4,037; Phase E max reply was 551 chars, within the 600-char target. The combined planner prompt maximum was 4,758 chars; individual section-cap compliance was not measured here.
- Misrecognition made fatal — **open:** the desk-at-player selection at step 87 was rejected by `skills.interact` after appearing as an on-screen option; S1 live joins are limited and ambiguous. No perceiver prompt change was made.
- “Already talked to / already done” tracking — **improved (number):** 2 quests actually closed, 0 false closures, 0 finished-target re-adds; q1 closed after acknowledgement and q2 closed on target arrival. Actor identity remains ambiguous in the available S1 joins.

RCR is **CLOSED — LIVE GATE FAIL**. The no-layering rule stands: any follow-up is a new program, not another RCR amendment or live run.

## 9. Deletion ledger

| Section | Planned removals (Phase A) | Net lines | Commit |
|---|---|---|---|
| R1 | `Memory.view`/`resolve`, landmark and name merges, `#n` suffixes, `add_edge`/edges, arrival credit, `named_intents`; `locate.py` if it has no runtime caller | ≈ -200 | — |
| R2 | `_EXPLORE`, `_TACTICAL`, the reconcile rules, chapters, checks, migrations, `_fit`; tests ~770 → ~250; `eval_plan.py` -155 | ≈ -490 agent | — |
| R3 | free act path, name-list rows, `OPTION_CAP`, compact view, `here_flag`, act flags, brief prefs | **−75 agent, partial / gate failed** (4063 → 3988); plan ≈ −160 | **none; unstaged, stopped 10-16** |
| R4 | `predict`/`verdict`, the hold machinery, relook, cycle flags, `same_action`, the side-step push, migrations | ≈ -300 | — |

## 10. Decisions

**10-15, user:**
- RCR program. Four Opus planning sessions (xhigh) with Haiku workers. The coordinator approves solutions before packets are written.
- Cleanup of overfit code is authorised. This includes regenerating goldens and dropping old-checkpoint support.
- R3 may group menu rows by history ("Not tried / Worked / Failed here", all pickable). This supersedes OM generality rule 3 for RCR. Revert it if S4 does not move.
- Phase C builds on top of the uncommitted live-view edits and preserves them; it does not wait for a commit (§3).

**10-15, Phase A rulings by the coordinator:**
- **R1:**
  - Q1: the static gate stays at 1. It is a knob, set by measuring projection jitter in the replay.
  - Q2: no re-identification by elimination. Add it only if S1 misses after R1.
  - Q5: screen-edge exit records are dropped, in favour of explore directions.
  - Q6: goldens may be regenerated.
- **R2:**
  - Q2: the planner's judgement decides; `done` and `drop` need only a reason. S13 watches this.
  - Q3: `_TACTICAL` is removed; watch the first run.
  - Q4: the caps 5/5/8/8 are knobs.
- **R3:**
  - Q2: one options-only path.
  - Q3: no menu cap; report row counts.
  - Q4: the dialogue sentence comes back only if replay shows S36 and S38 still fail.
  - Q5: verb choice is R3's.
  - Q6: the top quest only.
  - Q8: deferred as D1.
  - The ablation calls are dropped unless S4 does not move.
- **R4:**
  - Q1: approve the method; K comes from the replay.
  - Q3: hold machinery deleted (contract HF-5 replaced).
  - Q4: migrations dropped.
  - Q5: the `verdict` key is kept with the new domain until `watch_run` unfreezes.
  - Q7: R1 owns arrival credit and the refuted-exit rule.
- **Cross-section:**
  - Act `Because:` shows cited verbatim game text only, no beliefs.
  - Shared files are serial in the order R1, R4, R2, R3.

**Standing decisions:** generic, Pokémon Red only for now; no steering gates; options-only act output; cheap spatial memory; scarce cloud calls, with caps approved by the user; commit at gates, never push; `pytest -n auto`.

**Deferred:**
- D1: the perceiver brief's top-down exit hint (`brief.GENERAL`) and other perceiver text. Changing it invalidates the perception cache, so it is revisited after RCR with a fresh run.
- F1 (R4): in live mode, settling near water still costs 2-8 s of real time per press, down from a 10 s timeout. A shorter per-press settle is a follow-up after RCR.
- F2 (R4): `_hold_row` and the inert `pending`, `unsettled` and `reperception_used` attributes stay, because live-view code reads them. Each has a `ponytail:` comment and is removed together with that live-view hunk.
- F3: `skills.refine` (`locate.py`) is a cloud refine path, which conflicts with the cheap-spatial-memory decision. It is outside RCR; review it after RCR.
- F4 (R1, 10-19): a reverse exit records the pressed-from tile as its arrival. A warp one tile further on lands re-entry 1 tile off (S59: (3,−5) vs truth (4,−5)) until a view match or crossing re-anchors. No game-shaped warp rule; revisit only if routes into exits fail live.

## 11. Waiting on the user

1. ~~R3 grouping by history~~: **resolved 10-15, allowed** (§10).
2. ~~Live-view work and RCR Phase C~~: **resolved 10-15, build on top** (§3). The question still asked at gates: whether a commit may include files that carry live-view hunks.
3. ~~Ledger caps for Phase D~~: **resolved 10-15.** The user raised the caps by 1,000 (TOTAL 4815, M1 4765; 3,756 had been used) and pre-approved all of Phase D: R3.7's 20-call replay with its fallbacks (+2, +20), the live run after R1 and the live run after R3. Each live run is a clean boot, `--steps 150 --max-calls 400`. No section needs to come back for budget unless the ledger runs out.
4. **RCR implementation and measurement closed 2026-10-19.** Phase E live gate failed; the complete scorecard and audit verdicts are in §8. No further amendment or live run is pending. Any follow-up starts as a new program.
