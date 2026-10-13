# Handoff notes

Last written by: the user's Claude session, 2026-10-12, after the W4 run. P4 committed (143e0e6). W4 committed with its offline anchor row deferred to L1 (user, option 1). Next: L1.

## State of the code
- Branch feat/lean-agent. HEAD = W4 commit. W4 contents: `agent/world.py` (`classify`, `POS_TOL`, `ANCHOR_EVIDENCE`, anchor/region/transient fields, HUD change flag, `_actor`, `self_spot` path in `fix_player`, state keys `cam_px`/`evidence`/`self_spot`/`hud_changed`), one call in `agent/loop.py` (`observe_after(..., before=, skill=)`), `tests/agent/fakes.py` (GridEnv `hud`/`wander`/`animated`, `perception()`), `tests/agent/test_world.py` (7 `anchor` tests), regenerated act golden entities, `scripts/check_world.py anchors`.
- Kept from P3b: `fix_player`, the cut rule, the `recent:` flag. The act path is the only path (P4).
- `ACTOR_KINDS` is the stand-in and is used only for `unknown` anchors.

## Decisions in force
- Build-first: gates are unit tests plus offline checks vs RAM truth; the only live spend left is L1 (two halves of 100 steps, <= 110 requests each, fresh boot into `runs/m1_long`; check `runs/e3_budget.json`: stop if M1 < 220 left).
- L1 tier: orchestrator GPT-6.1 Sol, subagents gpt-6-luna HIGH (set explicitly), one WP each, never Opus. No game-specific code in `agent/`; World never picks goals; never edit contracts.md or loop/.

## Measured numbers
- Full suite: 215 passed (~4.3 min). P4 alone was 209.
- W4 synthetic: `-k anchor` 7 passed; player self, npc actor, hp bar screen, fountain/door world within 2 steps.
- W4 offline `check_world.py anchors runs/m1_act` (exit 1): decided 0/7, undecided 1.000 (gate <= 0.30), actor_walls 0 vacuous (56 blocked rows have no direction). Cause: only 1 camera scroll in 40 steps.
- Lines: agent 2178 -> 2263, scripts 2667 -> 2827, tests 3346 -> 3469. Ledger unchanged (657 / M1 639 / stage_c 86).

## Environment and gotchas
- `.venv-agent/Scripts/python.exe`, forward slashes. pyflakes installed via `uv pip install --python .venv-agent/Scripts/python.exe pyflakes`.
- `eval_run.py` needs `--no-write` on runs/m1 and runs/m1_act. `_ledger` buckets by run name (`m1` in name -> M1).
- Regenerate goldens only with `W0_REGEN=1`; review the diff.
- GridEnv with `hud/wander/animated` uses a different vertical origin (64) so tiles sit on `observer.snap`'s grid; default geometry is unchanged.

## Deviations from plan and contracts
- W4: `classify` history in tile units; `responded` = appearance before at s1 vs after at s2; contracts.md text for `classify` says px-agnostic, no edit made.
- Act golden regenerated twice (P4: task/wake removed; W4: anchor fields). Trace keeps `"task": None`.

## Do not retry
- Do not tune thresholds or the replay to pass the anchor gate on `runs/m1_act`; it has no camera motion.
- No 40-step live comparisons; do not resume L1 from runs/m1 checkpoints; never git add -A, commit runs/ or .tmp/, reset a ledger, or push.

## Open issues for TL (next phase, docs/plans/TL.md)
- L1 failed on player identity: the perceiver labelled Mom as the player; `run_affordance` took `player_at` from that label (skills.py:219); `fix_player` never fired.
- Duplicates are a naming bug (`_base` strips one `#n`; the model echoes `furniture#2`) plus two observe calls per step. They are not an identity-design problem.
- Walls in L1 were correct. A false inferred reverse exit LEFT sits at `p_world@0,0` (mark_exit).
- Build-first: checks report, they do not block. Full suite before L2; L2 resumes runs/m1_long from checkpoint 70 (back up to runs/m1_long_l1 first).
- Measured: one LEFT step's scroll-compensated residual has 202/230 px in the player box; scroll 2 px every other frame.
