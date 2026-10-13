# Handoff notes

Last written by: Claude (user session), 2026-10-13, after fixing the S0 evaluator. Next: RUN the S0 baseline (no rebuild), then S2, E, STOP 6a.

## State of the code
- Branch feat/lean-agent; PS Status in progress; S1 done; S0 evaluator accepted, baseline not yet run.
- scripts/eval_frames.py + scripts/frames.json (10 frames) are accepted as is: do not rewrite them; just run them (PS.md §9).
- Baseline: `.venv-agent/Scripts/python.exe scripts/eval_frames.py` (≤26 attempts, ~20 requests, default out .tmp/ps/baseline.json); E: same with `--out .tmp/ps/new.json` after S2.
- S1 still owns locked player kind/name, marks model inputs on a copy and preserves raw frames/cache/cut behavior.
- Perception still proposes options; act still selects option indexes until S2.

## Decisions in force
- PS packet order S1 → S0 → S2 → full suite → E → STOP 6a → L3a → L3b → STOP 6b.
- Every subagent explicitly gpt-6-luna HIGH; one slice each, no Opus; root reviews and runs checks.
- S0's subagent and repair are used up; the user session fixed the scorer, labels and seeding; S0 needs no more subagent work.
- No game-specific agent code, new decision gates, loop/ or contracts.md edits.

## Measured numbers
- Inherited full suite: 238 passed, 1 warning in 246.74s (0:04:06).
- Closing full suite: 238 passed, 1 warning in 245.97s (0:04:05).
- Repaired py_compile and --dry passed; all nine dry rows printed with requests 0 (synthetic, not model accuracy).
- Root decode/wrong-way/cap checks passed; toward-stairs button assertion failed.
- Lines: agent 2400 unchanged; scripts 3122→3421 (+299); tests 3873 unchanged; goldens unchanged.
- Ledger total 931 / smoke 48 / M1 883 / a142 b297 p25 q60 c306; M1 left 167; PS spent 0/160.
- Protected evidence: 934 file hashes verified, zero changes; contracts SHA256 unchanged.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths.
- Frames: m1_long 3/4/5/18 UI, 37 2F facing up, 73 1F Mom; m1 23 keyboard, 71 1F, 88 2F facing down, 64 1F mid-room. m1_long 74 dropped (target in void).
- All world frames are marked; frames without a locked previous row use the labelled spot (harness-only, truth_injected).
- m1_long 71/72 are probes; 80 is black. m1 29 is dialogue, not keyboard; frames >94 stale.
- _buttons_toward: the first direction must move toward an acceptable point, and no direction may point away (one step toward a diagonal target counts).
- Evaluator uses no cache, fresh per-frame temporary memory, marker flag, pre-fix labels and a 26-attempt guard.
- CodeGraph used first; Ponytail full; Headroom proxy unreachable, local compression/retrieval works.

## Deviations from plan and contracts
- S0 stopped once on scoring; fixed outside the orchestrator by the user session (PS.md §9).
- This is scoring correctness against the card; no model-quality or progress gate was added.
- S0 draft 299 script lines (subagent final report incorrectly said 264); line counts report only.
- S0 proposes no contract change. S1's unapplied exact contract text remains in PS.md §9.
- Contracts SHA256: cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70.

## Do not retry
- Do not rebuild or relabel the S0 evaluator; if the live baseline output looks wrong, report it at STOP 6a.
- Never overwrite runs/m1_long_l1, runs/m1_long, runs/m1, runs/m1_act; L3 copies to runs/m1_ps.
- Never reset/refund/raise ledger caps, commit runs/ or .tmp/, git add -A or push.
- Preserve .cache/model/ and unrelated untracked .claude/.
- Do not change S1 marking eligibility to accommodate blank fixtures; that resolved behavior is correct.

## Open issues for PS
- Run S0 before S2 (S2 changes the prompts the baseline measures).
- UI-screen button scoring is lenient (A/START count for advance/select); report it alongside the numbers.
- S2 still requires fixture migration, unchanged normalize, prompt version bump and explained golden regen.
- Checkpoint 70's stale rival/player memory and notebook goal remain for the L3a repeat check.
- Place fragmentation and stale player-evidence goals remain PS §7 exclusions.
