# Batch 5: Card-confirm loop

**Complexity: High**
**Recommended agent:** orchestrator (`loop.py`, `sts2.py`, monitor)
**Prerequisite:** Batch 2e (`d0cbfff`); `tests/loop` green
**Primary failure:** live B2 `source-unconfirmed:card_1` ×12; B3/B4 unconfirmed → `decision_softlock` with energy 3/3.

## Objective

Close the STS2 card-confirm loop. ACT may still emit `play_card card_1`; the executor must refuse an off-hand mark, actually block a failed coordinate across snapshot invalidation, compare frames like-for-like, and replan in live combat instead of softlocking.

No prompt edits. Window size is not the bug (`Sts2Fixture.observe_image` crops; `_remap` / `map_content_xy` maps clicks).

## What the run artifacts showed

Cross-run dump of every `card_transaction` (A, B, B2, B3, B4) plus per-frame `content_box_for` on saved frames:

| Defect | Runs | Evidence |
|---|---|---|
| **D1** `card_1` bound to a non-card mark | A, B, B2 | Unconfirmed clicks at `(0.085–0.087, 0.79–0.83)` — energy orb / bottom-left HUD. `_HAND_X0 = 0.18`. Real cards at `x` 0.31–0.69, `y` 0.83–0.95 confirmed. `_aim_mark_for_object` did not reject off-hand geometry. |
| **D2** blocked-retry defeated by invalidation | B2 9–20, B3, B4 | `_source_plan_fingerprint` included `snapshot_id`. Unconfirmed → `_invalidate_hand_inventory` bumps `hand_generation` → new `snapshot_id` → `_is_blocked_source_retry` false → same coords again (B2: 12× at `0.085,0.794`; B4: `0.312,0.95` twice). Evidence string claimed a block that did not fire. |
| **D3** cropped-vs-raw `stale-obs:motion` | B3, B4 | Capture content in top-left 2/3 (`content_box = (0,0,0.667,0.667)`). Card tx returned `observe_image()`-cropped `fresh_frame` as `_carry_after`. Next `mid = grab_image()` raw. `mid_mad` compared cropped vs raw → MAD ~0.148 > `STALE_MAD 0.08`. Saved `_after.jpg` alternated 768×432 / 768×433. `observe_image` on an already-cropped frame reset `_content_box` to full. |
| **D4** fail-closed softlock in live combat | B3, B4 | Two same-`state_key` failures → `decision-softlock` with energy 3, five cards, End Turn available. Reasoner never told the source was blocked → re-emits `card_1`. |

## Open question (do not "fix" as window size)

B3/B4 clicks at `(0.312, 0.95)` produced `source movement 0.000` while identical coordinates succeeded in A (step 28) and B (step 16) when `content_box` was full. Whether the remapped click lands on the card in the 2/3-box state is not decidable from artifacts.

Batch 5 adds a hover-first probe and a `geometry:` evidence line so the next live run answers this. If hover shows no lift at an in-band mark, **stop and report** — do not iterate live.

## Owned files

- `loop/os/aim.py` — `in_hand_band`
- `loop/fixtures/sts2.py` — `observe_image` must not overwrite `_content_box` on already-cropped input; only `grab_image` sets the box
- `loop/agent/loop.py` — hand-band gate, `_blocked_sources`, like-for-like `mid_mad`, raw `_carry_after`, snapshot `playable=False`, replan, hover-first, geometry evidence
- `scripts/monitor_sts2_loop.py` — skip HUD-frozen stop during unconfirmed bind loop; print Batch 5 flag at `source-unconfirmed >= 3` (do not auto-stop on that flag)

## Required behavior

### 5a — Hand-band gate

`_hand_source_mark` rejects a resolved card mark whose center is outside `x ∈ [_HAND_X0, _HAND_X1]`, `y >= hand_y`. Returns `None` → `source-not-found` plus evidence `off-hand-source-mark:x,y`. Same check covers the store-object `AimMark(...)` fallback.

