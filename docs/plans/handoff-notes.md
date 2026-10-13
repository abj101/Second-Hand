# Handoff notes

Last written by: GPT-6.1 Sol orchestrator, 2026-10-12. W3 gate passed; next STOP 2, then P3 after approval.

## State of the code
- Branch feat/lean-agent; this session's W3 gate commit completes evidence, seen, tiers and the act view.
- W0/W1/W2 and P2 remain accepted; do not redo them.
- World.view replaces chooser/describe facts on the act path; gated branch keeps mem.view and describe.
- This session stops at STOP 2; P3 not started; no push.

## Decisions in force
- Root GPT-6.1 Sol; one gpt-6-luna HIGH subagent per WP, explicit model and effort.
- Line budgets suspended; record counts, never trim for them.
- Code reads via codegraph_explore; Ponytail full; Headroom for large output.
- Every cross-place reader skips merged-place rows.
- Place labels use name [id] when live names collide; no renaming or label aliases.
- World.resolve accepts exact labels/ids before name matching; evidence/tier never filters model options.
- Gone things are hidden from the view, but explicit model go_to still resolves them.
- Never edit contracts.md, loop/ or runs/m1; work on fresh checkpoint copies.
- STOP 2 requires user approval for up to 80 live M1 requests before P3 (454 left).

## Measured numbers
- W3 parent world gate: 19 passed, 17.35s; label gate: 3 passed/16 deselected, 9.21s.
- W3 checker: 1,989/2,000 chars, deterministic, 14/14 unique live labels, 0 model calls.
- Checkpoint Here: world [p_world_2] (-1,0), 30 walked, 0 seen, 27 untried/27 unseen edges.
- From 2F, resolve("world [p_world]") gives p_world, tile(0,0), stale false.
- W3 act gate: 5 passed/45 deselected, 32.34s.
- W3 full suite: 248 passed, 1 SDL warning, 329.87s (0:05:29).
- Parent W3.2 acceptance: 71 passed, 1 SDL warning, 247.95s.
- GridEnv seen union: 99 cells, 2 upserts; repeated rectangle adds no row and no floor.
- agent/2,448 lines (+234 W3); tracked tests/agent Python3,765 (+245); scripts2,501 (+29).
- world468, memory384, loop223, test_world415, checker359 lines.
- Requests0; ledger total614/M1596/smoke18; M1 left454; protected changes0/443.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths.
- Gate logs: .tmp/w3/gate-world.log, gate-label.log, gate-view.log, gate-act.log, gate-suite.log.
- Checkpoint view copy: .tmp/w3/view-tpcl_ba2/memory; never modify runs/m1/checkpoint/000094/memory.
- W3 Result in docs/plans/W3.md includes full literal view, commands and golden rationale.
- tests/agent/data is ignored; new seen.jsonl fixtures were force-added for both golden episodes.
- World.state optional last_miss_hash keeps last eligible miss-counted frame across resume.
- W2 graph/travel/fetch checker modes keep .tmp/w2; view mode uses .tmp/w3.
- Trace K place/frame pairs truth K; tile/next_place pairs truth K+1.
- Headroom proxy localhost8787 unreachable; local compression/retrieval works.
- Existing untracked .claude/ predates this work; preserve it.

## Deviations from plan and contracts
- One diagnosis round fixed actor remembered persistence, transient interest retention, offscreen text tiers and integer blocker moves.
- Regressions failed before fixes; parent W3.1 repair acceptance passed15 in7.19s.
- Both affected goldens regenerated: evidence/tier rows and 2 seen rows each; no non-W3 final entity change.
- Gated golden trace unchanged; act golden trace only changes view_ids to [].
- Integrated act/label checks are in card-owned test_world.py; test_loop.py unchanged.
- No contract changes, no loop/ edits, no checkpoint edits, no new gates.

## Do not retry
- Do not redo W0/W1/W2/P2 or regenerate unrelated goldens.
- Never reset/refund/raise ledgers, fabricate facts, git add -A or push.
- Do not start P3 or spend live requests before STOP 2 approval.

## Open issues for STOP 2 then P3
- No W3 gate blocker; approve up to 80 live requests from the M1 bucket to authorize P3.
- Checkpoint predates seen store (0 cells); view fills its budget at the Town heading amid legacy shelf duplicates.
- P3 measures live seen accuracy, duplicate identities and repeated actions/re-entries.
- General arrival verification remains unproven; W4 motion anchors/HUD are still deferred.
