# Handoff notes

Last written: 2026-10-24. Round r18-fix complete; G18F passed; LV18b ran exactly once.

## Code

- Branch feat/lean-agent; base f283d2e. Production merge HEAD 443f4f3. Nothing pushed.
- F1 42953c3 + orchestrator review 5df4086, merged --no-ff as 5d8c809: no-response presses reach space.fold, restoring silent/unconfirmed walls.
- F2 76a9bc3 + orchestrator review 2a4dc11, merged --no-ff as 443f4f3: hold post-cut segments until world-place identification; resolve arrival from located minus held motion; one crossing and reciprocal exits.
- Held state survives JSON checkpoints without frames/ndarrays. unseen_cross skips while held. Same-place identification flushes held motion into its source.
- loop.py, match_place, space.mark_exit, and existing assertions unchanged. No new option filters or game logic.
- Approved contract amendment/plan: 1e57374. Corrected fixture brief: 6bc2118 (1R,11D,1U onto door; cut;8U; source UP, destination DOWN).

## Validation

- Baseline 885 passed, 1 skipped. Final merged G18F: 892 passed, 1 skipped (369.12s).
- T18F: 25 PASS, zero model requests, actual pixel split and checkpoint replay. Script .tmp/r18fix/g/t18f.py.
- T18-O2 unchanged: waited600, late26, pos(39,0). T18-O3 unchanged:85/85,77 exclusions,4 map flips.
- Seven new tests. No golden differences, regeneration, or existing assertion changes.

## LV18b

- runs/m1_r18b_odo copied from m1_r17_boot; resume65, steps66–125, max-calls200, one invocation only.
- 60/60 unique steps;175 call rows;0 errors. Ledger10033→10132 (99 new requests), below10233.
- Source originals unchanged. Viewer was up.
- Entry crossing p_place_4@7,5>p_place_5 n1 restored; source UP, lab return DOWN.
- Lab offset(5,11) stable over21 eligible world steps. Return tile(0,0) maps RAM door(5,11).
- Step69 identifies lab immediately: player(0,-8), entrance(0,0). Deferred fixture instead yields player(0,0), entrance(0,8). Live sample did not exercise held branch.
- Source step69 has16 unique pre-cut tiles; erroneous continuation(7,4)…(7,-3) absent.
- Walls restored:13 rows/11 tiles vs LV18 zero. No-response18 vs LV18 17 vs r17 1; longest streak15 (111–125).
- Odometer:30856 frames,1988 uncertain,4 cuts,0 gap,0 dropped.
- Agent did not leave lab within first15 free world steps (88–118), or by125. Bottom-return option selected88/90 but DOWN no-response/dialogue blocked routes. Later fake top exit(0,-11)=RAM(5,0) attracts repeated attempts; lab naming/goal interpretation remains unstable.

## Next

- Scope remains the two approved fixes. Do not claim navigation improvement or deferred live validation from LV18b.
- Any further live run needs new authorization; do not exceed ledger10233 under this round.
- Full report and gate logs: .tmp/r18fix/report.md; live analysis: .tmp/r18fix/lv18b/{a_out,c_out,choices}.txt.
- User changes to eval_frames.py, watch_run.py, test_watch_run.py, baseline_eval.txt, frames_combo.json remain untouched.