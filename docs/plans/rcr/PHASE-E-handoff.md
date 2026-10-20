# RCR Phase E: handoff for external execution

Written 2026-10-19 by the RCR coordinator. The planning sessions stopped here; the remaining fixes are to be executed elsewhere (ChatGPT). Read [MASTER.md](MASTER.md) §8, "Phase D (10-19)", for the evidence.

## Where things stand

- **Branch:** `feat/lean-agent`.
- **Last gate commit:** `3db80df`, the R3.7 replay. Phase E work in progress is committed on top of it as one WIP commit (`RCR Phase E WIP`, full suite 629 passed, 1 skipped). See §3 for which items are done.
- **Evidence run:** `runs/m1_rcr_post_r3`, a clean boot of 150 steps on 3db80df. It reached Pallet at step 98, bounced between the house and Pallet, then sat in a 7-step no-op loop at the end.
- **Ledger:** `runs/e3_budget.json` stands at **4436 / 4815**, so 379 calls are left. Treat this as the budget for one confirmation run (150 steps, `--max-calls 300`).
- **Viewer:** `python scripts/watch_run.py` serves 127.0.0.1:8765.

## Standing rules (from MASTER and the user)

- **No game-specific code.** No game words in `agent/`. Check with `rg -ni 'oak|mom|pallet|pokemon' agent/`.
- **No new steering gates.** Fixes change what counts as evidence; they don't add refusals.
- **Root fixes only.** Fix the shared function every caller goes through.
- **Line counts are report-only.**
- **Tests:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto`.
- **Never use git worktrees with symlinks or junctions to `runs/` or `Rom/`.** On 10-16 this deleted both.
- **Commit at gates, never push.**

## The four root causes and their state

### 1. A picked option that does nothing becomes a dead loop (owners R4 + R1)

- **Evidence:** step 144 picked "go to house, not on screen". The action was `{"go_to": "thing:e_house"}`, the choice was None, 0 inputs were sent, and the step scored `unobservable`. Nothing was recorded, so the prompt stayed identical and the cached act reply repeated it for 7 steps.
- **R4 part, done (uncommitted at handoff, in agent/loop.py):**
  - `idle` = a picked option that sent no input and reported done. It scores `failed` and records `fails[...]["why"] = "did nothing"`.
  - Rows store `pick_key`, so the failure lands under the option's own intent key.
- **R1 part, partly done (agent/world.py):**
  - `World.reachable(ref, per, place)` runs the same resolve and plan the loop runs.
  - Off-screen groups offer only a reachable member.
  - **Still open:** confirm why `thing:e_house` did not resolve or route at step 144, and fix that link if it is a bug rather than only hiding the row. Then check that every row offered at step 144 resolves to an executable action.

### 2. Quests close the moment they are created (owner R2)

- **Evidence:**
  - q2-q4 and q6 were targeted at the place the agent was already in.
  - q5 was targeted at a door mat it walked over.
  - `observe` closed each of them at once, so 5 of the 7 closures were empty.
- **Root:** closure checked a state (here == target) instead of an event that happened after the target was set.
- **Done:** `agent/notebook.py` `observe(step, place)`.
  - A place quest closes only on arrival: the previous place (`data["at"]`) is not the target, the current place is the target, and this happens at or after the target-set step.
  - A thing quest closes only when game text is heard with `who == target` at or after the target-set step.
  - In `agent/loop.py`, `talked` is removed. `conversation["who"]` is cleared when the agent is back on the world screen with no dialogue.
  - `render_focus` done-when text now reads "you enter X" or "X speaks to you".
- **Tests and eval_plan:** see §3 for status.

### 3. Planner calls wasted on the intro (owner R4)

- **Evidence:** 17 plan calls on steps 1-49, every one an `add` error "why must cite game-text keys, none". The intro speech became citable only at step 53.
- **Done (agent/loop.py):**
  - `t5_armed`: the "no open quest" trigger fires once, and is re-armed only by a new T1, T2 or T3 event. It is persisted in state.
  - A conversation now flushes when its screen kind changes (dialogue to menu), not only on the world screen, so intro dialogue reaches `notebook.heard` early.
- **Acceptance still to measure:** on the next run, plan calls on the intro steps must be no more than the number of new-text events, and none may end with "none heard yet".

### 4. Outdoor act prompt too large: 3,008 user / 3,277 total chars at step 144, 41 rows (owner R3, plus R1's memory.py)

There are three causes. Status per item is in §3 and R3-decide.md §9.

- **(a) A UI element stored as a world thing.**
  - `memory.normalize_kind` (agent/memory.py:17) doesn't fold "dialogue box" to `dialogue_box`, or "ui" to `ui_element`, so the TRANSIENT filter never matches.
  - Fix: fold whitespace to `_` and map `ui` to `ui_element`.
- **(b) Walls listed as exits.**
  - The same-tile join in `Memory.observe` ("one static thing per tile") merges a stray "exit: path" perception into the wall or hedge record on that tile. `_is_exit` (world.py:44) then matches on that record's kinds and aliases.
  - Fix: the join never merges an exit perception into a non-exit record, or the reverse. Add a regression test.
- **(c) 17 off-screen things with no history.**
  - **The user approved:** an off-screen thing gets no row if it is static (not an actor, not an exit), has no history, and is not the current quest target. This supersedes R3's "never hide an option" rule for that case only.
  - Expected result at step 144: about 1,962 chars.
- **Deferred:** "exit: water" is the perceiver's own label. Changing `agent/perceive.py` would invalidate the perception cache, so it is not part of Phase E.

## §3 Status at handoff

| Item | State | Where |
|---|---|---|
| 1-R4: a picked option with 0 inputs is recorded as failed | **done** | loop.py `_settle`, `idle` / `pick_key` |
| 1-R1: `World.reachable`; off-screen rows only if executable | **done** | world.py `reachable`, `options` far_groups |
| 1: why step 144 failed | **explained** | `thing:e_house` resolves, but no walkable tile beside it is known, so `World.beside` is None and `plan` is None. Not a resolve bug |
| 2: quests close only on events | **done** | notebook.py `observe`/`heard` (`data["here"]`), eval_plan replay, tests |
| 3: T5 once per period; intro text flushed early | **done** | loop.py `t5_armed`, `_learn` flush |
| 4a: UI kinds folded | **done** | memory.py `normalize_kind` |
| 4b: same-tile join keeps exits separate | **done** | memory.py `Memory.observe` join paths |
| 4c: off-screen rule (user-approved) | **done in world.py; loop wiring OPEN** | see open item A |
| Golden | regenerated; only the `plan` field changed | tests/agent/data/w0_golden/act/trace.jsonl |
| Offline rebuild of m1_rcr_post_r3 | max act prompt **1,541** (S144 1,546, live was 3,277), rows max 18, 0 duplicates, 0 wall exits, 0 dialogue-box rows | `check_tracks.py m1_rcr_post_r3 --menu --first 1` |
| `eval_plan --check all` on post_r3 and baseline | exit 0. Closures: q2 S70, q3/q4 S99 (a real leave and re-enter), q5 stays open, q6 S102 | |

## Open items for the executor (do these, in order)

**A. Pass the quest target to the menu** (agent/loop.py, the `self.world.options(...)` call near line 159). Add:
`targets=tuple(t for t in [next((q.get("target") for q in self.notebook.data["quests"] if q.get("status") == "open" and q.get("target")), None)] if t)`
Add one loop test: an off-screen, history-less static quest target is still offered.

**B. Prove the step-144 dead loop is closed.**
- At step 144, a `go_to` whose `World.plan` is None set `action = None` at loop.py ~176, and nothing was recorded.
- R4's `idle` path should now record it under `pick_key` with why "did nothing".
- Add a loop test: a picked `go_to` row whose plan is None gives verdict `failed` and a `fails[pick_key]` entry, and the next menu shows that row under "Failed here".
- If the test fails, record the failure at loop.py ~176 with stage "route" and reason "no known route", like a blocked route. Do not hide the row.

**C. Known gap, leave unless cheap.** `reachable` only checks off-screen rows. On-screen and exit rows are not reachability-checked; B's failure recording covers them.

**D. Gate, commit, confirmation run, close.** See "What to run" below.

**References:**
- R3-decide.md §9 "Handover for an outside executor": exact roots, file:line and acceptance commands for items 4a-4c and 1.
- R2-quests.md §9: the closure design.
- R4-feedback.md §9: A, B1 and B2.

## What to run, in order

1. Finish the open items in §3. Run the full suite until it is green.
2. Golden: `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py`. Only plan, quest, verdict and flag fields may change.
3. Offline checks:
   - `<py> scripts/eval_plan.py --run runs/m1_rcr_post_r3 --check all`: 0 same-step closures, and q5 does not close.
   - `<py> scripts/check_tracks.py m1_rcr_post_r3 --menu --first 1`: dup 0, max act prompt ≤ 2,000, and every row executable.
4. Commit as "RCR Phase E: <one-line gate result>".
5. One confirmation run:
   - `<py> scripts/run_agent.py --run runs/m1_rcr_phase_e --steps 150 --max-calls 300 --decide`
   - Then `<py> scripts/eval_run.py runs/m1_rcr_phase_e`.
   - Compare against the scorecard of `runs/m1_rcr_post_r3` in MASTER §8.
6. Close RCR. Judge it against the six problems in `docs/plans/audit-2026-10-15.md`. Anything further starts as a new program, not another amendment round.

## Known open items, not in Phase E

- **S1 live:** the Pallet NPC got 4 ids. Outdoor walking NPCs are not covered by the tracker. Logged, not chased.
- **F4:** a re-entry can land 1 tile off at a warp exit; it self-corrects at the next view match or crossing.
- **F1:** live settle near water costs 2-8 s per press.
