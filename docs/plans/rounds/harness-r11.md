# Avatar harness round r11: an exit is a hypothesis until it is crossed

**Summary (6 lines)**
1. **r10's boot run got stuck at a door.** `runs/m1_r10_boot` never left Red's house 1F: from step 52 to 215 every step pressed `RIGHT, LEFT`. Truth puts Red at (2,7), the door row, so pressing DOWN would have left.
2. **The architectural gap:** memory records only what worked. A crossing is stored as an observed exit (`mark_exit`, `crossings`). A push that does not cross is stored nowhere, so `World.plan` rebuilds the same guess every time. The tile field `pushed` (`space.py:6`) exists for this and is never written.
3. **The design rule for r11 (picked: B):** an uncrossed exit carries a **ranked list of push directions**. Every failed push is written to the tile it was made from, and the next attempt takes the next direction. When none is left, the exit is refuted. A crossed exit that later fails `K` retries in a row is demoted the same way, which also covers a one-time scripted warp that was taken for a door.
4. **Amended after the E3 stop (O1):** the system does not choose the push for an uncrossed exit. It offers **one option row per push direction**, with the perceived wall and the recorded facts in the label, and the act model picks. `push_dirs` only orders the rows. The system supplies facts and what is legal; the agent decides.
5. **Supporting items:**
   - **Q1:** the planner may not target the run-start title place ("place 1");
   - **TC:** a 0-ask audit of cuts with no pressed direction (teleports, cutscenes);
   - **MC:** an act micro-check on the new rows (≤ 20 asks), the gate for LV11.
6. **Asks:** ≤ 20 for MC, then a clean-boot run of at most 400. Ledger 6998 / 7854, so 856 are left. **Executed by** a separate session; see `docs/plans/execute-r11.md`. Decisions D1–D6 are picked; D1 and D3 were amended (see below).

**Status (2026-10-22):**
- **Committed:** E1 `a7e051c`, E2 `a05f04a`, E3 `05ba46a`, E4 `8763901`, E5 `bd170e1`, O1 `f50b1ef`, Q1 `905ebed`.
- **Measures:** suite 753 passed, 1 skipped. G-rank and G-door passed; A3 shows no FAIL.
- **MC said no-go** (cases 1/2/4/5 scored 0/3, 3/3, 0/3, 0/3; 15 asks; ledger 7013 / 7854).
  - In cases 1 and 4 the act model picked the right doorway row 3/3, but pressed UP each time. Its why even read "the doorway at the bottom wall". The four rows differed by one word with no stated meaning.
  - Case 5 was a planning flaw. The state had no goal, and the perceiver called the lab a "shop", so the model talked to the "shopkeeper".
- **Next:** F1, then MC2.

| id | title | files | asks | gate |
|---|---|---|---|---|
| E0 | Push-rank measure on recorded crossings, plus the r10 door replay | `.tmp/r11/` scratch | 0 | none (baseline) |
| E1 | An exit thing keeps its perceived wall placement | `agent/memory.py`, `agent/world.py`, tests | 0 | suite |
| E2 | Ranked push directions for an exit tile (`push_dirs`) replace the single `outward` guess | `agent/space.py`, `agent/world.py`, `agent/loop.py`, tests | 0 | E0: G-rank |
| E3 | A push that does not cross is written to the tile's `pushed`, recorded by the caller from the push it asked for; a crossing clears it | `agent/space.py`, `agent/world.py`, `agent/loop.py`, tests | 0 | suite |
| E4 | An uncrossed exit is refuted only when no push direction is left | `agent/world.py`, `tests/agent/test_exits.py` | 0 | suite (D4) |
| E5 | A crossed exit that fails `K` retries in a row is demoted | `agent/world.py`, tests | 0 | suite |
| O1 | An uncrossed exit is offered as one row per push direction; the act model picks | `agent/world.py`, `agent/loop.py`, tests, `scripts/points.json` | 0 | G-door (options level) + A3 |
| Q1 | Only navigable places resolve as planner targets | `agent/world.py`, tests | 0 | suite + plan replay |
| TC | Audit: cuts with no pressed direction, and the places they minted | `.tmp/r11/` scratch | 0 | report only |
| MC | Act micro-check on O1 rows at stuck doors and the lab exit | `.tmp/r11/` scratch | 15 used | **no-go** (2026-10-22) |
| F1 | Each direction row states what is known that way (A); one generic act-prompt clause on walls (D) | `agent/world.py`, `agent/act.py`, tests, `scripts/points.json`, golden act trace | 0 | suite + G-door + A3 |
| MC2 | MC re-run after F1, with case 5 replaced by a lab state that has a leave goal | `.tmp/r11/` scratch | ≤ 15 | go / no-go for LV11 |
| LV11 | Clean-boot live run, 150 steps | run data | ≤ 400 | G-door + MC2 go |

