# Executor brief: Avatar harness round r15 (ChatGPT orchestrator + parallel subagents)

## Kickoff prompt (paste this into ChatGPT)
```
You are the orchestrator for Avatar harness round r15. Repo: C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent, HEAD 9c196fe.
Read, in full and in this order: CLAUDE.md, docs/plans/execute-r15.md (this brief: protocol, rules, lane paste blocks), docs/plans/harness-r15.md (item specs), then docs/plans/review-r14.md and docs/plans/diag-h3.md as background.
The user picked every option in this plan (F1, F2, H3a as fallback, H3c, H1, H2, H4); nothing is pending. Where the brief and the plan differ, the brief wins. Where a spec is unclear, stop and ask me; do not guess a design.
Follow the "Orchestrator protocol" exactly: wave 0, then lanes A and B in parallel worktrees, then the G15 gates, then LV15 once. Give each subagent its lane paste block verbatim with <WORKTREE> and <BASE> filled in. You merge, run the gates, regenerate the golden trace only within the allowed diff classes, and run LV15; subagents never do.
Goal: fewer code lines, not more. Edit in place, reuse the named helpers, delete what a change makes dead.
Truth metrics: trace row N's tile is the belief at the END of step N; truth row N is RAM at the START of step N; compare truth[N+1] with tile[N]. RAM truth is for gates and offline checks only, never in agent/.
Offline emulator checks are allowed and cost 0 asks: checkpoints under runs/<run>/checkpoint/NNNNNN/ hold pyboy.state; load them read-only into a PyBoy or agent.env_pyboy.PyBoyEnv with window "null".
Report to me at each stop condition and at the end, using the final report format in the brief.
```

---

You are the **orchestrator**.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `9c196fe`.
- **Plan:** `docs/plans/harness-r15.md`.
- **Ledger:** 8774 / 17854. LV15's cap is 800. **Stop** if the ledger would pass 9574.

## Standing rules (already inside every lane block)
1. Ponytail: reuse > stdlib > minimal code. Match the dense one-line style. Mark known ceilings with `# ponytail:`.
2. Run `codegraph explore "<symbols>"` before grep or Read. Summarise big outputs.
3. Python: `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`. Always run pytest with `-n auto`.
4. Agent code:
   - no game words in `agent/`, and no RAM reads in `agent/`;
   - no new gates, filters or hidden rows;
   - no new modules;
   - every cut needs a grep showing zero readers first.
5. Off-limits:
   - never touch `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `runs/e3_budget.json` or `docs/plans/`;
   - `runs/` is read-only, and only through absolute main-checkout paths;
   - never use junctions or symlinks.
6. Zero model requests in lanes. Never run `scripts/run_agent.py` or anything with `--live`. Offline PyBoy loads of checkpoint states are allowed: read-only, with `window="null"`.
7. Assertions: change an existing assertion only if the spec pre-approves it, and report it before and after. If any other assertion fails, stop the item, revert its uncommitted edits and report. Never set `W0_REGEN`.
8. Git, in your worktree only:
   - one commit per item, `<id> <full title from the Parts table>`, plus a one-line body and your own `Co-Authored-By:` trailer;
   - stage explicit paths;
   - never push, rebase or switch branches.
9. Scratch goes to `.tmp/r15/<lane>/` in the main checkout, uncommitted; offline gate scripts go to `.tmp/r15/g/`.

## Orchestrator protocol

### Wave 0 (main checkout)
1. `git status` should show these as modified: `scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`. These should be untracked: `baseline_eval.txt`, `scripts/frames_combo.json`, `docs/plans/review-r14.md`, `docs/plans/diag-h3.md`, `docs/plans/harness-r15.md`, `docs/plans/execute-r15.md`.
2. Commit only the four docs as `W15 r14 review, H3 diagnosis and r15 plan`.
3. Run the suite. It should be 812 passed, 1 skipped. If the user's watch_run edits change this, record it and continue only if the only failures are in `test_watch_run.py`.

### Wave 1 (A and B in parallel)
1. Create the worktrees and start each subagent with its block:
   ```
   git worktree add C:\Users\Abj1\Documents\GitHub\Avatar-wt\r15<lane> -b r15/<lane>
   ```
2. For each lane, check:
   - the diff stays in its owned regions;
   - only pre-approved assertions changed;
   - no game words or RAM reads entered `agent/`;
   - its offline gate outputs exist.
3. Merge with `git merge --no-ff r15/A`, then `r15/B`, and run the suite after each merge. Golden: follow G15 step 2.
4. Remove the worktrees without `--force` (if one refuses, stop and ask). Delete the merged branches.

### G15 and LV15
- Follow the plan, saving to `.tmp/r15/g/`. Re-run the T15 offline gates on the merged HEAD, not only in the lanes. Then run LV15 once.
- Check that the viewer is up first: `curl -s http://127.0.0.1:8765/state`. If it is not, start it in the background with `python scripts/watch_run.py --no-browser`.

