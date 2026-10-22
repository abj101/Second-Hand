# Avatar harness round r8: same-reply match, blocked verdict, and identity/grounding/map options

**Summary (6 lines)**
1. **The r7 LV** (`runs/m1_r7_resume115`, HEAD `d81df01`, steps 116–150, 80 asks) got no starter. The talk-to-Oak loop is gone. Instead, 19 rows in 115–150 were route-blocked (`eval_moves`), and the agent ended at RAM (7,4), right under a ball, without checking it.
2. **Two small bugs, approved pending review:**
   - **B1:** "same reply" needs a byte-exact match. LV 129 `OAK: Now, RED, which POKéMON do` and 132 `… do ▼` differ only by `▼`.
   - **B5:** a blocked route that scrolls new cells into view gets verdict `progress`. 8 LV rows, so the stuck counter never fills.
3. **The main finding (B3, B4, B6):** SA lets a row change between actor and static by a kind vote, so a static row can be taken over by a walking character. In LV, 8 lab rows changed class (OAK's row started as the static `item 1`; the ball table's row became `character`). Those 8 rows account for 45 of the lab's tile moves. The real OAK, perceived in the next frame as `clerk`, matched a different row, so the act prompt said `go to OAK, not on screen` while OAK stood beside the player (B6). Smoothing noisy names is fine; letting a vote change *what kind of thing* a row is is the flaw.
4. **Decision items (user picks, options below):** identity I (gap 2), quest closing Q (gap 5, also B2), verdict V (gap 4), tile map T (gap 3), grounding G (gap 1), eval replay R (gap 6, also B8). Every option is 0 asks except G (16-ask micro-check).
5. **Ledger:** 6666 / 7854, 1188 left. This round: at most 120 (LV), since G was not picked.
6. **B9 (no code):** act-size is measured on LV checkpoint 140 from now on. Old checkpoints lack the S fields, so their before/after numbers are identical by construction.

| id | title | files | asks | approval |
|---|---|---|---|---|
| B1 | A reply counts as the same when it matches after removing ▼/▶ and whitespace, or one is a prefix of the other (≥ 8 chars) | `agent/loop.py`, `tests/agent/test_loop.py` | 0 | approved pending review |
| V2 | Regression case 11 from LV (checkpoint 130, step 135): OAK reads `failed` / `same reply` | `scripts/points.json` | 0 | approved pending review |
| B5 | A blocked or failed action is never `progress`; blocked plus new cells is `changed` | `agent/predict.py`, `tests/agent/test_predict.py` | 0 | **needs the assertion change below** |
| RP | Perception-only identity replay (scratch, 0 asks): baseline for I | `.tmp/r8/` only | 0 | approved pending review |
| I | Identity: a row's class (actor / static) is not decided by votes | see options | 0 | **user picks** |
| Q | Quest closing on a thing's reply (B2) | see options | 0 | **user picks** |
| V | Verdict: what counts as progress (beyond B5) | see options | 0 | **user picks** |
| T | Tile map: routes through cells that are void or unknown | see options | 0 | **user picks** |
| G | Grounding: game words for on-screen things | see options | ≤ 20 | **user picks** |
| R | Points replay that follows changed behaviour (B8) | see options | 0 | **user picks** |
| LV | Resume r6/115 with r8 code, 35 steps | run data | ≤ 120 | yes, after the picked items land |

---

## Evidence (LV, offline, 0 asks)

- **Verdicts.** Rows 118, 119, 120, 124, 141, 142, 143 and 148 have `execution: blocked` and `verdict: progress`. Every one scrolled the view (`view_scrolled`), so `World.novel > 0`, and `predict.result` returns `progress` before it checks the status.
- **Same reply.** The `says` relations for `e_item_1` (OAK): 129 `'OAK: Now, RED, which POKéMON do'` and 132 `'OAK: Now, RED, which POKéMON do ▼'`. The lookup at `loop.py:293` compares `o` exactly, so 132 is "new".
- **Quest closed by an unrelated reply (B2).** q11 "Select a POKéMON from the 3 offered by OAK" targeted `thing:e_player`. That row is the desk with the encyclopedia, shown as "table". At 138 the agent checked it, and the reply at 140 ("It's encyclopedia-like, but the pages are blank!") closed q11 with reason `talked` (`Notebook.heard` → `talked`, `notebook.py:153-174`).
- **Class changes (B3).** Lab rows seen in 116–150 that held both actor and static kinds:

  | row | kinds held | names held |
  |---|---|---|
  | `e_item_1` | object, npc, character | item 1, NPC, display table, clerk, shopkeeper, poster, counter (called OAK) |
  | `e_display_case` | object, furniture, character | display case, display table |
  | `e_player` | object, furniture, npc | table, clerk, player |
  | `e_shopkeeper`, `e_clerk`, `e_customer`, `e_shelf_10`, `e_poster_2` | mixed | shelf, counter, clerk, poster … |

  - `e_item_1` was minted at 115 as a static `item 1` at tile [1,0]. It then matched an `NPC` perception, and SA's 3-of-6 vote turned its kind to `npc` at 121. From then on `_actor(row)` is true, so its tile follows every actor perception (`memory.py:341`).
  - Tile moves in the lab, 116–150: 45 on these 8 rows and 95 on the other 63 rows (SD counted 102 static moves; r6 had 55).
