# Executor brief: Avatar harness round r10 (identity from pixels, names from evidence)

You are a **new, separate Sonnet 5.5 (medium effort) session**. You **orchestrate Haiku 5.5 (medium effort) subagents** (`model: haiku`, `effort: medium`) for the mechanical work, and you do the judgment and the code yourself.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `9081817`.
- **Plan:** execute `docs/plans/harness-r10.md`. Read it completely first: the evidence, every item's spec, the gates, AP's go rules, the order and the validity check. Read `CLAUDE.md` as well.
- **Precedence:** where this brief and the plan differ, this brief wins.
- **Planner:** the planner is the Opus session that started you. Message it with `SendMessage` (its id is in your "Linked sessions" note) for every stop and for the final report, and wait for its answer on stops.

## Standing rules (repeat them in every subagent prompt)

1. **Project tooling** (mandatory, from `CLAUDE.md`):
   - the `ponytail` skill (full): reuse > stdlib > minimal code;
   - `codegraph_explore` before grep or Read;
   - `headroom_compress` on large outputs.
2. **Smallest change, reuse first.**
   - No new modules in `agent/` and no refactors.
   - No new gates on options or actions. B3 changes status only.
   - No removed options, and no game names or game words in `agent/` code or prompts.
   - Match the dense one-line style of the touched code. Add a `ponytail:` comment where the plan asks for one.
3. **Files you may edit:**
   - `agent/memory.py`, `agent/world.py`, `agent/loop.py`, `agent/perceive.py`, `agent/model.py`, `agent/act.py`, `agent/plan.py`, `agent/notebook.py`;
   - `scripts/eval_identity.py` (new);
   - `scripts/points.json` (only the `label_re` updates N3 allows);
   - `tests/agent/test_memory.py`, `test_world.py`, `test_loop.py`, `test_perceive.py`, `test_model.py`, `test_act.py`, `test_plan.py`, `test_notebook.py`, `test_options.py`, `test_eval_identity.py` (new).

   **Never touch** `scripts/run_agent.py`, `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, or anything under `docs/plans/`. Do not stage, stash, reset, checkout or format them. Do not switch branches.
4. **Assertions.** Baseline: **735 passed, 1 skipped** (verify it first). No existing assertion may change, except:
   - the pre-approved N2 change: adding `"name"` to a pinned op enum or REPLY string;
   - the N3 `label_re` updates for a `(or: …)` suffix.

   Fixture-input repairs that keep every assertion unchanged need the planner's OK first. Any other failing existing assertion: stop that part, revert it, and message the planner.
5. **Run data.**
   - Write only `runs/m1_r10_boot` (created by `run_agent.py`) and the ledger entries `_ledger` writes itself.
   - Never edit other runs or `runs/e3_budget.json`.
   - Scratch goes to `.tmp/r10/` and is never committed. You may copy the r9 scripts from `.tmp/r9/` and the GM template from `.tmp/r7/gm_check.py`.
   - Work in this checkout. No worktree, and never use junctions or links for `runs/` or `Rom/`.
6. **Commits.**
   - One commit per part: `M`, `R0`, `B1`, `B2`, `B3`, `N1`, `N2`, `N3`, `A`, `P`.
   - Message: `<id> <title from the plan table>` plus a one-line body, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
   - Stage explicit paths only; never `git add -A`, `git add .` or `git commit -a`. Never push.
   - A part that fails its gate is reverted (uncommitted edits discarded, or `git revert` if already committed), and you continue with the next part.
7. **Tests and interpreter.**
   - Interpreter: `.venv/Scripts/python.exe`. Set `PYTHONIOENCODING=utf-8`.
   - Suite: `-m pytest -n auto -q`, never serial.
   - Scripts take bare run names (`m1_r8_resume115`, not `runs/...`).
   - Run anything over about 2 minutes in the background.
8. **Model requests.**
   - Only AP (cap **40**) and LV10 (cap **400**, via `--max-calls 400`) may make requests.
   - AP charges every post through `scripts.run_agent._ledger`, as `gm_check.py` does.
   - Everything else prints `requests=0` or leaves the ledger unchanged. Ledger now: 6751 / 7854.
9. **Stop and report** when the code at a cited line differs from the plan's validity check. LV10 is a single attempt: if it stops early, keep the data and report.

## Gates (from the plan; you judge them)
- **Main gate** for R0, B1, B2 and B3 (with `scripts/eval_identity.py`; r8 116–146, r7 116–150, r6 116–150):
  - static-sprite switches on r8 and r7 at most half of HEAD's;
  - character switches no worse than HEAD;
  - no-tile world mints = 0;
  - object mints within 2 tiles at most half of HEAD's.
- **Per part:** no metric worse than the previous committed part, else revert that part.
- **The main gate is judged after B3,** on all of R0 + B1 + B2 + B3 together. Each part only has to pass the per-part rule.
- **AP:** the plan's go rules for A and for P, separately.
- **LV10** runs only if R0 and B2 are committed and the main gate passed after B3. Otherwise, report and stop.

## Worker split

**Haiku 5.5 (medium) subagents: mechanical only.** Give each one a self-contained prompt with the standing rules and exact commands. Never let two writers touch one file.

1. **Baseline:** the suite count, and `eval_identity.py` on r8, r7, r6 and r3b once M is committed. Return the table.
2. **After each part:** the suite, `eval_identity.py` on the three runs, and `scripts/eval_plan.py --run runs/m1_r8_resume115 --check all` after N2. Return the numbers as a table.
3. **PATCH_MATCH calibration (B2):** run your calibration scratch script on the r8 replay and return the score table. You choose the value.
4. **AP:** run your AP scratch script (you write it, from `gm_check.py`). Return the per-frame, per-arm, per-try table with the five metrics and the asks used.
5. **A3:** `scripts/eval_act.py --om points --dry`. Return the table: PASS / FAIL / INCONCLUSIVE per case.
6. **LV10:**
   - check the viewer (`curl -s http://127.0.0.1:8765/state`); if it is down, start `python scripts/watch_run.py --no-browser` in the background;
   - run the plan's command in the background;
   - then `eval_run`, `eval_moves` and `eval_entities` on `m1_r10_boot`, and `eval_identity.py m1_r10_boot` over the lab window (truth `map == 40`).

