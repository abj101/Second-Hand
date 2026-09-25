# Visual object fusion: orchestratable implementation plan

Status: experimental implementation, 2026-09-22. Offline qualification E is failing; live qualification F is blocked. The general tracker default remains `none`.

2026-09-23 scope update: the user selected turn-based/static-interface-first development. The [visual stack decision](lean-visual-stack.md) narrows live CLI choices to `none` and `fused-gpa`; DINO, SAM2 and YOLO-World remain offline benchmark arms. The comparison and frozen E criteria below remain historical qualification evidence, not approval of the lean baseline.

## Outcome

On a trusted STS2 frame, identify the visible controls, individual hand cards, player, and enemies as current-frame objects with stable IDs. OCR supplies names and values to those objects. The action catalog uses current object bindings; text spelling and old coordinates never become object IDs. Preserve the existing capture, lease, and armed-action rules in `system.md`.

Production perception remains application-agnostic. STS2 prompts, annotations, and qualification belong in a game brief or benchmark. The repository is already dirty: preserve every existing change, including the recent 60-second black-transition work. No reset, clean, commit, push, or model download in ordinary tests.

## Evidence and technology audit

The comparison replayed 20 saved 1261×720 STS2 frames from `.tmp/sts2-full-20260922-105336` on an RTX 4060; the latest run's seven frames confirmed the same broad pattern. One of the earlier run's 21 observations had no saved raw frame. Times below exclude the first inference and model load. All open-vocabulary tests used `person`, `animal`, `playing card`, `button`. Mean boxes and ID-set Jaccard are diagnostics, **not accuracy**; a model that sees one stable object can score well on Jaccard while missing the interface.

| Arm | Steady median / p95 | Mean boxes | Observed limitation |
|---|---:|---:|---|
| Legacy GPA GUI YOLO | 17 / 22 ms | 21.6 | Strong HUD/End Turn coverage; misses actors and individual hand-card boxes; some huge regions. |
| YOLO-World | 19 / 24 ms | 1.3 | Mostly the player; weak STS2 UI coverage with tested prompts. |
| SAM2 with YOLO proposals | 156 / 164 ms | 11.1 | Region count grew from 3 to 13, including duplicates; inherits proposal misses. |
| Grounding DINO tiny | 327 / 331 ms | 3.4 | Player and enemy; coarse hand/card regions, little UI. |
| Grounding DINO base | 424 / 430 ms | 11.6 | Player, enemy, End Turn, several HUD controls; duplicate HUD boxes and no five-card split. |
| MM Grounding DINO large | 4,403 / 4,427 ms | 1.0 | One partial player detection with the tested prompts/threshold; lowering threshold on one frame added mostly unlabeled strips. |

Raw experiment script and per-frame outputs are in `.tmp/compare_detectors.py` and `.tmp/detector_sts2-full-20260922-105336_*.json`. The sample is too small and too combat-heavy to establish production recall. Grounding DINO base was tested on both saved runs; MM large was tested on 20 frames and at three thresholds on one latest-run frame.

Increasing GPA input size to 1280 and lowering its confidence threshold exposed small card badges, not five card bodies. A DINO base prompt sweep (`card`, `playing card`, `game card`, `hand of cards`, `card in hand`) at lower thresholds still returned one hand-wide box per prompt. Card separation therefore needs its own measured proposal step; neither tested detector supplies it reliably.

