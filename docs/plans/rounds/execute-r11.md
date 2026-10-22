# Executor brief: Avatar harness round r11 (an exit is a hypothesis until it is crossed)

You are a **new, separate Sonnet 5.5 (medium effort) session**. You **orchestrate Haiku 5.5 (medium effort) subagents** (`model: haiku`, `effort: medium`) for the mechanical work, and you do the judgment and the code yourself.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `8136df2`.
- **Plan:** execute `docs/plans/harness-r11.md`. Read it completely first: the decisions, the evidence, every item's spec, the gates, the order and the validity check. Read `CLAUDE.md` as well.
- **Precedence:** where this brief and the plan differ, this brief wins.
- **Planner:** the Opus session that started you. Message it with `SendMessage` (its id is in your "Linked sessions" note) for every stop and for the final report, and wait for its answer on stops.

## Decisions (filled in by the planner before you start)

| id | pick |
|---|---|
| D1 push order | **R-b′** (amended): the placement's wall, then directions whose neighbour holds an exit record, then directions whose neighbour is not walked floor (ordered by the centroid guess), then walked floor last |
| D2 placement storage | **a**: the latest wall value seen; `middle` and edges are ignored |
| D3 repeated failing option | **O1** (amended 2026-10-22; replaces E6): one exit row per push direction, the act model picks |
| D4 `_refuted` outward term, and the one assertion change | **a**: refuted only when no push direction is left; the `test_exits.py:144-148` change (block all four directions, still `== 1`) is approved |
| D5 `K` | **2** |
| D6 TC audit | **include** (0 asks, report only) |

**If any row still says _pending_, stop and message the planner. Do not start.**

## Standing rules (repeat them in every subagent prompt)

1. **Project tooling** (mandatory, from `CLAUDE.md`):
   - the `ponytail` skill (full): reuse > stdlib > minimal code;
   - `codegraph_explore` before grep or Read;
   - `headroom_compress` on large outputs.
2. **Smallest change, reuse first.**
   - No new modules in `agent/` and no refactors. New functions: `push_dirs` and `push_failed` in `agent/space.py`, plus one World method for E3's call site. Nothing else.
   - No new gates on options or actions, and no removed or hidden options. E6 is notes only.
   - No game names or game words in `agent/` code or prompts.
   - Match the dense one-line style of the touched code.
