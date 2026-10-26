# Executor brief: Avatar round r23 — the map stops treating the unknown as floor, status is remembered, names follow evidence, the odometer log is honest (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `8170f0a` (the r22 result). All check data is read-only:
- `runs/m1_r22_odo` (LV22, steps 211–270);
- `runs/m1_r21_odo` (LV21; holds the checkpoint-210 start used by LV21 and LV22);
- `.tmp/r22/` (report, metric scripts);
- `.tmp/r23/E2/` (offline replay of LV22's explore steps: `report.md`, scripts, JSON);
- `.tmp/r23/E4/mine.py`, `.tmp/r23/E5/q*.py` (evidence scripts).

Scope: mechanical errors only. The agent's **sense of direction** (it walked back into old ground for 16 of LV22's 60 steps) is the next round. It is **not** in r23. That includes the fact that two scrolling maps share one place id.

## 0. Game-agnostic, modular by capability (applies to every WP)
- agent/ keeps no game words and no RAM/ROM reads (`tests/agent/test_boundaries.py`). Evaluator scratch may read RAM.
- Every r23 mechanism switches on from a **capability the game shows at runtime**, never from a game name or a per-game flag. A game without that capability gets the mechanism fully inert, with no behaviour change. Each WP adds one test proving this.

| Module | Active when | Inert when | Files |
|---|---|---|---|
| T terrain (route costs, wall evidence, explore targets, walk end) | the env has a tile grid (`tile_px` set) | `tile_px is None` (pointer/menu games): `explore_dirs` returns `{}` as today, `route` unused | space.py, world.py, loop.py, predict.py |
| S status meters | the perceiver reports `meters` on some screen | no meters ever perceived: no Status line, notebook unchanged | perceive.py, notebook.py, loop.py |
| N place names by evidence | any place that has a `called` name | no `called`: label unchanged | world.py, loop.py (Here line) |
| O odometer fate log | a scrolling view is tracked (odometer active) | no odometer: no change | world.py |

- No new config keys and no per-game switches. The game brief JSON (`agent/briefs/<game>.json`) keeps holding only controls and notes.
- `# ponytail:` ceiling for the brief: when a second game needs to turn a module off on purpose, add one optional brief key read in `agent/brief.py`. Not before.

## 1. Verified diagnosis (Opus, from LV22 offline replay; zero requests)
**G2/G3: the map treats unknown as floor, and wall evidence is not counted as learning.**
- **F1, unknown cells are routed as floor.** `route()` (`agent/space.py:55-78`) refuses only a cell with `floor is False`, a blocked edge, an obstacle entity or a wall look. A cell with no tile row is free. Of the 32 bumps during exploration in LV22 (211–270): 0 into known floor, 20 into seen-but-unclassified cells, 12 into never-seen cells. RAM confirms every refusal.
- **F2, a failed explore re-offers its failed target.**
  - `explore_dirs` (`world.py:1526-1538`) picks, per direction, the shortest route to any `unexplored()` cell and ignores `fails`.
  - One bump writes a wall on the pressed-from edge only. The target cell X stays in `unexplored()` (`world.py:78-87`, a union over frontier edges).
  - So the same X is offered again, now `failed`, sometimes under a different direction word because `_direction` depends on the current position.
  - At steps 255–257 all four rows were `failed`: UP→(12,-6) last failed at 215, LEFT→(-6,13) at 247, DOWN→(-1,15) at 254, RIGHT→(8,15) at 252.
- **F3, a wall is not novelty.**
  - `predict.result` returns `failed` for a blocked skill unless `novel` (`predict.py:12-13`).
  - `novel` (`loop.py:369`) counts only new seen cells (`world.py:362`), never a newly written blocked edge.
  - So a walk that taught the map a wall is scored a failure and leaves a fail mark (`loop.py:403`).
- **F4, the walk overshoots its target.** `explore_dirs` appends `[d] * span` presses after the route (`world.py:1537`) without checking them. At step 212 the walk passed target (0,-12) and bumped a remembered stone at (0,-10).
- The D3 r22 finding stands: the north blocks are real terrain. The fix is runtime evidence only, keeping uncertainty for unseen cells.

