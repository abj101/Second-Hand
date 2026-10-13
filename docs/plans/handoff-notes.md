# Handoff notes

Last written by: the user's Claude session, 2026-10-12. P3b gate passed offline; the program moved to build-first; next phase P4.

## State of the code
- Branch feat/lean-agent. The P3b commit holds `World.fix_player`, the cut rule in `Memory.match_place`/`World.name`, the `recent:` act flag, evaluator fields and `check_world.py roles|cut`.
- P2/W0/W1/W2/W3/P3/P3b gate passed. Next: P4 (delete the gated path), then W4, then L1 (program-r1-wm.md §1).
- `fix_player` runs after both `_see` calls in `Loop.step` (agent/loop.py), so the cached perception is corrected too.
- The recent-actions flag is built inline in `Loop.step` (about 25 lines). It is a compression candidate after P4.

## Decisions in force
- **Build-first (user, 2026-10-12):** no live "better than gated?" comparisons. Phases gate on unit tests plus offline correctness checks against RAM truth. Progress is reported, never gated, until L1.
- P4 needs no performance decision; the user already chose deletion.
- The only remaining live spend is L1: two halves of 100 steps, ≤ 110 requests each, fresh boot into `runs/m1_long`. Stop after the first half if any health row is red.
- Root GPT-6.1 Sol; gpt-6-luna HIGH per WP, set explicitly. Ponytail full; codegraph first; Headroom for large output.
- No game-specific code in `agent/`; World never picks goals or filters options; RAM truth is evaluation only.
- Never edit contracts.md or loop/; line budgets are report-only.

## Measured numbers
- `check_world.py roles runs/m1_act` → exit 0: 40 world steps, 0 anchor failures; truth role errors 39 → 0 after `fix_player`.
- `check_world.py cut runs/m1_act 75` → exit 0: replay returns `p_world_3` (recorded `p_world_2`). The original branch was a landmark overlap of 0.60.
- P3 evaluator: player_role_errors 39, cut_self_loops 1, recent_line_steps 0. All 35 older fields unchanged on runs/m1 and runs/m1_act.
- Full suite 267 passed / 3 stale P3b expectations, fixed: skills self-target test, 2 W0 goldens regenerated (act golden had recorded the self-loop bug). Affected files rerun 23 passed.
- Lines: agent 2508→2560 (+52), scripts 2710→2784, tests 3962→4161.
- Ledger unchanged by P3b: total 657 / M1 639 / stage_c 86. Ceilings: total 1,100, M1 1,050, stage_c 546. `--max-calls` caps one invocation only.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe with forward-slash paths. The full suite takes ~5 min.
- `eval_run.py` writes summary.json by default; use `--no-write` on runs/m1 and runs/m1_act.
- `_ledger` picks the bucket by run name: names containing `m1` charge M1, others charge smoke (cap 50).
- Only checkpoints 0,25,43,50,54,74,75,100,114 exist in runs/m1_act; `roles` replays trace `entities` with checkpoint-74 anchors.
- Role scoring uses sprite slot 0 screen center (x+8, y+8) on a 160×144 frame with a 16 px threshold.
- tests/agent/data is gitignored; force-add intended fixtures only. Pre-existing .claude/ preserved.

## Deviations from plan and contracts
- The P3b live rerun (P3b.6/7) was dropped by the build-first decision.
- The modal fixture (`test_modal_overlay_at_step_start_keeps_place`) now asserts against `first["next_place"]`: a forced cut correctly creates a new place.
- No contract changes.

## Do not retry
- Do not run 40-step live comparisons against runs/m1; that baseline is retired.
- Do not resume L1 from runs/m1 checkpoints; their memory carries gated-path places.
- Never git add -A, commit runs/ or .tmp/, reset/refund a ledger, or push.

## Open issues for P4
- Keep `fix_player`, the cut rule and the `recent:` flag through the deletion; `check_world.py roles/cut runs/m1_act` must still exit 0.
- `tasks.Stuck` feeds act flags (`same_action`, `no_progress`, `no_change`); keep it if the flags still use it.
- The cut-arrival naming marker (`World._cut_arrival`) is transient and not checkpointed.
- Dialogue/menu screens still become places (`p_dialogue_box_2` is really 1F); L1 counts them as `screen_places`.
- `fix_player` assumes a camera that follows the player; W4 motion anchors become the primary rule.
