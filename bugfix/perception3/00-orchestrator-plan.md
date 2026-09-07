# Orchestrator Plan: Perception V3 — canonical target identity

**Overall complexity: Medium**
**Execution model:** one orchestrator, at most two concurrent workers
**Prerequisite:** Perception V2 batches 1–4 committed on `feat/collect-loop` (`708d135`, `3ecb0d1`, `946a4f4`, `1897008`); `tests/loop` at 781 passed.
**Target outcome:** every ACT `target_id` is a canonical store id (`enemy_N`), no phantom targets in the snapshot, `identity_confidence` in `[0, 1]`, monitor script usable, then Run A (title→combat) and Runs B+C (two consecutive combat-start wins) pass.

## Why V2 live validation failed

Run B (`runs/20260907T200835Z_sts2_collect`, 28 steps) played two cards and then bound-looped. Per-step evidence from `turns.jsonl`:

| Symptom | Count | Root cause (file:line) |
|---|---|---|
| `target-not-found:object:N` | 16 | Store objects with `role=enemy` and `object_id=object:N`. `_control_ids` (`loop/os/frame_model.py:366-380`) only regenerates ids that start with `mark_`; `_stable_object_id` (`loop/memory/screen_objects.py:27-31`) only rejects `mark_N` via `is_frame_mark_token`. An object first typed `unknown`/`unknown_character` gets `object:N`, later relabeled `enemy` by `ground_combat_labels` / enrich, and keeps `object:N` across every later `save_frame`. `_build_turn_snapshot` (`loop/agent/loop.py:4249-4254`) only filters `is_prompt_instance_id`, so `object:N` leaks to ACT. Card transaction rebind then fails with `missing-stable-target` (`loop.py:2308`). |
| `source-unconfirmed:object:8/10` | 4 of 7 | Same id bug on cards: `role=card` with `object:N`. |
| Duplicate targets (`object:13` + `enemy_1`, both Nibbit) | steps 6–8, 10, 12, 14–16, 18–20, 23, 27 | Same sprite published twice: one row from the surface (`object:N`), one from `_prepare_frame_marks` (`enemy_1`). Dedup in `_prepare_frame_marks` (`screen_objects.py:45-59`) requires both marks to be `enemy_like` and IoU ≥ 0.45; the `object:N` row is not `enemy_like` at prepare time. |
| Phantom `enemy_2` / `enemy_3` "unknown enemy" 0.35 | steps 7–28 | Tracker rows (`EntityTracker.to_row`, `loop/agent/entities.py:426-441`) persist at stale bbox; `_build_turn_snapshot` appends tracker rows to `instance_rows` (`loop.py:4149-4152`), and `seed_enemies_from_host` / `merge_cached_enemy_marks` re-seed them. Single enemy fight showed 2–3 targets. |
| `identity_confidence` = 106–143 | 10 rows | `ScreenObject.from_mark` stores `mark.confidence or mark.score` (`screen_objects.py:163`); DINO/template `score` is unnormalized. `_build_turn_snapshot:4258-4264` casts to float without clamping, and the `>= 0.75` anonymous check treats 143 as high. |
| `router_reason=epistemic-blocker:unknown_character_N` | 28/28 | Phantom `unknown_character_2` never resolves → explore→force-ACT every step → `forced_rescan` 25/28 → fresh LLM1 each step (~17 s/step). |
| Monitor crash | step ~20 | `scripts/monitor_sts2_loop.py:60` references undefined `populated`. |

Run A and Run C were never started; Run B never won.

## Locked contracts (do not reopen)

1. V2 contracts 1–5 in `bugfix/perception2/00-orchestrator-plan.md` stay locked (overlay fix, geometry-gated card promotion, anonymous hostile inspectable, transition frames observation-only, workers never drive the live game).
2. **`object:N` is a frame-local mark-derived id, never executable identity.** Same status as `mark_N`. It may exist for chrome/informational objects, but never on `role in {card, enemy, player}` and never in `TurnSnapshot.cards[].card_id` / `targets[].target_id`.
3. **Snapshot targets come from the store only.** Tracker rows are metadata (name, hp, intent, confidence). A target without a current-frame store object with `role=enemy` is not published.
4. **`identity_confidence` is a probability.** Clamp to `[0, 1]`; unnormalized scores map to `None`.
5. **Prompt ids stay prompt ids.** `unknown_character_N` never becomes a store `object_id` (unchanged from V2).

## Agent assignment

| Batch | Agent | Owns |
|---|---|---|
| 1 Canonical target identity | Worker B + orchestrator | `loop/os/frame_model.py` `_control_ids`; `loop/memory/screen_objects.py` `_stable_object_id`, `_prepare_frame_marks`; `loop/agent/loop.py` `_build_turn_snapshot` (orchestrator) |
| 2 Phantom targets + confidence | Worker B + orchestrator | `loop/agent/entities.py` tracker aging/prune; `loop/agent/loop.py` `_build_turn_snapshot` confidence + tracker gating (orchestrator); `loop/os/aim.py` `seed_enemies_from_host` / `merge_cached_enemy_marks` only if needed |
| 3 Live tooling | Worker A | `scripts/monitor_sts2_loop.py`; `python -m loop inspect-run` target-id audit |
| 4 Live validation | Orchestrator with user at game | collect runs; no product edits |

Worker A also owns Run-B frozen fixtures and regression tests for Batches 1–2.

## Mandatory workflow for every batch

1. Record `git status --short`; preserve unrelated user changes and all `runs/` directories.
2. Read this file and the batch brief.
3. Add a failing regression test for each stated bug before editing product code.
4. Smallest fix that satisfies the brief.
5. Focused tests from the brief, then `python -m pytest tests/loop -q` (must stay ≥ 781 passed).
6. `git diff --check`, review diff, commit only batch files (`fix:` / `test:` / `chore:` prefixes).
7. Report changed files, test counts, remaining risks, commit hash.

## Batch order and promotion gates

### Batch 1 — Canonical target identity

Continue only when: replay of Run B step 1 (`000001_obs.jpg` + `llm1_raw`) yields exactly one target with `target_id == "enemy_1"` and no `object:N` on any card/enemy/player store row; replay of step 6 yields one Nibbit target (no duplicate); `test_mcp_tools.py` stable-id tests still pass.

### Batch 2 — Phantom targets + confidence

Continue only when: replay of steps 7 and 19 yields exactly one target; no tracker-only target is published; all `identity_confidence` values are `None` or in `[0, 1]`; route is `act` without an `epistemic-blocker` for a fully-visible, host-anchored single enemy.

### Batch 3 — Live tooling

Continue only when `scripts/monitor_sts2_loop.py` runs to completion against `runs/20260907T200835Z_sts2_collect` and `inspect-run` reports `noncanonical_target_ids=0` on a Batch-1 replay.

### Batch 4 — Live validation

Continue only when Run A plays ≥1 card from a title start with zero transition `empty-action-surface` and zero `decision_softlock`, and Runs B+C are two consecutive combat-start wins with zero `target-not-found`.

## Stop / rollback

Stop if a batch:

- lowers focused or cumulative pass count;
- lets `object:N`, `mark_N`, or a prompt id become an ACT `card_id`/`target_id`;
- publishes a target that has no current-frame store object;
- widens fixture/game-specific logic into the generic adapter;
- requires enabling skills for `no_skill` to win.

Revert only the offending batch commit.

## `skill_on` A/B

Blocked until Batch 4 gate passes.
