# Executor brief: Avatar round r22: failures that mean what they say, honest exits for the act model, one screen reader for menus (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `d18d5b5` (the r21 result). Check data, all read-only:
- `runs/m1_r21_odo` (LV21, steps 211–270, with `segments` and `seqs`);
- `runs/m1_r20_odo`;
- `.tmp/r21/` (`report.md`, `final-audit.json`, `walls.json`).

## 1. Verified diagnosis (Opus, from LV21; inferred claims are marked)
LV21 fixed walls, memory and crossings (0 actor or door walls, 0 false crossings, one offset per visit). But for the third run in a row the agent got no further north than RAM y 24 on Route 1. Of its 33 world steps:
- 20 were "check bush / hedge / crop / pillar / stone" (13 of them failed);
- 9 were exit walks;
- 2 were explore.

**F1, explore rows are permanently "Failed here" (verified):**
- The act prompt at step 249 lists all four explore rows under **Failed here**:
  - `explore UP … no response 1× (last step 156)`
  - `RIGHT … route blocked (163)`
  - `DOWN … (165)`
  - `LEFT … no response (63)`
- Checkpoint 250's `planner.fails` holds `p_place_4|approach|explore:p_place_4:up` from step 156, with `avoid [3,0]` and `blocker e_villager`. Those are Pallet tiles, roughly 20 rows south.
- **Mechanism:**
  - `World.options` keys an explore row as `explore:{place}:{direction}` (`world.py`, the explore loop in `options`).
  - `Loop` clears a failure only when *that same intent* later succeeds (`loop.py` ~411).
  - Pallet and Route 1 are one seamless place (`p_place_4`), so the first failure of each direction anywhere brands that direction for the rest of the run.
  - Failed rows sort last, so the model rarely picks them, and nothing ever clears them. The effect locks itself in.
- The stored `avoid` tile also follows the stale key into later routes (`loop.py` ~499, `held = fails[intent]["avoid"]`).
- Every other intent subject is a *resolved thing*: an entity, exit, place or button. Only explore is keyed by a direction that means a different target from every tile.

**F2, failure history is per object, so scenery floods "Not tried here" (verified):**
- `World.options` groups untried things by name ("check bush (nearest of 5)"). But a member with history (a failure, or "checked: nothing") leaves the group and becomes its own failed row.
- So the group row stays "new" no matter how many members of the same name failed in this place. At step 249: 1 bush group "new", 3 bush rows failed, plus crops, pillars and fence posts in the same shape.
- The model treats the "Not tried here" list as the place to look.

**F3, refuted exits still look untried to the act model (verified):**
- r21 P7 computes refutation ("ground continues beyond it", "not reached N×", "walked into N×"). But X1 preserved the existing option notes, so the reason reaches only the planner's `view`.
- Act rows still say `exit, never crossed` and are often state `new`: steps 217, 218, 234, 235 and 244 picked them from "Not tried here".
- `final-audit.json`: 7 choices of already-refuted exits, 4 of them travel trips.

**F4, menu reuse cannot work as built (verified from `511f579`):**
- `_menu_crop` boxes the perceived selectable points, padded by `tile_px`. Perceived `at` jitters between frames, so crop sizes differ and the `previous.size == crop.size` check fails.
- Grid menus (the 2×2 FIGHT/PKMN/ITEM/RUN) always read by design.
- Result: 1 of 14 reads skipped, and battle cost rose to 2.96 requests per step.
- Underlying issue: the screen is read **twice** per menu step. The perceiver reads it (pre-action perception), then `select` asks a second model call for items, cursor and layout. The perceiver schema has `selectable` but no highlight (`agent/perceive.py` ~34–44).

**F5, the north route is a 23-step detour for 2 cells (inferred; diagnose before designing anything):**
- At checkpoint 250, `unexplored()` has only 7 north candidates, and explore UP is labelled "23 steps, 2 unexplored cells".
- The seen store shows row −22 (RAM y 20) as seen but not open. Also, `space.route` in strict mode treats every remembered non-actor entity tile as an obstacle, and many Route 1 entities are "crop", "bushes" or "bush".
- Whether those are tall grass (walkable) or trees is the question.

