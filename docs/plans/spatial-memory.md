# Spatial memory: where am I, where is everything, and which ways out have I tried?

Status: **approved 2026-10-11** as E3R4, which runs before M1 resumes. The budget is accepted, and the run resumes from checkpoint 54. · Branch: feat/lean-agent · The packet is [E3.md §R4](E3.md); frozen signatures are in [contracts.md#space](contracts.md#space). Where this doc and the contract differ, the contract wins: it tracks the player tile `pos` directly instead of a camera offset, and §3.4 rule 4 (tile landmarks) is deferred.

## 1. Problem (measured on checkpoint 54/55, 2026-10-11)

Memory does not hold spatial facts, so the harness either asks the cloud model again or guesses.

| Symptom | Evidence | Root cause |
|---|---|---|
| A cloud `refine` call on every exit test, only to re-point a target | removed 2026-10-11 (contracts.md#skills); 1 call per test | Memory `at` values are screen fractions from one frame, so the perceiver's rough point was the only position the harness had. |
| Remembered positions go stale after any move | `offline.py` has to hand-shift cached `at` by the measured scroll; the rug was projected to [0.30, 0.49] and the walk overshot to x=0 | No world frame: `at` is valid only for the frame it came from. |
| Duplicate entities | 90 entities, 70 distinct (place, name), 20 duplicates (`shelf#3#9`, `trash can#4#10`) | Identity is (name, place) with an edit-distance tie-break; position plays no part. |
| Place splits | Mom's dialogue created `p_dialogue_box_2` (1F split from `p_game_world`); walking back upstairs at step 55 created `p_world` (2F split from `p_dialogue_screen_4`) | Place identity is the screen label plus a landmark-overlap score. Both drift with the perceiver version and with overlays. `places_matched` is false in runs/m1/summary.json. |
| Exit push goes the wrong way | Offline test 3 picked the real doormat (bottom wall) but pushed LEFT, its last walking direction | The skill does not know where the room boundary is. |
| Wasted walking | Offline test 1 used 30 inputs (the cap) and was blocked; test 4 also used 30 | Greedy axis walking with no memory of walls. |

All of this is information the harness already measures for free (scroll per press, tile size, blocked presses, scene cuts) and then throws away.

## 2. Goal

Each place keeps a small **tile map** in place-local coordinates, built only from pixel evidence the skills already measure. It answers, with no model call:
- where the player is (place, tile);
- where each known thing is (tile), across scrolling and revisits;
- which tiles are floor, wall, or untried;
- which tiles are exits, where each leads, and which tile it arrives on;
- which boundary pushes have been tried.

Cloud calls are then spent only on reading new things (perception of unseen views, text, choices), never on localisation.

## 3. Design

### 3.1 Coordinates (dead reckoning, no new sensing)
- **Place frame:** origin = the player's tile on first arrival in a place. Axes are screen axes in tiles.
- **Camera offset** `cam = (cx, cy)` in tiles = −(sum of measured content scroll) / `tile_px`, accumulated from `SkillResult.scroll` (already summed frame by frame in `observer.scroll`).
- **Player tile** = `cam + player_screen_tile`, where `player_screen_tile = round((player_at − 0.5) × screen / tile_px)` from the perceiver's player entity. In Pokémon the player stays centred, so this is just `cam`.
- **Thing tile** = `cam + round((at − 0.5) × screen / tile_px)` at the moment it is perceived, so it never goes stale.
- `cam` is reset to the arrival tile on every place change (§3.4), which bounds drift to one visit.
- **Drift check (free):** each new perception gives screen positions of things already on the map. The median disagreement between the remembered and the projected tile is the drift. If it is not zero for two perceptions in a row, re-anchor `cam` by that median and log `drift_fix` in the trace.

### 3.2 What movement teaches (from presses the skills already make)
`approach` already knows, per press, the direction and the measured move. It exposes them as `SkillResult.moves: list[(dir, (dx, dy) | None)]` (contract change §5). The loop folds them into the map:
- a press that moved one tile → the destination tile is `floor`;
- a confident `(0, 0)` → the tile in that direction is `wall` from this side (stored as a blocked direction on the current tile, because some games have one-way ledges);
- a press that caused a scene cut → the current tile plus direction is an **exit**;
- `None` (unsure) → nothing is recorded.

### 3.3 Storage (no Memory code change: "any store name is accepted")
- New store `tiles`, one row per tile touched: `{"id": "<place>@x,y", "place", "x", "y", "floor": bool|null, "blocked": ["UP", ...], "exit": {"dir", "to", "arrive": [x, y]} | null, "pushed": ["DOWN", ...], "step"}`. Only changed tiles are appended. A Pokémon room is about 64 tiles and a town about 360.
- `entities` gain `"tile": [x, y]` in the place frame. `at` stays for compatibility.
- **Identity becomes (place, base name, tile within 1).** The `#n` suffixes stop appearing for the same object seen from a scrolled view, and a second shelf two tiles away is still a second shelf.
- Harness state gains `cam` (checkpointed with the rest of `Loop.state()`).

### 3.4 Exits, arrivals and place identity
- On a scene cut from place A at tile T going `dir`, the arrival frame anchors place B. B's frame is new if B is new; if B is known, `cam` is set so the player stands on the stored arrival tile.
- The edge is stored on **both** tiles: `A@T.exit = {dir, to: B, arrive: T'}`, and `B@T'` gets the reverse with `to: A`. That replaces the name-matching `arrival_entrance` heuristic added on 2026-10-11 with a fact.
- **`match_place` order (contract change):**
  1. no cut → same place;
  2. cut through a known exit tile → its `to`;
  3. modal screens (dialogue, menu, keyboard) → same place (already true since E3R3);
  4. landmark overlap ≥ 0.6, as today. Comparing landmarks by (name, tile) instead of (name, kind), with no screen-label requirement, is **deferred**: frames from different entrances need aligning first;
  5. otherwise a new candidate place.

  Rule 2 alone would have prevented both measured splits.

### 3.5 Navigation and exit search (pure code)
- **Path:** BFS over the place's tiles, treating `floor` and unknown tiles as passable and `blocked` directions as walls. It is replanned after each blocked press. This replaces greedy axis walking inside `approach` when a map exists; with no map, today's greedy walk is unchanged. About 15 lines of stdlib.
- **Exit candidates for `new_place` goals,** in this order:
  1. perceived `exit:` things not yet tested;
  2. **frontier pushes:** known floor tiles at the map boundary (extreme row or column, or next to `unknown`) whose outward direction is not yet in `pushed`;
  3. other things at wall placements.

  Each test walks to the tile and pushes **outward**, meaning away from the map interior, not in the last walking direction. That is the doormat fix: the bottom-row tile is pushed DOWN.
- Known exits leading to visited places are skipped, and so are tiles already pushed in that direction. The decider ranker becomes an optional tie-break and is not needed for correctness. On the measured state it was nearly uniform (0.19 vs 0.16).

### 3.6 What the models see
- The Chooser and planner views get one compact line per place: `Here: house 1F (3,5). Exits: stairs (7,1) → 2F. Untried edges: bottom row x=2..3.`
- The planner can also get a small ASCII map (64–360 characters).
- The perceiver view gets nothing new.

### 3.7 Later, optional (separate approval): skip perception on known views
If every on-screen tile is mapped and no text, modal or entity motion is expected, reuse the remembered entities instead of calling the perceiver. Perception is the dominant cost (about 1.5 HTTP calls per step on the retained M1 run), so this is the largest saving, but it needs its own gate because NPCs move.

## 4. What it replaces or deletes
- `arrival_entrance` (decide.py, 2026-10-11) → replaced by stored reverse exits (§3.4).
- The `refine` call in exit mode is already deleted. The `refine` import stays for pointer environments only.
- Landmark matching by screen label → replaced by (name, tile) matching (§3.4).
- `.tmp/e3r3/offline.py` coordinate projection → replaced by `cam`.

## 5. Contract changes needed (the user applies these to contracts.md)
1. **skills:** `SkillResult.moves: list[tuple[str, tuple[int, int] | None]]`.
2. **memory:** a `tiles` store shape; `entities.tile`; the identity rule (§3.3); the `match_place` order (§3.4).
3. **run directory:** `harness.json` gains `cam`.
4. **tasks:** `new_place` unchanged; it still latches on a pixel-verified edge.

## 6. Work packages (sketch; line budgets are estimates)

| WP | Goal | Owns | Lines | Depends on |
|---|---|---|---|---|
| S.1 | `moves` in SkillResult; BFS path inside `approach` when a map is given; outward push | `agent/skills.py` | ≤ 35 | — |
| S.2 | `cam`, tile projection, `tiles` folding, entity tile identity, drift re-anchor | `agent/memory.py` (+ a small `agent/space.py` only if `memory.py` would exceed its budget) | ≤ 50 | — |
| S.3 | `match_place` order; exit and reverse-exit on cut; frontier candidates; view line; checkpoint `cam` | `agent/loop.py`, `agent/decide.py` | ≤ 35 | S.1, S.2 |
| S.4 | Delete `arrival_entrance`; migrate checkpoint 54 (empty map; origin = current tile) | `agent/decide.py`, `.tmp/` script | −15 | S.3 |

Net about +105 lines, against `agent/` currently at 1,603 lines, which is already over spec §12's 1,500 headline. That needs your decision on budget, or offsetting deletions; S.4 and §4 recover some.

## 7. Gate (all offline: 0 cloud calls except cached perception)

| Metric | Pass | Measured by |
|---|---|---|
| Tile position vs RAM truth (evaluation only, never used for choices) | ≥ 95% of steps exact after the per-visit offset | new `eval_run` field over a replay |
| Duplicate entities per (place, object) | 0 on a fresh house replay | `eval_run` |
| Revisiting a place reuses its id | 2F → 1F → 2F gives one id per floor | GridEnv unit test plus PyBoy replay |
| Exit search from checkpoint 54 | reaches Pallet Town in ≤ 2 exit tests, 0 localisation calls, ≤ 40 inputs | `offline.py` successor |
| Unit tests | `pytest tests/agent -q` green | — |

## 8. Risks
- **Unsure scrolls (`None`) add up to drift.** Mitigations: they are not recorded, there is the drift re-anchor (§3.1), and `cam` is reset on each arrival.
- **Fixed-camera rooms** (the player sprite moves and the screen does not scroll): the player tile comes from the perceiver's player position, so it costs no extra calls but is noisier. This is out of scope while Pokémon is the only game; it is flagged for STS2/E5.
- **Non-grid games** (`tile_px` None): there is no map, and today's behaviour is unchanged.
- **Perceiver point error:** a thing tile can be off by one. Identity allows ±1, and tests walk to the tile and then check pixels.

## 9. Decisions (user, 2026-10-11)
1. Run as **E3R4, before M1 resumes**.
2. Line budget **accepted** (about +110 in the packet).
3. §3.7 (skipping perception on known views) is **out of scope**, a later and separately gated step.
4. **Resume from checkpoint 54** as merged; the step-55 run is discarded.
