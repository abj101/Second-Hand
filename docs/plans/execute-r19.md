# Executor brief: Avatar round r19, honest novelty, goals that point somewhere, clean door crossings (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `a01c9b5` (V1 merged on top of `c7ea030`, the LV18g result). Check data: `runs/m1_r18g_odo` (read-only), `.tmp/r18g/`.

## 1. Verified diagnosis (LV18g, steps 151–200)
After the lab exit (step 152) the agent looped through the known house instead of heading north. Planner retries cost an extra request each time.

**Already fixed in V1 (`12e7dbb`, merge `a01c9b5`, done in Opus's session, not a lane):**
- `Memory.match_place` counted a visit only on a view match. The exit and relation branches returned known places without counting, so Pallet and the bedroom stayed at `visits=1`.
- `Loop._moved` therefore emitted T2 "arrived somewhere new" for both, which triggered planner calls and q12 ("check the bedroom objects").
- V1 counts the visit in all three branches.

**C2, goal gap (verified in code and trace):**
- q10 closed by arrival at step 152. Steps 153–154 ran with `Goal: (none)`, because `Loop._plan_trigger` applies the 3-step floor to every event.
- That contradicts `contracts.md` "notebook and act": "T2 and T4 keep the 3-step floor", so T3 (quest closed) and T5 (no open quest) should not wait.
- The planner then wrote q11 "find a way forward" but could only target a known place (`place:p_place_4`, Pallet). It closed on arrival at step 155 as "reached", giving no direction.
- Quests can target only `place:`/`thing:`. Nothing can express "somewhere not yet visited", even though the map view shows unexplored cells by direction.

**C3, option framing (verified in `World.options` `option()`):**
- A known-place row ("go to room [p_place_2], via door") always gets state `new`. The state comes only from talked/checked/uses, so a place row lands under **"Not tried here:"**, next to the genuinely new "explore UP, 9 unexplored cells".
- Exit rows show "(N unexplored spots beyond)"; place rows show no visit history at all.
- At step 153 the model picked the house ("which I haven't reached yet").

**C4, planner retries (high priority; verified in `Notebook._apply` and `calls.jsonl`):**
- `add` requires `why` to cite known game-text keys. An exploratory quest has no game text behind it.
- So the model writes a prose `why`, gets "why must cite game-text keys, newest: t31, t30, t29", and the loop re-asks in the same step (`agent/loop.py` ~148-151). The retry then cites t31 spuriously.
- Count: 6 such errors in LV18g (2 extra requests at steps 155 and 158), 4 in r17 (name ops), 4 in LV18f.
- The rule does not prevent ungrounded quests (q12 passed with a fake t31 citation); it only costs a request and forces a false citation.

**G1, wall recorded on a door during a successful crossing (verified in memory):**
- `p_place_3@-5,6` (house ground-floor exit DOWN → Pallet) got `blocked:["DOWN"], walled:{"DOWN":155}` on the step where that very exit was crossed.
- Hypothesis H1 (inferred, to confirm in D1): the exit-push press shows no pixel shift before the fade. `_odometer_drain` files it as a `(0,0)` bump in the pre-cut segment, and travel folds that segment (`self._travel_segments[:1]`), so `space.fold` writes a wall.
- A press answered by a scene cut is a crossing, not a bump.

**G2, lost travel segments (inferred):**
- Steps 153 and 156 each dropped 1 world-type segment: a 3-cut Pallet → ground floor → bedroom travel.
- `World.travel`, after a successful crossing, folds only `_travel_segments[-1]` in the destination and counts the middle ones as `dropped` (`agent/world.py` ~1530 and ~1543), although all `[1:]` were already retagged to the destination.
- Hypothesis H2: the middle segment is fade-in frames between two cuts. If it holds motion (a forced step off the door mat), dropping it loses a tile.

**G3, Pallet offset (lower priority, diagnosis only):**
- The two Pallet visits have RAM−tile offsets (5,6) at step 152 and (5,5) at step 155.
- Checked against RAM:
  - house door `p_place_4@0,0` = RAM (5,5), consistent with offset (5,5);
  - lab door `p_place_4@7,5` = RAM (12,11), consistent with (5,6).
- So Pallet's own map is stitched one row off between its two doors. The lab door was recorded at step 88 in `m1_r18f_odo` (deferred match from the scripted Oak walk at step 69).
- Hypothesis H3: the odometer still loses one row in that forced walk.

## 2. Design (stop and ask if any point cannot work as written)
- **P1, uncited quests are allowed and labelled (C4).**
  - `add` keeps citation when `why` holds known t-keys.
  - When `why` holds no known key, the quest is still added: store `why: []` and the given text as `basis` (≤ 200 chars), with `source: "own"`.
  - Renderers show it as "(your own idea: <basis>)" where cited text would go.
  - `name` keeps its citation rule unchanged: names come from game text.
  - Update the planner SYSTEM/REPLY lines: "why cites the t-keys the quest comes from; if no game text supports it, give one sentence".
  - The schema stays the same (`why` is already string or list).
  - Result: no error, no in-step retry for this case.
- **P2, a frontier target (C2).**
  - `add`/`target` accept the literal target `unexplored` (resolved to `"unexplored"`, not a place or thing).
  - Such a quest closes only when the loop enters a place minted at or after the quest's `opened`/`retargeted` step. Use the place row's `first`, which is correct now that V1 counts visits.
  - `render_focus`: "Goal: <text> (done when: you reach a place you have not been before)".
  - Planner SYSTEM: "target unexplored when the outcome is finding somewhere new".
  - No direction, no coordinates; the act model already sees the explore rows.
- **P3, T3/T5 skip the floor (C2; makes the code follow the existing contract text).**
  - In `_plan_trigger`, the 3-step floor applies only when every waiting event is T2 or T4.
  - The in-step retry loop is unchanged.
- **P4, place rows carry their history (C3).**
  - In `World.options`, a `place:` row's state is `worked` when the place has `visits ≥ 1`.
  - Its notes start with "visited N×, last step S", plus the existing `_beyond_suffix` (unexplored exits and spots beyond), as exit rows already show.
  - The "not reached: …" note stays.
  - Explore rows are unchanged (still `new`).
  - No row is hidden or reordered within its group.
- **P5, a press answered by a cut is not a bump (G1). Only if D1 confirms H1.**
  - In `World._odometer_drain`, a `(0,0)`/no-response press that falls in a segment that ends with a cut, and is the last press in that segment, is dropped from `_bumps` and recorded in `_presses` (a crossing press).
  - One condition, at the single place every caller routes through. The skills' endpoint `cut()` is untouched.
- **P6, fold every post-crossing segment (G2). Only if D1 confirms H2.**
  - At both `travel` sites, replace "fold `[-1]`, drop the middle" with "fold every segment in `[1:]` in order, from the arrival anchor".
  - If D1 shows that middle segments carry spurious fade motion instead, stop and report; do not implement P6.
- **G3: no code this round.** D1 reports where the row is lost. The fix is proposed in the report, not built.
- **Ceilings** (`# ponytail:` comments):
  - P1: uncited quests are trusted like cited ones. Upgrade path: reconcile their basis against the map evidence.
  - P2: "unexplored" closes on any new place, not a specific one.

### 2.1 Contract amendment (approved by the user issuing this brief; the orchestrator commits it in wave 0)
Append under `## notebook and act` in `docs/plans/contracts.md`:
```
- r19 (approved 2026-10-25): a quest's why cites the t-keys it comes from; with none it is still added as the
  planner's own idea (why [], basis = its sentence, source "own"), shown as such. name keeps its citation rule.
  A quest may target "unexplored": it closes when a place first seen at or after its opened/retargeted step is
  entered. T3 and T5 skip the 3-step floor (T2/T4 keep it). Known-place option rows are "worked" when visited and
  note "visited N×, last step S" plus what lies beyond.
```
Under `## world`, append only if P5/P6 are built:
```
        # r19: a no-response press in the segment a cut ends, as its last press, is a crossing press, never a bump.
        # After a travel crossing every post-cut segment folds in the destination from the arrival anchor.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| N1 | P1 + P2 + P3 + tests | agent/notebook.py, agent/plan.py, agent/loop.py, tests/agent/test_notebook.py, tests/agent/test_plan.py, tests/agent/test_loop.py, tests/agent/test_qs_e2e.py | wave 0 | gpt-6-luna HIGH |
| O1 | P4 + tests | agent/world.py (`options` only), tests/agent/test_options.py | wave 0 | gpt-6-luna HIGH |
| D1 | offline diagnosis of H1/H2/H3 (zero model requests) | `.tmp/r19/D/` only | wave 0 | gpt-6-luna HIGH |
| T1 | P5 and/or P6 + tests, only for the hypotheses D1 confirmed | agent/world.py (`_odometer_drain`, `travel` only), tests/agent/test_world.py | D1, O1 merged | gpt-6-luna HIGH |
N1, O1 and D1 run in parallel. T1 starts after D1 reports and O1 is merged, because it edits the same file.

### Pre-approved test changes
- **N1:**
  - Tests asserting the "why must cite game-text keys" error for `add` change to assert the own-idea quest (`why []`, `basis`, `source "own"`). Tests for `name` citation stay unchanged.
  - Tests asserting that T3 or T5 wait inside the floor change to fire at once.
  - List every changed assertion before/after.
- **O1:** assertions on a known-place row's state (`new` → `worked`) and its notes (the new visit note first). Nothing else.
- **T1:** none. Existing assertions must hold.
- Any other failing assertion: stop, revert, report. Never set `W0_REGEN`. If golden data changes, stop and ask.

### New tests (minimum)
- **N1:**
  1. An `add` with a prose why is accepted as own (no error), and the loop makes no second plan call in that step.
  2. An `add` citing t-keys is unchanged.
  3. A `name` with a prose why is still refused.
  4. An `unexplored` quest stays open on entering a known place (first < opened) and closes on entering a place minted after it opened.
  5. A T3 event right after a plan triggers at once; a lone T2 still waits.
  6. `render_focus` shows the unexplored done-when and the own-idea basis.
- **O1:** a visited known place's row is under worked with "visited N×, last step S"; an explore row is still new.
- **T1:**
  - P5: a travel leg whose exit press is followed by a blank cut writes no wall at the exit tile, and the crossing is recorded. A real no-response press with no cut still walls.
  - P6: a 3-segment crossing whose middle segment holds one tile of motion ends at arrive + 1.

### D1 tasks (zero model requests; pattern `.tmp/r18fix3/D/replay145.py`)
Copy every checkpoint/state file you use to `.tmp/r19/D/` first; never write in runs/. Offline PyBoy with `window="null"`.
1. **H1/H2:** replay `m1_r18g_odo` steps 151–156 from checkpoint 150, feeding each step's recorded perception (trace `entities`/screen) and recorded choice, with zero model calls.
   - Assert each after-frame matches the recorded frame (hash or pixel equal).
   - Dump per travel leg: every odometer segment (start/end seq, cut type, frames, uncertain, summed px, `_bumps`, `_presses`) and RAM (map, x, y) per frame.
   - Report: is the step-155 DOWN wall at `p_place_3@-5,6` the exit press filed as a bump (H1)? What do the middle segments at 153/156 contain: motion, blank, fade (H2)? Is the forced step off the door mat in a middle or the last segment?
   - If replay does not match recorded frames, stop and report the first divergence.
2. **H3:** run `scripts/check_odometer.py --run <copy of m1_r18f_odo> --from 65 --to 90`, plus a per-frame tile vs RAM table for step 69 (the scripted walk) through step 88.
   - Name the first frame where tile − RAM changes by one row, and the segment and its type.
   - Propose a fix in the report (no code).
3. Write `.tmp/r19/D/diagnosis.md`: confirmed/refuted per hypothesis with evidence, and exactly which of P5/P6 T1 should build.

## 4. Gates
| Gate | Pass |
|---|---|
| T-N1 | suite green (baseline after V1: 904 passed, 1 skipped); changed assertions only as pre-approved |
| T-O1 | suite green; only pre-approved assertions changed |
| T-T1 | suite green; D1's replay re-run on the T1 tree: no wall at `p_place_3@-5,6` (P5); final tile at 153/156 equals RAM − visit offset (P6) |
| G19 | merged HEAD: full suite green. T18F/G/H/I PASS. T18-O2 unchanged. T18-O3 unchanged, or (only with P6) every changed row moves closer to RAM, listed row by row. Golden unchanged. |

## 5. LV19 (one live run, orchestrator only, after G19)
It restarts from the same post-battle checkpoint, so the lab exit → Pallet → onward stretch is directly comparable with LV18g.
- **Ledger:** 10509 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap **120** calls; **stop if the ledger would pass 10630**. Viewer up (`curl -s http://127.0.0.1:8765/state`), else `python scripts/watch_run.py --no-browser` in the background.
- **Run:** copy `runs/m1_r18g_odo` to `runs/m1_r19_odo` (must not exist; never resume an original), then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r19_odo --resume --from-step 150 --steps 60 --max-calls 120`. One invocation; no rerun, extension or early stop.
- **Metrics** (window 151–210; reuse `.tmp/r18g/metrics.py` with only `ranges` changed to add `("m1_r19_odo",151,210)`):
  1. **T2 honesty:** every "arrived somewhere new" names a place whose `first` is that step. Target 100%.
  2. **Planner:** `add` errors (target 0 citation errors), plan requests per plan row, own-idea quests (text and basis).
  3. **Goal gap:** world steps with `Goal: (none)` in the act prompt (LV18g: 153, 154). Target 0 after a T3.
  4. **Progress:** the lab exit step; places minted after step 152 (Route 1 or anything new); untargeted re-entries to the lab or the house; the first crossing north out of Pallet, or the per-world-step choices showing why not.
  5. **Walls on exit tiles:** blocked edges on tiles that hold an exit, in the step that crossed them. Target 0. All walls are split into NPC/prop/scenery as in LV18g.
  6. Unchosen presses 0; lock ≥ 90%; odometer gap 0; dropped split by segment type (world-type target 0); one offset per visit (the Pallet row error is known and is reported, not counted as new).
- LV19 informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r19\<WP>\` and gate scripts at `.tmp\r19\g\`. agent/ stays game-agnostic: the literal "unexplored" and the visit notes are generic.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `<BASE>` and the §6 rules pasted. You alone review diffs, run gates, merge and run LV19.
1. **Wave 0:**
   - `git status` must show only the user's five files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`. HEAD is `a01c9b5`.
   - Apply the first block of §2.1 and commit it with this brief as `docs: r19 plan and approved contract amendment`.
   - Run the suite (expect 904 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - N1, O1 and D1 in worktrees `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r19N1` (`-b r19/N1`), `...\r19O1` (`-b r19/O1`) and `...\r19D1` (no commit).
   - Review each diff: P-points only; no new class, module, production gate or option filter.
   - Run T-N1 and T-O1. Allow one repair round per WP with the exact failing output; stop if it fails twice. Merge `--no-ff`, O1 first.
3. **Wave 2:**
   - Read D1's diagnosis. If it confirms neither H1 nor H2, skip T1 and report.
   - Otherwise run T1 from the merged HEAD (`...\r19T1`, `-b r19/T1`), building only the confirmed P5/P6. Run T-T1 and merge.
   - Then commit the §2.1 world block, if built.
4. Run **G19**, then **LV19** once.
5. **Report:** save `.tmp/r19/report.md` with:
   - commits and net lines per file;
   - tests deleted/added/changed (before/after);
   - D1's findings, including the G3 fix proposal;
   - the gate table;
   - LV19 metrics against LV18g;
   - deviations and open issues (the fake top-wall door and the "shop" label stay open by scope).
   Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r19 result`. Never push.
- **Stop and ask the user when:**
  - a §2 point cannot work as written, or D1 contradicts a design point;
  - a non-pre-approved assertion fails after one repair;
  - the golden data changes;
  - T18F/G/H/I or T18-O2 change, or T18-O3 changes other than as allowed;
  - the ledger would pass 10630;
  - `git status` differs from wave 0's expectation;
  - a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### N1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r19, WP N1 (own-idea quests, unexplored target, T3/T5 skip the floor). Worktree: <WORKTREE> (branch r19/N1 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r19.md §1, §2 P1-P3, §3 N1; docs/plans/contracts.md "notebook and act"; agent/notebook.py _apply, observe, render, render_focus, _game_text_for; agent/plan.py SYSTEM/REPLY; agent/loop.py _plan_trigger, the plan block in step (~140-160), _moved, _event.
Build exactly P1, P2, P3 and the ponytail ceiling comments. "unexplored" closes on a place whose row's first >= the quest's opened/retargeted step (the notebook gets this through one constructor callback like its existing ones, or the loop closes it in _moved; pick the smaller diff and say why). No new class, module, gate or option filter; no game words.
Pre-approved test changes are exactly §3 N1. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r19\N1\]
Done when: suite green; one commit "N1 own-idea quests, unexplored target, T3/T5 skip the floor"; report diff per file with net lines, tests changed (before/after) and added, and the suite summary line.
```

### O1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r19, WP O1 (known-place option rows carry their history). Worktree: <WORKTREE> (branch r19/O1 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r19.md §1 C3, §2 P4, §3 O1; agent/world.py options (its option() helper and the places loop), history, _beyond_suffix, beyond; tests/agent/test_options.py.
Build exactly P4: a place: row is "worked" when visits >= 1, with first note "visited N×, last step S" and the exits' beyond suffix. Edit only World.options (and its option() helper). Explore rows unchanged. No hidden or reordered rows within a group, no new class, module or filter.
Pre-approved test changes are exactly §3 O1. Any other failing assertion: stop, revert, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r19\O1\]
Done when: suite green; one commit "O1 known-place rows carry their visit history"; report the diff, tests changed/added, suite summary line, and one rendered option list from a test before/after.
```

### D1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r19, WP D1 (offline diagnosis, no code). Worktree: <WORKTREE> (from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r19.md §1 G1-G3, §3 D1; .tmp/r18fix3/D/replay145.py and diagnosis.md (replay pattern); scripts/check_odometer.py; agent/world.py _odometer_drain, _odometer_fold, travel; agent/track.py Odometer; agent/space.py fold.
Do the three D1 tasks. Copy every runs/ file you read to .tmp/r19/D/ first; never write in runs/. Zero model requests (feed recorded perceptions and choices); never edit agent/ or tests/; never run scripts/run_agent.py. RAM reads are allowed here (evaluator-side scratch only).
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r19\D\]
Done when: .tmp/r19/D/diagnosis.md states for H1, H2 and H3 confirmed/refuted with the dumped evidence, the first replay divergence if any, which of P5/P6 to build, and a G3 fix proposal; report its path and a 10-line summary.
```

### T1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r19, WP T1 (crossing press is not a bump; fold post-crossing segments). Worktree: <WORKTREE> (branch r19/T1 from <MERGED HEAD>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r19.md §1 G1/G2, §2 P5/P6, §3 T1; .tmp/r19/D/diagnosis.md; agent/world.py _odometer_drain, _odometer_fold, travel; agent/space.py fold; tests/agent/test_world.py travel/odometer tests.
Build only the P-points the orchestrator names: <P5 and/or P6>. One condition each, at the single shared site; edit only _odometer_drain and/or the two travel fold sites. No existing assertion may change.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r19\T1\]
Done when: suite green; D1's replay re-run on your tree shows the T-T1 conditions; one commit "T1 <one-line result>"; report diff, tests added, suite line and the replay output.
```
