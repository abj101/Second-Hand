# OT: Object tracks — keep one id per moving thing

Status: **proposal, awaiting user decision** (2026-10-15) · Branch: feat/lean-agent · Spec: [tracking-layer.md](tracking-layer.md) §5 row "Object tracks" (its trigger, "identity errors remain", is now met)

## 1. The failure

A moving NPC gets a second record (`name#2`) and the old one stays on the map as a ghost.

- **There is no entity tracking.** The only overlay is `World.mark` (`agent/world.py:116`): a red box on the player once `self_spot` is locked from motion. Nothing carries ids for anything else from frame to frame.
- **Identity is re-derived per perception** by `Memory.observe` (`agent/memory.py:256`) from name, kind and projected tile. Actors match only within `min(4, 1 + steps_since_seen // 4)` tiles (`memory.py:270`): one tile if the entity was seen last step. If an NPC walks 2+ tiles, or the vision model renames "girl" to "woman", a new record is minted.
- **Ghosts.** The old record stays `remembered` at its old tile, and nothing marks it gone.
- **Possible false drift.** `observe_after` (`world.py:401-407`) treats uniquely named entities as fixed references. An NPC that walked can read as camera drift (not yet confirmed in a trace).

## 2. What already exists

- **Every frame is available.** `wait_settled` screenshots every tick. `PyBoyEnv.press` screenshots every tick when given `on_frame`; some calls (`skills.py:181,190`) don't pass it yet. The emulator is paused while the models think, so between two perceptions NPCs only move on frames we tick.
- **Camera shift per frame:** `observer.scroll`. **What moved besides the camera:** `observer._residual` (today used only to find the player).
- **RAM truth:** `scripts/truth_pokered.read` gives every sprite slot's screen x/y (`0xC100`). This enables a 0-request evaluation.

## 3. Design: "the vision model names things, pixels track them"

1. **`agent/track.py` Tracker** (mechanical, 0 requests). It wraps the env so every emulated frame goes through `update(frame)`:
   - The camera shift comes from `scroll(prev, frame)`.
   - A static track moves by the camera shift only.
   - An actor track also searches a small window (±2 px per frame plus the shift) for its 16 px patch, scored by mean absolute difference after camera compensation. The patch is refreshed every frame so facing changes don't break it, and the search is restricted to pixels in the residual-motion blob.
2. **Seed.** After each perception, `World.before` has assigned ids. Every entity with an id and a screen point becomes or refreshes a track.
3. **Association, two stages, in `Memory.observe`:**
   - (a) A perceived entity within half a tile of a live track's predicted screen spot takes that track's id. The new name becomes an alias and the tile is updated, so the record moves rather than being minted again.
   - (b) Only unmatched entities fall back to today's name and tile matching.
4. **Lifecycle:**
   - A track that exits at a screen edge becomes `offscreen`, keeping its last tile as "remembered".
   - A track lost inside the view for N frames, not hidden by a dialogue box, becomes `gone` and leaves the map. This removes the ghosts.
   - When it reappears, stage (b) re-identifies it by name.
5. **Drift.** Tracks that moved on their own are excluded from the `observe_after` drift pairs.
6. **Viewer.** The lens shows each entity's track id and its path across the step's motion clip.

## 4. Overlay: your decision

| | What the vision model sees | Cost / risk |
|---|---|---|
| **A. Tracks only (recommended first)** | unchanged frame + red self box | 0 prompt change, the model cache stays valid, 0 requests. Identity is fixed mechanically; the model's names become aliases. |
| **B. A + id tags on the image** | small letter tags beside tracked things (drawn on the 640 px upscale); prompt adds an optional `"ref"` to copy the tag | Prompt change means every perception call misses the cache (paid calls), and tags can hide 16 px sprites. It helps only if act or the planner still confuse which "person" is which. |

The recommendation is A, then measure; add B only if the evaluation shows name or kind flips still hurting the act model.

## 5. Slices (each slice: unit test + targeted tests; full suite at the end)

- **OT1** Tracker core plus env wrapper; synthetic test: a sprite walking across a scrolling background keeps one id.
- **OT2** Two-stage association and lifecycle in `Memory.observe` / `World.before`; ghosts marked `gone`.
- **OT3** Drift exclusion plus viewer track ids.
- **OT4** `scripts/check_tracks.py`: replay recorded checkpoints against RAM sprite truth, using cached perception replies (0 requests). Metrics:
  - id switches per real sprite
  - records per real sprite (target 1.0)
  - ghost count
  - tracker screen error in px
