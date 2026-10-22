# Executor prompt: Avatar harness round r6b (finish r6: live run, R1, R2)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`). F1 and F2 (`196cb81`) and S1 (`991f39a`) are committed; the suite is green (721 passed, 1 skipped). Finish round r6 by executing **L, R1 and R2** of `docs/plans/harness-r6.md`. Read its sections L, R1, R2 and the report format.

All standing rules of `docs/plans/chatgpt-execute-r6.md` still apply:
- explicit-path staging, one commit per code item;
- no assertion edits;
- never touch `scripts/run_agent.py`, `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt` or `docs/plans/`;
- `.venv/Scripts/python.exe`, `pytest -n auto`, `PYTHONIOENCODING=utf-8`;
- stop and report on mismatch;
- no Computer Use.

## What changed since your stop

The S1 test conflict is resolved and committed with S1:
- Two `crate_scene` tests use `crate_seen=False`: the blocker is a wall the perceiver never reports.
- Two tests were rewritten with the user's approval:
  - `…goes_around_a_known_blocker…` now asserts that the seen crate is avoided on the first try;
  - `…without_a_detour…` starts walled in, so the fallback route hits the crate first.

S1's four new `test_space.py` tests are included. Do not touch these tests again.

## Decisions (user)

- **L:** approved. One attempt, `--max-calls 400`. If it stops early, keep the data, report it, and do not re-run.
- **R1:** approved. Rebuild `scripts/points.json` from `runs/m1_r6_live_150`, using checkpoints at multiples of 5. Commit it as `R1 …`.
- **R2:** approved. Dry A3 (goal: at most 1 INCONCLUSIVE), then `--live --no-cache` on the PASS cases (at most 20 asks), then `eval_moves.py` on the new run.
- The ledger has 1522 asks of headroom (6332 / 7854). The round spends at most 420.

## Order

1. Suite: confirm green on HEAD.
2. Viewer check (start it in the background if it is down).
3. The L command block from the plan, then `eval_run.py` and `eval_moves.py`.
4. R1 (commit).
5. R2.

## Workers (Luna, high effort)

Repeat these rules in every worker prompt.
- **Workers, MECHANICAL:** the suite, the run and its evals, building the R1 cases from the recorded prompts, and the R2 runs and tables.
- **You, JUDGMENT:** case choice and equivalents, review and commit of R1, and the report.

## Report format (reply with exactly this)

**Summary (3 lines):** whether the gate passed from boot, the movement-failure change against r3b, and the regression-set status.

Then:
1. **The L success-target table** (plan section L), with measured values.
2. **The R2 dry table:** `case | step | checkpoint | status | failed checks | divergence | fallbacks`.
3. **The R2 live table:** `case | choice | right/wrong`, and the asks.
4. **The `eval_moves` class table** for `m1_r6_live_150`, next to r3b, w8 and r4.
5. **Asks per command**, and the final ledger.
6. Then list:
   - (a) files edited and commits;
   - (b) commands that made model requests, with counts;
   - (c) skipped or unverified items;
   - (d) surprises.

State failures plainly.
