# Program: a building leads to the place behind it, and "check" walks a real route

Written 2026-10-19, from `runs/m1_exit_b1_live_150`, a clean boot of 150 steps on `0eb3603`. This is a new program. Read [rcr/PHASE-E-handoff.md](rcr/PHASE-E-handoff.md) §1, §2 and §5 (architecture, design rules D1-D9, offline playbook) and [program-exit-crossing.md](program-exit-crossing.md) (the B2 closure window this builds on). **There is no live run in this program**; whether to do one is decided after the offline gate. The user chose design A on 10-19.

## 1. What happened

Map ids come from `truth.jsonl`: 0 = outdoors, 40 = the lab interior.

- **S130:** went through the door mat, so the B1 fix works. **S131:** outdoors, `p_place_4`.
- **S132:** q4 "Locate and speak with Professor Oak" opened. At S135 the planner targeted "J" (an error); at **S138** it targeted `thing:e_large_house`, the building seen from outside (kind `building`, tile (8,3)).
- **S137:** the act picked "check large house". The `interact` skill walked greedily and happened to step on the door at (7,6), causing a scene cut into `p_place_5` (map 40).
- **S138, S141, S147:** inside `p_place_5`. Each time the act left within 1-2 steps. At S138 the act prompt read:
  - `Goal: Locate and speak with Professor Oak … (done when: large house speaks to you)`
  - `Here: room with counters`
  - why: *"I need to leave this house to find Professor Oak next door"*.
  - The planner's own why at S138 said *"I have entered the large house"*, but it still targeted the outdoor thing.
- **The evidence was already in memory and nothing read it.** `relations.jsonl` holds `e_large_house leads_to p_place_5` (src observed, sup 3, ev [137, 140, 146]). It is written by the existing effect path (`agent/loop.py` ~250-262, `PRED["place_changes"] = "leads_to"`). Its only reader is `Memory.match_place` (memory.py ~171), for place identity.
- **Zigzag, S143 and S149:** "check large house" sent 30 presses (DOWN RIGHT DOWN RIGHT …) and stopped on `input limit`.

## 2. Root causes

**C1. "Acting on thing X entered place P" is recorded but never used.**
- The goal, the closure, the `Here:` line and the menu notes don't know that `p_place_5` is what the large house leads to.
- So a quest that targets the building can never be met from inside it, and the act reads the target as being outside.

**C2. `interact` never routes.**
- `run_affordance` (agent/skills.py ~262) calls `interact(ctx, at, player)` without a route. `interact` then calls `approach(ctx, target_at, player_at)` with `route=None`, which is the greedy per-axis walker.
- Routing (`Loop._approach_route` → `_screen_route`, `World.beside`) is used only for `verb == "approach"` (loop.py ~187-188).
- Against a large solid thing, the greedy walker oscillates until `input limit`. Its one success at S137 was luck: it stepped on the door.

## 3. Design A: `leads_to` from action evidence (user-approved)

No new relation, writer or kind. A thing **leads to** place P when a live, observed relation `(thing, "leads_to", P)` exists with `sup > con`, `P != thing.place`, and P a live world place. Read it through **one** World function, `World.leads_to(ref) -> list of place refs`, which canonicalises the thing and merged places.

**Contradiction (keeps it honest).** In the same effect block, when an `interact` or `approach` on thing X **completes**, with no place change, and X has a `leads_to` relation, record `relate(X, "leads_to", P, hit=False)`. "Completes" means the skill status is `done`, with no route or interaction failure. The guard matters because a blocked or input-limited try never reached X and says nothing about it (the existing `route` guard).

This retires one-off accidental links. `e_shelf_2 leads_to p_place_3` (S87) was a walk that crossed the stairs on the way to the shelf.

### The four uses