---

## Decisions (picked 2026-10-22: D1 R-b, D2 a, D3 b, D4 a, D5 K = 2, D6 include)

**Amendments (2026-10-22, after the executor's stops):**
- **D1 → R-b′.** The order is: the placement's wall; then any direction whose neighbour tile holds an exit record (any src); then directions into tiles not walked; then walked floor last. Reason: both r6 crossings step onto a neighbour that already holds an exit record, and plain R-b ranks them 4th. Committed in E2.
- **D3 → O1.** It replaces E6's notes. The push of an uncrossed exit is the act model's choice, one row per direction. Reason: at r10 step 60 the act prompt showed `go to doorway, 1 right — exit, never crossed; … exit not crossed 8×`. The act model is text-only, so it could see neither the push the system had chosen nor the perceived wall.

| id | question | options | recommended |
|---|---|---|---|
| D1 | Order of the push candidates | **R-a:** the placement's wall, then `outward`'s centroid guess, then the other two in `DIRS` order. **R-b:** the placement's wall, then directions whose neighbour tile is **not** walked floor (ordered by the centroid guess), then directions into walked floor last. | R-b. A push into walked floor cannot leave the room. On r10 the door's only non-floor neighbour is DOWN, while the centroid said LEFT. |
| D2 | How an exit row keeps its placement | **a:** the latest wall value seen (`top/bottom/left/right wall`; `middle` and edges are ignored). **b:** the most frequent wall value of the last 6 sightings. | a. A wrong first guess costs one attempt, because E3 records it. |
| D3 | The act model repeating a failing option (candidate item 3) | **a:** nothing more; E3 already makes each retry a new push. **b:** E6, information-only notes on the exit option. **c:** reorder options by failure count. | b. It informs without gating. c is a mechanical gate on choice, against the standing direction. |
| D4 | `_refuted`'s outward term (`world.py:243`) refutes an exit after one blocked push in the guessed direction | **a:** replace it with "no push direction left" (E4). This changes one assertion: `test_exits.py:144-148` blocks only RIGHT and expects `== 1`. The test would block all four directions instead. **b:** keep it; then a blocked first guess still kills a real door. | a, with that one assertion change pre-approved. |
| D5 | `K` for demoting a crossed exit (E5) | 1, 2 or 3 failed retries in a row | 2. One miss can be an actor on the mat or a dialogue that opened. |
| D6 | TC, the teleport and cutscene audit | include (0 asks, report only) / skip | include |

---

## Evidence (0 asks; HEAD `8136df2`)

- **The loop.** `presses.jsonl` steps 52–215: `["RIGHT", "LEFT"]` every step, outcome `no_change` and the same frame hash. The agent stands at `(-5,6)`, walks RIGHT onto the door tile `(-4,6)`, pushes LEFT, and walks back. Truth: map 37, player at (2,7), the door row.
- **Two push paths, both blind to placement:**
  - `World.plan` (`world.py:1203`) for `exit:x,y` targets (steps 52–99): `door dir` → `exit_record dir` → `outward(self.mem, p, tile)`. A thing exit has `dir ""` (`world.py:778`), so it always falls to `outward`.
  - `Loop._approach_route` (`loop.py:465`) for an on-screen exit (steps 100–215): `outward(self.mem, place, t, None)`, even though `entity` with its `placement` is in hand.
- **The centroid guess.** 17 walked floor tiles of `p_place_3` have centre (-2.18, 4.35). For the door (-4,6), dx = -1.82 and dy = 1.65, so `outward` says LEFT. The door's neighbours: LEFT, RIGHT and UP are walked floor; DOWN is unknown. Under D1 R-b, DOWN is the only non-floor direction.
- **Placement is a hint, not truth** (raw perceiver replies in `calls.jsonl`):
  - r10: `exit: doorway` "bottom wall" ×4 (steps 44, 71, 72, 86); `exit: door mat` "bottom wall" ×1; `exit: door` "top wall" ×2.
  - r6: `exit: door` "top wall" ×17, "middle" ×3, "right wall" ×3, none ×4; `exit: doorway` "bottom wall" ×2, "top wall" ×2.
  - So placement goes first, and E3 must recover from it being wrong.
- **`pushed` is dead.** It is created in `_new` (`space.py:6`), copied in `fold`/`mark_exit`, unioned on merge (`world.py:1276`) and read by `frontier` (`space.py:68`). Nothing writes it.
- **One blocked guess refutes a door.** `_refuted` (`world.py:243`) counts `outward(...) in here["blocked"]` as refutation, and `_exits` (`world.py:800`) marks a never-crossed exit `refuted` on it. Under a wrong centroid guess into a wall, a real door is refuted after one push.
- **Crossed exits are never demoted.** `_exits` gives `status: "verified"` whenever `uses` > 0 (`world.py:801`), and `_paths` (`world.py:515-521`) routes through every exit with doors. A travel leg that fails to cross (`world.py:1223`, `not is_cut`) records nothing on the exit.
- **Place 1 as a target.** The planner set q5's target to "place 1" at step 56. Steps 64, 80, 112 and 176 tried `name` on "place 1" ("names no known thing"). The intro text was heard on the run-start place, so the planner tied Oak to it. `World.ref` resolves any place id (`world.py:585-586`), while things are already limited to world maps (`world.py:587`).
- **Teleports and cutscenes as coded** (reading; TC measures it):
  - a non-world screen keeps the current place;
  - after a cut: view match → known exit within 1 tile → `leads_to` relation → new place at (0,0);
  - an exit is written only for a cut that followed a pressed direction, with world screens on both sides (`loop.py:285`, `world.py:356-358`).

  Unmeasured risks: a teleport landing on a tile with no stored view mints a duplicate place; a scripted walk drifts the position.

---

## E0: push-rank measure (0 asks, scratch)

`.tmp/r11/push_rank.py <run> [...]`. Use `scripts/eval_moves.py`'s `_memory` loader (`:38`) for the memory at the checkpoint before each step.

1. **Recorded crossings.** Cover every run under `runs/` that has `memory/tiles.jsonl` (rule revised 2026-10-22).
   - **Tile state:** for each crossing, take the latest version of each tile id among rows with step < the crossing step. Checkpoints supply only entities and calls.
   - **The departure tile** is always floor and is never skipped.
   - **Count each crossing once.** Crossings inherited by a resumed run (r7 and r8 from r6, at or below step 115) are dropped.
   - **Rank the crossed direction** under:
     - `old`: today's `outward(mem, place, tile)`, then the rest in `DIRS` order;
     - `R-b` and `R-b′` (D1), each with and without the latest perceived wall placement of the exit thing on that tile (from `calls.jsonl`, nearest earlier step).

   Print per run and pooled: n, rank-1 share, mean rank, per scheme.
2. **The r10 door replay.** Copy `runs/m1_r10_boot`'s memory at the checkpoint ≤ 52 to `.tmp/r11/r10door/`. Target `exit:-4,6`, the same target as step 52. Loop at most 4 times:
   - `World.plan(...)` gives the push;
   - if it is not DOWN, apply E3's record for that push and plan again.

   Print the push sequence. At HEAD it prints `LEFT, LEFT, LEFT, LEFT`.
3. `requests=0`.

**Gates used later:**
- **G-rank (E2), passed:** pooled over the deduplicated crossings, R-b′'s rank-1 share ≥ `old`'s, and its mean rank ≤ `old`'s.
- **G-door (after O1), at the options level, 0 asks:** on the r10 checkpoint-50 copy, with the doorway entity on screen at the step-52 perception:
  1. with "bottom wall" stored, the doorway's rows include `then press DOWN`, and it is the first of the doorway's untried rows;
  2. with no placement, there is one untried row for each direction not pushed or blocked;
  3. after E3 records a LEFT push that moved without a cut, the LEFT row is `failed` with its fact, and the untried rows no longer include LEFT;
  4. the fallback order (`push_dirs`) never repeats a direction until all four are tried.

  The old limit ("DOWN within 2 pushes") is withdrawn. At checkpoint 50 none of the door's neighbours had been walked, so 3 pushes was the honest result; the limit came from the run's final memory.

---

## E1: an exit thing keeps its placement

- **Store:** in `Memory.observe` (`memory.py:238`), when a perceived entity that is an exit (`_is_exit`) binds to or mints a row, write `placement` on the row per D2. Only the four wall values count; `middle`, `room edge` and `screen edge` are ignored. `perceive.normalize` already keeps the field (`perceive.py:82`).
- **Carry:** the thing records in `_exits` (`world.py:778`) get `"placement": row.get("placement")`, and so does each exit's output (`world.py:802`).
- **Tests:**
  - a doorway perceived with "bottom wall" stores it;
  - "middle" leaves a stored wall unchanged;
  - `_exits` carries it.

## E2: ranked push directions (committed `a05f04a`, R-b′)

- **Function:** one function in `agent/space.py`, `push_dirs(mem, place, tile, placement=None)`. It returns the four directions in D1 order, minus those in the tile's `pushed` or `blocked`. After O1 it orders the rows and serves as the fallback when an action carries no push.
  - Keep `outward` as the centroid scorer used inside it. No other new helper.
- **Use the first one at both push sites:**
  - `world.py:1203`: `... or exit_record.get("dir") or next(iter(push_dirs(self.mem, p, tile, exit_record.get("placement"))), None)`. A recorded door `dir` still wins.
  - `loop.py:465`: `push_dirs(self.mem, place, t, entity.get("placement"))`, first or `None`.
- **When the list is empty:** the plan returns `None` for that exit (no route), as it does for an unreachable tile.
- **Tests:**
  - R-b on the r10 shape (a floor row with the door at one end, DOWN unknown) gives `DOWN` first;
  - placement "left wall" goes first;
  - a pushed or blocked direction is dropped;
  - all four dropped gives `[]`.

## E3: a failed push is remembered

- **The push is the caller's (ruling 2026-10-22):** the caller already knows the direction it asked for: `leg["push"]` in `World.travel` (`world.py:1205`), and `route["push"]` on the screen path (`loop.py:465`). Record from there. **`agent/skills.py` and `tests/agent/test_skills.py` are not touched**, so `test_exit_not_crossed_reports_crossing_failure` keeps its exact assertion.
- **Record it:** `space.push_failed(mem, place, tile, direction, step)` appends `direction` to the tile's `pushed`, once, through `_save`.
- **The tile** is the one the player stood on when pressing: fold the skill's moves except the last from the step's start position (the route's `exit_tile` when the route reached it).
- **One call site:** a single World method, called from the loop's settle path when `skill.failure.stage == "crossing"` and no cut happened, with the push the step asked for.
- **Clear on success:** `mark_exit` (`space.py:31`) removes `direction` from the departure tile's `pushed` when it writes the observed exit.
- **Tests:**
  - an approach with push LEFT that moves the player one tile and no cut writes `pushed: ["LEFT"]` on the start tile, and the next `World.plan` pushes the next candidate;
  - a crossing clears it;
  - a blocked push (shift 0) still writes `blocked` only (fold), and is also skipped.
- **Gate:** the suite. G-door is judged after O1.

## E4: refuted only when nothing is left (D4)

- **Change:** `_refuted` (`world.py:234-244`) keeps the neighbour term (`:239-241`). It replaces the `outward(...) in here["blocked"]` term (`:243`) with `not push_dirs(self.mem, place, tile)` on a floor tile.
- **Tests:**
  - one blocked direction no longer refutes;
  - all four pushed or blocked does;
  - `test_departure_crossing_prevents_refutation` is unchanged;
  - `test_blocked_outward_push_refutes_floor_exit_tile` is changed per D4 (block all four directions; still `== 1`). This is the only approved assertion change.

## E5: demoting a crossed exit (D5)

- **Count a miss:** when a travel leg with `expect` ends with no cut (`world.py:1223`), add 1 to `miss` on the crossings row `_cross_key(place, exit_tile, expect)`, and write the leg's push to the tile's `pushed` (E3's function).
- **Reset:** `_count` (`world.py:366-369`) sets `miss: 0` on a crossing.
- **Status:** in `_exits` (`world.py:800-801`), an exit whose doors' crossings rows all have `miss >= K` is `refuted`, even with `uses` > 0.
- **Routing:** `_paths` (`world.py:519-520`) skips `refuted` records. Options still list them; nothing is hidden.
- **Tests:**
  - `K` misses refute a crossed exit and drop it from `_paths`;
  - a crossing after one miss resets it;
  - `K - 1` misses keep it `verified`.

## O1: one row per push direction (D3 amended; replaces E6)

**The principle:** the system states facts and what is legal; the act model chooses. No push is chosen behind the act model's back for an exit that was never crossed.

- **Which exits:** an exit record from `_exits` with no recorded door `dir` (never crossed, never observed), on screen or remembered. A crossed or observed exit keeps its single row exactly as today.
- **Rows** (`World.options`, the exit loop at `world.py:1054-1070`). One row per direction, in this order:
  - first, the untried directions in `push_dirs` order (they land in "Not tried here");
  - then the tried ones: in the tile's `pushed` (moved, no exit) or `blocked` (did not move). They stay listed, `failed`, with their fact, and are never hidden.
  - **A refuted exit** (`_exits` status `refuted`: the neighbour term, or all four pushes tried) shows **one** `failed` row, exactly as today: same label, `exit failed: blocked n×` note, same key. Ruling 2026-10-22, after the O1 suite stop.
- **Label:** `go to <exit label> (<wall>), <where>, then press <DIR>`. The `(<wall>)` part appears only when E1 stored a wall placement.
- **Notes** (ruling 2026-10-22):
  - untried: unchanged, `["exit, never crossed"]`. The act prompt's "Not tried here" group already says it is untried.
  - pushed: `exit, never crossed` plus `pressed <DIR>: moved, no exit`;
  - blocked: `exit, never crossed` plus `pressed <DIR>: blocked`;
  - plus the existing per-key failure note (`n× (last step s)`).
- **Remembered exits:** a remembered uncrossed exit (the `go_to` path) gets the same per-direction rows, so G-door uses the doorway as the options list it. **Do not project the doorway onto a perception that omits it.**
- **Action:** the row's action carries the push: `{"thing": idx, "verb": "approach", "push": DIR}` on screen, or `{"go_to": exit_ref, "push": DIR}` remembered.
  - `Loop._approach_route` (`loop.py:449-466`) and `World.plan` (`world.py:1203`) use `action["push"]` when present, else `push_dirs` as now.
  - Pass it in through the loop's existing action handling (`loop.py:184-213`). Add no new parameter beyond what carrying `push` needs.
- **Intent key:** the push is part of the row's key, so failures count per direction: `fail_key(..., ref=f"{ref}>{DIR}")`. A crossed exit's key is unchanged.
- **No game words:** wall names come from the perceiver's enum; directions come from `DIRS`.
- **Tests:**
  - an uncrossed doorway with "bottom wall" gives 4 rows, `then press DOWN` first among the untried;
  - after `push_failed(LEFT)` the LEFT row is `failed` with `pressed LEFT: moved, no exit`;
  - a crossed exit still gives one row with its old label;
  - picking the DOWN row makes the route push DOWN on both paths;
  - with all four tried, there is one failed row.
- **Assertions (pre-approved, report each with before/after):**
  1. an existing assertion that pins the label, count, order or numbering of option rows **for an exit with no recorded direction**, and changes only because of this split;
  2. `tests/agent/test_loop.py:1442-1443` (`test_repeated_failure_flags_act_and_replans_once_on_new_evidence`). It assumed one exit row: with O1 the stub picks a new direction after each failure. Replace it with: every approach row the stub picked is `failed` and carries its own failure note, and the rows not picked stay `new`. The `plan is None` checks stay unchanged;
  3. the golden act trace `tests/agent/data/w0_golden/act/trace.jsonl`, regenerated with the test's own regen switch (`W0_REGEN`). Allowed only if the diff is limited to the exit row's label suffix `, then press <DIR>`, the intent `>DIR`, `push` in the action, and option numbering. The memory stores must be identical. Save the diff to `.tmp/r11/`.

  Anything else that fails is a stop.
- **A3:** `scripts/points.json` `label_re` updates for those exit rows only; report each one.
- **Gate:** G-door (options level, E0 section) and A3.

## MC: act micro-check on O1 rows (≤ 20 asks, scratch; the gate for LV11)

`.tmp/r11/mc_act.py`. Charge every post through `scripts.run_agent._ledger`, as `.tmp/r7/gm_check.py` does. Use the act model and prompt the runs use: `agent.act.act` with the options from `World.options`, built on a checkpoint copy plus that step's recorded perception (perceiver reply in `calls.jsonl`).

**Cases** (3 tries each, 15 asks, at most 5 spare for parse retries):
1. r10 step 52: the doorway with "bottom wall", nothing tried.
2. r10 step 53: as 1, with LEFT recorded by `push_failed`.
3. r10 step 52 with the placement removed: nothing tried. This one is informative only.
4. The step in `m1_r6_live_150` where Red first leaves house 1F (truth map 37 → Pallet Town), rebuilt as an uncrossed doorway with its perceived wall.
5. The lab exit: a step in `m1_r8_resume115` after the party (truth `party ≥ 1`, map 40) with the lab doorway on screen, rebuilt as uncrossed, with its perceived wall.

**Truth direction:** the direction of the recorded crossing for 4 and 5, and DOWN for 1–3 (truth: map 37, (2,7) is the door row).

**Go for LV11:** on cases 1, 2, 4 and 5, at least 2 of 3 tries pick the row with the truth direction. Report every reply's `why`. **No-go:** stop and report; do not run LV11.

**Result (2026-10-22): no-go.** See Status; superseded by F1 + MC2.

## F1: the direction rows say what is known that way (A), plus one generic prompt clause (D)

**A: a direction fact in the label.** In the O1 row loop (`world.py:1078-1090`), each direction row's label gains `: <fact>` after `then press <DIR>`. The fact comes from memory only:
- **toward the wall:** `toward the <wall>` when the exit has a stored wall and `DIR == outward(mem, place, tile, wall)` (`space.py:57`, the existing mapping);
- **the neighbour tile in DIR** (`tile + DIRS[DIR]`), first match:
  - holds an exit record: `onto a known exit to <place label>`;
  - walked floor: `back onto floor you walked`;
  - anything else: `onto ground not yet seen`.
- **Join:** with `, `. For example, `go to doorway (bottom wall), 1 right, then press DOWN: toward the bottom wall, onto ground not yet seen` and `…, then press UP: back onto floor you walked`.
- **Limits:**
  - one small helper at most, beside the loop;
  - no game words;
  - notes are unchanged (`exit, never crossed`, plus the tried fact);
  - an exit with no tile gets no fact.

**D: one generic clause in the act `SYSTEM`** (`act.py:6-11`).
- **Wording:** `to go through an exit on a wall, step onto it and press toward that wall`.
- **Placement:** append it with `; ` inside the third sentence, after the name-keyboard clause, so that `len(SYSTEM.split(". ")) == 4` (`test_act.py:71`, `:87`) still holds.
- **Constraints:** it adds no new sentence, contains no `. `, and includes none of the banned words (`test_act.py:72`).

**Tests:**
- the r10-shaped room (door at the end of a floor row, below unknown, "bottom wall") gives `then press DOWN: toward the bottom wall, onto ground not yet seen` and `then press UP: back onto floor you walked`;
- a neighbour that holds an exit says `onto a known exit to <place>`;
- `SYSTEM` contains the clause, and the existing `test_act.py` assertions pass unchanged.

**Assertions (pre-approved, report each with before/after):**
- O1's label class, now including the `: <fact>` suffix;
- `scripts/points.json` `label_re` for O1 exit rows (r6:64);
- the golden act trace, regenerated with `W0_REGEN=1`. Allowed only if its diff is limited to that exit row's label and option numbering. If `SYSTEM` is stored in the golden data, that one string may change too. Memory stores must be identical.

Anything else that fails: revert F1 and stop.

**Gate:** suite; G-door checks 1–4 still pass; A3 has no FAIL.

## MC2: act micro-check after F1 (≤ 15 asks, scratch; the gate for LV11)

Same script, model, prompt path and ledger charging as MC (`.tmp/r11/mc_act.py`).

**Cases:**
1. r10 step 52, "bottom wall", nothing tried (3 tries).
2. r10 step 53, LEFT recorded (3 tries).
3. r10 step 52 with no placement (1 try, informative).
4. r6 step 64, the rug, "bottom wall" (3 tries).
5. **New:** a lab exit with a leave goal (3 tries).
   - Take the step before the verified lab-exit crossing in `m1_exit_b1_live_150` (departure `p_place_5@0,0`, DOWN, step 139, truth map 40 → 0). Use the latest act call before it whose recorded focus has an open goal (the `Goal:` line is not `(none)`).
   - If that run has none, try the other runs with a verified departure from truth map 40 (`m1_r3b_*`, `m1_r4_resume100`, `m1_rcr_*`) the same way.
   - Rebuild the lab doorway as uncrossed: drop the crossing and exit records written at or after that step from the checkpoint copy. Keep the recorded perception and focus as they are.
   - **Do not write a goal or fix the perceiver.** If no run has such a state, report it and judge MC2 on cases 1, 2 and 4 only.

**Cap:** 13 posts, plus at most 2 for parse retries. Stop at 15.

**Go for LV11:** at least 2 of 3 tries pick the truth-direction row on each of cases 1, 2, 4 and 5 (or on 1, 2 and 4, if case 5 has no valid state). Report every reply's label and `why`. **No-go:** stop and report.

## Q1: navigable places only as targets

- **Change:** `World.ref` (`world.py:585-586`) resolves a place id only when that place's `screen == "world"`, the same rule as things at `:587`. The `target` op on "place 1" then returns `_no_target` with the closest labels.
- **Tests:**
  - `ref("p_place_1")` for a title-screen place is `None`;
  - a world place still resolves;
  - `eval_plan --check ops` on `m1_r8_resume115` still passes.

## TC: teleport and cutscene audit (D6; 0 asks, report only)

`.tmp/r11/cuts.py <run>` over `m1_r6_live_150`, `m1_r10_boot`, `m1_r8_resume115`, `m1_r4*` (whichever exist). For every trace row whose `next_place != place`, or with `scene_cut` true, print:
- the step and whether a direction was pressed (`presses.jsonl`);
- how the place was matched (view / exit / relation / new, from the `places` and `views` rows);
- whether a tile exit was written at that step;
- truth `map` before and after.

Summarise:
- cuts with no direction;
- new places minted for a map already seen (truth map seen before, under another place id);
- exits written where truth shows no door (a map change during a text box).

No code change this round; the findings go into the report.

---

## LV11: clean-boot live run (≤ 400 asks)

```
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r11_boot --steps 150 --max-calls 400 --decide
```

**Precondition:** E2, E3, O1 and F1 are committed, G-rank and G-door passed, and MC2 said go.

**Success:** a party by step 150 (truth), from boot. This has never happened.

**Report:**
- first party step;
- `m1_gate_pass`;
- places reached, with the step each was first entered;
- asks and seconds per step;
- every crossing attempt, with its push sequence, the O1 row picked, the act model's `why`, and the attempt that crossed;
- every `pushed` row at the end;
- exits refuted or demoted;
- the planner's `target` ops.

## Order
1. E0 baselines (both parts). Done.
2. E1. Done (`a7e051c`).
3. E2, then G-rank. Done (`a05f04a`).
4. E3 (caller-recorded push; no skills change).
5. E4.
6. E5.
7. O1, then G-door (options level) and A3.
8. Q1, then plan replay.
9. TC.
10. MC (≤ 20 asks). Done: no-go.
11. F1, then the suite, G-door and A3.
12. MC2 (≤ 15 asks), then the go / no-go.
13. LV11, only on MC2 go.

Each part: code, suite (`-n auto`), measure, commit. Revert it if it fails its gate or is worse than the part before it.

## Validity check (2026-10-22, HEAD `8136df2`)

**`agent/space.py`:**
- `:6` `_new` (`pushed: []`); `:9-30` `fold`; `:31-37` `mark_exit`;
- `:56-61` `outward` (already takes `placement`); `:62-69` `frontier` (reads `pushed`).

**`agent/world.py`:**
- `:234-244` `_refuted` (`:243` outward term); `:350-362` `after`; `:363-369` `_cross` / `_count`;
- `:515-521` `_paths`;
- `:580-600` `ref` (`:585-586` places, `:587` the things filter);
- `:755-807` `_exits` (`:774` tile exit record, `:778` thing record with `dir ""`, `:800-801` status);
- `:999` `options`;
- `:1182-1207` `plan` (`:1203` push, `:1205` cross leg);
- `:1208-1241` `travel` (`:1219-1235` expect leg, `:1223` no cut);
- `:1276` merge unions `pushed`.

**`agent/loop.py`:**
- `:184-213` the action handling (`go_to`, `thing`), `:190` `World.plan` call;
- `:268-286` `_settle`;
- `:449-466` `_approach_route` (`:465` the push, `push_dirs` since E2).

**`agent/world.py` for O1:** `:1005-1030` `option`; `:1054-1070` the exit rows; `:1144-1146` the state sort.

**`agent/act.py`:** `:47-69` `act` (the options-only reply; a row's `action` is passed through as is).

**`agent/skills.py`:** `:45` `_crossing`; `:78-143` `approach` (`:101-109` `last_dir` push; `:138-142` route push).

**`agent/memory.py`:** `:145-182` `match_place`; `:238` `observe`.

**`agent/perceive.py`:** `:26-28` placement prompt; `:44` schema; `:82` normalize keeps it.

**`agent/notebook.py`:** `:112-119` `target` op (uses `_resolve` → `World.ref`).

**Tests:** `tests/agent/test_exits.py:144-148` (D4).

**Runs and ledger:**
- `runs/m1_r10_boot` loops at steps 52–215;
- ledger 6998 / 7854;
- suite 740 passed, 1 skipped.

## Known limits (not this round)
- A teleport into a known place on a tile with no stored view still mints a duplicate (TC sizes it).
- Scripted walking drifts the dead-reckoned position.
- Character identity (tracker) and pixel identity for objects (B reverted in r10).
- Feeding failures of non-exit actions back to the planner (option C from the r11 discussion).
