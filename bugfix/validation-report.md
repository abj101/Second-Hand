# Batch 6: Live Validation Report

**Status:** FAIL — `skill_on` is **not authorized**. This report recomputes the accounting from immutable `turns.jsonl`, `episodes.jsonl`, and `summary.json` artifacts. It does not modify those artifacts.

**Command:** `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`
**Baseline preserved:** `runs/20260906T021305Z_sts2_collect` (untouched)
**Accounting rule:** a `target-not-found` with `target_dispatched=false` is a target-resolution reject. A stale target dispatch requires a target primitive actually sent from an invalid snapshot. Source-only card transactions are partial; effect verification is separate.

## Handoff

The original three Batch 6 samples and the complete post-reopen sample all ended in HUD-stuck timeouts. The later `20260906T064037Z` attempt was stopped by the operator and is incomplete; it is not a comparable validation outcome.

`20260906T063400Z` confirms the live target-binding defect remains: intents use `unknown_character_1` or `enemy_1`, the source card is selected, fresh target resolution fails, and no target primitive is sent. This is fail-closed behavior, not stale-coordinate dispatch.

Do not continue a partial session. Before the next sample, start a fresh ordinary player combat turn. Historical run files, databases, and exports are evidence only and must remain unchanged.

## Recomputed evidence

### Transaction accounting

| Run | Outcome | Target-resolution rejects | Source primitives | Target primitives | Partial transactions | Verified effects | Stale target dispatches |
|---|---|---:|---:|---:|---:|---:|---:|
| `20260906T021305Z` baseline | win (Loot at step 29) | 2 | 19 | 8 | 11 | 6 | 0 |
| `20260906T060636Z` | inconclusive (timeout) | 22 | 24 | 0 | 24 | 0 | 0 |
| `20260906T061312Z` | inconclusive (timeout) | 23 | 24 | 0 | 24 | 0 | 0 |
| `20260906T062024Z` | inconclusive (timeout) | 23 | 24 | 0 | 24 | 0 | 0 |
| `20260906T063400Z` | inconclusive (timeout) | 24 | 24 | 0 | 24 | 0 | 0 |
| `20260906T064037Z` | inconclusive (operator-stopped) | 10 | 11 | 0 | 11 | 1 | 0 |

The baseline's two `target-not-found` transactions also have `target_dispatched=false`; they are resolution rejects, not stale dispatches. The baseline separately has eight actual target primitives, six with verified effects. No raw run provides evidence of a target primitive sent from an invalid/stale snapshot, so the stale-dispatch count is zero for every row.

### Per-run validation table

| Run ID | Comparable | Recomputed outcome | Steps | Wall (s) | Structured errors | Repeated blocked control clicks | Stale dispatches | First post-kill screen | MCP calls | LLM1 fresh/cache | Journal contradictions / malformed lessons | Export eligible / excluded | Artifacts | Notes |
|---|---|---|---:|---:|---|---:|---:|---|---:|---|---|---|---|---|
| `20260906T021305Z` baseline | reference | win | 29 | 372 | source-unconfirmed: 9; target-not-found: 2; effect-unverified: 2; inventory-inconsistent: 1 | 0* | 0 | Loot | 0 | unavailable | 0 / 0 | 9 / 14 | partial | Pre-5B artifacts lack `memory_grounded.json` and `summary.json`. |
| `20260906T060636Z` | yes | inconclusive | 24 | 354 | target-not-found: 22; source-unconfirmed: 2 | 0 | 0 | — | 0 | unavailable | 0 / 0 | 0 / 24 | complete | `hud_identity_stuck`; legacy episode/summary say `success=true`, but terminal is timeout. |
| `20260906T061312Z` | yes | inconclusive | 24 | 376 | target-not-found: 23; source-unconfirmed: 1 | 0 | 0 | — | 0 | unavailable | 0 / 0 | 0 / 24 | complete | `hud_identity_stuck`; legacy `success=true` is not a win. |
| `20260906T062024Z` | yes | inconclusive | 24 | 372 | target-not-found: 23; source-unconfirmed: 1 | 0 | 0 | — | 0 | unavailable | 0 / 0 | 0 / 24 | complete | `hud_identity_stuck`; legacy `success=true` is not a win. |
| `20260906T063400Z` | yes | inconclusive | 24 | n/a | target-not-found: 24 (19 `unknown_character_1`, 5 `enemy_1`) | 0 | 0 | — | 0 | unavailable | 0 / 0 | 0 / 24 | complete | Post-`9435b2d` failure; energy stayed 3 and `flush_completed=true`. |
| `20260906T064037Z` | no — stopped | inconclusive | 12 | n/a | target-not-found: 10; action-surface-invariant: 1 | 0 | 0 | — | 0 | unavailable | not finalized | not finalized | incomplete | Operator stop; no `summary.json` or `memory_grounded.json`. |

