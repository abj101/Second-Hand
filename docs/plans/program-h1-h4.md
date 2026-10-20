# Program: four harness bugs from `runs/m1_building_live_150` (H1-H4)

Written 2026-10-20. This is a new program. The evidence is in [program-building-link.md](program-building-link.md) §10. Read [rcr/PHASE-E-handoff.md](rcr/PHASE-E-handoff.md) §1, §2 and §5 (architecture, design rules D1-D9, offline playbook) first. **There is no live run in this program**: the ledger has 75 calls left under the user's uncommitted cap, and a run needs about 230.

Do these in this order, one change and one measurement each (D9): **H3, H1, H2, H4**. H3 is a one-liner that makes H2's evidence readable.

## H3. The "Things here:" header names the wrong place

- **Evidence:** in the planner prompts from S134 onward, `Things here: tiled room` appears while the agent is in the lab (`p_place_5`, "hallway").
- **Root:** in `World.view` (agent/world.py, near the `for other in others` / `pid = other["id"]` loop at about line 924), `label = self.place_label(pid)` overwrites the current place's `label`. Line ~965 then prints the last other place's name.
- **Fix:** rename the loop's variable. Nothing else changes.
- **Test:** a view with two places shows `Things here: <current place>`.
- **Gate:** `eval_plan --run runs/m1_building_live_150 --check size` (it renders the planner prompts) shows no `Things here:` header naming a place other than the step's own place. Add that assertion to the check, or grep the dumped prompts.

## H1. Drift correction runs on dialogue screens

- **Evidence:** the agent's tile disagrees with RAM truth on exactly two steps, S82 (`drift_fix [1,1]`) and S97 (`[-1,1]`). Both are `advance` steps on a dialogue screen right after a talk.
  - Every routed walk in the run matches the truth delta exactly.
  - After the two shifts, `eval_run --health` tile_accuracy is 0.41 (earlier runs: 0.97-1.0), and 28 interacts end in `route blocked`.
- **Root:** `World.observe_after` (world.py ~436-456) runs the entity-median drift correction on every screen. A few lines below, the view-closure block already runs only when `per.get("screen") == "world" and not modal`, where `modal` means any entity of kind dialogue_box, menu, keyboard or input_field.
- **Fix:** compute that one condition once, near the top of the `not reused` handling, and use it for **both** the drift block and the closure block. On a modal or non-world screen:
  - the drift block neither applies a fix nor stores a pending `self.drift` (otherwise a dialogue frame's offset confirms a world frame's);
  - `self.drift` and `self.drift_ref` are left untouched.
- **Tests (`tests/agent/test_world.py`):**
  - Two consecutive observations with the same entity offset: on world screens they apply the drift (existing behaviour); with the second one on a dialogue screen, `drift_fix` is None and `pos` is unchanged.
  - A dialogue frame between two world frames does not break a real world-screen correction.
- **Gate:** the offline replay can't re-run positions (`check_tracks` sets `world.pos` from truth). The unit tests are the gate. Add one check to `scripts/eval_run.py` health: `drift_fix_off_world` = the count of rows with a non-null `drift_fix` whose `screen != "world"`. It must read 2 on `m1_building_live_150` (S82 and S97) and 0 on the next live run.

## H2. The planner targets map-legend handles that the resolver rejects

- **Evidence:** the planner view's legend lists things as `Thing C counter with three spheres (2,-6) …` and `Landmark J …`. The planner answers with targets like `"Thing C"`, `"Thing B"`, `"B"` or `"J"`, and each is rejected with `matches no known place or thing`.
  - In this run that happened at S99, S102, S134 and S146 (5 of 8 lab plan calls failed, leaving no open quest from S92 to S150).
  - It also happened in m1_exit_b1_live_150 at S135 ("J") and in phase_e at S59 ("B").
- **Root:** two vocabularies. The view hands the planner letter handles; `World.ref` (world.py ~557) only knows names, ids and refs. **Anything shown to the planner must resolve.**
- **Fix:**
  - `World.view` keeps the legend it just rendered as `self.legend = {symbol: ref}`. Use the ref of the legend line's thing; for an exit line, use the exit thing's ref.
  - `World.ref(text)` resolves a legend handle when `text`, stripped, matches `^(?:(?:thing|landmark)\s+)?(\S)$` case-insensitively on the prefix and exactly on the symbol, and that symbol is in `self.legend`.
  - Exact name, id and ref matches keep precedence, so a thing really named "B" still wins.
  - The legend is the one from the planner prompt just answered: plan ops are applied right after that prompt's reply, with no re-render in between.
  - Persist `legend` in `World.state()` / restore so a checkpoint-resumed plan reply still resolves.
  - Don't change the prompt text.
- **Tests (`tests/agent/test_world.py`, `test_notebook.py`):**
  - After a view renders, `ref("Thing C")`, `ref("C")` and `ref("landmark J")` return the legend's refs.
  - An unknown symbol returns None and is listed in the error like any other miss.
  - A thing named "B" wins over legend "B".
  - Notebook `add` with `target: "Thing C"` succeeds.