**G4: status is perceived but not remembered.**
- **F5.**
  - The perceiver already transcribes on-screen meters as plain text. For example, LV21 step 262 read "CHARMANDER :L6 HP: 2/ 21", and the act model saw it in "Text on screen" yet kept attacking (LV21 263–270).
  - Once the screen changes, nothing carries the value: no schema field (`perceive.py:38-49`), no memory, no focus line.
  - Parsing text is unsafe: "27/35" is PP on the same screen (LV21 step 260), and "3/\n21" splits across lines (LV22 step 265).

**G5a: a picture guess is the permanent place name.**
- **F6.**
  - `Memory.name_place` keeps the first name ever read (`memory.py:294-300`). The perceiver guessed "shop" for the lab interior at step 87 (`p_place_5`).
  - Later game-text names land in `called` at tier `text`. `World._shown` (`world.py:975-977`) then prints `shop, maybe OAK's Lab`. That string was in 29 of 61 LV22 act prompts.
  - `loop.py:169` also tags the Here line "(a name read from the picture)" from `name`.

**G5b: the odometer's "dropped" counter disagrees with its own segment log.**
- **F7.**
  - LV22's `odometer.dropped` counter is 80 (steps 233/243/269), yet 0 logged segments carry fate `dropped`. Fates logged: folded 103, held 61, untagged 24.
  - Positions are not affected: per-step odometer px equal the RAM deltas at 233 and 269.
  - Likely cause: `_join_odometer_segments` (`world.py:508`) builds copies, so `_fate` tags copies rather than the logged originals. Not yet proven.
- **F8 (unexplained; diagnose before any code).** Step 220: the RAM delta is (-3,+7) tiles, but the odometer reports px (+4.5,+7) tiles. That is a 7.5-tile x error on the step before a battle starts, perhaps the battle-entry animation read as scroll. Later visit offsets look consistent, so the error may have been dropped. Not verified.

## 2. Design
### T1, three-state routing: walked floor, unknown, blocked (F1)
- `route()` becomes a cost search: `heapq` Dijkstra over the same neighbours and the same refusal rules.
- Costs:
  - stepping onto a cell with `floor is True` costs 1;
  - any other non-refused cell (unknown or unclassified) costs `UNKNOWN_COST = 3`.
  - `# ponytail: one constant; tune if detours over known floor grow too long.`
