# Avatar harness round r17: fix r16's two sensor regressions; explore toward unvisited map edges; remember where text was heard

**Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `352aedb`. **Ledger:** 9629 / 17854; LV17 cap 400; **stop** if it would pass 10029.

**Why (LV16, `runs/m1_r16_boot`, stopped at 257):**
- R1: door presses were judged "no response" (steps 55, 57, 65, 87), so crossings went unrecorded. Pallet Town and the rival's house merged into one place, and the M1 gate failed.
- R2: animated water and flowers sit inside W1's ring, so real bumps read as "no response". No wall was learned, and the same press repeated about 15 times (steps 238–255).
- B2: "explore" valued cells never *seen*. The route north was on screen once, so "explore UP" showed 1 cell and the agent never left town.
- B3: game text had no location, so the planner could not link Oak's hint to where it was heard.

## Parts

| id | title | lane | files |
|---|---|---|---|
| R1 | A press that crosses a scene cut is never judged "no response" | A | `agent/skills.py` (`_step`), `agent/world.py` (`travel`, one line) |
| R2 | Pixels that changed on their own before a press are ignored by the reaction ring | A | `agent/observer.py` (`Settle`, `wait_settled.motion`), `agent/skills.py` (`Ctx`, `_settled`, `_step`) |
| B2 | Exploration targets include seen cells on the edge of the seen map that were never visited | B | `agent/world.py` (`unexplored`, explore label) |
| B3 | Game text records the tile where it was heard and shows it | B | `agent/loop.py` (`_learn`, `_flush_conversation`), `agent/notebook.py` (`heard`, `_text_line`) |
| T17 | Offline gates (0 asks) | orchestrator | `.tmp/r17/g/` (uncommitted) |
| LV17 | Short clean-boot live run | orchestrator | `runs/m1_r17_boot` |

The lanes are disjoint. A owns `skills.py`, `observer.py` and `world.py` `travel`; B owns `world.py` `unexplored` and the explore label, plus `loop.py` and `notebook.py`.

## Specs

### R1 (lane A)
- `agent/skills.py` `_step`, the W1 block (currently `if move == (0, 0):` at about line 58): change the condition to
  `if move == (0, 0) and not (end.blank_seen or any(_blank(f) for f in frames) or cut(before, end.frame)):`
- `agent/world.py` `travel` (about line 1340): `if result.reason == "no response":` becomes `if result.reason == "no response" and not is_cut:`
- **Test** (`tests/agent/test_skills.py`): a synthetic press whose in-press frames include one blank frame and whose end frame differs completely from `before` returns `no_response` False.
- **Approved fixture clarification (2026-10-24):** keep the R1 guard exactly as specified. In `test_wall_needs_player_tile_reaction`, replace the all-white `before` frame with `Image.linear_gradient("L").resize((160, 144)).convert("RGB")`, and use `(255, 0, 0)` instead of `(0, 0, 0)` for marked pixels. Keep the parametrize table, scale handling, `self_spot`, actor boxes and final assertion unchanged. The same background swap is approved for another existing all-white fixture only if it fails for the same uniform-fade classification reason; any other failure still requires a stop.

### R2 (lane A) — REPLACED 2026-10-24 (amendment 2)
The ambient-mask R2 (`10749bf`) is **reverted**: `git revert --no-edit 10749bf` on `feat/lean-agent`.
Measured on r13–r15 replays plus r16's water, with 0 asks. A wall kept on a press where truth shows a text box is a false wall:

| rule | real walls kept | false walls kept | r16 water (1st / 2nd LEFT) |
|---|---|---|---|
| W1 + R1 only (no ambient) | 148/156 | 0/25 | no response / no response |
| R2 as specced (`10749bf`) | 148/156 | 5/25 | no response / — |
| ambient = pixels changed ≥ 2× in the settle | 148/156 | 2/25 | no response / bump |
| self tile only, no ring | 152/156 | 7/25 | bump / bump |

**Cause:** Pokémon Red animates water and flowers about every 21 frames, but the settle before a press ends after 24 quiet frames, so it rarely sees a full cycle. Any pixel rule either misses the animation or lets "!" bubbles and text events through. So W1 stays exactly as it is, and the loop is broken by **re-checking on a later step**.

**New R2: a press with no response from the same tile, in the same direction, on a later step is recorded as a wall.** W3 treats it as unconfirmed, so it can be re-tested.
- `agent/space.py` `fold`, at the top of the loop body, right after `direction, shift = ...` and **before** `if direction not in DIRS or shift is None: continue`, add:
  ```python
  if isinstance(move, dict) and move.get("no_response") and direction in DIRS:  # a press that changed nothing at the player
      row = _get(mem, place, here) or _new(place, here, step); old = {**row, "silent": dict(row.get("silent", {}))}
      if step <= row.get("silent", {}).get(direction, step): row.setdefault("silent", {})[direction] = step; _save(mem, row, old); continue
      shift = (0, 0)  # silent on an earlier step too: a bump (unconfirmed until re-bumped, W3)
  ```
  Execution then falls through to the existing actor/`held` and bump logic unchanged, so an actor tile still gives `held`.
