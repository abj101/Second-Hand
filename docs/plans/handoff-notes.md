# Handoff notes

Last written by: GPT-6.1 Sol orchestrator, 2026-10-12. W2 gate passed; next phase W3, not started.

## State of the code
- Branch feat/lean-agent; this gate commit completes W2; prior accepted W2 WIP is ae412b2.
- W0/W1/W2.1/W2.2 remain accepted; W2.3 graph/travel/fetch and the full suite now pass.
- W2 adds scripts/check_world.py checks and a tested World.plan unknown-neighbor repair.
- This session stops after W2; W3 has not started; no push.

## Decisions in force
- Root GPT-6.1 Sol; one gpt-6-luna HIGH subagent per WP, explicit model and effort.
- Line budgets suspended program-wide; record counts, never trim for them.
- Read code only via codegraph_explore; Ponytail full; Headroom for large output.
- Cross-place readers skip rows whose place state is merged, including evaluator selectors.
- Duplicate world names: travel targets truth-selected Town id; resolve("world") is informational.
- Fetch resolves the selected entity by name; never fabricate aliases, exits, arrivals or tiles.
- Never edit contracts.md, loop/ or runs/m1; use fresh checkpoint copies.
- No cloud before P3; W3 -> STOP 2 requires user approval before live P3 spend.

## Measured numbers
- Parent graph: 14 live places, 4 exits; 2F p_world_2 -> 1F p_dialogue_box_2 -> Town p_world.
- Parent travel: map0x00,x5/y6,17 inputs,one travel call,zero model calls; legs1/16/0 inputs.
- Parent fetch: e_table_3 table,stored(-1,1),truth target(4,4); map0x25,x4/y3,distance1,6 inputs,zero model calls.
- Fetch legs1/5 inputs; 1F coordinate offset(5,3),trace55/truth56; 18 offline pairs agree.
- resolve("world") returns place p_world_2,tile(0,0),staleFalse; information only.
- Parent repair unit: 2 passed,5 deselected,0.16s; unknown-neighbor and existing resolve/plan tests.
- Parent full suite: 236 passed,1 SDL library warning,308.94s (0:05:08)..
- agent/2,214 lines (+99 across W2); tracked tests/agent Python files3,520.
- scripts/check_world.py330 (+270); world244 (+4 repair); test_world170 (+21 repair).
- Requests spent0; ledger total614/M1596/smoke18,M1 remaining454; protected changes0/269.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; ROM and checkpoint94 present.
- Gate commands: scripts/check_world.py graph/travel/fetch, then -m pytest tests/agent -q.
- Regression args: -m pytest tests/agent/test_world.py -q -k 'unknown_entity_neighbor or resolve_and_plan'.
- Evidence under .tmp/w2/: final-graph.log,final-travel.log,final-fetch.log,final-suite.log,repair-unit.log.
- Protected baseline .tmp/w2/retry-protected-before.json includes 267 runs/m1 files,contracts.md,runs/e3_budget.json.
- Trace K place/frame pair with truth K; trace K tile/next_place pair with truth K+1.
- Evaluator map selection uses non-merged screen=world places; Town's candidate state remains eligible.
- Headroom proxy localhost8787 unreachable; local compression/retrieval available.
- Existing untracked .claude/ predates this work; preserve it.

## Deviations from plan and contracts
- Initial fetch had no plan because all four entity neighbors were unknown, excluded by floor=True-only candidates.
- One allowed World.plan repair admits unknown/non-false reachable neighbors, preserving ordering and facing press.
- No stored facts were fabricated; World folds actual travel moves only into scratch memory.
- Existing optional travel(trace=None) is backward-compatible; unseen arrivals still retain expected identity.
- Gate establishes checkpoint94 movement; general arrival verification remains unproven.
- No contract change required or proposed; no loop/ edits.

## Do not retry
- Do not redo accepted W0/W1/W2 or P2.
- Never reset/refund/raise ledgers, fabricate checkpoint facts, git add -A or push.
- Do not start P3 without STOP 2 approval.

## Open issues for W3
- No open W2 gate blocker; start from W3.md when authorized to continue.
- W3 evidence states, seen tiles and known-things view remain unimplemented by this session.
- W3 gate must pass before STOP 2 live-spend approval; preserve truth/navigation separation.
