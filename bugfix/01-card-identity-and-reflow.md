# Batch 1: Card Identity and Hand Reflow

**Complexity: High**  
**Recommended agent:** strong reasoning/coding tier  
**Prerequisite:** none  
**Primary failure:** repeated `source-unconfirmed:card_2` and stale `target-not-found:object:6`.

## Objective

Make card selection and targeting obey snapshot scope. In the current implementation, `card_N` is assigned from the current left-to-right ordinal and is not a persistent physical-card identity. After a hand mutation, the prior card intent must be invalidated and a fresh perception plus model decision is required.

## Owned files

- `loop/agent/loop.py`, limited to card transaction, snapshot invalidation, and rebind paths.
- `loop/mcp/tools/targeting.py`.
- `loop/memory/screen_objects.py` only if a small current-snapshot lookup helper is necessary.
- `tests/loop/test_two_stage_resolve.py`.
- `tests/loop/test_mcp_tools.py`.

Do not modify notebook/journal logic, reward scoring, export filters, OCR, or broad prompts.

## Required behavior

1. Treat `mark_id` and coordinates as valid only for their captured `(run_id, step_id, snapshot_id)`.
2. Resolve `card_id` only within the snapshot in which the model issued it. Do not reinterpret the same ordinal after reflow.
3. After confirmed selection, capture a fresh inventory and resolve `target_id` again before target dispatch.
4. Invalidate the hand inventory after any of:
   - energy change;
   - card count/hand membership change;
   - verified `card_left_hand`;
   - significant hand-region column shift;
   - a screen transition.
5. On `source-unconfirmed`, invalidate the source snapshot and return to perception/replanning. Do not immediately retry the same mark/coordinate.
6. Label, cost, and relative order may validate a current-snapshot selection, but cannot establish persistent physical identity—especially for duplicate cards.
7. If the requested snapshot-scoped object cannot be found, fail closed with a structured error and execute no click.
8. Preserve the existing fresh target-rebind safety behavior.

## Implementation steps

1. Trace `plan_card_by_id`, current snapshot identity, `_is_targeted_card_transaction`, and `_execute_card_transaction`.
2. Add a single source-resolution helper that accepts the planned snapshot-scoped ID and captured snapshot identity. Avoid duplicating lookup rules in planner and executor.
3. Attach the source snapshot identity to the execution plan, just as targeting already uses current inventory identity.
4. Add an explicit hand-generation/invalidation counter or snapshot nonce. Bump it on the mutation signals above.
5. Define the exact refresh boundary: capture frame → build and save action surface → issue decision → compare generation/snapshot immediately before dispatch. A stale rejection consumes that decision, executes no input, and requests an internal recapture/reperception followed by a new model decision.
6. After an unconfirmed source, mark that source plan stale for the unchanged frame so the loop cannot retry it without rebuilding inventory.
7. Keep all safety failures observable in `CardTransaction.verification_evidence`.

## Required regression tests

- A card plan created before a hand reflow is rejected before clicking.
- An old `card_2` intent is rejected after a preceding card leaves the hand; a rebuilt inventory requires a newly issued intent.
- Duplicate-label, same-cost cards cannot be silently substituted after reflow.
- `source-unconfirmed` causes invalidation and prevents an identical immediate click.
- A selected card causes target resolution from the post-selection inventory.
- Missing stable target executes the source only when selection is intentionally allowed; it never clicks a stale target.
- End Turn remains outside the card transaction path.
- Existing fresh target rebound tests continue to pass.

## Focused test command

`python -m pytest tests/loop/test_two_stage_resolve.py tests/loop/test_mcp_tools.py tests/loop/test_action_surface.py -q`

## Acceptance gate

- No test can construct a successful transaction using a mark from a prior snapshot.
- A hand reflow invalidates the prior ordinal intent and produces a new decision against a new snapshot.
- One source-confirmation failure cannot cause an immediate same-coordinate retry.
- No heuristic chooses a different card on the model's behalf; the system either resolves the requested snapshot-scoped ID or asks for a new decision.

## Handoff note

Report the exact invalidation signals implemented and any remaining ambiguous duplicate-card case. Batch 2 may consume the snapshot-generation API but must not change it.
