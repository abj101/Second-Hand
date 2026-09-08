# Collect Run Comparison

**Date:** 2026-09-02  
**Tooling:** `python -m loop inspect-run`, `python -m loop eval`, `scripts/analyze_sts2_run.py` (CLI blocked by circular import — metrics computed from `turns.jsonl` / `episodes.jsonl` directly).

## Runs Compared

| Run | Path | mtime (local) | Status |
|-----|------|---------------|--------|
| **Newest STS2 (in progress)** | `runs/20260902T085028Z_sts2_collect` | 2026-09-02 01:57 | 7 steps, no `episodes.jsonl` yet |
| **Newest completed STS2** | `runs/20260902T075216Z_sts2_collect` | 2026-09-02 01:02 | 9 steps, 1 episode |
| **Previous completed STS2** | `runs/20260902T075004Z_sts2_collect` | 2026-09-02 01:00 | 9 steps, 1 episode |
| **Fake baseline** | `runs/20260902T085013Z_fake_collect` | 2026-09-02 01:50 | 6 steps, 1 episode (parallel subagent) |

All STS2 runs share `prompt_id=bb77d64cf5022f7f`, `architecture=two_stage`, `arm=no_skill`. Fake run uses `prompt_id=4c70ad9636421e18`, `policy_id=skip-models`.

## Key Metrics

| Metric | 085028 (STS2, live) | 075216 (STS2) | 075004 (STS2) | 085013 (fake) |
|--------|---------------------|---------------|---------------|---------------|
| Steps | 7 | 9 | 9 | 6 |
| Episodes | — | 1 | 1 | 1 |
| Success rate | — | 100% | 100% | 0% |
| Episode return | — | 0.30 | 1.10 | −1.36 |
| Mean turn_reward | 0.093 | 0.033 | 0.122 | −0.227 |
| turn_reward > 0 rate | 29% (2/7) | 11% (1/9) | 44% (4/9) | 33% (2/6) |
| Two-click rate | 0% | 0% | 0% | 0% |
| Tutorial clear | ✓ | ✗ | ✗ | n/a |
| Title → map | ✗ | ✗ | ✗ | n/a |
| Combats started | ✗ (not yet) | ✓ | ✓ | n/a |
| SFT-eligible steps | 2 | 1 | 3 | 2 |
| `sft_traces.jsonl` rows | 0 | 1 | 3 | 0 |
| False-fail (pause) | 0 | 0 | 0 | 0 |
| Errors | 1× stale-obs | — | 1× non-combat-enemy-target | — |
| Mean perceive_ms | 14,876 | 10,561 | 9,273 | 27 |
| Mean reason_ms | 6,974 | 8,142 | 13,793 | — |

### Eval delta (075216 candidate − 075004 baseline)

Same fixture and compatible `prompt_id`; `compare_turn_runs` would accept this pair.

| Delta | Value |
|-------|-------|
| mean_turn_reward | −0.089 |
| turn_reward > 0 rate | −33 pp |
| success_rate | 0 |
| combats_started | 0 |
| tutorial_clear | 0 |
| two_click_rate | 0 |

## Notable Changes

### 075216 vs 075004 (completed STS2 pair)

**Regressions in newer run (075216):**
- **Reward yield collapsed:** turn_reward > 0 dropped from 44% → 11%; episode return 1.10 → 0.30; SFT-eligible steps 3 → 1.
- **HUD screen classification degraded:** 8/9 steps logged `screen=""` vs 4/9 `combat` in the older run — host OCR is not tagging screens reliably.
- **Less combat engagement:** only 1 combat-tagged step despite reaching post-combat "Conquered" (steps 7–9 stalled on `:icon` clicks with no reward).

**Improvements in newer run:**
- Reached post-combat summary and map-adjacent screens (death/conquered/map phases in thought timeline).
- No `non-combat-enemy-target` execution errors (stale-obs blocked the bad click instead).
- Faster reasoning (mean 8.1s vs 13.8s).

### 085028 (live STS2 — parallel collect)

**Early positive signals vs both completed runs:**
- `tutorial_clear=1` — dismissed map tutorial (`Got it`, +0.35 reward at step 5).
- Better screen tagging: `select` (2), `tutorial` (1), `map` (3) vs mostly empty screens in 075216.
- turn_reward > 0 at 29% through 7 steps — on pace to beat 075216's 11%.

**Issues so far:**
- Step 4: reasoner emitted `request_recheck` tool JSON instead of a click; stale-obs blocked a bad Ironclad target.
- Step 6: empty action — chose `Singleplayer` on map screen where allowed controls were map nodes only.
- Perceive latency up (~15s mean vs ~10s in 075216).

### 085013 (fake baseline vs system.md)

| Expectation (system.md) | Observed |
|-------------------------|----------|
| `prompt_id` on every StepLog | ✓ all 6 steps (`4c70ad9636421e18`) |
| `turn_reward > 0` for SFT gate | 33% positive (2/6); 2 SFT-eligible with `executed_as_proposed=true` |
| Core StepLog fields | ✓ `prompt_id`, `score_delta`, `metrics`, `result`, `action`, `thought`, frame refs |
| `executed_as_proposed` | ✓ nested in `result` (not top-level) |
| Episode artifacts | ✓ `turns.jsonl`, `episodes.jsonl`, `frames/`; no `sft_traces.jsonl` (STS2-only auto-write) |

Fake run behaved as expected for a `--skip-models` smoke: heuristic GSM survey chords, episode ended `terminal=fail` at HP=0.

## CLI Note

~~`python -m loop inspect-run` and `python -m loop eval` currently fail with a circular import~~ **Fixed (2026-09-02):** lazy exports in `loop/agent/__init__.py` break the `loop.agent.loop` ↔ `loop.eval.export` cycle. Verified:

```powershell
python -m loop inspect-run --fixture sts2 --run-dir runs\20260902T085028Z_sts2_collect
python -m loop eval --fixture sts2 --baseline runs\20260902T075004Z_sts2_collect --candidate runs\20260902T075216Z_sts2_collect
```

## Recommendations

1. ~~**Fix the circular import**~~ **Done** — `loop/agent/__init__.py` now lazy-loads `CollectAgent`/`CollectConfig`; `inspect-run` / `eval` / `export-sft` work from CLI again.
2. **Investigate empty `metrics.screen`** in 075216 (8/9 blank) — host `score_sts` OCR is failing to classify screens during two_stage runs; 085028 shows the fix path (better tagging when perceive + HUD align).
3. **Gate invalid choices on map/title screens** — step 6 in 085028 chose `Singleplayer` off-menu; `validate_choice` should reject and re-prompt before emitting an empty action.
4. **Let 085028 finish** before declaring eval winner — early tutorial/map progress and 29% reward rate look healthier than 075216, but no combat yet and perceive latency is high (~15s/step).
5. **Prioritize two-click chord data** — 0% two-click rate across all recent STS2 runs; combat card→enemy sequences are the highest-value SFT/DPO signal per system.md but none were captured in these short timeout runs.