| # | Use | Where | Behaviour |
|---|---|---|---|
| U1 | Closure | `Notebook._close_crossed` (generalise it, don't add a sibling) with an injected `leads_to` callable, injected like `route`/`is_exit` (loop.py ~44, scripts/eval_plan.py `notebook_at`) | An open quest whose target thing leads to P closes with `reason: "entered"` when `data["moves"]` has a move into P at `s >= opened - 1`. That is the same window as B2's exit rule, and it runs both on target-set (`apply_ops`) and on each move (`observe`). |
| U2 | Wording | `Notebook.render_focus`, `_evidence` | A target that leads somewhere reads "(done when: you enter X)". The evidence reads "entered X (place label)". Exit wording (B3) takes precedence for exits. |
| U3 | `Here:` line | One new `World.here(place)` used by **all four** copies of the focus string (loop.py ~161, scripts/check_tracks.py ~172, scripts/eval_act.py ~634, scripts/eval_frames.py ~205) | `Here: room with counters, entered through large house`, built from the things that lead to `place`. Replace the four copies with the one call; don't patch each one. |
| U4 | Menu note | `World.options` `option()` history notes | A row whose ref leads somewhere gets the note `leads to <place label> N×`. Inside P, the row for the outside thing X, if it is listed, also carries it. |

**Known limit (A without B).** Once C2 is fixed, "check large house" stands beside the building and presses A, so it no longer stumbles through the door. The entry then happens through the exit thing (`e_large_house_door`, offered at S134), and `leads_to` is recorded for the **door**, not for the building. A quest targeted at the building then won't close on entry, though U3 still shows "entered through large house door". Measure this first in the live run. If the planner keeps targeting buildings, option B (linking a door to the thing whose footprint it sits in) is the follow-up program, not part of this one.

## 4. Fix C2: `interact` routes like `approach`

- **Loop.step** (loop.py ~181-189): compute the route for `interact` too, with the same `_approach_route` call `approach` uses. Pass it through `run_affordance(..., route=...)` to `interact`.
- **For interact, the target tile is never the destination.** Stand on the nearest reachable free neighbour, facing the thing: `World.beside(place, pos, tile)` returns `(stand, facing, dirs)`. `_screen_route` already does this for occupied (actor) tiles; for interact, use `beside` for every target.
- **skills.interact** accepts `route`, passes it to `approach`, and takes the facing direction from the route's last element instead of the greedy `dx/dy` guess. No route found means **refused** (`refused = True`, stage `route`, "no route beside target"). **Never fall back to the greedy walker in world mode** when a tile map exists. The greedy walker stays only for the no-tile-map case (`not self.tile_px`) and pointer envs.
- `approach`'s own `on_player` refusal and the B4 underfoot rule are unchanged.

## 5. Tests (one per behaviour, existing fakes)

- `test_world.py`:
  - `leads_to` returns P for an observed relation with `sup > con`, excludes `con >= sup`, excludes `P == thing.place`, and follows merged places.
  - `here()` names the thing that leads in.
- `test_notebook.py`:
  - A thing-target quest closes `entered` on a move into a place it leads to, from `opened - 1`, including when the target is set after the move.
  - It does not close for a move into any other place.
  - The done-when and evidence wording.
- `test_loop.py`:
  - A completed interact on X with no place change adds `con` to `X leads_to P`.
  - A blocked or input-limited interact does not.
- `test_skills.py` / `test_loop.py`:
  - `interact` with a route walks it, faces the last direction and presses A.
  - With no route it is refused, sends 0 inputs and has failure stage `route`, never a greedy walk. A grid with a 3×2 solid block and its free neighbours is enough.
- `test_options.py`: the U4 note.

## 6. Order and gate (0 calls)

Each item is one change, with `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` green after it. Order: **`World.leads_to` and the contradiction, then U1, U2, U3, U4, then C2.**

- **Golden:** `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py`. Only the plan, quest, verdict, flag and focus-text fields may change; say which.
- **`eval_plan --check all`:**
  - `runs/m1_exit_b1_live_150` exits 0, and **q4 closes at S138 with reason `entered`** (target set at S138, move into `p_place_5` at S137, opened at S132).
  - `runs/m1_live_150`, `runs/m1_rcr_phase_e` and `runs/m1_rcr_post_r3` exit 0 with their B1/B2 closures unchanged: phase_e q2 S62 `crossed`, post_r3 q5 S98 `crossed`. Report any new `entered` closure on those runs with its relation, and check it isn't the shelf link.
- **`check_tracks m1_exit_b1_live_150 --menu --first 1 --dump .tmp/bl/prompts`:**
  - exits 0, duplicates 0, no-action rows 0, max act prompt ≤ 2,000;
  - `.tmp/bl/prompts/138.txt` contains `entered through large house` on the `Here:` line;
  - its goal line reads `done when: you enter large house`.
- **The C2 zigzag cannot be replayed**: offline replay doesn't re-press keys, and there is no checkpoint at S143. The unit tests above are its gate. Also grep `presses.jsonl`-shaped output in the tests for "no row sends more than 2×(route length)+2 keys".
- **Game-word grep:** `rg -ni 'oak|mom|pallet|pokemon|bedroom|kitchen|rug|door mat|lab' agent/` adds no hits.
- **Commit:** `Building link + routed interact: <one-line gate result>`. Never push. Don't commit `scripts/run_agent.py` (the user's cap edit).
- **Report** a pass/fail table and stop. Live run only with the user's OK, then `--steps 150 --max-calls 300 --decide`. Pass bar:
  - the agent enters map 40 and stays at least 3 steps on at least one entry;
  - no `interact` row sends more than 2×(route length)+2 keys;
  - q4 (or its successor) closes `entered` or by speech;
  - report which thing the `leads_to` was recorded on (the building or the door), which is the A-vs-B measurement.

