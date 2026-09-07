# Batch 3: Live tooling

**Complexity: Low**
**Recommended agent:** Worker A
**Prerequisite:** Batch 1 committed (Batch 2 may run concurrently)
**Primary failure:** `scripts/monitor_sts2_loop.py` crashed at step ~20 (`NameError: populated`, `:60`), leaving the operator blind during Run B; `inspect-run` has no check for non-canonical ids.

## Objective

Operator can watch a live collect end-to-end and get a one-line verdict on id hygiene without reading `turns.jsonl` by hand.

## Owned files

- `scripts/monitor_sts2_loop.py`
- `loop/__main__.py` `inspect-run` (or `loop/eval/inspect_run.py` if that is where the report lives)
- `tests/loop/test_inspect_run.py` (new or existing)

No edits to `loop/agent/*`.

## Required behavior

### 3a — monitor

- Define `populated` in `stuck_reason` as "any row in the window has `turn_snapshot.cards` or `turn_snapshot.targets` non-empty" (or delete the guard if the HUD-frozen rule should apply regardless). Cover with a unit test that feeds the Run B tail.
- Add a live counter line: `noncanonical_ids=<n> target_not_found=<n> source_unconfirmed=<n> forced_rescan=<n>/<steps>` updated every poll.
- Stuck rule: ≥ 4 consecutive `target-not-found` → print `STUCK: target bind loop` and exit non-zero (operator then stops collect cooperatively).

### 3b — `inspect-run`

Add to the report:

- `noncanonical_target_ids` — count of snapshot `card_id`/`target_id` matching `^(object:|mark_|mk_)\d+$` or `is_prompt_instance_id`.
- `duplicate_target_bboxes` — steps where two targets have IoU ≥ 0.45.
- `confidence_out_of_range` — count of `identity_confidence` outside `[0, 1]`.
- `router_reason` histogram and `perception_source` histogram (already computed ad hoc in the V2 report; make them first-class).

Running it on the preserved Run B must show the pre-fix numbers (`noncanonical_target_ids ≥ 16`, `confidence_out_of_range = 10`) so the tool is proven against known-bad data.

## Focused test command

```
python -m pytest tests/loop/test_inspect_run.py tests/loop/test_monitor_sts2.py -q
python scripts/monitor_sts2_loop.py --run runs/20260907T200835Z_sts2_collect --once
python -m loop inspect-run runs/20260907T200835Z_sts2_collect
```

## Gate

Monitor completes on Run B without exception; `inspect-run` prints the four new fields with the expected pre-fix values. Commit as `chore: monitor and inspect-run report non-canonical target ids`.
