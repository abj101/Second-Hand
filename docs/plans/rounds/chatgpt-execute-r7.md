# Executor prompt: Avatar harness round r7 (ORDERS round 7)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`, HEAD `87eb424`). Execute `docs/plans/harness-r7.md`. Read it completely first: evidence, V1, V2, the G options, the decision section (G1 + G3 behind GM), LV, and the order. This prompt adds the rules, the decisions and the report format. Where this prompt and the plan differ, this prompt wins.

## Standing rules

1. **Smallest change, reuse first.** No new modules, no refactors, no new gates, no removed options, no game names in `agent/` code or prompts. Match the dense style.
2. **Files you may edit:**
   - `agent/loop.py` (V1, G1)
   - `agent/notebook.py` (G3)
   - `agent/world.py` (V3, SB, SC)
   - `agent/memory.py` (SB, SA)
   - `tests/agent/test_loop.py`
   - `tests/agent/test_notebook.py`
   - `tests/agent/test_world.py`
   - `tests/agent/test_memory.py`
   - `tests/agent/test_options.py`
   - `scripts/eval_entities.py` (new)
   - `tests/agent/test_eval_entities.py` (new)
   - `scripts/points.json`

   Nothing else. **Never touch** `scripts/run_agent.py`, `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt` or `docs/plans/`. Do not switch branches.
3. **Run data:** write only `runs/m1_r7_resume115` (copied with `cp -r`, never linked) and the ledger entries `_ledger` writes itself. Scratch, including the GM script, goes to `.tmp/r7/` and is never committed.
4. **One commit per item:** V1, V2, V3, SD, SB, SA, SC, G3, G1. Commit message: `<id> <title from the plan table>` plus a one-line body; end with the attribution line your environment requires. Stage explicit paths only; never `git add -A`, `git add .`, or `git commit -a`.
5. **Assertions.** Only two assertion changes are approved:
   - replacing `test_repeated_reply_closes_quest_for_thing_and_marks_progress` with `test_repeated_reply_does_not_close_a_targeted_quest` (V1);
   - `tests/agent/test_world.py:527`, `gone` → `stale` plus "still offered" (SB).

   A3 `label_re` updates in `scripts/points.json` are allowed only where SA renamed the same tile's thing; report each one. Any other failing existing assertion: stop that item and report it. Baseline: **721 passed, 1 skipped**.
6. **Model requests:** GM at most 20 asks; LV at most 120 (`--max-calls 120`). Everything else is 0 and prints `requests=0`.
7. **Interpreter and tests:** `.venv/Scripts/python.exe`; `-m pytest -n auto -q` (never serial); `PYTHONIOENCODING=utf-8`. `eval_moves.py` takes the run basename.
8. **Stop and report on mismatch** with the plan's cited code. LV is a single attempt: if it stops early, keep the data and report.
9. **No Computer Use.** You may start the viewer in the background if it is down.

## Decisions (user)
- V1, V2, V3, SD, SB, SA, SC, G3, GM, LV: **approved**. Follow the plan's order (14 steps).
- G1: **approved only if GM is a go** by the plan's rule. If it is a no-go, skip G1, run LV with V1 + G3 only, and report the GM replies verbatim.
- Ledger: 6570 / 7854, 1284 left. This round spends at most 140.

## Workers (Luna, high effort)
Repeat the standing rules in every worker prompt.
- **Workers, MECHANICAL:**
  - the suites;
  - the case 11 dry runs;
  - running the GM script and tabulating replies;
  - the LV copy, the run and the evals;
  - the A3 table.
- **Workers may draft** code and tests one item at a time, in plan order. Never two writers on one file:
  - `agent/loop.py`: V1, G1;
  - `agent/world.py`: V3, SB, SC;
  - `agent/memory.py`: SB, SA.
- **Workers also run SD** on r6, r3b and the LV run.
- **You, JUDGMENT:**
  - review diffs and make the commits;
  - the GM go/no-go;
  - the stops;
  - the report.

## Report format (reply with exactly this)

**Summary (3 lines):** whether the starter came in LV, the GM verdict, the regression-set status.

| item | commit sha | files | suite (passed / skipped / failed) | result | notes |
|---|---|---|---|---|---|
| V2 before | - | - | - | case 11 status on HEAD | |
| V1 | | | | | |
| V2 after | | | | case 11 status | |
| V3 | | | | | |
| SD | | | | baseline metrics r6, r3b | |
| SB | | | | | |
| SA | | | | | |
| SC | | | | | |
| G3 | | | | | |
| GM | - | - | - | lab BALL x/8, control false names y/8, asks | |
| G1 | | | | | |
| A3 (11 cases) | - | - | - | PASS / FAIL / INCONCLUSIVE | |
| LV | - | - | - | party step, asks | |

Then:
1. **GM replies:** frame, try, and the table entity name (plus any false game-word names).
2. **SD table:** the six metrics for r3b, r6 and LV (steps 116–150), side by side. Also the act prompt size before and after S (`--om act-size`), and every A3 `label_re` change with its reason.
3. **LV:** quests after step 115 (key, text, target, status, reason); the table's names; repeat-talk verdicts; first party step; asks; seconds per step.
4. **Asks per command** and the final ledger.
5. Then list:
   - (a) files edited;
   - (b) commands that made model requests, with counts;
   - (c) skipped or unverified items;
   - (d) surprises.

State failures plainly.
