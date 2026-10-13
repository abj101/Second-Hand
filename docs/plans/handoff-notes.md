# Handoff notes

Last written by: Claude (user session), 2026-10-13, after resolving PS S1. Next: PS S0 (baseline frame eval), then S2, E, STOP 6a.

## State of the code
- Branch feat/lean-agent; PS Status "in progress"; S1 done; S0, S2, E, L3 unstarted.
- S1: World.fix_player owns player kind/name when locked (1.5-tile keep, tile/2 takeover, insert, idempotent); World.mark draws the red outline on a copy.
- Only model inputs are marked; raw frames are kept for storage, cut/scroll, HUD crops, miss hash and trace frame_hash.
- Loop: _see keeps its four-value return; the current model image and flag are loop.seen_image and loop.seen_marked; the cache hit also needs the same marked.
- Checkpoint state adds perception_marked and frame_mark_eligible; trace adds marked.
- Perception still proposes options and act still picks option indexes until S2; S2 also bumps prompt_version.

## Decisions in force
- PS packet as written (audited, 16 findings folded in); order S1 → S0 → S2 → full suite → E → STOP 6a → L3a → L3b → STOP 6b.
- Every subagent gpt-6-luna HIGH (or Haiku under Sonnet), one slice each; never Opus; orchestrator reviews and runs checks.
- No game-specific code in agent/, no new gates/filters, no loop/ or contracts.md edits; proposed S1 contract text is in PS.md §9.

## Measured numbers
- Full suite after the S1 fix: 238 passed, 1 warning (pysdl2), 269.00s.
- Golden act/trace.jsonl regenerated: only `marked` and the act flag "the sprite inside the red box is you" (steps 3–8) changed; memory unchanged.
- Lines: agent 2369→2400 (+31); tests 3775→3873; report only.
- Ledger total 931 / smoke 48 / M1 883 / a142 b297 p25 q60 c306; M1 left 167; PS spent 0 of 160.
- 2026-10-13 diagnostics: player label 0/2 on facing-away frames as run; red box + caption 8/8; split arm B with box 3/3 by intent (1 try per cell).

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths.
- observer._blank: any frame ≥ 95% one colour is a blank transition, which sets scene_cut and stops marking. Test fixtures need non-uniform frames.
- Offline eval: exclude probe frames (m1_long 71/72) and runs/m1 frames above 94 (stale leftovers).
- S0 prototype: .tmp/perc_ab/split.py (untracked; keep). S0/E seed self_spot/_self_from_motion/prev screen from the previous trace row.
- Every HTTP attempt is charged (retries, schema fallback); S0/E wrappers raise at 26.
- Headroom proxy unreachable; local compression works.

## Deviations from plan and contracts
- S1 stopped once (fixture failure), then resolved outside the orchestrator by the user's session; see PS.md §9.
- contracts.md SHA256 unchanged: cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70.

## Do not retry
- Never overwrite runs/m1_long_l1, runs/m1_long, runs/m1, runs/m1_act; L3 copies to runs/m1_ps.
- Never reset/refund/raise a ledger cap, commit runs/ or .tmp/, git add -A or push.
- Do not fix S1's test by changing marking eligibility; the blank-frame behaviour is correct.

## Open issues for PS
- S2 regenerates goldens again (option plumbing removed); explain each diff.
- Checkpoint 70 still stores the player as e_rival_2 "rival" with notebook "Talk to the rival"; the L3a repeat check (≥ 10) covers it.
- Place fragmentation and stale player-evidence goals remain §7 exclusions.
