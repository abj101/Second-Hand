# Executor brief: Avatar harness round r12 (ChatGPT orchestrator + parallel subagents)

You are the **orchestrator** in a ChatGPT session that can start subagents. You do the merging, the gates, the golden regeneration and the live run yourself. Subagents do one **lane** each, in their own git worktree.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `a017ba2`.
- **Plan:** `docs/plans/harness-r12.md`. Read all of it first: the decisions, the parts, the lanes and waves, every item spec, the gates, and the validity check. Read `CLAUDE.md` and `docs/plans/audit-2026-10-22.md` §1 as background.
- **Precedence:** where this brief and the plan differ, this brief wins. Where a spec is unclear, **stop and ask the user**. Do not guess a design.
- **All decisions D1–D16 are picked** (plan, Decisions table). Nothing is pending.

## Standing rules (paste them into every subagent prompt; the lane blocks below already include them)

1. **Tooling (from `CLAUDE.md`):**
   - reuse > stdlib > minimal code (the "ponytail" rule);
   - use `codegraph explore "<symbols>"` (shell) before grep or reading files;
   - summarise large outputs.
2. **Smallest change, reuse first.**
   - No new modules in `agent/`, and no refactors beyond the item spec.
   - Match the dense one-line style of the touched code.
   - No game names or game words in `agent/` code or prompts (`agent/briefs/*.json` is the one place for per-game text).
   - No new gates or filters on options or actions, and no hidden options. Facts go into labels and notes; the act model decides.
3. **Python:** `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with env `PYTHONIOENCODING=utf-8`. **pytest always with `-n auto`** (it is in `pyproject.toml` addopts).
4. **Files:**
   - edit only your lane's files and regions (plan: "Lanes, waves and file ownership");
   - never touch `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, anything under `runs/` (read-only) or `runs/e3_budget.json`;
   - never write `docs/plans/` (the orchestrator does);
   - scratch goes to `.tmp/r12/<lane>/` in the **main** checkout, never committed.
5. **Assertions:**
   - no existing test assertion may change, except the ones the item spec lists as **pre-approved**; report each with before/after;
   - any other failing existing assertion: stop the item, revert your uncommitted edits for it, and report;
   - never regenerate the golden trace (`W0_REGEN`); the orchestrator does that after merge.
6. **Zero model requests** in lanes. Never run `scripts/run_agent.py`, `.tmp/r11/mc*_act.py`, `eval_act --live`, or anything that charges the ledger.
7. **Git, in your worktree only:**
   - one commit per item, message `<id> <title from the plan's Parts table>` plus a one-line body, ending with your own `Co-Authored-By:` trailer;
   - stage explicit paths only (never `git add -A`, `git add .` or `commit -a`);
   - never push, rebase or switch branches; never touch the main checkout's working tree.
8. **Worktrees:** never create junctions or symlinks to `runs/`, `Rom/` or `.venv/`. Tests that need `Rom/` or `runs/` skip in a worktree, and that is expected: take your own worktree baseline first and compare against it.

## Orchestrator protocol

### Wave 0 (you, main checkout)
1. Run the baseline suite and confirm 755 passed, 1 skipped. If it differs, stop and report to the user.
2. W0 (plan, item W0):
   - the D9 commit of `scripts/run_agent.py` alone;
   - the D10 file moves into `docs/plans/rounds/`, committed with the audit, `harness-r12.md` and this brief;
   - delete the empty root dirs after the zero-file check.
   - Re-run the suite.

### Each wave
1. For each lane in the wave, create a worktree from the current `feat/lean-agent` HEAD:
   ```
   git worktree add C:\Users\Abj1\Documents\GitHub\Avatar-wt\<lane> -b r12/<lane>
   ```
   Then start one subagent per lane with its paste block (below), filling in `<WORKTREE>` and `<BASE>` (the HEAD sha).
2. Wait for every lane in the wave. Read each lane report and check:
   - its diff stays inside its owned files and regions;
   - every changed assertion is a pre-approved one;
   - there are no game words in `agent/`;
   - it made no model requests.
3. Merge the lanes into `feat/lean-agent` in the main checkout, in lane order (A, B, C; then D, E; then F), each with `git merge --no-ff r12/<lane>`.
   - Resolve conflicts only where both sides' intent is clear from the plan; otherwise stop and ask the user.
   - The protected uncommitted files (`scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`) must stay untouched; check `git status` before and after.
