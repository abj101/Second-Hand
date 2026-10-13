# Tracking layer: the vision model names things; pixels track them

Status: **proposed 2026-10-12, awaiting user approval** · Branch: feat/lean-agent · Follows: [L1.md §9](L1.md)
Decisions in force: build the tracking layer directly (no probe-only stopgap); stuck reflexes are **information-only**; appearance memory (crop signatures, DINOv2) is **on hold**.

## 1. Why

Both live runs (P3, L1) died the same way. The vision model labelled the wrong sprite "player". The agent then walked its real self into a wall for 33 or more steps. Every fallback needed something the failure had already destroyed:
- `fix_player` needs 3 correct sightings.
- W4 needs motion.
- Entity dedupe needs stable names.

The spatial system has never had a measurable live effect:
- 0 `go_to` uses.
- Seen cells 36–39% in bounds.
- **68 duplicate records each for two pieces of furniture over 33 identical frames.**

Its inputs, identity and entity ids, were broken.

The public Pokémon agents hit the same wall. Claude's and Gemini's harnesses fed **RAM** position and tile data to the model: grid overlays, blocked/passable/NPC tiles, a location tag, and a pathfinder over a self-built map. Their authors call navigation the bottleneck and say the scaffolding compensated for weak vision. VideoGameBench's vision-only agents finish 0.48% of its games. We cannot read RAM, so the harness has to produce those facts **mechanically from pixels**.

## 2. Measured facts this design rests on (0 requests, 2026-10-12)

| Fact | Measurement |
|---|---|
| Walking scroll | 2 px every other frame; 16 px per tile over ~16 frames (`runs/m1_long` checkpoint 70, LEFT and DOWN) |
| Existing `observer.scroll` accuracy | 48/48 frames correct, 0 ambiguous; ~10 ms/frame at ±4 px (±2 px suffices per frame) |
| Player found by motion | one LEFT step, scroll-compensated: **202/230** persistent residual pixels inside the player's 16 px box |
| Stuck spot | from checkpoint 70, LEFT and DOWN move the player; RIGHT and UP are walls (the only directions L1 tried) |
| Agent venv | numpy and Pillow only; no OpenCV, no SciPy |

## 3. Research conclusions (six Haiku reports, 2026-10-12)

- **Trackers:** plain NCC template matching in a predicted window beats learned trackers on pixel art. Pixel art has fixed shapes, flat palettes and discrete animation; deep and correlation trackers assume smooth appearance change. The NCC peak doubles as a confidence score. No CV model is needed for Game Boy. For 1080p (STS2), consider OpenCV CSRT, and TrackerNano only after a measured failure.
- **Self:** contingency awareness (Bellemare et al. 2012) means the controlled object is whatever responds to input:
  - camera-follow: it stays fixed while the world scrolls;
  - fixed camera: it moves in the pressed direction;
  - blocked move: only its facing changes;
  - menus: the cursor moves.

  Every ordinary move is a free probe. Active probing is needed only when identity is in doubt.
- **Identity across disappearance:** BoT-SORT's camera compensation, ByteTrack's two-stage association and OC-SORT's gap correction, without a learned ReID:
  - store positions in **world** coordinates;
  - match on centre distance in tiles, with labels as a soft term only (never a hard key; the VLM says rival/RED/player for the same sprite);
  - confirm a track at 2 sightings;
  - static objects never time out (delete only on contradiction);
  - a scene cut suspends a place's tracks; it does not delete them;
  - nothing under an overlay counts as missing.
- **Map:**
  - walkability from bumps and crossings;
  - seen cells from the exact viewport;
  - doors from cut-after-move;
  - frontier = walkable cells next to unknown cells (Yamauchi);
  - tile-hash place fingerprints for exact revisit recognition (deferred).
- **Harness lessons (Claude, Gemini, Whidden):**
  - put position and walkability in harness state and inject them as text, not in the model's head;
  - give the model a pathfinder over a self-built map;
  - label doors automatically;
  - add a stuck detector;
  - do not ask the model to draw maps;
  - keep goals checked against the screen.

## 4. Design

New module `agent/track.py` (numpy only). It owns everything mechanical about what is on screen and where. The vision model keeps names, text, affordances and meaning.

### 4.1 Frame feed
- `Tracker.feed(frame, key)` runs on **every emulator frame during an action**, via the existing `env.press(..., on_frame=...)` hook (`skills._step` already collects these frames and throws them away). It also runs on travel legs.
- `Tracker.settle(frame, step)` runs once after `wait_settled`. It returns the snapshot the loop uses (see 4.7).

### 4.2 Camera
- **Scroll per frame:** `observer.scroll(max_shift=2)` computed outside an exclusion mask (self box, overlay rows, HUD regions).
- Accumulate `cam_px` per place, and keep it in place-local pixels.
- **Fades and cuts:** the existing `_blank`/`cut`. On a cut, the current place's tracks are suspended and a new or matched place starts (the P3b cut rule stays).
- **Confidence:** an ambiguous scroll (`None`) on a frame marks the step's camera as unsure. Map writes are skipped for that step, and nothing is guessed.

