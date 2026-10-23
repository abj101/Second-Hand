# Avatar harness round r14: every offered row executes, re-entry lands right, exits get settled

**Summary**
1. **Source.** `docs/plans/review-r13.md`, gaps G1–G6. Each was diagnosed to code with zero model requests: `.tmp/r13/gaps/`, `.tmp/r14/g2/`, `.tmp/r14/g45/`. My recommended options are below; the user asked for a plan covering all six.
2. **Execution (G1).** An offered exit that cannot be planned fails loudly as a route failure, instead of turning into a silent zero-input wait. Its row states "no known route" as a fact.
3. **Position and exits (G2, G3, G5).**
   - Re-entering a known place prefers a matching stored view over an *inferred* arrival tile, and corrects that tile.
   - A push blocked by a character counts as tried, so a fake exit gets settled.
   - The way back through a door you just used is labelled by its destination and counts the crossing.
4. **Places and planner (G4, G6).**
   - While the scene has changed under a text box, prompts say where you are is not known yet.
   - Planner errors say what to do: "add it first", "reopen it", the closest labels.
   - `done` accepts a sentence given as `why`.
5. **Asks:** 0 for every part and gate. Then LV14: a clean boot, 300 steps, ≤ 800 asks. The ledger is 8314 / 17854; **stop** at 9114.

**Baseline:** HEAD `cc5f3b7` plus the r13 docs commit (the orchestrator makes it in wave 0). Suite: 788 passed, 1 skipped. `scripts/watch_run.py` and `tests/agent/test_watch_run.py` are the user's uncommitted edits: leave them untouched and uncommitted.

**Principles (unchanged):**
- The system states facts and legal options; the act model decides. No new gates, filters or hidden rows.
- No game words in `agent/`.
- Reduce churn: edit in place, reuse the named helpers, add no modules, and every new helper replaces something.

**Not in r14** (and why):
- Falsifying a guessed name when a reply doesn't contain it: it needs a design choice (what counts as contrary evidence). Propose it after LV14.
- Calibrating view matching for unseen ground (G4b: views beside exits, or a lower `VIEW_MATCH`): this changes calibration. The existing loop-closure merge (`world.py:500-519`) already joins a re-minted place once the agent walks onto known ground.
- Closing a talk quest the planner retargeted to an exit (G6d): the exit close is the r13 N1 contract, and the retarget was the planner's own decision.

---

## Parts

| id | title | lane | files (regions) |
|---|---|---|---|
| G1 | An unplannable pick fails loudly; exit rows state "no known route" | A | `agent/loop.py` (`:199` and the `refused` SkillResult at `:266`), `agent/world.py` (options exit rows `:1162-1165`) |
| G5 | The way back is named by its destination and counts the crossing | A | `agent/world.py` (`_exits` head at `~:839`, crossing count at `~:822-824`) |
| T14 | Replay cases for r14 | A | `scripts/points_r14.json` (new) |
| G2 | Re-entry prefers a matching view over an inferred arrival, and corrects it | B | `agent/memory.py` (`match_place` arrival at `:221`, `:229`, `:239`), `agent/world.py` (`before` `:162-165`, `_seen_at`/`_locate` `:409-419`), `agent/loop.py:130` (pass the frame) |
| G3 | A push into a character counts as tried | B | `agent/space.py` (`fold` `:15`, `push_dirs` `:72-77`), `agent/world.py` (the push-row facts at `:1146-1158` only) |
| G4 | Prompts say when the place is not known yet after a scene change under text | C | `agent/loop.py` (next to `cut_carry` at `:128`), `agent/world.py` (`here` `~:749-760`) |
| G6 | Planner errors say what to do; `done` takes a sentence `why` as its reason | C | `agent/notebook.py` (`_apply` `:85-127`, `_no_target` `:50-57`), `agent/plan.py` (REPLY) |
| G14 | Gates | orchestrator | `.tmp/r14/g/` |
| LV14 | Clean-boot live run | orchestrator | `runs/m1_r14_boot` |

**Waves:**
- Wave 1: lanes A and B in parallel. Their `world.py` regions are disjoint (A: options exits, `_exits`; B: `before`, `_locate`, the push-row facts), and so are their `loop.py` lines (A: `:199`/`:266`; B: `:130`).
- Wave 2: lane C, after both are merged, because its `loop.py` line sits next to B's.

---

## Item specs

### G1: an offered row executes or fails loudly
- **`loop.py:199`.** Today `action = None if travel_legs is None and not e else action` clears the pick, so the step becomes `wait_until` and reports `done` with 0 inputs (r13 steps 211–250: 31 steps, 40 act asks).
  - Instead, keep the action, so a `choice` exists and the intent key and fails counter work, and set `refused = "no known route"`.
  - The `refused` SkillResult at `:266` takes that reason, with `failure={"stage": "route", "reason": "no known route", "direction": None}`.
  - Today `refused` is a bool there. Make the reason a string, keeping the two existing texts for the existing cases ("no route beside target", "no route avoids blocker").