- The strict→non-strict fallback, `occupied`, goal exceptions and bounds are unchanged. Paths over known floor are identical to today's BFS shortest paths.
- **Why:** it keeps uncertainty about unseen ground (D3's proposal) without a classifier. Walked ground is preferred, unknown ground is a guess the next bump corrects, and known walls stay walls.
- **Rejected:**
  - (a) refusing unknown cells outright: nothing new could ever be explored;
  - (b) a vision walkability classifier: a model or game-specific guess where runtime evidence exists;
  - (c) ROM collision data: game-specific, forbidden in agent/.

### T2, a new wall is novelty (F3)
- `World` counts blocked edges **newly added** this step (in the fold path, `space.fold` around `space.py:26-28`; count only `direction not in row["blocked"]` → appended) into a per-step count.
- Loop `novel` (`loop.py:369`) also becomes true when that count is above 0.
- A walk that wrote a wall is then `changed`, not `failed`, and sets no fail mark.
- The existing safety net stays: `loop.py:389` fails a repeated blocked intent even when it revealed something.
- **Bounded:** every edge can be written once, so the count cannot repeat for the same wall.

### T3, explore never re-offers a failed target (F2)
- `explore_dirs(place, fails=None)`: when choosing a direction's target, skip candidates whose explore fail key (the exact key the option and the loop use, `explore:{place}:{x},{y}`) is in `fails`. Take the next-best route in that direction.
- If every candidate in a direction has failed, return that direction's best failed candidate as today, so the row shows `failed` honestly. No row is hidden.
- Callers pass the loop's `fails`: `World.options` (`world.py:1495`) and `Loop._unexplored` (`loop.py:512-513`).
- **Why:** a failure belongs to the thing that failed (the r22 contract). The row is a direction, so it should point at an untried thing whenever one exists.

### T4, the walk past the target stops at known obstacles (F4)
- Truncate the appended `[d] * span` at the first press whose next cell `route()` would refuse, using the same refusal predicate and no second copy of the rule.
- Factor that one predicate out of `route()`'s neighbour test if needed; reuse beats duplication.

### S1, status meters as a perception field and one notebook memory (F5)
- **Perception.** The schema gains optional `"meters": [{"name": str, "value": number, "max": number|null}]`. One prompt sentence, game-neutral:
  > `- "meters": every labelled gauge or counter on screen that measures something about a character or the player (a bar or "value/max" numbers), with its owner and label as name, e.g. {"name": "<owner> <label>", "value": 7, "max": 20}; [] when none.`
  - `normalize` keeps only well-formed rows.
  - Bump `Loop.prompt_version` (`loop.py:36`).
- **Memory.** The notebook keeps `status: {name: {"value", "max", "step"}}`, the latest reading per name, persisted with `notebook.json`. Absent in old checkpoints means `{}`. The loop updates it once per perceived frame.
- **Prompts.** `render_focus` (act) and `render` (planner, `plan.py:55`) add one line when `status` is non-empty: `Status (last seen): <name> <value>/<max> (step N); ...`, most recent first, at most 4 names.
  - No thresholds, no rules, no "heal" advice. The model decides (agent-autonomy rule).
- **Rejected:**
  - (a) regex over text: game-specific and confused by PP-style ratios;
  - (b) carrying the last battle screen's text forward: stale and unlabelled;
  - (c) a mechanical "low health" gate: forbidden by standing rules.
- **Gate H1, before S1 merges:** the perceiver must read meters correctly on recorded frames (§3).

### N1, a place is shown under its strongest name (F6)
- Display ranking for a place: a `called` name at tier `said` or `text` beats the perceiver's picture `name`.
- `_shown`/`place_label` print `called` and drop the `, maybe …` form. The picture name stays in `name`/`aliases` for matching (`world.py:859` already matches `called`, `name` and aliases).
- `loop.py:169` tags the Here line "(a name read from the picture)" only when no `called` name is shown.
- Identity is untouched: `name_place` still keeps the first name as the stored `name`. Only display changes.
- **Why:** this is the same evidence ranking entities already use (`Memory.call`, `memory.py:188-194`). A guess from a picture is the weakest claim.
- **Risk:** a wrong `text`-tier claim is shown. It was already shown as "maybe", and a later same-tier claim replaces it as today.

### O1, fate tags land on the logged segments (F7)
- Whatever `_join_odometer_segments` builds, the `_log` dict of every source segment must receive the fate its segment met. Share the `_log` dict, or tag the originals.
- Then `odometer.dropped` equals the number of logged segments with fate `dropped` on every step, as long as the 40-segment log cap is not reached.
- No change to folding or position.

### D4, step 220 odometer error (F8): diagnosis only
- Zero requests. From LV22 frames, presses and truth, find:
  - which segment(s) carried the +4.5-tile x;
  - whether they were folded into `pos` or a wall;
  - why the scroll estimate was wrong (the battle-entry flash or wipe read as scroll?).
- Propose a fix (no code). If the error reached a stored position or wall, flag it as the first item for r24.

## 2.1 Contract amendments (approved by the user issuing this brief; the orchestrator commits them in wave 0)
Under `## space` (end of section):
```
- r23: route is a cost search: walked floor (floor True) costs 1, any other non-refused cell costs UNKNOWN_COST; refusal
  rules are unchanged. The walk-on past an explore target stops at the first cell route would refuse.
```
Under `## world` (top of the class comment block):
```
        # r23: a newly written blocked edge counts as novelty for the step. explore_dirs takes the loop's fails and
        # skips failed frontier targets while an untried one exists in that direction. A place displays its called name
        # (said or text tier) before its picture name; identity and matching are unchanged. Odometer fate tags land on
        # the logged segments. All grid mechanisms are inert when tile_px is None.
```
Under `## perception` (after the r22 line):
```
- r23: perception may carry "meters": [{name, value, max}] for labelled gauges/counters on screen; [] when none.
```
Under `## notebook and act` (end of section):
```
- r23: the notebook keeps the latest reading per meter name (status) and both focus renders show one "Status (last
  seen)" line when any exists. No thresholds or advice.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| T | T1–T4 + tests | agent/space.py (`route`, `fold` new-wall count), agent/world.py (`explore_dirs`, its call in `options`, novelty count), agent/loop.py (`novel` ~369, `_unexplored` ~512), agent/predict.py only if needed, tests/agent/test_space.py, test_options.py, test_loop.py, test_world.py | wave 0 | gpt-6-luna HIGH |
| D4 | F8 diagnosis | `.tmp/r23/D4/` only | wave 0 | gpt-6-luna HIGH |
| H1 | meter measurement scorer (prepare only; orchestrator runs it) | `.tmp/r23/H1/` only | wave 0 | gpt-6-luna HIGH |
| NO | N1 + O1 + tests | agent/world.py (`_shown`, `place_label`, `_join_odometer_segments`/`_fate` path), agent/loop.py (Here line ~169 only), tests/agent/test_world.py, test_memory.py or test_options.py as needed | T merged | gpt-6-luna HIGH |
| S | S1 + tests | agent/perceive.py, agent/notebook.py, agent/loop.py (one status update after perception; `prompt_version`), agent/plan.py only if `render` needs it, tests/agent/test_perceive.py, test_notebook.py, test_act.py/test_plan.py as needed | NO merged, H1 passed | gpt-6-luna HIGH |

### Pre-approved test changes (list every one before/after)
- **T:**
  - assertions on exact `route()` paths where an equal-length BFS path now differs because of cost ties, or where a longer path over known floor now beats a shorter path over unknown cells;
  - explore option `to`/target assertions where a failed target is now skipped;
  - verdict assertions that change from `failed` to `changed` because the step wrote a new wall;
  - the r22 T18G route-mock `to` field stays as approved.
- **NO:**
  - place label strings `"<name>, maybe <called>"` → `"<called>"`;
  - Here-line tag assertions;
  - odometer fate/counter assertions that encoded the copy bug.
- **S:**
  - the perceiver schema/prompt snapshot assertions may add `meters`;
  - focus/render snapshot assertions may add the Status line.
- Any other failing assertion: stop, revert uncommitted edits, report. Never set `W0_REGEN`. If golden data changes beyond additive fields, stop and ask.

### New tests (minimum)
- **T:**
  1. `route` prefers a 5-step path over known floor to a 3-step path through unknown cells, and still crosses unknown cells when no floor path exists.
  2. A step that writes a new blocked edge makes `novel` true and the verdict `changed`; bumping the same edge again does not.
  3. With `fails` holding a direction's best target, `explore_dirs` returns the next-best target in that direction, and the option row is `new`.
  4. With every target in a direction failed, the row is still present and `failed`.
  5. The walk past the target stops before a remembered static entity and before a blocked edge.
  6. **Modularity:** with `tile_px=None`, options contain no explore rows and no T code path runs (no exception, same options as before).
- **NO:**
  1. A place with picture name "shop" and `called` "Lab X" (tier text) is labelled "Lab X". The Here line has no picture tag. `resolve("shop")` still finds it.
  2. No `called`: the label is unchanged.
  3. A synthetic step joins segments and drops some: the counter equals the number of logged `dropped` fates.
  4. **Modularity:** no odometer, no change.
- **S:**
  1. `normalize` keeps valid meters and drops malformed ones (missing name, non-numeric value).
  2. Notebook status keeps the latest reading per name, survives save/load, and an old notebook without `status` loads as `{}`.
  3. Act focus and planner render show one Status line with at most 4 names, most recent first.
  4. **Modularity:** never perceiving meters leaves focus and render byte-identical to today.

### H1 measurement (prepare in the H1 WP; the orchestrator runs it after NO merges; at most 20 model requests, charged to the ledger)
- **Frames:** 15 recorded battle frames from `runs/m1_r21_odo` and `runs/m1_r22_odo` where a meter is visible. Include LV21 steps 256, 260 (PP confounder), 262, 263, 264 and 270, LV22 step 265 (split line), and full-HP and mid-HP frames.
- Copy the frames to `.tmp/r23/H1/frames/` first.
- **Ground truth (evaluator only):** the player-side meter value/max decoded from `truth.jsonl` `screen_text` for that step (RAM tile text). Where `screen_text` lacks it, read the value from the frame by eye and record it in `truth-manual.json` with the reason.
- **Prompt:** the live perceiver prompt plus the S1 meters sentence and schema, with `agent.observer.for_model` scaling as at runtime.
- **Pass:**
  - on ≥ 13 of 15 frames, a meter whose name contains the player character's displayed name has the exact value and max;
  - on 0 frames is a PP-style counter reported as that character's meter.
- Freeze the prompt, schema and scorer before the paid run. No retuning after results.

## 4. Gates
| Gate | Pass |
|---|---|
| T-T | Suite green (baseline at `8170f0a`: 944 passed, 1 skipped); changed assertions only as pre-approved. Offline LV22 replay (reuse `.tmp/r23/E2/` scripts; zero requests): at steps 255–257, at least one explore row is `new`; no replayed explore target equals a target already in `fails`; every replayed explore route that has an all-floor alternative within +2 steps uses it; step 212's walk does not reach (0,-10). |
| D4 | `.tmp/r23/D4/diagnosis.md` states the F8 cause with frame/segment evidence and whether it reached memory. |
| T-NO | Suite green. On a copy of `runs/m1_r22_odo/checkpoint/000270`, `place_label("p_place_5")` contains no "shop" and no "maybe". Replaying LV22's trace odometer through the fixed path, or a synthetic equivalent, gives counter == tagged dropped. |
| H1 | ≥ 13/15 exact, 0 PP confusions, ≤ 20 requests. |
| T-S | Suite green. Offline: a checkpoint plus one recorded battle perception with meters yields the Status line in the act prompt captured by the `om_act_size`-style capture stub (zero requests). |
| G23 | Merged HEAD: full suite green. T18F/G/H/I PASS. T18-O2 and T18-O3 unchanged. Golden unchanged (additive fields excluded as in r20–r22). |

If H1 fails: skip S, report the scores, keep T/NO, and still run LV23.

## 5. LV23 (one live run, orchestrator only, after G23)
- **Ledger:** 11077 at plan time. The user approved raising the round ceiling to **11260** (H1 ≤ 20 + LV23 cap 160 + margin). Read `runs/e3_budget.json` first; stop if the ledger would pass 11260. Viewer up.
- **Run:** copy `runs/m1_r21_odo` to `runs/m1_r23_odo` (it must not exist). Verify that checkpoint 210 is byte-identical. Then `--resume --from-step 210 --steps 60 --max-calls 160`.
  - It is the same start as LV21/LV22. One invocation; no rerun, extension or early stop.
- **Metrics** (reuse `.tmp/r22/metrics.py`, `analyze.py`, `walls.py`, `final_audit.py`, `summary.py` and the E2 replay scripts, changing only run/range lists):
  1. Explore choices by verdict; explore rows offered new vs failed per world step (LV22: 73/43; four-way failed at 255–257).
  2. Bumps during explore by target cell class: known floor / seen-unclassified / never-seen (LV22: 0/20/12). Fraction of explore route steps over known floor.
  3. Steps where an explore walk pressed into a remembered obstacle (LV22: ≥ 1, step 212).
  4. Status: the step the Status line first appears after a battle; Status value vs RAM `screen_text` at that step; world steps carrying it.
  5. Act prompts containing a picture-only name where a `called` name exists (LV22: 29/61 contain "shop").
  6. Odometer: counter vs tagged `dropped` per step (must match).
  7. Informational: Route 1 minimum RAM y (LV21/22: 24/20), world steps spent on map 0 after first reaching Route 1 (LV22: 16), battle requests/step (LV22: 2.55).
  8. Standing: unchosen presses 0; lock ≥ 90%; gap 0; actor and door walls 0; false crossings 0; one offset per visit; refuted-exit choices 0; menu reads 0 on determined selects.
- LV23 informs; it does not gate the commits. Direction (metric 7) is expected not to improve and is not a target.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r23\<WP>\` and gate scripts at `.tmp\r23\g\`. Plus §0 of this brief:
- game-agnostic;
- modular by runtime capability;
- one inertness test per WP;
- options never hide rows;
- no new class, module, config key, gate or filter, except the single `UNKNOWN_COST` constant.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, with fresh context and one WP each. Give each its §8 block with `<WORKTREE>`, `<BASE>` and the §6 rules pasted. You alone review diffs, run gates, run H1's model requests, merge and run LV23.
1. **Wave 0:**
   - `git status` must show only the user's five files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief, `docs/plans/prompt-r23.md` and `.tmp/`. HEAD is `8170f0a`.
   - Apply §2.1 and commit it with this brief and the prompt as `docs: r23 plan and approved contract amendment`.
   - Run the suite (expect 944 passed, 1 skipped). Record the ledger.
2. **Wave 1, in parallel:**
   - T in `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r23T` (`-b r23/T`);
   - D4 in `...\r23D4` (no commit);
   - H1 in `...\r23H1` (no commit).
   - Review the T diff: T1–T4 only; no hidden rows; one constant; inert without `tile_px`.
   - Run T-T. Allow one repair round with the exact failing output; stop if it fails twice. Merge `--no-ff`.
3. **Wave 2:**
   - NO from the merged HEAD (`...\r23NO`, `-b r23/NO`), then T-NO, then merge.
   - Run H1 yourself within its 20-request cap.
4. **Wave 3:** if H1 passed, S from the merged HEAD (`...\r23S`, `-b r23/S`), then T-S, then merge. Otherwise skip S and report.
5. Run **G23**, then **LV23** once.
6. **Report:** save `.tmp/r23/report.md` with:
   - commits and net lines per file;
   - tests deleted/added/changed (before/after);
   - the D4 finding;
   - the H1 table;
   - the gate table;
   - LV23 metrics against LV22 and LV21;
   - module inertness evidence;
   - deviations and open issues.

   Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r23 result`. Never push.
- **Stop and ask the user when:**
  - a §2 point cannot work as written, or the evidence contradicts it;
  - a non-pre-approved assertion fails after one repair;
  - the golden data changes;
  - T18F/G/H/I, O2 or O3 change;
  - the ledger would pass 11260;
  - `git status` differs from wave 0's expectation;
  - a worktree refuses removal without `--force`;
  - D4 finds the step-220 error reached stored positions or walls (report it before LV23; the user decides whether to run).

## 8. Lane paste blocks
### T block
```
You are a gpt-6-luna HIGH worker on Avatar round r23, WP T (three-state routing, wall evidence is novelty, explore skips failed targets, the walk past a target stops at known obstacles). Worktree: <WORKTREE> (branch r23/T from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r23.md §0, §1 F1-F4, §2 T1-T4, §3 T; .tmp/r23/E2/report.md; agent/space.py route, fold, frontier; agent/world.py unexplored, explore_dirs, options (the explore loop ~1495), _record_view novelty (~349-362); agent/loop.py novel (~369), the failure block (~385-410), _unexplored (~512); agent/predict.py result; tests/agent/test_space.py, test_options.py, test_loop.py.
Build exactly T1-T4 with ponytail ceiling comments. One new constant (UNKNOWN_COST) and no other new class, module, config, gate or filter. No row hidden. Factor route's refusal test into one predicate if T4 needs it; never duplicate it. No game words, no RAM.
Pre-approved test changes are exactly §3 T. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r23\T\]
Done when: suite green; the T-T offline replay passes (print the before/after explore rows at LV22 steps 212, 247-259 and the route cell classes); the tile_px=None inertness test passes; one commit "T three-state routing, walls are evidence, explore skips failed targets"; report diff per file with net lines, tests changed (before/after) and added, suite line.
```

### D4 block
```
You are a gpt-6-luna HIGH worker on Avatar round r23, WP D4 (offline diagnosis, no code). Worktree: <WORKTREE> (from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r23.md §1 F7-F8, §2 D4; agent/track.py Odometer; agent/world.py _odometer_drain, _odometer_fold, odometer_start, after; agent/observer.py scroll, cut, _blank; .tmp/r23/E5/q*.py.
Zero model requests. Copy every runs/ file you read to .tmp/r23/D4/ first; never write in runs/; never edit agent/ or tests/; never run scripts/run_agent.py. RAM reads only in scratch.
Task: for runs/m1_r22_odo step 220 (RAM delta (-3,+7) tiles vs odometer px (+4.5,+7) tiles), find which segment(s) carried the x error, which frames produced it (scroll estimates per frame pair), whether it was folded into pos, a wall or memory, or dropped, and why. Check whether the same pattern occurs on other steps that end in a battle start (LV21 and LV22). Propose a fix (no code).
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r23\D4\]
Done when: .tmp/r23/D4/diagnosis.md states the cause with evidence and the "reached memory: yes/no" verdict; report its path and a 10-line summary.
```

### H1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r23, WP H1 (prepare the meter-reading measurement; no model requests). Worktree: <WORKTREE> (from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r23.md §1 F5, §2 S1, §3 H1; agent/perceive.py (PROMPT, SCHEMA, normalize); agent/observer.py for_model; .tmp/r22/D/task2.py (the r22 highlight evaluator: copy its structure, ledger checks and incremental saving); .tmp/r23/E4/mine.py.
Build .tmp/r23/H1/task.py with --dry-run, --paid (orchestrator only; at most 20 posts, ledger checked against 11260 before each), and --score. Frames: §3 H1 list, copied to .tmp/r23/H1/frames/. Ground truth from truth.jsonl screen_text (evaluator-only decode); manual entries in truth-manual.json with reasons. Prompt = live perceiver prompt + the exact S1 meters sentence; schema = live schema + meters. Scorer: exact value/max for the player-character meter on >= 13/15, 0 PP confusions.
Copy every runs/ file you read first; never write in runs/; never edit agent/ or tests/; never run scripts/run_agent.py; never send a model request.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r23\H1\]
Done when: dry-run passes with synthetic replies (one correct, one PP-confused, one split-line); report the frame list with ground truth, the frozen prompt hash and the three commands.
```

### NO block
```
You are a gpt-6-luna HIGH worker on Avatar round r23, WP NO (place names follow evidence; odometer fate tags land on the logged segments). Worktree: <WORKTREE> (branch r23/NO from <MERGED HEAD>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r23.md §0, §1 F6-F7, §2 N1/O1, §3 NO; agent/memory.py name_place, call, place_name, place_aliases; agent/world.py _shown, place_label, resolve (~859), here, _join_odometer_segments (~508), _fate (~24), odometer_start, after; agent/loop.py the Here line (~168-169).
Build exactly N1 and O1. Display only: never change stored name, aliases or matching. No new class, module, config, gate or filter. No game words.
Pre-approved test changes are exactly §3 NO. Any other failing assertion: stop, revert, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r23\NO\]
Done when: suite green; T-NO passes on a COPY of runs/m1_r22_odo/checkpoint/000270 (print place_label for every place before/after); inertness tests pass; one commit "NO place names by evidence, honest odometer fates"; report diff per file with net lines, tests changed (before/after) and added, suite line.
```

### S block
```
You are a gpt-6-luna HIGH worker on Avatar round r23, WP S (status meters: perception field, notebook memory, one focus line). Worktree: <WORKTREE> (branch r23/S from <MERGED HEAD>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r23.md §0, §1 F5, §2 S1, §3 S; .tmp/r23/H1/ (the frozen meters sentence and schema; use them verbatim); agent/perceive.py (PROMPT, SCHEMA, normalize); agent/notebook.py (state, save/load, render, render_focus); agent/loop.py (_see, prompt_version ~36, where perception is first used each step); agent/plan.py (~55); agent/act.py.
Build exactly S1: one optional schema field and sentence (verbatim from H1), normalize, notebook status (latest per name, persisted, old notebooks load as {}), one Status line in both renders (at most 4 names, most recent first), prompt_version bump. No thresholds, advice, gates, filters, new class or module. No game words.
Pre-approved test changes are exactly §3 S. Any other failing assertion: stop, revert, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r23\S\]
Done when: suite green; T-S passes offline (print the captured act prompt's Status line); inertness test passes (no meters means byte-identical renders); one commit "S status meters remembered and shown"; report diff per file with net lines, tests changed (before/after) and added, suite line.
```
