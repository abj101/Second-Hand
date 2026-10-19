# Handoff notes

Last written: 2026-10-16. Resumed R3.2 passed; final R3.4 full suite has one assertion failure. Evidence: R3-decide.md §9 and MASTER.md §8.

## State of the code
- Branch feat/lean-agent; HEAD dc4113f; live-view and R1/R4/R2 loop wiring committed at8fbe5eb.
- Overseer fixed World.label(ref,place=None), four stale assertions and DECIDE; root R3.2 acceptance148/148 passed.
- R3.4 removed remaining a.place in _live, completed script reply producers/callers, migrated owned OM test.
- Root migrated remaining test_eval_act/test_loop/test_harness_reliability/test_world_golden callers and regenerated golden trace.
- R3 gate PASS 10-19 on `runs/m1_rcr_baseline` (m1_bedroom_live lost 10-16): suite 612 passed/1 skipped (observer:160 needs the lost checkpoint), LV 20/20; `check_tracks.py m1_rcr_baseline --menu --first 1` exit 0, max prompt 1,909, dup 0, 74/74 steps. Off-screen remembered rows group by name (nearest of N). Next: R3.7 Phase D replay needs a ledger cap; S4 unmeasured offline.
- R3.5 read-only prep only; check_tracks.py unchanged; R3.6/R3.7 not implemented.
- All changes unstaged; index empty; no gate commit or push.

## Decisions in force
- CLAUDE: Ponytail full; CodeGraph first; Headroom large outputs; compression proxy unreachable.
- CodeGraph MCP transport closed during resumed work; codegraph explore CLI works.
- All workers explicitly spawned gpt-6-luna/high; interrupted R3.4/R3.5 resumed on user instruction.
- R3.4 failed two root suite acceptances; extra final same-tier review received failure output and failed final acceptance.
- Stop limit exhausted again; no more automatic repairs without a new user instruction.
- Grouping approved, never applied; revert-on-S4 rule unexercised.
- Phase D pre-approved but gate must pass; preserve all live-view hunks.
- Never git add -A, stage uncommitted user LV hunks, or push; amendment permits R3 loop/test-loop at passing gate.

## Measured numbers
- R3.1 isolated6passed; R3.3 focus4passed (initial attempt evidence).
- Resumed R3.2 three files148passed, exit0,11.96s; .tmp/rcr/r3/resume-r3_2.log.
- Root full suites:24failed587passed83.80s →1failed608passed73.29s → final1failed608passed110.80s; all exit1.
- Final failure: test_loop.py:1488 rejects substring cause: inside required Because: heading; preceding failed-row evidence passes.
- Last completed exact LV20passed, exit0,15.12s; .tmp/rcr/r3/resume-lv-1.log.
- Concurrent final LV rerun interrupted after startup, exit1/no result; resume-lv-final.log; do not claim it passed.
- Both eval script --help checks exit0; loop legacy-act grep0matches.
- Golden regeneration1passed14.38s: only8trace rows changed, memory unchanged;8steps/15modelasks; W0_REGEN cleared.
- S10 agent4063→3988 net−75; S1–S9/S11–S14 post-R3 unmeasured, replaycoverage0.
- Protected hashes10/10unchanged; loop live methods3/4unchanged (_live canonical-place fix); test-loop live2/2unchanged.
- Generality added-agent scan0matches; no agent cloudcalls; ledger4027total/3977M1/50smoke,788remaining.

## Environment and gotchas
- PY=.venv-agent/Scripts/python.exe -B; PT adds -m pytest -q -p no:cacheprovider -n auto.
- Every shell uses R3 packet TEMP/TMP prefix pointing at .tmp/rcr/r3/tmp.
- Evidence logs all .tmp/rcr/r3/; final full suite resume-full-final.log; audit resume-final-audit.log.
- Viewer previously returned HTTP200; runs/rcr_r3_live not created.
- Frozen runner routes non-m1 names to exhausted smoke bucket; future approved live needs prior funded-M1 launcher approach without raising caps.
- Requested clean boot150steps/400calls and replaycap20/fallbacks not run; no launcher created.

## Deviations from plan and contracts
- Root additional migrations: test_loop/test_trace_brief/test_harness_reliability/test_eval_act/test_world_golden; workers stayed in card files.
- Two tests/fixtures for deleted non-OM free/image CLI mode removed; golden uses strict numbered reply/follow-up path.
- _live's only change is canonical label replacing removed Act.place; all frozen runtime/perception files unchanged.
- contracts.md untouched; provisional built contracts and full per-file net deltas in R3 §9.
- R3.4 final same-tier worker recommended fixture helper; root applied unowned test-only patch, no worker scope expansion.

## Do not retry
- Do not apply another test/source correction automatically or label this gate green.
- No R3.5 edits/R3.6/R3.7/live until gate passes; no staging/commit for failed gate.
- Do not cap rows, patch duplicate labels, tune perception or weaken invariants.

## Open issues for R3
- test_loop.py:1488 should check complete cause: lines, not substring inside Because:; suggestion recorded only.
- Final exact LV command did not finish; latest completed20/20 and final full suite isolate the remaining failure outside LV.
- Later authorized continuation: finish R3.4 acceptance, R3.5 offline menu, R3.6 grouping, gate/commit, R3.7 replay, requested live scorecard.
