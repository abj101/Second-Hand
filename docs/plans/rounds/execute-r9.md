# Executor brief: Avatar harness round r9 (IB3 + clean-boot live run)

You are a **Sonnet 5.5 (medium effort)** session. You **orchestrate Haiku 5.5 (medium effort) subagents** for the mechanical work, and you do the judgment yourself.

- **Repo:** `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `ed20630`.
- **Plan:** execute `docs/plans/harness-r9.md`. Read it completely first: the evidence, the IB3 spec, the IB3 measure, the go rule and the order.
- **Precedence:** where this brief and the plan differ, this brief wins.
- **Planner:** the linked planner session wrote this. Report to it, or stop and ask it, through `SendMessage` (its id is in your "Linked sessions" note).

## Standing rules (repeat them in every subagent prompt)

1. **Project tooling** (from `CLAUDE.md`, mandatory for you and every subagent):
   - the `ponytail` skill (full): reuse > stdlib > minimal code;
   - `codegraph_explore` before grep or Read;
   - `headroom_compress` on large outputs.
2. **Smallest change, reuse first.** No new modules in `agent/`, no refactors, no new gates, no removed options, and no game names in `agent/` code or prompts. Match the dense one-line style of the touched code.
3. **Files you may edit:**
   - `agent/memory.py` (IB3a)
   - `agent/track.py` (IB3b, IB3c)
   - `tests/agent/test_memory.py`, `tests/agent/test_track.py`
   - `agent/world.py`: only if IB3c cannot be done inside `track.py`; say why in the report.

   **Never touch** `scripts/run_agent.py`, `scripts/eval_frames.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, or anything under `docs/plans/`. Do not stage, stash, reset, checkout or format them. Do not switch branches.
4. **Assertions.** Baseline: **735 passed, 1 skipped**. **No existing assertion may change.** New tests are fine. Fixture-input repairs that keep every assertion unchanged are allowed only after you message the planner and it approves. Any other failing existing assertion: stop that part, revert it, and report.
5. **Run data.**
   - Write only `runs/m1_r9_boot` (created by `run_agent.py`) and the ledger entries that `_ledger` writes itself.
   - Never edit other runs or `runs/e3_budget.json`.
   - Scratch goes to `.tmp/r9/` (`track_hits.py` is already there) and is never committed.
   - Never use junctions or links for `runs/` or `Rom/`, and work in this checkout, not a worktree.
6. **Commits.**
   - One commit per part: `IB3a …`, `IB3b …`, `IB3c …`, each with the title from the plan's IB3 spec plus a one-line body.
   - End each message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
   - Stage explicit paths only; never `git add -A`, `git add .` or `git commit -a`.
   - Never push.
7. **Interpreter and tests.**
   - Interpreter: `.venv/Scripts/python.exe`. Set `PYTHONIOENCODING=utf-8`.
   - Suite: `-m pytest -n auto -q`, never serial.
   - Run anything over about 2 minutes in the background.
8. **Model requests.**
   - Everything before the live run is 0 asks and must print `requests=0` or leave the ledger unchanged.
   - The live run is capped at `--max-calls 400`.
   - Ledger now: 6751 / 7854.
9. **Stop and report** if the code at a cited line differs from the plan's validity check. The live run is a single attempt: if it stops early, keep the data and report.

## Worker split

**Haiku 5.5 (medium) subagents: mechanical only.** Give each one a self-contained prompt with the standing rules and exact commands.

1. **Baseline measures (step 1).** On HEAD:
   - `scripts/check_tracks.py <run> --first 116 --steps 150` for `m1_r6_live_150`, `m1_r7_resume115` and `m1_r8_resume115`;
   - `.tmp/r9/track_hits.py <run> 116 150` for the same three runs.
