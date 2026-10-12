# Handoff notes

Last written by: Codex E4 orchestrator, 2026-10-11, after the first stage-M evaluator failure. M1 remains open; E4 build is complete, stage E unstarted.

## Next: program R1 + World Memory (approved 2026-10-12)
- Entry point docs/plans/program-r1-wm.md (order P2 → W0 → W1 → W2 → W3 → P3 → P4 → W4, STOPs after P2, W3, P3, W4). Orchestrator GPT-6.1 Sol; subagents gpt-6-luna high only. Contracts for `notebook and act` and `world` are already in contracts.md.

## Update 2026-10-12 (Claude orchestrator; Sonnet fixes, Haiku verify; 0 cloud calls)
- Evaluator CLI fixed (repo-root sys.path in scripts/eval_run.py) + standalone subprocess test; `eval_run.py runs/m1` exits 0, output identical to the diagnostic; runs/m1/summary.json refreshed.
- A: directional world→world exit into a new place now scores place_changes (loop.py `world_exit`, reused for mark_exit); dialogue/menu cuts unchanged.
- B: space.route()/frontier() never step through a tile's recorded exit direction (step-68 re-entry into house).
- C1: memory.observe() resolves an ambiguous same-name pool to the nearest-tile record (as resolve() does); fixes the 52 shelf duplicates / shelf loop.
- tests/agent: 211 passed (.tmp/e4/full-suite-abc.txt). Committed 53a1e03.
- Live resume 74→94 (stage c, 20 steps, 20 requests; ledger 585, M1 567, c 43; M1 left 483). Backup of pre-run state .tmp/e4/m1-before-abc/; output .tmp/e4/stage-m2-*.
- Result: stuck all 20 steps in house 2F (truth map 0x26; agent calls it p_world_2, a second id for 2F). 2F is closed; its only exit (stairs) leads to visited 1F, which the new_place goal filters out (pre-existing loop.py candidate filter + B). 13/20 steps "route blocked" with retries on one target (computer desk x8, "exit: path south" x5); 31 new entities, 18 #-suffixed (mostly invented "exit: path south").
- User rejected more mechanical gating (2026-10-12): agent must infer goals from game text. Redesign: docs/plans/autonomy-redesign.md (approved: ~1.5-2 req/step OK, names Hand/Blue as prompt preferences, old path kept behind flag, stay on gemma4:31b, Haiku workers).
- P0 memory fixes done (normalize_kind, border exits untiled, exit: prefix in _base, TRANSIENT landmarks). P1 done, uncommitted: agent/notebook.py, agent/act.py, skills.press_seq, perceive exit wording, memory.name_place (model-asserted place merge), loop act branch + run_agent --act. tests/agent 222 passed. Next: P2 prompt eval on ~15 recorded frames (15-30 requests) after user approval.

## State of the code
- Branch feat/lean-agent; HEAD remains ff485d6, with E4 changes uncommitted and nothing pushed.
- E4.0 ledger, E4.1 patterns, E4.2 reflection/wake and E4.4 metrics are built; each WP used explicitly selected gpt-6-luna at high effort.
- Parent acceptances: ledger 5, patterns 6, reflection/loop 45, evaluator 15; full tests/agent: 205 passed in 263.77 s, one existing SDL warning.
- Source growth: runner+6; memory+59/70, predict+9/30, decide+10/20, loop+8/15, evaluator+90/90 (97 inserted,7 replaced/deleted).
- Agent total 1,826 (+86 from 1,740), 326 over spec headline; budgets hold, no unrelated code compacted.
- Patterns count distinct observed source instances; optional verb/pattern_id distinguish actions and true pattern uses; inferred/model rows never support them.
- Reflection returns list-compatible PlanResult metadata; existing ids alone may be named; code alone promotes; plan_step tags created tasks.
- New wake reasons surprise/new_place/periodic use the existing checkpointed trigger and 15-step throttle; a known destination is not new_place.
- Offline replay learns pixel outcomes after scoring, pairs full/hidden on independent guesses, and reports excluded samples/coverage; repeat sequences are length-two action/outcome pairs.

## Decisions in force
- Stop enforced at the prescribed evaluator failure; no mid-check repair, further live chunk, E3.7 comparison/review, loop deletion, commits or E5.
- E3 caps unchanged: phase 1,100 / M1 1,050 / q 60 / c 546 / smoke 50; never reset, refund or raise.
- E4 has separate 600-request ledger support; runs/e4 and its ledger were not created, 0 requests spent.
- Preserve runs/m1/checkpoint/000074 with matching memory; any next resume needs a new instruction after the failed check.
- No target hints, no evaluation RAM for agent choices, no game-specific agent code; never start the local decider worker.
- Final THRESHOLDS unchanged: candidate 2, promote 3, contradiction_rate 0.10, demote_misses 2, use_window 5; no offline tuning ran.
- Alias map doorway->door after singularization; no game-specific alias; colon kinds use their prefix (exit: door->exit).
- No view items pruned; deletion candidates remain user decisions: agent/judge.py (26 lines), scripts/decider_worker.py (28), decide.escalate (6).

