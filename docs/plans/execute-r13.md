# Executor brief: Avatar harness round r13 (ChatGPT orchestrator + parallel subagents)

## Kickoff prompt (paste this into ChatGPT)
```
You are the orchestrator for Avatar harness round r13. Repo: C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent, HEAD 3533db0.
Read, in full and in this order: CLAUDE.md, docs/plans/execute-r13.md (this brief: your protocol, rules and the lane paste blocks), docs/plans/harness-r13.md (the item specs), then docs/plans/diag-r12-lab.md and docs/plans/diag-r12-explore.md as background.
All options are already picked by the user (N1a-N4a, I1a, I2a, I3a, X1a, X2b); nothing is pending. Where the brief and the plan differ, the brief wins. Where a spec is unclear, stop and ask me; do not guess a design.
Follow the "Orchestrator protocol" exactly: wave 0 baseline, wave 1 lanes B and C in parallel worktrees, wave 2 lane A, then the G13 gates, then LV13 once. Give each subagent its lane paste block verbatim, with <WORKTREE> and <BASE> filled in. You merge, run the gates, regenerate the golden trace within the allowed diff classes, and run LV13; subagents never do.
Goal of the round: fewer code lines, not more. Prefer editing in place and deleting dead code; every new helper must replace existing duplicates.
Report to me at each stop condition and at the end with the final report format in the brief.
```

---

You are the **orchestrator**. You merge, run the gates and the golden regeneration, and run LV13. Subagents do one **lane** each, in their own git worktree.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `3533db0`.
- **Plan:** `docs/plans/harness-r13.md`.
- **Ledger:** 7854 / 17854. LV13's cap is 700. **Stop** if the ledger would pass 8554.

## Standing rules (already inside every lane block)
1. Ponytail: reuse > stdlib > minimal code. Match the dense one-line style. Mark known ceilings with `# ponytail:`.
2. Run `codegraph explore "<symbols>"` before grep or Read. Summarise big outputs.
3. Python: `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`. Always run pytest with `-n auto`.
4. Agent code:
   - no game words in `agent/` code or prompts;
   - no new gates, filters or hidden options; facts go in labels and notes;
   - no new modules;
   - every cut needs a grep showing zero readers first.
5. Off-limits: never touch `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `runs/` (read-only, and only through absolute main-checkout paths), `runs/e3_budget.json` or `docs/plans/`. Never use junctions or symlinks.
6. Zero model requests in lanes. Never run `scripts/run_agent.py` or anything with `--live`.
7. Assertions: change an existing assertion only if the item spec pre-approves it, and report it before and after. If any other existing assertion fails, stop that item, revert its uncommitted edits and report. Never set `W0_REGEN`.
8. Git, in your worktree only:
   - one commit per item, `<id> <title from the Parts table>`, plus a one-line body and your own `Co-Authored-By:` trailer;
   - stage explicit paths;
   - never push, rebase or switch branches.
9. Scratch goes to `.tmp/r13/<lane>/` in the main checkout, uncommitted.

## Orchestrator protocol

### Wave 0 (main checkout)
1. `git status` should show only `scripts/eval_frames.py` modified and `baseline_eval.txt`, `scripts/frames_combo.json` and the r12 or r13 plan docs untracked.
2. Commit `docs/plans/diag-r12-lab.md`, `docs/plans/diag-r12-explore.md`, `docs/plans/harness-r13.md` and `docs/plans/execute-r13.md` as `W13 r13 plan and diagnoses`.
3. Run the suite and confirm 778 passed, 1 skipped. If it differs, stop and report.

### Waves 1 and 2
1. **Wave 1** (B and C in parallel):
   ```
   git worktree add C:\Users\Abj1\Documents\GitHub\Avatar-wt\r13<lane> -b r13/<lane>
   ```
   Start each subagent with its block. When both are done:
   - check that each diff stays inside its owned regions and only pre-approved assertions changed;
   - check there are no game words in `agent/`;
   - merge with `git merge --no-ff r13/B`, then `r13/C`;
   - run the suite after each merge.
2. **T13 "before":** right after merging B, before merging C, run
   ```
   eval_act --om points --points scripts/points_r13.json
   ```
   Save the result as the "before" column. Most cases should FAIL; that is expected.
3. **Golden:** if the golden test fails after merging C, regenerate it within the plan's G13 diff classes and commit `X2g Regenerate the golden act trace for explore facts and stale actors`. Any other diff class: stop.
4. **Wave 2:** create lane A's worktree from the new HEAD, run lane A, then merge and run the suite. Golden as above (`I1g …`).
5. Remove the worktrees with `git worktree remove <path>`, without `--force`; if it refuses, stop and ask. Delete the merged branches.

### G13 and LV13
- Follow the plan's G13 (save to `.tmp/r13/g/`), then LV13, once.
- Check that the viewer is up first: `curl -s http://127.0.0.1:8765/state`. If it is not, start it in the background: `python scripts/watch_run.py --no-browser`.

### Final report (to the user, and saved to `.tmp/r13/report.md`)
- a 3-line summary;
- per item: its commit, suite counts, and any approved assertion changes (before and after);
- net line delta per file in `agent/` (`git diff --stat 3533db0 -- agent`);
- the G13 table, with T13 before and after;
- the LV13 table from the plan's report list, beside the r12 numbers;
- the ledger before and after;
- anything skipped or reverted, and why.

