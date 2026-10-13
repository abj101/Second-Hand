# Handoff notes

Last written by: the Sonnet orchestrator, 2026-10-12. P4 gate passed; next phase W4, then L1.

## State of the code
- Branch feat/lean-agent. P4 deleted the gated path: the act path is the only path (`Loop(env, model, mem, game)`, no `act_mode`, no `--act`).
- Kept from P3b: `World.fix_player` (after both `_see` calls in `Loop.step`), the cut rule in `Memory.match_place`/`World.name`, the `recent:` act flag (inline in `Loop.step`).
- `agent/decide.py` now holds only `Choice` and `resolve_target`; `agent/tasks.py` only `Stuck`. `judge.py`, `decider_worker.py`, `compare_chooser.py`, `Tasks`, `space.describe` are gone.
- Trace rows keep `"task": None` (run_agent prints it); `wake` and `task_budget_exhausted` keys are gone; `"view_ids": []` stays.

## Decisions in force
- Build-first (user, 2026-10-12): no live "better than gated?" comparisons; gates are unit tests plus offline checks against RAM truth. The only live spend left is L1.
- L1: two halves of 100 steps, <= 110 requests each, fresh boot into `runs/m1_long`; stop after the first half if any health row is red. Check `runs/e3_budget.json` first: stop if M1 has < 220 left.
- Orchestrator Sonnet, subagents Haiku (`model: "haiku"` explicitly), one WP per agent, never Opus. Ponytail full, codegraph first.
- No game-specific code in `agent/`; World never picks goals or filters options; RAM truth is evaluation only. Never edit contracts.md or loop/.

## Measured numbers
- Full suite: 209 passed (`pytest tests/agent -q`, ~4 min).
- Act subset (golden, world, act): 30 passed. `check_world.py roles runs/m1_act` and `cut runs/m1_act 75` exit 0.
- Lines: agent 2560 -> 2178, scripts 2784 -> 2667, tests 4161 -> 3346.
- Ledger unchanged by P4 (total 657 / M1 639 / stage_c 86; ceilings 1,100 / 1,050 / 546).

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe, forward-slash paths. pyflakes installed there with `uv pip install --python .venv-agent/Scripts/python.exe pyflakes` (no pip in the venv).
- `eval_run.py` writes summary.json by default; use `--no-write` on runs/m1 and runs/m1_act.
- `_ledger` picks the bucket by run name: names containing `m1` charge M1.
- tests/agent/data is tracked for the goldens; regenerate only with `W0_REGEN=1` and review the data diff.
- Pre-existing .claude/ is untracked; leave it.

## Deviations from plan and contracts
- Golden act data regenerated in P4: `task` null, `wake`/`task_budget_exhausted` keys removed, `tasks.jsonl` removed (default-goal machinery deleted).
- contracts.md still describes `tasks`, `chooser and orchestrator`, `space.describe` and the exit filter (marked "removed in P4"); no contract edits were made.
- `Stuck` no longer emits "budget".

## Do not retry
- Do not run 40-step live comparisons against runs/m1; do not resume L1 from runs/m1 checkpoints.
- Never git add -A, commit runs/ or .tmp/, reset/refund a ledger, or push.

## Open issues for W4
- `Memory.view` still has `chooser`/`orchestrator` branches and sections used only by tests; a compression candidate after the program.
- The `recent:` flag block in `Loop.step` (about 25 lines) is a compression candidate.
- `fix_player` assumes a following camera; W4 motion anchors become the primary rule (`self` anchor beats the screen-spot rule).
- Dialogue/menu screens still become places; L1 counts them as `screen_places`.
