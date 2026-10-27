# Handoff notes
Last written: 2026-10-27. Branch `feat/lean-agent`; HEAD `f0f093c` before the r24 result documentation commit. Never pushed.

## Prior rounds
- R22 base `d18d5b5`, O2 merge `ab73b27`, M2 merge `6849410`. O2 uses frontier cells and recorded crossings; M2 uses highlighted menu geometry. Full evidence: `.tmp/r22/report.md`.
- R22 gates: 944 passed/1 skipped; T18F/G/H/I 25/33/65/133 pass; O2 byte-identical; O3 boot/phase identical. D3 decoder correction and terrain/frontier diagnosis are in `.tmp/r22/`.
- R23 plan/contracts `21b9807`, `ec6355c`; T merge `1632c96`, NO merge `83ebcc9`, S merge `a66ee02`, O1 repair merge `ab761ed`; meter guard base `4770721`.
- R23 T routes over known floor, N shows game-given names, S stores last-seen meters, and O1 records original segment fates. Code stayed game-agnostic. Full evidence: `.tmp/r23/report.md`.
- R23 post-repair suite: 956 passed/1 skipped; the r24 base after the meter guard has 959/1. T18F/G/H/I 25/33/65/133 pass; O2 identical, O3 boot/phase identical; golden/user files unchanged. LV23 ran once, steps 211–270, 145 requests; old trace's O1 dropped-fate metric failed and was repaired offline without rerun.
- R23 controlled HP reading: printed player HP 15/15; enemy bar values were invented or PP counters mislabelled. Direction work is out of r24 scope.

## R24 approved design and state
- `docs/plans/fields-r24.md` is the approved brief. Fields replace free-text meters with `{owner,label,kind,value?,box}`. Numbers require printed digits; bars use `agent.observer.bar_fill` pixels; status shows bar percentages. No game-specific production logic.
- Wave 0 committed brief, kickoff prompt and three contract amendments as `f0f093c` (`docs: r24 fields plan and approved contract amendment`). Baseline: 959 passed/1 skipped in 393.12s.
- Worker F is uncommitted in `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r24F` on `r24/F`: 9 files, 101 insertions/46 deletions. Its repaired full suite passed 960/1. Approved test changes and a new synthetic bar test are detailed in `.tmp/r24/report.md`.
- G-off failed twice: first scratch import path, then 36/82 within 1 px against the required 61/61 drawn bars. Exact output `.tmp/r24/F/g-off-failure.txt`; scratch runner `.tmp/r24/F/bars2.py`. The round stopped before merge by the twice-failed gate rule.
- G-paid, post-merge regressions and LV24f were not run; no requests were spent. `.tmp/r24/g/g_paid.py` is prepared but not frozen or run. `runs/m1_r24_fields` was not created.
- Stored ledger remains 11223; effective ledger remains 11239/11600 after 16 r23 H1 posts outside the file. G-paid cap 20, LV24f cap 45 if the user authorizes continuation.
- Fourteen protected user/golden files matched their Wave 0 hashes. A separate one-line `tests/agent/test_track.py` working-tree edit appeared after Wave 0; r24 did not stage or edit it. Do not touch the user's five protected files.
- No `W0_REGEN`, push, forced worktree removal, or `runs/`/`Rom/` worktree link. Preserve `r24F` and `.tmp/r24/` for a user-directed next step.