- **L** Short live run in Pallet Town (wandering NPC). **Needs a ledger cap approval.**

## 6. Out of scope

Appearance memory and re-identification across places (on hold); fixed-camera games; the tile-map change from section 4 of the review chat (separate packet).

## 7. Result (2026-10-15)

Option A built (tracks only, no prompt change, no overlay tags). OT1-OT4 done, slice L not run. 0 requests anywhere. Full suite: 609 passed.

- **OT1** `agent/track.py` (new, ~75 lines): `Tracker` with `seed`, `near`, `update`, `gone`, `moved`, `wrap(env)`. The camera shift comes from `observer.scroll` (max 2 px/frame); an actor track centres on the pixels the shift does not explain near it (its own motion); still = patch compared with its last patch; lost frames count to 24 = "gone". `wrap` hooks `env.press` (via `on_frame`) and `env.tick`, and composes with run_agent's `guarded_press`. Tests in `tests/agent/test_track.py`: walker on a scrolling background (error <= 2 px, 40 frames), static track moves with the camera only, offscreen, near/used, vanished sprite -> lost then gone.
- **OT2** `Memory.observe(..., tracks=)`: a perceived entity under a live track takes that track's record (the new name becomes an alias, kind kept). `World._hits` / `World._follow` (called from `before` and `observe_after`) associate, seed tracks, and mark a track lost in view (no text, screen "world", no dialogue_box) as `status: "gone"`; `gone` rows are no longer demoted to "stale" and stay out of the view (existing `eligible` filter). `Loop` wraps its env. Tests: NPC walks 3 tiles and is renamed -> 1 record with alias; lost NPC goes gone only without a text box.
- **OT3** `observe_after` drops tracks that moved on their own from the drift pairs; test shows drift triggers without the tracker and not with it. Viewer lens label adds `[id]` (path not drawn). `World.before` sets `tracker.ignore` to a box on the player so its walk animation is not read as a moved thing.
- **OT4** `scripts/check_tracks.py RUN --first N --steps M [--baseline]`: replays recorded key presses on the emulator from a checkpoint (key presses alone do not reproduce a run, so the replay is only valid until it diverges from truth; the script prints the first diverged step), feeds the recorded per-step perception to a fresh `World`, scores against RAM sprites (`truth_pokered.sprites`, split out of `read`). m1_qsr_smoke steps 51-71 (NPC wandering), tracked vs baseline (`--baseline` = association, gone and drift exclusion off): id switches 1 vs 4; records per sprite 1.50 vs 2.50 (the indoor NPC 2 vs 4; the Pallet NPC 1 vs 1 in this window); ghosts 2 vs 4; tracker error on motion-locked tracks mean 3.3 px, p95 3.4. m1_decide3 steps 51-82: switches 0 vs 2, records 1 vs 2, ghosts 2 vs 4.

Deviations: (1) the association radius is one tile, not half: the model's points are 8-16 px off the real sprite. (2) Tracking uses the motion pixels, not a patch search (patch search latched onto background when the seed point was off). (3) No path drawn in the viewer.
Open issues: replays diverge from recording around step 72/83, so the Pallet NPC wandering several tiles is only covered for a few perceptions; a track is seeded at the model's (inexact) point until it moves; a still NPC whose point is >1 tile off is still matched by name/tile (stage b); the "gone" rule has no menu detection beyond screen != world, text and dialogue_box. Live check (slice L) still needs a ledger cap.

## 8. Slice L result (2026-10-15)

Live run `runs/m1_ot_live`: a copy of `m1_decide3` resumed at checkpoint 100 (Pallet Town, 3 RAM sprites) for 60 steps (101-160). It used 113 requests; ledger 3538 -> 3651 of 3815 (cap +150 approved by the user, commit d5ec697). The agent stayed in Pallet Town: repeated NPC dialogue, with "surprise" verdicts on direction presses.

| Steps 101-140, same start | old code (`m1_decide3`) | OT (`m1_ot_live`) |
|---|---|---|
| new actor records (3 real sprites) | 14 | 9 (none after step 140) |
| actor records still on the map (seen/remembered/stale) | 33 | 4 |
| records marked gone | 1 | 12 |
| renames absorbed as aliases | 0 | 14 |

`check_tracks` replay (it diverges from the live recording after 16 steps): id switches 2 vs 4 baseline, ghosts 4 vs 14, tracker error mean 6.4 px vs 14.0.

Remaining: 9 new records is still about 3x the real sprite count. Likely causes are tracks seeded at the model's inexact point, and still NPCs perceived more than a tile off (see section 7 open issues).
