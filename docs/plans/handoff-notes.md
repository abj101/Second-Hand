# Handoff notes

Last written by: Codex orchestrator, 2026-10-13, at PS STOP 6a. Next: user approves L3 live spend; then L3a, L3b, STOP 6b.

## State of the code
- Branch feat/lean-agent; PS in progress at STOP 6a; S1, S0 baseline, S2 and E complete; L3 unstarted.
- Describe-only perception; act builds the action from a numbered entity or bare advance/cancel/type, go_to or buttons.
- Loop constructs one affordance after World.name using exact entity_ids[n]; no option/fuzzy-target plumbing.
- Prompt version e5-perceiver-9; old checkpoint perceptions are invalidated. S1 mark/raw/cache behavior preserved.
- normalize, World.fix_player and World.mark are unchanged; legacy scripts remain loadable with empty affordances.
- Accepted scripts/eval_frames.py and scripts/frames.json remain immutable; do not rebuild or relabel them.

## Decisions in force
- STOP 6a requires separate live-spend approval. No L3 run or copy has started.
- Every subagent explicitly gpt-6-luna HIGH; one slice each; no Opus; root reviews and runs checks.
- S2 used one repair for two fixtures; it passed. E audit used zero calls/writes.
- No game-specific agent code, new decision gates, loop/ or contracts.md edits.
- L3 retains checkpoint 70's stale rival/player memory and notebook goal; repeats ≥10 stop after L3a.

## Measured numbers
- S0→E frame ok: 8/10→10/10; world 3/5→5/5; UI 5/5→5/5; self-target flags 2→0.
- Dialogue 1/1, menu 2/2, keyboard 1/1 in both arms; title 1/1 in both.
- Pre-fix marked player identity 5/5 in both; E no-fix rows 3/5 (two name-only player character renames).
- Root targeted repair: 97 passed, 1 warning in 187.31s; root full: 240 passed, 1 warning in 382.40s.
- Subagent exact full suite: 240 passed, 1 warning in 274.38s. Golden regen: 1 passed in 23.95s.
- Closing full suite: 240 passed, 1 warning in 244.59s (0:04:04).
- Lines: S2 agent 2400→2399 (-1); scripts 3413 unchanged; tests/agent 3873→3916 (+43); report only.
- Ledger total 971 / smoke 48 / M1 923 / a142 b297 p25 q60 c306; M1 left 127; PS spent 40/160.
- S0 20/26, E 20/26; live 0/108. Protected 934 file hashes and accepted input hashes unchanged.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; full suite tests/agent only, -q -p no:cacheprovider.
- Results: .tmp/ps/baseline.json, new.json; logs s0_baseline_run.log and e_run.log; same ten frames/seeds verified.
- UI button scoring is permissive. Baseline m1/88 press point near self is flagged despite why intending UP.
- Identity metric uses raw player labels before fix; two E fixes change names only, not sprite position/kind.
- Goldens: only act/trace.jsonl changed. Steps 1–2 i 1→0, options 2→1; steps 3–8 options 1→0.
- All other trace fields and all memory goldens unchanged; regenerated only with W0_REGEN=1, then unset.
- CodeGraph first; Ponytail full; Headroom proxy unreachable, local compression/retrieval works.

## Deviations from plan and contracts
- S0's prior failure was fixed/accepted in the user session; this session only ran the accepted evaluator.
- S2 first checks failed on raw coordinate scaling and a missing NPC fixture; one authorized repair passed.
- Approved only the expected prompt-version line in test_trace_brief.py:37, required by S2.
- Subagent accidentally ran repository-wide pytest; stopped at 17%, no files changed; correct agent suite passed.
- Exact proposed S2 contract text is in PS.md §9, alongside the retained S1 proposal; contracts are unchanged.
- Contracts SHA256: cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70.

## Do not retry
- Do not rerun S0/E or spend more offline requests; both completed, 40 total, immutable results saved.
- Never overwrite runs/m1_long_l1, runs/m1_long, runs/m1, runs/m1_act; L3 copies to runs/m1_ps.
- Never reset/refund/raise ledger caps, commit runs/ or .tmp/, git add -A or push.
- Preserve .cache/model/ and unrelated untracked .claude/; never change S1 eligibility for blank fixtures.

## Open issues for PS
- Await approval for L3 ≤108 requests (L3a 60 + L3b 48); current M1 127 remaining is sufficient.
- Before live run full suite, read ledger; copy m1_long→m1_ps, SHA256 verify; trim only copied frames above 70.
- L3a resumes checkpoint 70 for 30 steps with --max-calls 60 --stage c; repeats ≥10 stop without L3b.
- L3b resumes L3a's last checkpoint, --steps 40, --max-calls 48; expected cap exit 3 means checkpoint/report/stop.
- Report live progress first, then health and worst stuck window; eval_run.py --health --from-step 71.
- Place fragmentation and stale player-evidence goals remain PS §7 exclusions.
