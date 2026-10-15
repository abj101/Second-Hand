# Handoff notes

Last written: 2026-10-15, QS Amendment A complete. Branch `feat/lean-agent`; [QS Result](QS.md#9-result-filled-in-by-the-orchestrator) has commands and evidence. Gate passes with the permitted HF3 cache exception. No push.

## State of the code
- QS.1–QS.7 accepted by root. Notebook v2: keyed facts/beliefs; ordered quests, notes, chapters; separate code event log. Read-only focus is the first open quest key. v1 migrates in memory until mutation records the original in history.
- All eight named invariant tests pass:
  - `test_inv1_omitted_heard_quest_kept_and_flagged`
  - `test_inv2_done_and_dropped_require_evidence_and_reference`
  - `test_inv3_open_quests_over_limit_are_parked`
  - `test_inv4_tactical_quest_text_is_rejected`
  - `test_inv5_belief_revision_has_reason_and_refuted_is_rendered`
  - `test_inv6_quest_reset_preserves_evidence_opened_and_check_results`
  - `test_inv7_facts_are_add_only_and_conflicts_get_suffix`
  - `test_inv8_nothing_deleted`
- Checks run on every open quest; evidence/opened/check history survive resetting. Omitted heard quests persist, extra open quests park, reorders count as changes. Facts/open quests and held beliefs are protected in rendering; irreducible protected content may exceed the render budget.
- Planner SYSTEM has 13 sentences reviewed against the approved design. Plan returns quests/beliefs/facts/note/why; accepted chapters reach later prompts. Act sees top/other open quests. Act ops no longer mutate the notebook.
- T1 complete game text, T2 first navigable place, T3 quest closed, T4 sustained stall, T5 no open quest. Priority T3/T1/T2/T5/T4; T2/T4 retain the three-step floor. Failures, revisits and cycles are act/evidence information.
- Stuck tracks first places and verified place>next_place edges, N=20; text/closure/arrival reset progress. First unchanged stall asks for a pivot at N/2; a second adds the generic fallback. stall_unchanged and optional evidence state round-trip through checkpoints.
- Evidence <=600 chars: new places, grouped target/outcome attempts, movement/failure deltas, heard text, closed checks, progress and reconcile flags plus pivot instruction. A separate post-plan journal retains attempts beyond act's five-row tail.
- `--lifecycle` is a zero-model trigger replay plus full-history/final-state metrics; removed/dropped heard leads count lost and planner writes coalesce to events. Viewer shows v2 quests/checks, beliefs and latest note, with v1 fallback. eval_plan is v2 and dry-runnable.
- `--om plan` copies checkpoint harness/memory, migrates v1, calls actual Loop evidence and plan prompt; no perception/ROM restore needed. Dry prints state/prompt/len/4; live prints every try's returned fields/flags. Each try starts independently; actual posts are capped and ledgered.

## Decisions in force
- Ponytail Full, CodeGraph before code reads/edits, Headroom for large output. All implementation cards explicitly used gpt-6-luna/high with disjoint ownership; root reran acceptance. Amendment A authorized the final repair/legacy fixture ports.
- Frozen contracts.md unchanged; replacement proposal and optional hooks are in QS Result for user merge. No game-specific agent code, option suppression, cap increases, live game runs or pushes. Unit models are stubs.
- Six authorized live requests are exhausted. No further live tuning or clean boot is authorized by this result. Clean-boot funding is the user's decision.

## Measured numbers
- Entry suite 521 passed/0 failed; final suite 564 passed/0 failed, 60.55 s: `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto`; `.tmp/qs/amendment-final-suite.txt`. Earlier 449/97 and 551/7 failures are documented diagnostics superseded by the final gate.
- Root card tests: notebook 23, plan+act 24, tasks 7, loop 112, eval_run 40, evaluator pair 35, legacy/golden 3 passed. Invariant gate notebook+plan 29 passed. Exact commands/logs in QS Result.
- Lifecycle: HF checkpoint 12, decide3 12, gp5 12 calls; no failure-only triggers. `python -B scripts/eval_run.py --lifecycle runs/<run>`; `.tmp/qs/gate-lifecycle-<run>.txt`. Source: trace metadata + notebook.history dialogue approximation. HF historical lost leads=2; replay numbers do not claim repair of old runs.
- Lifecycle audit passes lost=1/open=0/calls=1; proper regressions included. Golden audit: only act trace plan fields, 0 forbidden changes; per-field counts in Result. Root eval_plan dry: ok, 0 requests, P1–P4.
- Migration: both heard leads open, no open try_* quests. Dry plan sizes at 100/125: 1404.8/1451.2 tokens, below 3000. `python -B scripts/eval_act.py --om plan --run m1_ip --from-step <100|125>`; gate-plan-*-dry logs.
- Live 100: 3/3 retain both heard leads, nonempty notes, 0 tactical quest rejections. Live 125: 3/3 reverse the retained quest pair citing held/mid oak_in_lab, rather than revising/refuting a belief. `--tries 3 --live` on the same commands; `.tmp/qs/final-plan-<100|125>-live.txt` contains every response.
- Act/decide prompt size inconclusive: `python -B scripts/eval_act.py --om cases --arm decide`, exit 2, 4 old cache misses (limit 3), first/last steps 1/2; 0 requests. No size pass against 1700 claimed; packet permits the cache exception.
- Ledger total 3216 -> 3222, M1 3168 -> 3174, exactly 6 requests. Caps unchanged 3265/3215; remaining 43/41. `Get-Content runs/e3_budget.json`; caps from scripts.run_agent. Logs gate-ledger-before/after-live.txt.
- Staged code/test lines: agent +659/-175; scripts +352/-59; tests/fixtures +1199/-180. Report only; explicit fixture staging includes 273-line v1 and changed golden trace, plus new 119-line test_plan.

## Environment and gotchas
- Every shell before tests/scripts: create `.tmp/qs/tmp`, set TEMP and TMP to `$PWD\.tmp\qs\tmp`, confirm `tempfile.gettempdir()` there. Use `.venv-agent/Scripts/python.exe -B`; scratch only `.tmp/qs/`.
- Protected checkpoint/memory hashes: 58 files, 0 changed before/after live (`.tmp/qs/protected-before.json`, gate-protected-final.txt). Existing cache, runs and checkpoints preserved; only approved ledger counters changed.
- Viewer already listening on 8765, PID 8676; acceptance skipped by packet, process untouched. Headroom proxy unreachable; local compression/retrieval usable. Earlier automatic review denied a private-source export and viewer-management command; bounded inspection/existing-listener skip sufficed.
- Pre-action truth S corresponds to post-action trace S-1; avoid mixing coordinates. Old perception fixtures are incompatible with HF3; do not silently re-record.
- Pre-existing untracked `.claude/` untouched. Data fixtures are ignored by data/ and were explicitly force-staged. Contracts proposal remains separate from frozen contracts.md.

## Deviations from plan and contracts
- Amendment A authorized QS.5 round 2 and QS.6/QS.7 ports from uncommitted work. Root repaired no implementation files; all cards stayed with Luna/high workers. Goldens changed planner metadata only.
- Gate passes with the explicitly permitted cache-dependent act/decide size measurement inconclusive. Legacy lifecycle text replay is approximate. Live notes still contain door-level suggestions although quests remain strategic; no extra spend to tune them.

## Do not retry
- Do not launch a live game, raise caps, spend more live requests, push or rewrite frozen contracts from this packet.
- Do not infer live autonomy from checkpoint prompts, silently refresh perception caches, or reuse old route perceptions after divergence.

## Open issues for the next phase (user decides)
1. Whether to fund a clean-boot QS run; cache-compatible act/decide size measurement and live autonomy remain unvalidated. Planner notes can still suggest specific doors; lab co-location is a model belief, not a heard fact.
2. HF place-merge labels and moving-NPC identity remain open; QS exposes uncertainty without fixing perception/world memory.
3. HF3 cache incompatibility/re-record decision, probe settle gating and remembered blockers on travel legs remain open. Boundary coverage for agent -> scripts/truth imports was previously missing.
