# Avatar harness round r10: identity from pixels, names from evidence

**Summary (6 lines)**
1. **r9's diagnosis:** most "identity churn" is objects, not characters. In the lab (r8 116–146), 15 of 21 sprite-slot id switches are the three Poké Balls and the table cycling between rows named table / ball / sphere / item. Real character switches: 3 in r8, 1 in r7.
2. **A concrete bug causes part of it.** In `Memory.observe`, an entity whose perceived kind is in `RECURRING` (`"menu_item", "move", "item", "option"`) gets **no tile** (`memory.py:248`), even on a world screen. So a ball perceived as kind `item` is matched by *name* only. Every new wording mints a new row: `ball 1/2/3` and `sphere 1/2/3` in r8, `ball` ×3 and `sphere 1/2/3` in r7.
3. **The design rule for r10:** memory feeds perception only *where* things are, never *what* they are. Identity is non-semantic (tile + pixel patch); names are revisable claims ranked by evidence.
   - **B, pixel-anchored objects:** an object's identity is its tile plus its stored 16×16 patch. Perception only labels it.
   - **A, perceive against memory:** remembered objects in view are drawn as numbered marks on the perceiver's image, with no names. The perceiver reports which mark each thing sits on, in its own words.
   - **N, name tiers:** perceiver votes < game-text naming by the planner (cited) < the game naming the thing on interaction. Lower tiers never overwrite higher ones; any tier can be replaced by newer evidence of the same or a higher tier. Unconfident rows show their candidates.
4. **Supporting items:**
   - **M:** a committed identity measure split by sprite kind;
   - **R0:** the `RECURRING` bug;
   - **P:** perceiver and act at temperature 0 with a fixed seed, behind the same micro-check as A.
5. **Asks:** about 40 for the A + P micro-check, then a clean-boot run of at most 400. Ledger 6751 / 7854, so 1103 are left.
6. **Executed by** a new Sonnet 5.5 (medium) session with Haiku 5.5 (medium) workers; see `docs/plans/execute-r10.md`.

| id | title | files | asks | gate |
|---|---|---|---|---|
| M | Identity measure split by sprite kind: `scripts/eval_identity.py` | `scripts/eval_identity.py`, `tests/agent/test_eval_identity.py` | 0 | none |
| R0 | On a world screen, every positioned entity gets a tile, whatever its kind | `agent/memory.py`, `tests/agent/test_memory.py` | 0 | M: object switches do not rise |
| B1 | A new object's tile is the sprite cell near the perceived point, not the raw point | `agent/world.py`, `tests/agent/test_world.py` | 0 | M |
| B2 | An object row keeps its patch; a perception binds to the row whose patch is at a nearby cell | `agent/world.py`, `agent/memory.py`, tests | 0 | M (main gate) |
| B3 | Presence from pixels: a row whose patch is at its tile is present; a changed patch at an in-view tile is evidence it moved or went | `agent/world.py`, tests | 0 | M |
| N1 | Names are tiered claims (`called_src`); a newer claim of the same or a higher tier replaces a lower one | `agent/memory.py`, `agent/loop.py`, tests | 0 | suite |
| N2 | The planner can name a thing from cited game text (`name` op) | `agent/plan.py`, `agent/notebook.py`, `agent/loop.py`, tests | 0 | suite + plan replay |
| N3 | An unconfident row's label shows its candidates | `agent/world.py`, tests | 0 | A3 |
| AP | Micro-check on recorded frames: marks (A) and temperature 0 + seed (P) | scratch only | ≤ 40 | go / no-go for A and P |
| A | Numbered marks for remembered objects in view; entities may carry `mark` | `agent/world.py`, `agent/perceive.py`, `agent/loop.py`, `agent/memory.py`, tests | 0 | AP go |
| P | Perceiver and act at temperature 0 with a fixed seed; options are part of the cache key | `agent/model.py`, `agent/loop.py` (role params), tests | 0 | AP go |
| LV10 | Clean-boot live run, 150 steps | run data | ≤ 400 | all gates above |

---

## Evidence (0 asks; HEAD `9081817`, which equals `ed20630` for `agent/`)

- **Switch classes** (`.tmp/r9/why_switch.py`, no-reload replay; r8 116–146, r7 116–150):

  | class | r8 | r7 |
  |---|---|---|
  | object rows (balls, table, counters, shelves) | 15 | 25 |
  | nearest perception flips between a character and an object | 3 | 1 |
  | a character's track starts under a new row id | 3 | 1 |
  | a character with no track | 0 | 1 |

  RAM sprite pictures in the lab: 1 = player (slot 0), 2 and 3 = characters (slots 1 and 5), 61 = the three balls (slots 2–4), 65 = two more sprites (slots 6 and 7).