**F6, risk carried into the next run:** LV21 ended in a battle with the starter at 2/21 HP. Nothing in the agent reads HP. LV22 therefore restarts from checkpoint 210 (see §5).

## 2. Architecture audit and design
### P1, explore intent is the frontier target, not the direction (F1)
- **Design:**
  - An explore row's subject (`ref`, the fail key and the trace intent) is `explore:{place}:{tx},{ty}`, the frontier cell `to` that `explore_dirs` already returns. The label keeps its direction wording.
  - The loop's own explore intent (`loop.py` ~179, today `fail_key(place, "approach", "unexplored")` with no ref) uses the same ref, so the option key and the executed intent agree.
  - Old direction keys are no longer produced, so they are inert. Leave them in checkpoints.
- **Why this is the root fix:** it puts explore on the same contract as every other intent. A failure belongs to the thing that failed: here, the frontier cell whose route failed.
  - A failed explore attempt already teaches the map (walls and `known` cells), and `unexplored()` stops offering cells that are known ("each costs at most one bump before `known` excludes it"). So the frontier moves on its own, and new frontier cells are honestly untried.
- **Alternatives rejected:**
  - (a) Age failures by steps or distance. It treats symptoms, adds a magic constant, and also forgets genuine failures of things that do not move.
  - (b) Split seamless places into regions. That is a large identity change; the stale key would still brand directions within one big room.
  - (c) Clear direction keys when new ground is seen. That is an ad-hoc event coupling, and it is still wrong across distant parts of one place.
- **Risk:** the frontier cell for a direction can change each step, so a dead direction might be retried. Bounded: each retry writes walls or `reach`, which shrinks the frontier. Ponytail ceiling noted.

### P2, failure history is per name within a place (F2)
- **Design:** in `World.options`, a name group's row carries the group's history:
  - state `failed` if any member in this place failed this verb, otherwise `worked` if any member was checked or talked;
  - one note: "N others named X here: nothing changed / no response".
  - Members with history keep their own rows as today. No row is hidden or removed, and order within groups is kept.
- **Why:** the model's choice is about *kinds* of things ("is checking bushes useful here?"). Per-object history answers a question it isn't asking.
- **Alternatives rejected:**
  - (a) Hide failed kinds. Hiding violates the options-never-hide contract.
  - (b) Rank scenery last. That is an ordering policy, i.e. a mechanical gate on the model.
  - (c) A planner rule "don't check scenery". That is game knowledge and a prompt band-aid.
- **Risk:** in a game where only one bush matters, the group row says "others did nothing", and the model may still try it. It sees the truth and decides.

### P3, refuted exits say so in the act options (F3)
- **Design:**
  - In `World.options`' `option()` and the exit rows, an exit whose `_exits` status is `refuted` gets state `failed`.
  - Its note replaces "exit, never crossed" with the existing `_exit_reason` text ("probably not an exit: ground continues beyond it" / "not reached 2×" / "walked into 1×").
  - A crossing (`uses > 0`) still wins.
- **Why:** one source of truth. The act and planner views must not disagree about the same exit. r21 kept them apart only to preserve assertions; this brief pre-approves those assertion changes (§3).
- **Alternative rejected:** a separate warning line in the act prompt. That duplicates the evidence and leaves the row's state wrong.

### P4, one screen reader: perception reports the highlighted choice, and `select` navigates by geometry (F4)
- **Design:**
  - The perceiver's entity schema gains an optional `"highlighted": true` for the currently highlighted selectable item (the cursor's item), described in the same sentence as `selectable`.
  - `select(ctx, item, perception)` uses the perception it already receives. If exactly one selectable entity is highlighted and the target item is among the selectables, it computes key presses from the two `at` points:
    - cluster selectable `at` values into rows and columns by rounding to the tile grid;
    - press vertically by row difference and horizontally by column difference.
    - This handles lists, rows and 2×2 grids with no layout field.
  - Otherwise it falls back to today's menu-read call, unchanged.
  - Delete B1's crop signatures and the `menu` store argument: no crop, no `screens.menu` cursors.
