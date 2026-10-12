# Handoff notes

Last written by: GPT-6.1 Sol orchestrator, 2026-10-12. Resume: W2.3, after user resumes work.

## State of the code
- Branch feat/lean-agent; HEAD ec74d81: W1 gate passed (230 tests; 0 wrong merges, 0 eligible checkpoint pairs; unit fallback).
- W0 e403541 and W1.1/W1.2 453cc59 were already accepted; do not redo them.
- W2.1 actor-aware fold and W2.2 resolve/plan/travel plus Loop go_to are accepted, uncommitted.
- User requested wrap-up after W2.2; W2.3, W2 phase gate and W3 have not run.
- Modified code/tests: agent/{space,world,loop}.py, tests/agent/{test_space,test_world}.py; agent/ is 2,210 lines.
- W2 packet Result and docs/plan.md record partial completion; no W2 gate commit until its gate passes.

## Decisions in force
- Source rows remain history under place state merged; no merged_into tombstones. Cross-place readers skip merged-place rows.
- One fresh gpt-6-luna HIGH subagent per WP, explicit model/effort; root GPT-6.1 Sol.
- Measure agent/ net lines. Accept WP overruns up to 50% only when every acceptance command passes; larger overruns stop.
- Contract changes stop; first look for an existing field/record. Never edit contracts.md or loop/.
- No perception/act calls inside travel; no cloud before P3; W3 -> STOP 2 requires user approval before P3 live spend.
- Follow program §2, packet read lists, Ponytail full and Headroom; code reads only via codegraph_explore.

## Measured numbers
- Parent W2.1: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_space.py tests/agent/test_world.py -q; 9 passed, exit0, 0.15s.
- Parent W2.2: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_world.py tests/agent/test_loop.py tests/agent/test_world_golden.py -q; 58 passed, exit0, 254.51s.
- Parent full suite: .venv-agent/Scripts/python.exe -m pytest tests/agent -q; 235 passed, exit0, 316.50s; one SDL warning.
- Parent test_world selectors -k travel/fetch/leg: 3/1/2 passed respectively; test_space -k actor: 1 passed; all exit0.
- Physical three-room GridEnv test uses real approach/presses; one Loop step, no model call during travel, adjacent/facing remembered entity.
- W2.1: space +1/4, world +5/6, test_space +19/20; agent/ 2,115 -> 2,121.
- W2.2: world +89/60 (48.3% over), loop net0, test_world +104/80 (30% over); agent/ 2,121 -> 2,210.
- W2 total agent/ +95 versus approximately +65 (46.2% over); overruns accepted under user rule.
- Requests spent0; ledger total614/M1596/smoke18, M1 remaining454.
- Protected audit: 0/269 changed (267 runs/m1 files, contracts.md, ledger).

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; ROM and checkpoints 94/74 exist.
- Headroom proxy localhost8787 unreachable; stored outputs may require explicit retrieval.
- tests/agent/data/ is gitignored; explicitly git add -f any future test data.
- After moving/removing any attribute, local or field, grep ALL agent/ and tests/agent/, including gated Loop code.
- W2.2 removed norm: no dangling uses; expanded audit .tmp/r1-wm/w2-usage-audit.txt.
- W2 logs: .tmp/r1-wm/w2-actor-acceptance.txt, w2-routing-acceptance.txt, w2-part-suite.txt.
- Unit selector logs in the same directory: w2-travel-unit.txt, w2-fetch-unit.txt, w2-leg-unit.txt, w2-actor-gate.txt.
- Current protected baseline: .tmp/r1-wm/protected-before.json; do not use the older .tmp/w1 snapshot.
- Existing untracked .claude/settings.json predates this work; preserve it.
- Verify every scratch recursive-delete path stays inside the intended .tmp directory.

## Deviations from plan and contracts
- W2.2 world/test overruns accepted only after targeted and full-suite parent acceptance passed; no unrelated compaction.
- World.travel has optional trace=None; contracted three-argument calls work. Uses existing trace records, no new stored fields.
- Unique exact trace frame matches identify arrival/modal state. Unknown/ambiguous frames retain expected destination; identity is unverified.
- Final perception is available, but Loop retains travel's place rather than independently re-identifying it; PyBoy gate must assess this.
- No contract, loop/, runs/m1 or ledger edits; no game-specific production code or new abort gates.

## Do not retry
- Do not redo W0, W1, W2.1/W2.2 or P2; their acceptance is established.
- Do not start W2.3/W3 until user resumes; this handoff stops at the requested boundary.
- Never fabricate exits/aliases, inject truth-derived production records, or regenerate goldens without reviewed intent.
- Never reset/refund/raise ledgers, delete checkpoints, git add -A, push, or start P3 without STOP 2 approval.

## Open issues for W2
- Next WP W2.3 owns scripts/check_world.py <=+60: graph, travel, fetch; spawn Luna HIGH from its card with program rules/contracts/lessons.
- Run check_world.py graph first; no 2F -> 1F -> Town chain means stop. Read-only inspection found a stored chain; official graph check pending.
- Town p_world and 2F p_world_2 both named world, no aliases: resolve-by-name may prefer current place. Diagnose honestly; do not fabricate data.
- PyBoy gates pending: Town <=80 inputs/one travel/zero model calls; 1F fetch <=60 inputs/adjacent/zero model calls.
- Run remaining acceptance, full suite and exact phase gate; record leg truth tables, update docs, commit listed paths plus docs/ only on pass.