2. **The `--no-reload` flag** on `.tmp/r9/track_hits.py` (scratch only):
   - load only the first checkpoint (`first-1`), then replay the recorded keys;
   - for each step, add the share of live character tracks within 4 px of a RAM sprite;
   - reuse `scripts/check_tracks.replay`'s logic by copying it into the scratch file, not by editing `scripts/check_tracks.py`;
   - print the divergence count, as `check_tracks` does.
3. **After each IB3 part:**
   - the suite;
   - the same measures (check_tracks plus the no-reload replay, three runs);
   - return the numbers as a table.
4. **A3:** `scripts/eval_act.py --om points --dry`. Return the table: PASS / FAIL / INCONCLUSIVE per case.
5. **Live run and evals.** Run these only after you call go:
   - check the viewer is up (`curl -s http://127.0.0.1:8765/state`); if it is down, start `python scripts/watch_run.py --no-browser` in the background;
   - `.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r9_boot --steps 150 --max-calls 400 --decide`, in the background;
   - then `scripts/eval_run.py runs/m1_r9_boot`, `scripts/eval_moves.py m1_r9_boot` and `scripts/eval_entities.py m1_r9_boot`;
   - `check_tracks.py m1_r9_boot --first <first lab step> --steps 150`, if the run reached the lab.

**You (Sonnet): judgment.**
- Write the three code parts and their tests yourself; each is a few lines.
- Review every worker result, and make the commits.
- Decide the revert-if-worse and the go.
- Handle the stops, and write the report.

Never let two writers touch one file.

## The parts (summary; the plan has the detail)

- **IB3a** (`agent/memory.py:284-286`): the character gate is `min(4, 3 + max(0, step - row.get("last", step)) // 4)`.
  - Test: a character perceived 2 tiles from its row, seen at the last step, keeps its id.
  - Test: two characters 1 tile apart, both perceived, keep their own ids.
- **IB3b** (`agent/track.py` `seed`, restart branch, actors only, only when `phase` is known): seed on the first non-background cell among the perceived cell and its 8 grid neighbours. "Background" means the 16×16 patch repeats at another cell of the same grid on this frame. If all 9 are background, seed at the perceived cell. Add the `ponytail:` comment from the plan.
  - Test: a synthetic frame with a repeating floor and one sprite one tile right of the perceived point; the track is seeded on the sprite.
  - Test: with no phase, the raw point is kept (the existing test already covers this).
- **IB3c** (`agent/track.py` `seed`): an actor id with no live track is not seeded when another id's live actor track lies within `tile_px / 2` of the chosen cell.
  - Test: seed "a" on a sprite, then seed "b" at the same point; only "a" has a track.

**Revert-if-worse:** if IB3b or IB3c raises r8 switches (in the no-reload replay) above the previous part's number, revert that part and continue.

**Go** (before the live run):
- r8 switches ≤ 8 in the no-reload replay;
- no run worse than at HEAD;
- the share of tracks on a sprite ≥ 80% after IB3b.

On no-go: report to the planner and **do not** start the live run.

## Report (reply to the planner, exactly this shape)

**Summary (3 lines):** whether the boot run got the starter (truth party step), the go verdict, and the suite.

| part | commit | suite (passed / skipped / failed) | r6 / r7 / r8 switches (check_tracks) | r8 switches (no-reload) | tracks on sprite | hits / perceptions | notes |
|---|---|---|---|---|---|---|---|
| HEAD | `ed20630` | 735 / 1 / 0 | | | | | |
| IB3a | | | | | | | |
| IB3b | | | | | | | |
| IB3c | | | | | | | |

Then:
1. A3 table.
2. **Boot run:**
   - first party step (truth);
   - `m1_gate_pass`;
   - places reached and the step each was first entered;
   - asks;
   - seconds per step;
   - lab `check_tracks` switches;
   - `eval_entities` metrics;
   - failed-row classes from `eval_moves`.
3. Ledger before and after.
4. Then list:
   - (a) files edited;
   - (b) commands that made model requests, with counts;
   - (c) reverted parts or skipped steps;
   - (d) surprises.

State failures plainly.