## 7. Logged, not in this program

- Oak is not in the lab at this point in the game; the trigger is walking north out of town. Reaching him is the planner's and the act's job, not the harness's.
- Outdoor "explore LEFT/RIGHT … route blocked" repeats (S133, S136, S144, S150): the outdoor tile map is wrong. This is its own investigation.
- Menu label "go to DOWN, here" and "via DOWN": an exit with no name falls back to its push direction as its label.
- `leads_to` rows with a place as the subject and the same place as the object (`p_place_2 leads_to p_place_2`): noise from `go_to place:` actions. `World.leads_to` ignores non-thing subjects; the writer is left alone.

## 8. Hard rules (repeat them in every subagent prompt)

- No game words in `agent/`.
- No new steering gates.
- Fix the shared function.
- Pytest always runs with `-n auto`.
- **Never use git worktrees with symlinks or junctions to `runs/` or `Rom/`.**
- Never delete or overwrite anything in `runs/`.
- Don't change `agent/perceive.py` prompts.
- Line counts are report-only.
- Report exact command output.

## 9. Review of c82abcc (2026-10-20, coordinator)

The code is accepted. Both reported gate failures were mistakes in this plan's gate, not in the code.

| Reported fail | Verdict |
|---|---|
| S138 prompt `Goal: (none)` / `Here: place 5` | **The gate was wrong.** (1) U1 closes q4 *at* S138, the step its target is set, so the S138 act prompt correctly has no open goal; the bar contradicted U1. (2) `check_tracks --menu` rebuilds memory from perception alone. It never runs the loop's effect path, so it has no `leads_to` relations and uses its own place labels. It cannot show U3. **The replacement check passes on the run's own memory** (copied to `.tmp/blv`, 0 calls): `World.here("p_place_5")` = `room with counters, entered through large house`; a q4 targeting `thing:e_large_house` renders `(done when: you enter large house)`; `here()` costs about 0.2 ms. |
| Game-word grep, 12 hits | **The pattern was wrong.** `lab` matches `label`. Use `rg -niw 'oak\|mom\|pallet\|pokemon\|bedroom\|kitchen\|rug\|lab' agent/`. |
| q4 closes "again" at S141 in the replay | This is a replay artifact of `eval_plan` re-applying recorded quest snapshots. A live `target` op on a closed quest returns "key is not open" (notebook.py ~113). Logged, not a blocker. |

**Known limits for the live run:**
- `e_shelf_2 leads_to p_place_3` is still live. It only loses support (`con`) on a future completed interact; in this run it closes nothing.
- `World.beside` checks the 4 neighbours of the thing's single stored tile. A multi-tile building's centre neighbours can be inside its footprint, so "check large house" may end `route blocked`. That is recorded honestly and is not a zigzag.