### 5b — Blocked-retry keyed on object + coordinate

`_blocked_sources: dict[(object_id, round(x,2), round(y,2)) -> hand_generation_at_block]`. No `snapshot_id` in the key. Match `object_id` **or** coordinate within 0.02. Clear only when `_hand_inventory_signature()` changes or on combat/episode reset — not on invalidation. `blocked-same-coordinate-retry` only when the block actually fires.

### 5c — Like-for-like frames

- `observe_image`: if last box is cropped, incoming looks full, and size ≠ last grab → return unchanged (do not reset box).
- `mid_mad`: `roi_thumb(view)` vs `roi_thumb(mid_view)` — both `observe_image` cropped.
- `_carry_after` / `_CardExecution.after`: raw grab via `_PostClickProbe.raw_frame` (`_probe_after_frame`).

### 5d — Replan instead of softlock

`_build_turn_snapshot` sets blocked cards `playable=False` → `legal_intents_for_snapshot` excludes them; `validate_action_intent` rejects a repeat. After unconfirmed, if combat + energy > 0 + remaining legal intents: `_force_perceive`, `_rescan_scene(focus="source-unconfirmed")`, reset `_unchanged_failure_count`, continue. Softlock only when no non-blocked legal intent remains. Empty ACT after unconfirmed records `replan-after-unconfirmed`, not `decision-softlock`.

### 5e — Hover-first + geometry telemetry

Before the V2 source click: `execute_chord` hover (`mouse_move`), probe ~250 ms, require source ROI change ≥ 0.045. No lift → `source-unconfirmed:hover:<id>`, no click, block via 5b. Lift → existing click path. Hover is a no-op in STS2 game state. `CardTransaction.verification_evidence` gets `geometry:... click_client=x,y`.

### 5f — Monitor

Do not HUD-frozen-stop when the last N steps are `source-unconfirmed` / `stale-obs` / `stale-source-plan` in combat with energy > 0. Count `source-unconfirmed`; print `BATCH5 fail: source-unconfirmed>=3` but do not auto-stop on that flag.

## Tests

- `test_two_stage_resolve.py`: off-hand mark → `source-not-found`; hover no-lift → `source-unconfirmed:hover`, no click primitive.
- `test_combat_fail_closed.py`: block survives invalidation + new `snapshot_id`; clears on hand signature change.
- `test_perception_v3_replay.py`: B4 frames (`runs/20260908T040110Z_sts2_collect`) — first-hand Strike/`card_1` in-band; no `decision_softlock`.
- `test_sts2.py` / `test_geometry.py`: cropped `observe_image` leaves `_content_box`; mixed cropped-vs-raw MAD > like-for-like.
- `test_decision.py`: blocked card `playable=False`, `validate_action_intent` rejects.
- `test_monitor_sts2.py`: skip HUD-freeze during unconfirmed loop; Batch 5 flag.

Keep `test_mcp_tools.py`, `test_perception_v3_replay.py`, `test_two_stage_resolve.py` green. Cumulative `tests/loop` ≥ 810.

## Gate

Focused tests + `python -m pytest tests/loop -q` green. One commit. Then operator live B and C from a fresh first combat turn.

## Live (operator, after commit)

Preserve all listed `runs/` dirs (A through B4). No `skill_on`, curate, or train.

```
python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu
python scripts/monitor_sts2_loop.py --run <dir> --poll-s 20
```

Fresh Ironclad, first combat, first player turn (80/80, 3 energy, 5 cards). Clear `data/loop/stop` only with no collector running.

**B pass:** fight won, `target-not-found = 0`, `source-unconfirmed < 3`, no `decision_softlock`, `forced_rescan/steps <= 0.3`. Check `geometry:` on the first card play. If hover shows no lift at an in-band mark, **stop and report**.

**C:** next consecutive comparable-start collect, same criteria.

## Stop condition

Stop if the fix requires prompt edits, treating window size as the bug, weakening identify-before-act, or deleting `runs/`.
