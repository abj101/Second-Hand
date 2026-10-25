# Handoff notes
Last written:2026-10-25. R20 implemented, G20 passed, single LV20 completed. Never pushed.

## Source and implementation
- Branch feat/lean-agent; base341264d; live/code HEAD1fb1ecf.
- Wave0 docs/approved notebook contract24e9a74.
- N3 b62f3ba, merge05209e7:optional act replan flag; T1 needs a flag; text always retained; T5 unchanged.
- Tile first step persists; legacy missing first means old. Unexplored also closes half a screen beyond every old tile.
- A2 b94a08f, merge1ab2f20:observed settled:true, inferred settled:false; settled arrivals absorb post-cut segments.
- Known legacy src:inferred remains unsettled without the new flag; other legacy rows default observed.
- Trace/view matches are settled; inferred-view promotion updates the flag and travel refreshes its edge.
- Unsettled travel retains prior fold-last/drop-middle behavior; zero-motion presses/bumps remain processed.
- World contract1fb1ecf. No new action filters, classes/modules, game-specific logic or RAM reads in agent/.
- User approved minor-medium compatible test changes/contract-preserving decisions; no further permission was needed.

## Verification and diagnosis
- Baseline910passed1skipped. Main T-N3:915passed1skipped. G20:922passed1skipped,29000warnings,151.84s.
- T18F/G/H/I:25/33/65/133PASS,0FAIL. T18-O2 output identical; O3 boot85/85 and phase109/109 JSON identical.
- Golden fixtures unchanged. Comparators exclude only additive replan/first/settled/absorbed; direct tests cover metadata.
- All4 reconstructed/verified crossing cases retain RAM-minus-tile[5,6] on merged production travel.
- Historical lab full post-cut segment[112,80] ends tile[0,0]/RAM[5,6]; [7,6]/[12,12] was only its forced-step prefix.
- House155 intermediate replay unavailable; saved checkpoint proves old tile[0,1]/RAM[5,6] vs observed anchor[0,0].
- D2 confirms P3 semantics. House155 re-derivation explicitly uses measured r17 template plus saved endpoint.
- LV19 battle78requests/26steps:26act,36perceiver,16skill. Perceiver excess is10 fresh pre-action reads, not retries.
- No P4 cost code:stored menu schema or combined act/menu reading could each save up to16 LV19 requests; alternatives.

## LV20, exactly one invocation
- runs/m1_r20_odo resumed checkpoint150; steps151-210 completed,exit0; max160,actual139requests.
- Ledger10629->10768 (smoke50,m110718), below10789 ceiling; no rerun,extension or early stop.
- Plans7:T4x3,T2x1,T3+T5x3. New T1 raised4,qualified T1 dispatch0; text retained and consumed through other triggers.
- One replan flag156 cited goal fulfillment; no eligible T1 was queued. No obsolete post-battle defeat quest appeared.
- Lab exit154 (LV18g152,LV19 156). One new place p_place_6/map39 at159; return160.
- Unexplored q11 closed on new ground182 after retarget179; q12 closed200; same place p_place_4 across Route1 seam.
- Furthest Route1 RAM y25 vsLV19 24 (one row short); LV18g did not reach Route1 in comparison window.
- World91requests/40steps=2.275; battle25/10=2.5. Six existing-cache hits explain part of lower battle request cost.
- False crossings0; unchosen presses0; world lock40/40=100%; odometer gap0.
- Pallet offsets[5,6] across both visits; Route1[5,42]; new place39[2,7]; one offset per visit.
- Dropped73:45 inherited battle,27 current battle,1 zero-motion transition. Absorbed1 world segment at160.
- Wall targets missed:NPC overlap1 at209/RAM[5,24]; crossed exit marked DOWN wall at160/p_place_6@0,0.
- Wall split39:10 perceived props,25 scenery/no entity overlap,1 RAM-confirmed sprite,3 unscored map-change rows.

## Open issues and evidence
- NPC/prop/scenery classification and wall-on-crossed-exit learning need a later scoped fix; LV20 did not gate commits.
- Initial exit-lab quest had no target until160; model continued exploring after arrival. Target/goal meaning remains imperfect.
- Fake top-wall door and shop label remain open by scope. Battle request optimization needs the user's choice.
- Full report,commit/net-line/test ledger: .tmp/r20/report.md; N3/report.md,A2/report.md and assertions.diff files.
- Metrics: .tmp/r20/metrics.json,analysis.json,extra.json,walls.json,segment-audit.json.
- Gates/integrity: .tmp/r20/g/,preflight.json,integrity.json,source-hashes.json,user-hashes.json,golden-hashes.json.
- D2 raw copied inputs and corrected diagnosis: .tmp/r20/D/; A2 merged-endpoints.txt and rederive_endpoints.py.
- Single-run log/exit: .tmp/r20/live.log,exit.txt. Original runs and user files stayed unchanged.
- N3/D2/A2 worktrees were clean and removed normally; no force,symlink,junction,regen or push.