### 4.3 Self (contingency)
- **Evidence per action, three modes:**
  1. *Camera-follow:* the camera moved; the persistent scroll-compensated residual concentrates in one sprite-sized box. That box is the **self spot**. The measured 202/230 case is this mode.
  2. *Fixed camera:* the camera did not move, and one blob moved one step in the pressed direction. That blob is self.
  3. *Blocked:* nothing moved, and a local appearance change (facing) appears in one box. This is weak evidence at that spot.
- **Lock:** decided after 1 mode-1 or mode-2 action, or 2 agreeing mode-3 actions.
  - While locked, the self spot (camera-follow) or the self track (fixed camera) defines the player.
  - The vision model's `player` label is **ignored for identity**. If it points elsewhere, that entity is relabelled `character`, and a `player_label_wrong` counter goes to the trace.
  - Self templates (one per facing seen) are captured while locked. They are only used to re-confirm after cuts. This is not appearance memory for other objects.
- **Unlock:** after a scene cut, a menu/battle screen change, or 2 actions whose evidence contradicts the lock.
- **Probe reflex** (information only): on a world screen with self unlocked, before the next model action:
  - press directions until one mode-1 or mode-2 action occurs (at most 4 presses);
  - prefer directions known to be open;
  - undo with the opposite direction when it is known free.

  The probe never chooses goals. Its result is recorded as facts (4.6).

### 4.4 Tracks and association
- **Track fields:** `{id, place, world_px box, label, kind, anchor (self|actor|world|screen), state (tentative|confirmed|lost|suspended), first, last_seen, sightings, misses}`.
- **Between model calls:** world objects move with the camera, so their screen position = world − cam with no per-frame matching.
- **Moving sprites** (blobs not explained by the camera, not self):
  - NCC-tracked per frame in a ±1-tile window, with the template captured on first sight;
  - the per-frame motion classifies them as `actor`;
  - this replaces W4's step-pair evidence.
- **Once per step, vision-model entities → tracks** (two stages):
  1. Fresh entities vs confirmed tracks in view. Cost = centre distance in tiles (gate ≤ 1.5 tiles); label similarity (base name, `memory._base`) is a tie-break only. Greedy by cost; no SciPy.
  2. Leftovers vs lost and suspended tracks of the same place (gate ≤ 3 tiles).
  3. Still unmatched → tentative; confirmed on the 2nd sighting. A tentative track missed once is dropped.
- **Lifecycle:**
  - In view and unmasked but not reported → `misses += 1`. Under an overlay or off-screen → no miss.
  - `world` tracks are deleted only after 3 contradictions: the location is visible, its pixels changed, and no entity is there. They never time out.
  - `actor` tracks become `lost` when off-screen, re-associate by world position and label, and are dropped after the place is left 3 times.
- **Memory:** `Memory` entity rows are written **only for confirmed tracks**, keyed by track id. This removes the duplicate source at its root. The tracker owns identity; Memory stores it.

### 4.5 Overlays and screens
- **The perceiver's `screen` field gates tracking:** `dialogue`/`menu`/`keyboard` freeze map writes and track misses.
- **Mechanical overlay mask:** pixel rows/regions that stay identical while the camera scrolls (and are not the self spot) are screen-fixed overlays. The mask excludes them from scroll, residuals and misses.

### 4.6 Map writer (mechanical; replaces guessed seen cells)
- **Per place, a tile grid** with fields: `{state: unknown|walkable|blocked-side(dir), seen_step, occupied_by (actor id)}`.
- **Crossing:** the camera moved a full tile (or self moved a tile in a fixed camera) → walkable source and destination.
- **Bump:** a direction was pressed, no scroll and no self displacement → that side is blocked. This is recorded only when self is locked, which removes the L1 failure where walls were inferred from the wrong player.
- **Seen:** the exact viewport rectangle from `cam_px`, minus the overlay mask. In bounds by construction.
- **Doors:** cut after a move → an edge with the departing tile and direction (existing `mark_exit`).
- **Frontier:** walkable cells with an unknown neighbour. Exposed to `World.plan`/`travel`, which already route over tiles.
- **Facts for the act prompt:** `you are at (x,y)`; `tried here: RIGHT blocked, UP blocked; LEFT open, DOWN open`; `nearest unexplored: 3 tiles west`. Information only.

### 4.7 Loop integration
- `Loop.step`:
  1. `tracker.settle` before perception;
  2. `tracker.associate(per)` replaces `fix_player`, `World._anchor`/`anchors`, `World._learn`/`classify` evidence and the entity-id part of `World.before`;
  3. the act prompt lists things with **stable ids** and marks **you**.
- **Stuck reflexes (information only):** after 2 consecutive actions with no effect, (a) drop the perception cache and re-perceive the same frame once; (b) run the probe if self is unlocked; (c) add the map facts above. The `recent:` flag shows the true streak from `Stuck` (`x33`, not `x5`).
- **Deleted when this lands:** `World.fix_player`, `World._anchor` and `anchors`, W4's `_learn`/`classify`/`self_spot`/`cam_px` (superseded), `ACTOR_KINDS` stand-in, entity-id minting in `World.before`.

