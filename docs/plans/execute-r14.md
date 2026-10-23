# Executor brief: Avatar harness round r14 (ChatGPT orchestrator + parallel subagents)

## Kickoff prompt (paste this into ChatGPT)
```
You are the orchestrator for Avatar harness round r14. Repo: C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent, HEAD cc5f3b7.
Read, in full and in this order: CLAUDE.md, docs/plans/execute-r14.md (this brief: protocol, rules, lane paste blocks), docs/plans/harness-r14.md (item specs), then docs/plans/review-r13.md as background (its G2 and G3 rows were corrected; trust the corrected text).
The user has accepted this plan's options for G1-G6; nothing is pending. Where the brief and the plan differ, the brief wins. Where a spec is unclear, stop and ask me; do not guess a design.
Follow the "Orchestrator protocol" exactly: wave 0, wave 1 lanes A and B in parallel worktrees, wave 2 lane C, then the G14 gates, then LV14 once. Give each subagent its lane paste block verbatim with <WORKTREE> and <BASE> filled in. You merge, run the gates, regenerate the golden trace only within the allowed diff classes, and run LV14; subagents never do.
Goal: fewer code lines, not more. Edit in place, reuse the named helpers, delete what a change makes dead.
IMPORTANT for any truth-vs-trace metric: trace row N's tile is the belief at the END of step N; truth row N is RAM at the START of step N; compare truth[N+1] with tile[N].
Report to me at each stop condition and at the end, using the final report format in the brief.
```

---

You are the **orchestrator**.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `cc5f3b7`.
- **Plan:** `docs/plans/harness-r14.md`.
- **Ledger:** 8314 / 17854. LV14's cap is 800. **Stop** if the ledger would pass 9114.

