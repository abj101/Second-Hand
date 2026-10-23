# r13 review (LV13 `runs/m1_r13_boot`, 250 steps, 460 asks, 2026-10-22)

**Sources:**
- the orchestrator's `.tmp/r13/report.md`;
- a 0-ask Haiku gap survey (`.tmp/r13/gaps/`);
- my own spot checks against trace, truth and code.

The suite stands at 788 passed, 1 skipped. Agent code is net −1 line.

## Fixed (verified)

| item | r12 | r13 | evidence |
|---|---|---|---|
| False "speaks to you" goal close (N1) | goal pointed at Oak, balls never picked | none. q8 "Choose a POKéMON" targeted the display case and was closed with `done` | **starter chosen, step 140** (first party on the r12+ code) |
| Planner map loses its legend (N2) | balls missing from the plan prompt | T13 step-196 case passes | `.tmp/r13/g/points.txt` |
| No-op "go to NPC, not on screen, here" (X1) | 18 picks | 0 | trace labels |
| Guessed names printed as facts (I1) | 11 bare picks | 0. Every guess reads "X, maybe NAME" | trace labels |
| Outdoor NPC chase / repeats | 27-step plateau, 43 tiles / 50 revisits, 37 "go to" picks | 5-step plateau, 12 tiles / 9 revisits, 1 "go to" pick | r12 diag scripts on r13 |
| Places can be named (N3) | `names no known thing` errors | `name` on places is ok | plan ops |
| Asks per step | 2.78 (cache collapse) | 1.84 (perceiver cache: 108 hits) | ledger 7854 → 8314 |

## Improved, not fixed

1. **Identity guesses still steer the planner.**
   - Mom's line "PROF. OAK, next door" led the planner to name Blue's house "PROF. OAK's office" and the woman there "PROF. OAK". Neither is right; picture 3 (Oak) was never in map 39.
   - Result: 29 steps in Blue's house (65–93), 7 "talk to … maybe PROF. OAK" picks, and map-0 → lab took 44 steps (r12: 29).
   - Two pieces are missing:
     - **nothing falsifies a guess**: talking to her and getting a reply with no Oak in it leaves "maybe PROF. OAK" in place;
     - **nothing confirms one**: no guess reached `said`, because only 3 replies in the run had a `NAME:` prefix.
2. **Wrong closes remain.** q6 "Talk to PROF. OAK in his office" closed by arrival ("crossed") at step 93, without talking to anyone.
3. **X1 has one likely false `gone`:** `e_scientist` in the lab, with a truth sprite 8 px away (1 of 6).

## Open gaps (ranked)

| # | gap | evidence | root cause (code) |
|---|---|---|---|
| G1 | **An offered exit that cannot be planned becomes a silent no-op.** | steps 211–250: 31 steps and 40 act asks, choice null, 0 inputs, "did nothing 37×" | Exit rows are offered with no route check (`world.py:1162-1165`), while place and thing rows have one (`reachable`, `plan`). `loop.py:199` sets `action = None` when `plan()` is None, so the step becomes a wait and reports `done`. This breaks the "offered = executable" rule. |
| G2 | **Re-entry lands one tile off and shifts the place's frame.** *(Corrected 2026-10-22: the earlier "67 of 121 steps drift" claim paired the wrong steps. With truth[N+1] matched to tile[N], belief follows truth on all 124 lab moves.)* | The offset was (5,3) for steps 123–208 and (6,4) from step 209 on, after re-entering the lab: a single one-tile shift. Bumps recorded in the new frame landed on cells first mapped in the old frame, so tile (3,1) ended up walled on all four sides. | `_locate` (`world.py:412-419`) prefers the arrival handed over by the matched exit (`memory.py:229`). That arrival was an *inferred* way back, and `space.py:35` already notes it "lands 1 off until re-anchored". A view match is tried only when there is no arrival. |
| G3 | **A phantom exit was tried 19 times.** | "door (top wall)" (exit:0,-2 in the lab; truth (5,1), a wall). Every "walk onto" reported `done`, and the truth map never changed. | *(Corrected.)* Belief was right: (0,-2) = truth (5,1). The pushes UP, RIGHT and LEFT were recorded. DOWN was pressed three times into Oak (truth picture 3 one tile below). `fold` (`space.py:15`) skips a zero shift into an actor's tile, so DOWN stays untried in `push_dirs`, `_refuted` never fires, and the exit is offered again. |
| G4 | **Place identity across cuts.** | `p_place_4` spans Pallet and the lab (105–122), so the lab entry was not a new place, and "OAK's Lab" was named on the wrong place. Leaving the lab created a new place, "outside house" (`p_place_7`), instead of re-identifying Pallet. | Cut / place-fingerprint matching. |
| G5 | **The exit you just came through reads as a direction.** | Step 208, outside: "go to UP, here", note "exit, never crossed". It was picked, and the player walked straight back into the lab. | The exit label falls back to the push direction (`world.label` exit branch). The crossing that arrived here is not shown on the reverse exit. |
| G6 | **Planner op errors: 23 of 77 ops (30%).** | "key is not open" ×11; "reason is empty" ×4 (q11 "Leave OAK's Lab" stayed open after leaving); name errors ×5 | The prompt or schema doesn't show which keys are open or that `done` needs a reason; the errors come back only on the next plan. |

**Recommended order for r14:**
1. **G1** first: small, and a 0-ask fix. Give exit rows the same route check as other rows, or turn an unplannable pick into a `route` failure instead of a wait. Every offered row must execute or fail loudly.
2. Then a **0-ask diagnosis of G2** (truth-aligned per-move fold audit on the lab steps), since G2 drives G1 and G3.
3. G4 and G5 next.
4. G6 and guess falsification after that.
