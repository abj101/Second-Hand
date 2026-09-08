# Batch 6: Live Validation and V1 Gate

**Complexity: Low**  
**Recommended agent:** economical execution/analysis tier with game UI access  
**Prerequisites:** Batches 1–5C complete and full `tests/loop` suite green  
**Product-code edits:** prohibited unless the orchestrator explicitly reopens a failed earlier batch.

## Objective

Demonstrate that fixes improved the `no_skill` harness before introducing skill behavior.

## Safety and setup

- Preserve `runs/20260906T021305Z_sts2_collect` as the baseline.
- Start with Slay the Spire 2 focused at the beginning of a normal player combat turn, with no modal open. Record HP, energy, enemy count, and screenshot before launch.
- Use exactly: `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`.
- Remove only a stale stop file after verifying no collector process is active.
- Stop after the first confirmed combat reward screen.
- Never curate/train during this batch.

## Runs

Perform at least three comparable `no_skill` runs. Two consecutive wins are required; three runs provide only a smoke/reliability signal. Comparable means the same game build, character, tutorial setting, one enemy, and an ordinary three-energy opening player turn. Abort and restart a sample if it begins on a map, reward, modal, enemy turn, or different combat configuration.

A stable observation is three consecutive capture polls over at least 400 ms with no screen classification change and normalized frame MAD <= 0.02. For database closure, open `objects.db` after process exit, run `PRAGMA quick_check`, then perform a transaction rollback without lock error.

For each run capture:

- start and end state;
- combat won;
- turns and wall time;
- intent counts;
- each structured execution error;
- repeated identical action count;
- stale-target dispatch count;
- source-unconfirmed count;
- explicit MCP tool calls;
- fresh versus cached LLM1 calls;
- perception/OCR/reason/action timing distributions;
- first stable post-kill screen classification;
- journal fact/binding/slot counts;
- contradictory identities;
- malformed lessons;
- SFT/DPO eligible and excluded row counts with reasons;
- presence of final row, `journal.json`, `memory_grounded.json`, and closed/readable `objects.db`.

## Pass criteria

- Two consecutive combat wins.
- Zero stale-target dispatches.
- Zero repeated clicks on a twice-blocked unchanged control.
- At most one source-unconfirmed event per combat and no same-coordinate immediate retry.
- Loot recognized on the first stable post-victory observation.
- No stale combat objects on Loot.
- No contradictory typed memory identities.
- No malformed lesson text.
- All uncertain rows excluded from positive training exports.
- Graceful stop flushes all expected artifacts.

## Failure routing

- Card/source/target failure → reopen Batch 1.
- Hallucinated or repeated control → reopen Batch 2.
- Delayed Loot/stale combat state → reopen Batch 3.
- Bad memory or unsafe export → reopen Batch 4.
- Telemetry mismatch → reopen Batch 5A; missing/unclosed artifacts → Batch 5B; OCR/cache regression → Batch 5C.

Do not patch around a failure in this validation batch.

## Required report

Create `bugfix/validation-report.md` with:

1. baseline metrics;
2. one section per fixed run;
3. aggregate comparison;
4. pass/fail for every criterion;
5. remaining risks;
6. recommendation on whether to begin a separate `skill_on` A/B test.

If this batch passes, the system qualifies as a reliable combat-level V1. It does not yet establish full-run or game-agnostic mastery.

Use one table row per run with columns: run ID, comparable, won, turns, wall time, structured errors by category, repeated actions, stale dispatches, first post-kill screen, MCP calls, LLM1 fresh/cache counts, journal contradictions, malformed lessons, export eligible/excluded, artifacts complete, and notes. Follow with an aggregate pass/fail checklist matching every criterion above.
