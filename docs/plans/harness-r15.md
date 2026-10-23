# Avatar harness round r15: follow forced movement, tell "no response" from a wall, keep one id per room

**Summary**
1. **Source.**
   - `docs/plans/review-r14.md` (H1, H2, H4);
   - `docs/plans/diag-h3.md` (F1, F2, H3a, H3c);
   - the user's picks of 2026-10-23: F1, F2, H3a as a fallback, H3c, H1, H2, H4.
2. **Motion (F1, H1, H2):**
   - the camera is followed during every settle, so scripted walks move the believed position and give a cut its direction;
   - a press that changes nothing on screen is "no response", not a wall;
   - a tile remembered as walled on every side offers its walls for re-testing.
3. **Identity (F2, H3a, H3c, H4):**
   - merges carry crossing counts;
   - the view search reaches ±6 tiles, as a fallback to crossings;
   - a newly minted place says what it partly looks like;
   - joined dialogue pages are split per speaker.
4. **Why:** all three r12–r14 room duplicates trace to forced walks the map did not follow (`diag-h3.md`), and LV14's 143-step freeze came from no-response presses recorded as walls (`review-r14.md`).
5. **Asks:** 0 for every part and gate. Then LV15: a clean boot, 300 steps, ≤ 800 asks. The ledger is 8774 / 17854; **stop** at 9574.

**Baseline:** HEAD `9c196fe` plus the docs commit (wave 0). Suite: 812 passed, 1 skipped. `scripts/watch_run.py` and `tests/agent/test_watch_run.py` remain the user's uncommitted edits; leave them untouched.

**Principles (unchanged):**
- Facts and legal options only; no gates, filters or hidden rows.
- No game words in `agent/` (no RAM reads in `agent/`; RAM is for gates and offline checks only).
- Edit in place, reuse the named helpers, no new modules, every cut grep-proven.
- **Alignment for any truth metric:** truth[N+1] ↔ trace tile[N].

---

## Parts

| id | title | lane | files (regions) |
|---|---|---|---|
| F1 | The camera is followed during every settle; a scripted walk moves the position and gives a cut its direction | A | `agent/observer.py` (`Settle`, `wait_settled` both branches), `agent/skills.py` (`press` non-direction keys `:211-217`, `wait_until` `:231-233`), `agent/world.py` (`prepare` `:336-351` only if needed) |
| H1 | A press that changes nothing on screen is "no response", not a wall | A | `agent/skills.py` (`_step` `:46-65`), `agent/space.py` (`fold` `:9-33`), `agent/world.py` (the free-buttons row note in `options`) |
| H2 | A tile remembered as walled on every side offers its walls for re-testing | A | `agent/world.py` (`options`, beside the explore rows `~:1196-1199`) |
| F2 | A merge carries crossing counts to the surviving place | B | `agent/world.py` (`merge` `:1397-1437`) |
| H3a | The view search reaches ±6 tiles | B | `agent/memory.py:88` |
| H3c | A place minted by a cut states what it partly looks like | B | `agent/memory.py` (`seen_view` `:250-264`, the mint at `:234-240`), `agent/world.py` (`here` `~:770-775`) |
| H4 | Joined dialogue pages are split per speaker; a name is confirmed only by a single-speaker conversation | B | `agent/loop.py` (`_flush_conversation` `:466-480`) |
| T15 | Offline gate scripts (emulator states, 0 asks) | A (F1, H1) and B (H3a, H3c) | `.tmp/r15/g/` scripts, uncommitted |
| G15 | Gates | orchestrator | `.tmp/r15/g/` |
| LV15 | Clean-boot live run | orchestrator | `runs/m1_r15_boot` |

**Waves:** one wave, A and B in parallel. Their `world.py` regions are disjoint:
- A: `prepare` if needed, the free-buttons row note, and the new re-test rows next to the explore rows;
- B: `merge` and `here`.

Only B touches `memory.py` and `loop.py`; only A touches `observer.py`, `skills.py` and `space.py`.

---

## Item specs

### F1: follow the camera during every settle
- **Facts:**
  - In r14, steps 118–123, Oak walked the player from the grass to the lab door while dialogue was up.
  - `press("A")` and `wait_settled` measure no scroll, and `prepare` folds only direction-press moves, so belief stayed at the grass. The cut had no exit evidence, and the lab was minted again.
  - Offline (`.tmp/r15/h3/escort.py`, from r14 `checkpoint/000120/pyboy.state`): summing per-frame scroll above the text box gives (−16, −152) px, which is a walk of (+1, +9.5) tiles; the truth is (+1, +10). That puts belief within 1 tile of the known exit `p_place_4@7,6` → `p_place_5`.