- **Why:** deletion over addition. The perceiver already reads every menu frame, so a second model read is duplicate work. Geometry from `at` is game-neutral, and a jittering `at` still clusters correctly into rows and columns.
- **Alternatives considered:**
  - (a) Fix B1's crop: snap to a fixed grid and compare by content. It still needs a stored cursor image per item, and grids still read.
  - (b) Have the act reply carry menu fields. That moves screen reading into the decision model, which mixes roles.
  - (c) Track the cursor from our own presses. The game resets cursors between menus, so the first read is still needed.
- **This is a new approach that replaces the user's earlier choice (a), so it needs the user's approval.** If it is not approved, drop P4 and M2 from this round and keep B1.
- **Precondition:** D3 measures highlight accuracy offline on the recorded LV21 battle frames (§3, D3 task 2) before M2 merges. Merge only if the highlighted item is correct on ≥ 13 of 14 recorded select frames.

### P5, F5 diagnosis only
D3 determines whether the north blockage is real map topology, mis-seen walkability (`_open_edge`), or strict-route obstacles from tall-grass entities. No code this round; it proposes a fix.

### 2.1 Contract amendments (approved by the user issuing this brief; the orchestrator commits them in wave 0)
Under `## world`:
```
        # r22: an explore option's subject is its frontier cell (explore:{place}:{x},{y}), never a bare direction.
        # A same-name group row carries the group's history in this place (failed/worked + one note). A refuted exit's
        # act option is "failed" with its refutation reason; a crossing always wins.
```
Under `## perception` (only if P4 is approved):
```
- r22: entities may carry "highlighted": true for the one selectable item under the cursor. select navigates by the
  selectables' grid geometry from the highlighted item to the target and reads the menu with a model call only when
  that is not determined.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| O2 | P1 + P2 + P3 + tests | agent/world.py (`options`, `option()`, `explore_dirs` callers), agent/loop.py (explore intent ~179 only), tests/agent/test_options.py, tests/agent/test_exits.py, tests/agent/test_loop.py | wave 0 | gpt-6-luna HIGH |
| D3 | F5 diagnosis; P4 highlight measurement | `.tmp/r22/D/` only | wave 0 | gpt-6-luna HIGH |
| M2 | P4 + tests (only if the user approved P4 and D3's measurement passes) | agent/perceive.py, agent/skills.py (`select`, delete `_menu_crop`/`_menu_signature`), agent/loop.py (the select call site only), agent/memory.py (drop menu cursor storage only), tests/agent/test_skills.py, tests/agent/test_perceive.py | D3, O2 merged | gpt-6-luna HIGH |

### Pre-approved test changes
- **O2:** assertions on:
  - explore row `ref`/`intent_key` strings (direction → cell);
  - same-name group row state and notes;
  - refuted-exit option state and notes ("exit, never crossed" → the refutation reason).
  List every change before/after.
- **M2:** the B1 tests are deleted (`test_select_reuses_matching_stored_menu_signature`, `test_select_different_menu_crop_reads_again_and_grid_always_reads`, and the menu-signature part of `test_observing_known_menu_keeps_model_order_and_cursor_signatures`). The perceiver schema test may add `highlighted`. List every change.
- Any other failing assertion: stop, revert, report. Never set `W0_REGEN`; if golden data changes beyond additive fields, stop and ask.

### New tests (minimum)
- **O2:**
  1. Two explore attempts toward different frontier cells in the same direction have different keys, and a failure of one leaves the other "new".
  2. The loop's executed explore intent equals the option's `intent_key`.
  3. A group of 3 same-name things with 1 failed member: the group row is "failed" with a note.
  4. A group with no history stays "new".
  5. Refuted exits (each reason) are "failed" with that reason; a crossed exit is "worked".
- **M2:**
  - List, row and 2×2 grid navigation from a highlighted item make no model call and press the right keys.
  - No highlight, or two highlights, falls back to the read call.
  - A jittered `at` (±0.3 tile) still navigates correctly.

### D3 tasks (copy every runs/ file you read to `.tmp/r22/D/` first; evaluator scratch may read RAM)
1. **F5, zero model requests.** For `runs/m1_r21_odo` checkpoint 250 and the frames of steps 238–250:
   - (a) From the ROM's map/collision data via `scripts/truth_pokered.py` helpers, or from RAM tile reads, find which RAM cells north of y 24 (x 0–17) are walkable.
   - (b) Map the agent's tile, seen and open cells and remembered entities onto RAM coordinates using offset [5,42].
   - (c) Explain the 23-step explore UP route and the 7 candidates: which cells strict `route` treats as obstacles and why (entity names/kinds), and what `_open_edge` concluded for the top strip.

   Conclude, with evidence: real topology, misread walkability, or entity obstacles. Propose a fix (no code).
2. **P4 precondition (orchestrator runs it with at most 30 model requests, charged to the ledger, after the user approves P4).**
   - Re-perceive the 14 LV21 select frames (`frames/NNNNNN.png` of the select steps in `.tmp/r21/final-audit.json` / the trace) with the perceiver prompt plus the `highlighted` sentence.
   - Score: exactly one highlighted selectable, and it matches the cursor item recorded by the menu read of that step. The pass mark is ≥ 13/14.
   - Also report whether row/column clustering of `at` gives the right press sequence to the chosen item for each frame.
3. Write `.tmp/r22/D/diagnosis.md`.

## 4. Gates
| Gate | Pass |
|---|---|
| T-O2 | suite green (baseline at `d18d5b5`: 934 passed, 1 skipped); changed assertions only as pre-approved; on checkpoint 250, offline (zero requests), `World.options` lists the explore rows as "new" and the bush group as failed |
| T-M2 | suite green; offline replay of the 14 LV21 select steps using D3's re-perceptions: the press sequence matches the recorded successful one on ≥ 13/14, with 0 menu reads |
| G22 | merged HEAD: full suite green. T18F/G/H/I PASS. T18-O2 and T18-O3 unchanged. Golden unchanged (additive fields excluded as in r20/r21). |

## 5. LV22 (one live run, orchestrator only, after G22)
- **Ledger:** 10913 / 17854 at plan time, plus D3 task 2 if run (at most 30). Read `runs/e3_budget.json` first. Cap **160**; **stop if the ledger would pass 11103**. Viewer up.
- **Run:** copy `runs/m1_r21_odo` to `runs/m1_r22_odo` (must not exist), then `--resume --from-step 210 --steps 60 --max-calls 160`.
  - Same start as LV21, which makes the comparison direct. Its checkpoint-210 memory is already the r21-cleaned copy, and its HP is healthy.
  - One invocation; no rerun, extension or early stop.
- **Metrics** (reuse `.tmp/r21/metrics.py`, `analyze.py`, `walls.py`, `final_audit.py`, with only run/range lists changed):
  1. Route 1 minimum RAM y. LV19/20/21: 24/25/24. Target: north of 24.
  2. World choices by kind (check / exit / explore / talk / travel) and verdict. LV21: 20/9/2/1/1.
  3. Explore rows offered as new vs failed per world step.
  4. Choices of refuted exits. LV21: 7. Target 0, unless the act's `why` names new evidence.
  5. Battle requests per step and menu reads per select. LV21: 2.96 and 13/14.
  6. New places minted; unexplored-quest closes.
  7. Standing: unchosen presses 0; lock ≥ 90%; gap 0; actor and door walls 0; false crossings 0; one offset per visit.
- LV22 informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r22\<WP>\` and gate scripts at `.tmp\r22\g\`. agent/ stays game-agnostic: no RAM reads, no game words. Options never hide rows.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `<BASE>` and the §6 rules pasted. You alone review diffs, run gates, run D3 task 2's model requests, merge and run LV22.
1. **Wave 0:**
   - `git status` must show only the user's five files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`. HEAD is `d18d5b5`.
   - **P4 is approved by the user (2026-10-26)**, gated on D3 task 2 scoring ≥ 13/14. Apply both §2.1 blocks.
   - Apply §2.1 (the perception block only if P4 is approved) and commit it with this brief as `docs: r22 plan and approved contract amendment`.
   - Run the suite (expect 934 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - O2 in `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r22O2` (`-b r22/O2`); D3 in `...\r22D3` (no commit).
   - Review the O2 diff: P1–P3 only; no hidden rows, no new class, module, production gate or option filter.
   - Run T-O2. Allow one repair round with the exact failing output; stop if it fails twice. Merge `--no-ff`.
   - If P4 is approved, run D3 task 2 yourself within its 30-request cap.
3. **Wave 2:** if P4 is approved and D3 task 2 passes, run M2 from the merged HEAD (`...\r22M2`, `-b r22/M2`), then T-M2, then merge. Otherwise skip M2 and report.
4. Run **G22**, then **LV22** once.
5. **Report:** save `.tmp/r22/report.md` with:
   - commits and net lines per file (expect M2 to be a net deletion in skills.py);
   - tests deleted/added/changed (before/after);
   - D3's findings and F5 proposal;
   - the gate table;
   - LV22 metrics against LV21 and LV20;
   - deviations and open issues (the shop label, HP awareness, held segment fates).
   Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r22 result`. Never push.
- **Stop and ask the user when:**
  - a §2 point cannot work as written, or D3 contradicts a design point;
  - a non-pre-approved assertion fails after one repair;
  - the golden data changes;
  - T18F/G/H/I, O2 or O3 change;
  - the ledger would pass 11103;
  - `git status` differs from wave 0's expectation;
  - a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### O2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r22, WP O2 (explore keyed by frontier cell; name-level history; refuted exits honest in act options). Worktree: <WORKTREE> (branch r22/O2 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r22.md §1 F1-F3, §2 P1-P3, §3 O2; agent/world.py fail_key, options (option() helper, plain_groups/far_groups, explore loop, exit rows), explore_dirs, _exits, _exit_reason, history; agent/loop.py the explore branch (~179), the intent capture (~227), the failure block (~390-416) and the avoid read (~499); tests/agent/test_options.py, test_exits.py.
Build exactly P1, P2, P3 with ponytail ceiling comments. No row is hidden or reordered within its group; no new class, module, gate or filter; no game words.
Pre-approved test changes are exactly §3 O2. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r22\O2\]
Done when: suite green; offline on a COPY of runs/m1_r21_odo/checkpoint/000250 (zero requests), World.options shows the explore rows as new and the bush group failed (print the option list before/after); one commit "O2 explore keyed by frontier, name-level history, honest exit options"; report diff per file with net lines, tests changed (before/after) and added, suite line.
```

### D3 block
```
You are a gpt-6-luna HIGH worker on Avatar round r22, WP D3 (offline diagnosis, no code). Worktree: <WORKTREE> (from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r22.md §1 F4/F5, §2 P4/P5, §3 D3; agent/world.py unexplored, open_cells, unseen_edges, _record_view, _open_edge, explore_dirs; agent/space.py route, frontier; agent/perceive.py; agent/skills.py select; scripts/truth_pokered.py.
Do D3 task 1 fully (zero model requests). For task 2 prepare everything (frame list, prompt with the highlighted sentence, scorer, clustering check) but do NOT send model requests: the orchestrator runs it. Copy every runs/ file you read to .tmp/r22/D/ first; never write in runs/; never edit agent/ or tests/; never run scripts/run_agent.py. RAM/ROM reads only in your scratch scripts.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r22\D\]
Done when: .tmp/r22/D/diagnosis.md states the F5 cause with RAM-mapped evidence and a fix proposal, and the ready-to-run task-2 script with its command; report its path and a 10-line summary.
```

### M2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r22, WP M2 (perception reports the highlighted choice; select navigates by geometry). Worktree: <WORKTREE> (branch r22/M2 from <MERGED HEAD>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r22.md §1 F4, §2 P4, §3 M2; .tmp/r22/D/diagnosis.md (task 2 result); agent/perceive.py (SYSTEM text, schema, normalization ~83); agent/skills.py select, _menu_crop, _menu_signature; agent/loop.py the select call; agent/memory.py menu cursor storage.
Build exactly P4: one optional schema field and one sentence; select's geometry path with fallback to the existing read; delete the B1 crop/signature code and its storage. No new class or module.
Pre-approved test changes are exactly §3 M2. Any other failing assertion: stop, revert, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r22\M2\]
Done when: suite green; T-M2 offline replay passes; one commit "M2 select navigates from the perceived highlight"; report diff (expect a net deletion in skills.py), tests deleted/added/changed, suite line, replay table.
```
