# Batch 4: Memory Truth Maintenance and Training Export Safety

**Complexity: High**  
**Recommended agent:** strong reasoning/coding tier  
**Prerequisites:** Batches 1–3 complete  
**Primary failures:** contradictory role bindings, mislabeled successful actions, and raw HUD/OCR text becoming a lesson.

## Objective

Make memory evidence-based and prevent uncertain or semantically corrupted rows from entering SFT/DPO datasets.

## Owned files

- `loop/memory/journal.py`.
- `loop/memory/lexicon.py`.
- `loop/memory/grounded.py`.
- `loop/memory/notebook.py`.
- `loop/eval/export.py`.
- `tests/loop/test_journal_persist.py`.
- `tests/loop/test_notebook.py`.
- `tests/loop/test_memory_upgrade.py`.
- `tests/loop/test_export.py` and `tests/loop/test_dpo.py`.

Do not change action execution, transition detection, OCR, or model gateways.

## Required behavior

1. Persistent identity includes semantic namespace/role. A control and card at similar coordinates cannot share a binding.
2. Store provenance, confidence, evidence type, and source step for promoted facts.
3. Promotion rules:
   - card success requires verified transaction evidence;
   - control success requires verified screen/turn/control transition;
   - OCR-only labels remain observations or hunches;
   - `combat_won` is trusted transition evidence;
   - pixel movement alone is insufficient.
4. Reject or demote contradictions instead of accumulating incompatible aliases.
5. Notes/lessons must pass semantic sanitation: no raw HUD dump, build/version string, long OCR residue, or mostly numeric text.
6. Mark IDs may appear in short-lived Last/Avoid diagnostics but never define stable semantic truth.
7. Export must reject rows with:
   - `source-unconfirmed`;
   - `target-not-found`;
   - `effect-unverified`;
   - `inventory-inconsistent`;
   - missing/empty intent;
   - unverified card transaction;
   - unresolved stable identity.
8. Use separate predicates: `eligible_as_positive`, `eligible_as_chosen`, and `eligible_as_rejected`. Chosen/positive rows require verified success. Rejected rows may represent failed actions only when their observation/snapshot is comparable to the chosen row, execution provenance is complete, and the failure is a valid negative rather than stale/inconsistent state.

## Implementation steps

1. Add shared evidence helpers plus the three role-specific eligibility predicates above; use them consistently in SFT and DPO export.
2. Add typed provenance/confidence fields with backward-compatible loading defaults.
3. Route `record_click`, `promote_turn_reward`, and notebook lesson ingestion through evidence-specific validators.
4. Add contradiction resolution keyed by stable typed identity. Prefer current live sensor evidence over older memory.
5. Add a conservative lesson sanitizer; reject questionable content rather than trying to rewrite it.
6. Ensure run-scoped journal behavior and existing `no_skill` notebook injection remain intact.

## Required regression tests

- `mk_5` cannot become End Turn, Back, Defend, and Confirm under one stable identity.
- A card pair cannot be recorded as a single-button binding.
- Pixel change without verified effect remains a hunch/non-training fact.
- Positive verified enemy-HP/energy evidence promotes card success.
- `combat_won` promotes a transition fact once.
- The baseline-style raw HUD line is rejected as a lesson.
- Every listed uncertain error is excluded from SFT.
- Every listed uncertain error is ineligible as a DPO chosen row.
- Existing journal files without confidence/provenance still load.

## Focused test command

`python -m pytest tests/loop/test_journal_persist.py tests/loop/test_notebook.py tests/loop/test_memory_upgrade.py tests/loop/test_export.py tests/loop/test_dpo.py -q`

## Acceptance gate

- A generated journal cannot contain incompatible roles for one stable identity.
- No raw OCR dump can enter Lessons.
- A baseline regression fixture identifies and drops the 13 structured-error rows plus the one empty-intent row, reporting categories and step IDs rather than relying only on a brittle total count.
- Valid verified combat actions remain exportable.

## Handoff note

Report schema changes, backward-compatibility behavior, and export counts before/after the new gate. Batches 5A–5C must preserve these fields.