- **Duplicate OAK (B6).** At 137 the frame shows OAK directly above the player, perceived as `clerk` and matched to `e_customer`. `e_item_1` (called OAK) was not perceived that frame, so it was offered as a remembered actor: `go to OAK, not on screen, 1 left, 2 up`. The same happened at 141 and 147. Then 137, 141, 142 and 147 were route-blocked by an actor, the real OAK (`eval_moves` class `actor`).
- **Why the matcher allows this.** `Memory.observe` ranks pairs by `(tile distance, actor mismatch, name distance, -seen)` (`memory.py:288`). An actor/static mismatch only ranks lower; it never excludes the pair. Before SA, the first-mint kind was locked, so a hijacked row stayed static. SA made the kind votable, and the static/actor class went with it.
- **Blocked routes (B7).** `eval_moves` on LV: `unknown_cell` 29 rows / 168 keys, `actor` 8 / 63.
  - Lab cells blocked in RAM terms: 4 at x = −1, outside the room; 3 at the ball row (6,3), (7,3), (8,3) at steps 148–150, which the map had as unknown; 1 shelf; the rest were OAK.
  - Only 2 of 31 cell-blocked rows hit a cell that had already blocked. Learned walls alone would save little.
  - The 9 explore frontiers in the run (41 … 120) were never observed later; they lie past walls.
  - Note: the agent's tiles match RAM with a constant offset (+5, +1) in the lab. Player position is not the problem; void and unperceived statics are.
- **Grounding (GM, r7).** With the 300-character game-text line, lab frames named the balls 8 of 8 (as `POKéMON` / `POKé BALL`). All 4 false names on control frames were *characters*: a town NPC called "BLUE", and OAK called "RED". No object got a false game name.
- **Replay (B8).** From LV checkpoint 130, case 11 at 135 failed only its `section` and `note_re` checks, because OAK read `worked`. That is B1, not the replay. 3 of 10 A3 cases are INCONCLUSIVE (19, 25, 139).

---

## B1: same reply, normalised (`agent/loop.py:290-295`)

**Root cause.** `said` (line 293) looks for a `says` relation with `o == detail` exactly. A reply captured at another typing point or page differs by `▼`, `▶`, spacing or a cut-off tail.

**Change.**
- Add a small normaliser next to the lookup: drop `▼` and `▶`, collapse whitespace, casefold. `_learn` (line 417) already strips the same marks for conversation lines; reuse that expression rather than writing a second rule.
- Two texts are the same reply when, after normalising, they are equal, or one is a prefix of the other and the shorter is at least 8 characters. An empty `new_text` never matches.
- Only the `text_appears` lookup changes. The relation that gets stored is still the raw text.

**Tests (`tests/agent/test_loop.py`):**
- a reply that differs only by a trailing ` ▼` reads `failed`, with `fails[intent]["why"] == "same reply"`;
- a page-cut prefix of an earlier reply does the same;
- a different reply that shares a 5-character prefix is new;
- an empty reply is never "same".

## V2: regression case 11 from LV

Add to `scripts/points.json`: run `m1_r7_resume115`, checkpoint 130, step 135, situation "lab: talk to OAK after the same reply". Checks:
- `section` {`OAK`, `failed`};
- `note_re` {`OAK`, `same reply`};
- wrong `talk to OAK`.

No `right` check: the ball table has no stable name in LV. Run `--om points --dry --steps 135` on HEAD (expect FAIL, as r7 recorded) and after B1 (expect PASS). Commit `points.json` only on PASS. If it reads INCONCLUSIVE, report it and do not commit.

## B5: blocked is never progress (`agent/predict.py:12-16`)