### Final report (to the user, and saved to `.tmp/r15/report.md`)
- a 3-line summary;
- per item: its commit, suite counts, and any approved assertion changes (before and after);
- `git diff --stat 9c196fe -- agent`, with the net lines per file;
- the G15 table: suite, golden, T15 F1, H1 and H3a, and the replays;
- the LV15 table (the plan's report list), beside r14;
- the ledger before and after;
- anything skipped or reverted, and why.

### Stop and ask the user when
- the baseline differs beyond `test_watch_run.py`;
- a spec is ambiguous;
- an unapproved assertion fails;
- a merge conflict's intent is unclear;
- a gate fails after one fix attempt;
- `world_exit` excludes forced-walk cuts (F1 step 3);
- a request would hit a model other than through LV15;
- the ledger would pass 9574.

---

## Lane paste blocks

### Lane A: motion (F1, H1, H2). Wave 1.
```
You are lane A of Avatar round r15. Work ONLY in the git worktree <WORKTREE> (branch r15/A, base <BASE>). Read docs/plans/harness-r15.md in full, then implement F1, H1 and H2 exactly as specified, one commit each, in that order, plus the T15-F1 and T15-H1 offline gates.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words and no RAM reads in agent/ (RAM is allowed only in the .tmp/r15/g/ gate scripts, to compare with truth). No new gates or hidden rows: H1 adds a note to the existing free-buttons row; H2 adds re-test rows only for a tile walled on all four sides. Zero model requests; never run scripts/run_agent.py or --live. Offline PyBoy loads of C:\Users\Abj1\Documents\GitHub\Avatar\runs\<run>\checkpoint\NNNNNN\pyboy.state are allowed (read-only, window="null"); copy any checkpoint memory you need into .tmp/r15/A/ before using it; never write under runs/. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, scripts/watch_run.py, tests/agent/test_watch_run.py, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN: if the golden act trace fails, report its diff and leave it failing.
Your files/regions: agent/observer.py (Settle, wait_settled, one new _moving_rows helper beside scroll); agent/skills.py (_step, press, wait_until); agent/space.py (fold); agent/world.py (ONLY prepare's move handling and no-response counter, the free-buttons row note in options, and the new re-test rows beside the explore rows); tests/agent/test_observer.py, test_skills.py, test_space.py, test_world.py, test_options.py, test_loop.py (new tests).
Do NOT touch World.merge, World.here, agent/memory.py or agent/loop.py (lane B owns them).
F1 first step: measure on the offline PyBoy env whether _step's own settle already contains the tail of the player's own move, and report it; never count a move twice.
Pre-approved assertion changes: NONE. If an existing test fails because a settle now reports motion, stop and report before/after.
Steps: take your worktree baseline (pytest -n auto -q), then per item: code + tests, suite, commit "<id> <full title from the plan's Parts table>". Run T15-F1 and T15-H1 (scripts and outputs in C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r15\g\).
Report: per item the commit sha, suite counts, net lines changed per file, the F1 measurement, the T15-F1/H1 results (numbers), the golden diff (if any) as text. Under 40 lines.
```

### Lane B: identity (F2, H3a, H3c, H4). Wave 1.
```
You are lane B of Avatar round r15. Work ONLY in the git worktree <WORKTREE> (branch r15/B, base <BASE>). Read docs/plans/harness-r15.md in full, then implement F2, H3a, H3c and H4 exactly as specified, one commit each, in that order, plus the T15-H3a offline gate.
Rules: ponytail (reuse > stdlib > minimal code; dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words and no RAM reads in agent/. No new gates: H3c states a fact, it never merges; H4 changes how lines are stored and only narrows when a name is confirmed. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, scripts/watch_run.py, tests/agent/test_watch_run.py, runs/ (read-only, absolute main-checkout paths), docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN.
Your files/regions: agent/world.py (ONLY merge and here); agent/memory.py (VIEW_REACH at :88, seen_view, the mint in match_place); agent/loop.py (ONLY _flush_conversation); every seen_view caller's unpacking (grep "seen_view(" across agent/, scripts/, tests/ and list them); tests/agent/test_world.py, test_memory.py, test_loop.py, test_notebook.py (new tests).
Do NOT touch World.prepare, options, observer.py, skills.py or space.py (lane A owns them).
T15-H3a: re-run C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r15\h3\reach.py and mint.py against your worktree's agent.memory (adjust only their sys.path to your worktree), save outputs to .tmp\r15\g\, and report: wrong best-matches >= 0.9 among the 41 arrivals (must be 0), the closest wrong score, and the r13-207 / r14-123 mint frame results (must match the correct place).
Pre-approved assertion changes: NONE. If an existing test asserts a view does not match at 5-6 tiles, or that a multi-speaker text is one heard row, stop and report before/after.
Steps: take your worktree baseline, then per item: code + tests, suite, commit "<id> <full title from the plan's Parts table>".
Report: per item the commit sha, suite counts, net lines changed per file, the seen_view callers changed, the T15-H3a numbers, the golden diff (if any) as text. Under 40 lines.
```
