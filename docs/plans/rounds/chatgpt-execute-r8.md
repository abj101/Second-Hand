# Executor prompt: Avatar harness round r8 (ORDERS round 8)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`, HEAD `d81df01`). Execute `docs/plans/harness-r8.md`. Read it completely first: the evidence, B1, V2, B5, RP, the option sections, the order, and the validity check. This prompt adds the rules, the decisions and the report format. Where this prompt and the plan differ, this prompt wins.

## Standing rules

1. **Smallest change, reuse first.** No new modules in `agent/`, no refactors, no new gates, no removed options, no game names in `agent/` code or prompts. Match the dense style.
2. **Files you may edit** (only for the items approved under Decisions):
   - `agent/loop.py` (B1)
   - `agent/predict.py` (B5)
   - `agent/memory.py` (I-A)
   - `agent/notebook.py` (Q-A)
   - `agent/space.py`, `agent/world.py` (T-A)
   - `scripts/points.json` (V2)
   - `tests/agent/test_loop.py`, `test_predict.py`, `test_memory.py`, `test_notebook.py`, `test_world.py`, `test_space.py`

   Nothing else. **Never touch** `scripts/run_agent.py`, `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt` or `docs/plans/`. Do not switch branches.
3. **Run data:** write only `runs/m1_r8_resume115` (copied with `cp -r`, never linked; no junctions) and the ledger entries `_ledger` writes itself. Scratch (RP, the T measure) goes to `.tmp/r8/` and is never committed.
4. **One commit per item:** B1, V2, B5, I, Q, T. Commit message: `<id> <title from the plan table>` plus a one-line body; end with the attribution line your environment requires. Stage explicit paths only; never `git add -A`, `git add .`, or `git commit -a`.
5. **Assertions.** Baseline: **729 passed, 1 skipped**. Only the changes listed under "Approved assertion changes" are allowed. Fixture-input repairs that keep every assertion unchanged are allowed; report each one. Any other failing existing assertion: stop that item and report it.
6. **Model requests:** LV at most 120 (`--max-calls 120`). Everything else is 0 and prints `requests=0`.
7. **Interpreter and tests:** `.venv/Scripts/python.exe`; `-m pytest -n auto -q` (never serial); `PYTHONIOENCODING=utf-8`. `eval_moves.py` takes the run basename.
8. **Stop and report on mismatch** with the plan's cited code. LV is a single attempt: if it stops early, keep the data and report.
9. **No Computer Use.** You may start the viewer in the background if it is down.

## Decisions (user)
- **B1, V2, RP:** approved.
- **B5:** approved, including its assertion change.
- **I: I-A** (class fixed at mint).
- **Q: Q-A** (a reply never closes a quest; the planner closes it).
- **T: T-A** (void cells from pixels), only if its 0-ask measure is a go by the plan's rule. On no-go, report and skip the code.
- **V:** no change beyond B5 this round.
- **G:** not approved this round. No GM2, no G1o, no prompt changes.
- **R:** not approved this round. No `eval_act.py` changes.
- **LV:** approved after the items above land.
- **Ledger:** 6666 / 7854, 1188 left. This round spends at most 120 (LV only).

## Approved assertion changes
- B5: `tests/agent/test_predict.py:30`, `"progress"` → `"changed"`, plus the new line `classify("failed", ["view_scrolled"], True) == "changed"`.
- Q-A: `tests/agent/test_notebook.py:148`, `:193` and `:352`, `reason == "talked"` → the quest stays open and the text is recorded with `who`.
- A3 `label_re` updates in `scripts/points.json`, allowed only where I-A changed the shown name of the same tile's thing. Report each one.

## Workers (Luna, high effort)
Repeat the standing rules in every worker prompt.
- **Workers, MECHANICAL:**
  - the suites;
  - the V2 dry runs;
  - RP and the T measure (scripts in `.tmp/r8/`);
  - the LV copy, the run and the evals;
  - the A3 table.
- **Workers may draft** code and tests one item at a time, in plan order. Never two writers on one file:
  - `agent/loop.py`: B1;
  - `agent/memory.py`: I-A;
  - `agent/world.py` / `agent/space.py`: T-A.
- **You, JUDGMENT:**
  - review diffs and make the commits;
  - the RP sanity check;
  - the T go/no-go;
  - the stops;
  - the report.

## Report format (reply with exactly this)

**Summary (3 lines):** whether the starter came in LV, the I and T verdicts, the regression-set status.

| item | commit sha | files | suite (passed / skipped / failed) | result | notes |
|---|---|---|---|---|---|
| V2 before | - | - | - | case 11 status on HEAD | |
| B1 | | | | | |
| V2 after | | | | case 11 status | |
| B5 | | | | | |
| RP baseline | - | - | - | SD + class-change ids, LV and r6 | |
| I | | | | RP after | |
| Q | | | | | |
| T measure | - | - | - | void share of blocked cells, false void on walked cells | |
| T | | | | | |
| A3 (all cases) | - | - | - | PASS / FAIL / INCONCLUSIVE | |
| LV | - | - | - | party step, asks | |

Then:
1. **RP table:** before and after I, for LV and r6 (116–150).
2. **T measure:** per blocked row, the cell's class (void / not void), plus the false-void count on RAM-walked cells.
3. **LV:**
   - quests after 115 (key, text, target, status, reason);
   - the ball-table and ball rows' names and kinds;
   - class-changing ids;
   - adjacent-actor "not on screen" labels;
   - blocked rows with verdict `progress`;
   - stuck replans;
   - first party step;
   - asks;
   - seconds per step;
   - act-size at LV checkpoint 140 (`--om act-size --run m1_r8_resume115 --from-step 140`).
4. **Asks per command** and the final ledger.
5. Then list:
   - (a) files edited;
   - (b) commands that made model requests, with counts;
   - (c) skipped or unverified items;
   - (d) surprises.

State failures plainly.
