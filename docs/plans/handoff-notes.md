# Handoff notes

Last written by: GPT-6.1 Sol P2 orchestrator, 2026-10-12; amended by Claude after the user waived P2 place-name purity. Next phase: W0.

## State of the code
- Branch feat/lean-agent; HEAD 2ab17f4. P0/P1 committed at 4af5ce2; E4 build at 53a1e03.
- P2.1 implemented by gpt-6-luna HIGH: scripts/eval_act.py (89/90 lines), tests/agent/test_eval_act.py (40/40).
- P2 files (scripts/eval_act.py, tests/agent/test_eval_act.py) and docs committed after the waiver; no push. Pre-existing untracked .claude/ untouched.
- P2 Status is gate passed (purity waived by user, 2026-10-12). W0 is the first unfinished program row.

## Decisions in force
- Entry point docs/plans/program-r1-wm.md; order P2 -> W0 -> W1 -> W2 -> W3 -> P3 -> P4 -> W4.
- Orchestrator GPT-6.1 Sol; one WP per subagent, gpt-6-luna HIGH only, explicitly set.
- Failed gates stop the program. P2 purity was waived by the user (W1 makes place identity code-owned); P3 carries the real concern: repeated NPC interaction and re-entering visited maps (new P3 rows).
- Cloud only in P2/P3 through persistent M1 ledger; P2 cap 40, P3 cap 80; never reset/refund/raise a cap.
- No game-specific agent code, RAM for decisions, new behaviour gates, contracts.md edits or loop/ edits.
- Never modify runs/m1/; preserve checkpoints and .cache/model/; no git add -A or push.

## Measured numbers
- P2 command: .venv-agent/Scripts/python.exe scripts/eval_act.py --max-calls 40; exit 0, completed 15/15 frames.
- Parsed actions 15/15 (>=14); distinct text-supported goal keys 2 (>=2), including Mom frame 53 goal 1 (>=1).
- World frames 11: map 0x26 room 3/4 (75%, FAIL); map 0x25 room 6/6; map 0x00 world 1/1.
- Name room spans 0x25 and 0x26 (FAIL); evaluation exit 0 means completion, not a passed gate.
- Requests 29/40, matching total and M1 ledger deltas: total 585 -> 614, M1 567 -> 596, smoke 18 unchanged.
- Remaining: M1 454/1,050; phase 486/1,100. Stage a142/b297/p25/q60/c43 unchanged.
- WP acceptance: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_eval_act.py -q; 2 passed, exit 0.
- Full suite: .venv-agent/Scripts/python.exe -m pytest tests/agent -q; 224 passed, exit 0 in 305.79 s (.tmp/p2/full-suite.txt).
- Immutability check: 267/267 runs/m1 files unchanged; contracts.md unchanged (.tmp/p2/immutability-check.json).

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and CodeGraph-only code reads from the active packet's list; Ponytail full required.
- Ollama gemma4:31b-cloud was reachable; all 163 saved frames and default eval steps present.
- Review artifacts: .tmp/p2/table.md, rows.jsonl, summary.json, notebook/notebook.json, notebook/notebook.history.
- Ledger evidence: .tmp/p2/ledger-before.json and ledger-after.json. Original frames/truth never written.
- Eval resumes completed rows and notebook; summary requests counts the phase spend in the output directory across invocations.
- Evaluator adds repo-root sys.path for direct CLI execution; cached replies do not reserve a request, retries do.
- Headroom invoked; configured proxy unreachable, no measured savings. Existing SDL2 warning during tests.

## Deviations from plan and contracts
- Contracts and agent/ unchanged; no line overrun, new gates, live play, model comparison or prompt repair.
- Static input, wrapper and StubModel symbols explicitly referenced by the WP were read via CodeGraph.
- Table Goals added also lists removal keys at 44/53; exact notebook_ops/history distinguish edits; summary counts set ops only.
- Text-supported goal metric counts distinct keys over the episode; intro was later removed, final notebook holds one active goal.

## Do not retry
- Do not rerun P2; its result stands with the waiver.
- Do not resume the stopped M1/E4 live check or delete old paths; their gates remain unpassed.
- Runner code 3/4 requires checkpoint/report/stop; no ledger reset, refund, cap increase or polling a free-tier window.

## Open issues for W0
- W0 next (0 requests). P3 must report repeat_actions and reentries per its packet.
- Upstairs frame 44 is mislabeled Professor's Lab; upstairs/downstairs share `room`. Waived: W1 makes place identity code-owned.
- After the Oak goal, the eval often chooses another NPC interaction or house entry; live progress remains unmeasured.
- The table goal column should distinguish additions from removals in any later authorized evaluator update.
