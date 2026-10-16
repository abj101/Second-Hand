# Handoff notes

Last written: 2026-10-15. RCR R1 offline gate passed on feat/lean-agent; the final live retry completed at the user's 80-step cap. Evidence: R1-identity.md §9 and MASTER.md §8.

## State of the code
- S0 landed at d83bd9a; eligible R1 files are committed at d787836.
- R1 provides geometry/track identity with aliases/kinds, position-based self exclusion, departure-only crossings, blocked-push exit truth, refs/canon/known/history and pixel-offset place closure.
- Names no longer merge places; Memory.view/resolve/expire/add_edge and name/landmark identity paths are removed.
- Loop's R1 changes are applied but remain unstaged alongside the user's live-view edits.
- The golden changes exactly four memory files; its trace is unchanged.
- First live run completed 150 rows without gameplay; one retry stopped at 5 rows on request; final retry runs/m1_rcr_r1_live_retry2 completed 80 rows and was scored.

## Decisions in force
- CLAUDE.md requires Ponytail full, CodeGraph before code reads/edits, Headroom for large output; the compression proxy is unreachable.
- Every worker spawn explicitly used gpt-6-luna with reasoning high; all interrupted workers have finished.
- Earlier bounded same-tier failure/retry history remains in R1 §9; no additional repair or runtime steering was introduced during finalization.
- G8 was corrected by the user: inferred stairs remain unverified, retain destination/routability; the mat is verified by departure; phantom uses must be zero.
- STATIC_GATE stays 1; no Q2 elimination logic, threshold tuning or perception prompt changes were needed.
- Never stage files carrying live-view edits, never git add -A, never push.
- The user authorized the final 80-step retry; it is complete. Do not start another live run or implement R4 as part of this R1 task.

## Measured numbers
- Earlier offline gate snapshot full suite: 658 passed, exit 0, 104.36 s; command PY -m pytest tests/agent -q -p no:cacheprovider -n auto.
- Final replay: Mom/Pallet NPC 1/1 IDs, zero switches; corrected original-code comparator 3/1.
- Pallet records/floor 39/69 (limit 86.25), zero unused same-class same-tile mints; original-code comparator 139/74 and 76.
- Jitter p50/p95/max 0/2/3, distance-2 mints 11; replay diverges at 23 steps, first 76/85/86.
- One place ID each for maps 38/37/0; phantom uses 0; mat 1 departure; stairs unverified to p_place_2; 2 bumped candidates refuted (2x/1x).
- Agent delta: memory -99, world +95, R1-only loop -4, total -8; G10 prohibited-word scan clear.
- Exact packet live-view acceptance: 47 passed before / 47 passed after, exit 0, 4.30 s.
- Final live: 80 rows, 142 HTTP requests, 22 world rows from S49, 80/80 settled, zero stale/pending/timed-out; runner/scorer exit 0.

## Environment and gotchas
- PY is .venv-agent/Scripts/python.exe -B; TEMP/TMP use .tmp/rcr1/tmp.
- Final live evidence: .tmp/rcr1/live-retry2.log, live-retry2-eval.log; runs/m1_rcr_r1_live_retry2/summary.json.
- Final gate logs: .tmp/rcr1/finalize-suite.log, finalize-tracks.log, finalize-places.log, finalize-exits.log.
- Corrected original-code replay uses .tmp/rcr1/before_replay.py without reverting working-tree files.
- Only five baseline checkpoints exist (0/25/50/75/100); retain the 23-step replay divergence caveat.
- The replay floor uses matched-record first kind, changing its denominator 74 to 69; it is not an independent perception floor.
- Viewer /state on 127.0.0.1:8765 returned HTTP 200 before the live run.
- The frozen runner classifies names without m1 into smoke; .tmp/rcr1/live_launcher.py routes this approved run to M1, preserving source/options/output path and caps 4815/4765/400.
- First-run ledger 3756/3708/48 to 3762/3714/48; final direct-run retry ledger 3786/3736/50 to 3928/3878/50 (total/M1/smoke); 887 requests remain under unchanged caps.
- eval_run.py does not emit S4 or S7; repeated_mistakes is not S4, and place/edge counts are not S7 transition steps.

## Deviations from plan and contracts
- R1.5/R1.6 acceptance was combined to retire callee/caller together without compatibility code.
- Root removed an obsolete Memory.view assertion and updated two old non-live track expectations; workers stayed inside owned files.
- Root modified only MASTER §8 under the user's explicit request; contracts.md is untouched, with its gate-approved proposal in R1 §9.
- R1 hunks left unstaged: agent/loop.py, tests/agent/test_loop.py, tests/agent/test_track.py; all original live-view additions and live test functions remain present.
- All 18 protected hashes match the final retry baseline .tmp/rcr1/retry2-protected-before.json; subsequent user runtime fixes are preserved and fingerprinted.
- No push occurred; MASTER and this handoff remain unstaged.

## Do not retry
- Do not tune STATIC_GATE or weaken gates to hide the replay limitations.
- Do not regenerate the golden beyond the reviewed memory-only changes.
- Do not treat missing live samples or S3's zero duplicate proxy as proof of live identity improvement.

## Open issues for R4
- Preserve R1's departure-only exit invariant and stable references when implementing the R4 packet.
- Review replay divergence, actor coverage and the record-class-dependent floor before generalizing offline measurements.
- Final live S1/S2/S7 are unmeasured: no NPCs, Pallet or inter-map transitions; map 38 has one place p_place. S3 proxy 0 over 0 tile groups is inconclusive.
- Final live repeated_mistakes is 3/80, not S4; S4 requires failed-option/alternative evidence not emitted by eval_run.py.
