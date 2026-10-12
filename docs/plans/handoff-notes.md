# Handoff notes

Last written by: GPT-6.1 Sol W1 orchestrator, 2026-10-12. Next phase: W2.

## State of the code
- Branch feat/lean-agent; W0 accepted at e403541; W1.1/W1.2 accepted at 453cc59; do not redo them.
- W1 gate passed; scripts/check_world.py merge completes the offline checker in 60 lines.
- agent/ has 2,115 lines; World owns act spatial state and place name/merge bookkeeping.
- W2/W3/P3 have not started; next implementation is W2.1.

## Decisions in force
- User rejected merged_into tombstones; src rows remain history under a merged place.
- Cross-place readers skip rows whose place state is merged; live-place-filtered readers need no change.
- W1 budget revised to <=+75; actual +73 versus W0 baseline.
- A WP overrun up to 50% is accepted if all acceptance commands pass; record numbers and continue; larger overruns stop.
- Contract changes stop; first check whether an existing field/record already expresses the need.
- Program remains W1 -> W2 -> W3 -> STOP 2 -> P3 -> P4 -> W4.
- Orchestrator GPT-6.1 Sol; one fresh gpt-6-luna HIGH subagent per WP, explicit model/effort.
- No cloud before P3; live spend needs user approval at STOP 2.

## Measured numbers
- Parent merge unit: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_world.py -q -k merge; 1 passed, 1 deselected, exit0, 0.14s.
- Parent notebook/act: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_notebook.py tests/agent/test_act.py -q; 7 passed, exit0, 0.17s.
- Parent checker: .venv-agent/Scripts/python.exe scripts/check_world.py merge; exit0, no eligible pair, 0 wrong merges.
- Pair p_dialogue_screen_4 -> p_world_2: 0 shared tiled base names; computed/truth offset None; merge refused.
- Checkpoint cannot demonstrate a successful merge; clarified unit-only fallback applies.
- Parent full suite: .venv-agent/Scripts/python.exe -m pytest tests/agent -q; 230 passed, exit0, 305.76s, including golden tests.
- W1 agent/ 2,042 -> 2,115 (+73/<=+75); checker 60/70; W1.3 adds 0 agent lines.
- Requests spent0; runs/e3_budget.json total614/M1596/smoke18, M1 remaining454.
- Protected hash audit at W1 checker review: 0/269 changed (267 runs/m1 files, contracts.md, ledger).

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; StubModel/GridEnv tests make no cloud calls.
- Code reads only through codegraph_explore within active packet read list; Ponytail full and Headroom mandatory.
- Headroom proxy unreachable; stored large outputs may require explicit retrieval.
- tests/agent/data/ gitignored; commit future data explicitly with git add -f.
- Measure agent/ net lines; do not hide overrun by compacting unrelated code.
- When any attribute, variable or field moves/removes, grep tests/agent and all agent/, including the gated branch, after CodeGraph.
- Existing untracked .claude/settings.json predates this session; preserve it.
- W1 evidence: .tmp/r1-wm/w1-merge.txt, w1-merge-unit.txt, w1-notebook-act.txt, w1-suite.txt.
- Checker scores the actual persisted World.merge offset and resets checkpoint copy for each pair.
- Verify scratch-delete paths resolve within the intended .tmp directory before recursive deletion.

## Deviations from plan and contracts
- W1 original +35 estimate superseded by user's <=+75 budget; W1.1 notebook -1 versus -2 estimate already accepted.
- No tombstones, new contract changes, loop/ edits, runs/m1 edits or ledger changes in this session.

## Do not retry
- Do not redo W0, W1.1/W1.2 or P2; their acceptance is established.
- Never fabricate exits or regenerate goldens without an intended behavior change and reviewed diff.
- Never start P3 without STOP 2 approval, reset/refund/raise ledgers, delete caches/checkpoints, git add -A or push.

## Open issues for W2
- Implement W2.1 -> W2.2 -> W2.3; no perception or model calls inside travel.
- Confirm checkpoint94 graph has 2F -> 1F -> Town via check_world.py graph before PyBoy checks; no chain means stop.
- Respect non-merged-place filtering in resolve and all cross-place scans.
- Verify every moved variable/field across agent/ and tests/agent before handback.
- Run every WP acceptance, full suite, exact gate; record line numbers and gate legs before commit.
