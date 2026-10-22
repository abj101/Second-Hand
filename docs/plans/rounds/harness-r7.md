# Avatar harness round r7: revert P2's close-on-repeat; grounding options (proposals only)

**Summary (5 lines)**
1. The r6 run from boot (`runs/m1_r6_live_150`, HEAD `87eb424`) reached Oak's lab, got RED/BLUE, and never got the starter. From step 138 it looped talk-to-Oak → advance → advance until step 150.
2. **Cause 1, my P2.** A repeated reply closes the quest that targets the speaker ("talked"). The planner re-adds the same quest (q11, q12, q13, q15, q16), so:
   - every talk becomes `progress` and stays under "Worked here";
   - P4's "same reply / failed" never applies, because a closed quest wins over a failed action;
   - the stuck counter resets, so the stuck replan never fires.

   **V1 reverts only the close-on-repeat call.**
3. **Cause 2, grounding.** All 17 planner prompts in the lab contained "…They are inside the POKé BALLs… Choose!" (P1a works). Still, every quest said "Choose one of the 3 POKéMON offered by OAK", and `check display case` sat under "Not tried here" from step 129 to step 150. The perceiver calls the ball table "display case" (r3b also said "table with three spheres"). The r4 resume got the starter only because that run's perceiver said "pokeball". Nothing links a game-text noun to the thing on screen.
4. **G (proposals only, no code):** three grounding options. **Recommended: G1, the perceiver names things with the game's own words.** A 16-ask micro-check on recorded frames comes before any code.
5. **Ledger:** 6570 / 7854, so 1284 asks are left. V1 costs 0; the G micro-check costs about 16.
6. **Folded in (S):** perceived items flicker from step to step (8–22 on an unchanged lab scene), and memory locks in first-glance names and kinds, erases still objects after 4 missed frames, and lets tiles drift.
   - **SD** measures this;
   - **SB** keeps still things;
   - **SA** makes names and kinds follow a recent majority;
   - **SC** offers in-view remembered things like seen ones.

   **V3** removes the "OAK is ambiguous" planner errors.

| id | title | files | asks | approved |
|---|---|---|---|---|
| V1 | Revert P2's close-on-repeat (repeat speech never closes a quest) | `agent/loop.py`, `tests/agent/test_loop.py` | 0 | yes (user asked; one test replaced) |
| V2 | Regression case 11: the r6 lab churn (talk to OAK must read `failed` / `same reply`) | `scripts/points.json` | 0 | yes |
| G | Grounding options and a micro-check design | none | 0 | **proposal: user picks** |
| V3 | A name shared across places resolves to the one in the current place | `agent/world.py`, `tests/agent/test_world.py` | 0 | yes |
| SD | Entity-stability measure `scripts/eval_entities.py` (baseline r6, r3b; later LV) | `scripts/eval_entities.py`, `tests/agent/test_eval_entities.py` | 0 | yes |
| SB | Still objects are never `gone` from missed frames; their tile moves only on 2 agreeing sightings | `agent/world.py`, `agent/memory.py`, tests | 0 | yes (one assertion replaced, see SB) |
| SA | Shown name and kind follow the recent majority of sightings; non-self "player" is never a kind | `agent/memory.py`, tests | 0 | yes |
| SC | A remembered thing whose tile is in view is offered like a seen thing | `agent/world.py`, tests | 0 | yes |
| GM | G1 micro-check on recorded frames | none (scratch script) | about 16 | yes |
| G3 | Act goal shows the newest cited text whole | `agent/notebook.py`, `tests/agent/test_notebook.py` | 0 | yes |
| G1 | Perceiver context carries recent game text, so things get the game's names | `agent/loop.py`, `tests/agent/test_loop.py` | 0 | yes, if GM is a go |
| LV | Live resume from r6 checkpoint 115 (lab arrival), 35 steps | run data | ≤ 120 | yes |

---

## Evidence (r6, offline)

