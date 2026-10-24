# Handoff notes

Last written: 2026-10-24. Round r18 gates passed; one LV18 completed with unresolved place/exit behavior.

## State of the code

- Branch feat/lean-agent. O1 b8eda66, O2 8dd5511, O3 829a4b0 merged with --no-ff; approved golden 7275b10.
- Odometer is the source of camera motion for position. Env.listen covers every emulated frame, including model think time; World folds measured segments.
- World gates folds by perceived screen; cuts retain source exit tile and last destination segment. Trace has frames, px, uncertain, cuts, gap and dropped.
- O4 uses measured press-start _tile for failed_push and removes write-only _odo_segments. The legacy failed-push fixture now emits real RIGHT/LEFT frames; assertions unchanged.
- Code HEAD 6d12a4f; O4 d415d1e merged --no-ff. G18b passed; LV18 completed once. No push; clean lane worktrees retained.

## Decisions in force

- Follow docs/plans/contracts.md r18 and execute-r18.md user amendments; never infer motion from nonzero skill shifts.
- Public segment start/end expose existing inclusive bounds. pixels_since is bounded/non-consuming under the same lock.
- A camera-observed step onto a warp tile belongs to the departure segment; that tile is the exit, including a cut on the final route step.
- RAM is gate-only. On map flip, xy is SOURCE warp-tile coordinates; destination arrival writes later. Never subtract coordinates across maps.
- Keep after_cut and Settle scroll/last_dir/after_scroll while their assertions remain. Tracker object-scroll and live catch-up semantics still have readers.

## Measured numbers

- Baseline 860 passed/1 skipped; O1 suite 871/1; O2 and G18a 885/1.
- G18a: mean 1.227 ms/frame on 1570 consecutive frames, p95 7.231ms, max 80.421 ms. Perf threshold mean<=3ms only.
- G18a truth gates: boot 85/85 and phase_e 109/109, both 100%; step 69=(+1,+10), then(0,-8).
- G18a scripted cap: waited 600, settled False, late 26, pos(39,0), zero model calls.
- G18b: 885 passed/1 skipped, 179.44s; mean 1.242 ms/frame,p95 7.263ms,max 80.680 ms; O2 exact and both truth runs 100%.
- LV18 steps66-125,exit0,106 requests; ledger 9927->10033. Trace: 32186 frames, 1710 uncertain, 5 cuts, 0 dropped, no gaps.
- LV18 offsets constant within each place/map interval, but p_place_4 spans two maps/offsets; strict place-ID visit check fails. No recorded new-room entrance; no departure after 25 world steps.
- Committed production diff from 24712d6: agent +259, scripts +149, combined+408 lines; less-code goal unmet.

## Environment and gotchas

- Python .venv/Scripts/python.exe, PYTHONIOENCODING=utf-8; pytest always -n auto. Worktrees need absolute POKERED_ROM, no Rom/runs links.
- Frozen user paths: scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, scripts/watch_run.py, tests/agent/test_watch_run.py. Never stage them.
- runs/e3_budget.json is runner accounting only; never stage it. Ledger 10033 after the single LV18 (106/200 requests; ceiling 10227).
- Viewer 127.0.0.1:8765 must be up before LV18. Never resume/truncate m1_r17_boot itself.

## Deviations from plan and contracts

- Perf inputs were corrected from per-step snapshots to consecutive lag1 frames; no scroll optimization.
- User approved narrow frame-backed fixtures and exit-tile assertions, then the final-settle chronology correction. Reports contain before/after evidence.
- O4 hit an unchanged legacy fixture, stopped/reverted, then resumed under explicit fixture-only approval. All O4 assertions remain byte-identical.
- Proposed contract clarification: document approved pixels_since API and update superseded World signature comments. contracts.md was not edited by the orchestrator.
- Report .tmp/r18/report.md includes all gates, failure history, candidate grep proofs and honest per-file net source counts.

## Do not retry

- Do not use per-step PNGs as a per-frame perf sample; do not apply the reverted batched-scroll attempt.
- Do not reintroduce the rejected scratch odometer candidate: it depended on drain cadence. All 36 residuals were RAM truth accounting.
- Do not fold skill shifts as a fallback or change unrelated assertions. Do not push, force-remove worktrees, or run a second LV18.

## Open issues for r18

- Fixed-camera sprite motion remains out of scope; dominant-layer parallax and camera lag are known ceilings.
- Animated screens can hit wait_settled's 600-frame cap; motion is retained, but the screen may never become quiet.
- LV18 reused p_place_4 across the dialogue map change, then identified p_place_5 at 88; its offset(5,3) stayed constant through 125, but entrance/exit identity and failed navigation remain unresolved.
- The less-code goal is not met: net +408 production lines; exact per-file counts and reader/grep proofs are in the report.