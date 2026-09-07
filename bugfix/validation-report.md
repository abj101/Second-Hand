# Batch 6: Live Validation Report

**Status:** FAIL — `skill_on` remains **unauthorized**. This report recomputes accounting only from immutable `turns.jsonl`, `summary.json`, `journal.json`, and finalization artifacts. No raw runs, databases, exports, or product behavior were changed.

**Command family:** `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`
**Baseline preserved:** `runs/20260906T021305Z_sts2_collect` (untouched)
**Accounting rule:** `target-not-found` with `target_dispatched=false` is a target-resolution reject. A stale target dispatch requires an actual target primitive sent from an invalid snapshot. A source-only card transaction is partial; effect verification is separately counted. Timeouts, operator stops, crashes, and continuation-only probes are inconclusive unless the raw artifacts record a completed episode win or loss.

## Handoff

The historical `060636`, `061312`, `062024`, and `063400` samples are comparable fresh-combat attempts, but all ended inconclusively with target resolution blocked. Their historical 22–24 `target-not-found` events are **resolution rejects**, not stale dispatches: `target_dispatched` is zero in every such transaction.

The latest diagnostics are not additional comparable clean samples:

- `20260907T003032Z_sts2_collect` continued an existing combat. It dispatched five freshly rebound targets and verified four card effects, then reached a Loot transition after one effect-unverified card transaction; collection terminated at the subsequent Loot-frame `EncodedFrame`/inventory-consistency failure. It has no finalized episode or recorded win.
- `20260907T003540Z_sts2_collect` started on Loot, found an empty reward action surface, and rejected both steps before dispatch. It is inconclusive.
- `20260907T003920Z_sts2_collect` started on Loot with OCR text for `Skip`, but no bindable control entered the legal action surface; the step was rejected before dispatch. It is inconclusive.
- `20260907T004340Z_sts2_collect` started on Loot after the reward-surface fix. A current-frame OCR-grounded `control:skip` was dispatched and the screen changed. It records `combat_won=true` on that one reward frame, but the run is a one-step continuation with `step_budget_timeout`, not a completed comparable combat episode.

Do not continue a partial session as a validation sample. The next qualification run must begin with a fresh ordinary player combat turn.

## Recomputed transaction evidence

| Run | Outcome | Target-resolution rejects | Source primitives | Target primitives | Partial card transactions | Verified card effects | Stale target dispatches |
|---|---|---:|---:|---:|---:|---:|---:|
| `20260906T021305Z` baseline | completed win (Loot step 29) | 2 | 19 | 8 | 11 | 6 | 0 |
| `20260906T060636Z` | inconclusive (timeout) | 22 | 24 | 0 | 24 | 0 | 0 |
| `20260906T061312Z` | inconclusive (timeout) | 23 | 24 | 0 | 24 | 0 | 0 |
| `20260906T062024Z` | inconclusive (timeout) | 23 | 24 | 0 | 24 | 0 | 0 |
| `20260906T063400Z` | inconclusive (timeout) | 24 | 24 | 0 | 24 | 0 | 0 |
| `20260906T064037Z` | inconclusive (operator-stopped) | 10 | 11 | 0 | 11 | 1 | 0 |
| `20260907T003032Z` | inconclusive (mid-combat continuation; Loot then `EncodedFrame` failure) | 0 | 5 | 5 | 0 | 4 | 0 |
| `20260907T003540Z` | inconclusive (Loot continuation; empty reward surface) | 0 | 0 | 0 | 0 | 0 | 0 |
| `20260907T003920Z` | inconclusive (Loot continuation; `Skip` unbindable) | 0 | 0 | 0 | 0 | 0 | 0 |
| `20260907T004340Z` | inconclusive (Loot continuation; one-step reward probe) | 0 | 0 | 0 | 0 | 0 | 0 |

The baseline's two `target-not-found` transactions also have `target_dispatched=false`; they are resolution rejects, not stale dispatches. Across all listed artifacts there is no target primitive sent from an invalid/stale snapshot, so stale-target dispatches are zero. The five targets in `003032` are explicitly fresh rebounds (`target_rebound=true`), not stale input.

## Per-run validation table