4. After each merge: run the full suite in the main checkout.
   - If only the golden act trace fails, and the merged lane is D (A2) or F (R1): regenerate with `W0_REGEN=1` and the golden test alone, check that the diff is limited to the classes that item's spec allows, and commit it as `A2g …` or `R1g Regenerate the golden act trace for <reason>`.
   - Any other failure: revert that merge (`git revert -m 1`), report, and continue with the next lane only if it does not depend on the reverted one.
5. Remove the wave's worktrees (`git worktree remove <path>`, **without** `--force`; if it refuses, stop and ask) and delete the merged branches.

### Wave 4 (you)
1. **G** (plan, item G): run every gate, save the outputs to `.tmp/r12/g/`, and report a table.
   - G-door′: adapt `.tmp/r11/gdoor.py` into `.tmp/r12/g/gdoor2.py`.
   - G-V1: run the lane-E seeding script, then copy `.tmp/r12/looks/pokemon_red.jsonl` to `runs/looks/pokemon_red.jsonl`. This is the only new file you write under `runs/` before LV12.
2. **LV12**, only if G passes:
   - check that the viewer is up (`curl -s http://127.0.0.1:8765/state`), and start it if not (`CLAUDE.md`);
   - run the command in the plan once. **No resume and no second attempt** without asking the user;
   - if the run stops on an error, report it.
3. **Final report** (to the user, and saved to `.tmp/r12/report.md`):
   - a 3-line summary;
   - per item: commit, suite counts, gate result, the approved assertion changes (before/after);
   - G table;
   - LV12 report (plan, LV12 "Report");
   - ledger before and after;
   - anything reverted or skipped, and why.

