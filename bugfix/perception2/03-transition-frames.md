# Batch 3: Transition frames and softlock hygiene

**Complexity: Medium**
**Recommended agent:** orchestrator for `loop/agent/loop.py`; Worker A for tests
**Prerequisite:** Batch 2 gate green
**Primary failure:** 190633Z step 1.4 `empty-action-surface`; two empty surfaces → `decision_softlock`.

## Objective

Treat dark/empty loading frames as observation-only. Softlock only when the surface is empty **and** the frame is stable or already has interactive evidence.

## Owned files

- `loop/agent/loop.py` — `_run_v2_act` empty-surface branch, `_record_perception_telemetry`, inventory-inconsistent rescan.
- Tests: `tests/loop/test_perceive_gate.py` and/or `tests/loop/test_perception_v2_replay.py`.

Do not change legal-intent construction or fusion.

## Required behavior

1. Transition observation: `marks == 0` and (mean luminance below a documented threshold **or** `hud=off` with `mad > 0.03` vs previous).
2. `_run_v2_act` treats that class as `observation_only`: no `empty-action-surface` error row, no `_empty_action_surface_count` increment, no `_decision_softlock`. Wait one quiet cycle (`_wait_stable` polls) and re-capture.
3. Telemetry: `perception.transition=True`.
4. Softlock increment only when `interactive_evidence` is True **or** the frame is stable (`mad <= 0.02`) across the two samples.
5. `inventory-inconsistent` must call `_rescan_scene` (fresh OCR/GUI/CV), not only `_force_perceive` + cache reset.

## Required regression tests

- `test_transition_frame_is_observation_only`
- `test_two_transition_frames_do_not_softlock`
- `test_stable_empty_combat_frame_still_softlocks`
- `inventory-inconsistent` → `forced_rescan` in `test_perceive_gate.py`
- Replay of 190633Z 1.4 fixture produces no error row

## Focused test command

```
python -m pytest tests/loop/test_perceive_gate.py tests/loop/test_perception_v2_replay.py -q
```

Cumulative: `python -m pytest tests/loop -q`

## Gate

190633Z 1.4 replay: no error row. Stable empty combat still softlocks after two samples.

## Stop condition

Stop if observation-only swallows a true empty combat with interactive evidence, or if softlock can never fire.
