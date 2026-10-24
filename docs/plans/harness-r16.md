# Avatar harness round r16: a wall needs the player to have tried; unconfirmed walls are re-tested when they trap you; the agent's own answers are part of the conversation

**Summary**
1. **Source.** LV15 (`runs/m1_r15_boot`): M1 passed at step 126, then steps 180–221 (42 steps) were spent walled in at Oak's lab `p_place_6@0,-5`, and q9 "Give a nickname" stayed open for 26 steps after the agent had answered NO.
2. **Walls (W1–W3):**
   - W1: a press only teaches a wall when the player's own sprite reacted and nothing around it changed; anything else is "no response";
   - W2: a walk stops at the first no-response press, instead of pressing into a dialogue box;
   - W3: a wall bumped on only one step is unconfirmed; when remembered walls leave no route, the walker re-presses the unconfirmed ones. Its press is re-judged by W1, and a move erases the wall.
3. **Conversation (N1):** a choice the agent makes inside a dialogue is written into that conversation, so the planner sees the question *and* the answer.
4. **Asks:** 0 for every part and gate. Then LV16: a clean boot, 300 steps, ≤ 800 asks.

**Principles (unchanged):** facts and legal options only; no gates, filters or hidden rows; no game words or RAM reads in `agent/`; edit in place, reuse helpers, no new modules; truth[N+1] ↔ trace tile[N].

---

## Diagnosis (offline, 0 asks)

### What happened at LV15 step 144
Replayed from `checkpoint/000140/pyboy.state` with the trace's presses, by `.tmp/r16/g/replay144.py`:

