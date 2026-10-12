# Handoff notes

Last written by: GPT-6.1 Sol W1 orchestrator, 2026-10-12; amended by Claude after the user's decision. Next: W1.3 (check_world.py merge), then the W1 gate.

## State of the code
- Branch feat/lean-agent; HEAD bbbac46; W0 accepted at e403541 and must not be redone.
- W1.1 is implemented and reviewed; notebook places removed, act accepts known place names.
- W1.2 accepted and committed (W1 WIP): World.name/merge, no tombstones; src rows stay as history under the merged place. 230 tests pass; agent/ 2,115 (W1 budget revised to <= +75).
- W1.3, W2, W3 and P3 not started; W1 gate not yet run.
- Modified phase records: docs/plans/W1.md, docs/plan.md and this file.

## Decisions in force
- User rule 7 stops work when a frozen contract needs a change; orchestrators never edit contracts.md.
- merged_into tombstones REJECTED; contracts.md#world 'Merges' rule: cross-place readers (W2 resolve, W3 view, evaluators) skip rows whose place is merged. W1.3 with no eligible pair exits 0 and prints 'no eligible pair'.
- Program order remains W1 -> W2 -> W3 -> STOP 2 -> P3 -> P4 -> W4.
- GPT-6.1 Sol orchestrates; one fresh gpt-6-luna HIGH subagent per WP, explicit model/effort, sequential execution.
- No cloud calls before P3; live spend needs STOP 2 approval.
- No game-specific agent code, new behavioral gates, loop/ edits, runs/m1 edits, git add -A or push.

## Measured numbers
- Parent W1.1: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_notebook.py tests/agent/test_act.py -q; 7 passed, exit 0, 0.31 s.
- Parent stopped-draft diagnostic: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_world.py -q; 2 passed, exit 0, 0.23 s (.tmp/w1/world-draft-tests.txt).
- No full-suite or offline-merge gate measurement exists for W1; targeted tests do not imply phase acceptance.
- Agent/ 2,042 -> 2,117 (+75); world 72 -> 148 (+76 versus <=+40); loop 213 -> 213; notebook -1; act 0.
- New tests/agent/test_world.py is 45 lines (<=80); the subagent's last reported count was stale.
- Requests spent 0; runs/e3_budget.json remains total614/M1596/smoke18; M1 remaining454.
- Hash audit: all 267 runs/m1 files plus contracts.md unchanged, 0/268 changed (.tmp/w1/protected-before.json).

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe with forward-slash paths; unit tests make no cloud calls.
- Code reads only through codegraph_explore and the active packet read list; Ponytail full is mandatory.
- Headroom proxy was unreachable; compression calls could store text but did not reliably summarize it.
- tests/agent/data/ is gitignored; commit future test data explicitly with git add -f.
- loop.py is dense; measure agent/ net growth, and report impossible budgets numerically without unrelated compaction.
- For each removed/moved Loop attribute/local, grep tests/agent and all agent/loop.py including the gated branch after CodeGraph.
- Existing untracked .claude/settings.json predates W1; preserve it.

## Deviations from plan and contracts
- W1.1 notebook -1 versus -2 estimate; W1.2 world +76 versus <=+40; phase +75 versus approximately +35.
- Draft redirects use place:null and merged_into on source entity/tile rows; these semantics are absent from frozen contracts.
- Memory's raw last-row-wins rows/get do not implement deletion; in-memory removal would resurrect rows on reload.
- contracts.md#world gained the 'Merges' rule (user decision via Claude, 2026-10-12).
- W1.1-W1.2 committed as W1 WIP; the gate commit follows W1.3.

## Do not retry
- Do not redo W0 or rerun P2; both are accepted.
- Do not continue W1 coding before the contract decision, or skip directly to W2/W3.
- Do not regenerate W0 goldens without an intended behavior change and reviewed diff.
- Do not start P3, reset/refund/raise ledgers, delete caches/checkpoints, or push.

## Open issues for W1
- W1.3: build check_world.py merge per the clarified card; then run the W1 gate.
- If approved, audit active identity/spatial readers and redirect relations from collapsed entity IDs; expand WP scope only as authorized.
- Review pending-flag clearing, player frame shift and source/destination merge data before acceptance.
- Reconcile world +36 over its WP cap and phase +40 over its estimate; do not hide growth in unrelated code.
- W1 checker criteria conflict: no eligible pair may rest on units in gate section, but W1.3 requires >=1 correct merge to exit 0.
- Complete W1.2 acceptance, W1.3, full suite and exact gate only after the blocker is resolved; then fill Result and commit listed paths plus docs.
