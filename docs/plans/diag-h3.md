# H3: recognising a room on re-entry (measurement, 0 asks, 2026-10-23)

**Data:** r11–r14 boot runs, 41 world arrivals, 22 of them re-entries. Scripts are in `.tmp/r15/h3/` (`events.py`, `evidence.py`, `reach.py`, `mint.py`). Alignment: truth[N+1] is paired with trace step N.

## Findings
1. **19 of 22 re-entries reused the right place id; 3 minted a duplicate.** No wrong merges.
   - r12 step 150: into the lab, under Oak's dialogue.
   - r13 step 207: out of the lab, to Pallet.
   - r14 step 123: into the lab, under Oak's dialogue. This one started the LV14 failure chain.
2. **Each duplicate arrived 5–7 tiles from any tile stood on in the earlier visit.** Views are stored only where the player stood, and `seen_view` searches ±`VIEW_REACH` = ±4 tiles. A screen is 10×9 tiles, so a stored view 5–7 tiles away still overlaps the arrival frame by 30–50%, but it is never compared.
3. **On the actual mint frames, a ±6 search finds the right room:**

   | duplicate | ±4 (today) | ±6 | best other place (±6) |
   |---|---|---|---|
   | r14 step 123 (lab) | 0.604 | **0.996**, at offset (0,-8), the same offset the later loop-closure merge found | 0.317 |
   | r13 step 207 (Pallet) | 0.648 | **1.000** | 0.498 |
   | r12 step 150 (lab) | 0.566 | 0.634 (the first visit stored only 2 views) | 0.324 |

4. **False-match risk at ±6, threshold 0.9 (unchanged):** 0 of 41 arrivals match the wrong place, and 18 of 19 correct reuses still match correctly. The closest wrong score is 0.847: Blue's house against the Red's-house interiors (0.786 at ±4). The margin to 0.9 shrinks from 0.114 to 0.053.
5. **Lowering `VIEW_MATCH` does not work.** Wrong interiors already score 0.74–0.85, so even 0.75 gives 3–9 false matches. Thing names and confirmed speakers don't separate rooms either: overlap is generic ("table", "npc"), and speaker overlap was 0 in all 41 arrivals.
6. **Topology alone does not decide.** 2 of the 3 duplicates had a recorded exit from the previous place to the right room, but so do most places. For the lab, the wrong interiors (1F, Blue's house) score *higher* than the right lab at ±4, so "best among neighbours" would pick wrong.

## Options

| id | option | recovers | risk / cost | recommended |
|---|---|---|---|---|
| **H3a** | `VIEW_REACH` 4 → 6 (one constant, `memory.py:88`). The same search also feeds the loop-closure merge and G2's re-anchor, so those get the wider reach too. | 2 of 3 duplicates (r13, r14), on the real mint frames | No false matches in 41 arrivals, but a thinner margin (closest wrong 0.847 vs 0.9). About 2× the comparisons per match (169 vs 81 shifts per view), which is still small. | **yes** |
| H3b | H3a, plus accept a match found beyond ±4 only if it beats the next-best place by ≥ 0.2 | the same 2 | one more condition; no case in the data needs it (the margins here are ≥ 0.5) | no (add it only if a false match ever shows up) |
| **H3c** | A new place minted by a cut says so: "new place since step N; it partly looks like <X> (0.63)", whenever the best other place scores ≥ 0.5. The planner and act see that the room may be one they know; nothing merges. | the residual r12-type case (as a stated fact, not a fix) | one line in the view and focus | **yes** (facts, no gate) |
| H3d | Lower `VIEW_MATCH` | 0–1 | 3–9 false merges among similar interiors | no |
| H3e | Store views of the cells beside each exit, not only tiles stood on | unknown; needs a capture change and a new measurement | larger change | not now |

## Follow-up: identity from crossings, and forced movement (2026-10-23)

1. **Merges are carried through memory:** `World.merge` (`world.py:1397-1437`) moves tiles, exit arrivals, entities, relations and the current position, and views follow through `_home` offsets. One gap: `crossings` rows keep the merged place's id, so their "used N×" counts are orphaned. r14's duplicate *was* merged (by step 150), but only after the damage at 142–148.
2. **Forced movement is not followed.**
   - `advance` (`skills.py:228`, i.e. `press("A")`) and `wait_settled` measure no scroll, and `prepare` folds only direction-press moves.
   - In r14, steps 118–123, Oak walked the player from the grass to the lab door under dialogue. Belief stayed at (6,-5) (truth (11,1)) until the cut.
   - The cut therefore had no exit evidence (`memory.py:224` needs the believed tile within 1 of a known exit, plus a direction), so only a view match could identify the room.
3. **Crossing knowledge was already there.** Pallet had a known exit to the first lab id at belief (7,6), from the first visit.
4. **Offline probe** (`.tmp/r15/h3/escort.py`, from `checkpoint/000120/pyboy.state`):
   - pressing A as the harness does, and summing per-frame camera scroll above the text box, gives (−16, −152) px. That is 1 tile right and 9.5 tiles down (scroll is opposite to motion), against the true walk of (+1, +10).
   - Belief would have been (7,5): within 1 tile of the known lab exit, so the existing exit rule returns the right room with no pixel matching.
5. **All 3 duplicates trace back to forced walks:**
   - r12 step 150 and r14 step 123 were cuts at the end of escorts;
   - r13 step 207 left a lab first entered under an escort, so no lab-side way back had been recorded.

| id | option | recommended |
|---|---|---|
| **F1** | Follow the camera during every settle, not only direction presses. Sum per-frame scroll above any text box and fold it into position. The last movement direction before a blank becomes the cut direction, so `match_place`'s exit rule and `World.after`'s crossing record work for scripted walks too. Reuses `observer.scroll` and `fold`; no RAM. | **yes**, primary |
| **F2** | `merge` re-keys `crossings` rows to the surviving place | yes, small |
| H3a | `VIEW_REACH` 4 → 6, kept as the *fallback* for arrivals with no crossing knowledge (first visits, warps, fainting) | yes, fallback |
| H3c | A new place minted by a cut states "partly looks like X" | yes |

**Proof without model requests:**
- the escort replay from checkpoint 120 must end with belief within 1 tile of `p_place_4@7,6`, and `match_place` returning `p_place_5`;
- re-scoring the 41 arrivals with F1 off and on;
- in addition to the existing proof below.

**Existing proof:** keep `.tmp/r15/h3/mint.py` and `reach.py` as the check. After the change:
- re-scoring the 41 arrivals must show the r13 and r14 duplicates matched and 0 wrong matches;
- a T15 replay of r14 step 123 from checkpoint 120 must give `next_place` = `p_place_5`, not a new id.
