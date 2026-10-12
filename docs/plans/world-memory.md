# World Memory: know where things are without asking the model

Status: **APPROVED 2026-10-12** (recommended order). Execution: [program-r1-wm.md](program-r1-wm.md), packets W0–W4. Where a packet and this doc differ, the packet wins for gates and budgets. · Branch: feat/lean-agent
Builds on: [spatial-memory.md](spatial-memory.md) (E3R4, built: tile map, exits, BFS inside a place) and [autonomy-redesign.md](autonomy-redesign.md) (R1: notebook and act call). Frozen signatures stay in [contracts.md](contracts.md). This doc only proposes contract changes (§5); the user applies them.

## 0. Summary

World Memory is one module, `agent/world.py`. It keeps code-maintained, evidence-based memory of **where the agent is, where things are, what kind of thing each one is, and how places connect**. It does this with **no model calls**, no game-state access, and no new behaviour gates.

It adds five things on top of today's stores:
1. **Anchors.** Each tracked thing is classified as *screen* (HUD/UI), *world* (static object), *actor* (moves by itself: NPC or enemy) or *self*. The classification comes from motion evidence the harness already measures, not from labels.
2. **One owner for places.** Code owns place records. Names the model gives become aliases. Established places can be merged.
3. **Navigation from memory.** `go_to` can target a thing seen earlier or a known place in any visited place, using routing across places (exit graph plus BFS inside each place).
4. **Evidence states.** Each thing is *seen*, *remembered*, *stale* or *gone*. Things that are missing from a spot that is in view become stale. Visible tiles are marked *seen* (the optional extra from the 2026-10-12 discussion, now included).
5. **A known-things view.** A capped block in the act prompt with a small ASCII map of the current place, the things here, other places and their exits, and HUD changes. It replaces `describe()` and `notebook.places`.

It works on a ladder of **capability levels**: topological memory (places and exits) for any game, a metric 2D map when camera motion can be measured, and a screen frame for UI. Pokémon Red uses all three. 3D games get only the topological level until a real need appears.

## 1. Problem (measured)

| Symptom | Evidence | Root cause |
|---|---|---|
| The model can only `go_to` what is on screen right now | `agent/loop.py:81` resolves `go_to` against `per["entities"]` only | Remembered tiles exist in `entities.tile` but nothing reads them for navigation |
| It can't travel to a known place more than one exit away | `space.route()` (`agent/space.py:34`) is a BFS inside a single place | The exit graph (`tiles.exit.to/arrive`) is stored but never chained |
| Places are recorded twice | `notebook.places` (model-written, `agent/notebook.py:3`) next to `places`, `tiles` and edges (code) | Two writers with no reconciliation; the place-split bug comes back in a new form |
| Places still split | Resume 74→94: 2F under a second id `p_world_2` (handoff notes 2026-10-12) | `name_place` merges only new arrivals (the `ponytail:` note in `agent/memory.py`); merging two established places needs re-keying |
| NPCs leave permanent walls | `fold()` records any non-moving press as `blocked` (`agent/space.py:17`), whatever caused it | No idea of *what* blocked the press: wall or a passing actor |
| Things never disappear | Nothing ever marks an entity absent; picked-up items and moved NPCs stay "known" | No negative evidence: memory only ever adds |
| The model sees almost no map | `describe()` (`agent/space.py:60`) gives one line: current place, its exits, the number of untried edges | No compact rendering of map or memory for the act call |
| Invented things bloat memory | 31 new entities in 20 steps, 18 `#`-suffixed, mostly invented `exit: path south` | Every perceived name is kept forever at full weight; nothing distinguishes things of interest from noise |

## 2. Goals, non-goals and design rules

**Goals**
- Answer with no model call: where am I; where is X; how do I get to X or to place P; what is static, what moves, and what is UI; what have I seen but not visited.
- One act call per `go_to`, however far the destination is.
- Generic: nothing Pokémon-specific in `agent/world.py`.

**Non-goals (for now):** metric 3D, probabilistic beliefs (particle filters, covariances), an MCP server or external API, forgetting or compaction, recognising objects from raw pixels.

