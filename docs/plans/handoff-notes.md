# Handoff notes

Last written by: E3R3 orchestrator, 2026-10-11; R3.6 offline check failed; validation stopped.

## State of the code
- Branch is feat/lean-agent; prior WIP is 31246ad and the approved packet is 273e5ec; E3R3 is uncommitted.
- Every WP ran on explicitly selected gpt-6-luna at high effort, in G-a/G-b/G-c/J before G-d order.
- Orchestrator acceptance passed 154 tests in tests/agent, including boundaries, with one existing SDL warning.
- E3R3 net agent growth is +115: G-a14, G-b19, G-c4, J15 and G-d63.
- Model planning, already-true guards, task-relative deadlines, blocked defaults and the new_place latch are implemented.
- Placement, canonical passage IDs, entity-only navigation tests, exit=True dispatch and concise Chooser context are implemented.
- Planner triggers and deferred ended-goal context survive checkpoint restore.
- The real GridEnv regression reaches its bottom-wall floor tile on the third test and remembers the first two failures.
- Latest retained M1 checkpoint is runs/m1/checkpoint/000055/, house2F at (7,1), party0.
- runs/m1/summary.json still reports M1 false and places_matched false; E4 must not start.

## Decisions in force
- Caps are phase1100, M1 1050, q40 and c546, with smoke50 unchanged; never raise them further.
- Preserve runs/e3_budget.json; abandoned requests count and rewinds never refund them.
- Stage p stays closed at25/30; stage b retains default180 and its existing approved300 override.
- Keep Gemma provisionally for Perceiver/Chooser; local decider-2b is only the approved navigation ranker.
- Role B objective classification stays off; no new Chooser policy has been wired for general decisions.
- Defaults remain Hand/Blue; fallback is Reach a place not visited yet, budget40, blocked during model goals.
- Model goals carry fail_when steps_over, default40; legacy empty defaults migrate on resume.
- Frozen contracts and old loop/ remain intact; nothing was deleted because the M1 gate has not passed.
- Commit only after a passed gate, never push, and do not start E4.

## Measured numbers
- Durable spend is499 requests: smoke18/50 and M1 481/1050 = a142 + b297 + p25 + q17 + c0.
- Remaining allowances are phase601, M1 569, smoke32 and q23; c's ceiling stays546.
- E3R3 validation used0 cloud requests,0 gameplay inputs and0 completed target tests.
- Retained M1 has55 steps and66 game_text rows; HAND was typed with uppercase fallback and BLUE used a matching preset.
- House2F/1F dominant-place purities remain1.0; Town/lab are unmeasured, with3 confirmed places over2 maps.
- Only the evaluated2F-1F edge is true; no Town/lab edge exists, and upstairs p_world remains a candidate.
- Retained execution is46/55 (83.6%); prediction hits48/55 (87.3%), first50 88.0% and last5 80.0%.
- Retained cost is91 asks (1.655/step),84 HTTP requests (1.527/step),7 cache hits,0 retries and4.370s/step.
- Retained perception reuse is90.9%, no-call choices56.4%, historical escalations2 and repeated mistakes6/55.
- Retrieval use is0/1176 and refuted model claims0/54; summary boundaries are null while fresh boundary tests pass.
- Retained metrics exclude discarded attempts/experiments; the durable ledger is authoritative.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe forward-slash commands; unit tests make no cloud calls and stub the real judge.
- Perception version is e3-perceiver-7; old cached checkpoint perceptions invalidate, and .cache/model/ remains preserved.
- Resume must load environment and memory together; checkpoint54 remains at runs/m1/checkpoint/000054/.
- The checkpoint54 planner view includes all66 game_text rows in2074 estimated tokens.
- The worker uses .venv-decider and cached Mapika/decider-2b revision b37f7e1ba3fbc9238004cf531fabbee2619973fd.
- The first real Judge.rank call returned None after10019ms under the10-second client window.
- Raw check evidence is .tmp/e3r3/offline-result.json; cached points/names with hand placements are .tmp/e3r3/offline-perception.json.
- .tmp/e3r3/offline-before.png and .tmp/e3r3/offline-after.png are byte-identical checkpoint54 frames.
- The one-off .tmp/e3r3/offline.py uses .tmp/e3r3/offline-memory/; it neither advances nor rewinds the main M1 run.
- Prior checkpoint55, logs and the ledger are preserved under .tmp/e3r3/pre-repair/.

