# Batch 2: Combat inventory truth

**Complexity: High**
**Recommended agent:** Worker B for `frame_model.py` / `sts2.py` / `overlay_state.py`; orchestrator for `loop/agent/loop.py`
**Prerequisite:** Batch 1 fixtures + overlay commit
**Primary failure:** LLM1 `hand_cards`/`enemies` never become ACT `cards`/`targets`.

## Objective

Close D1 (card promotion), D2 (anonymous enemy inspectable), and D4 (tutorial page current-frame truth) so the 191012Z replay yields a playable combat snapshot.

## Owned files

Orchestrator:

- `loop/agent/loop.py` — `_reconcile_scene_marks`, `_build_turn_snapshot`, STS2 identify-before-act force-ACT path.

Worker B:

- `loop/os/frame_model.py` — `legal_intents` / `_legal_intents` for inspect on low-confidence enemies.
- `loop/fixtures/sts2.py` — `enrich_frame_marks` only if a stamp is missing after reconcile; do not suppress domain entities on leftover nav.
- `loop/memory/overlay_state.py` — tutorial page prefers current-frame LLM1 when frame hash changed; live combat board with no dialog closes tutorial.
- `loop/agent/decision.py` — `legal_intents_for_snapshot` inspect for low-confidence targets.
- Tests listed below.

Do not edit fusion in `aim.py`, export filters' structure (only verify they still drop uncertain rows), or prompts beyond overlay imports already moved.

## Required behavior

### D1 — LLM1 hand_cards promote unlabeled hand marks

In `_reconcile_scene_marks`, allow an LLM1 `hand_cards` row to set `role="card"` when **all** hold:

- `context.allows_domain_entities`
- mark `y >= HAND_Y` (same constant `Sts2Fixture` uses)
- bbox IoU or center match with the LLM1 row
- mark role in `{unknown, board_candidate, icon}` — never `button`, `dialog`, `nav_prev`, `nav_next`
- provenance stamped `llm1-current-frame`

Keep `compatible()` strict for every other role. Card `object_id` is still assigned by `_prepare_frame_marks` (`card_N`), never an LLM1 id.

### D2 — anonymous hostile is inspectable

When `identify-before-act` fires on the STS2 path:

- If the anonymous hostile has current-frame geometry, publish it as a snapshot target named `unknown enemy` with low `identity_confidence`.
- Store `object_id` remains `enemy_N` (or a non-prompt id from `_prepare_frame_marks`). Drop/replace `unknown_character_N` before `_build_turn_snapshot` so `is_prompt_instance_id` does not strip the target.
- Add `inspect` to `legal_intents_for_snapshot` for such targets.
- `play_card` targeting a low-confidence target stays legal.
- Verify `loop/eval/sft.py` and `dpo.py` still exclude uncertain rows (do not weaken filters).

Do **not** auto-run inspect. Policy stays in control.

### D4 — tutorial page current-frame truth

- `overlay_state` prefers current-frame LLM1 `tutorial_page` over cached OCR when they disagree and the frame hash changed.
- When layout is `combat_board` (or combat activity) and no dialog frame is detected, tutorial is closed regardless of leftover `Prev`/`Next` labels.

## Required regression tests

Flip Batch 1 xfails to pass. Add:

- `test_llm1_hand_cards_promote_unlabeled_hand_marks`
- `test_llm1_cannot_promote_button_to_card`
- `test_anonymous_enemy_becomes_low_confidence_target_with_inspect`
- `test_tutorial_page_prefers_current_frame`

Must keep passing:

- `test_bottom_controls_never_become_cards_outside_combat`
- `tests/loop/test_combat_fail_closed.py`
- `tests/loop/test_canonical_sprite_ids.py`
- `tests/loop/test_layout_adapter.py` (generic adapter still skips takeover)

## Focused test command

```
python -m pytest tests/loop/test_perception_v2_replay.py tests/loop/test_action_surface.py tests/loop/test_two_stage_resolve.py tests/loop/test_layout_adapter.py tests/loop/test_combat_fail_closed.py tests/loop/test_canonical_sprite_ids.py -q
```

Cumulative: `python -m pytest tests/loop -q`

## Gate

Replay of 191012Z frame yields 5 cards + End Turn + 1 target. Worker A independently reviews the diff. Then commit.

## Stop condition

Stop if promotion would accept buttons as cards, if prompt ids leak into store `object_id`, or if generic adapter starts taking over combat roles.
