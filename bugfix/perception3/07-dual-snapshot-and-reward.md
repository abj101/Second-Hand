# Batch 7 — Dual snapshot leak (second attempt) and reward-screen stall

Status: implemented
Live evidence: `runs/20260908T194352Z_sts2_collect` (Batch 6 code, commit `61f3943`)
Predecessor: `06-run-b-dual-target.md`

## 1. What Batch 6 fixed and what it did not

Batch 6 shipped three things:

- **6a** `same_sprite_cluster` gained `hp_body_column_stack`: an HP/intent plate stacked
  over a body collapses into one sprite when `|dx| <= SAME_SPRITE_COLUMN` (0.08) and the
  two centers sit in the HP band (`ENEMY_Y_MIN..ENEMY_Y_MAX`) and the body band
  (`ENEMY_Y_MAX..HAND_Y`) respectively.
- **6b** `_blocked_targets` / `_target_is_blocked` drop a target from the published
  snapshot after a `target-not-found` execution failure, plus combat replan-after-TNF.
- **6c** `_replan_after_control_failure` for `map` / `reward` click_control no-ops.

Live Batch 6 B (`20260908T194352Z`) held the Batch 5 gate (`source-unconfirmed=0`) but
still leaked a dual target at steps 2, 3, 4, 6, 10 and 11, took a
`target-not-found:enemy_2` at step 6, then stalled out on the reward screen and burned
the step budget (`decision_softlock`, `no_pixel_change`, `step_budget_timeout`).

## 2. Audit

### Q1 — Why did the column stack miss this live dual?

`same_sprite_cluster` only consults `hp_body_column_stack` **inside the
`dist >= SEPARATED_SPRITE_CENTER` branch**:

```
dist = bbox_center_distance(a, b)
if dist >= SEPARATED_SPRITE_CENTER:          # 0.25
    if host_enemies == 1: return True
    return hp_body_column_stack(a, b)        # <-- only reachable here
if _bbox_iou(a, b) >= SAME_SPRITE_IOU: return True
return dist <= SAME_SPRITE_CENTER            # 0.12
```

That leaves a **dead band** for `0.12 <= dist < 0.25` in which no stack test runs at all.
The live pairs (aim-mark centres from `turns.jsonl`, milli-normalised) land in it:

| step | mark A            | mark B            | dx    | dy    | dist  | branch taken               |
|------|-------------------|-------------------|-------|-------|-------|----------------------------|
| 2    | Nibbit 0.639,0.289| Nibbit 0.748,0.447| 0.109 | 0.158 | 0.192 | dead band — no stack test  |
| 3    | Nibbit 0.616,0.328| Nibbit 0.755,0.633| 0.139 | 0.305 | 0.335 | separated — `|dx|` > 0.08  |
| 10   | Nibbit 0.494,0.353| Nibbit 0.488,0.500| 0.006 | 0.147 | 0.147 | dead band — no stack test  |
| 11   | Nibbit 0.795,0.647| unknown 0.773,0.414| 0.022 | 0.233 | 0.234 | dead band — no stack test  |

Two independent predicate failures, so widening one constant would not have been enough:

1. **Dead-band gap.** Steps 2, 10 and 11 never reached `hp_body_column_stack`.
2. **Band + column too tight.** Step 3 did reach it but `|dx| = 0.139 > 0.08`. And at
   step 2 *both* centres (0.289 and 0.447) sit inside the HP band `0.15..0.45`, so the
   `hp band over body band` requirement is false even with an infinitely wide column.

The real invariant the live data supports is simpler than "HP plate over body": **two rows
with the same (or name-compatible) identity, stacked so that vertical separation dominates
horizontal separation, in an overlapping column, are one sprite.** That holds with
`host_enemies is None` and does not depend on which y-band each row happens to fall in.

Published targets do go through `same_sprite_cluster` (via `collapse_same_sprite_cluster`
at the end of `_build_turn_snapshot`), so this is not a bypass — the predicate simply
returned `False`.

### Q2 — Why can the snapshot list two Nibbits while entity merge lists one bbox?

`_build_turn_snapshot` publishes by iterating `self._screen_objects.objects` — the store.
The entity tracker's merged entities are only appended to `instance_rows` and handed to
`bind_snapshot_target_ids` for **id binding**; they are never the source of the published
`TargetSnapshot` rows. So a merge down to one entity does not remove the second store
object, and the store keeps two canonical ids (`enemy_1`, `enemy_2`). The cluster collapse
is the only thing standing between two store rows and two published targets.

### Q3 — Why did reward soft-lock?

`_replan_after_control_failure` (6c) is only reachable from the branch that runs when
`legal_intents` is **non-empty** (`_should_replan_unchanged_failure`, called under
`if self._last_execution_failure and state_key == self._last_reason_state_key`). On the
reward screen the surface went *empty* — steps 14, 17 and 18 published no controls at all
— so control replan was never consulted. Execution fell into the empty-legal-intents
branch, which has exactly two outcomes:

- `interactive_evidence and count == 1` → `inventory-inconsistent` + one rescan, or
- otherwise → `empty-action-surface`, and at `count >= 2` an unconditional
  `_decision_softlock = True`, which breaks the episode loop into `terminal="timeout"`.

