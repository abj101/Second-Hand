# r14 review (LV14 `runs/m1_r14_boot`, 300 steps, 460 asks, 2026-10-23)

**Headline:** no party. The run never got a Pokémon: truth `party` is 0 on every step. From step 149 to the end (152 steps), the player was frozen in Oak's lab with the sprite invisible.

## What happened (truth, trace and an offline emulator probe, 0 asks)

1. **The lab got two place ids.** The first visit (71–108) became `p_place_5`. The second (124+), entered under Oak's "come with me" dialogue, was minted as a new place, `p_place_7`. This is G4: the view match missed, and calibration was deferred in r14.
2. **The quest targeted the Oak in the other copy.** Oak's line t11 said "There are 3 POKéMON … inside the POKé BALLs … Choose!".
   - The planner wrote q10 "Choose a POKéMON from PROF. OAK" and targeted `e_customer`, which is the Oak remembered in `p_place_5`.
   - The Oak standing in the room (`e_npc_1`) had been *confirmed* by his own speaker line at step 144 (`said` tier, I2 working). The quest target still pointed at the other copy.
3. **The act went looking for that Oak.** At step 145 it reasoned "I am currently in a shop, I should try exiting the building to find him". It walked toward the exit, and the game fired its scripted "Hey! Don't go away yet!" (step 148).
4. **The game ended in a locked state.** At step 149 the agent pressed UP, RIGHT, DOWN while Oak's script was walking the player back.
   - From then on the player sprite's image byte is 0xFF (hidden), and arrow presses are ignored.
   - **Offline probe** (`checkpoint/000150/pyboy.state`): arrows at 8- and 16-frame holds do nothing. **START, then B, redraws the player, and movement works again.**
   - Replaying the recorded presses from checkpoint 145 did not reproduce the lock exactly, because the timing differs. So the trigger is likely input during the scripted walk, but it is not proven.
5. **The harness turned "no response" into walls.** The three presses that changed nothing at step 149 were recorded as bumps, so tile `p_place_7@0,-5` was walled on all four sides. After that:
   - every option was refused before pressing anything: 143 steps of "no route beside target", 0 inputs;
   - the act picked the same refused row ("talk to NPC, maybe PROF. OAK") 90 times;
   - the 8 "free buttons" picks pressed only arrows; START was never tried.

## The plan, item by item

| item | plan | result | status |
|---|---|---|---|
| G1 | an unplannable pick fails loudly | 0 null-choice / 0-input waits. The same stuck pattern now shows up as 143 loud refusals, and nothing presses a key to re-test the walls. | **letter met, goal not:** a refusal before trying never tests the walls that cause it |
| G2 | re-entry prefers a view over an inferred arrival | the T14 replay lands on (0,8) | **fixed (replay).** Not exercised live: the second lab visit was a different place id |
| G3 | a push into a character counts as tried | unit checks pass; replay INCONCLUSIVE, as allowed; no fake-exit loop live | **fixed (unit)** |
| G4 | "not known yet" after a scene change under text | implemented | **partly.** The live failure came from the deferred half: the same room was not recognised (G4b) |
| G5 | the way back is named and counted | T14 step-208 replay passes | **fixed (replay)** |
| G6 | planner errors that guide | op errors 29.9% → 11.7% | **fixed** |
| I2 (r13) | a speaker line confirms a name | `e_npc_1` = OAK, `said`, step 144 | **working live** |
| size | net fewer lines | net +23 agent lines | missed |
| LV14 | party, then leave the lab | no party | **failed** (chain above) |

## Remaining gaps, ranked by how much of LV14 they cost

| # | gap | what it cost | fix direction (facts, no hardcoding) |
|---|---|---|---|
| H1 | **A press that changes nothing at all is recorded as a wall.** A real bump turns the sprite (pixels change); an ignored input changes nothing. | 143 steps | Record a wall only when the press made a visible change without moving. A zero-pixel-change press is "no response", stated as a fact ("your last N presses changed nothing on screen"); it writes no wall. If you stand on a tile walled on all four sides, show that contradiction. |
| H2 | **Refusing before trying makes remembered walls permanent** (they can't be falsified) | the same 143 steps | When every route is blocked only by remembered walls, offer the presses into those walls as legal rows ("press LEFT: remembered wall from step N"), so a wrong wall can be refuted. |
| H3 | **The same room re-entered under dialogue gets a new place id** (G4b) | quest target in the wrong copy, which started the chain | Choose: store views beside exits; lower the cut-mint `VIEW_MATCH`; or merge a new place into an old one when an identity fact matches (same confirmed speaker, same things), checked against r12–r14 frames before changing anything. Needs your pick. |
| H4 | **Speaker parsing merges lines.** t11 was stored with speaker BLUE, though it holds both BLUE and OAK lines (`who` None). | target confusion | Split joined pages at each `NAME:` prefix. |

**Recommendation:**
1. H1 and H2 first, at 0 asks. Together they turn a game-side freeze into something the act can see and escape, and they make walls falsifiable, which is your memory principle.
2. Then a 0-ask H3 measurement on the r12–r14 lab re-entries before picking an identity option.
3. H4 is small; bundle it.