- **Quests** (`memory/notebook.json`):
  - q10 "Choose one of the 3 POKéMON offered by OAK." targeted `place:p_place_6`; it closed at 135 when the lab was reached.
  - q11, q12, q13, q15 used the same text with target `thing:e_customer` (Oak). Each opened and closed at the same step (138, 141, 144, 147) with reason `talked`.
  - q16 was open at 150.
- **Each churn plan** first tried `add … target "OAK"`, which was rejected as ambiguous (`thing:e_oak` outside vs `thing:e_customer` in the lab), then succeeded with the ref.
- **Trace rows** 138, 141, 144 and 147: `interact thing:e_customer`, verdict `progress`. The act prompt at 147 lists `5. talk to OAK … talked step 144` under **Worked here**, and `3. check display case, 2 right, 2 down` under **Not tried here**.
- **The act goal line** was cut: render_focus prints at most 160 characters of each cited text. At step 129 it read "…Here, come with me! BLUE: Gramps! I'm fed u", so the act model never saw "POKé BALLs" either.
- **BLUE's repeat** at 125 was correctly `failed` / `same reply 1×`: no quest targeted BLUE, so P4 applied. That is the behaviour V1 restores for Oak.

## V1: revert the close-on-repeat (`agent/loop.py` about line 292)

**Root cause.** In `Loop._settle`, inside `if said_step is not None:`, the line `self.notebook._save(self.notebook.talked(self.step_no + 1, f"thing:{tid}"))` (from P2, `d569e55`) closes the quest on a repeated line. A repeated line is not evidence that the quest's outcome happened.

**Change.** Delete that one line. Keep `Notebook.talked` (since P2, `heard` uses it for the first time a thing speaks) and keep the `says the same as before` note. A first-time reply still closes a "speaks to you" quest through `heard`.

**Tests.** Replace `test_repeated_reply_closes_quest_for_thing_and_marks_progress` (`tests/agent/test_loop.py`, added in `d569e55`) with `test_repeated_reply_does_not_close_a_targeted_quest`. Same setup, but it asserts:
- the quest stays `open`;
- the verdict is `failed`;
- `loop.fails[intent]["why"] == "same reply"`;
- no T3 event at that step.

The user's request approves replacing this one test. The `test_notebook.py` tests from P2 cover `heard` → `talked` and stay. Any other failing assertion is a stop.

## V2: regression case 11

Add to `scripts/points.json`: run `m1_r6_live_150`, step 141, checkpoint 140, situation "lab churn: talk to OAK after the same reply". Checks:
- `section` {`talk to OAK`, `failed`};
- `note_re` {`talk to OAK`, `same reply`};
- wrong `talk to OAK`;
- right `display case|ball|sphere|table`.

Run `--om points --dry --steps 141`. On HEAD it is expected to FAIL (today the row says `worked`); after V1 it is expected to PASS. Run it before and after V1 and report both.

(`--live` scoring of case 11 is part of the G check, not V.)

---

## V3: a name shared across places picks the current place's thing
**Evidence.** r6 plans 138, 141, 144 and 147 each failed once with `target "OAK" is ambiguous: thing:e_oak (outside house), thing:e_customer (shop)`, then retried with the ref, which cost a wasted op every plan.

**Change.** In `World.ref` (`agent/world.py` about line 596), when `_named_things(text)` returns more than one thing and exactly one of them is in `self.current_place`, return that one. `ties()` (line 607) returns `[]` in that case. Ties within one place stay ambiguous.

**Test.** Two things called "OAK" in two places: `ref("OAK")` from one place returns that place's thing, and `ties` is empty. Two in the same place: still a tie.

---

## S: entity stability (amendable, not fluctuating)

### Evidence (r6 lab, steps 115–150, offline)
- **Raw perception churns.** Over 10 lab steps where the player's RAM position did not change, the trace's entity list ranged from 8 to 22 (mean 15.5), and raw perceiver replies went as low as 4. The live viewer shows this per-frame list.
- **Names and kinds drift for the same remembered id:**
  - `e_customer` (Oak): NPC, customer, scientist, shopkeeper, person, player;
  - `e_display_case`: display table / display case / table with spheres;
  - shelves: "bench".