- **The `RECURRING` path.** Lab mints on world screens with a `RECURRING` kind: r8 6 of 79 (`ball 1/2/3`, `sphere 1/2/3`); r7 6 of 53 (`ball` ×3, `sphere 1/2/3`); r6 0. There were 12 such perceptions in r8 and 9 in r7. Each mint is a ball, and each has no tile.
- **Object jitter.** r8 minted 42 object rows within 1–4 tiles of an existing row (31 at 1 tile), and static projection jitter has p95 2 against `STATIC_GATE = 1`. Object tiles come from `tile_of(e["at"], anchor, …)` on the perceiver's point (`world.py:103-110`). That point is often 6–16 px off; r9 measured 14–18 px for characters.
- **Snapping works.** r9's IB3b used a "non-repeating patch" rule to move character tracks onto sprites. It raised tracks on a sprite from 57% to 77% on r8 and from 69% to 80% on r7 (commit `f87b621`, since reverted). B1 reuses that rule for objects.
- **Sampling.** All roles run at temperature 0.2 with no seed (`model.py:24-29`). The same image and prompt asked twice gave 92 of 122 entities identical (75%); lab frames 55–86%.
- **The cache key excludes `options`** (`model.py:108-115`). A temperature change would silently reuse replies cached under the old temperature.
- **First name wins.** `Memory.call` sets `called` only when it is empty (`memory.py:128-132`), so the first game name locks.

---

## M: identity measure split by sprite kind (`scripts/eval_identity.py`, 0 asks)

Promote `.tmp/r9/why_switch.py` and the no-reload replay in `.tmp/r9/track_hits.py` into one committed script.

- **Usage:** `scripts/eval_identity.py <run-basename> --from S --to S [--reload]`. The default is no-reload, since reloading every 5 steps clears the tracker.
- **Sprite classes:** a RAM sprite slot is **static** when its RAM x and y never change over the window; otherwise it is a **character**. Slot 0 is the player and is skipped. No picture ids are hard-coded.
- **Per class it prints:**
  - id switches;
  - max records per slot;
  - the switch causes (the four classes above).
- **Also:**
  - object mints within 2 tiles of an existing object row of the same place;
  - mints with no tile on a world screen;
  - the divergence count.
- **Finally:** `requests=0`.

**Test:** a tiny fake replay callback sequence with one static slot and one moving slot, and a known switch in each, reports 1 / 1.

**Baselines at HEAD:**
- r8 116–146 (r8 diverges from 147);
- r7 116–150;
- r6 116–150;
- r3b, over its lab window (find it from truth `map == 40`).

**The main r10 gate (used by R0, B1, B2 and B3):**
- static-sprite switches on r8 and r7 at most half of HEAD's;
- character switches no worse than HEAD;
- no-tile world mints = 0;
- object mints within 2 tiles at most half of HEAD's.

Each part must also not make any of these worse than the part before it, or it is reverted.

---

## R0: positioned entities on a world screen always get a tile

**Root cause.** `memory.py:248`: `tile = tiles.get(i) if tiles and kind not in RECURRING else None`. `RECURRING` exists so that menu lines ("POTION ×2", a move name) keep a name identity across screens. A world object that the perceiver happens to call `item` should not lose its position.

**Change.** Skip the tile only when the perception's `screen` is not `"world"`:
- `tile = tiles.get(i) if tiles and (perception.get("screen") == "world" or kind not in RECURRING) else None`.
- `tiles` is already `{}` off-map (`world.py:104`).
- The tile-less loop (`:252-263`) keeps its `RECURRING` pool for non-world screens.

**Test.** On a world screen, an entity of kind `item` at a tile mints a row with that tile. A second perception named differently ("sphere" vs "ball"), at the same tile, binds to the same row. On a menu screen, `item` rows still match by name.

---

## B: pixel-anchored objects

