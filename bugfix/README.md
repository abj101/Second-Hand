# STS2 Harness Bugfix Plan Set

These plans turn the findings from run `20260906T021305Z_sts2_collect` into small, sequential implementation batches. The run won one combat and reached Loot, but it also exposed unstable card identity, stale controls, delayed victory recognition, polluted memory, ambiguous telemetry, and avoidable OCR cost.

Use [00-orchestrator-plan.md](00-orchestrator-plan.md) as the entry point. Execute batches in numeric order. Every batch is intended to land as a separate commit and must pass its own gate plus the cumulative test gate before the next batch starts.

## Sequence and agent tier

1. [01-card-identity-and-reflow.md](01-card-identity-and-reflow.md) — **Complexity: High** — strong coding agent.
2. [02-action-surface-and-control-suppression.md](02-action-surface-and-control-suppression.md) — **Complexity: Medium** — standard coding agent.
3. [03-combat-victory-transition.md](03-combat-victory-transition.md) — **Complexity: Medium** — standard coding agent.
4. [04-memory-truth-and-export-safety.md](04-memory-truth-and-export-safety.md) — **Complexity: High** — strong coding agent.
5. [05a-telemetry.md](05a-telemetry.md) — **Complexity: Medium** — standard coding agent.
6. [05b-graceful-finalization.md](05b-graceful-finalization.md) — **Complexity: Medium** — standard coding agent.
7. [05c-ocr-cache-and-performance.md](05c-ocr-cache-and-performance.md) — **Complexity: High** — strong coding agent.
8. [06-validation-runs.md](06-validation-runs.md) — **Complexity: Low** — economical execution/analysis agent with UI access.

## Shared invariants

- Marks (`mk_N`) are frame-local execution handles, never persistent identities.
- Current-frame object IDs must be resolved against the current frame before input. `card_N` is an ordinal selector, not a persistent physical-card identity.
- OCR text alone cannot prove that an object is actionable or that an action succeeded.
- No uncertain action may become positive SFT/DPO training data.
- A safety rejection must never fall back to an unverified click.
- Existing `no_skill` behavior remains the baseline until the final validation batch passes.
- Do not enable post-run learning while fixing the harness.
