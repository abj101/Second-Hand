# Perception V2: close remaining perception → ACT gaps

These plans turn the findings from runs `20260907T190633Z_sts2_collect` and `20260907T191012Z_sts2_collect` into sequential batches. Complementary fusion, the LLM1 evidence gate, `last_execution`, and `_rescan_scene` already landed from `writeups/perception-orchestrator-plan.md`. This set closes the remaining combat-inventory, transition-frame, and non-combat-gate gaps.

Use [00-orchestrator-plan.md](00-orchestrator-plan.md) as the entry point. Execute batches in numeric order. Every batch lands as a separate commit and must pass its own gate plus `python -m pytest tests/loop -q` before the next batch starts.

## Sequence and agent tier

1. [01-regression-lock.md](01-regression-lock.md) — **Complexity: Medium** — fixtures/tests worker (A).
2. [02-combat-inventory.md](02-combat-inventory.md) — **Complexity: High** — production-module worker (B) plus orchestrator for `loop.py`.
3. [03-transition-frames.md](03-transition-frames.md) — **Complexity: Medium** — orchestrator (`loop.py`) plus worker A tests.
4. [04-noncombat-gates.md](04-noncombat-gates.md) — **Complexity: Medium** — worker B (`loop/__main__.py`, `loop/eval/*`).
5. [05-live-validation.md](05-live-validation.md) — **Complexity: Low** — orchestrator with user at the game; no product edits.

## Shared invariants

- Marks (`mk_N`) are frame-local execution handles, never persistent identities.
- Store `object_id`s are assigned by `_prepare_frame_marks` (`card_N` / `enemy_N`). LLM1 ids and prompt ids (`unknown_character_N`) never become store authority.
- LLM1 may promote a mark to `role="card"` only with current-frame geometry, hand-band `y`, and non-button/nav roles.
- Uncertain targets stay uncertain in SFT/DPO exports.
- Overlay leftovers (`Prev`/`Next`) never paginate a live combat board.
- Workers never touch the live game window. Only the orchestrator (or user) runs live collects.
- Revert only the offending phase commit.

## Failure routing from live runs

- Card/target empty after valid LLM1 → reopen Batch 2.
- `empty-action-surface` / `decision_softlock` on transition frames → reopen Batch 3.
- Map no-op / unrunnable benches → reopen Batch 4.