### Stop conditions (stop and ask the user)
- the baseline differs;
- a spec is ambiguous;
- an unapproved assertion fails;
- a merge conflict whose intent is unclear;
- a gate fails after one fix attempt;
- any step that would make model requests other than LV12;
- the ledger would exceed 7704 (LV12's 400 cap is on top of 7304).

---

## Lane paste blocks

Fill in `<WORKTREE>` (for example `C:\Users\Abj1\Documents\GitHub\Avatar-wt\A`) and `<BASE>` (the sha you branched from). Each block is self-contained.

### Lane A: modal screens out of room memory (A1, then S1). Wave 1.
```
You are lane A of Avatar round r12. Work ONLY in the git worktree <WORKTREE> (branch r12/A, base <BASE>). Read docs/plans/harness-r12.md in full, then implement items A1 and then S1 exactly as specified there, one commit each.
Rules: ponytail (reuse > stdlib > minimal code; match the dense one-line style); run `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. No new option gates or hidden options. Zero model requests; never run scripts/run_agent.py. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, runs/, docs/plans/. No junctions/symlinks. Stage explicit paths; never push/rebase/switch branches. Never set W0_REGEN.
Your files/regions: agent/world.py (_tiles, before, observe_after observe calls, the modal predicate beside FURNITURE, the options modal-row block, the type row note); agent/memory.py (observe and the new screens store); agent/loop.py (_settle reply capture and the modal kind test only); tests/agent/test_memory.py, test_world.py, test_options.py, test_loop.py (new tests only).
Steps: 1) Take your worktree baseline: pytest -n auto -q; record the counts. 2) A1: code + tests; suite; commit "A1 A non-world screen writes no world entities; one shared modal() predicate". 3) S1 (includes the typing-cost note): code + tests; suite; commit "S1 A screens store: a modal screen's items and the game's reply to each choice".
Pre-approved assertion changes: NONE. If an existing assertion fails, stop that item, revert its uncommitted edits, and report.
Report: per item the commit sha, suite counts against your baseline, the files and line ranges changed, the new tests, and any surprise. Under 40 lines.
```

### Lane B: prompt facts (A5, then A6, then A7). Wave 1.
```
You are lane B of Avatar round r12. Work ONLY in the git worktree <WORKTREE> (branch r12/B, base <BASE>). Read docs/plans/harness-r12.md in full, then implement A5, A6 and A7 exactly as specified, one commit each, in that order.
Rules: ponytail (reuse > stdlib > minimal code; match the dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/ code or prompts (agent/briefs/pokemon_red.json is the per-game place). No new option gates. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. Never set W0_REGEN.
Your files/regions: agent/loop.py (_see; the focus line and the act call at :174-175; _evidence's stall text); agent/notebook.py (render_focus); agent/act.py (SYSTEM only; act()'s signature does not change); agent/briefs/pokemon_red.json; tests/agent/test_loop.py, test_notebook.py, test_act.py, test_perceive.py.
Pre-approved assertion changes (report each before/after): tests/agent/test_loop.py:1987 (the stall number follows STALL*2**u); tests/agent/test_act.py:71 and :91 (the sentence-count asserts become: SYSTEM contains none of the banned words and len(SYSTEM) <= 1200). Nothing else.
Steps: take your worktree baseline; for each item, code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, changed files/lines, approved assertion changes before/after. Under 40 lines.
```

### Lane C: tooling, A3 cases, hygiene (T1, then MT, then H1). Wave 1.
```
You are lane C of Avatar round r12. Work ONLY in the git worktree <WORKTREE> (branch r12/C, base <BASE>). Read docs/plans/harness-r12.md in full, then implement T1, MT and H1 exactly as specified, one commit each, in that order.
Rules: ponytail (reuse > stdlib > minimal code); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. Zero model requests: never run scripts/run_agent.py or eval_act --live. Read runs/ only through absolute paths into the MAIN checkout (C:\Users\Abj1\Documents\GitHub\Avatar\runs\...); never write there. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, docs/plans/. No junctions. Do not install packages. Stage explicit paths; never push/rebase/switch branches. Never set W0_REGEN.
Your files/regions: scripts/eval_act.py, scripts/eval_memory.py (new), scripts/points.json, scripts/points_r12.json (new), scripts/run_agent.py (resolve_modes, flags, manifest_record dirty flag), scripts/check_track.py (git rm), pyproject.toml, agent/decide.py (removed), agent/loop.py ONLY at the import of Choice, the Loop.__init__ mode params, the Choice construction at :220, the row dicts at :322-331 and :388-398, and state/restore at :542-570; the matching tests.
Notes: points_r12.json cases must reference runs/m1_r11_boot with checkpoints at multiples of 5 (they exist). They are expected to FAIL until lanes A and D merge; just verify they load and replay without errors (INCONCLUSIVE or FAIL are fine for now; report which). For eval_memory.py, verify on the main checkout's runs/m1_r11_boot that it prints world 150, modal-kind 43, route 36.
Pre-approved assertion changes (report each before/after): tests that only exercise the removed modes, i.e. test_run_provenance.py:35-44, test_loop.py:1795-1799 (drop the decide parametrize), test_loop.py:2159-2162 (becomes "a saved modes key is ignored"), test_om_eval.py:246 and :275-277. Removing trace fields: only task, pred, shortcut, ranker, and only after a grep of scripts/ and tests/ proves no reader; keep action_step, view_ids, used.
Steps: take your worktree baseline; for each item, code + tests, suite, commit "<id> <title from the plan's Parts table>".
Report: per item the commit sha, suite counts, changed files, approved assertion changes before/after, the eval_memory r11 row, and the dry `eval_act --om points` PASS/INCONCLUSIVE/FAIL counts. Under 50 lines.
```

### Lane D: exits and unreached evidence (A2, then A3). Wave 2.
```
You are lane D of Avatar round r12. Work ONLY in the git worktree <WORKTREE> (branch r12/D, base <BASE>, which already contains lanes A, B and C). Read docs/plans/harness-r12.md in full, then implement A2 and then A3 exactly as specified, one commit each.
Rules: ponytail (reuse > stdlib > minimal code; match the dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. The system states facts and legal options; the act model chooses: no hidden push choice for an uncrossed exit, no removed options. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. NEVER set W0_REGEN: if the golden act trace test fails, report its diff and leave it failing; the orchestrator regenerates it.
Your files/regions: agent/world.py (options exit block and option(); plan()); agent/loop.py (action handling, _approach_route/_screen_route, the _settle failure branch); agent/skills.py (ONLY the run_affordance onto kwarg); agent/memory.py (ONLY the new reach store helpers); scripts/points.json (ONLY the label_re of m1_r6_live_150:64); tests/agent/test_options.py, test_exits.py, test_world.py, test_loop.py, test_skills.py.
Pre-approved assertion changes (report each before/after): the O1/F1 exit-row label/count/order/numbering class for an uncrossed exit WITH a tile, in test_options.py, test_exits.py, test_world.py and test_loop.py; the r6:64 label_re. Nothing else; the golden failure is expected and reported, not fixed.
Steps: take your worktree baseline; A2 code + tests, suite, commit; A3 code + tests, suite, commit.
Report: per item the commit sha, suite counts, changed files/lines, every approved assertion change before/after, and the golden trace diff (if any) as text. Under 50 lines.
```

### Lane E: looks and the per-game ground store (V1). Wave 2.
```
You are lane E of Avatar round r12. Work ONLY in the git worktree <WORKTREE> (branch r12/E, base <BASE>, which already contains lanes A, B and C). Read docs/plans/harness-r12.md in full (V1 especially) and docs/plans/audit-2026-10-22.md's "Measure results" block, then implement V1 exactly as specified in one commit, plus the seeding script in scratch.
Rules: ponytail (reuse > stdlib > minimal code; match the dense one-line style; mark known ceilings with `# ponytail:` comments as the spec says); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. NEVER use RAM truth (truth.jsonl, scripts/truth_pokered.py) in agent/ or in the seeding script: the seed comes only from each past run's own frames, trace and memory/tiles.jsonl. Zero model requests. Read runs/ only through absolute paths into the MAIN checkout; never write under runs/. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. Never set W0_REGEN.
Your files/regions: agent/memory.py (the Looks class, the per-run looks store, learn_look); agent/space.py (fold's learn_look calls; route's strict-pass exclusion); agent/world.py (_record_view look writes; view's x glyph and the conditional legend text); agent/loop.py (ONLY the Loop.__init__ looks kwarg); scripts/run_agent.py (ONLY the looks= argument in the Loop(...) call); tests/agent/test_space.py, test_world.py, test_memory.py (new tests).
Scratch: write the seeding script to C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r12\v1_seed.py and its output store to C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r12\looks\pokemon_red.jsonl. Adapt C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\r12\v0\v0.py into .tmp\r12\v1_check.py, which scores that store against truth (truth is allowed in this CHECK script only, never in the seed). Report wall/floor precision and per-game coverage of distinct bumped walls.
Pre-approved assertion changes: NONE. With looks=None nothing may change (the golden trace must still pass).
Report: commit sha, suite counts, changed files/lines, new tests, the seed store size (rows, looks), and the v1_check numbers against the gate (precision >= 0.95; coverage >= 60%). Under 40 lines.
```

### Lane F: re-plan on a bump (R1). Wave 3.
```
You are lane F of Avatar round r12. Work ONLY in the git worktree <WORKTREE> (branch r12/F, base <BASE>, which contains lanes A-E). Read docs/plans/harness-r12.md in full (R1 especially), then implement R1 exactly as specified in one commit.
Rules: ponytail (reuse > stdlib > minimal code; match the dense one-line style); `codegraph explore "<symbols>"` before grep/Read; summarise big outputs; python = C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe with PYTHONIOENCODING=utf-8; pytest always `-n auto`. No game words in agent/. The act model's choice of target is never changed; R1 only keeps executing that same target after a bump. Zero model requests. Never touch scripts/eval_frames.py, scripts/frames_combo.json, baseline_eval.txt, runs/, docs/plans/. No junctions. Stage explicit paths; never push/rebase/switch branches. Never set W0_REGEN.
Your files/regions: agent/world.py (travel, _travel_result, the BUMPS constant); agent/loop.py (ONLY the skill-execution block that picks travel / run_affordance, and building the one-leg trip from act_route); tests/agent/test_world.py, test_loop.py.
Pre-approved assertion changes (report each before/after): assertions about the trace fields `travel` and `push` that change ONLY because on-screen approaches now run as one-leg travel. Nothing else. If the golden act trace fails, report its diff and leave it failing; the orchestrator regenerates it within the spec's limits.
Required tests (from the spec's invariants): each move is folded once; a mid-route wall is recorded and avoided within the same step; 3 bumps end the step with bumps: 3; a crossing failure from the tile still reaches failed_push; a scene cut mid-route ends the leg as today.
Report: commit sha, suite counts, changed files/lines, approved assertion changes before/after. Under 40 lines.
```
