# HF handoff

## State of the code
- HF1-HF4 done (offline gates passed). HF5 offline gate passed; program is offline verified, autonomy not validated. Read program-harness-reliability.md before a packet.
- Audited baseline: a3695e9 on feat/lean-agent, plus existing runner/test budget edits and untracked .claude/; inspect actual checkout at entry.
- IP conversation/fact integration is present; its historical gate was 354 tests, not a newly measured HF baseline.

## Decisions in force
- GPT-6.1 Sol orchestrator / gpt-6-luna HIGH workers, or user-selected Sonnet 5.5 / Haiku HIGH on a compatible host.
- Ponytail Full; CodeGraph first; zero network requests during HF1–HF4 and HF5 offline.
- Models retain goal/action choice; repair valid execution and feedback, no history-based option suppression.
- Shared HF contract proposals are in program-harness-reliability.md; contracts.md remains unchanged.

## Measured numbers
- m1_ip: 150 steps; final 28 no-change outcomes, steps 123–150; 30 outside-house selections, steps 120–149.
- Checkpoint150 failures: outside house 31 versus exit: door mat 7. Actor blocks correct route-frame position.
- Planning-time ledger: total 3120/3125, M1 3072/3075; reread rather than assuming availability.

## Environment and gotchas
- Python: .venv-agent/Scripts/python.exe. Repro checkpoints: m1_ip/000125 and 000150; other saved checkpoints include 000025/42/50/75/100.
- Protected evidence stays in runs/; all execution branches use .tmp/hf/. Original cache entries are preserved.
- Headroom source export was denied by automatic review; proxy was unreachable. Use local inspection.
- Truth S is pre-action; trace S tile is post-action. Compare trace S with truth S+1.

## Deviations from plan and contracts
- HF1: contracts 1-2. HF2: contracts 3-4 (+ factual planner cause); see HF1/HF2 Result for exact additions.
- Suite after HF3: 454 passed + 1 baseline failure (test_stage_b_cap_override..., budget-edit related, not HF). HF2 regenerated one golden (w0_golden/act trace) for choice.intent.
- HF3: entity `selectable` (True only); modal select offered/dispatched only for it; run_affordance select refuses otherwise (failure stage `interaction`, no evidence written). Perceiver prompt/schema changed -> old cached perceptions miss; `eval_act --om cases` and `--om talk --run m1_decide3` abort on cache misses (HF5 adapts/decides re-recording). Live perceiver compliance with `selectable` unmeasured.
- Do not infer a coordinate error from mismatching pre/post step timestamps.

## Do not retry
- Do not launch a live run or raise ledger limits from this plan alone.
- Do not rewrite goal prompts before deterministic failures and feedback are corrected.
- Do not replay cached perception from the old route after the new route diverges.

- HF4: rows carry observation_status/execution/action_step/invocation/call_ids; state has pending+modes; harness.json has provenance; run dir has manifest.jsonl, images/, richer calls.jsonl. Suite after HF4: 494 passed, 1 baseline failure (+1 fixture fix in test_type_text). Goldens regenerated twice for additive keys only.

## Open issues after HF5
- Fixed in HF5 (user-approved): agent/loop.py first-attempt no-route approach now refused with zero input (stage route); `refused = bool(t and act_route is None)`. Suite: 513 passed, 1 failed (baseline test_stage_b_cap_override).
- HF5 added: tests/agent/test_harness_reliability.py (8), `eval_act.py --om reliability` (checkpoint125: primitive PASS, continuation INCONCLUSIVE at 126, exit 2), execution-based eval_run metric. Old OM cases/talk replays exit 2 incompatible; fixture re-record undecided.
- Live validation (checkpoint repair, fresh boot) not authorized; ledger 3120/3072 unchanged; requests 0.
- Small: test_boundaries lacks an agent/ -> scripts/truth import check; probe presses not settle-gated; memory travel legs ignore remembered blockers.
