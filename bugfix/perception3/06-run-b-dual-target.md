# Batch 6: Run B dual-target + map softlock

**Complexity: Medium**
**Recommended agent:** orchestrator (`screen_objects.py`, `loop.py`)
**Prerequisite:** Batch 5 (`3b716a9`); live `runs/20260908T070054Z_sts2_collect` preserved
**Primary failure:** Batch 5 Run B won first combat but logged `target-not-found:enemy_2` ×2 and map `decision_softlock`.

## Objective

One visible hostile sprite → one snapshot target (`enemy_1`). TNF on a vanished rebound id is fail-closed (drop + replan), not a bind loop. Map/reward no-op clicks exclude that control and replan instead of softlocking a won fight.

No prompt edits. Do not alias `enemy_2` → `enemy_1` inside `resolve_planned_target`.

## What 20260908T070054Z showed

Batch 5 card-confirm **held**: `source-unconfirmed=0`, hover-lift, in-band `(0.312, 0.950)`, full `1277×720` box. Fight won (~step 14 reward).

| Defect | Evidence |
|---|---|
| **D5** HP/body stack not a 2e cluster | Step 1 marks `unidentified_enemy @750,347` vs `unknown_enemy @745,622`. `dx≈0.005`, `dy≈0.275` ≥ `SEPARATED_SPRITE_CENTER` 0.25. Host `enemies` often None → host==1 cap never fired. Snapshot listed `enemy_1`+`enemy_2`. ACT picked `enemy_2` → card confirmed → `_reacquire_current_target_surface` → `missing-stable-target`. `enemy_1` body survived (steps 2, 6, 8). Same on step 7 (both labeled Nibbit). |
| **D6** map softlock after loot | Steps 24–25 `click_control` on map nodes, `pixel_changed=False`. Step 26 empty ACT → `decision-softlock`. Batch 5 replan only covered combat `source-unconfirmed`. |

2e tests only covered nearby plates (centers ≤0.12). They never covered STS2 intent/HP at `y≈0.35` stacked over the body at `y≈0.62`.

## Owned files

- `loop/memory/screen_objects.py` — `same_sprite_cluster` column stack
- `loop/agent/loop.py` — `_blocked_targets`, `_blocked_controls`, replan branches

## Required behavior

### 6a — HP-over-body column stack

When `dist >= 0.25` and `host_enemies != 1`, still cluster if `|dx| <= SAME_SPRITE_COLUMN` (0.08) **and** one center in HP/intent band (`ENEMY_Y_MIN`–`ENEMY_Y_MAX`) and the other in body band (`ENEMY_Y_MAX`–`HAND_Y`). Do not bump `SEPARATED_SPRITE_CENTER`. Side-by-side hostiles (`dx≈0.27`, same y) stay two.

### 6b — TNF fail-closed

On `missing-stable-target` / `target-not-found:enemy_N`, record `_blocked_targets`. Snapshot omits those ids. Combat + remaining `play_card`/`end_turn` → replan (`target-not-found` focus), not softlock. Clear on combat exit, host enemy-count change, episode reset.

### 6c — Map replan

No-pixel `click_control` records `_blocked_controls` and omits that id from the next snapshot. Map/reward + remaining click_control ids → replan, not `decision_softlock`. Softlock only when no legal intent remains. Do not auto-stop after loot.

## Tests

- Synthetic HP `y=0.35` + body `y=0.62` without host `enemies` → one `enemy_1`
- Replay-shaped 20260908T070054Z step 1/7 marks (Nibbit stack) → one target; blocked `enemy_2` rejected by `validate_action_intent`
- `test_two_separated_enemies_stay_two` still green
- Blocked target dropped from snapshot; host-count change clears the block
- Map two no-ops with a second node → no `decision_softlock`

## Gate

`python -m pytest tests/loop -q` ≥ 824. One commit. Then operator B and C from a fresh first combat turn.

## Live (operator, after commit)

Preserve all listed `runs/`. No `skill_on` / curate / train.

```
python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu
python scripts/monitor_sts2_loop.py --run <dir> --poll-s 20
```

**B pass:** fight won, TNF=0, `source-unconfirmed < 3`, no `decision_softlock`, loot on first stable reward frame. Dual `enemy_1`+`enemy_2` still publishing → **stop and report**.

**C:** next consecutive comparable start, same criteria.