### 4.8 Trace fields (evaluation)
Per step:
- `self {spot, locked, mode, evidence}`;
- `cam_px`;
- `tracks_in_view [{id, anchor, world_px}]`;
- `player_label_wrong`;
- `map_writes {walkable, blocked, seen}`;
- `probe {presses, result}` when a probe ran.

## 5. Gates: offline first, on real frames, at 0 requests

`scripts/check_track.py` drives PyBoy from saved checkpoints with **scripted inputs and no model**. Truth comes from RAM, for evaluation only: sprite slot 0, SCX/SCY, map x/y, map id.

| Check | Data | Pass |
|---|---|---|
| Camera vs RAM | 4 checkpoints (m1_long 50, 70; m1 54, 94), 40 scripted moves each | 100% of moves: cam delta equals RAM position delta × tile; 0 ambiguous frames on moves |
| Self lock | same | locked within 1 successful move; spot within 4 px of sprite-0 centre; 0 wrong locks |
| Probe from the L1 stuck state | m1_long checkpoint 70, self unlocked | locks within ≤ 4 presses; returns to the start tile |
| Bump walls | same episodes | every recorded blocked side has an unchanged RAM position on that press; 0 false walls |
| Seen in bounds | same | 100% inside the RAM map size |
| Identity on recorded perception | `runs/m1_long` trace entities, steps 38–70 (same frames) | `furniture` records: **1 each** (was 68); `player_role_errors` replayed **0** |
| Overlay | a dialogue checkpoint (m1 step with a text box) | no track misses and no map writes under the box |
| Unit | GridEnv: camera-follow, fixed-camera and blocked fixtures; a wandering NPC; an overlay; a cut and a return | full `tests/agent` green |

**Live gate, after the offline gates pass:** resume `runs/m1_long` from checkpoint 70 for ≤ 110 requests (the L1 second half). Green rows needed:
- `player_role_errors` 0;
- `no_effect_streak_max` ≤ 5;
- `repeat_actions` ≤ 5;
- `seen_in_bounds` ≥ 98%.

Progress (Town, lab) is reported.

## 6. Work packages (proposed)

| WP | Goal | Files | Depends on | Est. |
|---|---|---|---|---|
| T0 | Truth harness: scripted PyBoy episodes from checkpoints, RAM truth, report | `scripts/check_track.py`, `scripts/truth_pokered.py` (+SCX/SCY) | — | ½ d |
| T1 | Frame feed + camera + overlay mask | `agent/track.py`, `agent/skills.py` (pass `on_frame`), tests | T0 | ½ d |
| T2 | Self contingency (3 modes), lock/unlock, probe reflex | `agent/track.py`, tests | T1 | 1 d |
| T3 | Tracks, two-stage association, lifecycle; Memory writes only confirmed tracks | `agent/track.py`, `agent/memory.py`, tests | T1 | 1 d |
| T4 | Mechanical map writer (crossing, bump, seen, doors, frontier) + facts | `agent/track.py`, `agent/world.py`, tests | T2 | 1 d |
| T5 | Loop integration, stuck reflexes, act-prompt ids and "you", delete superseded code | `agent/loop.py`, `agent/world.py`, `agent/act.py`, goldens | T2–T4 | 1 d |
| T6 | Offline gates (§5), then the live gate | `runs/m1_long` resume | T5 | ½ d + run |

**Run order:** T0 → T1 → (T2 ‖ T3) → T4 → T5 → T6. Total ≈ 5 days of work packages; 0 requests until T6 (≤ 110).

## 7. Contracts
This changes contracts.md#world (self/anchor/track fields, deleted `fix_player`/anchors), #trace (new fields) and #memory (entity rows keyed by track id). **The user applies contract text.** T0 starts only after the user approves the contract delta, which will be proposed with the packet.

## 8. Deferred, with triggers

| Deferred | Add when |
|---|---|
| Tile-hash place fingerprints (exact revisit recognition) | place identity errors show up after T5 |
| Appearance memory for non-self objects (crop signatures, DINOv2) | identical-label objects swap ids in the identity check |
| OpenCV / CSRT / TrackerNano | the 1080p game (STS2); NCC failure measured |
| Pathfinder-to-frontier as a model tool | after T5, if the model ignores frontier facts |
| A critic model for stuck states | never by default (costs requests); only if information-only reflexes demonstrably fail |

## 9. Risks
- **NPC moving in step with the player:** mode-1 residual has two boxes. Lock only when one box holds ≥ 80% of the residual; otherwise stay unlocked and probe again.
- **Camera-follow assumption broken at map edges or cutscenes:** mode 2 covers a fixed camera. Cutscenes run under a `dialogue` screen, which freezes writes.
- **Per-frame cost:** scroll at ±2 px is ~3 ms/frame, about 25 frames per move, ≈ 75 ms per move. Acceptable for a turn-based loop.
- **Probe side effects:** probes only on world screens with no overlay, at most 4 presses, undo when known free. Every probe is logged in the trace.
- **Scope creep:** world.py is 600+ lines already. T5 must delete at least what it replaces (listed in 4.7). Line counts are reported, not gated.
