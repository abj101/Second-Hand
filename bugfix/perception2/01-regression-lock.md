# Batch 1: Regression lock

**Complexity: Medium**
**Recommended agent:** Worker A (fixtures, tests, regression review)
**Prerequisite:** overlay/pagination fix committed (`fix: do not paginate combat on stale nav labels`)
**Primary failure:** 191012Z ACT snapshot empty despite valid LLM1; 190633Z step 1.4 `empty-action-surface` on a transition frame.

## Objective

Freeze the two failure observations as fixtures and a replay test that (a) proves leftover `Prev`/`Next` no longer paginate combat, and (b) documents D1/D2 as `xfail(strict=True)` until Batch 2.

## Owned files

- `loop/fixtures/sts2_screens/` (new json/jsonl rows and optional copied frames; truth visible only to the scorer).
- `tests/loop/test_action_surface.py` or a new `tests/loop/test_perception_v2_replay.py`.
- Do not edit `loop/agent/loop.py`, `loop/os/aim.py`, or fixtures' game logic.

## Required behavior

1. Extract from `runs/20260907T191012Z_sts2_collect/turns.jsonl` the first combat step whose LLM1 `perceive_raw` lists five hand cards + End Turn + an enemy bbox while the ACT snapshot has empty `cards`/`targets`. Persist aim marks, LLM1 rows, overlay/activity, and layout fields.
2. Extract from `runs/20260907T190633Z_sts2_collect/turns.jsonl` step 1.4 (`marks=0`, `hud=off`, pixels changing). Persist enough to classify it as a transition frame.
3. Replay path: `build_action_surface` → reconcile → `enrich_frame_marks` → `_build_turn_snapshot` (or the smallest public/testable seam that covers that chain).
4. Overlay assertion (must pass now): layout is not `paginated_overlay`; `allows_domain_entities` is True when combat is live and tutorial is closed.
5. D1/D2 assertions (xfail until Batch 2): `len(cards) == 5`, End Turn control present, at least one enemy target **or** an explicit inspectable blocker with geometry.

## Implementation steps

1. Confirm the overlay commit is present (`paginated_tutorial_active` shared by `infer_overlay_nav` and `infer_layout`).
2. Copy compact JSON (not the entire turns.jsonl) into `loop/fixtures/sts2_screens/`. Prefer existing `combat_turn.jpg` if the bbox space matches; otherwise store marks-only JSON.
3. Add `proposal_replay.jsonl` / `labels.jsonl` rows with truth only in the scorer fields.
4. Write the replay test with `pytest.mark.xfail(strict=True, reason="perception-v2 D1/D2")` wrapping only the inventory assertions.

## Required regression tests

- Overlay: leftover Next/Prev on a combat board with `tutorial_open=False` does not paginate or strip the hand (`test_combat_ghost_next_does_not_paginate_or_strip_hand` already exists; keep it).
- Replay xfail for 191012Z inventory.
- Transition-frame fixture loads and reports `marks == 0` (no behavioral change in this batch).

## Focused test command

```
python -m pytest tests/loop/test_action_surface.py tests/loop/test_perception_v2_replay.py -q
```

Cumulative: `python -m pytest tests/loop -q`

## Stop condition

Stop after the replay test is committed and the overlay assertions pass. Do not implement D1/D2 promotion in this batch.

## Handoff

Finding, fixture paths, test counts, which assertions are xfail, unresolved concerns.