- **Memory keeps stable ids and tiles** (28 rows, no duplicate tiles), but:
  - **(a) First-mint name and kind are locked.** Oak's row is named "customer". `e_player` is a poster minted as kind `player` at step 115; it yields `check player, 1 left, 1 up`, the wrong A4 pick in case 7.
  - **(b) Still objects go `gone` after 4 missed frames** (`agent/world.py:300`, `MISS_TO_GONE = 4`). Seven lab rows went `gone`, including `e_item_1` [1,0] and `e_item_3` [3,0], minted at step 115 in a row with `e_display_case` [2,0]. Those are very likely two of the three Poké Balls (not proven against sprites).
  - **(c) Static tiles drift with projection noise** (`e_shelf_14` [-4,3] → [-5,4]; `e_counter_3` [-3,-2] → [-3,-1]).
  - **(d) The option list follows this frame's perception,** so a skipped thing changes form or drops out.
- **There are no spatial relations** (relations are only says / opens / moves_view / leads_to / does_nothing). This is out of scope for r7 (later S-E: derive "next to" or "on" from tiles).

### SD: measure first (`scripts/eval_entities.py <run-basename> [--from S --to S]`, 0 asks)
Read `trace.jsonl`, `truth.jsonl` and the append journal `memory/entities.jsonl` (one snapshot per change). Report per run, over world steps:
1. **Perceived count** on steps where the truth position is unchanged: mean, min, max.
2. **Shown-name flips per id:** the number of times the row's `name` changes in the journal. Today this is 0 by construction; after SA it should be small, not 0, because the name may move once from the first guess to the majority.
3. **Kind flips per id** (journal `kind`), and the number of non-self rows with kind `player`.
4. **False gone:** static rows that went `gone` and were matched (seen) again later in the same place, plus static rows `gone` at run end.
5. **Static tile moves:** the number of tile changes of non-actor rows.
6. **Option-label churn:** for steps with an unchanged truth position, the number of thing-option labels (`check …` / `talk to …`) that differ from the previous such step.

Test: a tiny hand-made trace and journal in `tmp_path`, with one known value per metric.

**Baseline:** run on `m1_r6_live_150` and `m1_r3b_live_150_20261021_150630` before any S code.

### SB: still things persist; tiles move only on agreement
- **Gone.** In the misses branch (`agent/world.py` about line 300, statics only: actors returned earlier at line 292), cap a static row at `stale`: `gone` is no longer reached from misses. `gone` for statics now comes only from direct evidence (the existing refuted / pixel paths).
  - **Approved assertion replacement:** `tests/agent/test_world.py:527` asserts `status == "gone"` after 4 misses. Replace it with `status == "stale"`, and assert the row still offers its option. Any other failing assertion is a stop.
- **Tile.** In `Memory.observe` (`agent/memory.py`, the `row["tile"] = list(tile)` update near the end), for non-actor rows only: move the stored tile only when the same new tile was seen in the previous sighting too. Keep `pending_tile` on the row; actors are unchanged.
- **Test:** a static row missed 6 times stays `stale` and offered. A one-off projection to a neighbour tile does not move it; two in a row do.

### SA: shown name and kind follow the recent majority
- **Name.** In `Memory.observe`, where a matched row gets its update, keep `recent_names` (the last 6 perceived names) on the row. Set `row["name"]` to the most common of those when it has at least 3 of 6 votes; otherwise leave it. Every name ever seen stays in `aliases`, so identity matching (which uses name + aliases) is unchanged. `called` (a game name) still wins in `World.label` and the legend, as today.
- **Kind.** Keep `recent_kinds` with the same rule. A perceived kind `player` on a non-self entity counts as `unknown` (the self entity is already blanked by `World._observation`).
- **Old rows** (from checkpoints) without these fields fall back to the current behaviour.
- **G1 interplay:** once G1 makes the perceiver say "POKé BALL", the 3-of-6 rule flips the name within about 3–4 sightings, while a single odd frame never does.
- **Test:**
  - one odd name among 5 consistent ones does not change `name`;
  - 4 of 6 new names do;
  - a non-self "player" never becomes kind `player`;
  - an old row without the fields still matches and updates.