## Measured numbers
- Exact initial stage-M command resumed 54 for 20 steps/max 40 requests; exit 0, steps 55-74, checkpoint 74, 23 actual requests.
- Town reached after step 67, truth 68 map 0x00 (5,6), within 14 requests; path north immediately re-entered house 1F at 68.
- Final truth 75: house 2F 0x26 (7,1), party 0, HAND/BLUE correct; M1 is not passed.
- E3 ledger 565 = smoke 18 + M1 547; a 142 / b 297 / p 25 / q 60 / c 23; remaining phase 535 / M1 503 / c 523.
- Prescribed eval_run.py runs/m1 exits 1: ModuleNotFoundError for agent.memory in _e4_metrics; runs/m1/summary.json is stale.
- Separate root-imported diagnostic evaluator, 0 calls: m1_gate_pass=false, places_matched=false; house purities 1.0, Town/lab unconfirmed, all three gate edges false; 66 game_text rows.
- Diagnostic spatial: 20/20 tile samples; 52 new duplicates (53 total); known-exit id reuse 1/1.
- Diagnostic execution 48/74; prediction 49/74; 1.5 asks/step, 1.392 successful uncached asks/step, 4.780 s/step; repeated mistakes 7.
- Diagnostic retrieval 0/1094 and poison 0/88; reflection 1 completed goal/2 wakes; 12 repeated action/outcome pairs, promoted patterns 0.
- E4 gate learning accuracies, paired memory proxy, trainer/move and door promotion checks remain unmeasured; no E4 run or threshold tuning.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe; unit tests use StubModel/GridEnv and make 0 cloud calls; full-suite evidence .tmp/e4/full-suite.txt.
- New evaluator agent imports need repository-root sys.path for direct script execution; pytest/imported evaluate concealed this CLI bug.
- Headroom 0.40.0 installed outside repo at C:/Users/Abj1/.codex/tools/headroom, globally registered/enabled with Codex MCP; CodeGraph MCP is registered/enabled too.
- This session used real MCP tools through C:/Users/Abj1/.codex/tools/headroom/mcp_call.py; Headroom retained protected/no-op reads, no measured savings; no model proxy was started.
- Preserve .cache/model/; abandoned requests count even when resume truncates logs.
- Failed command output .tmp/e4/stage-m-eval.json is a traceback; diagnostic numbers are separately .tmp/e4/stage-m-diagnostic-eval.json.
- Live output .tmp/e4/stage-m-output.txt; trace with before/after truth .tmp/e4/stage-m-evidence.json; final frame .tmp/e4/stage-m-final.png.
- Frames runs/m1/frames/000067.png before exit, 000068.png visibly Town, 000069.png after re-entry, 000074.png before stairs.
- Pre-resume evidence .tmp/e4/pre-stage-m/; original ledger .tmp/e4/e3-budget-before.json; previous probe evidence .tmp/e3r5/probe2/.

## Deviations from plan and contracts
- All WP models changed by user to GPT-6 Luna high; no Sonnet/Haiku/Opus agents were used.
- Optional parameters/metadata follow contracts' additive convention; contracts.md is unchanged and no further contract change is needed.
- Evaluator diagnostic API invocation was reporting only and did not turn the failed prescribed CLI into a passed check.
- Initial reflection fixture failures were corrected for frame reuse/new plan replies; metric fixes briefly exceeded budget, then only new code was reduced to +90.
- Commits made: none; M1 and E4 gates remain unpassed; loop/ still exists.

## Do not retry
- Do not patch/restart the stopped live check or rerun the kickoff blindly; first plan an offline CLI repair and obtain the next instruction.
- Never rescue agent decisions with truth, game-specific exit rules or the unused ranker; never overwrite the retained checkpoint/ledger/cache.

## Open issues for E4
- Fix the evaluator import path and add a real standalone CLI check before any newly authorized live continuation.
- Candidate Town never confirms before re-entry; rug exit is scored screen_changes/surprise; repeated shelf inventions create 52 duplicates; later upstairs becomes p_world_2.
- New-route re-entry framing, perception inventions and generic goals remain risks; tile accuracy alone does not establish stable place identity.
- Defer shortcuts, live ablation, E1b, E5, perception skipping, tile landmark matching and fixed-camera games.
