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
| B2 combat-start (2e) | first combat 80/80 3 energy | `runs/20260908T001121Z_sts2_collect` | 22 | no (monitor stop) | **5** | **0** | **0** | **0** `object:N` | no | **partial** — TNF **0**; `source-unconfirmed:card_1` ×12 |
| B3 combat-start | first combat 80/80 3 energy | `runs/20260908T034711Z_sts2_collect` | 6 | no | **0** | **0** | **1** | **0** `object:N` | no | **fail** — `source-unconfirmed` + `decision_softlock`; window 1915×1080 |
| B4 retry | first combat 80/80 3 energy | `runs/20260908T040110Z_sts2_collect` | 4 | no | **0** | **0** | **1** | **0** `object:N` | no | **fail** — same: `card_1` unconfirmed → empty intent → softlock |
| C combat-start | not started | — | — | — | — | — | — | — | — | fail |

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

## Run B2 detail (`20260908T001121Z`, post-2e)

Wall ~6.3 min. Terminal `alive`. `flush_completed=true`. `stop_requested=true` (monitor HUD-frozen, not operator).

Comparable start: 80/80, energy 3, `screen=combat`. Step 1 Strike `target_id=enemy_1` landed (energy 3→2). All ACT binds used `enemy_1`. **Zero `target-not-found`.** Dual snapshot `enemy_1`+`enemy_2` still on 7/22 steps, but ACT did not bind `enemy_2`.

Card txs: success 5, auto_resolved 1, effect_unverified 1, `source_unconfirmed` 12 (`card_1` at x≈0.09 — left-edge miss). Monitor: HUD frozen hp=72 for 12 steps → stop after stuck 3/3. Step 21 then landed Strike on `mk_5` (x=0.37, energy 1→0, enemy 15→6). Step 22 End Turn. Fight unfinished.

`inspect-run`: `noncanonical_target_ids=0`, `confidence_out_of_range=0`, `forced_rescan` 21/22, `sft_n=0`, `dpo_n=0`. Monitor exit 0.

## Criteria

| Criterion | Result |
|---|---|
| Run A ≥1 `play_card` with `effect_verified=true` | **pass** (12) |
| Run A no transition `empty-action-surface` | **pass** (0) |
| Run A no `decision_softlock` | **pass** (0) |
| Run A zero `target-not-found` | **fail** (4) |
| Run B combat-start win, zero TNF | **fail** — won, TNF `enemy_2` ×2 (pre-2e) |
| Run B2 (2e) zero TNF | **pass** (0) |
| Run B2 combat win | **fail** — monitor HUD-frozen during card bind loop |
| B+C two consecutive combat wins | **fail** |
| Zero stale-target / `object:N` dispatches | **pass** |
| `confidence_out_of_range = 0` | **pass** |
| `forced_rescan / steps ≤ 0.3` | **fail** |
| Graceful stop flushes artifacts | **pass** |

## Failure routing

- `object:N` / `mark_N` → **closed**.
- Dual snapshot `enemy_1`+`enemy_2` → **reduced** (TNF 0 on B2) but still published 7/22 steps. Residual, not TNF.
- `source-unconfirmed:card_1` ×12 → **Batch 5 landed** (hand-band gate + blocked-retry + replan). Live B/C still pending.
- In-combat `decision_softlock` (B3/B4) → **Batch 5** (replan when energy>0 and legal intents remain).
- Transition empty-surface / non-combat softlock → **not seen**.

## Remaining risks

- Snapshot can still list two `enemy_*` for one Nibbit; ACT happened to pick `enemy_1`.
- Card hover/confirm on `card_1` loops until monitor kills the episode.
- Forced rescan almost every step.

## `skill_on` A/B

**Do not start.** Need two consecutive combat-start wins with zero TNF after the card bind loop is closed.

## Batch 2 reopen (2e)

Landed `d0cbfff`. Live B2: TNF 0. Dual rows still appear in some snapshots.

## Next operator step

B3 (`20260908T034711Z`) died in 6 steps: `source-unconfirmed:card_1` then `decision_softlock`. Client was **1915×1080** with content box 0.67 (prior wins used **1277×720** fill). TNF still 0.

Do **not** treat leftover combat as Run C.

## Batch 5 (card-confirm) — code landed, live pending

Product: hand-band gate, `_blocked_sources` keyed on object+coord (survives snapshot invalidation), cropped-vs-cropped `mid_mad` + raw `_carry_after`, blocked cards `playable=False`, replan instead of in-combat softlock, V2 hover-first probe, `geometry:` evidence, monitor skips HUD-frozen stop during unconfirmed bind loop.

`tests/loop`: **824 passed**.

| Run | Start | Dir | Steps | Combat won | `source-unconfirmed` | `decision_softlock` | TNF | Pass |
|---|---|---|---|---|---|---|---|---|
| B5-B combat-start | first combat 80/80 3 energy | `runs/20260908T070054Z_sts2_collect` | 26 | yes (reward ~14) | **0** | **1** (map) | **2** `enemy_2` | **partial** — card-confirm pass; TNF+map fail |
| B5-C combat-start | not started | — | — | — | — | — | — | pending |

Card-confirm on B5-B: hover-lift, in-band `(0.312, 0.950)`, `geometry:client=1277x720 box=0.00,0.00,1.00,1.00`. Dual snapshot `enemy_1`+`enemy_2` (HP y≈0.35 over body y≈0.62). Map `click_control` no-ops → `decision_softlock` step 26.

## Batch 6 (dual-target + map) — code landed, live pending

Product: HP/body column stack in `same_sprite_cluster`; `_blocked_targets` drop + TNF replan; `_blocked_controls` omit + map replan.

`tests/loop`: **829 passed**.

Operator: fresh Ironclad, first combat, first player turn (80/80, 3 energy, 5 cards). Same collect + monitor. Pass = fight won, TNF=0, `source-unconfirmed < 3`, no `decision_softlock`, loot on first stable reward. Dual `enemy_1`+`enemy_2` still publishing → stop and report.
