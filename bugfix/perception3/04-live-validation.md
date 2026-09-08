# Batch 4: Live validation

**Complexity: Low (product), High (coordination)**
**Recommended agent:** orchestrator with user at the game
**Prerequisite:** Batches 1–3 committed, `tests/loop` green
**Product-code edits:** prohibited; failures route back to the batch named below.

## Objective

Prove the V3 identity fix live: a title→combat run that plays a card, then two consecutive combat-start wins, all with zero `target-not-found`.

## Setup (operator)

- Preserve `runs/20260907T190633Z_sts2_collect`, `runs/20260907T191012Z_sts2_collect`, `runs/20260907T200835Z_sts2_collect`.
- Command, unchanged: `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`.
- Run `python scripts/monitor_sts2_loop.py` in a second terminal for every run.
- Before Run A the game **must** be on the title screen (abandon the current run in-game). V2 skipped Run A because the window was already in combat; do not repeat that.
- Runs B and C: start a fresh Ironclad run, advance to the first combat's first player turn (80/80, 3 energy, 5 cards), then launch collect. Comparable-start rules in `bugfix/06-validation-runs.md`.
- Step budget: ≥ 20 for Run A; ≥ 40 for B and C (a full first fight is ~6–10 player actions plus End Turns).
- Remove a stale stop file only after confirming no collector process is active.
- Never curate/train during this batch.

## Runs and pass criteria

**Run A (title→combat).** ≥1 `play_card` with `effect_verified=true`; zero `empty-action-surface` on transition frames; zero `decision_softlock`; zero `target-not-found`.

**Run B (combat start).** Combat won; `target-not-found = 0`; `noncanonical_target_ids = 0`; `duplicate_target_bboxes = 0`; loot recognized on the first stable post-victory frame.

**Run C (combat start).** Same as B; must be the run immediately after B (consecutive).

All runs: `confidence_out_of_range = 0`; `forced_rescan / steps ≤ 0.3`; uncertain rows excluded from `sft`/`dpo` exports; cooperative stop flushes `turns.jsonl`, `journal.json`, `memory_grounded.json`, `objects.db`, frames, `summary.json`.

For each run record: start/end HUD, steps, wall time, intent counts, structured-error counts, `router_reason` histogram, `perception_source` histogram, fresh vs cached LLM1, `inspect-run` id-hygiene fields, artifacts complete.

## Failure routing

| Observation | Route |
|---|---|
| Any `object:N` / `mark_N` in `card_id` / `target_id` | reopen Batch 1 |
| More targets than visible enemies; `identity_confidence > 1`; `epistemic-blocker` on an anchored single enemy | reopen Batch 2 |
| `target-not-found:enemy_N` with dual snapshot `enemy_1`+`enemy_2` | Batch 7 (`07-dual-snapshot-and-reward.md`) |
| Dual `enemy_*` published for one sprite, no TNF | Batch 7 (publish-path collapse) |
| In-combat TNF after collapse (rebound miss) | Batch 6 fail-closed |
| `source-unconfirmed:card_N` ≥ 3 in one run | Batch 5 (`05-card-confirm.md`) |
| In-combat `decision_softlock` with energy > 0 | Batch 5 (replan vs fail-closed) |
| Map/reward `decision_softlock` after loot, surface non-empty | Batch 6 control replan |
| Map/reward `decision_softlock` after loot, surface **empty** | Batch 7 (`_recover_loot_surface`) |
| Transition `empty-action-surface` | reopen V2 Batch 3 |
| Monitor or `inspect-run` crash | reopen Batch 3 |

Do not patch around a failure inside this batch.

## Required report

`bugfix/perception3/validation-report.md`: one row per run, pass/fail per criterion, `inspect-run` hygiene fields, remaining risks, and an explicit `skill_on` A/B decision. `skill_on` may start only when A, B, and C all pass.
