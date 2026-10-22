# Executor prompt: Avatar harness round r6 (ORDERS round 6)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`, HEAD `39b1074`). Execute the plan in `docs/plans/harness-r6.md`. Read it completely first: it holds the evidence, the exact design of each item, the tests, the live command and the success targets. This prompt adds the rules, the decisions and the report format. Where this prompt and the plan differ, this prompt wins.

## Standing rules

1. **Smallest change, reuse first.**
   - F1 and F2 change only the points stub and the `--om points` path in `scripts/eval_act.py`.
   - S1 changes only `route` in `agent/space.py`, reusing `ACTOR_KINDS`, `TRANSIENT`, `_is_exit` and `normalize_kind` from `agent/memory.py`.

   No new modules, no refactors, no new gates, no removed options, no game names in `agent/`. Match the dense one-line style.
2. **Files you may edit:**
   - `scripts/eval_act.py`
   - `tests/agent/test_om_eval.py`
   - `agent/space.py`
   - `tests/agent/test_space.py`
   - `scripts/points.json`

   Nothing else. **Never touch** `scripts/run_agent.py` (user WIP; it already holds the raised caps and the 5-step checkpoints), `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, or anything under `docs/plans/`. Do not stage, edit, stash, reset, checkout or format them. Do not switch branches.
3. **Run data:** write only the new run folder `runs/m1_r6_live_150` (created by `run_agent.py`) and the ledger entries that `run_agent._ledger` writes itself. Never edit other runs or `runs/e3_budget.json` by hand. Scratch goes to `.tmp/r6/`. Copy, never link.
4. **One commit per code item**, in order: F1, F2, S1, R1. Commit message: `<id> <title from the plan table>` plus a one-line body; end with the attribution line your environment requires. Stage explicit paths only; never `git add -A`, `git add .`, or `git commit -a`.
5. **No existing assertion or golden changes.** Baseline **709 passed, 1 skipped**. If an existing test fails after your edit, stop that item and report the test, the assertion and the output. Do not edit the test. This matters most for S1: routing tests in `test_world.py`, `test_loop.py`, `test_exits.py` and `test_space.py` must stay green unchanged.
6. **Model requests:** only L (hard cap 400 asks, enforced by `--max-calls 400`) and R2 step 2 (hard cap 20 asks). Everything else refuses HTTP and prints `requests=0`.
7. **Interpreter and tests:** `.venv/Scripts/python.exe`; `.venv/Scripts/python.exe -m pytest -n auto -q` (never serial); `PYTHONIOENCODING=utf-8` for game text.
8. **Stop and report on mismatch.** If cited code differs from the plan, if importing `agent.memory` from `agent.space` creates an import cycle, or if a planned regression case's situation never occurs in the new run, report it. Do not improvise around it, except the plan's "pick the nearest equivalent" rule for R1, which you must report per case.
9. **The live run is one attempt.** If it stops early (CallCap, crash, emulator error), keep the data, report the step and the error, and do not start another run.
10. **No Computer Use.** You may start the run viewer in the background as the plan says. Never drive a GUI.

## Decisions (user)

- F1, F2, S1, L, R1, R2: **all approved.**
- Live budget: L ≤ 400 asks, R2 ≤ 20 asks. The ledger has 1522 asks of headroom (6332 / 7854).
- If S1 breaks an existing assertion, **do not** run L. Report and stop: the user decides.

## Workers (Luna, high effort)

You are the lead. Delegate to Luna workers at **high** effort and repeat these standing rules in every worker prompt.

- **Workers, MECHANICAL:**
  - the baseline suite and the per-item suites;
  - the A3 re-run after F1;
  - the `eval_moves.py` check after S1;
  - starting the live run in the background and running `eval_run.py` and `eval_moves.py` on it;
  - building the R1 cases (read the recorded act prompts and copy the labels; verify truth and checkpoints);
  - the R2 dry and live runs, and filling the tables.
- **Workers may draft** F1, F2 and S1 code and tests from the plan, one item at a time. F1 and F2 both edit `scripts/eval_act.py`: serialize them.
- **You, JUDGMENT:**
  - review every diff against the plan before committing, and make the commits;
  - judge the S1 test outcomes and any stop;
  - choose the R1 cases and any "nearest equivalent";
  - write the final report.

## Order of work

1. Baseline suite.
2. F1 (commit), then the A3 dry re-run on the r5 cases (report cases 3, 5, 6).
3. F2 (commit).
4. S1 (commit), then the `eval_moves.py` unchanged check on r3b, w8 and r4.
5. L: viewer check, the live run, `eval_run.py`, `eval_moves.py`.
6. R1 (commit), then R2.

## Report format (reply with exactly this)

**Summary (3 lines):** commits made, whether the gate passed from boot, the movement-failure change.

| item | commit sha | files changed | suite (passed / skipped / failed) | assertion edits | result | notes |
|---|---|---|---|---|---|---|
| baseline | - | - | 709 / 1 / 0 expected | - | | |
| F1 | | | | 0 | r5 cases 3, 5, 6 new status | |
| F2 | | | | 0 | test added | |
| S1 | | | | 0 | eval_moves unchanged? | |
| L | - | - | - | - | success-target table | |
| R1 | | | | 0 | 10 cases, equivalents used | |
| R2 | - | - | - | - | dry and live results | |

Then:
1. **The L success-target table** from the plan, with measured values.
2. **The R2 dry table:** `case | step | checkpoint | status | failed checks | divergence | fallbacks`.
3. **The R2 live table:** `case | choice | right/wrong`, and the asks.
4. **The `eval_moves` class table** for `m1_r6_live_150`, next to r5's three runs.
5. **Asks spent per command** and the final ledger.
6. Then list:
   - (a) files edited;
   - (b) commands that made model requests, with ask counts;
   - (c) anything skipped or unverified;
   - (d) anything surprising.

State failures plainly. Do not hedge results you verified.
