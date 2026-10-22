# Executor prompt: Avatar harness round r3b (ORDERS round 3b)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`). R1, R3, R4 and R5 of round r3 are committed. Execute the section **"Round r3b"** at the end of `docs/plans/harness-fixes-r3.md` (read the whole file once for context; r3b holds the exact edits, the prompt test script and the gates). The standing rules of `docs/plans/chatgpt-execute-r3.md` all still apply (smallest change, one commit per item, explicit-path staging, never `git add -A`, never touch `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json` or `docs/plans/`, stop and report on mismatch, no assertion edits except the one R1b lists, `.venv/Scripts/python.exe`, `pytest -n auto`, no Computer Use, manual checks never block).

## Decisions

- R2' (corrected rule, the test from `.tmp/r3-r2.patch` unchanged): **approved**
- R1b prompt test (12 asks, charged to the ledger by the script): **approved**
- R1b variant A: **approved**. Variant A+ (mentions the labels END, ED, OK): **NOT approved unless the user says so**; if A fails, report and stop.
- Live resume from checkpoint 100 into `runs/m1_r3_resume100` (about 105-130 asks): **approved, only if R1b passed and is committed**
- Full 150-step run: only if the user says the ledger caps were raised (the user edits `scripts/run_agent.py`; you never do)

## Workers (Luna, high effort)

You are the lead; delegate to Luna workers at **high** effort and repeat the standing rules in every worker prompt.
- Workers (MECHANICAL): applying R2' and re-running the suite; running the R1b prompt test script and collecting replies; the resume run (`cp -r runs/m1_w8_live_150 runs/m1_r3_resume100`, never a junction; then the plan's `run_agent.py --resume --from-step 100 --steps 50 --max-calls 140 --decide` and `eval_run.py`), and filling the success-target table.
- You (JUDGMENT): review each diff against the plan, commit, pick the R1b variant by the plan's go rule, every stop, the final report.

## Order

1. R2': apply the one-line change and the preserved test, full suite (must be all green with zero assertion edits; the provenance test passes in the repo), offline trace rule check (expect 0 firings on `m1_h1h4_live`, steps 118-150 on `m1_w8_live_150`), commit `R2 identical repeated done action counts as did nothing`.
2. R1b: run the 12-ask prompt test; apply the go rule; commit the passing variant with the updated R1 test; or stop and report.
3. Live resume (only if step 2 committed); eval; success-target table, including: nickname prompt answer, longest keyboard `cancel` run, any `A` press on a keyboard screen other than confirming the end key, names in truth.

## Report format

**Summary (3 lines).** Then the same table as in `chatgpt-execute-r3.md` with rows R2', R1b-test, R1b, live run; the verbatim R1b replies (12 lines, `variant step reply`); the success-target table with measured values; asks spent and final ledger; (a) files edited, (b) commands that made model requests and their ask counts, (c) skipped items, (d) surprises. State failures plainly.
