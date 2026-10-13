# Handoff notes

Last written by: Codex orchestrator, 2026-10-13, after PS S0 failed review and its one repair. Stop; do not skip PS.

## State of the code
- Branch feat/lean-agent; PS Status gate failed at S0; S1 remains resolved and committed.
- New scripts/eval_frames.py and scripts/frames.json are an evaluator draft, not an accepted baseline.
- S0 baseline not run; S2, E and L3 unstarted; no STOP 6a comparison exists.
- S1 still owns locked player kind/name, marks model inputs on a copy and preserves raw frames/cache/cut behavior.
- Perception still proposes options; act still selects option indexes until S2.

## Decisions in force
- PS packet order S1 → S0 → S2 → full suite → E → STOP 6a → L3a → L3b → STOP 6b.
- Every subagent explicitly gpt-6-luna HIGH; one slice each, no Opus; root reviews and runs checks.
- One S0 subagent and one repair used. No third repair or root implementation allowed under this kickstart.
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
- S0 final frames: m1_long 3/4/5/18 UI, 37 up, 74 down, 73 Mom; m1 23 keyboard and 71 sideways.
- 37 previous row is unlocked, so unmarked; later three world examples marked; m1 self injection harness-only.
- m1_long 71/72 are probes; 80 is black. m1 29 is dialogue, not keyboard; frames >94 stale.
- _buttons_toward requires BOTH directions to a diagonal target; RIGHT alone toward stairs returns false.
- Exact failure: .tmp/ps/s0_repair_orchestrator_check.log; dry output: s0_repair_orchestrator_dry.json/log.
- Evaluator uses no cache, fresh per-frame temporary memory, marker flag, pre-fix labels and a 26-attempt guard.
- CodeGraph used first; Ponytail full; Headroom proxy unreachable, local compression/retrieval works.

## Deviations from plan and contracts
- Stopped before baseline spend on the second evaluator implementation failure, per user one-repair limit.
- This is scoring correctness against the card; no model-quality or progress gate was added.
- S0 draft 299 script lines (subagent final report incorrectly said 264); line counts report only.
- S0 proposes no contract change. S1's unapplied exact contract text remains in PS.md §9.
- Contracts SHA256: cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70.

## Do not retry
- Do not auto-repair or continue from a gate-failed PS packet; revised instructions are needed.
- Never overwrite runs/m1_long_l1, runs/m1_long, runs/m1, runs/m1_act; L3 copies to runs/m1_ps.
- Never reset/refund/raise ledger caps, commit runs/ or .tmp/, git add -A or push.
- Preserve .cache/model/ and unrelated untracked .claude/.
- Do not change S1 marking eligibility to accommodate blank fixtures; that resolved behavior is correct.

## Open issues for PS
- Fix evaluator scoring with explicit new authorization, re-review labels/harness, run S0 before S2.
- UI button scoring is broad; review alongside toward-target scoring before spending requests.
- S2 still requires fixture migration, unchanged normalize, prompt version bump and explained golden regen.
- Checkpoint 70's stale rival/player memory and notebook goal remain for the L3a repeat check.
- Place fragmentation and stale player-evidence goals remain PS §7 exclusions.
