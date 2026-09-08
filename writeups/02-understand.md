# Understand experiment — Can it understand without a manual?

**Stage:** Understand (L0)  
**Same canned 2048 frames as the See experiment.** Pixels-only. No how-to text.  
**Run:** `runs/20260818T201836Z_2048_understand` (frames captured; VLM calls skipped — no vision SKU yet)

## Protocol

First token of the contract: classify **UI Screen vs Gameplay Screen**. Then goal + sensible next move. Human-score with `eval/rubrics/understand.md`. Optional 2048 DOM oracle scores *later*, never goes to the model.

```powershell
python scripts/run_understand.py
```

## Results

Harness path works: start / mid / gameover frames land under `runs/*_understand/frames`. Model cells are empty until `qwen2.5vl` or an OpenRouter vision ID is configured.

Expected once models run: legal-move guesses on `mid`; confused or “no moves” language on `gameover`. That split is the point.

Manual-in-prompt is **not** this experiment. That ablation is the Analyze experiment (`--manual`).
