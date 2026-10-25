# Handoff notes

Last written: 2026-10-24. Round r18-fix4 complete; G18I passed; LV18f ran exactly once (85 steps, exit 0).

## Code and commits

- Branch feat/lean-agent; base af48210. Nothing pushed.
- Plan/approved contract 090ed44; S1 82b7ea7; --no-ff merge 55291bb.
- Delete World.probe and loop probe execution/locals; trace keeps probe:null.
- Live resume preserves self_spot, contradiction counter and motion-lock evidence.
- Locked off-self player-kind entities count as actors; self index remains excluded.
- Ceiling: busy-scene residual rejection can delay reacquisition after a real unlock.
- S1 source/tests net -41 lines (+55/-96); production alone -52.
- Two probe tests deleted; three regressions added; chosen-motion acquisition test reused.
- Existing resume assertion changed only as pre-approved; no other existing assertion changed.
- New P3 test corrected with explicit user authorization: silent first, held second, never blocked.
- Both lanes explicitly gpt-6-luna HIGH, fresh context, parallel, one WP each; no model requests.

## Validation

- Wave 0: 902 passed, 1 skipped, 369.61s.
- Base T18I: G-a/G-b/G-c FAIL; G-d H67/G34/F25 PASS, before any S1 merge.
- T-S1 after authorized test-only repair: 903 passed, 1 skipped, 391.39s; T18I all PASS.
- G18I merged suite: 903 passed, 1 skipped, 391.64s; T18I/H/G/F all PASS.
- T18H 67/0, T18G 34/0, T18F 25/0 outputs exactly match base.
- T18-O2/O3 byte-for-byte unchanged; O3 85/85 scored steps agree, 77 excluded, 4 map flips.
- Golden data unchanged; never set W0_REGEN. No new production gate/module/class/option filter.

## LV18f

- Copied m1_r17_boot to m1_r18f_odo; resume checkpoint 65; steps 66-150, truth through 151.
- One invocation (manifest 2), max 250 requests. Ledger 10313 -> 10430: 117 used, 133 below 10563.
- 225 call rows, including cache hits; 2 logged malformed replies; runner recovered and exited 0.
- 0 unchosen presses, probe null on all 85 rows, 12/12 world steps locked (100%).
- 1 stable lab visit: RAM-tile offset (5,3) on all 12 scored world steps; odometer gap 0.
- Odometer 28891 frames, 2672 uncertain, 7 cuts, dropped 0 (world/non-world both 0).
- No NPC-wall hit. Literal any-sprite metric has 1: step102 RIGHT into Poké Ball/table at RAM(6,3).
- 0 lab re-entries; one Pallet->lab crossing; inferred lab entrance (0,8) DOWN maps to RAM(5,11).
- Party>=1 first world step113; only 4 later world steps in horizon. No lab exit observed.
- Step120 selected Pallet exit; stopped into dialogue/battle. End150 post-battle dialogue, map40 RAM(5,6).
- No Pallet continuation/new place after lab; 15-world-step exit criterion not fully observable.
- Viewer remains up and followed m1_r18f_odo. No second live run.

## Open issues and artifacts

- Fake top-wall door remains open by scope; not fixed or claimed fixed by this round.
- Literal sprite-target wall target0 is not met (Poké Ball prop); NPC wall target0 is met in this sample.
- Lab departure/Pallet behavior remains unobserved; evaluation is informational, not a commit gate.
- First-seen model mislabel 'shop' sticks on the lab this run; no naming-policy change in S1.
- All 1070 original r17 files and 5 user files hash unchanged. No ledger edits outside runner.
- Report: .tmp/r18fix4/report.md; gates: .tmp/r18fix4/g; live: .tmp/r18fix4/lv18f.log.
- Read-only comparison audit: .tmp/r18fix4/metrics.py and metrics.json (r17, LV18c/d/e/f).
