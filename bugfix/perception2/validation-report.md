# Perception V2 live validation

**Date:** 2026-09-07
**Command:** `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`
**Preserved baselines:** `runs/20260907T190633Z_sts2_collect`, `runs/20260907T191012Z_sts2_collect`

Game window was already on an opening Ironclad combat (80/80 HP, 3/3 energy, one 43/43 enemy, five-card hand, End Turn visible). That matches Batch 6 comparable-start, not a title tutorial start. Run B used that frame. Runs A and C were not launched.

`scripts/monitor_sts2_loop.py` crashed at step ~20 (`NameError: populated` in `stuck_reason`). Collect was stopped cooperatively after a target-id bind loop. `python -m loop inspect-run` ran on the flushed run.

## Summary table

| Run | Start | Dir | Steps | Combat won | `play_card` success | `empty-action-surface` | `decision_softlock` | Stale-target | Loot first stable | Pass |
|---|---|---|---|---|---|---|---|---|---|---|
| A title→combat | blocked: window already in combat | — | — | — | — | — | — | — | — | fail (not started) |
| B combat-start | 80/80, 3 energy, 1 enemy 43/43, 5 cards | `runs/20260907T200835Z_sts2_collect` | 28 | no (stopped mid-fight 68/80 vs 20/43, energy 2) | yes (Strike then Bash; energy 3→2→0) | **0** | **0** | **0** | n/a (no victory) | fail (no win) |
| C combat-start | not started: B did not finish a win; leftover fight is not a comparable opening turn | — | — | — | — | — | — | — | — | fail (not started) |

## Run B detail (`20260907T200835Z`)

Start/end: combat player-turn → still combat, HP 80→68, enemy 43→20, energy ended at 2. Wall ~7.9 min. Terminal `alive`. `flush_completed=true`.

Snapshot on step 1 already had five named cards (`Defend`, `Strike`, `Strike`, `Bash`, `Strike`) plus End Turn. That is the 191012Z gap closed: ACT no longer sees `cards=[]`.

Intent counts: `play_card` 27, `end_turn` 1. Structured errors: `target-not-found` 16, `source-unconfirmed` 7, `effect-unverified` 2. Verified combat effects: `energy_decreased` + `enemy_hp_decreased` on two rows; `card_left_hand` on seven.

LLM1: 26 fresh / 2 cached (`scene_cache`). Perception source: `forced_rescan` 25, `llm1` 1, `scene_cache` 2 (forced rescans follow inventory-inconsistent / unverified effect). `metrics.perception.transition` stayed false on logged steps; after End Turn (step 8) HUD dropped off and step 9 resumed with 14 marks — no empty-surface error, no softlock.

`inspect-run`: `sft_n=0`, `dpo_n=0` (uncertain / unverified rows excluded). Prompt id `sts2-action-intent-v2` only. Artifacts: `turns.jsonl`, `journal.json`, `memory_grounded.json`, `objects.db` (`PRAGMA quick_check=ok`, rollback ok), frames, `summary.json`.

Remaining live bind bug: anonymous/low-confidence targets often publish as store `object:N` (`object:10` on step 1) instead of `enemy_N`. Executor then logs `target-not-found:object:N`. When the snapshot used `enemy_1`, some plays landed. Do not treat `object:N` as a prompt id (`unknown_character_N` was not written as `object_id`).

## Criteria

| Criterion | Result |
|---|---|
| Run A ≥1 `play_card`, no transition empty-surface, no softlock | **fail** — A not started |
| B+C two consecutive combat wins | **fail** — B no win; C not started |
| Zero stale-target dispatches | **pass** on B (0) |
| Loot on first stable post-victory frame | **n/a** |
| Uncertain rows excluded from positive exports | **pass** (`sft_n=0`, `dpo_n=0`) |
| Graceful stop flushes artifacts | **pass** |

## Failure routing

- Empty combat inventory after valid LLM1 → **closed** on B (5 cards + End Turn on step 1).
- `object:N` / mixed `enemy_N` target ids → `target-not-found` loop → **reopen Phase 1 (D2)** so ACT snapshot `target_id` is the canonical `enemy_N` from `_prepare_frame_marks`, never a raw store `object:N`.
- Transition `empty-action-surface` / `decision_softlock` → **not seen** on B; A (title fade) still untested live.
- Map no-op / missing benches → **not exercised** live; CLI benches on fixtures: `legend_clean=true`, `legend_false_promotions=0`, `transfer_gate=pending`.

## Remaining risks

- Phantom tracker rows (`unknown_character_2` at a stale bbox) compete with the live Nibbit.
- `scripts/monitor_sts2_loop.py` is not usable until `populated` is defined.
- Title→combat (Run A) and two consecutive wins (B+C) still required before `skill_on` A/B.

## `skill_on` A/B

**Do not start.** Need a title-start run plus two consecutive `no_skill` combat wins after the `enemy_N` target-id bind is fixed.