**Root cause.** `result()` returns `progress` when `novel` is true, before it looks at `status`. A route that stopped short but scrolled the view has `novel > 0`.

**Change.** Check `status in ("blocked", "failed")` first:
- novel → `changed`;
- otherwise → `failed`, as today.

`closed` quests still give `progress` at `loop.py:337`, which runs before `result()`. A first visit to a new place is still counted by `Stuck.update` itself (`tasks.py:17`). The `fails` rule at `loop.py:353` is unchanged: a first block does not count as a fail, a repeated block does.

**Assertion change (needs your approval):** `tests/agent/test_predict.py:30` asserts `classify("blocked", ["no_change"], True) == "progress"`. Replace it with `== "changed"`, and add `classify("failed", ["view_scrolled"], True) == "changed"`. `tests/agent/test_tasks.py:65` replays recorded rows through `result`; if any of its assertions change, stop and report.

**Expected effect.** In LV, 8 more non-progress world rows in 116–150. With `STALL = 8`, the stuck replan may then fire in 141–150, which it never did. Not proven offline: other rows in that span could still count as progress.

## RP: perception-only identity replay (scratch, 0 asks)

This is the measure for I; it is not committed. `.tmp/r8/replay_identity.py <run> --from-checkpoint C --to S`:
1. Load the checkpoint's `memory/` into a fresh `Memory` in `.tmp/r8/`.
2. For each world step, take the recorded perceiver reply (`calls.jsonl`, `role == "perceiver"`, matched by step) and the trace's player `tile`.
3. Project entity tiles with `agent.space.tile_of`, as `World` does, and call `Memory.observe(per, place, step, tiles)` (no tracks).
4. Run the SD metrics (`scripts/eval_entities.py` functions) on the replayed journal.

**Sanity check first:** on HEAD, the replay of LV 116–150 should reproduce the recorded class-change ids (`e_item_1`, `e_display_case`, …) and kind flips within ±20%. If it does not, report and use only the recorded LV numbers.

Report on HEAD for LV 116–150 and r6 116–150:
- the SD table;
- class-changing ids;
- for each step, the number of perceived actors and the number of distinct rows they matched.

---

## I: identity (gap 2; B3, B4, B6). Options; user picks

The flaw: the perceiver's per-frame kind decides both what a row is called and whether the row is an actor. Actors match loosely (a gate up to 4 tiles) and move their tile every frame; statics hold their tile. So one noisy kind vote changes how the row is matched and moved from then on.

### I-A (recommended): class is fixed at mint; a perception of the other class never matches the row
**What.**
- In `Memory.observe`, the pair loop (`memory.py:280-288`) skips a row whose class (`_actor(row)`) differs from the perception's class, instead of only ranking it lower.
- The tile-less pool (line 255) is unchanged.
- SA's kind vote (lines 334-338) counts only kinds of the row's own class. Other kinds are still added to `kinds`, for the record.
- A perception that matches nothing of its class mints a new row, as today.

**Why.** Small (about 4 changed lines), and it removes the hijack path behind B3, B4 and B6. Names still follow the 3-of-6 majority, within one thing.

**Risks.**
- A character first seen as an object stays an object row. Its actor perceptions then mint a separate actor row: one duplicate, but a correct one.
- Rows from old checkpoints that already changed class keep their current kind.

**Measure.** RP on LV and r6 before and after. Go: no class-changing ids, kind flips down at least 50% from LV's 158, static tile moves at or below r6's 55, and at most one row per perceived OAK on 116–150.

### I-B: the pixel tracker owns actor identity
**What.** A perceived actor binds to the row of the pixel track under it (`agent/track.py`; `tracks` is already passed to `observe` and wins at line 278). With no track, it mints or matches only actor rows within 1 tile. Statics match by tile only. Perception supplies names and attributes, never identity.

**Why.** The architecture the handoff points to: identity from pixels, labels from the model.

**Cons.**
- Larger.
- It depends on track quality in rooms where several sprites stand still (OT was measured on the house and town, not the lab).
- RP cannot replay tracks, so measuring it needs frames: `scripts/check_tracks.py` on LV frames, 0 asks, slower.

### I-C: revert SA to the first-mint lock and keep SB
**What.** Undo `4decea8`'s vote. Names and kinds lock at mint, as in r6.

