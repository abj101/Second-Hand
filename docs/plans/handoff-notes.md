# Handoff notes

Last written: 2026-10-24. Round r18-fix3 complete; G18H passed; LV18e ran once, ended early by user authorization.

## Code and commits

- Branch feat/lean-agent; starting base c33bb04. Nothing pushed.
- Plan/approved contract 8420380; R1 e02b7bc; --no-ff merge e05308f.
- agent/space.py +2/-1: route never enters observed exit tile unless goal, in both passes.
- Start-on-exit, forbidden exit-direction press, inferred exits and all callers otherwise unchanged.
- Ceiling: old observed exits may be pressed-from tiles; store cut landing semantics if detours matter.
- Four new tests in test_space.py (+36); no existing assertions or goldens changed.
- All lanes explicitly gpt-6-luna HIGH, fresh context, parallel, one WP; resumed after usage reset.
- R2/D uncommitted scratch only. All three worktrees removed normally. No forced removals/rewrites.

## Validation

- Wave 0: 898 passed, 1 skipped, 371.20s. Worker: 900 passed, 3 skipped (no run fixtures), 374.31s.
- T-R1 with main fixtures: 902 passed, 1 skipped, 383.48s; T18H all PASS.
- Base T18H red before merge: S1 FAIL/S2 PASS; fallback UP,LEFT enters observed exit(7,5).
- Initial gate green because strict routing detoured around entity; reviewed, added fallback check.
- R1 fallback LEFT,UP avoids exit; direct exit goal UP works. Checkpoint explore_dirs yields no routes.
- G18H suite: 902 passed, 1 skipped, 368.76s. T18H/G/F all PASS, standalone scripts exit0.
- T18G 34/0, T18F 25/0 outputs unchanged; T18-O2/O3 also diff 0 lines.
- O3: 85/85 scored steps agree, 77 excluded, 4 map flips. No W0_REGEN/new production gates.

## LV18e and pending diagnosis

- m1_r18e_odo copied from m1_r18d_odo; one resume from 145, requested 20 steps/max 80 calls.
- User authorized early stop after enough evidence. Completed 146-150; checkpoint 150 retained.
- Window-close attempts failed; exact runner stopped after checkpoint 150. Exit -1; partial 151 retained.
- Ledger 10292 -> 10313: 21 requests (20 completed + 1 partial); 59 below ceiling 10372. No rerun.
- Two unintended lab re-entries: probe UP 146 and 150; 150 returns Pallet within same trace row.
- Selected-route executed-prefix audit: 0 intermediate known observed exits; explicit exit goals allowed.
- Full planned dirs not persisted; unexecuted tails and 20-step horizon not scored.
- Lab offset(5,11), 3 rows 146-148; Pallet offset(5,6), 2 rows 149-150; each visit stable.
- Existing Pallet/lab only; Pallet arrival(7,6), ends(6,6). No new place.
- Odometer: 6880 frames, uncertain 960, cuts 6, gap 0, dropped 2 (146,150). Dropped target fails.
- Crossings +2 Pallet@7,5>lab,+2 lab@-1,0>Pallet; intra-step150 warps hidden by endpoint maps.
- D: original 146 has 1 probe + 9 selected inputs; transition likely precedes approach baseline.
- Offline 146 detects first blank cut, does not reproduce miss; 145 replay matches after-image exactly.
- Hypothesis: probe endpoint check loses a nonblank intermediate jump followed by compatible scroll.
- Proposed probe odometer jump/blank check plus one test NOT IMPLEMENTED; needs user approval.
- Fake top-wall exit, walls into NPC tiles, probe behavior and dropped segments remain open.
- Original 1196 run files and five user files hash unchanged. Viewer remains up.
- Full report: .tmp/r18fix3/report.md; gates: .tmp/r18fix3/g; diagnosis: .tmp/r18fix3/D/diagnosis.md.
