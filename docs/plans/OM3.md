# OM3: Unexplored means unseen; one exit, one name; a prompt that fits

Status: built 2026-10-13, dry gates 8/8, live decision points FAILED (1/8 vs >= 6), one measure spent, no re-measure · Branch: feat/lean-agent · Follows: [OM2.md](OM2.md) (STOP 9)

## 0. Why (the 7 findings from reviewing OM2's act prompts)
1. The dead-end line appeared in 0/8 prompts: `beyond` counted all untried edges, and the 2F bedroom always had a fake `doorway#2` plus 25-26 edges "(0 into unseen)".
2. The explore option said `(26 spots)` in a room where 0 edges lead into unseen tiles.
3. One exit had two names and positions (`stairs` vs `push RIGHT`, and a fake exit one tile away); use counts differed per side.
4. Noise: doubled tool word, a 45-name things row with other-place names and `vent`, `vent 1`, `vent 2`.
5. Size gate 0/8 (1,861-2,140 vs 1,600).
6. Crossings seen only by the next observation were not counted.
7. Contract text of OM and OM2 was unmerged.

## 1. Decisions (executive, D1-D8)
D1 unexplored = untried edges into unseen tiles everywhere (explore label, `beyond`, `dead_end`); explore stays offered. D2 a destination-less unverified exit within Chebyshev 1 of a verified exit is that exit. D3 one name (`head`) and symmetric counts. D4 tool word once; things row only of this place, grouped (`vent ×3`). D5 compact act facts, shorter reply text. D6 crossings via the next observation. D7 contracts merged. D8 one live measure only if every dry gate passes.

## 2. Work packages
| WP | Goal | Result |
|---|---|---|
| A | D1: `unseen_edges`, explore label, `beyond`, dead end | landed |
| B | D2, D3: exit merge, one name, symmetric counts | landed |
| C | D4: `_option_text`, things row, `resolve` strips `×N` | landed |
| D, D2 | D5: `view(compact=True)`, shorter REPLY/SYSTEM (-474 chars), first-crossing arrival backfill, compact trims | landed |
| E | D6: `World.unseen_cross` called at loop.py:62 | landed |
| F | dry gates: dead-end note on the 2F stairs, 2F explore label `(0 spots lead anywhere unseen)` | landed |
| G | D7 contracts.md merge (docs commit) | landed |

## 3. Gate
| Metric | Result | Command |
|---|---|---|
| Unit suite | 331 passed (310 at entry) | `pytest tests/agent -q -p no:cacheprovider` |
| Generality | 0 hits | `git grep -niP ...` (section 2 of OM2) |
| Places | PASS | `scripts/eval_act.py --om places` |
| Dry: right present / exit truth / size | 8/8, 8/8, 8/8 | `scripts/eval_act.py --om cases` |
| Act prompt tokens (len/4) | 1,345 / 1,418 / 1,412 / 1,461 / 1,480 / 1,460 / 1,583 / 1,460 (limit 1,600) | same |
| **Live: decision points** | **1/8 (gate >= 6); old arm 0/8** | `scripts/eval_act.py --om cases --live` |
| Requests | 8 (ledger total 2112 -> 2120, M1 2064 -> 2072 of 2125, 53 left) | `runs/e3_budget.json` |

## 4. Result: live picks (new arm; the model's why)
- 82: free buttons RIGHT, wrong ("exit the current room to the right to find the lab").
- 102: option 3 `approach NPC`, wrong ("talking to the NPC (Mom) might provide more clues").
- 103: free buttons DOWN, wrong ("need to get back to 'place 3'... move down").
- 110: free buttons UP A, wrong ("move up and interact with the door mat").
- 113: option 1 `exit stairs -> place 3`, right.
- 115: option 4 `interact with character`, wrong.
- 122: free buttons UP A, wrong ("exit the current area to reach Prof. Oak's lab").
- 137: free buttons DOWN, wrong ("DOWN is the only cardinal direction not yet tried").
Reading: 5 of 8 picks bypassed the options (free buttons), even where the stairs option was listed with the dead-end note; the model reasons about directions, not about options. The prompt content is now truthful and short; the weakness is that the option list is not what the model reasons from.