**Cons.** The poster stays kind `player`, and Oak stays "customer" (r6's problems). It reverses a decision taken one round ago.

**Recommendation: I-A now, I-B later** if RP or LV still shows actor duplicates.

---

## Q: quest closing on a reply (gap 5; B2). Options; user picks

The flaw: `heard` → `talked` closes any open quest whose target is the speaker, whatever was said. Place quests closing on arrival (`reached`) and exit quests closing on crossing (`crossed`) are fine: there, the side effect *is* the outcome.

### Q-A (recommended): a reply never closes a quest; the planner reads it and decides
**What.**
- `Notebook.heard` stops calling `talked` (`notebook.py:159`).
- New text already triggers a plan (T1), and the planner prompt already carries the newest text whole (P1a), so the planner closes the quest with `done` and a reason when the reply shows the outcome.
- `talked` and its `_evidence` wording stay for old notebooks.

**Assertion changes (need your approval):** `tests/agent/test_notebook.py:148`, `:193` and `:352` assert `reason == "talked"`. Each becomes "the quest stays open, and the text is recorded with `who`".

**Risk.** "Speak to X" quests then close one plan later, and only if the planner closes them. Before P2 (r5) the planner did close them, but sometimes late. Watch for this in LV.

### Q-B: a reply marks the quest "answered", it does not close it
**What.** `talked` sets `answered: {step, text}` instead of `done`. The planner's quest line shows `(answered at N: «first 80 chars»)`, and the planner closes or keeps the quest.

**Pro:** the planner sees the link between quest and reply. **Con:** a new quest field, plus a new render line.

### Q-C: close only when the reply shares a content word with the quest text
**Con:** a lexical gate. It would still close q11 if the reply said "POKéMON". This is the band-aid version.

**Recommendation: Q-A.**

---

## V: what counts as progress (gap 4, beyond B5). Options; user picks

After B5, `progress` still comes from new cells or pixels on a *successful* action. Stepping around a room reveals cells and resets the stuck counter, even if the quest's target never gets closer.

- **V-A (recommended): B5 only, then measure.** B5 plus Q-A remove the two false sources seen in the data. Re-measure progress rows in LV before redesigning.
- **V-B: goal-relative progress.** `progress` means one of:
  - a quest closed;
  - a new place;
  - new game text;
  - the route length to the focused quest's target (a thing or place on this map) got shorter.

  New cells alone become `changed`. About 10 lines in `_settle` plus a route-length helper from `space.route`. It needs a target on the map, which many quests lack.
- **V-C: Stuck ignores the verdict.** `Stuck.update` counts only quest closes, new places, new edges and new text. The verdict keeps only its role in `fails` and option history. This is the smallest change, but the verdict stays generous in the option history ("worked").

**Recommendation: V-A now.** If LV still shows long runs of `changed` rows without stuck replans, take V-B next round.

---

## T: tile map (gap 3; B7). Options; user picks

Routes treat any cell without a tile row as floor, within the known tiles' bounding box plus 1 (`space.py:43-53`). In the lab, the blocked cells were void past the walls (4) and unperceived statics (3).

- **T-A (recommended): void cells from pixels.**
  - When a world view is recorded (`World._record_view`), a view cell whose pixels are one flat colour, and that touches the screen edge through other such cells, is stored as `floor: False` in `tiles`.
  - `route` already skips `floor is False` (line 53), and `frontier` stops at it.
  - Generic: no game names. 0 asks; a few lines with PIL on the existing frame.
  - **Measure first (0 asks):** on LV and r6 frames, classify the cells of every `unknown_cell` blocked row. Go if at least half are void and fewer than 2% of walked cells (RAM-verified) would be marked void.
- **T-B: prefer known ground.** `route` gives unknown cells a higher cost (0-1 BFS or Dijkstra) and picks explore targets only among *seen* but unwalked cells. Fewer blind routes, but slower exploring, and it does not stop routes into void that has been "seen".
- **T-C: learned walls.** A blocked step marks the target cell `floor: False` when no actor holds it. Tiny, but the measured ceiling is 2 of 31 rows.

**Recommendation: T-A, behind its 0-ask measure.** T-C can come with it at almost no cost if you want it. Unperceived statics (the balls) are I and G's job.

---

## G: grounding (gap 1). Options; user picks

GM showed that the game-text line names the balls (8/8), and that every false name was a *character* name given to the wrong character.

- **G1o (recommended): G1 for objects only.**
  - Same line as r7's G1, worded: "If an object on screen (not a person) is something this text names, use the text's word as its name; name people as usual."
  - Bump `prompt_version` to `"e5-perceiver-12"`.
  - **GM2 micro-check:** the same 16 frames and two tries as GM (16 asks, cap 20). Go: lab BALL ≥ 6/8, control false names ≤ 1/8, fidelity as in GM.
- **G-E: names from evidence.** After the agent interacts with a static thing and the reply names a noun in caps (`POKé BALL`), that noun becomes the row's `called`, the same path as speaker names (`loop.py:305-306`). No prompt change. But it helps only after an interaction, and the balls must be checked to be named.
- **G2: aliases in labels.** `check display table (also: …)`. Weak: LV's aliases for the table were "display case" and "display table".

**Recommendation: G1o.** With I-A, a static "POKé BALL" row can no longer be taken over by OAK.

---

## R: replay that follows changed behaviour (gap 6; B8). Options; user picks

- **R-A (recommended): key perception by frame hash.** `calls.jsonl` stores each image's `sha`. `PointsStub.ask` (`scripts/eval_act.py:801-833`) first looks for a recorded perceiver reply whose image sha equals the current request's, and falls back to the (step, role) cursor. Act choices fall back from the recorded action dict to the option with the same label when the entity id differs.
  - Deterministic emulator: same keys give the same frames, so a hash hit is exact. A miss stays INCONCLUSIVE, as today.
  - 0 asks. Test: a replay where the step numbering shifts by 1 still finds the reply.
- **R-B: policy only.** Re-cut cases from each round's LV run, as done for V2. No code, but cases expire each round.
- **R-C: live scoring.** Cases run the real model (asks per case). Ruled out by the ask budget.

**Recommendation: R-A**, after I and Q, because those change option ids and labels.

---

## Decision (user, 2026-10-22)
- **I-A**, **Q-A**, **T-A** (behind its 0-ask measure).
- **B5** with its assertion change.
- **Not this round:** V beyond B5, G (no GM2, no prompt change), R.
- **Asks:** LV only, ≤ 120.

## Order
1. V2 dry on HEAD (expect FAIL).
2. B1, then suite and commit.
3. V2 dry (expect PASS), then commit.
4. B5, then suite and commit.
5. RP baseline: LV and r6, 116–150.
6. I-A, suite, commit; RP after.
7. Q-A, suite, commit.
8. T measure; T-A if go; suite; commit.
9. A3 dry on all cases (report `label_re` updates as in r7).
10. LV: copy `m1_r6_live_150` to `runs/m1_r8_resume115`, resume from 115, 35 steps, `--max-calls 120 --decide`; then `eval_run`, `eval_moves`, `eval_entities`, RP.

**LV success:** a party by step 150. Also report: class-changing ids (expect 0 with I-A), `go to … not on screen` for an adjacent actor (expect 0), blocked rows with verdict `progress` (expect 0), and quests closed by `talked` (expect 0 with Q-A).

## Validity check (2026-10-22, HEAD `d81df01`)
- **B1:** `agent/loop.py:290` `new_text`; `:293` exact `(s, p, o)` lookup; `:336` `idle |= repeated`; `:417` the `▼`/`▶` strip in `_learn`.
- **B5:** `agent/predict.py:13` `if novel: return "progress"`, before the status check at `:14`; `agent/loop.py:337` verdict; `:353` the `fails` rule for a repeated block; `agent/tasks.py:17` progress from verdict.
- **B5 test:** `tests/agent/test_predict.py:30` (the assertion to replace).
- **Q:** `agent/notebook.py:153-161` `heard` calls `talked` at `:159`; `:163-174` `talked`; `_close_crossed` `:196-211` (kept). Tests: `tests/agent/test_notebook.py:148`, `:193`, `:352`.
- **I:** `agent/memory.py:255` tile-less pool; `:278` track hit; `:280-288` pair cost with actor mismatch as a rank term; `:334-338` SA kind vote; `:340-348` SB tile update and `_actor(row)` free tile.
- **T:** `agent/space.py:39-55` `route` (bounds `:44`, `floor is False` skip `:53`); `:62-69` `frontier`.
- **R:** `scripts/eval_act.py:793-833` `PointsStub` ((step, role) cursor `:815`; act match by action dict `:812`).
- **G1o:** `agent/loop.py:99` ctx; `:36` `prompt_version "e5-perceiver-11"`.
- **Runs:** LV and r6 have checkpoints every 5 steps from 0 to 150 (115 and 130 exist).
- **Ledger:** `runs/e3_budget.json` total 6666.

Not verified: RAM bounds for the lab, apart from x = −1 being outside the map. Whether `e_item_1`'s first tile [1,0] was a ball is not proven.

## Report format
Same as r7. Add:
- the RP table, before and after I;
- the T measure;
- GM2 replies;
- the LV extras listed under Order.
