# Executor brief: Avatar round r20, plans that follow meaning, ground-truth arrivals, cheaper battles (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `341264d` (Ha/Hb/He merged on top of `cbcbbed`, the r19 result). Check data: `runs/m1_r19_odo`, `runs/m1_r18g_odo`, `runs/m1_r17_boot` (all read-only), `.tmp/r19/` (`haiku/steps.csv`, `identity.txt`, `battle.txt`, `prompts.txt`).

## 1. Verified diagnosis (LV19, steps 151–192)
**Already fixed in Opus's session (Haiku workers, merged before this brief; not lanes):**
- **Ha `776a2e4`+`e11fd93`, finding (a):** `World.travel` took any cut on a door leg as that door's crossing. At step 175 a wild-battle flash came 5 presses into a longer route, and travel "crossed" into the house ground floor (`p_place_3`) while RAM stayed on Route 1 (map 12). The 6 "world-type" dropped segments at 175 were that flash. Now a cut before the leg's last press stops travel open, and the loop re-identifies the place; `battle` joined the modal-screen check.
- **Hb `2594a81`, part of (b):** a quest can no longer target the place it is already in. q12 "Defeat the wild RATTATA" had target `place:p_place_4` while standing in it, so the act walked away in order to "enter" it, which led into the grass at step 175. The planner prompt also now says `unexplored` is never for a known place or exit.
- **He `a079b77`, finding (e):** every trace row's odometer stats now carry `segments` (start/end seq, cut, gap, frames, uncertain, px, screen, place, bumps, presses, fate folded/dropped/held), and `presses.jsonl` carries `seqs`.
  - Why this matters: live runs drive the continuously running `LivePyBoyHost`, so a stepped offline replay can never match frames. r19 D1's mismatch was only the text-box rows (y 124–139), where text printing timing differs.
  - From now on, cut and position diagnoses read these live logs instead of replaying.

**R1, replanning on every new game text (high priority; verified in code):**
- `Loop._learn` raises T1 once per finished conversation (`_flush_conversation`) and for each new world-screen text.
- Since r19 P3, T1 skips the 3-step floor, so every conversation, sign or battle message plans at once. The qs_e2e test now expects 200 plans for 200 steps of fresh text.
- LV19 step 171: a plan triggered by the just-finished battle's text created "Defeat the wild RATTATA" after the battle had already ended.
- Whether text changes the plan is a judgement about meaning. Only a model can make it, and the act model already reads that text every step.

**B, target misuse that a refusal can't catch (verified):**
- At step 152 "Exit the lab and return to the outdoor area" got `target: unexplored`, so the act spent 152–155 exploring inside the lab. LV18g exited at 152.
- Hb's prompt line addresses the wording; R1 removes the stale-text quests. No further code is planned for B; LV20 measures it.

**C, "unexplored" can't close in a seamless overworld (verified):**
- Pallet → Route 1 (map 0 → 12) has no cut, so Route 1 continues Pallet's tile frame (Pallet offset [5,6], Route 1 [5,42]: Route 1 is 36 rows taller, which is consistent). No place is minted.
- `unexplored` closes only on entering a newly minted place, so q11 "Explore the outdoor area" stayed open through Route 1.
- The geometry is right; the close condition is the gap.

**D, arrival anchors mean two different things (verified from data; which segment holds the forced step is inferred):**
- **Observed arrive:**
  - r17 step 52 minted Pallet with origin (0,0) at RAM (5,6). That is where the player stands after the game's automatic step off the house door (the door itself is RAM (5,5)).
  - So the observed edge `p_place_3@-5,6 DOWN → p_place_4 arrive (0,0)` is a post-forced-step position.
- **Inferred arrive:** the lab's way back `p_place_5@0,8 → p_place_4` lands on the door tile (7,5), RAM (12,11), which is a pre-forced-step position.
- Travel lands on `arrive` and then folds the post-cut segment, which holds the forced step. That is right for the lab (LV18g 152 and LV19 156 match RAM) and double-counts it for the house:
  - LV18g step 155 ended on tile (0,1) with RAM (5,6), i.e. offset (5,5);
  - every other Pallet visit has offset (5,6).
- r19 D1's "transient at step 69" is unrelated.
- `space.mark_exit`'s ponytail comment already names a related ceiling ("a warp one tile further lands 1 off").

