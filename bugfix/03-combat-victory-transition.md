# Batch 3: Combat Victory and Reward Transition

**Complexity: Medium**  
**Recommended agent:** standard coding tier  
**Prerequisites:** Batches 1–2 complete  
**Primary failure:** the lethal attack reached Loot, but stale combat state survived into the next decision.

## Objective

Recognize `combat -> victory_animation -> reward` promptly, clear combat-only state atomically, and expose a durable `combat_won` event.

## Owned files

- `loop/reward/sts.py`.
- `loop/fixtures/sts2.py`.
- `loop/agent/loop.py`, limited to transition application and state clearing.
- `loop/memory/overlay_state.py` if it owns screen reconciliation.
- `tests/loop/test_sts2.py`.
- `tests/loop/test_action_surface.py`, only reward inventory assertions.

Do not alter card matching, journal confidence rules, export filters, or OCR implementation.

## Required behavior

1. Represent the transition explicitly as combat, optional victory animation, then reward/Loot.
2. Implement a pure `combat_exit_evidence(previous, current) -> CombatExitEvidence` helper. It returns individual booleans, numeric score, `confirmed`, and a reason list. Score: current screen classified `reward` = 3; explicit `Loot`/`Rewards` heading = 3; typed reward action (`add card`, `take gold`, `skip`) = 2; no enemies plus no hand/energy = 1; prior enemy HP reaches zero = 1. Confirm at score >= 3 only when at least one 2-or-3-point reward-specific signal is present. A loading/black frame always returns unconfirmed.
3. Useful signals include:
   - Loot/reward/add-card/skip controls;
   - disappearance of enemy and combat intent;
   - disappearance of active combat energy/hand inventory;
   - large stable transition from combat;
   - enemy HP reaching zero.
4. On confirmed transition, use one idempotent reset method to clear the entity tracker, selected-card state, current `ScreenObjectStore`, aim/action inventory, combat perception cache, pending execution plan/error, card hand generation, and Batch 2 blocked-control suppression. The implementing agent must enumerate the concrete field/method names in its handoff.
   - tracked combat entities;
   - selected card;
   - hand/card inventory generation;
   - combat-only controls and blocked-control state;
   - cached combat scene and target bindings.
5. Rebuild the reward inventory before requesting another decision.
6. Emit canonical `StepLog.metrics["combat_won"] = True` exactly once. Key idempotency by `(episode_id, combat_index)` and store the same transition ID in metrics.
7. Never classify a normal act reward as terminal game victory.

## Implementation steps

1. Centralize combat-exit detection in a pure helper with structured evidence.
2. Add an idempotent transition application method to the loop.
3. Call it after action verification and again before the next prompt, so delayed animation is handled safely.
4. Ensure clearing happens before grounding/reasoning on the reward frame.
5. Ensure the monitor can observe `combat_won` without interpreting screenshots manually.

## Required regression tests

- Combat frame followed by the baseline-style Loot frame yields `screen=reward` and `combat_won=True`.
- Stale `Nibbit`, selected card, and combat actions are absent after transition.
- Transition reward is emitted once even across multiple stable Loot observations.
- Reward screen is not terminal win.
- Brief combat animation without Loot evidence does not clear state prematurely.
- A black/loading frame is not mistaken for reward.
- A multi-frame sequence `combat → animation → Loot → stable Loot` emits one event and clears state only at Loot.

## Focused test command

`python -m pytest tests/loop/test_sts2.py tests/loop/test_action_surface.py tests/loop/test_audits.py -q`

## Acceptance gate

- The first stable baseline Loot observation cannot produce `inventory-inconsistent` from stale combat objects.
- No combat action is legal on the reward screen.
- `combat_won` is machine-readable and idempotent.

## Handoff note

Report the exact evidence threshold and state fields cleared. Batch 4 may consume `combat_won` as trusted promotion evidence but must not redefine it.