- **Change:**
  1. **`Settle` gains `scroll: tuple[int, int] = (0, 0)` and `last_dir: str | None = None`.**
     - In both `wait_settled` branches, for each new frame that is not blank and whose hash differs from the previous non-blank frame, add `scroll(prev, cur)` with the default `max_shift`.
     - Compute it on the rows that are **not a static band**: drop the contiguous top and bottom rows that are byte-identical between `prev` and `cur` (a fixed text box or HUD). If fewer than half the rows remain, skip that frame.
     - Keep the sum, and `last_dir` = the direction of motion of the last non-zero shift. Scroll is opposite to motion; use the same sign convention as `fold`, which is `-shift`.
     - A `None` from `scroll` adds nothing.
     - This is one small helper next to `scroll`: `_moving_rows(a, b) -> (top, bottom)`, used only here.
  2. **`press` for a non-direction key, and `wait_until`, add the end settle's motion as moves.**
     - If `end.scroll` is non-zero, append one move per axis with a non-zero component: `{"dir": <axis direction of motion>, "shift": <that axis's px as a (sx, sy) tuple>, "forced": True}`.
     - Put the axis whose motion came last (`last_dir`) last, so a cut's direction is the last move's.
     - Direction-key presses (`_step`) are **unchanged**. First measure, on the offline PyBoy env, whether `_step`'s own settle already contains the end of the player's own step. If it does, leave it; never count a move twice. Report the measurement.
  3. **`prepare` folds these moves as it already does** (`fold` takes tuple shifts). On a cut, `cut_dir` = the last move's `dir`, which now exists for a scripted walk, so `exit_key` and `match_place`'s exit rule (`memory.py:224`, within 1 tile) apply.
     - Check that `loop.py:344-345` (`if choice and scene_cut … if world_exit: mark_exit`) records the crossing for a forced-walk cut.
     - If `world_exit` excludes it, report it, and do not widen it on your own (stop condition).
  4. **Travel legs are not changed.** Add a `# ponytail:` note that forced motion inside a travel leg is not followed.
- **Tests (unit):**
  - `wait_settled` on a fake env whose frames scroll 16 px per frame for 3 frames, with a static bottom band, sums 48 px and gives the right `last_dir`;
  - a static frame sequence gives (0, 0);
  - `press("A")` on that env returns forced moves that `fold` turns into 3 tiles.
- **Offline gate (T15-F1)**, from r14 `checkpoint/000120/pyboy.state`: drive `advance` through the escort with the real skill code on a `PyBoyEnv`.
  - The summed forced moves must be within 1 tile of (+1, +10) in each axis.
  - With a temp copy of checkpoint 120's memory, `match_place(cut=True, exit=(<belief after the walk>, cut_dir))` from `p_place_4` must return `p_place_5`.
  - Save the script and its output to `.tmp/r15/g/`.

### H1: no response is not a wall
- **Facts:**
  - In r14 at step 149, after Oak's scripted walk-back, the game hid the player (sprite image 0xFF) and ignored arrow keys until START then B (verified offline).
  - The harness recorded three zero-shift presses as walls, so tile `p_place_7@0,-5` was walled on all four sides and 143 steps were refused with 0 inputs.
  - A real bump animates the sprite in place: r13 step 210's frames changed by 96 px with no scroll.
- **Change:**
  - In `_step`, when the press's captured frames and `end.frame` are all byte-identical to `before`, return the move with `shift: None` and `"no_response": True`. Otherwise the result is unchanged.
  - `fold` already skips `None` shifts, so no wall is written and the position does not change.
  - `World` counts consecutive `no_response` moves in `prepare` (reset by any move with a shift).
  - When that count is ≥ 1, the free-buttons row gets the note `"your last N presses changed nothing on screen"`. That is the row from which START or B can be pressed. It is a fact on an existing row; no row is added or hidden.
- **Tests:**
  - identical frames → `shift is None` and `no_response`, and `fold` writes no wall;
  - frames that change without scrolling → `(0, 0)` and a wall, as today;
  - the note appears after 2 no-response presses and clears after a real move.
- **Offline gate (T15-H1):**
  - r14 `checkpoint/000150/pyboy.state`: `press("UP")` → `no_response`;
  - r13 `checkpoint/000210/pyboy.state`: `press("DOWN")` → `(0, 0)`, a bump. This is r13 step 210's real bump at truth (9,5); if that checkpoint's facing makes the first press a turn, press twice and report it.
  - Save the outputs.

### H2: walls that enclose you can be re-tested
- **Facts:** a remembered wall on every side of the tile you stand on is a contradiction, because you got there. Today nothing ever presses into a remembered wall again, so a wrong wall can't be falsified.
- **Change:**
  - In `options`, when the current tile's row has all four directions in `blocked` (and the place is not `unplaced`), add one row per direction: `press D here: remembered wall from step N`, with action `{"buttons": [D]}` and tool `buttons`. N is the tile row's `step`; the row id carries no per-direction step today, so one N for all four is fine.
  - Put these in the `failed` state group, so they sort after everything new.
  - The press runs through `press_seq` → `_step` → `fold`: a real move clears the wall (`fold` removes a direction from `blocked` on a move), and a bump keeps it.