**F, battles take most of the budget (measured, not yet explained):**
- LV19 battle steps after 150: 26 of 42 steps, about 78 of 120 requests.
- Per battle step: perceiver 36 requests over 26 steps, act 26, skill 16. The skill calls are one menu-read call ("items, cursor, layout") per `select`.
- Why the perceiver exceeds one request per step is unexplained. The run hit its cap at step 192 of 210.

## 2. Design (stop and ask if any point cannot work as written)
- **P1, the act model decides whether new text changes the plan (R1).**
  - The act reply gains one optional boolean field: `"replan": true` only when the text on screen changes what you should be doing. Default false; still strict JSON; options and `i` are unchanged.
  - A T1 event dispatches a plan only if some act reply flagged `replan` since that text started.
  - Unflagged text is still recorded (`heard`, the notebook game text) and reaches the planner at its next plan from any other trigger.
  - T3, T5, T2 and T4 are unchanged. T5 still arms on new text when no quest is open.
  - The trace row records the flag.
  - Revert r19's "fresh text plans immediately" expectation to: text plans only when flagged.
- **P2, "unexplored" closes on ground that was off the map (C).**
  - Tile rows carry `first` (the step a tile row was created; `space._new` sets it once and later saves keep it).
  - An `unexplored` quest also closes when the player stands on a tile of the current place whose `first` ≥ the quest's opened/retargeted step and that is at least half a screen (`size // tile_px // 2` tiles on either axis, the same span as `explore_dirs`) from every tile whose `first` < that step.
  - Rows without `first` count as old. Minted-place closing stays.
  - Ponytail ceiling: distance by tile count, not reachability.
- **P3, one meaning for `arrive` (D). Only if D2 confirms.**
  - An edge's `arrive` is where the player stands once the screen has settled after the crossing (the observed meaning, which existing maps already use).
  - `mark_exit`'s inferred way back keeps its guess, but marks `"settled": false`; observed edges are `"settled": true`, and old rows without the key count as observed.
  - In `travel`, landing on a settled arrive drops the post-cut motion as already contained in the anchor. Count it in a new `absorbed` stat, not `dropped`.
  - Landing on an unsettled arrive folds it as now.
  - On the first observed crossing of an inferred edge, `mark_exit` replaces it as today.
- **P4, battle cost: diagnosis and options only (F).** D2 explains the per-step request breakdown and proposes options. No code in this round: anything that skips a decision needs the user's choice, and no new mechanical gates are allowed.

### 2.1 Contract amendments (approved by the user issuing this brief; the orchestrator commits them in wave 0)
Under `## notebook and act`:
```
- r20 (approved 2026-10-25): the act reply may carry "replan": true when the text on screen changes what should be
  done; T1 (new game text) dispatches a plan only when an act reply flagged it since that text began. Text is always
  recorded. An "unexplored" quest also closes on standing on ground first seen after it opened, at least half a
  screen beyond everything seen before. A place target equal to the current place is refused (Hb).
```
Under `## world`:
```
        # r20: a cut before a door leg's last press is not that door (travel stops open, the loop re-identifies) (Ha).
        # Trace odometer stats carry per-segment logs (He). If P3 is built: an edge's arrive is the settled standing
        # tile; inferred ways back are marked settled:false until crossed; landing on a settled arrive absorbs post-cut motion.
```
If P3 is not built, commit the world block without its last two lines.

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| N3 | P1 + P2 + tests | agent/act.py, agent/loop.py, agent/notebook.py, agent/space.py (`_new` only), tests/agent/test_act.py, tests/agent/test_loop.py, tests/agent/test_notebook.py, tests/agent/test_qs_e2e.py, tests/agent/test_space.py | wave 0 | gpt-6-luna HIGH |
| D2 | confirm or refute D; explain F; zero model requests | `.tmp/r20/D/` only | wave 0 | gpt-6-luna HIGH |
| A2 | P3 + tests, only if D2 confirms D | agent/space.py (`mark_exit`), agent/world.py (`travel`), tests/agent/test_space.py, tests/agent/test_world.py | D2, N3 merged | gpt-6-luna HIGH |
N3 and D2 run in parallel. A2 starts from the merged N3 HEAD.

### Pre-approved test changes
- **N3:**
  - `test_fresh_text_plans_immediately_and_events_are_never_lost` (qs_e2e) and the r19 trigger test that a lone T1 dispatches at once now assert: without a flag no T1 plan, with a flag a plan. "Events are never lost" still holds: the text is recorded.
  - Act tests that pin the act schema or reply-format line exactly may add the optional field.
  - List every changed assertion before/after.
- **A2:** none, except assertions on `exit` dicts that gain `"settled"`. List them.
- Any other failing assertion: stop, revert, report. Never set `W0_REGEN`; if golden data changes, stop and ask.

