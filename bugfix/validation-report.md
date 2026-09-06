# Batch 6: Live Validation Report

**Branch:** `feat/collect-loop` @ `46326e4` (Batch 5C)  
**Date:** 2026-09-06  
**Command:** `python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu`  
**Baseline preserved:** `runs/20260906T021305Z_sts2_collect` (untouched)

## Executive summary

Three live validation runs were executed from comparable combat starts (80/80 HP, 3 energy, one enemy, no modal). **All three failed** — none reached a combat reward screen. Each run hit the 24-step HUD-identity stuck timeout with 22–23 `target-not-found` errors per run. **Batch 6 does not pass.** Definition-of-done is not met; `skill_on` A/B is **not authorized**.

Primary regression vs baseline: entity target resolution (`unknown_character_1` / `enemy_1` in model intent cannot be mapped to current-frame aim marks), producing a fail-closed loop where every targeted card play is rejected before dispatch.

---

## 1. Baseline metrics (`20260906T021305Z_sts2_collect`)

Pre-fix reference run (Batch 0). Preserved unchanged.

| Metric | Value |
|---|---|
| Turns | 29 |
| Wall time | 372 s (~6.2 min) |
| Combat won | Yes (Loot OCR on step 29) |
| Start state | combat, HP 80/80, energy 1 |
| Intent counts | 19 `play_card`, 7 `click_control`, 2 `end_turn`, 1 empty |
| Structured errors | 9 `source-unconfirmed`, 2 `target-not-found`, 2 `effect-unverified`, 1 `inventory-inconsistent` |
| Stale-target dispatches | 2 |
| Repeated blocked clicks | 0 (but 4 ineffective Next clicks across two pairs: steps 7–8, 23–24) |
| MCP tool calls | 0 |
| LLM1 fresh/cache | not tagged in metrics (field absent on all steps) |
| Perceive ms (min/mean/max) | 3114 / 5561 / 7649 |
| Reason ms (min/mean/max) | 542 / 620 / 817 |
| OCR ms (min/mean/max) | 1666 / 2485 / 5935 |
| First post-kill screen | Loot (OCR on step 29) |
| Journal contradictions | 0 detected |
| Malformed lessons | 0 detected |
| Export eligible / excluded | 9 / 14 |
| Artifacts | `turns.jsonl`, `journal.json`, `objects.db` present; **`memory_grounded.json` and `summary.json` missing** (pre–Batch 5B) |
| DB quick_check | ok |

---

## 2. Per-run sections

### Run 1 — `20260906T060636Z_sts2_collect`

**Start:** combat, HP 80/80, energy 3/3, one enemy (Jaw Worm), no modal. Comparable: **yes**.

**End:** step 24 timeout (`hud_identity_stuck`, `no_pixel_change`). Still on combat screen; enemy HP unchanged at 44/44; player energy still 3/3.

| Metric | Value |
|---|---|
| Won | **No** |
| Turns | 24 |
| Wall time | 354 s |
| Errors | 22 `target-not-found`, 2 `source-unconfirmed` |
| Stale dispatches | 22 |
| Repeated blocked clicks | 0 |
| MCP calls | 0 |
| First post-kill screen | N/A (combat never ended) |
| Export eligible / excluded | 0 / 24 |
| Artifacts complete | **Yes** (`flush_completed=true`, DB ok) |

**Failure signature:** Every targeted `play_card` intent named `unknown_character_1` or `enemy_1`, but aim marks used numeric mark IDs (e.g. `mk_9`). Gate rejected with `target-not-found:unknown_character_1` before any card reached the enemy. Rescan loop broke repeat but could not resolve identity. Audit flags: `hud_identity_stuck`, `no_pixel_change`.

---

### Run 2 — `20260906T061312Z_sts2_collect`

**Start:** combat, HP 80/80, energy 3/3. Comparable: **yes** (same combat, agent re-entered mid-fight).

**End:** step 24 timeout, identical stuck pattern.

| Metric | Value |
|---|---|
| Won | **No** |
| Turns | 24 |
| Wall time | 376 s |
| Errors | 23 `target-not-found`, 1 `source-unconfirmed` |
| Stale dispatches | 23 |
| Repeated blocked clicks | 0 |
| MCP calls | 0 |
| Export eligible / excluded | 0 / 24 |
| Artifacts complete | **Yes** |

Same `unknown_character_1` target resolution failure as Run 1.

---

### Run 3 — `20260906T062024Z_sts2_collect`

**Start:** combat, HP 80/80, energy 3/3. Comparable: **yes**.

**End:** step 24 timeout, identical stuck pattern.

| Metric | Value |
|---|---|
| Won | **No** |
| Turns | 24 |
| Wall time | 372 s |
| Errors | 23 `target-not-found`, 1 `source-unconfirmed` |
| Stale dispatches | 23 |
| Repeated blocked clicks | 0 |
| MCP calls | 0 |
| Export eligible / excluded | 0 / 24 |
| Artifacts complete | **Yes** |

---

## 3. Aggregate comparison

