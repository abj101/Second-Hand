# Batch 5: Live validation

**Complexity: Low**
**Recommended agent:** orchestrator with user at the game
**Prerequisite:** Batches 1–4 complete and `tests/loop` green
**Product-code edits:** prohibited unless the orchestrator reopens a failed earlier batch.

## Objective

Demonstrate that perception→ACT gaps are closed in live STS2 play before any `skill_on` A/B.

## Safety and setup

- Preserve `runs/20260907T190633Z_sts2_collect` and `runs/20260907T191012Z_sts2_collect`.
- Use exactly: `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`.
- Step budget at least 20 for Run A.
- Remove only a stale stop file after verifying no collector process is active.
- Never curate/train during this batch.
- Use `scripts/monitor_sts2_loop.py` and `python -m loop inspect-run`.

## Runs

**Run A (tutorial-on, from title).** Pass: reaches combat and plays at least one card; zero `empty-action-surface` rows on transition frames; zero `decision_softlock`.

**Runs B and C (combat start)** per `bugfix/06-validation-runs.md` comparable-start rules. Two consecutive wins required. Zero stale-target dispatches. Loot recognized on the first stable post-victory frame.

For each run capture: start/end state, combat won, turns, wall time, intent counts, structured errors, repeated actions, stale dispatches, fresh vs cached LLM1, `router_reason` counts, `last_execution` effect distribution, artifacts complete.

## Pass criteria

- Run A: ≥1 `play_card`, no transition-frame `empty-action-surface`, no `decision_softlock`.
- Runs B+C: two consecutive combat wins.
- Zero stale-target dispatches.
- Loot recognized on first stable post-victory observation.
- Uncertain rows excluded from positive training exports.
- Graceful stop flushes expected artifacts.

## Failure routing

- Card/target empty after valid LLM1 → reopen Batch 2.
- Transition `empty-action-surface` / `decision_softlock` → reopen Batch 3.
- Map no-op / missing benches → reopen Batch 4.

Do not patch around a failure in this batch.

## Required report

Create `bugfix/perception2/validation-report.md` with one table row per run, pass/fail per criterion, remaining risks, and whether `skill_on` A/B may start.