### SC: a remembered thing in view is offered like a seen thing
In `World.options` (`agent/world.py` about lines 1060–1130), a remembered static row of the current place whose tile lies inside the current view (`_view_rect`, line ~250) and that this frame did not perceive gets the same verb and the same label form as a perceived one (`check X, 2 right, 1 up`), not the remembered or "not on screen" form. Nothing is removed. Test: a perceived shelf, missing from the next frame with the player unmoved, keeps an identical option label.

### Interactions to watch
- **Replay case labels may change** once SA renames things (the replay uses recorded perception, but the shown name is now a majority). If an A3 case fails only because a `label_re` names the old first-mint name, update that `label_re` to the new shown name of the same tile. Report every such change. Any other A3 change is a finding.
- **SB keeps more rows listed** (no false `gone`), so option lists get longer. SD's label churn and the act prompt size (`--om act-size`) are reported before and after.

---

## G: grounding options (proposals; no code until the user picks)

The goal: when the game's text names something ("POKé BALL", "potion", "key"), the planner and the act model can find that thing among the perceived objects. Today they see the perceiver's own word for it ("display case").

### G1 (recommended): the perceiver names things with the game's words
**What.** Add one line to the perceiver context in `Loop._see` (`agent/loop.py` about line 99, `ctx = GENERAL + "\n" + load_brief(...)`), carrying the most recent game-text lines verbatim, capped at about 300 characters, newest first, from `notebook.data["text"]`: "Recent game text: «…». If a thing on screen is something this text names, use the text's word as its name; otherwise name it as usual."
**Why this one.** It fixes the vocabulary at the source:
- entity names, legend lines, option labels, planner targets and the act goal then share the word, with no new matching code;
- it is generic, with no game names in code;
- it reuses the existing `context` argument of `perceive()`.

**Risks.**
- The perceiver may over-apply game words (a shelf called "POKéMON").
- The context changes when new text arrives, so slightly fewer model-cache hits (the in-loop reuse is keyed by frame hash and is unaffected).
- Bump `prompt_version` so old caches are not mixed.

**Cost.** No extra calls; about 80 extra prompt tokens per perceiver call.