## Standing rules (already inside every lane block)
1. Ponytail: reuse > stdlib > minimal code. Match the dense one-line style. Mark known ceilings with `# ponytail:`.
2. Run `codegraph explore "<symbols>"` before grep or Read. Summarise big outputs.
3. Python: `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`. Always run pytest with `-n auto`.
4. Agent code: no game words in `agent/`; no new gates, filters or hidden rows; no new modules. Every cut needs a grep showing zero readers first.
5. Off-limits: never touch `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py` (the user's uncommitted edits), `runs/` (read-only, and only through absolute main-checkout paths), `runs/e3_budget.json` or `docs/plans/`. Never use junctions or symlinks.
6. Zero model requests in lanes. Never run `scripts/run_agent.py` or anything with `--live`.
7. Assertions: change an existing assertion only if the spec pre-approves it, and report it before and after. If any other assertion fails, stop the item, revert its uncommitted edits and report. Never set `W0_REGEN`.
8. Git, in your worktree only:
   - one commit per item, `<id> <title from the Parts table>`, plus a one-line body and your own `Co-Authored-By:` trailer;
   - stage explicit paths;
   - never push, rebase or switch branches.
9. Scratch goes to `.tmp/r14/<lane>/` in the main checkout, uncommitted.

## Orchestrator protocol

### Wave 0 (main checkout)
1. `git status` should show these as modified: `scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`. These should be untracked: `baseline_eval.txt`, `scripts/frames_combo.json`, `docs/plans/review-r13.md`, `docs/plans/harness-r14.md`, `docs/plans/execute-r14.md`.
2. Commit only the three docs as `W14 r13 review and r14 plan`.
3. Run the suite. It should be 788 passed, 1 skipped (the user's watch_run edits may change this; if so, record the actual baseline, run that test file alone and report it, and continue only if the only failures are in `test_watch_run.py`).

### Waves
1. **Wave 1 (A and B in parallel):**
   ```
   git worktree add C:\Users\Abj1\Documents\GitHub\Avatar-wt\r14<lane> -b r14/<lane>
   ```
   Start each subagent with its block. Then, for each lane:
   - check that the diff stays in its owned regions, only pre-approved assertions changed, and no game words entered `agent/`;
   - merge with `git merge --no-ff r14/A`, then `r14/B`;
   - run the suite after each merge.
2. **T14 before:** after merging A (which adds `points_r14.json`) and before merging B, run the T14 replay and save it as the "before" column.
3. **Wave 2:** create C's worktree from the new HEAD, run it, merge it, run the suite.
4. **Golden:** if the golden test fails after any merge, follow the plan's G14 step 2.
5. Remove the worktrees without `--force` (if one refuses, stop and ask). Delete the merged branches.

### G14 and LV14
- Follow the plan, saving to `.tmp/r14/g/`. Then run LV14 once.
- Check that the viewer is up first: `curl -s http://127.0.0.1:8765/state`. If it is not, start it in the background with `python scripts/watch_run.py --no-browser`.

### Final report (to the user, and saved to `.tmp/r14/report.md`)
- a 3-line summary;
- per item: its commit, suite counts, and any approved assertion changes (before and after);
- `git diff --stat cc5f3b7 -- agent`, with the net lines per file;
- the G14 table, with T14 before and after;
- the LV14 table (the plan's report list), beside the r13 numbers;
- the ledger before and after;
- anything skipped or reverted, and why.

### Stop and ask the user when
- the baseline differs beyond `test_watch_run.py`;
- a spec is ambiguous;
- an unapproved assertion fails;
- a merge conflict's intent is unclear;
- a gate fails after one fix attempt;
- a request would hit a model other than through LV14;
- the ledger would pass 9114.

---

## Lane paste blocks

### Lane A: loud route failures, the way back, replay cases (G1, G5, T14). Wave 1.
```
You are lane A of Avatar round r14. Work ONLY in the git worktree <WORKTREE> (branch r14/A, base <BASE>). Read docs/plans/harness-r14.md in full, then implement G1, G5 and T14 exactly as specified, one commit each, in that order.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. No new gates or hidden rows: G1 keeps the unplannable row offered and only adds a fact note plus a loud failure. Zero model requests; never run scripts/run_agent.py or --live. Read runs/ only through absolute paths into the MAIN checkout; never write there. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, scripts/watch_run.py, tests/agent/test_watch_run.py, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN: if the golden act trace fails, report its diff and leave it failing.
Your files/regions: agent/loop.py (ONLY :199 and the refused SkillResult at :266, plus where `refused` is assigned by _approach_route callers if its type changes); agent/world.py (ONLY the exit-row label and note lines of options ~:1130-1135 and :1162-1165, and _exits' head / uses computation ~:815-845); scripts/points_r14.json (new); tests/agent/test_loop.py, test_options.py, test_exits.py, test_world.py, test_om_eval.py (new tests and the pre-approved ones).
Do NOT touch World.before, _locate, _seen_at, the push-row facts at :1146-1158, space.py or memory.py (lane B owns them).
Pre-approved assertion changes (report each before/after): G5 exit-label assertions that expected a direction head for an exit WITH a known destination. Nothing else.
T14: read the r13 checkpoints you replay from (C:\Users\Abj1\Documents\GitHub\Avatar\runs\m1_r13_boot\checkpoint\...) before writing each regex, and state its basis; for the step-209 case, verify the (0,8) frame mapping from checkpoint 205's memory. Report PASS/FAIL/INCONCLUSIVE per case on your base.
Steps: take your worktree baseline (pytest -n auto -q), then per item: code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, net lines changed per file, approved assertion changes before/after, the T14 per-case results, the golden diff (if any) as text. Under 40 lines.
```

### Lane B: re-entry anchoring, character-held pushes (G2, G3). Wave 1.
```
You are lane B of Avatar round r14. Work ONLY in the git worktree <WORKTREE> (branch r14/B, base <BASE>). Read docs/plans/harness-r14.md in full, then implement G2 and then G3 exactly as specified, one commit each.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. No new gates: G2 only changes which evidence sets the arrival tile (a matching view beats an inferred guess) and corrects the guessed row; G3 records a fact (held) and writes no wall. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, scripts/watch_run.py, tests/agent/test_watch_run.py, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN.
Your files/regions: agent/memory.py (match_place's arrival assignments :221, :229, :239 only); agent/world.py (before :162-165, _seen_at and _locate :409-419, and ONLY the push-row fact lines :1146-1158); agent/loop.py (ONLY :130, passing frame=before to world.before); agent/space.py (fold :15 and push_dirs :72-77); tests/agent/test_world.py, test_space.py, test_memory.py, test_loop.py (new tests).
Do NOT touch the options exit block outside :1146-1158, _exits, loop.py:199/:266 (lane A owns them).
Pre-approved assertion changes: NONE. Every reader of Memory.arrival must keep working with the 3-element form; list every reader you changed (grep "arrival").
Steps: take your worktree baseline, then per item: code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, net lines changed per file, the arrival readers changed, the golden diff (if any) as text. Under 40 lines.
```

### Lane C: an unknown place after a hidden scene change, planner errors that guide (G4, G6). Wave 2.
```
You are lane C of Avatar round r14. Work ONLY in the git worktree <WORKTREE> (branch r14/C, base <BASE>, which already contains lanes A and B). Read docs/plans/harness-r14.md in full, then implement G4 and then G6 exactly as specified, one commit each.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style; G6's name-error candidates must reuse _no_target's text, not a second builder); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/ code or prompts. No new gates: every op still applies or errors exactly when it does today, except done/drop accepting a non-empty string `why` as the reason. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, scripts/watch_run.py, tests/agent/test_watch_run.py, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN.
Your files/regions: agent/loop.py (ONE line next to the cut_carry assignment ~:128); agent/world.py (the World.unsettled default and World.here's Here line ~:749-760 only); agent/notebook.py (_apply's not-open, done/drop reason and name branches; _no_target reuse); agent/plan.py (REPLY one clause); tests/agent/test_notebook.py, test_plan.py, test_world.py, test_loop.py.
Pre-approved assertion changes (report each before/after): the error-message strings in test_notebook.py for not-open keys, empty reasons, and name failures. Nothing else.
Steps: take your worktree baseline, then per item: code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, net lines changed per file, approved assertion changes before/after. Under 40 lines.
```