### New tests (minimum)
- **N3:**
  1. New world text with `replan` false: no plan, the text is in the notebook.
  2. The same with `replan` true on an earlier dialogue step: a plan at the first allowed step.
  3. No open quest + new text: T5 still plans.
  4. An unexplored quest stays open on old ground, and also on new ground within half a screen of old ground; it closes on new ground beyond that.
  5. Tile `first` survives later saves.
- **A2:**
  - A settled arrive plus a 1-tile post-cut segment ends on arrive.
  - An unsettled arrive plus the same ends on arrive + 1.
  - An old exit row without `settled` behaves as settled.

### D2 tasks (zero model requests; evaluator scratch may read RAM)
Copy everything you read from runs/ to `.tmp/r20/D/` first.
1. **D:** using `scripts/check_odometer.py` logic (it re-emulates presses from checkpoints and matched RAM 100% in r19 D1), produce per-frame tile vs RAM tables for:
   - `m1_r17_boot` 51–53 (the house exit that minted Pallet);
   - `m1_r18g_odo` 151–156 (lab exit 152 and house exit 155);
   - `m1_r19_odo` 155–157 (lab exit 156).

   For each crossing, report:
   - the warp frame;
   - the landing tile on the first post-cut world frame;
   - any forced motion after landing and which segment holds it;
   - the anchor travel or minting used;
   - the resulting tile − RAM at settle.

   Confirm or refute: observed arrive = settled (post-forced-step), inferred arrive = landing tile, and travel double-counts only on settled arrives.
2. **F:** from `runs/m1_r19_odo/calls.jsonl` and the trace, break down every battle-step request by role and reason (perceiver retries or repairs, re-perception after a skill, menu reads).
   - Say which calls repeat information already known: the same menu layout per screen id in the `screens` store, an unchanged screen re-perceived, and so on.
   - Propose 2–3 generic options, each with the number of requests saved in LV19. No game words. None may auto-pick an action for the model.
3. Write `.tmp/r20/D/diagnosis.md`: D confirmed/refuted with tables, and the F breakdown and options.

## 4. Gates
| Gate | Pass |
|---|---|
| T-N3 | suite green (baseline at `341264d`: 910 passed, 1 skipped); changed assertions only as pre-approved |
| T-A2 | suite green; D2's per-frame tables re-derived on the A2 tree give tile − RAM equal across all Pallet visits in those windows |
| G20 | merged HEAD: full suite green. T18F/G/H/I PASS. T18-O2 unchanged. T18-O3 unchanged, or (only with A2) every changed row moves closer to RAM, listed. Golden unchanged. |