**You (Sonnet): judgment.**
- Write all code and tests yourself, one part at a time, in plan order.
- Review every worker result.
- Decide each gate and revert, make the commits, handle the stops, and write the report.

## Report (message it to the planner when done or stopped; exactly this shape)

**Summary (3 lines):** whether LV10 got a party from boot (truth step), the gate verdicts (main, AP-A, AP-P), and the suite.

| part | commit | suite (p/s/f) | static switches r8 / r7 / r6 | character switches r8 / r7 / r6 | no-tile world mints | object mints ≤2 tiles | notes |
|---|---|---|---|---|---|---|---|
| HEAD | `9081817` | | | | | | |
| M | | | | | | | |
| R0 | | | | | | | |
| B1 | | | | | | | |
| B2 | | | | | | | `PATCH_MATCH` = |
| B3 | | | | | | | |
| N1 | | | - | - | - | - | |
| N2 | | | - | - | - | - | eval_plan |
| N3 | | | - | - | - | - | A3 label_re updates |
| A | | | | | | | |
| P | | | - | - | - | - | |

Then:
1. **AP table:** the five metrics per arm, the go verdicts, and the asks.
2. **A3 table.**
3. **LV10:**
   - first party step (truth);
   - `m1_gate_pass`;
   - places reached, with the step each was first entered;
   - asks and seconds per step;
   - `eval_identity` per class;
   - the ball rows' names, `called`, `called_src`;
   - the planner's `name` ops;
   - marks drawn and used per lab step;
   - failed-row classes from `eval_moves`.
4. Ledger before and after.
5. Then list:
   - (a) files edited;
   - (b) commands that made requests, with counts;
   - (c) reverted parts or skipped steps;
   - (d) surprises.

State failures plainly.