| press | scroll | pixels changed during the press | RAM |
|---|---|---|---|
| DOWN ×3 | (0,−16) each | whole view | y 3→6; the 3rd move sets off "BLUE: Wait RED!" |
| DOWN | (0,0) | only x144–150, y129–133 (the text box's blinking arrow) | y 6, text box up |
| LEFT | (0,0) | same arrow only | text box up |
| UP | (0,0) | same arrow only | text box up |

- `_step` called those three presses bumps, because the frames were not identical (the arrow blinks), so H1's "nothing changed" test never fired.
- `fold` wrote DOWN, LEFT and UP as walls on `p_place_6@0,-5`. At step 180 RIGHT was a real wall.
- Every route from that tile was then refused before pressing anything ("no known route", "no route beside target").
- From `checkpoint/000180`, LEFT and UP both **move** the player. Only RIGHT is a wall.

### This happens every round
In `runs/*/memory/tiles.jsonl`, walls written in a step that ends with a text box or battle:

| run | false-wall cluster |
|---|---|
| r12 | steps 144, 178, 197 |
| r13 | steps 36, 98 (Oak's "Hey! Wait!" in Pallet, 3 walls on one tile) |
| r14 | steps 35, 117 (same Oak scene, 3 walls), 147 |
| r15 | step 84 (same Oak scene, 3 walls), step 144 (above) |

**Cause:** a scripted event freezes the player while the screen keeps changing a little: the box arrow, the "!" bubble, NPCs walking. The bump test only asks "zero scroll, something changed". It never asks *whether the player tried to move*.

### What a real bump looks like
From `checkpoint/000180`, pressing RIGHT into a bookshelf:
- the pixels that change during the press lie **only** inside the player's own tile (x66–78, y60–75; `self_spot` (0.45, 0.451));
- the end frame equals the start frame.

The frozen presses never touch the player tile. Oak's "!" bubble touches the tile **above** the player.

### Rule check on every wall r13–r15 recorded
Replayed every wall-writing step from its nearest checkpoint, by `.tmp/r16/g/validate2.py <run>`. The rule:

> reacted = some pixel inside the player's tile changed during the press, and no pixel in the ring one tile around it changed.

| run | real walls kept / total | false walls kept / total (current code: all kept) |
|---|---|---|
| r13 | 82 / 84 | 0 / 14 |
| r14 | 27 / 30 | 0 / 6 |
| r15 | 39 / 42 | 0 / 5 |
| **all** | **148 / 156** | **0 / 25** |

Of the 8 real walls rejected:
- **3** are Oak's cutscene, where the RAM text-box flag is briefly off between pages. The player is frozen, so rejecting them is correct; the truth label is wrong.
- **5** are bumps with an NPC moving in the next tile. Missing such a wall costs one more bump later, while a false wall can trap the agent for the rest of the run.

---

## Parts

| id | title | files (regions) |
|---|---|---|
| W1 | A wall needs the player's own sprite to react; any other zero-scroll press is "no response" | `agent/skills.py` (`Ctx` + `self_spot`; `_step` `:47-70`), `agent/loop.py` (pass `self.world.self_spot` into both `Ctx(...)` at `:135`, `:261`) |
| W2 | A walk stops at its first no-response press | `agent/skills.py` (route loop `:96-103`, free-walk loop `:104-140`), `agent/world.py` (`travel`: "no response" is not a leg failure to reroute around) |
| W3 | Walls bumped on one step only are unconfirmed; the fallback route may cross them, and a move erases them | `agent/space.py` (`fold` `:9-35`, `route` `:46-63`) |
| W3x | Delete H2's "press X here: remembered wall" rows, which W3 replaces (approved 2026-10-23) | `agent/world.py` (`options`, the H2 rows) |
| N1 | A choice made inside a dialogue is written into that conversation | `agent/loop.py` (`_learn` / where `select` runs; `_flush_conversation` unchanged) |
| T16 | Offline gates, 0 asks | `.tmp/r16/g/` (uncommitted) |
| LV16 | Clean-boot live run | `runs/m1_r16_boot` |

One lane, in order: W1 → W2 → W3 → W3x → N1. All of these touch `skills.py` or `space.py`, so they don't run in parallel.

---

## Item specs

### W1: a wall needs the player to have tried
- **Change** (`_step`), for a press whose total scroll is (0,0) and whose frames are not all identical:
  - OR the per-frame pixel differences against `before` over every in-press frame and the end frame into one mask;
  - `self` = the square ±0.75·`tile_px` around `ctx.self_spot`, **not snapped**. `self_spot` is measured from the player's own motion, so it already carries the sprite's offset from the tile grid (in Pokémon Red the sprite draws 4 px above it, and snapping rejected 156/156 real walls);
  - `ring` = the square ±1.5·`tile_px` around `self_spot`, minus `self`, minus `ctx.actor_boxes()`;
  - every size is in `tile_px` units and the centre is a screen fraction, so the rule doesn't depend on screen or tile size. Ceiling (`ponytail:` note): a sprite larger than 1.5 tiles touches the ring, so its bumps read as "no response". That loses walls but never invents one; the upgrade is to measure the sprite's extent from motion;
  - **bump** when the mask has a pixel in `self` and none in `ring`;
  - otherwise set `end.no_response = True` and return `move=None`, exactly as H1's identical-frames branch does.
- `self_spot` unknown (`None`): no response. That never happened on a world step in r13–r15.
- Keep `ponytail:` notes for the ring's size and the conservative miss: "a missed wall costs one re-bump; widen the ring rule if NPC-adjacent misses matter."
- **Validated** (`.tmp/r16/g/validate_box.py`, the same geometry at tile 16): real walls kept 148/156, false walls kept 0/25. A centroid-only variant kept 4 false walls and was rejected.
- **Tests** (`tests/agent/test_skills.py`, synthetic frames; each case also run at 2× and 3× scale with `tile_px` scaled, and must give the same answer):
  - player-tile-only change → bump;
  - change only far away (box arrow) → no response;
  - change in the tile above the player ("!" bubble) → no response;
  - change only inside an actor box in the ring → bump.

### W2: stop walking when control is gone
- **Change:** in the route loop and the free-walk loop, a `no_response` press returns `blocked` with reason "no response" and `failure={"stage": "route", "reason": "no response", "direction": d}`, without pressing the rest of the route.
- `World.travel` must not reroute a leg after "no response". Control returns to the loop, whose next perception sees the dialogue.
- **Tests:** a route whose 2nd press is no response presses exactly 2 keys and marks no walls.

### W3: unconfirmed walls are re-tested when they trap you
- **Change (`fold`):**
  - on a bump, if the direction is already in `blocked` and this is a later step than the step it was written at, add it to a new list `firm`;
  - a move in a direction removes it from `blocked` and `firm`, as it does today for `blocked`.
- **Change (`route`):** the existing non-strict fallback pass (`strict=False`) also ignores `blocked` directions that are not in `firm`.
- **So:** a route is found whenever unconfirmed walls are all that stands in the way. The walker then presses into them, and W1 judges each press again:
  - a real wall turns `firm` (and the next fallback avoids it);
  - a false wall moves the player and is erased.
- This is the visual re-check, done by the walker with no model call and no option the act model has to pick.
- **No loop:** a direction can fail the re-check only by becoming `firm`, so each wall is re-tested at most once.
- **Tests (`test_space.py`):**
  - a tile walled once on all four sides: strict route `None`, fallback route found;
  - a second bump on a later step makes it `firm`, and the fallback route avoids it;
  - a move erases both.

### W3x: delete H2's re-test rows (approved 2026-10-23)
- In LV15, rows 14–17 ("press UP here: remembered wall …") were shown for 39 steps. The act model cited them and never picked one.
- W3 does the same re-test inside routing, so the rows only add lines to the prompt.
- **Change:** delete the H2 re-test rows and their tests; grep-prove nothing else reads them. The free-buttons row's "blocked directions" note stays.

### N1: the agent's answer is part of the conversation
- **Facts (LV15):**
  - step 127 `select NO` answered "give a nickname to CHARMANDER?";
  - the conversation was saved at step 129 as t14, ending with the question;
  - the planner at step 130 added q9 "Give a nickname", which closed only at step 156.
- **Change:** when a `select` (or a menu choice) runs while a conversation is open, append `(you chose <item>)` to `self.conversation["lines"]`. `_flush_conversation` then saves it with the rest. This is generic, with no game words.
- **Tests (`test_loop.py`):** dialogue → select NO → more dialogue → world. The saved text contains `(you chose NO)` after the question.

---

## T16: offline gates (0 asks)
- **T16-W1:** `validate2.py` rewritten to call the real `_step` with `Ctx.self_spot`, over every wall-writing step of r13, r14 and r15.
  - Pass: **false walls kept = 0 / 25**, real walls kept ≥ 140 / 156.
- **T16-W2/W3:** from r15 `checkpoint/000180` (state + memory copied to `.tmp/r16/`), run the loop's approach to `exit:0,0` with a stub model.
  - Pass: the walker re-tests the unconfirmed walls of `p_place_6@0,-5`, LEFT or UP moves, and the route reaches the exit tile. The tile keeps at most RIGHT in `blocked`.
- **T16-N1:** replay steps 126–130 of the r15 trace through `_learn` + `select`.
  - Pass: t14 contains "(you chose NO)" after "give a nickname to CHARMANDER?".
- Suite: `pytest -n auto -q`, no regressions. The golden act trace must not change.

## LV16: live run
- A clean boot, 300 steps, ≤ 800 asks, with the viewer up.
- **Pass:**
  - no tile with ≥ 3 walls written in a step that ends with a text box;
  - after the rival battle, the agent leaves the lab within 30 steps;
  - no quest opened for a question the agent had already answered.
