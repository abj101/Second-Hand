# Batch 5B: Graceful Stop and Artifact Finalization

**Complexity: Medium**  
**Recommended agent:** standard coding tier  
**Prerequisites:** Batch 5A complete

## Objective

Make stop-file handling finish at a safe boundary and persist all artifacts without external force termination.

## Owned files

- Collector lifecycle in `loop/agent/loop.py`.
- Command lifecycle in `loop/__main__.py`.
- `loop/log/store.py` and memory-store close/save entry points only as needed.
- `loop/log/schema.py`, **only** the two `SessionSummary` fields named below. Do not add them to `StepLog` or `EpisodeLog`. Do not retouch Batch 5A’s `StepLog` telemetry fields.
- `tests/loop/test_collect.py` and `tests/loop/test_store.py`.

Do not change policy, action validation, OCR, memory contents, or export rules.

## Schema ownership and storage (blocker fix)

Stop/flush is a **session** lifecycle event, not a per-step observation.

| Field | Model | File on disk | Default |
|---|---|---|---|
| `stop_requested: bool` | `SessionSummary` in `loop/log/schema.py` | `summary.json` via `EpisodeStore.write_summary` | `False` |
| `flush_completed: bool` | `SessionSummary` in `loop/log/schema.py` | `summary.json` via `EpisodeStore.write_summary` | `False` |

- Batch 5B owns these two additive fields. Batch 5A must not add them.
- Do not put them on `StepLog` (would duplicate on every turn) or `EpisodeLog` (episode rollup is not the collector lifecycle record).
- Old `summary.json` without the keys still loads as `False`.
- `stop_requested=True` only when the stop file (or equivalent cooperative stop) was observed.
- `flush_completed=True` only after all of: persist any complete current `StepLog` row, save journal, save `memory_grounded.json`, commit/close `objects.db`, write `summary.json`. Partial flush leaves `flush_completed=False`.
- Normal completion (no stop file) still runs the same finalizer: `stop_requested=False`, `flush_completed=True` on success.

## Required behavior

On stop request: schedule no new decision, finish or safely cancel at the current action boundary, persist any complete current row, save journal and `memory_grounded.json`, commit/close `objects.db`, write `SessionSummary` with `stop_requested=True` and `flush_completed=True` to `summary.json`, and exit normally. Implement one idempotent finalizer shared by normal completion, stop-file exit, and safe exception cleanup.

## Required tests

- Stop before decision produces complete artifacts and no action.
- Stop after action but before serialization persists that completed row exactly once.
- Repeated finalization is harmless.
- SQLite can be reopened for read/write after exit.
- Journal and grounded memory parse successfully.
- `summary.json` validates as `SessionSummary` with `stop_requested`/`flush_completed` set as specified; old summaries without those keys still load.
- Normal completion behavior is unchanged (`stop_requested=False`, `flush_completed=True`).

## Focused test command

`python -m pytest tests/loop/test_collect.py tests/loop/test_store.py tests/loop/test_journal_persist.py -q`

## Acceptance gate

A synthetic stop needs no forced process kill and leaves a complete last row, parseable journal/grounded memory, and reopenable database.