| Metric | Baseline | Run 1 | Run 2 | Run 3 | Fixed Δ (runs 1–3 avg vs baseline) |
|---|---:|---:|---:|---:|---|
| Combat wins | 1 | 0 | 0 | 0 | **−1** |
| Turns to outcome | 29 | 24 | 24 | 24 | stuck earlier |
| Wall time (s) | 372 | 354 | 376 | 372 | similar |
| `target-not-found` | 2 | 22 | 23 | 23 | **+20** (regression) |
| `source-unconfirmed` | 9 | 2 | 1 | 1 | improved |
| Stale dispatches | 2 | 22 | 23 | 23 | **+21** (regression) |
| Repeated blocked clicks | 0* | 0 | 0 | 0 | same |
| Loot recognized | yes (step 29) | no | no | no | **regression** |
| Export eligible rows | 9 | 0 | 0 | 0 | **−9** |
| Artifacts complete | partial | yes | yes | yes | Batch 5B fix works |
| Perceive ms mean | 5561 | 3679 | 3622 | 2748 | faster (OCR cache) |
| OCR ms mean | 2485 | 2951 | 3147 | 3210 | similar |

\*Baseline had 4 ineffective Next clicks on unchanged surface (steps 7–8, 23–24) — pre–Batch 2 behavior.

---

## 4. Summary table

| Run ID | Comparable | Won | Turns | Wall (s) | Errors (cat.) | Repeated | Stale disp. | 1st post-kill | MCP | LLM1 f/c | Journal contra | Malformed | Export el/ex | Artifacts | Notes |
|---|---|---:|---:|---:|---|---:|---:|---|---:|---|---:|---:|---:|---|---|
| `20260906T021305Z` (baseline) | yes | yes | 29 | 372 | src-unconf:9, tgt-nf:2, eff-unv:2, inv:1 | 0 | 2 | Loot | 0 | n/a | 0 | 0 | 9/14 | partial | pre-fix reference |
| `20260906T060636Z` | yes | **no** | 24 | 354 | tgt-nf:22, src-unconf:2 | 0 | 22 | — | 0 | n/a | 0 | 0 | 0/24 | yes | identity stuck |
| `20260906T061312Z` | yes | **no** | 24 | 376 | tgt-nf:23, src-unconf:1 | 0 | 23 | — | 0 | n/a | 0 | 0 | 0/24 | yes | identity stuck |
| `20260906T062024Z` | yes | **no** | 24 | 372 | tgt-nf:23, src-unconf:1 | 0 | 23 | — | 0 | n/a | 0 | 0 | 0/24 | yes | identity stuck |

---

## 5. Pass/fail checklist

| Criterion | Result | Evidence |
|---|---|---|
| Two consecutive combat wins | **FAIL** | 0/3 wins; 0 consecutive |
| Zero stale-target dispatches | **FAIL** | 22–23 per run (baseline had 2) |
| Zero repeated clicks on twice-blocked control | **PASS** | 0 across all validation runs |
| ≤1 source-unconfirmed per combat, no same-coord retry | **FAIL** | 1–2 per run, but combat never progressed; target-not-found dominates |
| Loot on first stable post-victory observation | **FAIL** | No run reached victory |
| No stale combat objects on Loot | **N/A** | No loot reached |
| No contradictory typed memory identities | **PASS** | 0 contradictions in all run journals |
| No malformed lesson text | **PASS** | 0 detected |
| Uncertain rows excluded from training exports | **PASS** | 0 eligible, 100% excluded |
| Graceful stop flushes all artifacts | **PASS** | Runs 1–3: `flush_completed=true`, DB quick_check ok, all 5 artifacts present |

**Overall Batch 6 gate: FAIL**

---

## 6. Remaining risks

1. **Entity ID → aim mark bridging is broken for unidentified enemies.** Model emits `unknown_character_1` / `enemy_1`; gate cannot resolve to current mark IDs. Every targeted card play fails closed. → **Reopen Batch 1** (card identity and reflow / target resolution).

2. **HUD identity stuck timeout masks combat failure.** Collector reports `success=True` with `success_rate=1.0` despite zero progress — telemetry gap (Batch 5A).

3. **Baseline comparability caveat.** Baseline started at energy 1 (mid-combat); validation runs at energy 3 (turn 1). Same enemy/HP but different turn phase may affect card hand — still comparable per plan (one enemy, ordinary player turn).

4. **Batch 5B artifact flush verified.** Post-fix runs correctly write `summary.json`, `memory_grounded.json`, and close `objects.db`. This sub-criterion passes.

5. **Batch 5C OCR cache.** Perceive/OCR timing improved (mean perceive 3622 ms vs baseline 5561 ms) without fixture regression — performance fix holds, but combat reliability regressed independently.

---

## 7. Recommendation: `skill_on` A/B

**Not authorized.** Definition of done requires two consecutive clean `no_skill` combat wins with zero stale dispatches and immediate Loot recognition. Current validation shows 0 wins and 22–23 stale dispatches per run — a regression from baseline (1 win, 2 stale dispatches).

**Suggested orchestrator action:** Reopen **Batch 1** (entity/target identity resolution). Do not proceed to skill A/B until a repeat of Batch 6 passes.

---

## Failure routing

| Symptom | Batch |
|---|---|
| `target-not-found:unknown_character_1` on every targeted play | **Batch 1** |
| HUD stuck timeout with `success=True` misreport | Batch 5A (secondary) |

No product-code changes were made during this validation batch.