## Deviations from plan and contracts
- G-d adds63 agent lines versus its42-line card; total115 exceeds the roughly100-line repair estimate after compaction.
- contracts.md is unchanged, and agent/ contains no new game-specific policy.
- CodeGraph CLI was used; named Headroom tools were unavailable, so reads were bounded.

## Do not retry
- The first failed R3.6 check forbids perception, plan, probe or stage c continuation under this packet.
- Do not restart the worker, change its timeout or rerun the offline check without a new concrete instruction.
- Do not spend unused q/p allowance, reset/refund the ledger, raise caps, add target hints or use RAM-informed choices.
- Do not treat unit coverage or unchanged house purity as an M1 autonomy pass.

## Open issues for E3
- The real worker supplied no usable answer; its return interface and discarded stderr do not identify startup, inference or timeout as the exact cause.
- Code fallback nominated television at[0.25,0.26], but the failed real-worker replay executed no candidate and demonstrated no Town traversal.
- Checkpoint54 has no leads_to relations; its raw place edges do not establish the expected current-room stair filter.
- No reverse edge or memory hint was fabricated; the seven-candidate replay state still included the stairs.
- Live placement, live goals, the Town probe, stage c, final Chooser comparison and ten-surprise review remain pending.

## Follow-up 2026-10-11 (Claude, user-approved fixes 1-3)
- Judge root cause: worker cold start measured 29.7s (GPU load + compile) against a 10s first-call window; warm calls 0.1s. Every retry respawned under the same window, so the judge could never answer.
- Fix: worker prints {"ready": true} after warm-up; Judge waits startup_s=180 for it, keeps timeout_s=10 per call, logs worker stderr to <run>/decider.log, and disables itself after a failed startup (code order fallback).
- known_routes() now renders leads_to rows as "X leads to <place> (already visited)" or "X: tested, no new place"; free-text model claims are skipped.
- Arrival-entrance rule: after a scene cut into a new place, the exit named like the `via` target (else the exit within 2 tiles of the player) gets an inferred leads_to back to the source place.
- Checkpoint 54 merged via .tmp/e3r3/merge_cp54.py: p_game_world retired (state merged, merged_into p_dialogue_box_2); the 2F stairs edge now targets p_dialogue_box_2; e_stairs_2 leads_to p_dialogue_screen_4 (inferred). Original is at .tmp/e3r3/pre-merge/000054/.
- Judge-only replay on merged checkpoint 54: stairs excluded; the pick was shelf at 0.19, with rug 0.16 and table 0.12. The ranker is near-uniform here.
- Checkpoint 55 and live runs/m1/memory still contain the step-55 stairs mistake and the p_world split; resume from 54.
- Per-shape compile also stalled calls (first call per length bucket took 1-8s; call 4 hit 10s). The worker now warms batch-1 buckets up to 1024 tokens: startup is about 25s and every call takes 0.04s.
- The R3.6 offline check rerun on merged checkpoint 54 PASSED: test 5 (chair_left_bottom) reached Pallet Town (map 0, at 5,6). 5 ranker answers, 0 cloud calls, ledger unchanged; see .tmp/e3r3/offline-result.json.
- The pass was partly luck: test 3 picked the real doormat (rug, bottom wall) but pushed LEFT (last direction) and ended at x=0; tests 1 and 4 hit the 30-input cap.
- Exit-mode refine was removed (user-approved); the contracts.md skills line was updated, and exit tests make 0 cloud calls. 156 tests pass (the refine-None test was deleted).
- E3R4 (spatial memory) was approved 2026-10-11: packet E3.md §R4, paste block §R4.5, contracts.md#space plus the E3R4 notes. Budget about +110 lines accepted; resume M1 only with --resume --from-step 54.
