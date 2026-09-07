# Batch 1: Canonical target identity

**Complexity: Medium**
**Recommended agent:** Worker B (`frame_model.py`, `screen_objects.py`), orchestrator (`loop.py`), Worker A (fixtures + tests)
**Prerequisite:** none beyond V2 commits
**Primary failure:** `target-not-found:object:N` ×16 and `source-unconfirmed:object:N` ×4 in Run B.

## Objective

ACT never receives a mark-derived id. Every card/enemy/player store object carries `card_N` / `enemy_N` / `player_N` (or a verified stable id), and the same sprite is never published twice.

## Owned files

Worker B:

- `loop/os/frame_model.py` — `_control_ids` (`:353-385`).
- `loop/memory/screen_objects.py` — `_is_mark_object_id` (`:21`), `_stable_object_id` (`:27`), `_prepare_frame_marks` dedup (`:43-60`).

Orchestrator:

- `loop/agent/loop.py` — `_build_turn_snapshot` card/enemy branches (`:4221-4279`).

Worker A:

- `tests/loop/fixtures/perception3/` — frozen Run B frames.
- `tests/loop/test_perception_v3_replay.py`, additions to `tests/loop/test_canonical_sprite_ids.py`, `tests/loop/test_screen_objects.py`.

Do not edit `bind_snapshot_target_ids`, `resolve_planned_target`, or export filters.

## Required behavior

### 1a — `object:N` is mark-derived everywhere

- `_is_mark_object_id` returns true for `mark_N`, `mk_N`, **and** `object:N` (reuse `_MARK_DERIVED_OID_RE` from `frame_model.py:125`; move the regex to `loop/agent/entities.py` if that avoids a circular import).
- `_control_ids`: for `role in {card, enemy, player}` regenerate the id when it is empty, `mark_*`, **or** matches `_MARK_DERIVED_OID_RE`. Keep ordinals by role, keep the `used` collision loop.
- `_stable_object_id`: returns `""` for `object:N` so `assign_unique` falls through to `enemy_N` / `card_N`.

### 1b — one sprite, one store row

- In `_prepare_frame_marks`, run the IoU ≥ 0.45 dedup **after** roles are final for this frame: treat a mark as `enemy_like` if its role is `enemy`/`target` **or** it is a hostile `unknown_character`/`character` row that `enrich_frame_marks` will type as enemy. Simplest acceptable implementation: dedup on `(role in _TARGET_ROLES, IoU ≥ 0.45)` where `_TARGET_ROLES` is the frame_model set (`unknown_character, character, target, enemy, player`), keeping the row with the highest `confidence` and preferring an existing canonical `enemy_N` id.
- Result on Run B step 6: one Nibbit store row, `object_id=enemy_1`.

### 1c — snapshot fail-closed

- `_build_turn_snapshot` skips a `card` or `enemy` object whose `object_id` is empty, a prompt id, **or** mark-derived (`object:N`, `mark_N`). Log one telemetry counter `snapshot.dropped_noncanonical` per step (count only; no new prompt text).
- Cards: same rule. Do not silently rename; drop and count. A dropped card means Batch 1a/1b is incomplete, and the test must fail.

## Required regression tests (Worker A)

Freeze from `runs/20260907T200835Z_sts2_collect`:

- step 1 `000001_obs.jpg` + `turns.jsonl[0].llm1_raw` (single anonymous enemy, five cards).
- step 6 `000006_obs.jpg` + `llm1_raw` (Nibbit identified; duplicate `object:13`/`enemy_1` in the original run).

Tests:

- `test_control_ids_regenerate_object_prefixed_domain_ids` — a `FrameObject(role="enemy", object_id="object:10")` comes out as `enemy_1`.
- `test_stable_object_id_rejects_object_prefixed_ids`.
- `test_save_frame_never_persists_object_ids_on_domain_roles` — after `save_frame` on any fixture frame, no store row with `role in {card, enemy, player}` has `object_id` matching `^object:\d+$`.
- `test_run_b_step1_replay_yields_single_canonical_target` — snapshot has 5 cards, `targets == [enemy_1]`, End Turn control.
- `test_run_b_step6_replay_dedups_same_sprite` — exactly one target, name `Nibbit`.
- `test_snapshot_drops_mark_derived_ids_and_counts` — synthetic store row `role=enemy, object_id=object:3` is not in `targets`, counter increments.

Must keep passing: `tests/loop/test_mcp_tools.py` (stable-id contract), `tests/loop/test_canonical_sprite_ids.py`, `tests/loop/test_action_surface.py`, `tests/loop/test_perception_v2_replay.py`, `tests/loop/test_screen_objects.py`.

## Focused test command

```
python -m pytest tests/loop/test_perception_v3_replay.py tests/loop/test_canonical_sprite_ids.py tests/loop/test_screen_objects.py tests/loop/test_mcp_tools.py tests/loop/test_action_surface.py tests/loop/test_perception_v2_replay.py -q
```

Cumulative: `python -m pytest tests/loop -q`

## Gate

Step 1 and step 6 replays pass; zero `object:N` on domain roles across every fixture frame in `tests/loop/fixtures/`; cumulative ≥ 781 passed. Worker A reviews the diff. Commit as `fix: never publish mark-derived ids as ACT targets`.

## Stop condition

Stop if the fix requires renaming ids inside `bind_snapshot_target_ids` / `resolve_planned_target` (that is aliasing, not identity), or if any test needs a prompt id to become a store id.