## 5. LV20 (one live run, orchestrator only, after G20)
- **Ledger:** 10629 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap **160** calls (battles cost about 3 per step); **stop if the ledger would pass 10789**. Viewer up, else `python scripts/watch_run.py --no-browser` in the background.
- **Run:** copy `runs/m1_r19_odo` to `runs/m1_r20_odo` (must not exist), then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r20_odo --resume --from-step 150 --steps 60 --max-calls 160`. One invocation; no rerun, extension or early stop.
- **Metrics** (window 151–210; reuse `.tmp/r19/metrics.py` and `.tmp/r19/analyze.py`, with only the run/range lists changed):
  1. **Plans:** plan rows by trigger, T1 events raised vs dispatched, `replan` flags with the act's why, quests created from text after a modal screen ended (LV19: q12).
  2. **Targets:** quests with `unexplored` and what they meant; place-target refusals (Hb).
  3. **Progress:** lab exit step (LV18g 152, LV19 156); `unexplored` closes and where; furthest Route 1 row reached (RAM y on map 12, lower = further north); new places minted.
  4. **Travel:** every cut during travel, with fate (crossing, stopped-open) and RAM before/after. Target: 0 false crossings (LV19: 1 at 175).
  5. **Segments:** from the new `segments` logs, dropped and absorbed by fate and screen type; one offset per (place, map) visit, including Pallet across visits.
  6. **Cost:** requests per world step and per battle step, by role (LV19 battle: about 3).
  7. Unchosen presses 0; lock ≥ 90%; odometer gap 0; walls split NPC/prop/scenery; walls on exit tiles crossed that step: target 0.
- LV20 informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r20\<WP>\` and gate scripts at `.tmp\r20\g\`. agent/ stays game-agnostic.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `341264d` and the §6 rules pasted. You alone review diffs, run gates, merge and run LV20.
1. **Wave 0:**
   - `git status` must show only the user's five files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`. HEAD is `341264d`.
   - Apply the §2.1 notebook block and commit it with this brief as `docs: r20 plan and approved contract amendment`.
   - Run the suite (expect 910 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - N3 in `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r20N3` (`-b r20/N3`); D2 in `...\r20D2` (no commit).
   - Review the N3 diff: P1/P2 only; no new class, module, production gate or option filter; act options unchanged.
   - Run T-N3. Allow one repair round with the exact failing output; stop if it fails twice. Merge `--no-ff`.
3. **Wave 2:**
   - If D2 refutes D, skip A2 and report.
   - Otherwise run A2 from the merged HEAD (`...\r20A2`, `-b r20/A2`), then T-A2, then merge.
   - Commit the §2.1 world block, without its last two lines if A2 was skipped.
4. Run **G20**, then **LV20** once.
5. **Report:** save `.tmp/r20/report.md` with:
   - commits and net lines per file;
   - tests deleted/added/changed (before/after);
   - D2's findings, including the F options for the user;
   - the gate table;
   - LV20 metrics against LV19 and LV18g;
   - deviations and open issues (the fake top-wall door and the "shop" label stay open by scope).
   Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r20 result`. Never push.
- **Stop and ask the user when:**
  - a §2 point cannot work as written, or D2 contradicts a design point;
  - a non-pre-approved assertion fails after one repair;
  - the golden data changes;
  - T18F/G/H/I or T18-O2 change, or T18-O3 changes other than as allowed;
  - the ledger would pass 10789;
  - `git status` differs from wave 0's expectation;
  - a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### N3 block
```
You are a gpt-6-luna HIGH worker on Avatar round r20, WP N3 (act-flagged replanning; unexplored closes on new ground). Worktree: <WORKTREE> (branch r20/N3 from 341264d). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r20.md §1 R1/C, §2 P1-P2, §3 N3; docs/plans/contracts.md "notebook and act"; agent/act.py act (schema, reply line, pick); agent/loop.py _learn, _flush_conversation, _event, _plan_trigger, the plan block in step, the act call and trace row; agent/notebook.py observe and the unexplored close; agent/space.py _new, _save; agent/world.py explore_dirs (span).
Build exactly P1 and P2 with ponytail ceiling comments. The replan flag is one optional boolean in the act schema and reply line; options, "i" and the follow-up button/type prompts are unchanged. No new class, module, gate or option filter; no game words.
Pre-approved test changes are exactly §3 N3. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r20\N3\]
Done when: suite green; one commit "N3 act-flagged replanning, unexplored closes on new ground"; report diff per file with net lines, tests changed (before/after) and added, the new act prompt reply line, and the suite summary line.
```

### D2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r20, WP D2 (offline diagnosis, no code). Worktree: <WORKTREE> (from 341264d; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r20.md §1 D/F, §3 D2; scripts/check_odometer.py; .tmp/r19/D/diagnosis.md and h3_per_frame.csv (the per-frame table pattern); agent/world.py travel, _odometer_drain, _odometer_fold, odometer_start, _land; agent/space.py mark_exit; agent/memory.py match_place; agent/skills.py menu/select paths; .tmp/r19/haiku/battle.txt.
Do the D2 tasks. Copy every runs/ file you read to .tmp/r20/D/ first; never write in runs/. Zero model requests; never edit agent/ or tests/; never run scripts/run_agent.py. RAM reads are allowed in your scratch scripts only.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r20\D\]
Done when: .tmp/r20/D/diagnosis.md states D confirmed/refuted with the per-crossing tables, whether P3 as written fixes it (or what would), and the F breakdown with 2-3 options and savings; report its path and a 10-line summary.
```

### A2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r20, WP A2 (one meaning for arrive). Worktree: <WORKTREE> (branch r20/A2 from <MERGED HEAD>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r20.md §1 D, §2 P3, §3 A2; .tmp/r20/D/diagnosis.md; agent/space.py mark_exit, exit_to; agent/world.py travel (both crossing sites), _land, _odometer_fold.
Build exactly P3 as amended by D2's diagnosis (if D2 says P3 as written is wrong, stop and report instead). One flag in mark_exit, one branch at the travel landing sites, the absorbed stat. No new class, module or gate.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r20\A2\]
Done when: suite green; D2's tables re-derived on your tree show one Pallet offset; one commit "A2 settled arrivals absorb post-cut motion"; report diff, tests added/changed, suite line, re-derived table.
```
