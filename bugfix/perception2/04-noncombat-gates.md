# Batch 4: Non-combat gates runnable

**Complexity: Medium**
**Recommended agent:** Worker B
**Prerequisite:** Batch 3 gate green
**Primary failure:** proposal/effect benches exist as library+pytest only; `perception_cost.py` `transfer_gate: "pending"`; no `python -m loop` subcommand.

## Objective

Make production-faithful proposal and effect benches runnable from the CLI, pin map-node legality, and leave the transfer gate honest.

## Owned files

- `loop/__main__.py`
- `loop/eval/grounding_benchmark.py`, `loop/eval/effect_benchmark.py`, `loop/eval/perception_cost.py` (wrappers only; do not change scoring truth leaks)
- Tests: `tests/loop/test_aim.py` (node legality pin), optional CLI smoke in `tests/loop/`

Do not edit the turn loop.

## Required behavior

1. `python -m loop benchmark-proposals` wraps `run_production_proposal_benchmark`.
2. `python -m loop benchmark-effects` wraps the effect benchmark.
3. `python -m loop benchmark-perception-cost` emits p50/p95 perceive/ocr/think ms (from fixture or last-run merge), LLM1 fresh vs cached counts, fusion audit (`gui_kept`, `kept_sources`, `cv_reserve`).
4. `node_N` is `click_control` only after high-confidence `ground_spatial_labels` binding; placeholders otherwise remain `inspect`. Pin with a test.
5. `transfer_gate` stays `"pending"` unless a non-STS2 layout fixture row already passes. Do not fake it.

## Required regression tests

- Existing fusion tests stay green (`test_merge_keeps_complementary_cv_when_gui_present`, dense-cluster, overlap dedupe).
- New: placeholder `node_0` → `inspect`; grounded high-confidence `node_0` → `click_control` only when bound as a control role the surface admits (document the exact role; do not widen `_legal_intents` just to pass).
- CLI subcommands listed in `python -m loop --help`.

## Focused test command

```
python -m pytest tests/loop/test_aim.py tests/loop/test_grounding_benchmark.py tests/loop/test_effect_benchmark.py -q
python -m loop benchmark-proposals
python -m loop benchmark-effects
```

Cumulative: `python -m pytest tests/loop -q`

## Gate

Subcommands run on fixtures with zero legend false promotions. Transfer flag honest.

## Stop condition

Stop if scoring starts leaking truth into the production proposal path, or if placeholders become `click_control` without a high-confidence bind.
