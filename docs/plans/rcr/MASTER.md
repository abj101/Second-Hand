# RCR: Root-Cause Refactor (master tracker)

Status: **Phase B done: all four packets final (10-15).** Docs committed; the user starts implementation later (§11.4)  ·  Branch: `feat/lean-agent`  ·  Started 2026-10-15  ·  Source: [audit-2026-10-15.md](../audit-2026-10-15.md)

**Goal:** remove the four root mechanisms the audit found, and leave the agent *smaller* than before.
**Not in scope:** new features, new gates, game- or label-specific fixes, or tuning the perceiver prompt per label.
**Role of this file:** it tracks the whole program and is the reality check. Any claim about progress needs a row in §8 with evidence.

## 1. Sections

| | Section | Doc | Planning session | Phase | Status |
|---|---|---|---|---|---|
| R1 | Identity and world truth | [R1-identity.md](R1-identity.md) | `rcr-r1-identity` | B → C | **packet final** 10-15; the 4 edits and 2 amendments (G10 excludes env_pyboy.py, one replay function per mode) were checked in the doc. Waits on S0 |
| R2 | Quest state and planner contract | [R2-quests.md](R2-quests.md) | `rcr-r2-quests` | B → C | **packet final** 10-15; the 4 edits (report-only lines, serial after R4 with no worktree, re-add count, `notebook_at`) were checked in the doc. Waits on R4's gate |
| R3 | Act prompt and option menu | [R3-decide.md](R3-decide.md) | `rcr-r3-decide` | B → C | **packet final** 10-15; the 5 edits (report-only lines, `--menu` mode of check_tracks, R2's `notebook_at`, explore `dir`, grep fix) were checked in the doc. Grouping allowed by the user. Lands last |
| R4 | Feedback and control loop | [R4-feedback.md](R4-feedback.md) | `rcr-r4-feedback` | B → C | **packet final** 10-15; the 5 edits and the explore-`dir` amendment were checked in the doc. Waits on S0 and R1 |

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

## 9. Deletion ledger

| Section | Planned removals (Phase A) | Net lines | Commit |
|---|---|---|---|
| R1 | `Memory.view`/`resolve`, landmark and name merges, `#n` suffixes, `add_edge`/edges, arrival credit, `named_intents`; `locate.py` if it has no runtime caller | ≈ -200 | — |
| R2 | `_EXPLORE`, `_TACTICAL`, the reconcile rules, chapters, checks, migrations, `_fit`; tests ~770 → ~250; `eval_plan.py` -155 | ≈ -490 agent | — |
| R3 | free act path, name-list rows, `OPTION_CAP`, compact view, `here_flag`, act flags, brief prefs | ≈ -160 | — |
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

## 11. Waiting on the user

1. ~~R3 grouping by history~~: **resolved 10-15, allowed** (§10).
2. ~~Live-view work and RCR Phase C~~: **resolved 10-15, build on top** (§3). The question still asked at gates: whether a commit may include files that carry live-view hunks.
3. ~~Ledger caps for Phase D~~: **resolved 10-15.** The user raised the caps by 1,000 (TOTAL 4815, M1 4765; 3,756 had been used) and pre-approved all of Phase D: R3.7's 20-call replay with its fallbacks (+2, +20), the live run after R1 and the live run after R3. Each live run is a clean boot, `--steps 150 --max-calls 400`. No section needs to come back for budget unless the ledger runs out.
4. **Go-ahead for implementation.** The coordinator recommends this order:
   - **S0:** the coordinator writes the scorecard in `eval_run.py` with a Haiku worker, and records the `m1_bedroom_live` baseline. 0 cloud calls.
   - **Sections:** R1, then R4, then R2, then R3. Each runs in a fresh **GPT-6.1 orchestrator** session using its packet's paste block, with **gpt-6-luna (reasoning high)** workers. The user changed the tier on 10-15, and it overrides the Haiku/Sonnet tiers written in the packets.
   - **Gates:** the coordinator reviews each gate against §4 and §5 before the next section starts. Any commit that touches a file with live-view hunks goes to the user first.