The live sequence: 13 click `add_a_card_to_your_deck` (pixel yes) → 14 empty surface,
`inventory-inconsistent` → 15 `skip` (pixel yes) → 16 click `12_gold` (pixel **no**, the
control was already consumed) → 17 empty surface, `inventory-inconsistent` → 18 empty
surface, `empty-action-surface` → budget exhausted. The screen was still a live reward
screen with real loot buttons on it; the surface builder just came back empty. Declaring
a decision softlock there is wrong: a reward/map screen with an empty surface is a
perception stall, not a genuine "no legal move" state.

### Q4 — TNF fail-closed is a last defense

Confirmed: at step 6 ACT had already chosen `enemy_2`, dispatch missed, and only *then*
did `_blocked_targets` suppress the row (step 7 correctly showed a single target). The fix
has to stop the dual from being published in the first place; the TNF block stays as the
last line of defense and is not modified.

## 3. Fix

### A. One hostile sprite → one published target

`loop/memory/screen_objects.py`

1. Restructure `same_sprite_cluster` so the stack predicates are evaluated in the
   `0.12 <= dist < 0.25` dead band as well as above `SEPARATED_SPRITE_CENTER`.
2. Add `vertical_sprite_stack(a, b)`: true when
   - the two rows' names are **compatible** — identical after normalisation, or at least
     one of them is anonymous/unknown (an unnamed plate belongs to the named body); two
     *differently* named hostiles are never one sprite;
   - **vertical separation dominates**: `|dy| > |dx|`. Side-by-side hostiles have
     `|dy| ≈ 0` and are therefore always kept apart, which is what keeps
     `test_two_separated_enemies_stay_two` green;
   - the pair shares a column: `|dx| <= 0.14`, widened to `0.20` when both rows carry the
     same non-anonymous name **and** the same hp, or the bounding boxes overlap in x;
   - total centre distance `<= 0.45`.
3. Add `collapse_duplicate_named_targets(...)` as a snapshot-level last defense: two rows
   with the same non-anonymous name whose bboxes overlap horizontally are one sprite,
   regardless of distance. Side-by-side hostiles have disjoint x ranges and survive.

`SEPARATED_SPRITE_CENTER` is **not** changed.

`loop/agent/loop.py`

4. `_build_turn_snapshot` runs `collapse_duplicate_named_targets` after
   `collapse_same_sprite_cluster`.

### B. Dual leak must not reach ACT

Covered by A: the collapse happens while building the snapshot, before
`legal_intents_for_snapshot` and before ACT sees anything. `_target_is_blocked` /
`_replan_after_tnf` are left exactly as Batch 6 shipped them.

### C. Reward / map must not soft-lock on an empty surface

`loop/agent/loop.py`

5. New `_recover_loot_surface(reason)`: on `map` / `reward`, clear this screen's blocked
   control ids, reset the perception cache, force a perceive and rescan, and report that
   the caller should not soft-lock. Bounded by `_LOOT_SURFACE_RECOVERY_LIMIT` recoveries
   per screen visit; the counter resets whenever the screen changes or a decision
   succeeds, so a genuinely dead screen still terminates.
6. In `_run_v2_act`, the empty-legal-intents path calls `_recover_loot_surface` before
   setting `_decision_softlock`, and the `decision-softlock` path (non-empty intents,
   unchanged failure twice) does the same.

No auto `request_stop` after loot is added; the operator still ends a clean win.

## 4. Not changed

- No prompt edits, no `skill_on` changes, no window-size handling.
- `resolve_planned_target` still does not alias `enemy_2` → `enemy_1`.
- `SEPARATED_SPRITE_CENTER` stays at 0.25; `SAME_SPRITE_COLUMN` stays at 0.08.
- `runs/` is untouched; tests use synthetic rows shaped like steps 2/6/10 of
  `20260908T194352Z`, not a copy of the run.

## 5. Tests

`tests/loop/test_perception_v3_replay.py`

- `test_dual_nibbit_dead_band_collapses_to_one` — step 2 shape (two `Nibbit`, hp 38,
  centres 0.639/0.289 and 0.748/0.447, host enemies omitted) → one target, `enemy_1`.
- `test_dual_nibbit_same_column_collapses_to_one` — step 10 shape (same column, dist in
  the dead band) → one target.
- `test_dual_nibbit_split_hp_collapses_to_one` — step 6 shape (hp 18 vs hp 1, the pair
  that produced `target-not-found:enemy_2`) → one target and the snapshot never lists
  `enemy_2`.
- `test_named_body_absorbs_anonymous_plate` — step 11 shape (`Nibbit` + `unknown enemy`).
- Existing `test_two_separated_enemies_stay_two` and
  `test_hp_body_column_stack_collapses_without_host_count` stay green.

`tests/loop/test_decision.py`

- Reward screen, empty action surface twice → no `_decision_softlock`, blocked controls
  cleared, rescan requested.
- Same on `map`.
- Recovery budget exhausted → soft-lock still fires (no infinite loop).
- Combat with an empty surface still soft-locks (behaviour unchanged off the loot path).