- **Tests:**
  - an enclosed tile offers 4 re-test rows, and an ordinary tile offers none;
  - picking one that moves clears that wall.
- **Pre-approved:** none. Golden: no change is expected, because the golden has no enclosed tile.

### F2: merges carry crossing counts
- In `merge`, for each `crossings` row whose id starts with `f"{src}@"`, write the row under `_cross_key(dst, (x+dx, y+dy), other)`, adding `n` to any existing row and keeping the max `last`.
- For rows whose id ends with `f">{src}"`, re-key them to `>{dst}`.
- The old rows stay as history (the same rule as tiles).
- **Test:** after merging, a crossing counted on the merged place shows as "crossed N×" on the surviving place's exit.

### H3a: the view search reaches ±6 tiles
- `memory.py:88`: `VIEW_MATCH, VIEW_REACH = .9, 6`. That is the only change.
- It also widens the merge pair check at `:284` and every `seen_view` caller (`world.py:418, 467, 490, 508`). That is intended.
- **Offline gate (T15-H3a):** re-run `.tmp/r15/h3/reach.py` and `mint.py` with the new constant imported.
  - **Required:** 0 wrong best-matches ≥ 0.9 among the 41 arrivals, and the r13 step-207 and r14 step-123 mint frames matched to the correct place.
  - Report the closest wrong score.
- **Pre-approved:** none. If an existing test asserts a match fails at 5–6 tiles, stop and report.

### H3c: a minted place says what it partly looks like
- `seen_view` gains `at_least: float = VIEW_MATCH` and returns `(place, tile, score)`. Every caller unpacks the first two; grep `seen_view(` and update each one.
- At the mint (`memory.py:234-240`), when `cut and frame is not None`, call `seen_view(frame, screen, prev, tile_px, at_least=.5)`. If it hits, store `"looks_like": [place, round(score, 2), step]` on the new place row.
- `World.here()` appends `, partly looks like <place_label> (<score>)` while the current place has `looks_like` and that place is not merged. Remove the clause once a merge happens: the merged row is no longer current.
- **Tests:**
  - a mint whose frame scores 0.6 against another place stores `looks_like`, and the Here line shows it;
  - a mint with nothing ≥ 0.5 stores nothing.

### H4: split joined pages per speaker
- **Facts:** r14's t11 was stored with speaker BLUE, but holds "Gramps! … OAK: BLUE? … BLUE: Hey!…" (who None). `_flush_conversation` (`loop.py:466-480`) reads only a leading `NAME:`.
- **Change:**
  - Split `joined` at every `NAME:` whose name passes the existing test: regex `([A-Z][A-Za-z.' -]{0,19}):\s`, at the start or after whitespace, with ≥ 2 letters.
  - The text before the first prefix keeps no speaker.
  - Call `notebook.heard` once per segment, each with its own speaker and the same `who`.
  - The said-tier claim (`:476`) runs only when the conversation has exactly one distinct speaker; several speakers confirm nothing.
- **Tests:**
  - the r14 t11 text gives 4 segments (unlabelled, OAK, BLUE, OAK) and no claim;
  - a single-speaker conversation still confirms, as in r13 I2's test.
- **Pre-approved:** none. If an existing test asserts that a multi-speaker text is one heard row, report it before and after.

---

## G15: gates (orchestrator, 0 asks)
1. Full suite after each merge, with `-n auto`.
2. **Golden.** If it fails, regenerate with `W0_REGEN=1` only when the diff is limited to:
   - F1's forced moves (in `advance` rows);
   - H1's free-row note;
   - H3c's Here clause.
   Commit as `F1g …`. Any other diff: stop.
3. **T15 offline gates** (F1, H1, H3a) must all pass, with outputs in `.tmp/r15/g/`.
4. **Replays:**
   - `points_r14.json` statuses must be unchanged or improved (the G3 case may stay INCONCLUSIVE);
   - `points_r13.json` 6/6 PASS;
   - `points_r12.json` unchanged.

## LV15: live run (orchestrator)
- Check that the viewer is up, then run once:
  ```
  .venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r15_boot --steps 300 --max-calls 800
  ```
  No resume or retry without asking.
- **Success:** a party (truth `party` ≥ 1), then reaching Pallet with the party without walking straight back into the lab within 10 steps. **Stretch:** truth map 12.
- **Report** to `.tmp/r15/report.md`, against r14, with truth[N+1] ↔ tile[N]:
  - party step;
  - each room's place-id count against truth maps (duplicates must be 0);
  - belief-vs-truth offset changes across forced walks;
  - no-response presses, and any wall written on a no-response press (must be 0);
  - steps with 0 inputs;
  - re-test row picks;
  - planner op error rate;
  - asks per step;
  - ledger before and after.
- If the game locks as in r14, report which rows the act saw (with notes) and whether it picked the free-buttons row.