- Add `**({"silent": dict(row["silent"])} if "silent" in row else {})` to the existing `before = dict(row, ...)` copy.
- On a successful move in that direction, also `row.get("silent", {}).pop(direction, None)`, next to the `walled` pop.
- `# ponytail:` note: "two separate steps of no response at one tile/direction = wall; a scripted freeze spanning two steps on a world screen makes an unconfirmed wall that W3 re-tests."
- **Why it is safe:** W2 stops a walk at its first no-response press, so a step marks at most one direction silent. The r13–r15 false clusters each sat inside one step's dialogue freeze.
- **Tests (`test_space.py`):**
  - one no-response move at step 1 leaves `blocked` empty and `silent == {"LEFT": 1}`;
  - the same at step 2 gives `blocked == ["LEFT"]`, not in `firm`;
  - two in the same step leave it unblocked;
  - a later successful LEFT move clears `blocked`, `walled` and `silent`.
- **Lane A also reverts R2's test edits** (the revert does this). Keep R1's fixture changes.

### B2 (lane B)
- `agent/world.py` `unexplored(mem, pid)` (about line 68): also return `edge` cells, where
  - `seen` = that place's seen cells; candidates are only seen cells that are also in that place's seen-row `open` list (cells once seen past a drawn, non-void screen edge, i.e. real ground). **Amended 2026-10-24:** using every seen cell put explore rows toward the black void around every indoor room (Oak's lab went from 0 to 4 explore directions on LV16 memory) and broke the dead-end note;
  - `known` = every tiles-row cell of the place, plus every cell a `blocked` direction of a tiles row points into;
  - `edge = {c in seen ∩ open, not in known, with at least one 4-neighbour not in seen}`.
- Return `<existing set> | edge`. `explore_dirs` and everything downstream stay unchanged.
- `# ponytail:` note: "edge cells may be walls; each costs at most one bump before `known` excludes it."
- Explore label (about line 1242): change `unseen cells that way` to `unexplored cells that way`.
- **Pre-approved assertion changes:**
  - `tests/agent/test_options.py:412` label string;
  - `scripts/points.json:61` regex (`unseen` becomes `unexplored`).
- **Test** (`test_world.py`): a place whose seen block (also listed in `open`) extends 3 rows past its walked tiles yields an "UP" explore target in that block. The same block without `open` yields none. A cell next to a blocked side is not a target. `test_untried_edge_into_a_seen_cell_is_not_unexplored_so_the_dead_end_fires` must pass **unchanged**.

### B3 (lane B)
- **`agent/loop.py`:**
  - In `_learn`, the conversation start `self.conversation.update(place=place, screen=screen)` gets `at=list(self.world.pos)`.
  - The world-screen `self.notebook.heard(step, None, ...)` call gets `at=list(self.world.pos)`.
  - `_flush_conversation`'s `heard(...)` call gets `at=self.conversation.get("at")`.
- **`agent/notebook.py`:**
  - `heard(..., who=None, at=None)` stores `"at": at` only when `at` is not None.
  - `_text_line` renders `at {where} ({x},{y})` when the row has `at`, and is unchanged otherwise.
- **Test** (`test_notebook.py` / `test_loop.py`): a conversation started at pos (5,-6) and flushed after the position changes is stored with `at == [5, -6]`, and its rendered line contains `(5,-6)`.

## T17 gates (orchestrator, merged HEAD, 0 asks, outputs in `.tmp/r17/g/`)
- **Suite:** `pytest -n auto -q` passes. The golden act trace must not change; never set W0_REGEN.
- **T17-W1:** re-run `.tmp/r17/g/validate2.py` (real `_step`) on r13, r14 and r15 after the R2 revert. Pass: false walls kept 0/25, real walls kept ≥ 148/156. Never run two validators at once: they share a staging directory.
- **T17-R2 (amended):** on r16 `checkpoint/000245` (RAM pos (8,14)), use a fresh `Ctx(env, None, 16, 16, self_spot=(0.45, 0.4513888888888889))` per simulated step. Call the real `_step(ctx, "LEFT", before)` and then `agent.space.fold(mem, "p_place_4", (3, 8), end_moves, 16, step)` on a temp `Memory`, where `end_moves = [{"dir": "LEFT", "shift": move, **({"no_response": True} if no_response else {})}]`, for steps 245 and then 246. Pass: after step 246, `mem.get("tiles", "p_place_4@3,8")["blocked"]` contains LEFT. Either press may be a bump or a no-response.
- **T17-R1:** the unit test above, plus: replay r16 `checkpoint/000060` with presses 61–64 from `presses.jsonl`, then each key of step 65 through the real `_step`. Pass: no press that changes the RAM map is `no_response`.
- **T17-B2:** on a copy of `runs/m1_r16_boot/memory`, run `World(mem,16)` with `size=(160,144)`, `current_place="p_place_4"`, `pos=(8,-4)`, then `explore_dirs("p_place_4")`. Pass: it has "UP", with target y ≤ -9 (prototype: `UP: 8 steps, 3 cells, to (5,-9)`). Also on that copy, `current_place="p_place_6"`, `pos=(0,-2)`: no more than 2 explore directions (prototype: LEFT and RIGHT, 1 cell each).

## LV17 (orchestrator, once, after T17 passes)
- Make sure the viewer is up: `curl -s http://127.0.0.1:8765/state`. If not, start `python scripts/watch_run.py --no-browser` in the background.
- Run: `.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r17_boot --steps 150 --max-calls 400`
- **Glance pass** (from `runs/m1_r17_boot` `truth.jsonl` and `trace.jsonl`; truth row N+1 is the state after trace row N):
  1. Every trace row whose skill reason contains "no response" has truth map[N+1] == map[N]: no missed door crossings.
  2. `scripts/eval_run.py` `place_purity` is 1.0 for every map reached.
  3. No run of ≥ 5 consecutive rows with the same place and tile whose skill reason contains "no response".
  4. Report the step the starter was received (`steps_to_starter`), or "not reached in 150".

## Lane blocks (paste verbatim to each subagent; fill `<WORKTREE>`)

**Shared rules (both lanes):**
- Ponytail: reuse > stdlib > minimal code, in the dense one-line style. Run `codegraph explore "<symbols>"` before grep or Read.
- Python is `C:\Users\Abj1\Documents\GitHub\Avatar\.venv\Scripts\python.exe` with `PYTHONIOENCODING=utf-8`. Run pytest only as `-n auto -q`.
- No game words and no RAM reads in `agent/`; no new modules, gates or hidden rows. Zero model requests. Never run `scripts/run_agent.py`.
- Never touch: `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `runs/` (read-only), `docs/plans/`. No junctions or symlinks.
- Git: one commit per item, titled `<id> <title from the Parts table>`. Stage explicit paths. Never push, rebase, amend or switch branches. Never set W0_REGEN.
- Change only the pre-approved assertions. If another assertion fails, stop and report the test, before/after, and why.
- Report in under 25 lines: per item the sha, net lines per file, and suite counts before/after.

**Lane A:**
```
You are lane A of Avatar r17. Work ONLY in worktree <WORKTREE> (branch r17/A from 352aedb). Read docs/plans/harness-r17.md sections "R1" and "R2" and implement them exactly, R1 then R2, one commit each, with the listed tests. Your files: agent/skills.py, agent/observer.py, agent/world.py (ONLY the one travel line), tests/agent/test_skills.py, tests/agent/test_observer.py. Shared rules from the plan's "Lane blocks" section apply.
```

**Lane B:**
```
You are lane B of Avatar r17. Work ONLY in worktree <WORKTREE> (branch r17/B from 352aedb). Read docs/plans/harness-r17.md sections "B2" and "B3" and implement them exactly, B2 then B3, one commit each, with the listed tests and the pre-approved assertion changes. Your files: agent/world.py (ONLY unexplored and the explore label line), agent/loop.py, agent/notebook.py, tests/agent/test_world.py, test_options.py, test_notebook.py, test_loop.py, scripts/points.json (one regex). Shared rules from the plan's "Lane blocks" section apply.
```

## Orchestrator steps
1. Commit this plan as `docs: r17 plan`. Baseline: `pytest -n auto -q`; expect 852 passed, 1 skipped.
2. Create worktrees `.worktrees/r17-A` and `.worktrees/r17-B` with `git worktree add -b r17/A .worktrees/r17-A HEAD`, and the same for B. Run lanes A and B in parallel.
3. Merge `r17/A` then `r17/B` into `feat/lean-agent` with `--no-ff`. Run the suite.
4. Run the T17 gates. On a failure, give the owning lane one fix attempt, then stop and report.
5. Run LV17 once and score the glance pass.
6. Remove the worktrees with `git worktree remove` (never `--force` on a path holding runs/ or Rom/).

**Stop and ask when:**
- the baseline differs;
- a spec is ambiguous;
- an assertion outside the pre-approved list fails;
- a gate fails after one fix attempt;
- the ledger would pass 10029.

**Final report (under 40 lines):**
- the commit shas;
- suite counts;
- T17 numbers;
- the LV17 glance-pass results (criteria 1–4);
- `asks used`, and the ledger before and after;
- anything skipped and why.