**Design rules**
1. **No new gates** (memory: agent autonomy over gating). World Memory stores facts, answers queries and executes the model's own `go_to`. It never picks goals, filters options or overrides the model.
2. **Evidence, not labels.** The perceiver's `kind` is a label; the anchor is decided by motion. When they disagree, the anchor wins for memory, and the disagreement is logged.
3. **Unknown is a valid answer.** Nothing is classified or marked until the evidence threshold is met.
4. **No model calls and no RAM.** RAM truth (`scripts/truth_pokered.py`) scores memory offline only.
5. **Calibration knobs, not magic numbers.** Every threshold is a named constant in `agent/world.py` (§3.12).

## 3. Design

### 3.1 Capability levels

| Level | Requires | Gives | Pokémon | STS2-like | 3D |
|---|---|---|---|---|---|
| **Topological** | Scene cuts (`observer.cut`) and perceived exits | Places, exits, things per place, routing across places | yes | yes (screens are places) | yes |
| **Metric 2D** | Measurable camera shift (`observer.scroll`) and a tile size (`tile_px`), or a fixed camera | Tile map, tile positions, BFS, frontier, seen tiles | yes | n/a | no |
| **Screen** | Any frames | HUD and UI regions, region-change flags | dialogue/menu boxes, battle UI | yes (main use) | yes |

The level is detected per place: if `tile_px` is None, or scroll stays unmeasurable on more than half of the moving presses, the place is topological only. Today's behaviour without `tile_px` is unchanged.

### 3.2 Anchors: screen, world, actor, self

Classification compares two perceptions of the same entity id in the same place, using the camera shift measured between them (the sum of `skill.scroll` per press, in pixels):

- `s1`, `s2` = screen positions in pixels (from perceiver `at` × screen size). `Δcam` = accumulated content scroll between the two frames.
- **World displacement** `d_w = (s2 − s1) − Δcam`, in tiles = `d_w / tile_px`. (Content scrolling left by 16 px means the camera moved right by one tile.)

| Evidence over ≥ `ANCHOR_EVIDENCE` (2) qualifying pairs | Anchor |
|---|---|
| `\|d_w\|` < `POS_TOL` (1 tile) while `\|Δcam\|` ≥ 1 tile | **world** |
| `s2 ≈ s1` (within `POS_TOL`) while `\|Δcam\|` ≥ 1 tile, and not *self* | **screen** |
| `\|d_w\|` ≥ 1 tile not explained by `Δcam`, and not *self* | **actor** |
| Responds to directional input (see below) | **self** |
| Anything else, or not enough pairs | **unknown** |

**Self vs HUD.** In a camera-follow game the player is fixed on screen while the world moves, which is exactly how a HUD behaves. The tie-breaker is **controllability**: after a directional press, the player's pixels change even when the move is blocked (facing or walk frame), and a HUD's don't. The check is `observer.changed()` on a one-tile crop at the entity's position, before vs after the press. The perceiver's `kind: player` acts as a prior, but self needs the pixel check at least once. Today's `_anchor()` median for the player's screen spot stays.

**Screen-anchored without camera motion** (rooms that never scroll, battle screens): a thing whose screen position is unchanged across a **scene cut**, and that appears in ≥ 2 places, is **screen**. Dialogue boxes and menus are a transient kind of screen thing: they disappear when the modal closes. They are stored as `screen` with `transient: true` and are never shown as HUD.

**Animated tiles** (water, flowers) change pixels but don't move. Only a displacement of the entity's position ≥ 1 tile counts as actor.

**Before W4 is built:** W2/W3 use the perceiver kind as a stand-in. Kinds `person`, `npc`, `character`, `enemy`, `monster` and `animal` count as actor, `player` counts as self, everything else counts as world.

**What anchors are used for:**
- **Actors never become walls.** A blocked press whose target tile holds an actor seen now records `blocked_by: actor` with no wall (fix for `space.py:17`). Routing treats actor tiles as blocked only while the actor is seen.
- **Actors' positions decay.** A remembered actor becomes stale after `ACTOR_STALE_STEPS` (20) steps without being seen.
- **Screen things are never placed on the tile map.** They are stored per screen region, not per tile.
- **HUD regions get a cheap change flag.** `changed()` on each HUD region's crop each step lets the view say "HP bar changed at step N", so the model knows to read it again.

### 3.3 What is tracked (things of interest, not everything)

Only things the perceiver names. The code never creates objects from pixels. Retention tiers:

