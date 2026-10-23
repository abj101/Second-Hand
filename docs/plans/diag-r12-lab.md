# Why LV12 never picked a Poké Ball (diagnosis, 0 asks, 2026-10-22)

**Source:** `runs/m1_r12_boot` steps 151–198 (Oak's lab). Worker outputs are in `.tmp/r12/diag/{plan,name}/`.

## Findings (verified against code)
1. **The act goal states a close that no longer exists.**
   - `notebook.render_focus` (`agent/notebook.py:314`) shows a thing target as `(done when: <thing> speaks to you)`.
   - The speech close (`talked()`, `notebook.py:171-182`) has had no caller since `e71bd68` (r8 Q-A: "no auto-close on talk"). Only exits ("crossed") and places ("entered") close a thing target.
   - So in steps 180–198 the act model read "Choose a POKéMON … (done when: OAK speaks to you)" and kept walking to Oak. The ball rows (`check item (or: ball…)`, `check sphere 3`) were offered at every world step and never picked.
2. **The planner is told a target is a place or a person.** `plan.py:10`: "Give a quest a target when it means being in a place or talking to someone the map names." Across all 16 runs no quest ever targeted an item: 69 place targets, 27 actor targets, 46 other things, 0 items.
3. **The planner's map loses its legend.**
   - `World.view` writes the map grid first, then the other places' summaries, then this place's legend ("Things here"), all within `VIEW_TOKENS * 4 = 2000` characters (`world.py:~995-1023`).
   - At step 196 the map showed 12 letters but only one legend line ("Thing A BLUE"); the offline repro gives 1980/2000 characters, legend `{A, B}`.
   - The balls (`e_sphere_1_2`, `e_sphere_3_2`, landmarks in memory) never appeared in a plan prompt.
   - `name C/J/N "POKéMON 1-3"` failed because those letters had no legend entry (`world.py:623-624`, `self.legend` keeps only the legend lines that were shown).
4. **Places cannot be named.** `name shop` resolves to `place:p_place_7`, and the `name` op takes things only (`notebook.py:122-123`). It failed at steps 176 and 179 (10 `names no known thing` errors in the run).

## Options (all 0 asks to build; **picked 2026-10-22: N1a, N2a, N3a, N4a**)

| id | question | options | recommended |
|---|---|---|---|
| N1 | The false done-when | **a:** say only what is true: exits "go through X", leads-to "enter X", any other thing "(target: X; you close this quest)"; `plan.py:10` lets a target be any thing to talk to or check, and drops "closes by itself" for those. **b:** a, plus close a thing-target quest when the agent interacts with that thing and the game replies (reverses r8 Q-A). **c:** keep | a (it keeps your r8 decision; the planner closes the quest with `done` after the reply) |
| N2 | The planner map budget | **a:** this place's legend comes right after its map, before other places' summaries; a letter is drawn only if its legend line fits. **b:** a, plus double the budget (`VIEW_TOKENS` 500 → 1000; roughly +500 tokens per plan call, about 30 plan calls per run). **c:** keep | a |
| N3 | Naming places | **a:** `name` accepts a place ref, as a text-tier alias with the same game-text citation rule; the label shows it first. **b:** a clearer error ("that is a place") only. **c:** keep | a (the perceiver's "shop" became the goal wording twice: r11 RUN, r12 lab) |
| N4 | Proof before any live run | **a:** a 0-ask replay. Re-render the step-179 and step-196 plan and act prompts from checkpoints 175/195 with the new code, and check that the balls are in the legend, C/J/N resolve, and no "speaks to you" appears. **b:** a, plus a planner micro-check (≤ 10 asks) on the step-169 prompt, which needs a ledger cap raise | a now; b only with a cap raise |

**Ledger:** 7854 / 7854. Any live check needs a cap raise in `scripts/run_agent.py:16` (your call).