- **`world.py` options, exit rows (`:1162-1165`).** For an exit row whose action is `go_to`, compute `self.plan(...)` the way place rows do (`:1204-1206`), reusing the resolve the row already does if any. When it returns None, append the note "no known route from here (remembered walls block every path)". **The row is still offered**: a wall in memory can be wrong, and the failure now teaches the fails counter.
- **New tests:**
  - an exit whose only route is walled in memory is offered with that note;
  - picking it gives skill `blocked`, 0 inputs, `failure.stage == "route"`, and a fails entry (the row's state becomes `failed` next step);
  - an executable exit is unchanged.
- **Pre-approved:** none. If the golden trace changes, it may differ only by that note.

### G5: the way back names where it goes and counts the crossing
- **Label.** In `_exits` (`world.py:~839`), `head = … if name else dir_` changes to `if name else self.place_label(dest) if dest else dir_`. "go to UP, here" becomes "go to <dest label>, here".
- **Crossing count.** The `uses` and `status` of an exit record also count the partner crossing, `_cross_key(dest, tuple(door["arrive"]), pid)`, when the record's `arrive` is set: the other side of the same door.
  - So the inferred way back of a door you just walked through reads "crossed 1× → <place>", not "exit, never crossed".
  - Change the count where `uses` is summed per door (`~:822-824`); find it with `codegraph explore "World._exits _cross_key"`.
- **New tests:**
  - after crossing A→B through a door, B's inferred return exit is verified with uses 1, and its label names A;
  - an unnamed exit with no destination keeps its direction head.
- **Pre-approved:** exit-label assertions that expected a direction head for an exit *with* a known destination. List each one before and after; any other label change means stop.

### T14: replay cases
`scripts/points_r14.json`, on `runs/m1_r13_boot`, using the r13 points checker (`right`, `absent`, `note_re`, `text_re`, `text_absent`). If a step cannot be replayed from its checkpoint, use the nearest step with the same situation and say so.

| step from checkpoint | item | checks |
|---|---|---|
| 211 from 210 | G1 | `note_re`: label `^go through door mat` notes `no known route` |
| 208 from 205 (if multi-step replay is unsupported: the nearest step outside the lab after 207) | G5 | `absent` label `^go to UP,`; `right` label `^go (to\|through) shop` |
| 209 from 205 | G2 | `text_re` `Here: [^\n]*\(0,8\)`, the lab's original frame for truth (5,11). Verify that mapping on checkpoint 205's memory first, and write down the basis. |
| 161 from 160 | G3 | `absent` label `^press DOWN here` (needs the `held` fact; if checkpoint 160 has no evidence the replay can add, mark it INCONCLUSIVE and rely on G3's unit test) |

The "before" column is taken on the wave-1 base; expect FAIL.

### G2: re-entry prefers a matching view over an inferred arrival
- **Facts** (from `.tmp/r14/g2/`): with truth[N+1] paired with tile[N], belief followed truth on all 124 lab moves. The single frame shift, (5,3) → (6,4), happened at the re-entry at step 209.
  - Its origin was `memory.py:229` (`self.arrival = (best[2], best[3])`), the matched exit's `arrive` [-1,7].
  - That came from an *inferred* way back, which `space.py:35` already notes "lands 1 off until re-anchored".
  - `_locate` (`world.py:412-419`) uses that arrival first and tries a view only when there is none. `World.before` (`:164`) calls `_locate(place)` with no frame.
- **Change:**
  1. `Memory.arrival` carries its source as a third element: `"view"` at `:221`, the exit row's `src` (`"observed"` or `"inferred"`) at `:229`, and `"new"` at `:239`. `_seen_at` returns `(tile, src)`.
  2. `World.before(…, frame=None)`: `loop.py:130` passes `frame=before` (the frame `match_place` already used). It calls `self._locate(place, frame)`.
  3. In `_locate`:
     - when the arrival's src is `"inferred"` and `self.mem.seen_view(frame, None, None, self.tile_px, only=place)` hits, use the view tile;
     - if the two differ, correct the exit row that handed the arrival: write `arrive` to the view tile and `src` to `"observed"` with `mem.upsert`, the same row shape. Note the row id in the trace's existing `drift_fix` field if one fits, otherwise in no new field.
     - Otherwise keep today's order.
- **New tests**, built from the templates `test_pre_map_things_are_no_refs_and_reentry_lands_on_the_matched_exit_arrival` (`test_world.py:231`) and `…_unplaced_until_a_view_matches` (`:248`):
  - an inferred arrival one tile off with a matching view lands on the view tile and corrects the exit row;
  - an observed arrival is kept even when a view matches elsewhere;
  - with no view match, the inferred arrival is kept.
- **Pre-approved:** none.

### G3: a push into a character counts as tried
- **Facts:**
  - At the lab's fake "door (top wall)" (r13 steps 152–161), the pushes UP and RIGHT were blocked and LEFT moved.
  - DOWN was pressed three times into Oak, who stood one tile below.
  - `fold` (`space.py:15`) skips a zero shift into an actor's tile; that is right, since a character is not a wall. But DOWN then stays in `push_dirs`, `_refuted` never fires, and the exit is offered 19 times.
- **Change:**
  - `fold` records that skip as `held: [direction]` on the tile row (the same row shape, a new list key read with `.get("held", [])`) instead of only `continue`.
  - A later real move in that direction removes it, like `blocked`.
  - `push_dirs` excludes `held`, the same way it excludes `pushed` and `blocked`.
  - The push-row facts (`world.py:1146-1158`) add "pressed D: someone stood there" for held directions.
  - No wall is written, so routes are unchanged.
- **New tests:**
  - a zero-shift push into an actor tile records `held` and writes no wall;
  - an exit whose untried pushes are all held, blocked or moved is refuted;
  - a later move in a held direction clears it.
- **Pre-approved:** none.

### G4: say when the place is not known yet
- **Facts:** the lab entry (r13 step 104, truth 0 → 40) happened under a dialogue box. `match_place` keeps the old place on non-world screens (`memory.py:209-212`) and `cut_carry` waits for a world frame (`loop.py:128`). For 18 steps the prompts said "outside house, maybe SHOP", and the planner named Pallet "SHOP" and "OAK's Lab".
- **Change:**
  - `loop.py`, next to `:128`: `self.world.unsettled = self.cut_carry` (one attribute, default False on World).
  - `World.here()` (the "Here:" line used by the act focus and the planner view) appends ": the scene changed under the text; where you are is not known yet" when it is set.
- **New test:** with `unsettled`, the Here line carries the fact; without it, the line is unchanged.
- **Pre-approved:** none. Existing "Here:" assertions use non-cut fixtures.

### G6: planner errors that say what to do
- **Facts:** 23 of 77 r13 ops errored.
  - "key is not open" ×11: the key was never added (and was added later), or was already closed.
  - "reason is empty" ×4: the closing sentence was put in `why`.
  - name errors ×5, with no candidates listed.
- **Change, `notebook.py _apply`:**
  - **Not open.** Split the error by fact:
    - `"q5 is not a quest yet; add it first"`;
    - `"q11 is closed (<status> at step N); reopen it first"`.
  - **`done` / `drop`.** With no `reason` and a non-empty *string* `why`, use that string as the reason. A list of t-keys is still an error, and its text becomes `"reason is empty (a sentence, not t-keys)"`.
  - **`name`.** On a failed resolve, return the same closest-labels text `_no_target` builds (reuse it), as in `"oak" names no known thing or place; closest: …`. When the resolved place is not where you are, the op still applies, and its ok result says `"ok (named <label>, not where you are)"`.
- **`plan.py` REPLY:** one added clause: "top, target, done and drop take an open quest key (add creates one); done and drop need a reason sentence; name's key is a map label."
- **Pre-approved** (report each before and after): the error-message strings in `test_notebook.py` for not-open keys, empty reasons and name failures.
- **New tests:** one per message. A string `why` closes with that reason.

---

## G14: gates (orchestrator, 0 asks)
1. Run the full suite after each merge, with `-n auto`.
2. **Golden.** If `test_world_golden` fails, regenerate it with `W0_REGEN=1` only when the diff is limited to:
   - G1's "no known route" note;
   - G5's exit heads and uses;
   - G3's held facts.
   Commit as `G1g …`. Any other diff: stop.
3. **T14.** Run `eval_act --om points --points scripts/points_r14.json` (dry). Record before and after; the after column must be all PASS, except a G3 case marked INCONCLUSIVE as the spec allows.
4. **r13 cases.** `points_r13.json` must still be 6/6 PASS, and `points_r12.json` statuses unchanged.

## LV14: live run (orchestrator)
- Check that the viewer is up, then run once:
  ```
  .venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r14_boot --steps 300 --max-calls 800
  ```
  No resume or retry without asking.
- **Success:** a party, and then leaving the lab with the party to Pallet (truth map 0) without walking straight back in within 10 steps. **Stretch:** truth map 12 (Route 1).
- **Report** to `.tmp/r14/report.md`, against r13. Use the scripts in `.tmp/r13/` and `.tmp/r13/gaps/`, **with the corrected alignment truth[N+1] ↔ tile[N]**:
  - party step;
  - lab-exit step;
  - steps with choice null and 0 inputs (must be 0);
  - steps with a "no known route" pick;
  - re-entry offsets;
  - fake-exit attempts;
  - planner op error rate;
  - asks per step;
  - ledger before and after.