| Run ID | Comparable | Recomputed outcome | Steps | Structured errors | Target / source / effect evidence | Combat-win evidence | Reward-surface evidence | Artifacts | Notes |
|---|---|---|---:|---|---|---|---|---|---|
| `20260906T021305Z` baseline | reference | completed win | 29 | source-unconfirmed: 9; target-not-found: 2; effect-unverified: 2; inventory-inconsistent: 1 | 8 / 19 / 6 | completed win, Loot step 29 | Loot | partial | Pre-5B: no `memory_grounded.json` or `summary.json`. |
| `20260906T060636Z` | yes | inconclusive | 24 | target-not-found: 22; source-unconfirmed: 2 | 0 / 24 / 0 | none | none | complete | `hud_identity_stuck`; legacy `success=true` is not a win. |
| `20260906T061312Z` | yes | inconclusive | 24 | target-not-found: 23; source-unconfirmed: 1 | 0 / 24 / 0 | none | none | complete | `hud_identity_stuck`; legacy `success=true` is not a win. |
| `20260906T062024Z` | yes | inconclusive | 24 | target-not-found: 23; source-unconfirmed: 1 | 0 / 24 / 0 | none | none | complete | `hud_identity_stuck`; legacy `success=true` is not a win. |
| `20260906T063400Z` | yes | inconclusive | 24 | target-not-found: 24 (19 `unknown_character_1`, 5 `enemy_1`) | 0 / 24 / 0 | none | none | complete | Fail-closed: energy stayed 3; `flush_completed=true`. |
| `20260906T064037Z` | no — stopped | inconclusive | 12 | target-not-found: 10; action-surface-invariant: 1 | 0 / 11 / 1 | none | none | incomplete | Operator stop; no summary or grounded-memory artifact. |
| `20260907T003032Z` | no — continued combat | inconclusive | 9 | effect-unverified: 1; inventory-inconsistent: 1 | 5 / 5 / 4 | Loot observed after step 8, but no recorded completed win | Loot then failure | summary finalized; 0 episodes | Fresh target rebound on all five card actions; step 9 rejected pre-dispatch. |
| `20260907T003540Z` | no — Loot continuation | inconclusive | 2 | inventory-inconsistent: 1; empty-action-surface: 1 | 0 / 0 / 0 | none | Loot OCR, no bindable action surface | complete | Both steps rejected pre-dispatch. |
| `20260907T003920Z` | no — Loot continuation | inconclusive | 1 | inventory-inconsistent: 1 | 0 / 0 / 0 | none | OCR `Skip`, no legal/bindable surface | complete | Typed action unavailable; rejected pre-dispatch. |
| `20260907T004340Z` | no — Loot continuation | inconclusive | 1 | none | 0 / 0 / 0 | `combat_won=true` transition only; summary wins 0 | Current-frame `Skip` dispatched; pixel changed | complete | One-step reward probe; `step_budget_timeout`. |

`Target / source / effect` is `target primitives / source primitives / verified card effects`. The reward `Skip` action in `004340` is an action primitive, not a card source or target primitive.

## Aggregate comparison

| Metric | Baseline | Comparable fresh-combat Batch 6 (`060636`, `061312`, `062024`, `063400`) | Latest diagnostics (`003032`, `003540`, `003920`, `004340`) |
|---|---:|---:|---:|
| Completed episode wins / losses / inconclusive | 1 / 0 / 0 | 0 / 0 / 4 | 0 / 0 / 4 |
| Target-resolution rejects | 2 | 92 | 0 |
| Source-only partial transactions | 11 | 94 | 0 |
| Target primitives dispatched | 8 | 0 | 5 |
| Verified card effects | 6 | 0 | 4 |
| Stale target dispatches | 0 | 0 | 0 |
| Loot/reward observed | yes | no | yes, all four continuations |

The earlier 22–24 “stale dispatches” per post-fix run were incorrectly labeled: all 92 are target-resolution rejects. The new continued-combat diagnostic verifies that fresh target dispatch and effect verification now occur (5 and 4 respectively), but it cannot establish clean combat reliability or a completed win.

## Gate checklist

| Criterion | Result | Evidence |
|---|---|---|
| At least three comparable clean samples | **FAIL** | Four prior comparable samples exist, but zero are clean; the four latest runs are continuations/probes, not qualifying samples. |
| Two consecutive completed combat wins | **FAIL** | Zero completed post-fix wins. `004340` has only a one-frame `combat_won` transition and summary wins remain 0. |
| Zero stale-target dispatches | **PASS (safety only)** | Zero stale target primitives in every listed raw artifact. |
| Fresh target dispatch and verified effect before qualification samples | **OBSERVED, not sufficient** | `003032`: 5 fresh target primitives and 4 verified effects in a continued combat. |
| No repeated clicks on a twice-blocked unchanged control | **PASS** | No repeated blocked control click recorded in the validation artifacts. |
| Loot on first stable post-victory observation | **NOT QUALIFIED** | Loot is observed in continuations, but no fresh completed post-fix episode demonstrates the criterion. |
| No stale combat objects on Loot | **NOT QUALIFIED** | `003032` reaches Loot then fails during encoding/inventory consistency; reward probes do not prove a clean transition. |
| Typed reward control is bindable and dispatches | **OBSERVED, not sufficient** | `004340` dispatches OCR-grounded `control:skip` and changes the screen; prior `003540`/`003920` failures remain inconclusive probes. |
| Complete finalization writes artifacts | **PASS where finalized** | `060636`, `061312`, `062024`, `063400`, `003540`, `003920`, and `004340` finalized; `003032` has finalized summary but zero episodes after its crash path. |

**Overall:** FAIL. `skill_on` is not authorized. Safety accounting is clean (zero stale-target dispatches), and the new diagnostic proves some fresh target/effect behavior, but the required three comparable clean samples and two consecutive completed combat wins have not passed.

## Remaining live validation

1. Start a new ordinary player combat from a fresh session; do not reuse the current Loot/partial-combat state.
2. Run at least three comparable `no_skill` samples. For each successful sample, require fresh target dispatch and verified effect evidence, clean transition to the first stable Loot observation, and complete finalization.
3. Require two consecutive completed combat wins among those samples before authorizing `skill_on`.
4. Keep raw JSONL, databases, and exports immutable; this report is analysis only.
