# Tracking layer (lean): know where "you" are from pixels

Status: **approved 2026-10-12 (lean version, after audits by GPT-6 Astra and Opus 5.5)** · Branch: feat/lean-agent · Implementation: [TL.md](TL.md)

## 1. The failure being fixed (L1, verified against `runs/m1_long`)

1. The perceiver labelled the wrong sprite `player`; both P3 and L1 picked Mom in house 1F.
2. `run_affordance` took `player_at` straight from that label (`agent/skills.py:219`). `approach` therefore steered the real player into the RIGHT/UP walls for 33 steps.
3. `World.fix_player` never fired: it needs ≥ 3 remembered sightings or a W4 `self_spot`, and a fresh run had neither.
4. The act model saw "blocked" and "screen unchanged ×5" every step and repeated the move anyway.

**Not the cause:**
- **Walls.** `tiles.jsonl` holds the correct UP/RIGHT walls at `p_world@0,0`, because `fold` builds walls from scroll, not from player identity.
- **Entity identity.** The 68 duplicate furniture rows are a naming bug. The perceiver echoes `furniture#2` from memory context, `_base` strips only one `#n` (`memory.py:23`), and each new row becomes `furniture#2#4`, which never matches again. `observe` also runs twice per step.

## 2. Measured facts (0 requests, real emulator, checkpoint `runs/m1_long/checkpoint/000070`)

- Walking scrolls 2 px every other frame, 16 px per tile. `observer.scroll` was exact on 48/48 frames, at ~10 ms/frame (±4 px).
- One LEFT step, scroll-compensated: **202/230** persistent residual pixels sit inside the player's 16 px box. This is one measurement, so the lock rule treats it as a hypothesis with an unlock path.
- At that spot LEFT and DOWN are open and RIGHT and UP are walls.

## 3. Design

**Principle:** the vision model names things and reads text. Code decides where "you" are, from how the screen responds to your own input (contingency).

### 3.1 Self spot from motion (camera-follow games)
- `observer.residual(frames, shifts)`:
  - compares each frame of a move with the first frame, shifted by the cumulative scroll;
  - heats pixels that keep changing;
  - returns the box holding ≥ 80% of the heat, if it fits inside 1.5 tiles; otherwise `None`.
- `skills._step` already holds a move's frames and shifts. It adds `self_at` (the box centre as a screen fraction) to the move record. `SkillResult` carries the last non-`None` value.
- **Lock (`World.self_spot`):**
  - set by the first move whose camera scrolled ≥ 1 tile and whose residual has one box;
  - kept across cuts and menus;
  - **unlocked only on contradiction**: 2 consecutive scrolled moves whose box is > 1 tile from the spot.
- **Use:**
  - `World.fix_player` already relabels the perceived player to the self spot;
  - `World.before` uses the self spot as `pa` (tile projection and travel) when locked;
  - `run_affordance` therefore steers from the true position.
- **Not built:** fixed-camera mode, blocked-move facing evidence, per-frame tracking of other sprites. See §5.

### 3.2 Probe (information only)
- **When:** on a world screen, with no overlay (`screen == "world"`), the self spot unlocked and the tile size known.
- **What:** before the act call, `World.probe(ctx)` presses directions until one move locks the self spot. At most 4 presses.
  - It prefers directions not recorded as blocked at the current tile.
  - It stops at once on a cut, a blank frame or a screen change.
- **No undo** (it could step onto a warp). Moves are folded into the map as normal.
- **Recorded** in the trace as `probe {presses, locked}`. It never picks goals.

### 3.3 Fixes found by the audits
- **Naming:** `_base` strips all trailing suffixes (`(#\d+)+$`). One `Memory.observe` per distinct perception: `World.before` reuses the ids from `observe_after` when the perception is the reused cached one.
- **Scroll:** in `_step`, if any frame shift is `None`, use the whole-move scroll. If that is also `None`, the move is `None` and the position is not advanced.
- **Map:**
  - a successful crossing removes that direction from `blocked`;
  - exits with `src == "inferred"` do not block `route` or `frontier`, because `mark_exit` invents reverse doors (L1 got a false LEFT exit at the stairs).
- **Re-perception:** after 2 consecutive no-effect actions, re-perceive once with an added line ("Your last labels may be wrong; look again at who the player is"). The prompt change also changes the model disk-cache key.
- **Streak:** the `recent:` flag reports the true no-effect streak (`Stuck.n`), not a 5-row window.

### 3.4 Facts for the act prompt (information only)
One flag line, built from the `tiles` row at `World.pos`, e.g.:
`here: blocked RIGHT, UP; open LEFT, DOWN; untried none; self locked`

### 3.5 Deleted
- W4's `classify`, `_learn` and `evidence`.
- W4's `self_spot` writer, replaced by 3.1.
- `World._anchor`'s 3-sighting rule; it stays only as the fallback `pa` while the self spot is unlocked.

## 4. Evaluation (reported, not blocking; see TL.md)
- **`self_spot_accuracy`:** world steps whose self spot lies within 8 px of truth sprite 0's centre, plus `self_spot_coverage` (locked world steps / world steps). This replaces `player_role_errors` as the identity metric, which relabelling makes trivially 0.
- **`--from-step N`:** health is scored on the resumed segment only.
- **Walls:** a recorded blocked side later contradicted by a crossing counts as a false wall.
- **Dropped:** "seen in bounds" (the viewport shows padding outside small maps; not fixable by construction).

## 5. Deferred, with triggers

| Item | Add when |
|---|---|
| Object tracks, two-stage association, NCC tracking, lifecycle | identity errors remain after the naming fix |
| Fixed-camera self mode, blocked-move facing evidence | the first game whose camera does not follow (STS2 has a cursor instead) |
| Overlay mask, `wait_settled` frame feed | measured misses caused by overlays or settle-time motion |
| Tile-hash place recognition | place identity errors after TL |
| Appearance memory, OpenCV trackers | per the user's 2026-10-12 hold |
