# Handoff notes
Last written: 2026-10-25. R21 implemented, G21 passed, single LV21 completed. Never pushed.

## Source and implementation
- Branch feat/lean-agent; base b24b7d2; live/code HEAD fc31869. Initial docs/contract commit 477167f.
- M1 6867d71, merge373ed20: final cut-segment bump is a crossing press, never folded as a wall.
- Actors require two distinct inner-view misses or tracker loss; recent remembered live actor tiles remain held.
- observe_after/_record_view skip unresolved-place writes and preserve re-observation after matching.
- X1 7ef574f, merge695f16c: exact-tile reach n>=2 or known outward floor refutes unverified exits; crossings win.
- User required existing option assertions preserved: new reasons appear in world view, legacy option notes/order stay.
- User corrected T-X1: reach0,8 has n2 but no exit there; actual exit1,8 stays wall-refuted. No adjacent inference.
- B1 511f579, mergefc31869: stored layout/model item order/cursor crops; unique match skips menu read; grids always read.
- Learned order survives observe_screen. No new production module/class/filter, game-specific logic or RAM read.

## Verification
- Baseline922 passed/1 skipped; G21 final934 passed/1 skipped,29000 warnings. Twelve tests added, none deleted.
- Only prior actor-omission assertion changed: one miss remembered/misses1, second distinct miss gone.
- Worker M1 926/3 skipped; X1 923/3; B1 923/3. Extra worktree skips are expected missing ROM/data.
- F/G/H/I25/33/65/133 PASS; O2 output identical; O3 boot85/85 and phase109/109 complete JSONs identical.
- Step160 replay aligns10 seq/key pairs; DOWN15511 crossing leaves no door wall. Corrected T-X1 passed.
- Offline LV19 menus30 selects,27 reads,3 skips,3/3 cursor agreement (168/178/188); actual16px crop confirmed.
- Protected five user files, nine golden files and all1657 source-run files unchanged by SHA-256.

## LV21, exactly one invocation
- runs/m1_r21_odo copied from r20; checkpoint210 cleaned; steps211-270 completed, exit0, no extension/rerun.
- C1 removed194 entity versions/15 contaminated IDs, zero relations/knowledge; dry/live removals match; Memory loads.
- Ledger10768→10913:145/150 calls, five below10918 ceiling. Viewer stayed up.
- World self-lock33/33=100%; unchosen presses0; odometer gap0; false travel crossings0.
- Walls12:6 prop,5 scenery/no entity overlap,1 unscored map-change row; no NPC/sprite overlaps.
- Crossed-door walls0; held-actor target candidates0 (conservative actor-history superset check).
- One gone transition: e_npc_6 last244, misses1 at245, gone/misses2 by250; returned251. Inherited gone repaired212.
- Unresolved observation writes0; no new entity first in carried-cut windows; offsets stable per RAM-map visit.
- Route1 offsets[5,42] both visits; Pallet[5,6] at241. RAM transitions241/242, no new successful crossing versions.
- Menus14,13 reads,1 skip (224 SCRATCH); all14 post-results verified, zero mismatches/ambiguities.
- Battle68 requests/23 steps=2.9565 (LV19 3.0, LV20 2.5); cache alone did not lower total battle cost.
- Furthest Route1 RAM y24 (LV19 24, LV20 25); no new places. Plans6: T4x5,T3+T5x1.
- Seven already-refuted exit choices, including four explicit go_to trips; final reasons7 floor-beyond,1 reach,1 wall.
- End270: wild Rattata L2 battle, Charmander L6 at2/21 HP. Dropped59 segments on before-world steps.

## Open issues and evidence
- Refutation warnings reach planner view but not preserved action-option notes; action repeatedly chose refuted exits.
- Menu crop jitter/dynamic content causes frequent fallbacks. Lab shop label, replan misuse and held fates stay open.
- Main report .tmp/r21/report.md; audits metrics/analysis/extra/walls/supplement/final-audit.json under .tmp/r21/.
- Gates .tmp/r21/g/; integrity-final.json; live.log/exit.txt; M1/X1/B1/C1 artifacts and cleanup removed.json.
- Historical r20 details/endpoints/diagnosis remain in .tmp/r20/report.md, A2/, D/ and g/.