| Tier | What | Kept |
|---|---|---|
| **Map skeleton** | Places, exits (`to`, `arrive`), tiles walked or bumped, tiles seen | Always (room ≈ 64 tiles, town ≈ 360) |
| **Interest** | Has an affordance; named in on-screen text or a goal; is an exit; blocked a move; was interacted with; any actor | Always, with evidence state (§3.5) |
| **Landmark** | Static world things not of interest | Up to `LANDMARKS_PER_PLACE` (8) per place, the most often seen first; used for place recognition |
| **Transient** | Actor positions (last-seen only, no trail), dialogue boxes, menus | Overwritten as they change |
| **Ignored** | Decoration beyond the landmark cap, animation, textures, terrain *appearance* | Never stored |

Terrain *types* (grass, ledge, water) are stored only when an effect teaches them, through existing `relations` (for example "walking on (4,7) started a battle").

Invented exits are dropped before they become interest: a perceived `exit:` whose tile is a known non-exit floor tile, and which was tested and failed, stays at landmark tier and is never shown.

### 3.4 Records (changes to existing stores; no new storage engine)

- **`entities`** gain `anchor` (`screen|world|actor|self|unknown`), `status` (`seen|remembered|stale|gone`), `misses` (int), `tier` (`interest|landmark|transient`), and `region` (`[x0, y0, x1, y1]` in screen fractions, for screen things only). Existing `tile`, `place`, `first/last/seen`, `src` stay.
- **`places`** gain `level` (`topological|metric`) and `offset` (used only on rows merged into another place: tile offset into the target's frame). `aliases`, `state: merged` and `into` exist already.
- **`tiles`**: unchanged shape. A `blocked_by: actor` press is not written as `blocked`.
- **`seen`** (new store, one row per place): `{"id": place, "cells": ["x,y", ...], "step"}`. Upserted only when new cells appear. At most a few hundred cells per place.
- **Harness state** (`Loop.state()`): gains `world` = `{"cam_px": [x, y], "pairs": {...anchor evidence...}}`, so a checkpoint resume keeps anchor evidence.

### 3.5 Evidence states and negative evidence

```
seen ──(not seen this step)──▶ remembered ──(misses ≥ MISS_TO_STALE)──▶ stale ──(misses ≥ MISS_TO_GONE)──▶ gone
  ▲                                                  │                                            │
  └────────────────────────── seen again at or near its tile (resets misses) ◀────────────────────┘
```

- **A miss** = a world or actor entity in this place whose tile lies inside the **current view rectangle**, shrunk by a 1-tile margin, on a `screen: world` frame with no modal open, and which the perception did not match.
- **Misses count only on distinct frames** (different frame hash). Cached perceptions never add misses.
- **Thresholds:** `MISS_TO_STALE` = 2, `MISS_TO_GONE` = 4. The perceiver's measured recall is 0.71 (E1), so one miss is not evidence. With 0.71 recall, two independent misses on something actually present happen about 8% of the time and four about 0.7%.
- **Actors** go stale on a timer instead (§3.2); their misses only mean "not here now".
- **Stale things** are still routable, but the view says "last seen at (x,y), step N, may have moved". **Gone things** are hidden from the view and kept in storage.

The **view rectangle** is computed once per step, from the player tile, the player's screen spot and the screen size in tiles. Both negative evidence and seen tiles use it.

### 3.6 Map detail and seen tiles

Per metric place, each tile is one of:

| State | Source | Render |
|---|---|---|
| floor (walked) | a press moved onto it (`fold`) | `.` |
| seen, not walked | inside the view rectangle at least once | `,` |
| blocked side | a confident `(0, 0)` press with no actor there | `#` on the far side (rendered per tile, ignoring direction) |
| exit | a scene cut when leaving it | `E`, listed with destination |
| thing | an interest entity's tile | a letter, listed in a legend |
| you | the player tile | `@` |
| unknown | nothing | space |

**Seen tiles** let exploration tell "looked at and unwalkable-looking" from "never looked at". The frontier (`space.frontier`) is unchanged; the view reports unseen frontier edges separately ("3 edges into unseen area"). Seen tiles are never used to mark floor; only walking does.

**Render limits:** crop to `MAP_W × MAP_H` (21 × 15 tiles) around the player, at most about 330 characters. Example for Red's house 1F:

```
Here: house 1F (3,6)  map: 28 walked, 41 seen, 4 untried edges (3 into unseen)
,,,#,,,S      S stairs (7,1) -> house 2F
,T,.,,,.      T TV (1,1)         remembered, step 40
,..M...,      M Mom (3,2)        actor, seen now
,..@...,      @ you
,,,E,,,,      E door mat (3,7) -> Pallet Town
```

### 3.7 Update pipeline (per loop step, no model calls)

`world.update(...)` is called once after the "before" perception, and once after the skill result and the "after" perception, replacing the inline spatial code in `loop.step`:

1. **Camera:** add `skill.scroll` to `cam_px`. If the scroll is None (unsure), record nothing for anchors this step.
2. **Cut and place:** `observer.cut` decides a cut, then `mem.match_place` (unchanged order: known exit, then edges, then landmarks, then new) and `mem.name_place` with the model's `place`.
3. **Fold moves:** `space.fold` with the actor exception (§3.2), then `mark_exit` on a cut.
4. **Matching:** `mem.observe` assigns entity ids (unchanged rule: place, base name, tile within 1). Actors use a wider radius, `1 + steps_since_seen // 4` tiles, capped at 4.
5. **Anchor evidence:** for each matched entity with a previous screen position and `|Δcam|` ≥ 1 tile, add one evidence pair. Classify once there are `ANCHOR_EVIDENCE` agreeing pairs.
6. **Negative evidence and seen tiles:** compute the view rectangle, add misses (§3.5), union the cells into `seen`.
7. **Tiers:** promote an entity to interest when it meets any rule in §3.3. Enforce the landmark cap.
8. **HUD flags:** `changed()` per screen region against its last crop; store the step when it changed.

### 3.8 Place identity: one owner, repairable

- **Code owns `places`.** The act reply's `place` becomes an alias through `name_place` (exists). The `places` section is removed from the notebook. The act prompt lists known place names (top 12 by recency) so the model reuses them.
- **Merging established places** (new; today only new arrivals merge): when the model names place B with an alias of established place A, merge B into A if a frame offset can be found. The offset is the median tile delta between landmark or interest entities with the same base name present in both places, with at least 2 agreeing pairs. Then:
  - B's tiles are re-keyed into A's frame by the offset;
  - entities get `place = A` and shifted tiles;
  - edges, tile exits and relations pointing at B are redirected (as `name_place` already does);
  - B gets `state: merged, into: A, offset`.

  If no offset can be found, the alias is recorded, the merge is skipped, a `merge_pending` flag goes to the model ("two records for 'bedroom'"), and the trace logs it.
- **Splitting** (one id covering two real places) is **not built**. It is detected offline by the gate metric "truth maps per place id" (§7), and built only if that metric fails.

### 3.9 Navigation: `go_to` through memory

`world.resolve(name)` resolves in this order:
1. An on-screen entity by name (today's behaviour).
2. A remembered interest entity by name (base-name match, `_distance ≤ 1`), with status seen, remembered or stale. Stale results come back with a caveat.
3. A place by name or alias. The target is its known arrival tile.
4. Otherwise `None`. The act call gets the flag `go_to "<name>": not in memory`.

**Route:** BFS over the **place graph**, where nodes are non-merged places and edges are tile exits with a known `to`, weighted by hop count. Each leg is (walk to the exit tile with `space.route`, then push the exit direction). The last leg walks to the target tile (or next to it, for an entity) and faces it.

**Execution:** `world.travel(ctx, route)` runs all legs **inside one loop step** with the existing `approach` skill. After each leg it checks that the scene cut landed in the expected place (`exit_to` agrees) and that the arrival tile matches. It stops early on the first surprise (blocked with no alternate path, wrong place, a modal opening such as an NPC stopping you, or the input cap) and returns a `SkillResult` whose reason names the failed leg. That reason is shown to the model as a fact. There's no perception and no act call while legs succeed, so one act call covers the whole trip. Tiles are still folded from the moves.

This executes the model's own choice; it is not a gate. The model can still choose `buttons` or an affordance instead.

### 3.10 What the model sees

`world.view(goal_text)` replaces `describe()` in the act call, and becomes the place list for the act prompt. It is capped at `VIEW_TOKENS` (≈ 500 tokens, about 2,000 characters), filled in priority order:
1. The **Here** line and the cropped map (§3.6).
2. **Things here** (interest tier): name, tile, anchor, status, last-seen step.
3. **Screen:** HUD regions with their last-changed step; current modal, if any.
4. **Other places,** ranked by graph distance, then by overlap with the goal text, then by recency. For each: name, aliases, exits (`→ destination`), interest things (name and status), counts of untried and unseen edges.
5. **Flags:** `merge_pending`, the last `travel` failure, `go_to` misses.

Gone things and landmarks are not shown (landmarks only appear as map letters when they fit). The view stays deterministic for the same memory, which helps caching.

### 3.11 Module boundary

```
loop.py ──▶ world.py ──▶ space.py   (grid primitives: fold, route, frontier, outward, tile_of; imported only by world.py)
               │
               └──────▶ memory.py   (storage + identity: match_place, name_place, observe, resolve; unchanged role)
```

- `loop.py` imports only `world` for spatial work: `World(mem, tile_px)`, `world.update(...)`, `world.view(goal)`, `world.resolve(name)`, `world.travel(ctx, route)`, `world.state()`, `world.restore(s)`.
- `memory.py` stops importing `agent.space` (the lazy `exit_to` import in `match_place` becomes an inlined `tiles` lookup), which removes the two-way dependency.
- `world.py` is the only writer of the `tiles`, `seen` and anchor/status fields. `memory.py` keeps writing identity fields.
- If World Memory is disabled, today's behaviour is unchanged (§6, W0 is a pure extraction).

### 3.12 Constants (calibration knobs, all in `agent/world.py`)

`POS_TOL = 1` tile · `ANCHOR_EVIDENCE = 2` pairs · `MISS_TO_STALE = 2` · `MISS_TO_GONE = 4` · `ACTOR_STALE_STEPS = 20` · `LANDMARKS_PER_PLACE = 8` · `MAP_W, MAP_H = 21, 15` · `VIEW_TOKENS = 500` · `TRAVEL_MAX_INPUTS = 120`.

## 4. What this replaces or deletes

| Current | Becomes |
|---|---|
| Inline spatial code in `loop.step` (projection lambda, `fold`/`mark_exit`/`exit_to` calls, `pos` resets) | `world.update` (W0) |
| `go_to` on-screen lookup (`loop.py:79-86`) | `world.resolve` + `world.travel` (W2) |
| `describe()` (`space.py:60`) | `world.view` (W3); `describe` deleted once the gated path is deleted (autonomy P4) |
| `notebook` section `places`, `Notebook.places()` | removed; known place names come from `world.view` (W1) |
| `ponytail:` "merge only new arrivals" in `name_place` | merging established places with an offset (W1) |
| `memory.py` → `space.exit_to` import | inlined lookup (W0) |

## 5. Contract changes needed (the user applies these to contracts.md)

1. **memory:** entity fields `anchor`, `status`, `misses`, `tier`, `region`; place fields `level`, `offset`; new store `seen`; merging established places (§3.8).
2. **space:** `fold` takes an optional `actors: set[tuple[int, int]]`; presses into an actor tile are not recorded as `blocked`.
3. **world (new section):** the interface in §3.11 and the view format in §3.10.
4. **notebook/act:** sections become `goals, hints, lessons`; `go_to` accepts a remembered entity name or a place name; the reply gets `travel` failure reasons as flags.
5. **run directory:** `harness.json` gains `world`.

## 6. Work packages

Tiers (user, 2026-10-12): GPT-6.1 Sol orchestrator; every WP on `gpt-6-luna` at high effort; set explicitly. The WPs run sequentially because W0–W4 share `agent/world.py` (disjoint ownership would need artificial file splits).

| WP | Goal | Owns files (line budget) | Depends on | Tier |
|---|---|---|---|---|
| W0 | Extract the spatial code from `loop.step` into `world.update`/`state`/`restore`, with **no behaviour change**; remove the `memory → space` import | `agent/world.py` (new, ≤ 70), `agent/loop.py` (net ≤ −40), `agent/memory.py` (±5) | — | gpt-6-luna high |
| W1 | One owner for places: drop `notebook.places`, list place names in the act prompt, merge established places with an offset, `merge_pending` flag | `agent/memory.py` (≤ +30), `agent/notebook.py` (−2), `agent/act.py` (±6), `agent/world.py` (≤ +10) | W0 | gpt-6-luna high |
| W2 | Navigation: `resolve`, place-graph route, `travel` as one multi-leg step, actor-aware `fold` (perceiver kind as stand-in) | `agent/world.py` (≤ +60), `agent/space.py` (≤ +4), `agent/loop.py` (go_to branch, net ≤ 0) | W1 | gpt-6-luna high |
| W3 | Evidence states, view rectangle, `seen` store, tiers, `world.view` replacing `describe` in the act call | `agent/world.py` (≤ +80), `agent/memory.py` (observe radius/status, ≤ +10), `agent/loop.py` (≤ +2) | W2 | gpt-6-luna high |
| W4 | Anchors from motion plus the controllability check; HUD regions and change flags; replaces the kind stand-in | `agent/world.py` (≤ +60), `tests/agent/fakes.py` (GridEnv: optional HUD overlay, wandering NPC, animated tile, ≤ +30), `scripts/check_world.py` (offline anchor replay over the P3 run) | P4 | gpt-6-luna high |
| W5 *(later, separate approval)* | Topological-only mode verified on a synthetic non-grid env (`tile_px=None`, screens as places) | `tests/agent/` only, if the code already holds | W4 | gpt-6-luna high |

Every WP adds its tests to **`tests/agent/test_world.py`** (new) and keeps `tests/agent` green.

### W0: extraction
- **Read:** `agent/loop.py` `step` (place matching, projection, fold, exit marking, pos resets), `agent/space.py`, `agent/memory.py` `match_place`.
- **Acceptance:** `pytest tests/agent -q` green with no test edits except imports. On a replay of `runs/m1`, the `tiles`, `places` and `entities` JSONL written with World Memory are byte-identical to those written without it.
- **Risk:** `loop.step` is dense, and the act path and gated path share it. Mitigation: move only the lines named above; leave the gated branch's candidate logic in `loop.py`.

### W1: place ownership
- **Acceptance:** unit tests for (a) merging an established place with 2 shared landmarks re-keys tiles and entities by the offset; (b) with no shared landmarks it records the alias, doesn't merge, and sets the flag; (c) the notebook rejects `section: places`. On replay, `p_world_2` merges into the first 2F id (§7).
- **Risk:** a wrong merge corrupts two maps. Mitigation: require 2 agreeing landmark pairs; keep merge history in the JSONL (append-only) so it can be rolled back by truncation.

### W2: navigation
- **Acceptance:** a GridEnv test with 3 rooms: `go_to` a place two exits away completes in **one** loop step with **0** model calls (StubModel raises if called during `travel`); `go_to` a remembered off-screen entity ends adjacent to and facing it; a blocked leg returns a reason naming the leg. A test where an NPC blocks a corridor records no permanent `blocked`.
- **Risk:** a stale exit graph (wrong `arrive`). Mitigation: verify each leg's arrival; on mismatch, update `arrive` from the observed tile and stop.

### W3: evidence, seen tiles, view
- **Acceptance:** GridEnv tests: an item removed becomes stale after 2 distinct frames and gone after 4; a cached frame adds no misses; seen cells equal the view rectangles visited; `world.view` stays within the budget on the 360-tile town fixture; the act prompt contains no `describe()` line.
- **Risk:** perceiver recall 0.71 → false stale. Mitigation: distinct-frame rule, a 1-tile margin, and stale ≠ gone.

### W4: anchors and HUD
- **Acceptance:** GridEnv with HUD overlay and wandering NPC: the HUD is classified screen, the NPC actor, the player self, walls and objects world. Plus the RAM gate in §7.
- **Risk:** an NPC walking in step with the camera looks like world. Mitigation: 2 evidence pairs, plus the actor timer. A fixed-camera room has no `Δcam`. Mitigation: cut persistence rule; otherwise unknown.

## 7. Gates (all offline: 0 cloud calls; cached perception, StubModel, replays, PyBoy with recorded memory)

The packets refine these: W0 uses golden GridEnv episodes; W1 checks merges on the checkpoint-94 memory; W2 travels from checkpoint 94 (2F → 1F → Town); W3's duplicate and seen-accuracy numbers are measured live in P3; W4 replays the P3 run against truth sprites.

| Metric | Pass | Measured by |
|---|---|---|
| W0: behaviour unchanged | replayed stores byte-identical; `tests/agent` green | replay diff script (created in W0) |
| W1: place ids per truth map, `runs/m1` replay | every truth map in the trace (0x26, 0x25, 0x00) → exactly 1 non-merged place id; every place id → 1 truth map | `scripts/eval_run.py` new field `place_ids_per_map` |
| W2: return trip through memory | PyBoy from a Pallet Town state with a memory that knows 2F/1F/Town: `go_to "<2F alias>"` reaches truth map 0x26 in ≤ 60 inputs, 1 loop step, 0 model calls | `scripts/check_world.py --goal travel` (created in W2) |
| W2: remembered thing | from 2F, `go_to "TV"` (seen earlier in 1F) ends adjacent in 1F | same script, `--goal fetch` |
| W2: no false walls | 0 `blocked` rows recorded where truth had an NPC sprite on the destination tile | `eval_run` new field over a replay |
| W3: duplicates | 0 duplicate interest entities per (place, object) on the `runs/m1` replay | `eval_run` (existing duplicate metric, restricted to interest tier) |
| W3: seen accuracy | ≥ 98% of seen cells lie inside the truth map bounds | `eval_run` new field |
| W4: anchor accuracy vs RAM sprites | of entities with a decided anchor, ≥ 90% match truth (slot 0 = self; a sprite whose map position changed = actor; else world); undecided ≤ 30% | `eval_run` new field; `truth_pokered` sprite map coords |
| W4: synthetic HUD | 100% on the GridEnv HUD/NPC fixture | `pytest tests/agent/test_world.py -q` |
| No regression | tile accuracy vs RAM ≥ 95% (today 20/20); known-exit reuse 100% | `eval_run` existing fields |
| Cost | 0 additional model calls per step; act calls per `go_to` = 1 | trace counts |

**If a gate fails:** stop and report. For W4, the fallback is to keep the perceiver-kind stand-in, which is still better than today.

## 8. Budgets

- **Lines:** `agent/world.py` ≈ 280 at the end of W4; `loop.py` ≈ −40; memory/notebook/act/space ≈ +45. **Net ≈ +285** on `agent/` (1,988 today, already over the 1,500 headline). Recommended offset: autonomy P4 deletions (gated path in `loop.py`, `decide.py`, `tasks.py`, `judge.py`, `escalate`, `describe`), expected to remove more than this.
- **Cloud calls:** 0 for every gate. Optional after W3: a live check of 20–30 requests with `--act`, from the remaining M1 budget (483 left), needing separate approval.
- **Runtime:** `update` is O(entities + tiles in view) per step. `changed()` crops are a few hundred pixels each. Travel BFS runs over ≤ 20 places × ≤ 400 tiles.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Perceiver position noise (`at` on a 0–1000 grid) makes anchors flicker | Snap to tiles; `POS_TOL` = 1 tile; 2 agreeing pairs; unknown is allowed |
| Perceiver recall 0.71 → false misses | Distinct frames only; 2/4 thresholds; stale is reversible |
| Wrong merge corrupts maps | 2 landmark pairs required; append-only history; split detection metric |
| `travel` hides useful observations (no perception during legs) | Stops on any surprise; tiles still folded; the next step perceives the arrival normally |
| Overlap with autonomy P2–P4 confuses comparisons | Order in §11, decision 1 |
| Line budget | Paired with P4 deletions |
| Over-generalising | Capability ladder; 3D and MCP are explicit non-goals; W5 only on approval |

## 10. Out of scope / later

- **Skipping perception on fully known views** (spatial-memory §3.7): seen tiles + anchors make it safer (actors are known to move), but it needs its own gate.
- **Splitting places**, built only if the W1 metric shows mixed ids.
- **3D metric memory** (visual odometry), an MCP server, forgetting or compaction.
- **A second game** (STS2) beyond the W5 synthetic check.

## 11. Decisions for you

Decided 2026-10-12 (user: "go with your recommended order"):
1. Order: P2 → W0–W3 → P3 (live, act + World Memory) → P4 → W4.
2. W4 last; the perceiver-kind stand-in covers NPC walls from W2.
3. The live check is P3 itself (≤ 80 requests), after STOP 2.
4. Line budget accepted, offset by P4.

## 12. Result (filled in after each WP)

- **Gate:**
- **Deviations:**
- **Contract changes applied:**
- **Open issues:**
