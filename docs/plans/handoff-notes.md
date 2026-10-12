# Handoff notes

Last written by: GPT-6.1 Sol W0 orchestrator, 2026-10-12; amended by Claude after repairing W0.2 (user-approved). Next phase: W1.

## State of the code
- Branch feat/lean-agent; W0 committed (gate passed). P2 committed with the user's purity waiver.
- agent/world.py (72 lines) owns act-path spatial state: before, prepare, after, mark_exit, observe_after, state, restore; Loop.pos is a property over world.pos; tests read loop.world.drift / loop.world.anchors.
- tests/agent/test_world_golden.py + tests/agent/data/w0_golden/ freeze spatial behaviour; regenerate only where a later packet intends a change (W0_REGEN=1), and review the diff.
- tests/agent: 226 passed. agent/ 2,042 lines (W0 +54; budget revised to <= +60 by the user).
- W0 lesson for packet budgets: loop.py is dense one-liners, so extracting into named methods costs lines; judge W budgets on agent/ net, not loop shrinkage.

## Decisions in force
- User continued past STOP 1 after waiving P2 purity; never rerun P2.
- Program order remains W0 -> W1 -> W2 -> W3 -> STOP 2 -> P3 -> P4 -> W4.
- Orchestrator GPT-6.1 Sol; each WP uses a fresh gpt-6-luna HIGH subagent with model/effort explicitly set.
- Blocked or failed phase stops execution. W1 not started; P3 live spend needs the user at STOP 2.
- World Memory must store facts and execute model choices without new gates or game-specific agent code.
- Frozen contracts unchanged; no contract change is proposed. Never edit loop/ or runs/m1/; preserve cache/checkpoints.
- Commit listed paths plus docs only at a passed gate; never git add -A or push.

## Measured numbers
- HEAD entry suite: .venv-agent/Scripts/python.exe -m pytest tests/agent -q; 224 passed, exit 0 in 299.32 s (.tmp/w0/head-suite.txt).
- Single W0_REGEN=1 golden capture: pytest tests/agent/test_world_golden.py -q; 2 passed in 25.87 s at unchanged HEAD.
- HEAD golden comparison: same command without W0_REGEN; 2 passed in 25.49 s (.tmp/w0/golden-head-compare.txt).
- Golden inventory: 112 memory upsert rows and 12 trace rows across 13 files; all capture hashes unchanged.
- Parent W0.2 acceptance: pytest tests/agent/test_world_golden.py tests/agent/test_loop.py tests/agent/test_space.py tests/agent/test_memory.py tests/agent/test_act.py -q; 69 passed, 6 failed, exit 1 in 202.49 s.
- Four failures: undefined moves; two failures: missing Loop.drift and Loop.anchors (.tmp/w0/extraction-acceptance.txt).
- Both golden tests passed in the targeted run; no differing golden store row. Post-extraction full suite not run because W0.2 was not accepted.
- Agent lines 1988 -> 2048 (+60 vs <=0); loop 232 -> 213 (-19 vs <=-50); memory 383 -> 384 (+1); world 78/80.
- Dependency check removes memory's space import, but loop still has act-specific route/outward and arrival bookkeeping.
- Requests spent 0; ledger unchanged: total614/M1596/smoke18, M1 remaining454; all 267 runs/m1 files and contracts unchanged.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; unit tests use StubModel/GridEnv, no cloud calls.
- Read code only through codegraph_explore, within the active packet's list; range query agent/loop.py:185-219 exposed the original drift tail.
- Ponytail full and Headroom mandatory; Headroom proxy unreachable, no measured compression savings.
- Golden files under tests/agent/data/w0_golden/ are ignored by existing data/ Git rule; explicit force-add is needed only at a future pass.
- Goldens compare parsed memory histories sorted stably by id, plus trace rows excluding t/ms/entities/scroll only.
- W0 evidence lives in .tmp/w0/: line counts, hashes, draft-budget.json, final-immutability.json, test outputs.
- The subagent lost one started test session handle and claimed no result; the parent's independently verified acceptance is authoritative.

## Deviations from plan and contracts
- W0.2 exceeds net and loop budgets; no unrelated code was compacted to hide growth.
- World.after requires prepare() for the same step; mark_exit uses the prepared exit tile (contracts.md#world as built).
- Spatial arrival/go_to code remains in the act branch; extraction is incomplete despite golden episode parity.
- No contracts, old loop/, golden baseline, P2 prompt, ledger, runs/m1 or cache removal changes.

## Do not retry
- Do not redo W0; it is committed and gate passed.
- Do not regenerate W0 goldens unless a packet intends the behaviour change; review the diff when you do.
- Do not resume M1/E4 live checks or start P3; STOP 2 live-spend approval is still required.
- Do not reset/refund/raise ledgers, delete old paths before P4, or push.

## Open issues for W0
- Repair the six acceptance failures and complete spatial ownership/World.after behavior under unchanged contracts.
- Meet world<=80, loop<=-50, memory+/-6 and phase net<=0 without compacting unrelated code, or obtain a revised packet from the user.
- Preserve captured golden files and evaluate the next authorized attempt against them.
- P3's evaluator WP must build repeat_actions and reentries report rows; P2 waiver carries those concerns forward.