\*The baseline had four ineffective Next clicks across two pairs before Batch 2, but no repeated click on a twice-blocked unchanged control under the current criterion.

### Aggregate comparison

| Metric | Baseline | Comparable Batch 6 samples (`060636`, `061312`, `062024`, `063400`) |
|---|---:|---:|
| Determinate wins / losses / inconclusive | 1 / 0 / 0 | 0 / 0 / 4 |
| Recomputed success rate | 1.0 | null (no determinate episodes) |
| Target-resolution rejects | 2 | 92 |
| Source-only partial transactions | 11 | 94 |
| Target primitives dispatched | 8 | 0 |
| Verified effects | 6 | 0 |
| Stale target dispatches | 0 | 0 |
| Loot reached | yes | no |
| Complete finalization artifacts | no (pre-5B) | yes, 4/4 |

The previous report's 22–24 “stale dispatches” per post-fix run was incorrect: those counts were target-resolution rejects. The reliability regression remains severe—94 source selections produced no target dispatch or verified effect—but it is a target-binding failure rather than unsafe stale input.

## Gate checklist

| Criterion | Result | Evidence |
|---|---|---|
| Two consecutive combat wins | **FAIL** | Zero wins in four comparable post-fix samples. |
| Zero stale-target dispatches | **PASS (safety only)** | Zero target primitives from invalid/stale snapshots in all raw logs. |
| Zero repeated clicks on a twice-blocked unchanged control | **PASS** | Zero in all validation samples. |
| At most one source-unconfirmed event and no same-coordinate immediate retry | **FAIL** | `060636` has two source-unconfirmed events; no sample achieved combat progress. |
| Loot on first stable post-victory observation | **FAIL** | No post-fix sample reached victory. |
| No stale combat objects on Loot | **N/A** | No post-fix Loot observation. |
| No contradictory typed memory identities | **PASS** | Zero reported in finalized journals. |
| No malformed lesson text | **PASS** | Zero reported in finalized journals. |
| Uncertain rows excluded from positive training exports | **PASS** | Every finalized post-fix train row is excluded (0 / 24). |
| Graceful finalization writes complete artifacts | **PASS** | `060636`, `061312`, `062024`, and `063400` have all expected artifacts and `flush_completed=true`. |

**Overall:** FAIL. Passing the stale-dispatch safety sub-criterion does not compensate for zero successful target dispatches, zero verified effects, or zero wins.

## Remaining requirements and recommendation

1. Reopen Batch 1 only: repair canonical prompt target ID to fresh-frame actionable-object binding. The `unknown_character_1` / `enemy_1` defect is live in `063400`; do not add a coordinate, lane, nearest-object, or one-enemy fallback.
2. Run one fresh-combat diagnostic after that repair. Require a fresh target primitive and verified game effect before beginning Batch 6 samples.
3. Run at least three new comparable `no_skill` samples and require two consecutive wins, zero resolution rejects in successful samples, first stable Loot, clean memory/export evidence, and complete finalization.
4. Keep `skill_on` unauthorized until those requirements pass.

No product-code changes or live UI runs were performed while recomputing this report.
