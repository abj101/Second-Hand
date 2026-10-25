# Handoff notes

Last written: 2026-10-25. LV18g measurement complete: one continuation, steps151-200, exit0.

## Source and scope

- Branch feat/lean-agent; live source HEAD745f88e (r18-fix4 result).
- Fix4 code: S1 82b7ea7; --no-ff merge55291bb; result745f88e.
- LV18g changed no code, tests, contracts or gates; no subagents or test/gate runs.
- Preflight matched HEAD, branch, ledger10430, five user files plus the new brief; viewer up.
- Copied m1_r18f_odo to previously absent m1_r18g_odo; never resumed the original.
- Source checkpoint150 locked at [0.45,0.4513888888888889], contradictions0.
- Metrics copied from fix4; only ranges changed to LV18f66-150 and LV18g151-200.

## Execution and integrity

- Command: .venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18g_odo --resume --from-step 150 --steps 50 --max-calls 100
- Exactly one new invocation (manifest3), no extension or early stop; all50 steps complete, truth through201.
- Exit0; ledger10430 ->10509, delta79; cap100, ceiling10530, margin21.
- 115 call rows: 79 requests +36 cached rows; zero malformed replies/errors.
- All1137 source LV18f files and all5 user files hash unchanged; only runner wrote ledger.
- Viewer stayed available. Nothing pushed. Commit only the brief and these notes.

## Measurements

- Unchosen presses0; probe null on all50 rows.
- World lock25/25 (100%); all50 end-step locks true; checkpoint150,155,...,200 counters0.
- No observed unlock/re-lock; transient intra-step counters are not logged.
- Dialogue step151; first world step152 exits lab40 ->Pallet0 through the bottom door.
- Exit is 1st post-dialogue world step, 5th at/after party>=1 world step113 (4 strictly after113).
- Door RAM(5,11) is inferred from stored tile(0,8) DOWN +lab offset(5,3), supported by motion images.
- No fake top-wall door choice; no untargeted lab re-entry.
- Then house loop:152 Pallet,153 bedroom38 via ground floor37,155 Pallet,156 bedroom via37.
- Step159 computer interaction crosses downstairs37;161 returns bedroom38; remains inside through200.
- Six endpoint map changes expand to nine crossings using stored edges and motion images.
- Intermediate warp RAM instants not logged; report separates measured endpoints from inferred paths.
- No new place IDs or Route1 crossing. Canonical names: lab 'shop', town 'outside house', house floors 'room'.
- Ends map38 RAM(4,5), party1, battle0, text_box false. Repeated object checks/advance; no northward escape.
- Six scored visits each have one offset: Pallet(5,6), bedroom(3,6), Pallet(5,5), bedroom(3,6), ground(7,1), bedroom(3,6).
- Pallet origin differs by one y tile between visits; cause unproven, no repair attempted.
- Odometer23927 frames,1545 uncertain,11 cuts,gap0,dropped47.
- Dropped45 are confirmed carried battle segments from source checkpoint150 (8571 frames), flushed at152.
- Remaining2 drops at153/156 are inferred world-tagged middle travel segments; world target0 missed.
- Classification uses serialized segment screen tags and travel tagging code, not before-screen label.
- NPC sprite walls0 detected, prop sprite walls0 detected; 9/13 edge events RAM-scored.
- Four intermediate-map edges have no at-press RAM snapshot; motion review shows terrain/scenery, not NPCs.

## Open observations and artifacts

- World travel's two dropped segments, house looping and cross-visit Pallet origin need future diagnosis.
- No code change, rerun or extension was made to address any observation.
- Fake top-wall lab door remains open by scope; not selected in this continuation.
- Report: .tmp/r18g/report.md; raw metrics: metrics.json; supplementary evidence: analysis.json.
- .tmp/r18g/source-hashes.json, user-hashes.json, integrity.json; live.log and exit.txt.
- Motion contact sheets: .tmp/r18g/motion-152.png, motion-153.png, motion-155.png.
- Prior fix4 full validation remains recorded in .tmp/r18fix4/report.md (903 passed,1 skipped).
