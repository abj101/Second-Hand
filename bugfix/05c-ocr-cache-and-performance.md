# Batch 5C: OCR Cache and Performance

**Complexity: High**  
**Recommended agent:** strong coding tier  
**Prerequisites:** Batch 5B complete

## Objective

Reduce OCR cost and warning noise without changing recognized action surfaces.

## Owned files

- `loop/os/ocr.py`.
- `loop/agent/perception_cache.py`.
- OCR/perception call sites in `loop/agent/perceive.py` and `loop/agent/loop.py` only as required.
- `tests/loop/test_ocr.py`, `tests/loop/test_perception_cache.py`, `tests/loop/test_perceive_gate.py`, `tests/loop/test_hud.py`, and `tests/loop/test_aim.py`.

Do not change action policy, role semantics, memory, rewards, exports, or finalization.

## Required behavior

1. First verify `_rapid_engine` singleton behavior using `_ENGINE`, `_TRIED_RAPID`, and `_LOCK`. Do not rewrite initialization unless a test proves multiple construction. If startup latency matters, add an explicit warm-up outside timed turns.
2. Treat empty OCR output as a normal empty result and narrowly reduce the RapidOCR warning flood.
3. Cache by relevant crop hash. Screen/surface transitions and changed crops invalidate reuse.
4. Region OCR may cover HUD, hand, overlay, and controls, but uncertain transitions retain full-frame fallback.
5. Record cache hit/miss and region timing metrics.
6. Preserve all existing label/action-surface fixtures exactly.

## Required tests

- RapidOCR construction occurs once per process.
- Empty OCR is returned normally without warning spam.
- Identical crop hits cache; changed crop misses.
- Screen transition invalidates cached regions.
- Full-frame fallback runs for uncertain transitions.
- Existing HUD, aim, and perception fixtures yield unchanged labels.

## Focused test command

`python -m pytest tests/loop/test_ocr.py tests/loop/test_perception_cache.py tests/loop/test_perceive_gate.py tests/loop/test_hud.py tests/loop/test_aim.py -q`

## Acceptance gate

The agent reports before/after timing and cache-hit evidence. No speedup may be claimed from model latency, and no semantic label regression is allowed.