- **Gate:** `eval_plan --run runs/m1_building_live_150 --check all` exits 0. The recorded ops at S99, S102, S134 and S146 now resolve, which needs `eval_plan` to render the view for the step before applying its ops; it already builds the prompt for `--check size`. Each must resolve to the thing its legend line named at that step: report the ref and name per step, and a coordinator checks them against the dumped prompt.
  - Same on `m1_exit_b1_live_150` (S135 "J") and `m1_rcr_phase_e` (S59 "B").
  - The other closures stay unchanged: phase_e q2 S62 `crossed`, post_r3 q5 S98 `crossed`, exit_b1 q4 S138 `entered`.

## H4. Interact success is read from a whole-frame pixel diff, which walking NPCs spoil

- **Evidence:** at S145-S149, "check counter 5" (2 keys: a face bump, then A) was scored `changed` five times with nothing happening, while the aides walked in view. No failure was recorded, so the act repeated it; this contributes to `repeat_actions_max_length=13`.
- **Root:** `skills.interact` (skills.py ~171) returns `done` when `changed(start.frame, end.frame)` is truthy, and `observer.changed` returns the *fraction* of pixels that differ. That compares the whole step (the walk, the face turn and every moving actor), not the effect of the A press.
- **Fix:**
  1. `observer.changed(a, b, thresh=16, mask=())`: `mask` is a list of pixel boxes `(x0, y0, x1, y1)` excluded from the comparison.
  2. `interact` judges **the A press only**: the settled frame just before A against the settled frame after it. It passes `mask` = boxes of live **actor** tracks other than the player, one tile square around each track's position in either frame.
     - Get the boxes through one new optional `Ctx` field (e.g. `ctx.actor_boxes`, a callable returning boxes), filled by `Loop` from `World.tracker.tracks` and `self_spot`.
     - With no tracker (the default), `mask=()` and behaviour is unchanged.
  3. Any change left after masking means `done`; none means `failed "nothing changed"`, which `_settle` already records under the intent key.
  - Leave `predict.effects`' own `changed` call alone. It is logged for later and not part of this change.
- **Tests:**
  - `test_observer.py`: a masked box excludes its pixels.
  - `test_skills.py`:
    - with a fake env where only an actor patch moves between before-A and after-A, interact is `failed "nothing changed"`;
    - where a text box appears, it is `done`;
    - where the face step turns the player but A shows nothing, it is `failed`.
- **Offline check:** for S146-S149 (2 keys each, no walk), compute `changed(frames/NNNNNN.png, frames/NNNNNN_after.png, mask=…)` from `runs/m1_building_live_150`. Build the mask from that row's non-player actor `entities` positions (tile boxes, screen fractions × 160×144). It must be 0 for S146-S149 and > 0 for the dialogue openings at S123 and S126. Report the four values.

## Gate summary (0 calls)

- After each fix: `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` is green.
- Golden: `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py`. Only the plan, quest, verdict and flag fields may change; say which.
- `eval_plan --check all` exits 0 on `m1_building_live_150`, `m1_exit_b1_live_150`, `m1_live_150`, `m1_rcr_phase_e` and `m1_rcr_post_r3`, with the H2 resolutions and unchanged closures as above.
- `check_tracks m1_building_live_150 --menu --first 1` exits 0: duplicates 0, no-action rows 0, max act prompt ≤ 2,000.
- `eval_run --health` shows the new `drift_fix_off_world` field: 2 on `m1_building_live_150`.
- The H4 offline frame check is reported.
- Word grep: `rg -niw 'oak|mom|pallet|pokemon|bedroom|kitchen|rug|lab' agent/` adds no hits. Use `-w`: `lab` must not match `label`.
- Commit `H1-H4: <one-line gate result>`. Never push. Don't commit `scripts/run_agent.py`.
- Report a pass/fail table and stop. A live run needs the user's cap decision; its pass bar is:
  - `drift_fix_off_world` 0;
  - tile_accuracy ≥ 0.95;
  - no plan op error that names a legend handle;
  - no `interact` scored `changed`/`done` 3 times in a row on the same intent with no new text.

## Logged, not in this program

- `predict.effects` uses the same unmasked whole-frame `changed() > 0` for every verdict (81 of 150 steps were "changed").
- An unnamed exit falls back to its direction as its label ("go to DOWN").
- `cut_self_loops=10` has held across five runs.
- Oak is triggered by walking north out of town; that is game logic, not the harness.

## Hard rules (repeat them in every subagent prompt)

- No game words in `agent/`.
- No new steering gates.
- Fix the shared function.
- Pytest always runs with `-n auto`.
- **Never use git worktrees with symlinks or junctions to `runs/` or `Rom/`.**
- Never delete or overwrite anything in `runs/`.
- Don't change `agent/perceive.py` prompts.
- Line counts are report-only.
- Report exact command output.
