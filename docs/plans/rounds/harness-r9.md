# Avatar harness round r9: characters keep one identity (I-B)

**Summary (5 lines)**
1. **r8 passed M1 from the lab resume** (party at step 137). Characters still split into many rows. In `runs/m1_r8_resume115`, 116–150, the 7 RAM sprites of the lab got 20 id switches and up to 4 rows each; r6 had 8 switches. I-A stopped characters taking over objects, so the noise now shows up as new character rows.
2. **The pixel tracker does nothing in the lab.** `check_tracks.py` gives identical numbers with the tracker on and off (`--baseline`): 20 switches, the same records.
3. **Cause 1: the perceiver's character points are often a full tile off.** On the 39 character perceptions in 116–150, the point is 14–18 px from the real sprite 16 times. Tracks are seeded at that point (`World._follow` → `Tracker.seed`), so a seeded track can sit on the floor next to the sprite. Only 16 of the 39 perceptions had a track under them.
4. **Cause 2: the matching gate for characters is 1 tile** when the row was seen in the last 3 steps (`memory.py:284`). Two readings that are each a tile off can be 2 tiles apart. Of r8's 12 character mints that had a tile, 7 had a free character row exactly 2 tiles away, and 3 more at 3 tiles.
5. **Options below (user picks); everything is 0 asks to measure.** The measure is `check_tracks.py`'s per-RAM-sprite records and switches, which are truth-based and already exist.

| id | title | files | asks | approval |
|---|---|---|---|---|
| IB1 | A character matches the nearest free character row within 3 tiles | `agent/memory.py`, `tests/agent/test_memory.py` | 0 | **option** |
| IB2 | A character's track is seeded on the sprite, not on the perceived point | `agent/track.py`, `agent/world.py`, `tests/agent/test_track.py` | 0 | **option** |
| IB3 | The track owns character identity; the perceived point only names it | `agent/memory.py`, `agent/world.py`, tests | 0 | **option** (needs IB2) |
| LV9 | Live check: lab resume or clean boot | run data | ≤ 120 / ≤ 400 | **user picks** |

---

## Evidence (0 asks)

**`check_tracks.py <run> --first 116 --steps 150`** (replays the recorded keys from checkpoints and compares ids with RAM sprite slots):

| run | id switches (total) | records per sprite slot (max) | static jitter p95 |
|---|---|---|---|
| r6 `m1_r6_live_150` | 8 | 2 | 1 |
| r8 `m1_r8_resume115` | 20 | 4 | 2 |
| r8 with the tracker off (`--baseline`) | 20 | 4 | 2 |

**Per-step instrumentation** (`.tmp/r9/track_hits.py`, scratch; r8, 19 lab world steps):
- 39 character perceptions; 16 had a live track within one tile.
- Distance from the perceived point to the nearest real sprite: 1–3 px in 23 perceptions and 14–19 px in 16 (a full tile off), for example at 119, 122, 147 and 150.
- At 122 and 150, both characters were 16 px off the sprites, and the tracks sat 16 px off as well: the tracks follow the perception's mistake.

**Mints (`memory/mints.jsonl`)**, character rows with a tile in the lab, nearest *free* character row:

| run | character mints | at 2 tiles | at 3 | at 4+ |
|---|---|---|---|---|
| r8 | 12 | 7 | 3 | 2 |
| r7 | 6 | 1 | 1 | 4 |

