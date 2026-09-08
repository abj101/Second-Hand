# Batch 2: Typed Action Surface and Blocked-Control Suppression

**Complexity: Medium**  
**Recommended agent:** standard coding tier  
**Prerequisite:** Batch 1 complete  
**Primary failures:** hallucinated `Next` controls and role collisions between controls and cards.

## Objective

Ensure every allowed control is a typed, current-frame object and suppress controls that are repeatedly proven ineffective on an unchanged surface.

## Owned files

- `loop/agent/prompts.py`, specifically `allowed_controls_for_turn` and `allowed_sets_from_controls`.
- `loop/agent/perceive.py`, limited to control grounding/filtering.
- `loop/os/frame_model.py`, only if canonical role normalization requires a narrow change.
- `loop/agent/loop.py`, limited to suppression state and surface-change reset.
- `loop/mcp/tools/targeting.py`, limited to role classification.
- `loop/os/aim.py`, limited to Prev/Next synthesis rules.
- `loop/memory/overlay_state.py` or a small episodic suppression structure.
- `tests/loop/test_action_surface.py`.
- Relevant focused cases in `tests/loop/test_aim.py` and `tests/loop/test_perceive.py`.

Do not change card transaction execution, memory promotion, victory detection, exports, or model-wide prompt wording.

## Required behavior

1. Build Allowed controls only from objects typed as `button/control/dialog/menu/nav_prev/nav_next` in the current inventory.
2. Never promote OCR text into a control solely because it says `Next`, `Back`, or `Confirm`.
3. Keep card, control, enemy, player, HUD, and OCR-only text in distinct role namespaces.
4. Synthesize Prev/Next only when overlay pagination geometry and overlay state agree; never synthesize them in ordinary combat.
5. Track blocked attempts using `(surface fingerprint, typed control key, verified attempted bounds)`, not mark ID or label alone. Quantize bounds to 0.025 normalized-screen units so small detector jitter does not create a new attempt.
6. First no-change: refresh and rebind once.
7. Second no-change on the same control and unchanged surface: suppress it from Allowed controls.
8. Clear suppression after a meaningful change: screen type changes, typed action-object membership changes, or a tracked object's center/bounds move by more than 0.05 normalized-screen units. Ignore animation-only pixel changes.
9. Suppressed controls may remain visible for diagnostics but may not be actionable.

## Typed admission contract (blocker fix)

Current `allowed_controls_for_turn` takes `scene_labels` / `detector_labels` as `Sequence[str]` and returns `tuple[list[str], str]`. Label strings have no role. That signature cannot enforce role-based admission. **Migrate the function to typed rows.** Do not keep the string signature and “filter somewhere else.”

Reuse `_GENERIC_CONTROL_ROLES` from `loop/os/frame_model.py`:

`{"button", "control", "dialog", "menu", "nav_prev", "nav_next"}`.

Required signature:

```python
def allowed_controls_for_turn(
    *,
    inventory: Sequence[dict[str, Any]] | None = None,
    detector_rows: Sequence[dict[str, Any]] | None = None,
    tutorial_info: dict[str, Any] | None = None,
    host_stats: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str]:
```

- `inventory` / `detector_rows` are current-frame typed objects (`label`, `role`, and geometry when known). Bare label lists are invalid input.
- Return `(admitted_typed_rows, source)` with `source` in `{"inventory", "detector"}`.
- Admit a row only when `str(row["role"]).strip().lower()` is in `_GENERIC_CONTROL_ROLES`. Exclude `other`, HUD, unknown, card, player, enemy, `character`, `unknown_character`, and OCR-only text.
- Prompt-facing label lists are derived **after** admission (`[row["label"] for row in admitted]` then `clickable_labels_from_list`). Labels must not re-enter admission.
- Paginated tutorial may swap in `detector_rows` only when those rows already carry an admit-list role **and** overlay/aim geometry. Do not fabricate `{"label": "Next", "role": "button"}` from a string. Update `reason_controls_for_turn`’s tutorial branch the same way; it currently synthesizes button dicts from detector label strings.
- Four callers in `loop/agent/loop.py` plus `tests/loop/test_perceive.py` must pass typed rows. Update those tests; do not wrap strings as fake buttons to keep old tests green.

`allowed_sets_from_controls` must use the same allow-list. Current else-branch dumps every non-target role (including `other`/HUD/unknown) into `allowed_controls`. Replace that with: role in `_GENERIC_CONTROL_ROLES` → controls; role in `{unknown_character, character, target, enemy, player}` → targets; everything else excluded.

## Implementation steps

1. Migrate `allowed_controls_for_turn` to the typed signature above. Update `allowed_sets_from_controls` to the same role allow-list. Update all callers and focused tests.
2. Introduce a stable control key: `(screen, normalized label, quantized bounds/region, role)`.
3. Introduce a surface fingerprint derived from screen plus normalized typed action objects and quantized bounds; exclude transient mark IDs and raw pixels.
4. Record no-change counts against `(surface fingerprint, control key)`.
5. Filter twice-blocked controls on the typed admitted rows **before** deriving prompt labels and before `validate_choice`.
6. Tighten `infer_overlay_nav`/equivalent so side geometry alone is insufficient in combat.
7. Keep a log reason such as `suppressed:blocked-twice`.

## Required regression tests

- A mid-right combat object labeled `Next` is not actionable without overlay evidence.
- A real tutorial navigation control remains actionable.
- One blocked click allows one refreshed retry.
- Two blocked attempts suppress the control on an unchanged surface.
- Surface change clears suppression.
- A bottom-band `Confirm` typed as button never becomes a card.
- An OCR-only word matching a control label is not admitted to Allowed controls.
- A typed row with role `other`, `card`, `enemy`, `player`, or HUD is excluded even when its label is `Next`/`Confirm`.
- `allowed_controls_for_turn` returns typed rows; callers no longer pass `scene_labels`/`detector_labels` strings.
- A rebound control with a genuinely different bound is treated as a new attempt; detector jitter inside tolerance is not.

## Focused test command

`python -m pytest tests/loop/test_action_surface.py tests/loop/test_aim.py tests/loop/test_perceive.py tests/loop/test_frame_model.py -q`

## Acceptance gate

- The baseline's `Next` sequence would stop after at most two attempts.
- A suppressed mark cannot be selected through either model choice validation or deterministic fallback.
- Existing tutorial Prev/Next tests still pass.

## Handoff note

Document the surface fingerprint fields and suppression-reset condition. Batch 3 will use the same screen/surface transition signal.