## 5. Deviations
- D2: only destination-less records merge into a verified exit; an unverified exit with another known destination stays separate (merging hid a distinct exit).
- Test fixtures `north`/`hatch` in test_world/test_options moved from Chebyshev 1 to 2 of a verified exit (D2 merges adjacent destination-less ones).
- The compact view also drops the `Don't believe:` line (the option notes carry it) and `Flag:` lines (the `Flags:` line carries them); case 122 was 1,643 before these trims.
- The docs are in a second commit because the live result is part of them.
- Golden: `act/memory/crossings.jsonl` gained the arrival-side row of the first crossing (symmetric counts).

## 6. Contract text
Merged into contracts.md (memory, notebook and act, world) by WP-G; the OM3 items are the final behaviour.

## 7. Open issues
1. Live 1/8: the model chooses free buttons in 5/8 cases. Options: make free input less attractive in wording only (no gate), put the options first in the prompt, or give free buttons the same map truth. Needs the user's decision; do not re-measure the same cases.
2. `unseen_cross` trusts `world.pos` to be in the prior place's coordinates; after an uncut place change it can miscount (rare).
3. A mirror case (leaving tile unknown, arrival known) is not backfilled.
4. A clean-boot live run needs a cap raise (about 250 requests per 150 steps).
5. Movement fixes and the perceiver-prompt pass (invalidates the replay cache) remain out of scope.

## 8. OM3.1: why the model pressed free buttons (wording only, no live re-measure)
Diagnosis of the OM3 live replies (0 requests): (1) the compact view had dropped the exit lines and map letters, so nothing said where the stairs were; (2) the flags said `untried UP` next to `UP A -> done x4`; (3) the free-buttons row carried two conflicting notes and "(not checked against memory)".
- H: compact view prints one line per current-place exit: `E at (x,y) <head>: used|possible, never crossed|not an exit, <dx> left/right, <dy> up/down` (`on it` when standing on it).
- I: `here_flag(fails)`: a direction is `untried` only if no failed button combo is recorded under it; new part `failed here: UP A x4, DOWN x2`.
- J: the free row has one note `failed here: ...`; label is `free buttons: <inputs>`; `_option_text` ignores a colon after the tool word.
- Size limit raised 1,600 -> 1,700 (user-approved); prompts `.tmp/om/prompts_om3_1/` (OM3 prompts kept in `.tmp/om/om3_prompts/`).
- Gates (0 requests, ledger 2120/2175, M1 2072/2125 unchanged): dry right 8/8, exit truth 8/8, size 8/8 (1,362 / 1,448 / 1,415 / 1,460 / 1,478 / 1,492 / 1,610 / 1,458); `--om places` PASS; generality 0 hits; suite 333 passed.
- No live re-measure; the user decides.

## 9. OM3.2: recent-flag wording, `--steps`, three live samples
- `recent:` flag: a run whose every result left the screen unchanged reads `<act> -> no effect xN` (was `-> done xN`); shared summary code in `Loop.step`, one test in test_loop.py.
- `scripts/eval_act.py --om cases --steps 82,103,137` filters the cases (dry gates run over the selection).
- Dry (0 requests): right 3/3, exit truth 3/3, size 3/3 (1,362 / 1,415 / 1,454).
- Does `approach exit stairs` cross at case 137? Probably yes, not executed. Evidence: option 1 is `{"thing": 4, "verb": "approach"}`; the perceived stairs sit about one tile right of the player (dx 15.5 px, dy 5.6 px on a 160x144 frame, so delta rounds to (1 tile, 0)); `skills.approach` steps RIGHT onto it and `run_affordance` treats a target starting with `exit` as an exit target (extra push and side steps follow). The observed exit tile (3,-5) has dir RIGHT and crossed at step 114 by a RIGHT press. If the perceived exit had coincided with the player tile it would return `blocked: target is the player` with 0 inputs. Movement was not changed.
- Live, 3 requests (ledger 2120 -> 2123, M1 2072 -> 2075 of 2125): options arm right 0/3; all three picks were free buttons `LEFT` (82: "move left to explore the area and look for a door"; 103: "need to move towards the st[airs]"; 137: "blocked from moving UP; moving LEFT is the next logical step to explore"). No further live run.
