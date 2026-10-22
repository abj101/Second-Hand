# Executor prompt: Avatar harness round r5 (ORDERS round 5: A regression set, B movement audit)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`, HEAD `1a663de`). Execute the plan in `docs/plans/harness-r5-ab.md`. Read it completely first: it holds the measured evidence, the design of each item, the case table and the checks. This prompt adds the rules, the decisions and the report format. Where this prompt and the plan differ, this prompt wins.

**This round is evaluation and audit only.** You change no file under `agent/`. Any harness fix that B suggests is a proposal in your report, not code.

## Standing rules

1. **Smallest change, reuse first.** A1 is a new mode inside `scripts/eval_act.py`, reusing `om_reliability`'s restore, stub, HTTP-guard and act-capture code, plus `is_right` / `hits` and the `om_cases.json` case shape. B1 is one new script `scripts/eval_moves.py`. No new modules beyond these, no frameworks, no refactors of existing modes. Match the file's dense style.
2. **Files you may create or edit:**
   - `scripts/eval_act.py`
   - `scripts/points.json` (new)
   - `scripts/eval_moves.py` (new)
   - `tests/agent/test_om_eval.py`
   - `tests/agent/test_eval_moves.py` (new)

   Nothing else. **Never touch** `agent/`, `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, or anything under `docs/plans/`. Do not stage, edit, stash, reset, checkout or format them. Do not switch branches.
3. **Run data is read-only.** Never write under `runs/`. Scratch work goes to `.tmp/` (the replay already uses `.tmp/om`, `_hf5_path`). Copy, never link (no junctions or symlinks to `runs/` or `Rom/`).
4. **One commit per item**, in order: A1 (with its tests), A2, B1 (with its test). A3, B2 and B3 are report content, not commits. Commit message: `<id> <title from the plan table>` plus a one-line body; end with the attribution line your environment requires. Stage explicit paths only; never `git add -A`, `git add .`, or `git commit -a`.
5. **No existing assertion or golden changes.** The suite must stay green: baseline **701 passed, 1 skipped**, plus your new tests. If an existing test fails, stop and report.
6. **Zero model requests** except A4 (approved below, at most 20 asks). Every replay must refuse HTTP as `om_reliability` does, and print `requests=0; ledger unchanged=True`. Never edit `runs/e3_budget.json` or the caps.
7. **Interpreter and tests:** `.venv/Scripts/python.exe`; `.venv/Scripts/python.exe -m pytest -n auto -q` (never serial); `PYTHONIOENCODING=utf-8` for anything that prints game text.
8. **Stop and report on mismatch.** If the code at a cited line is not what the plan describes, or a planned case's step or label does not exist in the recorded data, report what you found. You may move a case by ±2 steps as the plan allows, but report every move. Do not invent other cases.
9. **No Computer Use, no viewer, no GUI.** Manual checks never block.
10. **Do not change the plan.** Put disagreements in the report's notes.

## Decisions (user)

- A1, A2, A3, B1, B2, B3: **approved** (all zero-ask).
- Ledger caps were **raised by the user** to `TOTAL_CAP, M1_CAP = 7854, 7804` (1522 asks of headroom).
- A4 (live act/plan scoring, about 12 asks): **approved**. Implement `--live` on top of the dry path and run it once after A3, with every ask going through `scripts/run_agent._ledger` exactly as the r4 micro-check did. Report each case's choice and the asks spent. Hard cap: 20 asks.
- D1: **done by the user.** `scripts/run_agent.py` now checkpoints every 5 steps (`% 5`), so future runs have a checkpoint every 5 steps. The three recorded runs still have them every 25, so the case table keeps its checkpoints. You do nothing for D1.
- `scripts/run_agent.py` still holds the user's uncommitted WIP: never edit, stage or commit it.
- B3: proposals only. **No changes to `agent/`**, even ones that look trivial.

## Workers (Luna, high effort)

You are the lead. Delegate to Luna workers at **high** effort and repeat these standing rules in every worker prompt.

- **Workers, MECHANICAL:**
  - Building `scripts/points.json` from the recorded data. For each planned case, read the recorded act prompt at that step in `runs/<run>/calls.jsonl` (`role == "act"`) and copy the exact option labels into `label_re` patterns. Confirm `truth.jsonl` map/x/y at that step. Confirm the checkpoint exists.
  - Running the suite, the A3 dry replay and B1 on the three runs, and filling the tables.
  - Drafting B1 and its test from the plan's spec.
- **Workers may draft A1** from the plan's spec. A1 and the A2 file edits are serialized: never two writers on one file. B1 is a different file and may run in parallel with A.
- **You, JUDGMENT:**
  - review every diff against the plan, and make the commits;
  - decide INCONCLUSIVE reasons and case moves;
  - write B2's ranking and B3's proposals and counterfactuals yourself;
  - write the final report.

## Order of work

1. Baseline suite.
2. A1, then A2, then A3 (the dry replay on HEAD), then A4 (`--live`, at most 20 asks).
3. In parallel or after: B1, then B2 on r3b (1–150), w8 (1–150) and r4 (101–147), then B3.
4. Final report.

## Report format (reply with exactly this)

**Summary (3 lines):** commits made, what passed, the headline finding of B.

| item | commit sha | files changed | suite (passed / skipped / failed) | result | notes |
|---|---|---|---|---|---|
| baseline | - | - | 701 / 1 / 0 expected | - | |
| A1 | | | | tests added, dry mode works | |
| A2 | | | | number of cases, moves made | |
| A3 | - | - | - | PASS / FAIL / INCONCLUSIVE counts | |
| A4 | - | - | - | live choice right / wrong per case, asks spent | |
| B1 | | | | test added | |
| B2 | - | - | - | class table (below) | |
| B3 | - | - | - | proposals (below) | |

Then:
1. **A3 table:** `case | run step | status | failed checks | first divergence | plan_extra`, and for each INCONCLUSIVE, the reason.
2. **B2 tables:**
   - classes × runs (failed rows, wasted presses);
   - target kind × class;
   - the town streak 57–67 row by row.
3. **B3:** for each proposal, the class it targets, the change in one or two sentences (generic, no game names), the files it would touch, and the counterfactual "would have avoided X of Y failed rows", with how you computed it.
4. Then list:
   - (a) every file edited;
   - (b) every command that made a model request, with its ask count (expected: only A4);
   - (c) anything skipped or unverified;
   - (d) anything surprising.

State failures plainly. Do not hedge results you verified.