The [official Grounding DINO repository](https://github.com/IDEA-Research/GroundingDINO) exposes tiny and base checkpoints. [IDEA Research's 1.5 Pro/Edge repository](https://github.com/IDEA-Research/Grounding-DINO-1.5-API) documents an API route; no user game frames were sent to that service. [MM Grounding DINO documentation](https://huggingface.co/docs/transformers/model_doc/mm-grounding-dino) lists larger local checkpoints. The [GPA GUI detector](https://huggingface.co/Salesforce/GPA-GUI-Detector) is purpose-trained for UI boxes. A [newer GroundCUA YOLO UI model](https://huggingface.co/laywens/uitag-yolo11s-ui-detect-v1) is a possible challenger, but its model files currently require access acceptance and its published desktop benchmarks do not establish STS2 performance.

### Decision after this replay

Start with the cached GPA GUI YOLO for UI proposals and Grounding DINO tiny for world entities. Run both on each trusted frame during qualification so every proposed box has current-frame evidence. A lower DINO cadence is a later optimization only if a cheap visual tracker independently verifies current geometry between DINO calls. OCR and a small visual hand-card splitter complete the UI path. Keep `--tracker none` as the default until the annotated and live gates below pass. Retain DINO base as a measured alternative if tiny misses actors on diverse scenes; do not make it the default because its extra boxes did not solve card separation. Do not integrate SAM2 or the tested MM large checkpoint at this stage.

This is **role-based fusion**, not averaging detector confidences. GPA answers “where is a UI element?”; DINO answers “where is a character or enemy?”; OCR answers “what does nearby text say?” Detector scores are not calibrated against one another. A single canonical tracker assigns identity after proposals are reconciled.

The simpler option is GPA + OCR + card splitting only. Measure that first. Add DINO to the hot path only if independently annotated world targets remain uncovered; the current combat screenshot indicates they do, but a broader replay decides the production cadence.

## Frozen integration contract

Use the existing `TrackHit` and scene/offer contracts where possible. A source adapter emits only current-frame proposals: normalized bbox, role (`ui`, `world`, or `unknown`), source, class hint, confidence, and frame ID. It cannot assign an actionable registry ID, declare a click safe, or persist a box after it disappears.

1. Reject malformed, off-frame, and full-screen boxes before matching. Preserve raw rejection counts in diagnostics.
2. Merge proposals by role and geometry. For a single physical UI object, GPA owns the control box; DINO may add a semantic hint but never a second offer. World objects use DINO boxes. Overlap alone cannot merge distinct adjacent cards or two enemies.
3. Split a detected hand surface into individual visible cards using visual boundaries and OCR title positions. A hand-wide box is context, never one card offer. If a card cannot be separated unambiguously, withhold that card rather than guess a click point.
4. Bind each OCR token to at most one visible object when geometry is unambiguous. Multiple tokens may label one object. Unmatched OCR stays evidence, not an actionable registry entry. Text changes do not change object identity.
5. Match current objects to previous tracks one-to-one using role, geometry, appearance, and bounded motion. `registry_id` comes from the track. A vanished object loses offer eligibility immediately; historical memory may help a later visible return.
6. Distinguish **visible object**, **eligible control**, and **verified action**. Characters/enemies are targets, not automatically controls. A box with no text can be a UI candidate, but current semantic and armed gates still govern whether it may be clicked.
7. A black transition, focus loss, target change, or real screen replacement invalidates current offers and pending decisions. Resume only from a fresh trusted frame. Never act from propagated or cached geometry alone.

## Orchestration rules

One coordinator freezes the contract and accepts work. Future implementation may assign the bounded packages below to separate agents; this planning task does not dispatch them. At most one writer owns a file at a time. Workers may propose contract changes, but only the coordinator decides them. Keep a visible checklist `P0, A, B, C, D, E, F`; a failed gate blocks its dependents and names the exact failed assertion or measured threshold.

Every handoff reports changed files, test command/results, model actually loaded, latency and detection data where relevant, unresolved limits, and contract deviations. Mock tests do not qualify a live model. Model load failure must be reported as unavailable, never as a zero-detection success. Keep weights out of Git and do not fetch them from core tests.

Dependency graph: `P0 → (A, B, C in parallel, disjoint files) → D → E → F`. E's offline replay and F's live qualification are sequential. The coordinator owns integration files during D; no worker edits them concurrently.

### P0 — Freeze evaluation and baseline (coordinator)

Own: evaluation manifest in ignored `.tmp/`, this plan, no runtime code.

- Record `git status --short`, focused test baseline, detector versions, model hashes, GPU and VRAM. Do not modify pre-existing work.
- Independently annotate the saved combat frames for player, enemy, End Turn, HUD controls, and each of the five visible cards. Add diverse menu, reward, map, combat-animation, and screen-replacement frames before setting a production winner. Use local-only game screenshots; commit synthetic fixtures/tests rather than game art.
- Freeze target matching rules: center-hit for small controls, IoU ≥0.5 for actors/cards, one-to-one assignment, and separate counts for misses, duplicates, oversized regions, and false offers.

Gate: annotations exist independently of model output, with frame hashes and review notes. No downstream task changes the expected set to match its detections.

### A — UI proposals and card separation

Own: new `loop/adaptive/gui_yolo.py`, new `loop/adaptive/card_proposals.py`, their focused tests. No edits to `perceive.py`, `tracker.py`, CLI, or offer extraction.

- Wrap the locally cached GPA weights as an optional detector; expose actual model/load status and boxes, with no auto-download in production tests.
- Separate individual hand cards from a coarse hand region. Use visual boundaries plus OCR positions; test fanned/overlapping cards, duplicate titles, hover enlargement, and a changing hand count.
- Keep HUD indicators and background boxes observable but distinguish them from candidate controls.

Gate: the annotated combat frame yields five distinct card proposals and the End Turn proposal, with no whole-hand actionable box. No two cards share a click box. Missing weights are explicit.

### B — World proposals

Own: new `loop/adaptive/grounding_dino.py`, its focused tests. No edits to A or integration files.

- Wrap DINO tiny with prompt input from a game brief, model status, load/inference timing, and no model download during unit tests.
- Run on every trusted qualification frame. Never turn a missing current object into an offer from its old box. A later cadence optimization requires a separate current-frame visual verification gate.
- Keep base behind an experiment setting for diverse-scene comparison; do not silently substitute it for tiny.

Gate: player and enemy are distinct on the annotated combat frame, with no giant hand box promoted as a control. A detector failure does not yield invented hits. p95 latency is recorded.

### C — One identity across sources

Own: new `loop/adaptive/proposal_fusion.py`, `loop/adaptive/tracker.py`, their focused tests. Do not edit A/B or integration files.

- Implement role-aware one-to-one reconciliation, duplicate suppression, OCR-to-track attachment, disappearance, return, and screen-generation reset.
- Prefer a UI box for a control and a world box for an actor. Keep source-specific evidence and exclusion reasons. Never average uncalibrated scores or produce two registry IDs for one object.
- Test identical card names, changing OCR spellings, animated backgrounds, moving/overlapping actors, detector disagreement, missing detections, and black/replacement frames.

Gate: no duplicate actionable identity per physical object; five same-name cards remain distinct; old offers vanish on the first absent/replacement frame; labels can change without ID changes.

### D — Perception and offer integration (coordinator)

Own: `loop/adaptive/perceive.py`, `loop/adaptive/orchestrate.py`, `loop/adaptive/cli.py`, integration tests, and any necessary game-brief wiring. Accept A/B/C before editing.

- Replace OCR-owned control IDs only when a unique visible fused track owns that control. Keep OCR nodes as information evidence.
- Feed the existing catalog and semantic eligibility path from current tracks. Preserve exact snapshot/catalog/lease binding; do not infer click safety from detector class.
- Expose an explicit experimental fused-perception mode. Leave default `none` and existing shadow/armed behavior intact until qualification.
- Report source contributions, duplicate suppression, missing proposals, track retention, eligible controls, current OCR attachment, latency, and model status.

Gate: frozen replay proves current-frame offers use track IDs; changing label text leaves IDs stable; black/replacement clears offers and pending choices; shadow sends no input. Existing capture and lease tests pass.

### E — Offline comparative qualification (coordinator or separate evaluation owner)

Own: benchmark evaluator under `benchmarks/`, report JSON in ignored `.tmp/`; no production perception edits while measuring.

- Replay the same annotated frames through `none`, GPA+OCR+cards, GPA+tiny DINO+OCR+cards, and GPA+base DINO+OCR+cards. Report per-role precision/recall, card count, duplicate/ID-switch rate, p50/p95 latency, load time, VRAM, and failure cases. Keep raw counts next to filtered offer metrics.
- Test with the Qwen decider loaded on the 8 GB GPU; a detector that OOMs or blocks the decision deadline fails even if standalone detection is good.
- Select the smallest configuration that meets the gates. If GPA+OCR+cards covers annotated world targets, omit DINO from production. If tiny fails and base passes within resource limits, select base. Otherwise record the failure and retain `none` as default.

Gate: on reviewed fixtures, every expected actionable control has a current uniquely bound proposal, five visible cards produce five cards, player/enemy each have a distinct box with IoU ≥0.5, zero decorative/oversized offers, zero stale offers after replacement, and no ID collision. In a 30-frame animated sequence, control-ID retention ≥0.95. End-to-end perception p95 ≤1,000 ms with Qwen resident and no OOM. Do not pass based solely on Jaccard or box count.

### F — Live shadow qualification and decision (coordinator only)

Own: new run workspace and final report; runtime fixes only after a failed gate is diagnosed.

- Run perception-only on the pinned current STS2 window in a fresh workspace, then shadow without native input. Include menu, combat, reward/map, hover, and a real transition only when reached through authorized interaction or operator navigation.
- Verify expected objects against saved target-only frames, 30 consecutive trusted observations per tested surface, stable target identity, no input during black transitions, no stale choices, and actual backend/model names in telemetry.
- Do not switch the general default or claim armed readiness until E and F pass. Armed action promotion remains a separate existing gate.

Gate: the same annotated metrics hold live; shadow produces zero native input; no black-transition termination before the configured 60-second timeout; no action from old geometry. Publish the winner, misses, latency/VRAM, model hashes, commands, and run links.

## Copyable worker handoff shape

For each ready package, the coordinator supplies: package ID, exact owned files, frozen proposal/track contract, local fixture paths, required gate command, and current dirty-file list. The worker returns: files changed, concise behavior summary, gate output, observed model status, measurements, and unresolved risks. A worker must not broaden scope or edit another package's files; request a coordinator decision if the contract is insufficient.

## Execution checkpoint — 2026-09-22

The experimental adapters, fusion mode, and offline evaluator are implemented. Targeted repairs fixed metadata pairing after track reordering, separated all five cards on the original combat frame, withheld hand-wide regions and generic GUI icons from offers, prevented fused-detector failure from resuming OCR-owned offers, and stopped animation pixels at distant screen positions from resetting track IDs. The frozen combat frame now has five matched cards and six provisional offers, all matched to annotated actionable objects; the settings icon is detected but withheld because the UI detector cannot classify it as a control.

E still fails. The hover and three-card frames lack all expected card boxes; DINO tiny/base actor boxes miss the frozen IoU threshold; the hover frame has one unmatched provisional offer; perception p95 remains above 1,000 ms; only three annotated frames are available; and Qwen was not coresident. The latest machine-readable results are `.tmp/fusion_eval/offline_qualification.json` and `.tmp/fusion_eval/e_verdict.json`. Complete those gates before F or any default change.