### B1: new object rows start on the sprite cell
- **Where:** in `World.before`, after `tiles = self._tiles(...)` (`world.py:159`), for each static perception (not an actor, not an exit, not the player) whose tile has no existing static row within 1 tile.
- **What:** look at the snapped cell and its 8 neighbours on the screen grid. The grid phase comes from `self.tracker.phase`, which is set from the motion-locked player (`world.py:156`); B1 runs only when the phase is known.
- **Background test:** a cell is *background* when its 16×16 patch occurs exactly at another grid cell of the same frame. Floors and walls repeat; an object on them does not.
- **Choice:** use the perceived cell if it is not background, else the nearest non-background neighbour; if all 9 are background, keep the perceived cell.
- **Projection:** the tile is projected from the chosen cell.
- **Helper:** one small function `_distinct_cell(arr, cx, cy, tile_px)` shared with nothing else. Reuse the logic from reverted commit `f87b621` (`git show f87b621 -- agent/track.py`); do not resurrect that commit.
- Mark it: `# ponytail: repeated-patch test; a unique floor tile beside an object can win; swap for a sprite mask if one exists`.
- **Test:** a synthetic frame with a repeating floor and one distinct 16×16 block one cell right of the perceived point; the new row's tile is the block's.

### B2: identity by patch
- **On mint, and on each binding sighting** (B1's chosen cell, or the matched row's cell), store on the row `patch`: the 16×16 greyscale cell, base64, as views do (`memory.py:181-187`). Store at most one patch; refresh it only when the row is bound by the same patch, so drift can't creep in.
- **Matching:**
  - Before `mem.observe`, for each static row of this place whose tile is in view (`_view_rect`, `world.py:252`), compare its stored patch with the frame's cell at its expected screen position, and at the 8 neighbours.
  - A row is **present** when the best score passes a patch threshold `PATCH_MATCH`. Its **seen cell** is that best cell.
  - The score is the normalised similarity of the two greyscale 16×16 patches, in the style of `_view_score` (`memory.py:68`; that function works on whole-frame thumbnails, so don't call it directly).
  - Set `PATCH_MATCH` once from the r8 replay: the lowest score that never binds two different static RAM sprites. Report the value, and mark it `# ponytail: one threshold from r8's lab; a knob`.
- **Binding:**
  - A static perception whose snapped cell is within 1 cell of a present row's seen cell binds to that row, nearest first, one perception per row. This beats the tile and name pair costs.
  - Pass it to `Memory.observe` through the existing `tracks` argument (`memory.py:236`): index → row id, already the highest-priority pair (`:277-279`). No new parameter is needed.
  - A row with no patch (old checkpoints) matches as today.
- **Tile:** a present row's stored tile is the seen cell's projected tile. No SB vote is needed, since pixels decide.
- **Test:**
  - two frames where the perceived point of the same object moves 1 cell and its name changes: one row, same id;
  - a different object one cell away: two rows;
  - an old row without a patch still matches by tile.

### B3: presence and absence from pixels
- **Present rows** (B2) are offered as seen, even when perception missed them. This extends SC's in-view rule from "should be in view" to "is verified in view".
- **Moved or gone:** an in-view row whose patch matches neither its own cell nor any of the 8 neighbours for 2 consecutive world frames, with no dialogue box or menu over it, gets `status: "stale"`, plus a `pixel_gone` note with the step.
- **Not yet:** it never becomes `gone` from pixels this round. That would be a new gate on options; status only.
- **Test:** the object's block removed from the frame for 2 frames turns its row `stale` with the note; restored, it is present again.

---

## N: names as tiered claims (no lock-in)

### N1: tiered `called`
- **Storage:** next to `called`, store `called_src: {"tier": "text"|"said", "step", "key"}`.
  - `said` = the game named it when the agent interacted with it (today's speaker path, `loop.py:309`, via `Memory.call`).
  - `text` = the planner named it from cited game text (N2).
- **`Memory.call(entity_id, name, step, tier="said", key=None)`** replaces `called` when there is no claim yet, or when the new claim's tier is the same or higher. Tier order: `text < said`.
- **Old rows:** a `called` with no `called_src` counts as `said`.
- **Perceiver votes** stay in `name` / `recent_names` (tier 1) and never touch `called`.
- **Test:**
  - a `text` claim is replaced by a later `said`;
  - a `said` claim is not replaced by a later `text`;
  - a newer `said` replaces an older `said`;
  - an old row without `called_src` behaves as `said`.

### N2: the planner names things from game text
- **New op:** `{"op":"name","key":"<thing ref>","text":"<name>","why":["t-key"]}`.
- **Validation in `Notebook._apply`** (`notebook.py:72`):
  - the key resolves to a thing (`self._resolve`);
  - the text is non-empty and at most 40 characters;
  - `why` cites known text keys, the same rule as `add` (`:79-82`).
- **On success** it records an event, and the loop applies it with `Memory.call(..., tier="text", key=why[0])`.
- **Wiring:** the notebook gets a `name` callback, as it already gets `ref`, `label`, …. Add `"name"` to the op enum (`plan.py:28`), the REPLY line (`plan.py:13-16`) and `notebook.py:119`.
- **SYSTEM:** append "When game text names something you can see on the map, you may give that thing the name with op name, citing the text."
- **Generic:** no game words anywhere in the code or prompt.
- **Test:**
  - a valid op sets `called` with `called_src.tier == "text"`;
  - an op with no cited key, or an unknown thing, is an error row;
  - `eval_plan --check ops` on r8 still passes.
- **Assertions:** any existing test that pins the op enum or the REPLY text is a stop. Message the planner; that change is pre-approved **only** for adding `"name"` to an enum or to REPLY.

### N3: unconfident rows show their candidates
- **Where:** `World.label` (`world.py:736`) for a thing.
- **When:** it has no `called`, and its shown name has fewer than 3 of the last 6 votes.
- **What:** return `"<name> (or: <next>, <next>)"` with up to two other recent names, most frequent first, e.g. `check sphere (or: ball, table)`. With a `called`, return `called` as today.
- **Test:** 2 votes "ball", 2 "sphere", 1 "table" gives `ball (or: sphere, table)`; 4 of 6 "ball" gives `ball`.
- **A3:** if a case fails only because a label gained `(or: …)`, update that case's `label_re` to allow the suffix. Report each such update; no other A3 change.

---

## AP: micro-check for A and P (≤ 40 asks, scratch)

A scratch script calls `agent.perceive.perceive` on recorded frames. Every post is charged through `scripts.run_agent._ledger`, as in r7's GM (`.tmp/r7/gm_check.py` is the template).

**Frames:** r8 lab world steps 121, 125, 129, 141 and 146; r6 lab 147; r3b lab 112; plus one control, r6 step 88 (town). That is 8 frames.

**Arms**, 2 tries each:
- **P0:** today's prompt at temperature 0.2. Reuse r7's GM replies where the frame matches; otherwise ask.
- **P1:** today's prompt, temperature 0, seed 7.
- **A1:** P1 plus marks.
  - Marks come from the frame's memory at the checkpoint before it: the in-view static rows of that place, at most 8, nearest first.
  - Each mark is a 1-px coloured corner bracket around the cell plus a small digit drawn outside the sprite area. The caption is below.
  - The schema gains an optional integer `mark`.

**Cost:** 8 frames × 2 tries × (P1 + A1) = 32 asks, plus at most 8 for P0 when it can't be reused.

**Metrics:**
1. **Repeatability:** the share of entities identical across the 2 tries (name plus a cell of 1/10 of the screen). Go for P: P1 ≥ 90%, and P1 is greater than P0.
2. **Seed honoured:** P1 try 1 equals try 2 byte for byte on at least 6 of 8 frames. If not, P keeps temperature 0 without relying on the seed, and the report says so.
3. **Marks used:** in A1, the share of mark-tagged entities whose mark's row is present by pixels (B2) at that cell. Go: ≥ 80%.
4. **No anchoring or harm:**
   - names in A1 never contain the mark digit as a name (for example "3");
   - A1's entity count stays within ±20% of P1's;
   - on the control frame (no marks), A1 equals P1.
5. **Small things kept:** each ball (slots 2–4) on the r8 frames is still listed in A1 as often as in P1.

**Go for A:** metrics 3–5 hold. **Go for P:** metric 1 holds.

---

## A: perceive against memory (marks; only after AP says go)

- **Drawing.** `World.mark` (`world.py:140-146`) already draws the player box when the screen was a world screen. It also draws marks for at most 8 in-view static rows of the current place: present rows (B2) first, then rows that should be in view, nearest first.
  - Each mark: a 1-px corner bracket in a colour that differs from the player box, plus a digit 1–8 in a 5×7 bitmap at the bracket's top-left corner, outside the 10×10 sprite centre.
  - `World.marks` keeps `{digit: row id}` for this frame.
  - Mark it: `# ponytail: fixed bitmap digits and colour; pick a contrasting colour per frame if a colour game hides them`.
- **Caption.** Append to the perceiver ctx (`loop.py:100`, next to `CAPTION`) only when marks are drawn: "Small numbered brackets mark spots where something was seen before. If a thing you list sits in a bracket, add "mark": its number. Describe every thing in your own words; a bracket may now be empty."
  - Bump `prompt_version` (`loop.py:36`) to `"e5-perceiver-12"`.
- **Schema.** Add an optional integer `mark` to the entity properties in `perceive.py:43-45`. `normalize` keeps it when it is an int from 1 to 8.
- **Binding.** In `World.before`, an entity with `mark: d`, where `d` is in `World.marks`, joins the B2 binding map (index → that row) when the classes agree and its point is within 2 cells of the mark. Otherwise it is ignored, and B2 and the tile path decide.
  - **Names are never sent**, so the perceiver's name stays an independent tier-1 vote.
- **Cache.** The marked image differs from the raw frame, so the existing frame-hash plus `marked` reuse key (`loop.py:98`) must include the mark set. Hash the `{digit: cell}` list into `cached_marked`.
- **Tests:**
  - with two remembered rows in view, the image the perceiver receives has 2 brackets, and `World.marks` maps digits to row ids;
  - an entity returned with `mark: 2` binds to row 2 even when it is named differently and its point is 1 cell off;
  - with no rows in view there are no marks and no caption line;
  - `prompt_version` bumped. Any existing test pinning `"e5-perceiver-11"` or the exact ctx is a stop.

## P: temperature 0 and a seed (only after AP says go)

- **`Ollama`** gets optional per-call `options`. `ask(..., options=None)` merges over `self.options`.
- **The cache key** (`model.py:108-115`) includes the merged options, but only the keys that differ from the constructor defaults, so existing cached keys for default calls stay valid.
- **The loop** passes `{"temperature": 0, "seed": 7}` for `role="perceiver"` and `role="act"`. The planner and skills are unchanged. Wire it at the two call sites: `perceive()` gains an `options` passthrough, and so does `act()`.
- **Test:**
  - the key changes when options differ from the defaults, and does not change for default options;
  - the perceiver and act requests carry temperature 0 and seed 7, and the planner's does not.

---

## LV10: clean-boot live run (≤ 400 asks)

```
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r10_boot --steps 150 --max-calls 400 --decide
```

Run it in the background, with the viewer up. Then run `eval_run`, `eval_moves`, `eval_entities`, and `eval_identity` on the lab window, if the lab was reached.

**Success:** a party by step 150 (truth), from boot. This has never happened.

**Report:**
- first party step;
- `m1_gate_pass`;
- places reached, with the step each was first entered;
- asks and seconds per step;
- `eval_identity` per class;
- the names, tiers and `called_src` of the ball rows;
- the planner's `name` ops;
- how many marks were drawn and used per lab step.

## Order
1. M, and its baselines.
2. R0, then the gate.
3. B1, then the gate.
4. B2, then the gate.
5. B3, then the gate.
6. N1.
7. N2.
8. N3, then A3.
9. AP (≤ 40 asks).
10. A if go, then the gate plus A3.
11. P if go.
12. A3 on all cases.
13. LV10, if R0 and B2 passed their gates. A and P are optional for the run.

Each part: code, suite (`-n auto`), measure, commit. Revert it if it is worse than the part before it.

## Validity check (2026-10-22, HEAD `9081817`)

**`agent/memory.py`:**
- `:12` `RECURRING`; `:14` `STATIC_GATE = 1`; `:60` `VIEW_MATCH`; `:68` `_view_score`;
- `:128-132` `call` (first name wins);
- `:181-187` `remember_view` (patch storage pattern); `:189-203` `seen_view`, `_view_score` / `VIEW_MATCH`;
- `:236` `observe(..., tracks)`; `:248` the `RECURRING` tile drop; `:252-263` the tile-less pool; `:277-279` the track-hit pair.

**`agent/world.py`:**
- `:103-110` `_tiles` (projects the perceived point);
- `:140-146` `mark` (player box);
- `:147-168` `before` (`:156` grid phase, `:159` tiles, `:161` observe call);
- `:252` `_view_rect`; `:736` `label`.

**`agent/loop.py`:**
- `:18` `CAPTION`; `:36` `prompt_version "e5-perceiver-11"`;
- `:98` the reuse key with `marked`; `:99-100` ctx and caption;
- `:309` `self.mem.call(...)` (speaker naming).

**`agent/perceive.py`:** `:37-48` `SCHEMA`; `:71-84` `normalize`.

**`agent/model.py`:** `:24-29` options; `:108-115` `_key` (options not in the key).

**`agent/plan.py`:** `:5-12` `SYSTEM`; `:13-17` `REPLY`; `:28` the op enum. **`agent/act.py`:** `:47` `act(...)` (gains the `options` passthrough for P).

**`agent/notebook.py`:** `:72` `_apply`; `:79-82` the citation rule; `:119` the op list.

**Runs and ledger:**
- r8 diverges in the no-reload replay from step 147;
- ledger 6751 / 7854.

## Known limits (not this round)
- Characters keep the tracker as is: 3–4 real switches per run.
- `gone` from pixels.
- Spatial relations ("on the table").
- The tile map (T-A failed its measure in r8).
- Goal-relative verdicts (V-B).
