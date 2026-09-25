# Perception Play Plan

Status: execution sequence
Date: 2026-09-21
Vision: [`perception-reaction-orchestration-north-star.md`](perception-reaction-orchestration-north-star.md)

This plan makes a live window playable from pixels. A visible control is offered on the first frame it is stable. Qwen3.5-0.8B chooses only when two or more offers exist. An unchanged region is not re-read. A returned control keeps its id. A click that cannot be undone bans that id. Training happens after the episode.

`system.md` and ADR 0001 stay the product invariants. The old [`north-star-build-plan.md`](north-star-build-plan.md) stays the record of the lease, release, and shadow work. It is not this sequence. Do not load Decider on the live path.

Hardware is one RTX 4060, 8 GB. The pinned weights are `Qwen/Qwen3.5-0.8B` revision `2fc06364715b967f1860aea9cf38778875588b17`.

## Already built

Do not rebuild these.

- Window capture, scene graph, OCR, regions, motion, previous-frame tracks, schema, projection, lease, `Session.act`, release clock, shadow loop.
- `QwenReactionPolicy` loads the pinned checkpoint and accepts only an offered id. The score call sends compact state. It does not send a frame.
- A live text probe can click Settings and roll it back. That probe is a special case. This plan replaces the hand-picked label list.

## Holes

- Track ids die when a region misses one frame. Nothing remembers a control that leaves and comes back.
- A still menu re-runs OCR. Logo text jitters and looks like a new screen.
- A control is not offered until two undone numeric trials, or until a label is on a hand list. A menu with no offers never calls Qwen.
- `negative_evidence` is written and never read.
- Live logs keep metadata. Frame bytes exist only on the benchmark path.
- The steer clock does not wake on a new screen. Waking on raw pixels would fire on animation.

## Rules for every phase

- The model request has ids, labels, and field values. No boxes, key names, or primitives.
- No per-game ontology and no new model dependency in phases 1–4.
- A detector, if a later phase needs one, runs only when a region changed, then yields the card. It is not resident next to Qwen.
- Shadow still sends no input. The first real click stays behind an explicit arm.
- One runnable check per phase. Do not expand the phase to make the check pass.

## Phase 1 — Change gate

Files: `loop/adaptive/perceive.py`, `loop/adaptive/evidence.py`, `tests/loop/test_adaptive_perception.py`

Work: hash each tracked region. If the hash matches the previous frame, reuse that region's OCR and features. Re-read only the regions that changed. A full-frame logo animation must not force a re-read of a still label.

Gate: a test with two identical frames reuses the first frame's OCR tokens, and a test that changes one region re-reads only that region.

## Phase 2 — Registry

Files: `loop/adaptive/perceive.py`, `tests/loop/test_adaptive_perception.py`

Work: remember a control by a short stable label plus appearance. A label that is not a short alphabetic phrase is not a key. Logo garbage and icon-only controls do not enter the registry. When the track id disappears and the same label and appearance return, the registry id is the same. A label that changes is a new id. Coordinates update and are not the id.

Gate: a control absent for one frame comes back with the same registry id. A garbage label does not.

## Phase 3 — Provisional offers

Files: `loop/adaptive/orchestrate.py`, `loop/adaptive/runtime.py`, `loop/adaptive/cli.py`, `tests/loop/test_adaptive_experiment.py`

Work: a registry control that is visible is an offer on that frame. The first armed click is allowed. Confirmation still requires the effect to repeat. The offer description is the label. The leased scene still resolves the point.

Remove the hand list that only allows Settings, Options, and Profile. Do not add a list of dangerous words. After the click, if the pinned window is gone, or the set of registry labels does not return after one rollback and Escape, forbid that registry id and write `negative_evidence`. The next offer pass drops forbidden ids.

Gate: a synthetic menu with two stable labels produces two offers and no coordinates in the model state. A click that does not restore forbids that id. A click that restores does not.

## Phase 4 — Choose

Files: `loop/adaptive/qwen_policy.py`, `loop/adaptive/controller.py`, `tests/loop/test_qwen_policy.py`

Work: zero offers release. One offer is deterministic and does not call Qwen. Two or more call `QwenReactionPolicy` on the worker thread. Attach a crop only when two offers share no distinguishing label. The crop is the region, with enough neighboring pixels that the control is not a bare patch. A reply that is not an offered id is unavailable.

Gate: a fake Qwen is not called for one offer, is called for two, and a test with two identical labels is the only case that passes an image into the prompt builder.

## Phase 5 — Steer

Files: `loop/adaptive/orchestrator.py`, `loop/adaptive/controller.py`, `tests/loop/test_native_faults.py`

Work: the screen signature is the set of registry ids, not raw pixels. Wake the orchestrator when that set changes, when a visible control has no registry key, or when `negative_evidence` is recorded. Do not wake on a frame whose registry set is unchanged. The directive may forbid ids. It may not add one.

Gate: an animated region that does not change the registry set does not bump the directive. A new registry id does.

## Phase 6 — Log for later training

Files: `loop/adaptive/telemetry.py`, `loop/adaptive/arm.py`, `tests/loop/test_native_arm.py`

Work: after the decision is logged, write the frame bytes under the existing frame digest path. The write is off the reaction deadline. The row records registry ids, offer ids, the choice, and whether the effect happened. Nothing in this phase updates weights.

Gate: a shadow frame records a digest and the offer ids. An armed failed effect is present in the ledger and absent from the next offer list.

## Done

The first live gate is phase 3. Phases 1 and 2 exist because a live menu already treated OCR jitter and a dead track id as a new screen. Phase 3 does not wait on Qwen. The plan is done when phases 1–5 pass on a fresh workspace pointed at one live menu: stable labels are offered, an unchanged label is not re-read, a returned label keeps its id, two offers can call Qwen, and an unrestorable click is absent from the next offer list. Phase 6 is the training log. A fine-tune is a later revision, not this plan.

## Out of scope

OmniParser, PaddleOCR, Florence, a second encoder, UI-TARS, a per-game perception table, DOM or engine trees, online training, and combat animation crops beyond the single ambiguous-label crop in phase 4.
