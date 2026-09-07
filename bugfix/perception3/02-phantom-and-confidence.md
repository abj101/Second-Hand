# Batch 2: Phantom targets and confidence normalization

**Complexity: Medium**
**Recommended agent:** Worker B (`entities.py`), orchestrator (`loop.py`), Worker A (tests)
**Prerequisite:** Batch 1 committed
**Primary failure:** single-enemy fight published 2–3 targets (`enemy_2`, `enemy_3` "unknown enemy" 0.35) for 22 steps; `identity_confidence` 106–143; `epistemic-blocker:unknown_character_2` routed every step to explore→force-ACT and forced a fresh LLM1 rescan on 25/28 steps.

## Objective

Snapshot targets equal the set of hostile sprites visible in the current frame. Confidence is a probability. A fully anchored single enemy routes straight to ACT without an epistemic blocker.

## Owned files

Worker B:

- `loop/agent/entities.py` — `EntityTracker` aging (`last_seen_cycle`, `_MATCH_RADIUS`, `to_row`).
- `loop/os/aim.py` — `seed_enemies_from_host`, `merge_cached_enemy_marks` **only if** the tracker fix alone does not clear the phantom (verify first with the step 7 replay).

Orchestrator:

- `loop/agent/loop.py` — `_build_turn_snapshot` (`:4149-4152` tracker rows, `:4255-4273` confidence), `_two_stage_epistemic_blockers` / `_entity_rows_for_route` (`:2676`).

Worker A: tests.

Do not edit prompts, `bind_snapshot_target_ids`, or `is_prompt_instance_id`.

## Required behavior

### 2a — tracker rows never create targets

- In `_build_turn_snapshot`, tracker `to_row()` entries feed `bind_snapshot_target_ids` for **aliasing only**. Targets are still built from `self._screen_objects.objects` (already the case after Batch 1c). Confirm no other path appends tracker-derived marks to the store on combat frames; if `seed_enemies_from_host` or `merge_cached_enemy_marks` inserts an enemy mark with no current-frame pixel evidence, that insertion must require a bbox overlap with a current-frame `unknown_character`/`enemy` mark.

### 2b — tracker aging

- `TrackedEntity` unseen for ≥ 2 consecutive combat cycles (`_cycle - last_seen_cycle >= 2`) is dropped from `entities` and from `_entity_rows_for_route`. Identified entities keep their name in a side map keyed by canonical name so re-appearance re-identifies without inspect.
- When `host_stats` reports `enemies == 1` (HP plate count), at most one unresolved tracker row may act as an epistemic blocker; extra unresolved rows are pruned, not routed.

### 2c — confidence normalization

- `ScreenObject.from_mark`: `confidence = mark.confidence if 0 <= mark.confidence <= 1 else None-equivalent (0.0)`; never fall back to raw `mark.score` unless `0 <= score <= 1`.
- `_build_turn_snapshot`: `identity_confidence = clamp(value, 0, 1)` when numeric in range, else `None`. The anonymous-label rule (`>= 0.75 → 0.35`) then behaves.
- Add the same clamp to `TargetSnapshot.__post_init__` as a last defense (dataclass is frozen; use `object.__setattr__`).

### 2d — route

- `_two_stage_epistemic_blockers` ignores rows that Batch 2b pruned and rows whose only evidence is a tracker bbox with no current-frame store match. Expected on Run B step 9+: `router_reason=act`, `perception_source` mostly `scene_cache` / `llm1` on hand change, not `forced_rescan`.

## Required regression tests (Worker A)

Freeze from `runs/20260907T200835Z_sts2_collect`: step 7 (`enemy_2` phantom first appears), step 19 (`enemy_2` + `enemy_3` phantoms, Nibbit as `object:14`).

- `test_tracker_prunes_entities_unseen_two_cycles`
- `test_single_host_enemy_allows_one_blocker_max`
- `test_run_b_step7_replay_single_target`
- `test_run_b_step19_replay_single_target_canonical_id`
- `test_identity_confidence_clamped_or_none` (store row confidence 143.1 → `None`; 0.72 → 0.72)
- `test_anchored_single_enemy_routes_to_act_without_blocker`

Must keep passing: `tests/loop/test_entity_discovery.py`, `tests/loop/test_perception_cache.py`, `tests/loop/test_perceive_gate.py`, `tests/loop/test_llm1_target_routing.py`, `tests/loop/test_aim.py`, all Batch 1 tests.

## Focused test command

```
python -m pytest tests/loop/test_perception_v3_replay.py tests/loop/test_entity_discovery.py tests/loop/test_perception_cache.py tests/loop/test_perceive_gate.py tests/loop/test_llm1_target_routing.py tests/loop/test_aim.py tests/loop/test_decision.py -q
```

Cumulative: `python -m pytest tests/loop -q`

## Gate

Steps 7 and 19 replays each yield exactly one target with canonical id; no `identity_confidence > 1` anywhere in replay snapshots; route test passes; cumulative ≥ Batch 1 count. Commit as `fix: prune phantom tracker targets and normalize identity confidence`.

## Stop condition

Stop if pruning removes a real second enemy on any multi-enemy fixture (`tests/loop/fixtures/sts2_screens/combat_turn.jpg` and any two-enemy frame in `runs/`), or if the route change requires weakening `identify-before-act` for genuinely unanchored rows.
