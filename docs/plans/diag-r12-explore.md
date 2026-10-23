# Mistaken identity and outdoor repeats (diagnosis, 0 asks, 2026-10-22)

**Source:** `runs/m1_r12_boot`, map 0 (Pallet Town), steps 1–150, checked against truth sprites. The scripts are in `.tmp/r12/diag/explore/`. Truth sprite ids: pic 3 = Oak (the only id tied to Oak's speech), pic 13 = generic NPC.

## Findings (verified against code)

### Identity

1. **Every "PROF. OAK" label came from a planner `name` op, not from the game.**
   - At step 51 the planner named `e_person_2` "PROF. OAK", citing t4 (Mom: "PROF. OAK, next door, is looking for you") and t6 (the NPC said "I'm raising POKeMON too!"). Neither line names that NPC.
   - At steps 56–62 the sprite was pic 13. Oak (pic 3) first appears on map 0 at step 145.
   - At step 110 it named `e_building_edge` "PROF. OAK" with no talk at all.
   - The perceiver never called anything outdoors Oak.
2. **A guess prints exactly like a fact.**
   - `World.label` (`agent/world.py:765`) returns `called` bare, and `history()` drops `called_src`.
   - So a `text`-tier guess (`memory.py:187`) reads the same as a name the game gave.
   - The `said` tier needs a `NAME:` speaker prefix (`loop.py:336`), which the generic NPC's line did not have.
3. **The act model also borrows names from quest text.** At step 76, in the lab, the label was "talk to NPC" (pic 13), but its `why` said "talk to PROF. OAK".
4. **Id churn.** The same pic-13 NPC got 8+ entity ids (`e_person_2`, `e_npc_7`, `e_npc_8`, `e_npc_9`, …). Its remembered positions pile up as separate rows.

### Exploration

5. **The biggest waste is no-op "go to" rows, not exploring.**
   - The most common pick on map 0 was "go to NPC, not on screen" (36 of 93 picks).
   - 16 of those resolved to `here`: the remembered actor tile *is the player's tile*. The skill runs zero moves and reports `done`, and the same row is offered again (steps 99–101, 106–107, 123, 125–126).
   - Cause: `World.options` (`world.py:1177-1194`) offers any remembered off-screen entity that has a tile.
   - An actor is marked `gone` only when its track is lost in view (`world.py:189-196`). Standing on its remembered tile and not seeing it does not count against it.
6. **Explore itself works when picked.** 9 of 12 explore picks gained 16+ new cells. The two stalls (95–108, 14 steps; 116–142, 27 steps) are both NPC go-to loops.
7. **The explore row gives no useful facts.**
   - "explore UP, 15 steps to unseen ground" counts route length plus overshoot (`world.py:1197`, `1234`), not how much is unseen.
   - Tiles record only their last step, so nothing says "you walked here 6 times" or how much of a direction was already covered.
8. **For contrast, r11 map 0:** 12 distinct tiles visited, 9 revisits, no NPC loops, and 7 of its picks were exit walks. r12: 43 tiles, 50 revisit steps.

## Options (0 asks to build; recommended = what you said you'll take)

| id | question | options | recommended |
|---|---|---|---|
| I1 | A guessed name prints like a fact | **a:** labels show provenance: a `text`-tier name renders "NPC, maybe PROF. OAK (from game text, not confirmed)". The bare name appears only at `said` tier, i.e. once the game itself names the thing when the agent talks to it. The planner prompt says that a `name` from text is a guess and stays one until the thing confirms it. **b:** a, plus the `name` op on a thing must cite a line that thing said (a gate). **c:** keep | a (a fact, not a gate; the guess stays falsifiable) |
| I2 | `said` tier is too narrow | **a:** after an interaction, a reply that names a speaker (a `NAME:` prefix *or* a perceived speaker name on the text box) confirms that thing's name; a reply with no name confirms nothing. **b:** keep | a |
| I3 | Quest text leaks names into act | **a:** the focus line keeps the quest wording but adds the target's label with its provenance, e.g. "target: NPC, maybe PROF. OAK". **b:** keep | a (pairs with I1) |
| X1 | No-op "go to" on a stale actor | **a:** evidence rule: if a remembered thing's tile is on screen now (including the player's own tile) and nothing is perceived there, the row is marked `gone`, the same status a lost track gets. Off-screen actors also show "last seen N steps ago". **b:** a, plus remembered actors expire after K steps (an arbitrary constant). **c:** drop only rows at distance 0 (a band-aid) | a (the screen contradicts the memory, so memory updates) |
| X2 | The explore row says nothing useful | **a:** the label states facts: "explore UP: ~N unseen cells past the edge, M steps away, last walked step S / never". N is the open cells in that direction, from the `seen` store that already exists. **b:** a, plus the planner view gets a coverage line per place ("seen 642 cells; open edges: UP 30, LEFT 0, …") | b (cheap, about 1 line per place; it lets the planner choose a direction on purpose) |
| X3 | Revisits aren't visible | **a:** `tiles` rows count visits (`n`); the explore and go-to labels say "mostly walked before" when most of a route's tiles have n ≥ 2. **b:** keep (X2 covers most of it) | b (X1 + X2 fix the measured loops; add X3 if repeats persist) |
| X4 | Id churn | **a:** an actor seen within 1 tile of a remembered same-kind actor in the same place re-uses that row instead of minting a new one. **b:** keep, since X1 clears stale copies anyway | b for now (X1 removes the harm; churn is a perception-tracking problem to measure after r13) |

**Combined with N1a–N4a** (`diag-r12-lab.md`), this is round r13. **Proof:**
- an extended N4a 0-ask replay that re-renders steps 56, 99, 110 and 196;
- checks: no bare "PROF. OAK" before step 150; no `here` go-to rows; explore labels carrying a cell count; balls in the legend.

**Ledger:** 7854 / 7854. A live check needs a cap raise in `scripts/run_agent.py:16`.