3. **Files you may edit:**
   - `agent/space.py`, `agent/world.py`, `agent/loop.py`, `agent/memory.py` (**not** `agent/skills.py`: E3 records the caller's push); `agent/act.py` (F1's D clause only);
   - `scripts/points.json` (only the `label_re` updates O1 allows);
   - `tests/agent/test_space.py`, `test_exits.py`, `test_world.py`, `test_loop.py`, `test_skills.py`, `test_memory.py`, `test_options.py`, `test_notebook.py`.

   **Never touch** `scripts/run_agent.py`, `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, or anything under `docs/plans/`. Do not stage, stash, reset, checkout or format them. Do not switch branches.
4. **Assertions.** Baseline: **740 passed, 1 skipped** (verify it first). No existing assertion may change, except:
   - `tests/agent/test_exits.py:144-148`, only if D4 = a, and only as the plan says (block all four directions, still `== 1`);
   - O1's pre-approved changes, exactly as listed in the plan's O1 "Assertions" item:
     - the exit-row label/count/order/numbering class;
     - `test_loop.py:1442-1443`, replaced as specified;
     - the golden act trace, regenerated via `W0_REGEN` within the stated diff limits (that fixture may be written for this purpose only).

     Report each with before/after;
   - O1's `label_re` updates in `scripts/points.json` for those exit rows.

   Fixture-input repairs that keep every assertion unchanged need the planner's OK first. Any other failing existing assertion: stop that part, revert it, and message the planner.
5. **Run data.**
   - Write only `runs/m1_r11_boot` (created by `run_agent.py`) and the ledger entries `_ledger` writes itself.
   - Never edit other runs or `runs/e3_budget.json`. E0 copies checkpoint memory into `.tmp/r11/` and works on the copy.
   - Scratch goes to `.tmp/r11/` and is never committed.
   - Work in this checkout. No worktree, and never use junctions or links for `runs/` or `Rom/`.
6. **Commits.**
   - One commit per part: `E1` through `Q1` (all done), then `F1`. New tests for F1 may also go in `tests/agent/test_act.py`.
   - Message: `<id> <title from the plan table>` plus a one-line body, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
   - Stage explicit paths only; never `git add -A`, `git add .` or `git commit -a`. Never push.
   - A part that fails its gate is reverted (uncommitted edits discarded, or `git revert` if already committed), and you continue with the next part. The exception: if E3, O1 or MC fails, stop before LV11 and report.
7. **Tests and interpreter.**
   - Interpreter: `.venv/Scripts/python.exe`. Set `PYTHONIOENCODING=utf-8`.
   - Suite: `-m pytest -n auto -q`, never serial.
   - Scripts take bare run names (`m1_r8_resume115`, not `runs/...`).
   - Run anything over about 2 minutes in the background.
8. **Model requests.**
   - Only MC (cap **20**; done, 15 used), MC2 (cap **15**) and LV11 (cap **400**, via `--max-calls 400`) may make requests. MC and MC2 charge every post through `scripts.run_agent._ledger`, as `.tmp/r7/gm_check.py` does.
   - Everything else prints `requests=0` or leaves the ledger unchanged. Ledger now: 6998 / 7854.
9. **LV11 is one attempt.**
   - If the process ends early for any reason (a crash, a cap, a stop from the planner or the user), keep the data and report.
   - **Never resume it from a checkpoint and never start a second run.** In r10 a resume after a stop cost about 137 unplanned asks.
10. **Keep the report current.** After each part, append its row to `.tmp/r11/report.md`, so a session that runs out of usage still leaves the numbers.
11. **Stop and report** when the code at a cited line differs from the plan's validity check.

## Gates (from the plan; you judge them)
- **G-rank (E2):** passed (pooled R-b′).
- **G-door (after O1), options level:** the four checks in the plan's E0 section. The old "DOWN within 2 pushes" limit is withdrawn.
- **E3, E4, E5, Q1:** the suite, plus each item's tests. Q1 also needs `scripts/eval_plan.py --run runs/m1_r8_resume115 --check ops`.
- **O1:** G-door, plus A3 (`scripts/eval_act.py --om points --dry`) with no new FAIL other than an O1 exit-row `label_re` update.
- **MC:** done, no-go.
- **F1:** the suite, G-door checks 1–4, and A3 with no FAIL. The approved changes are the ones listed in the plan's F1 "Assertions" item. `agent/act.py` may be edited for the D clause only.
- **MC2:** the plan's go rule.
- **LV11** runs only if E3, O1 and F1 are committed, G-door passed, and MC2 said go.

## Worker split

**Haiku 5.5 (medium) subagents: mechanical only.** Give each one a self-contained prompt with the standing rules and exact commands. Never let two writers touch one file.

1. **Baseline:**
   - the suite count;
   - your E0 scripts at HEAD (push ranks, r10 door replay);
   - A3 dry.

   Return the tables.
2. **After each part:** the suite; E0 after E2 and E3; A3 after E6; `eval_plan --check ops` after Q1. Return the numbers as a table.
3. **TC:** run your `.tmp/r11/cuts.py` over the plan's runs. Return the per-run summary.
4. **LV11:**
   - check the viewer (`curl -s http://127.0.0.1:8765/state`); if it is down, start `python scripts/watch_run.py --no-browser` in the background;
   - run the plan's command in the background;
   - then `eval_run`, `eval_moves` and `eval_entities` on `m1_r11_boot`, plus a dump of every `pushed` tile row and every exit with status `refuted`.

**You (Sonnet): judgment.**
- Write the E0, TC and code changes and tests yourself, one part at a time, in plan order.
- Review every worker result.
- Decide each gate and revert, make the commits, handle the stops, and write the report.

## Report (message it to the planner when done or stopped; exactly this shape)

**Summary (3 lines):** whether LV11 got a party from boot (truth step), the gate verdicts (G-rank, G-door), and the suite.

| part | commit | suite (p/s/f) | rank-1 share old / new (r6, r10, r8, r7) | mean rank old / new | r10 door pushes | notes |
|---|---|---|---|---|---|---|
| HEAD | `8136df2` | | | | | |
| E1 | | | - | - | - | |
| E2 | | | | | | |
| E3 | | | | | | |
| E4 | | | - | - | | assertion change |
| E5 | | | - | - | - | |
| O1 | | | - | - | G-door 1–4 | A3; changed assertions |
| Q1 | | | - | - | - | eval_plan |
| MC | - | - | - | - | - | per case: tries picking the truth row; asks |

Then:
1. **TC summary:** cuts with no direction, duplicate places by truth map, exits written with no truth door, per run.
2. **A3 table.**
3. **LV11:**
   - first party step (truth);
   - `m1_gate_pass`;
   - places reached, with the step each was first entered;
   - asks and seconds per step;
   - every crossing attempt with its push sequence;
   - the `pushed` rows and refuted or demoted exits;
   - the planner's `target` ops;
   - failed-row classes from `eval_moves`.
4. Ledger before and after.
5. Then list:
   - (a) files edited;
   - (b) commands that made requests, with counts;
   - (c) reverted parts or skipped steps;
   - (d) surprises.

State failures plainly.
