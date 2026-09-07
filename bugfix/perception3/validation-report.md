# Perception V3 live validation

**Date:** 2026-09-07
**Command:** `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`
**Preserved baselines:** `runs/20260907T190633Z_sts2_collect`, `runs/20260907T191012Z_sts2_collect`, `runs/20260907T200835Z_sts2_collect`

`scripts/monitor_sts2_loop.py` completed without crash on both live dirs. `python -m loop inspect-run` ran on flushed runs.

## Summary table

| Run | Start | Dir | Steps | Combat won | `play_card` verified | `empty-action-surface` | `decision_softlock` | Stale-target | Loot first stable | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| A title→combat | title → Singleplayer | `runs/20260907T212107Z_sts2_collect` | 42 | yes (reward) | **12** | **0** | **0** | **0** `object:N` | **yes** (step 40 `screen=reward`) | **partial** — TNF `enemy_N` ×4 |
| B combat-start | first combat 80/80 3 energy | `runs/20260907T225552Z_sts2_collect` | 21 | yes (reward) | **8** | **0** | **0** | **0** `object:N` | **yes** (step 20 `screen=reward`) | **partial** — TNF `enemy_2` ×2 |
| C combat-start | not started | — | — | — | — | — | — | — | — | fail (blocked: B TNF) |

## Run A detail (`20260907T212107Z`)

Wall ~13.2 min. Terminal `alive`. `flush_completed=true`. `stop_requested=true`.

Path: title `click_control:singleplayer` → select → tutorial Next → combat. Step 11 entered combat (80/80, 3 energy). Step 12 Bash `target_id=enemy_1` landed (energy 3→1). Enemy 43→1 across the fight. Step 40 clicked "Add a card to your deck" → first stable reward frame.

Intent counts: `play_card` 21, `click_control` 16, `end_turn` 3. Structured errors: `target-not-found` 4 (`enemy_2` ×3, `enemy_1` ×1), `source-unconfirmed:card_1` 2, `effect-unverified` 3, `inventory-inconsistent` 1. Zero `empty-action-surface`, zero `decision_softlock`, zero `object:N` / `mark_N` ids.

Duplicate live snapshot: several combat steps published both `enemy_1` and `enemy_2` as Nibbit. ACT then bound `enemy_2` → `target-not-found:enemy_2`. When the snapshot had a single `enemy_1`, Bash/Strike usually landed.

LLM1 / perception: `forced_rescan` 24, `llm1` 12, `ocr_cache` 6 (`forced_rescan/steps=0.57`, above the 0.30 gate). `router_reason` histogram: `confirmed-action-surface` 42 (no `epistemic-blocker`).

`inspect-run`: `noncanonical_target_ids=0`, `duplicate_target_bboxes=0` (logged snapshots often omit bbox so IoU auditor is blind), `confidence_out_of_range=0`, `sft_n=6`, `dpo_n=0`. Artifacts: `turns.jsonl`, `journal.json`, `memory_grounded.json`, `objects.db`, `frames/`, `summary.json`.

## Run B detail (`20260907T225552Z`)

Wall ~6.8 min. Terminal `alive`. `flush_completed=true`. `stop_requested=true`. Monitor: `run ended phase=stopped`, exit 0.

Comparable start: first player turn, `hp=80/80`, energy 3, `screen=combat`. Step 1 Bash `target_id=enemy_1` landed (energy 3→1). Host enemy ~43 HP. Cooperative stop after loot; step 21 Skip → `screen=map`.

Intent counts: `play_card` 15, `end_turn` 3, `click_control` 2. Card txs: success 8, auto_resolved 1, `target_not_found` 2, `source_unconfirmed` 2, `effect_unverified` 2. Structured errors: `target-not-found:enemy_2` ×2 (steps 2, 17), `source-unconfirmed:card_1` ×2 (steps 11–12), `effect-unverified` ×2, `inventory-inconsistent` ×1 (step 19, victory transition). Zero `empty-action-surface`, zero `decision_softlock`, zero `object:N` / `mark_N` ids.

Duplicate live snapshot: **11/21** steps published both `enemy_1` and `enemy_2` (unknown enemy / Nibbit). ACT bound `enemy_2` on steps 2 and 17 → `missing-stable-target`. `enemy_1` binds landed.

LLM1 / perception: `forced_rescan` 19, `llm1` 2 (`forced_rescan/steps=0.90`, above the 0.30 gate). `router_reason`: `confirmed-action-surface` 21 (no `epistemic-blocker`).

`inspect-run`: `noncanonical_target_ids=0`, `duplicate_target_bboxes=0`, `confidence_out_of_range=0`, `sft_n=0`, `dpo_n=0`. Artifacts: `turns.jsonl`, `journal.json`, `memory_grounded.json`, `objects.db`, `frames/`, `summary.json`.

## Criteria

| Criterion | Result |
|---|---|
| Run A ≥1 `play_card` with `effect_verified=true` | **pass** (12) |
| Run A no transition `empty-action-surface` | **pass** (0) |
| Run A no `decision_softlock` | **pass** (0) |
| Run A zero `target-not-found` | **fail** (4) |
| Run B combat-start win, zero TNF | **fail** — won, TNF `enemy_2` ×2 |
| B+C two consecutive combat wins | **fail** — C blocked |
| Zero stale-target / `object:N` dispatches | **pass** |
| Loot on first stable post-victory frame | **pass** (A step 40, B step 20) |
| `confidence_out_of_range = 0` | **pass** |
| `forced_rescan / steps ≤ 0.3` | **fail** (A 24/42, B 19/21) |
| Uncertain rows excluded from positive exports | **pass** (`dpo_n=0`) |
| Graceful stop flushes artifacts | **pass** |

## Failure routing

- `object:N` / `mark_N` in `card_id`/`target_id` → **closed** (A+B `noncanonical_target_ids=0`).
- Two snapshot targets for one Nibbit (`enemy_1`+`enemy_2`) → **reopen Batch 2**. Live bind then logs `target-not-found:enemy_2` (A ×3, B ×2). B: 11/21 steps dual-published.
- `target-not-found:enemy_1` while `enemy_1` is in the snapshot (A step 17) → **Batch 5**. Not seen on B.
- `source-unconfirmed:card_1` ×2 on both A and B → under the Batch 5 threshold of 3; still a hover/confirm risk.
- Transition empty-surface / softlock → **not seen**.
- Monitor / inspect-run crash → **not seen**.

## Remaining risks

- Canonical ids still fork (`enemy_2` phantom) on many combat frames even after Batch 1 freeze replays.
- Forced rescan after every combat transaction keeps LLM1 cost high.
- `inspect-run` `duplicate_target_bboxes` is 0 because many `turn_snapshot.targets` rows have no bbox.
- B `sft_n=0` vs A `sft_n=6` — short episode + cooperative stop; not a hygiene fail.

## `skill_on` A/B

**Do not start.** Need Runs B+C consecutive combat-start wins with zero `target-not-found` after the duplicate-`enemy_N` bind is closed.

## Batch 2 reopen (2e)

Landed after Run B. Cluster collapse: IoU ≥ 0.20 or center ≤ 0.12 → one `enemy_*`. Far sprites (≥ 0.25) stay two. Snapshot last-defense + cache merge skip. `tests/loop` 809 passed.

## Next operator step

Window is on the **map** (Run B skipped remaining loot). Do **not** treat leftover floor as Run C.

Patch is in. Abandon this Ironclad run, start a fresh one, stop on first combat's first player turn (80/80, 3 energy, 5 cards), then say go. That collect is the new Run B (TNF must be 0).
