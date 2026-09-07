# Batch 2 reopen: dual store `enemy_1`+`enemy_2` on one sprite

**Complexity: Medium**
**Recommended agent:** Worker A (tests), Worker B (`screen_objects.py`, `aim.py`), orchestrator (`loop.py`)
**Prerequisite:** Batch 2 committed (`961bbf9`); live Run B `runs/20260907T225552Z_sts2_collect` preserved
**Do not drive the live game. Do not delete `runs/`. Do not edit prompts, `bind_snapshot_target_ids`, or `is_prompt_instance_id`.**

## Why Batch 2 live-failed

Run B won first combat (21 steps, loot step 20) but logged `target-not-found:enemy_2` ×2 (steps 2, 17). `inspect-run`: `noncanonical_target_ids=0`. Tracker prune/confidence gates held.

Root cause is **not** a tracker-only phantom. `_build_turn_snapshot` publishes **store** `role=enemy` rows. Live snapshots often listed both `enemy_1` and `enemy_2` as the same name (`unknown enemy` / `Nibbit`) — 11/21 steps. ACT bound `enemy_2` → `missing-stable-target`.

Batch 2 `_cap_unresolved_blockers` only drops **unidentified stale** tracker rows when host `enemies==1`. Both live rows were **identified store objects**, so the cap never ran. `_prepare_frame_marks` only collapses sprites at **IoU ≥ 0.45**. Body box vs HP-plate / cached box often sits below that → two `enemy_*` ids.

Logged `turn_snapshot.targets` omit bbox → `duplicate_target_bboxes=0` is blind.

LLM1 on these steps usually emitted **one** `unknown_enemy` plus the player. Dual ids come from mark fusion / cache / seed, not from two LLM1 enemies.

## Objective

One visible hostile sprite → one snapshot target (`enemy_1`). Two spatially separated hostiles stay two. ACT never sees a sibling id for the same sprite.

## Owned files

Worker B:

- `loop/memory/screen_objects.py` — `_prepare_frame_marks` cluster collapse (IoU **or** center distance).
- `loop/os/aim.py` — `merge_cached_enemy_marks` must not reattach a second enemy-band mark that clusters with a current-frame mark.

Orchestrator:

- `loop/agent/loop.py` — `_build_turn_snapshot` last-defense: cluster duplicate `role=enemy` store rows before publishing `TargetSnapshot`. Prefer `enemy_1` / identified name / higher confidence.

Worker A: tests.

## Required behavior

### 2e — spatial cluster, not host-count-only

Host `enemies` is often missing on live `metrics` (Run B dump: `host None`). Do **not** make the fix depend only on `_host_enemy_count`.

Collapse two enemy-like marks/objects into one when **either**:

- bbox IoU ≥ 0.20, or
- center distance ≤ 0.12

Keep the `_prefer_target_row` winner (typed enemy > unknown; canonical `enemy_N`; higher confidence). Existing 0.45 IoU path stays; 2e is the looser same-sprite cluster.

When host **does** report `enemies == 1` (int or one-row list), at most one enemy-like mark/object survives even if centers are farther (HP OCR ghost). When host reports ≥2, never use this extra cap.

### 2f — snapshot last defense

`_build_turn_snapshot` must not publish two `TargetSnapshot` rows that cluster under 2e. Store may still have chrome; executable targets must not.

### 2g — cache merge

`merge_cached_enemy_marks`: skip a cached enemy-band mark that clusters with an already-kept enemy-band mark (same 2e rule). Do not drop a second cached mark whose center is far (real second enemy).

## Required regression tests (Worker A)

Add to `tests/loop/test_perception_v3_replay.py` (and `test_aim.py` if cache merge lives there):

- `test_two_same_sprite_enemy_marks_below_iou_045_collapse_to_enemy_1` — body `[0.69,0.57,0.81,0.72]` + HP-ish `[0.70,0.50,0.80,0.58]` (IoU < 0.45, centers ≤ 0.12) + one Strike + End Turn → `len(targets)==1`, `target_id==enemy_1`, one store `role=enemy`.
- `test_two_named_nibbit_store_rows_snapshot_publishes_one` — two `role=enemy` objects both labeled Nibbit, clustered boxes, through `_build_turn_snapshot`.
- `test_two_separated_enemies_stay_two` — two enemy marks centers ≥ 0.25 apart, host `enemies` list length 2 → two targets. Must keep `tests/loop/fixtures/sts2_screens/combat_turn.jpg` behavior if that fixture is two-enemy; otherwise synthetic marks.
- `test_merge_cached_does_not_reattach_clustered_duplicate` — current frame one Nibbit in band; cached second Nibbit overlapping/nearby → still one enemy-band mark.
- Existing Batch 1–2 replays (steps 1, 6, 7, 19) must still yield exactly one target.

Do **not** copy live `runs/` into fixtures unless a frozen json+jpg already exists. Synthetic marks are enough; live Run B is evidence, not a required binary fixture.

## Focused test command

```
python -m pytest tests/loop/test_perception_v3_replay.py tests/loop/test_aim.py tests/loop/test_entity_discovery.py tests/loop/test_llm1_target_routing.py -q
```

Cumulative: `python -m pytest tests/loop -q`

## Gate

Focused tests green; cumulative ≥ previous `tests/loop` count; no `object:N` on domain roles; two-enemy synthetic still two targets.

Commit: `fix: collapse clustered duplicate enemy ids before ACT`

## Stop condition

Stop if clustering merges two real separated enemies on a multi-enemy fixture, or if the extra host `enemies==1` cap is the **only** path (it must be backup, not the sole fix).