### G2: the planner and act see each thing's other descriptions
**What.** Memory already keeps `aliases` (the perceiver's other names for the same entity across frames). Print them in the planner legend line and in the option label, e.g. `check display case (also: table with three spheres)`.
**Pro:** no perceiver change and no cache effect. **Con:** it depends on the perceiver ever saying something descriptive ("spheres") and on the planner inferring that spheres are balls. r6 never produced such an alias for the case.

### G3: show the cited text whole in the act goal
**What.** `render_focus` prints each cited text cut to 160 characters. Show the newest cited line whole within a budget (the same rule as P1a).
**Pro:** a tiny change, and the act model sees "inside the POKé BALLs… Choose!". **Con:** it does not solve the naming gap on its own. It is a cheap complement to G1 or G2.

**Recommendation: G1 + G3.** G1 makes the names line up; G3 makes sure the act model reads the instruction. G2 is a fallback if G1 over-labels.

### GM: G1 micro-check (about 16 asks, only after the user picks G1)
A scratch script (not committed) calls `agent.perceive.perceive(model, image, ctx)` on recorded frames, with today's context plus the G1 line built from that run's notebook text up to that step. Two tries per frame. Every HTTP post is charged through `scripts.run_agent._ledger`, as in the r4 micro-check.

**Frames:**
- lab with the ball table visible: r6 129, r6 147, r3b 112, r3b 139 (8 asks);
- controls with no ball: r6 41 (2F), r6 64 (1F), r6 88 (town), plus one r6 lab frame whose recorded perceiver reply has no table, sphere or ball entity (8 asks). r6 125 shows "table with spheres", so it is not a control.

**Go:**
- in at least 6 of 8 lab replies, the table or balls entity name contains `BALL` (case-insensitive);
- in at most 1 of 8 control replies, any entity is named with a game-text word it is not;
- the screen, text and entity count stay within ±20% of the recorded reply.

**No-go:** report the replies, and fall back to G2 + G3.

## Decision (user, 2026-10-21): G1 + G3, behind GM's go/no-go

### G3 code (no gate; 0 asks)
`Notebook.render_focus` (`agent/notebook.py` about lines 309–311) prints up to 3 cited text rows, each cut to 160 characters. Show the **newest** of those rows whole while the block stays within 600 characters; older rows keep the 160 cut (the P1a rule). Test: a 500-character cited row renders whole in the goal block.

### G1 code (only if GM is a go)
In `Loop._see` (`agent/loop.py` about line 99), after `ctx` is built, add the game-text line:
- newest-first rows of `self.notebook.data["text"]`, verbatim, joined with " / ", cut to 300 characters;
- the wording from G1 above;
- only when there is any text.

Bump `self.prompt_version` from `"e5-perceiver-11"` to `"e5-perceiver-12"` (line 36), so cached perception from the old prompt is never reused.

Tests:
- with notebook text, the ctx passed to `perceive` contains the line;
- with none, it does not;
- the 300-character cut holds.

If any existing test pins `e5-perceiver-11` or the exact ctx, stop and report.

### LV: live check from lab arrival (after G1 + G3 are committed)
Copy the r6 run, then resume from its checkpoint 115 (the first world step in the lab; checkpoints are every 5 steps):

```
cp -r runs/m1_r6_live_150 runs/m1_r7_resume115
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r7_resume115 --resume --from-step 115 --steps 35 --max-calls 120 --decide
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_r7_resume115
```

Report:
- first step with a party (truth);
- the quest texts and targets after 115;
- the entity names given to the ball table;
- the verdicts of repeat talks;
- asks spent.

**Success:** a party by step 150 and no talk-to-Oak churn.

If GM is a no-go: skip G1 and still commit G3, then run LV with G3 only and report it as such.

## Order
1. V2 (case 11 dry on HEAD: expect FAIL).
2. V1 (commit, suite).
3. V2 again (expect PASS, commit `points.json`).
4. V3 (commit, suite).
5. SD (commit), baseline on r6 and r3b.
6. SB (commit, suite).
7. SA (commit, suite).
8. SC (commit, suite).
9. G3 (commit, suite).
10. GM (about 16 asks).
11. G1 if GM is a go (commit, suite).
12. A3 dry on all 11 cases (report label_re updates).
13. LV.
14. SD on LV, next to the r6 baseline.

LV report additions: the SD metrics for steps 116–150, and whether the ball-table and item rows stay listed (not `gone`).

## Validity check (done 2026-10-21 on HEAD `87eb424`)
- Cited code is where the plan says:
  - `loop.py:292` (the P2 `talked` call);
  - `loop.py:36` (`prompt_version "e5-perceiver-11"`);
  - `loop.py:99` (the perceiver ctx);
  - `notebook.py:310-311` (the `[:160]` cut of cited rows);
  - `world.py:300` (static `gone` at `MISS_TO_GONE`), `:596` (`ref` single-match), `:607` (`ties`).
- Checkpoints: r6 has checkpoints at every 5 steps from 30 to 150 (140 and 115 exist).
- Fixed in this revision:
  - the GM town control (86 was 1F; now 88);
  - the lab control (125 shows the table);
  - SA vs G1 (a recency majority, so game words can win quickly);
  - the old-checkpoint rows SA must accept.
- Known and not fixed this round: spatial relations (S-E). A goal label once read "(done when: you enter outside house)" for target `place:p_place_6` at step 129; watch it in LV, and report the place label of `p_place_6`.

## Report format
- V1 commit and suite;
- case 11 before and after V1;
- the full A3 table on the 11 cases;
- asks (expected 0 for V);
- files edited.
