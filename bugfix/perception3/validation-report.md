# Perception V3 live validation

**Date:** 2026-09-07
**Command:** `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`
**Preserved baselines:** `runs/20260907T190633Z_sts2_collect`, `runs/20260907T191012Z_sts2_collect`, `runs/20260907T200835Z_sts2_collect`

Game was on the title screen. Run A launched from that frame. Cooperative stop after first-combat loot (`screen=reward`). Runs B and C not started.

`scripts/monitor_sts2_loop.py --run runs/20260907T212107Z_sts2_collect --poll-s 20` completed without crash (`run ended phase=stopped`). `python -m loop inspect-run` ran on the flushed run.

## Summary table

| Run | Start | Dir | Steps | Combat won | `play_card` verified | `empty-action-surface` | `decision_softlock` | Stale-target | Loot first stable | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| A title→combat | title → Singleplayer | `runs/20260907T212107Z_sts2_collect` | 42 | yes (reward) | **12** | **0** | **0** | **0** `object:N` | **yes** (step 40 `screen=reward`) | **partial** — TNF `enemy_N` ×4 |
| B combat-start | not started | — | — | — | — | — | — | — | — | fail (not started) |
| C combat-start | not started | — | — | — | — | — | — | — | — | fail (not started) |

## Run A detail (`20260907T212107Z`)

Wall ~13.2 min. Terminal `alive`. `flush_completed=true`. `stop_requested=true`.

Path: title `click_control:singleplayer` → select → tutorial Next → combat. Step 11 entered combat (80/80, 3 energy). Step 12 Bash `target_id=enemy_1` landed (energy 3→1). Enemy 43→1 across the fight. Step 40 clicked "Add a card to your deck" → first stable reward frame.

Intent counts: `play_card` 21, `click_control` 16, `end_turn` 3. Structured errors: `target-not-found` 4 (`enemy_2` ×3, `enemy_1` ×1), `source-unconfirmed:card_1` 2, `effect-unverified` 3, `inventory-inconsistent` 1. Zero `empty-action-surface`, zero `decision_softlock`, zero `object:N` / `mark_N` ids.

Duplicate live snapshot: several combat steps published both `enemy_1` and `enemy_2` as Nibbit. ACT then bound `enemy_2` → `target-not-found:enemy_2`. When the snapshot had a single `enemy_1`, Bash/Strike usually landed.

LLM1 / perception: `forced_rescan` 24, `llm1` 12, `ocr_cache` 6 (`forced_rescan/steps=0.57`, above the 0.30 gate). `router_reason` histogram: `confirmed-action-surface` 42 (no `epistemic-blocker`).

`inspect-run`: `noncanonical_target_ids=0`, `duplicate_target_bboxes=0` (logged snapshots often omit bbox so IoU auditor is blind), `confidence_out_of_range=0`, `sft_n=6`, `dpo_n=0`. Artifacts: `turns.jsonl`, `journal.json`, `memory_grounded.json`, `objects.db`, `frames/`, `summary.json`.

## Criteria

| Criterion | Result |
|---|---|
| Run A ≥1 `play_card` with `effect_verified=true` | **pass** (12) |
| Run A no transition `empty-action-surface` | **pass** (0) |
| Run A no `decision_softlock` | **pass** (0) |
| Run A zero `target-not-found` | **fail** (4) |
| B+C two consecutive combat wins | **fail** — not started |
| Zero stale-target / `object:N` dispatches | **pass** |
| Loot on first stable post-victory frame | **pass** (step 40 reward) |
| `confidence_out_of_range = 0` | **pass** |
| `forced_rescan / steps ≤ 0.3` | **fail** (24/42) |
| Uncertain rows excluded from positive exports | **pass** (`dpo_n=0`; SFT only verified rows) |
| Graceful stop flushes artifacts | **pass** |

## Failure routing

- `object:N` / `mark_N` in `card_id`/`target_id` → **closed** on this run (`noncanonical_target_ids=0`).
- Two snapshot targets for one Nibbit (`enemy_1`+`enemy_2`) → **reopen Batch 2** (more targets than visible enemies). Live Bind then logs `target-not-found:enemy_2`.
- `target-not-found:enemy_1` while `enemy_1` is in the snapshot (step 17) → **Batch 5** (store re-saved between snapshot and dispatch).
- `source-unconfirmed:card_1` ×2 → under the Batch 5 threshold of 3; still a hover/confirm risk.
- Transition empty-surface / softlock → **not seen**.
- Monitor / inspect-run crash → **not seen**.

## Remaining risks

- Canonical ids still fork (`enemy_2` phantom) on some combat frames even after Batch 1 freeze replays.
- Forced rescan after every combat transaction (unverified effect / inventory-inconsistent) keeps LLM1 cost high.
- `inspect-run` `duplicate_target_bboxes` is 0 because many `turn_snapshot.targets` rows have no bbox.

## `skill_on` A/B

**Do not start.** Need Runs B+C consecutive combat-start wins with zero `target-not-found` after the duplicate-`enemy_N` bind is closed.

## Next operator step

Window is on the post-combat **reward** screen, not a comparable opening turn. For Run B: abandon this run in-game, start a fresh Ironclad run, stop on the first combat's first player turn (80/80, 3 energy, 5 cards), then say go.
