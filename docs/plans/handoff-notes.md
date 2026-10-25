# Handoff notes
Last written: 2026-10-25. r19 code merged and G19 passed; LV19 ended at its authorized call cap.
## Source and implementation
- Branch feat/lean-agent; validated/live code HEAD 4c8ed14 (base a01c9b5).
- Wave-0 plan/approved notebook contract: 2c03e15.
- O1 9a28248, merge dcb3bef: known-place rows worked with visits/last/beyond notes.
- N1 cf73fe8, merge 4c8ed14: own-idea quests, unexplored target, event floor correction.
- Own quests store why [], basis <=200, source own; names retain strict citations.
- Unexplored closes only on entry to a place first seen >= opened/retargeted.
- Only all-T2/T4 queues keep the floor. User approved the additional T1 test update in chat.
- Tests added four functions; detailed before/after assertions: .tmp/r19/N1/report.md.
## Diagnosis and gates
- D1 valid Loop replay stopped at step151: 558 RGB bytes differ from recorded after-frame.
- H1/H2 unconfirmed; T1 skipped; no P5/P6 or world contract amendment.
- H3 copied r18f replay65-90:26/26 RAM agreement. First row delta frame856 clears868.
- G3 proposal: compare stable destination writes/door anchors before changing map stitching; no code.
- Baseline904 passed,1 skipped. G19 merged908 passed,1 skipped.
- T-O1:902 passed,3 skipped; T-N1:906 passed,3 skipped (worktrees omit ROM/runs links).
- T18F/G/H/I PASS with unchanged PASS/FAIL counts. O2 exact output unchanged.
- O3 complete JSON unchanged: boot85/85, phase109/109 agree. All9 golden files unchanged.
## LV19 execution and outcome
- Copied source m1_r18g_odo to previously absent m1_r19_odo; checkpoint150, invocation4.
- Command: scripts/run_agent.py --run runs/m1_r19_odo --resume --from-step 150 --steps 60 --max-calls 120
- Exactly one invocation. Completed151-192 (42 steps); attempt193 hit call_cap, exit3.
- Ledger10509 ->10629, delta120; ceiling10630. No rerun, extension or manual early stop.
- Requests: perceiver54, act44, plan6, skill16; four cached rows and one zero-request cap row.
- Lab exit156 (LV18g152); nine party-present world steps through exit, five post-dialogue.
- North crossing159: UP choice, RAM map0 ->12; wild battle160. LV18g never crossed north.
- No new place minted: visual memory reuses p_place_4 and p_place_3 on map12.
- No T2 events (honesty0/0, zero false novelty); q11 frontier remains open.
- Plan5 rows/6 requests (LV18g8/10); add errors0 (was2); one name citation error remains.
- Four own-idea quests; no Goal:(none) world prompts (LV18g actual153,154,156,157).
- Untargeted house-route attempt175 chose bedroom; visual p_place_3, but RAM stayed map12.
- No RAM-confirmed house/lab re-entry after156; no crossing-step exit wall recorded.
- Walls18:15 RAM-scored nonsprite terrain,0 NPC/prop overlaps,3 unscored intermediate edges.
- Unchosen presses0; all42 rows locked, world15/15 (100%); probe null; odo gap0.
- Odo35287 frames,3566 uncertain,24 cuts. Dropped96 =90 battle +6 world-tagged middle travel.
- World-type drop target0 missed at175 (7-cut travel into battle); real motion/fade unverified.
- Each of5 (place,map) visits has one offset. Pallet[5,6] is an already-known origin.
## Integrity, artifacts and open issues
- All1469 source-run files, five user files and nine golden files unchanged by hash.
- All r19 worktrees removed normally; no force, no push. Only runner updated the ledger.
- Report .tmp/r19/report.md; raw metrics metrics.json, analysis.json, spatial-audit.json.
- D1 evidence .tmp/r19/D/diagnosis.md; reproduction replay_loop.py and paired frame/hash evidence.
- Gates .tmp/r19/g/; preflight.json, integrity.json, live.log and exit.txt preserve provenance.
- Frontier identity failure and inappropriate battle unexplored targets remain observations, no added validator.
- Fake top-wall door, shop label, persistent Pallet origin and conditional travel fixes remain open by scope.