r8 also minted 42 object rows within 1–4 tiles of an existing row (31 at 1 tile). That is object projection jitter (p95 2 vs r6's 1), a separate item, not in this round. The 88 r8 mints without a tile are menu and keyboard entries (48 at step 137, the nickname keyboard) and are harmless.

**Replay caveat.** `check_tracks.replay` reloads a checkpoint every 5 steps and clears the tracker (`check_tracks.py:31-32`). That happened at 121, 126, 141 and 146, the steps with 0 live tracks. A live run keeps its tracks, so the replay *understates* the tracker. It still cannot explain the 14–18 px seeds.

---

## IB1 (recommended first): characters are conserved within 3 tiles
**What.**
- In `Memory.observe`'s pair gate (`memory.py:284-286`), the gate for a character row becomes `min(4, 3 + (step - last) // 4)` instead of starting at 1.
- Pairs are still taken cheapest first (`:289-292`), and a row is used once per frame, so the nearest character keeps its row.

**Why.** It is one number, and it matches the measured noise: two readings each a tile off sit up to 2 tiles apart. 10 of r8's 12 character mints had a free row within 3 tiles.

**Risk.** Two characters within 3 tiles can swap rows when one reading is off. The truth metric counts swaps as switches, so the measure catches it.

**Go (0 asks).** On r8, r7 and r6, 116–150:
- total switches at or below r6's 8 on r8;
- no run gets worse than its HEAD numbers;
- max records per slot ≤ 2.

**Test.** A character perceived 2 tiles from its row, seen last step, keeps its id. Two characters 1 tile apart, both perceived, keep their own ids.

## IB2: seed tracks on the sprite
**What.** When `Tracker.seed` starts a new character track, it looks at the perceived cell and its 8 neighbours on the sprite grid (`phase`). It picks the cell whose 16×16 patch differs most from its mean colour (a sprite against flat floor), when that cell's score is clearly higher than the perceived cell's. Otherwise it keeps the perceived cell. `near()` and `_hits` are unchanged.

**Why.** The tracker then sits on the sprite, so the next noisy reading still lands within a tile of it (`_hits`) and wins the match (`memory.py:278`).

**Risks.**
- "Most detail" is a heuristic: a patterned rug or a bookshelf can beat a sprite. It is generic (no game names), but its ceiling is real; mark it `ponytail:`.
- It is not measurable with the 5-step reload unless the measure also counts `px_to_sprite` for tracks: extend the scratch script, not `check_tracks.py`.

**Go (0 asks).** On r8 116–150, tracks within 4 px of a RAM sprite in at least 80% of live-track samples (today they inherit the 16-of-39 off-tile points), and switches no worse than IB1 alone.

## IB3: the track owns character identity
**What.** A character perception with a track under it takes the track's row, as today. A perception with no track mints only if no free character row of the place is within 3 tiles (IB1). Perceived names and kinds feed SA's vote only. In effect: IB1 + IB2, plus `_follow` never re-keys a live track to a new row id.

**Con.** It is the largest of the three and depends on IB2's heuristic. Take it only if IB1 + IB2 still leave switches above r6's.

**Recommendation: IB1, measure; IB2 only if IB1 misses the go.** IB3 is a later round.

---

## Decision (user, 2026-10-22): IB3 now, then a clean-boot live run

### IB3 spec (three parts, each measured)
- **IB3a = IB1.** `memory.py:284`: the character gate becomes `min(4, 3 + max(0, step - row.get("last", step)) // 4)`. Objects keep `STATIC_GATE`.
- **IB3b = IB2, made concrete.** In `Tracker.seed` (`track.py:25-33`), only on the restart branch (line 33) and only for `actor=True`:
  - Only when `self.phase` is known: with no phase there is no sprite grid (`test_no_phase_keeps_raw_point` and `test_near_matches_within_a_tile_and_skips_used` seed without a phase and must keep their behaviour).
- Take the snapped perceived point and its 8 neighbours on the phase grid (± `tile_px`).
  - A cell is *background* when its 16×16 patch occurs exactly at another cell of the same grid on this frame (floors and walls repeat; a sprite standing on them does not).
  - Seed at the first non-background cell, in this order: the perceived cell, then the neighbours by distance. If all 9 are background, seed at the perceived cell, as today.
  - Mark it `# ponytail: repeated-patch test; a unique floor tile beside a sprite can win, swap for a sprite mask if one is available`.
- **IB3c: no second track on one sprite.** In `seed`, an actor id that has no live track is not seeded when another id's live actor track lies within `tile_px / 2` of the chosen cell. The sprite already has a track, and through `_hits` (`world.py:169-183`) and the track-first match (`memory.py:278`) that track's row keeps winning.

Perceived names and kinds still feed SA's vote only. I-A's class lock stays.

### IB3 measure (0 asks)
`check_tracks.py` reloads a checkpoint every 5 steps and clears the tracker, so the tracker parts need a no-reload replay. The scratch `.tmp/r9/track_hits.py` gets a `--no-reload` flag: load only the first checkpoint, then replay the recorded keys. Check `diverged at 0 steps` before trusting it.

Metrics, per run (r6, r7, r8; 116–150), at HEAD and after each part:
1. id switches and max records per sprite slot (`check_tracks.py`, plus the same counts in the no-reload replay);
2. live character tracks within 4 px of a RAM sprite / all live character track samples;
3. character perceptions with a track hit / all character perceptions.

**Go before the live run:**
- r8 switches ≤ 8 (r6's level) in the no-reload replay;
- no run worse than at HEAD;
- metric 2 ≥ 80% after IB3b.

If IB3b or IB3c makes metric 1 worse than the part before, revert that part (do not commit it) and continue with the rest. If the go fails, report and skip the live run.

## LV9: live check (user picks)
- **Lab resume** (`runs/m1_r6_live_150` checkpoint 115 → `runs/m1_r9_resume115`, 35 steps, `--max-calls 120 --decide`). It compares with r7 and r8 directly. M1 has already passed from here, so it mostly confirms there is no regression.
- **Clean boot** (`runs/m1_r9_boot`, 150 steps, `--max-calls 400 --decide`). This is the real M1 test, which has never passed from boot. r6's boot run used 243 uncached asks in 150 steps. The ledger has 1103 left (6751 / 7854).

**Recommendation: clean boot.** The resume has already shown the starter; boot is the open question.

**Report:**
- first party step;
- `eval_run`, `eval_moves` and `eval_entities`;
- `check_tracks.py` switches on the lab steps;
- asks.

## Order (decided)
1. Baseline: `check_tracks.py` and the no-reload replay on r6, r7 and r8, 116–150.
2. IB3a: change, suite, measure, commit.
3. IB3b: change, suite, measure, commit unless worse.
4. IB3c: change, suite, measure, commit unless worse.
5. Go check.
6. A3 dry on all 11 cases.
7. LV9: clean boot, `runs/m1_r9_boot`, 150 steps, `--max-calls 400 --decide`.

## Validity check (2026-10-22, HEAD `ed20630`)
- `agent/memory.py:284-286`: the character gate `min(4, 1 + (step - last) // 4)`; `:278` track hit wins; `:280-281` the class filter (I-A); `:289-292` greedy cheapest-first assignment.
- `agent/world.py:169-183` `_hits` (within `tile_px`); `:184-186` `_follow` seeds from `e["at"]`.
- `agent/track.py:25-33` `seed` (snaps the perceived point to the phase grid; a new track's patch is cut there); `:34-40` `near`.
- `scripts/check_tracks.py:31-32`: the checkpoint reload clears the tracker every 5 steps (the replay caveat).
- Ledger 6751 / 7854.