### Stop and ask the user when
- the baseline differs;
- a spec is ambiguous;
- an unapproved assertion fails;
- a merge conflict's intent is unclear;
- a gate fails after one fix attempt;
- any request would hit a model other than through LV13;
- the ledger would pass 8554.

---

## Lane paste blocks

### Lane B: true closes, dead close path, replay text checks (N1, CUT1, T13). Wave 1.
```
You are lane B of Avatar round r13. Work ONLY in the git worktree <WORKTREE> (branch r13/B, base <BASE>). Read docs/plans/harness-r13.md in full, then implement N1, CUT1 and T13 exactly as specified, one commit each, in that order. N1 also writes plan.py's name sentence (the plan's "plan.py name sentence" paragraph under N3), since you own plan.py.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style; delete dead code only after a grep proves no reader); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. No new gates or hidden options. Zero model requests; never run scripts/run_agent.py or --live. Read runs/ only through absolute paths into the MAIN checkout (C:\Users\Abj1\Documents\GitHub\Avatar\runs\...); never write there. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. Never set W0_REGEN.
Your files/regions: agent/notebook.py (render_focus, talked, _heard_closed, _label, observe's use of _heard_closed ONLY; not the name op); agent/plan.py (SYSTEM); scripts/eval_act.py (the points checker only); scripts/points_r13.json (new); tests/agent/test_notebook.py, test_plan.py, test_eval_act*.py (whichever tests the points checker).
Pre-approved assertion changes (report each before/after): test_notebook.py:213 and :230 (the "speaks to you" row); deleting test_notebook.py:353 and :368 (talked tests); rewriting the setup of test_notebook.py:337 to close with a done op, keeping its label assertions. Nothing else.
T13 note: points_r13.json cases must replay from runs/m1_r12_boot checkpoints. Before writing a regex for step 196's legend, read what that step's memory really holds (entities near the player in the lab) and say what you based it on. On your base, the cases are expected to FAIL; report PASS/FAIL/INCONCLUSIVE per case.
Steps: take your worktree baseline (pytest -n auto -q), then per item: code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts against your baseline, net lines changed per file, approved assertion changes before/after, the T13 per-case results. Under 40 lines.
```

### Lane C: legend first, stale actors, exploration facts, write-only line (N2, X1, X2, CUT2). Wave 1.
```
You are lane C of Avatar round r13. Work ONLY in the git worktree <WORKTREE> (branch r13/C, base <BASE>). Read docs/plans/harness-r13.md in full, then implement N2, X1, X2 and CUT2 exactly as specified, one commit each, in that order.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style; the new unexplored() helper must REPLACE the three inline count sites and explore_dirs' candidate build; delete dead code only after a grep proves no reader); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. No new gates or hidden options: X1 changes memory status from evidence (the tile is in view, nobody is there), it never hides a row by rule. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN: if the golden act trace test fails, report its diff and leave it failing.
Your files/regions: agent/world.py (view's section order and budget loop; _follow and its two calls (before ~166, observe_after ~485); the remembered off-screen rows block; explore_dirs; the explore label; the Here: line; the count sites at ~548 and ~977; the new unexplored() helper beside open_cells); agent/loop.py:81 ONLY; scripts/points.json (ONLY the label_re of m1_r6_live_150 step 41); tests/agent/test_world.py, test_options.py, test_loop.py (new tests and the pre-approved ones).
Do NOT touch World.label, place_label, history, resolve, or the view's `called` reads (lane A owns them next wave).
Pre-approved assertion changes (report each before/after): test_options.py:399 (explore labels); scripts/points.json m1_r6_live_150:41 label_re; test_world.py:759 and :782 only if they assert other places before "Things here". Nothing else.
Steps: take your worktree baseline, then per item: code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, net lines changed per file, approved assertion changes before/after, the golden diff (if any) as text. Under 40 lines.
```

### Lane A: names as guesses until confirmed, speaker confirmation, place names (I1, I2, N3, I3). Wave 2.
```
You are lane A of Avatar round r13. Work ONLY in the git worktree <WORKTREE> (branch r13/A, base <BASE>, which already contains lanes B and C). Read docs/plans/harness-r13.md in full, then implement I1, I2, N3 and I3 exactly as specified, one commit each, in that order.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style; ONE _shown() helper used by every site that renders `called`; I2 deletes the duplicate speaker regex in _settle); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. No new gates: the name op still accepts any cited text; provenance is shown, never enforced. World.history and name resolution must NOT change. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN: if the golden act trace fails, report its diff and leave it failing.
Your files/regions: agent/world.py (World.label, place_label, the new _shown helper, the view's `called` reads, the place resolution name list); agent/loop.py (_settle's speaker claim, _flush_conversation, the name callable at ~46); agent/notebook.py (the name op ONLY); agent/memory.py (Memory.call store kwarg); tests/agent/test_world.py, test_options.py, test_loop.py, test_notebook.py, test_memory.py.
Pre-approved assertion changes (report each before/after): test_notebook.py:448-453 error-message string only; a said-tier test moving its expected step from _settle to the flush step (I2), if one exists. Nothing else.
Steps: take your worktree baseline, then per item: code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, net lines changed per file, approved assertion changes before/after, the golden diff (if any) as text. Under 40 lines.
```
