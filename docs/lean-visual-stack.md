# Visual stack decision: turn-based first

Date: 2026-09-23. Scope confirmed by the user: turn-based games and mostly static interfaces first, retaining a general pixels-only architecture. Three independent GPT-6 Luna agents at high reasoning effort reviewed detectors/OCR, tracking research, and local implementation. Recommendations below were checked against the local code and saved reports. No new GPU accuracy benchmark was run for this decision.

Execution handoff: [Sol 6 / DeepSeek 4.1 Flash implementation plan](lean-perception-implementation-plan.md), including package ownership, acceptance tests and qualification boundaries.

## Decision and implementation boundary

Use **GPA GUI proposals + RapidOCR Small + one CPU identity registry** as the next experimental visual baseline. Keep neutral scene evidence, the adaptive schema, current-frame binding, and action-effect verification. Spend the next engineering effort on temporal identity, card/hover geometry, and honest timing rather than another resident model.

The live `collect --tracker` interface now accepts only `none` and `fused-gpa`. DINO tiny/base, SAM2 and YOLO-World have been removed from that interface; their existing adapters and offline benchmark entry points remain so comparisons are reproducible. No weights or prior work were deleted. `none` remains the legacy default, not an accuracy winner. GPA-only has not passed qualification E and must not be represented as generally playable.

This is a narrowed live model surface, not a completed temporal redesign. The timing, identity, cache and outcome repairs listed below remain outstanding. Programmatic research factories still expose the old arms. OmniParser was an offline comparison, not a live integrated backend, and should stay that way.

## What the machine and data actually establish

Fresh read-only inspection confirmed RTX 4060 with 8,188 MiB VRAM. The documented policy environment has Torch 2.14.0+cu130, Transformers 5.17.0, RapidOCR 3.9.2 and ONNX Runtime 1.30.0. It currently lacks `ultralytics`, so the normal Qwen environment cannot yet load the GPA adapter. Install the existing tracking extra in that environment and recheck CUDA/package compatibility before a same-process run; do not infer coresidency from separate successful loads.

The installed OCR configuration defaults to PP-OCRv6 Small detection/recognition on ONNX Runtime CPU. The fused selector uses Small, while the legacy selector requests Medium. Keep this measured Small path; pin model version, file hashes, provider and package versions in the next run manifest. Do not assume the label “Small” will always identify identical weights after an upgrade.

The setup and smoke-check scripts still center the retired Decider package and its Triton/FLA checks, while the live Qwen loader uses Transformers. That is environment-maintenance debt, separate from resident inference cost. A lean setup should validate the actual Qwen + GPA + OCR path and isolate legacy experiments. Do not blindly uninstall shared acceleration dependencies from the working environment; first build and smoke-test the smaller profile alongside it. This review did not change installed packages.

The September 22 [offline report](../.tmp/fusion_eval/offline_qualification.json) contains only three annotated combat frames:

- GPA-only fusion matched **5/5, 3/5 and 1/3 cards**. The dedicated splitter produced **5, 3 and 0** proposals; a separate GUI proposal explains the last frame's one matched card. Proposal count and matched-card count are different measurements.
- All three fused arms produced **6, 4 and 2 offers**, with **0, 1 and 0 unmatched offers**. Adding DINO did not improve those UI outcomes.
- GPA-only frame times were **3,255 ms cold, 731 ms and 626 ms warm**. Tiny-DINO fusion took 1,698/1,064/968 ms; base-DINO fusion took 1,295/1,163/1,160 ms. OCR was already warm in the latter arms. These are individual sample times, not reliable population p95 estimates or a fair cold-start comparison.
- Tiny/base DINO each matched **0/6 actor boxes** under frozen IoU >= 0.5. The repair report separately records raw actor centers inside both actor annotations on all three frames. DINO has point-localization signal; it is not proven useless. Removing it loses that world evidence and does not solve enemy targeting.
- The earlier 20-frame detector comparison recorded YOLO-World at 19/24 ms median/p95 but only 1.3 boxes per frame with weak UI coverage. SAM2 plus YOLO proposals was 156/164 ms and accumulated duplicate regions. These are separate detector experiments, not end-to-end accuracy qualifications.
- The OmniParser CPU comparison covered no card bodies and cost roughly 1.0–1.2 seconds per frame; its GPU path failed in the tested environment. No evidence supports integrating it now.

The [repair log](gate-e-repair-plan.md) and [coresidency check](../.tmp/fusion_eval/qwen_coresident_check.json) document limitations. Only two adjacent control-ID comparisons underlie the apparent 100% retention. No claim of preserved overall accuracy or production temporal reliability follows from this sample.

## Why this architecture fits the research

[GPA's model card](https://huggingface.co/Salesforce/GPA-GUI-Detector) describes a YOLO GUI-element detector. Its specialization fits UI proposals, but its desktop examples do not establish card-game coverage. [Grounding DINO](https://github.com/IDEA-Research/GroundingDINO) supplies text-conditioned object detection; it does not provide temporal identity or click semantics. Keep it out of the regular loop until world-target tests demonstrate enough marginal usable coverage to justify its cost.

[SORT](https://arxiv.org/abs/1602.00763) shows that inexpensive association can be effective and emphasizes detector quality. [ByteTrack](https://arxiv.org/abs/2110.06864) improves association by recovering useful low-score detections. These support a tracking-by-detection approach, not a guarantee on overlapping cards. Their benchmark speeds and pedestrian results are not RTX 4060 game-interface measurements. Do not add a full tracking framework, learned ReID network, or camera-motion compensation just to manage a small static interface.

[SAM2](https://github.com/facebookresearch/sam2) is prompted image/video segmentation. Temporal mask propagation cannot discover objects omitted by its proposals or establish their interaction meaning. Its published GPU speeds use different hardware; masks and streaming model state are unjustified here until a concrete mask-dependent task appears.

[RapidOCR](https://github.com/RapidAI/RapidOCR) already provides the ONNX OCR route we need. Keep one full-frame OCR read as the correctness baseline on changed scenes. The previous region implementation multiplied work into 25–28 calls while losing card-title evidence. Do not reintroduce that optimization unchanged. No OCR replacement or new detector challenger is justified by the present evidence.

## Target temporal design

`trusted capture/time → change check → proposals + OCR → association → scene evidence → schema projection → bounded choice → fresh effect observation`

1. Capture cheaply and retain its true monotonic time. A screen transition or target/focus loss invalidates current offers and in-flight choices.
2. Skip expensive work only when fresh pixels verify the relevant region is unchanged. Start with an exact-frame reuse baseline, then evaluate region invalidation independently; never equate an old box with a currently observed object. Changed or uncertain regions are re-observed.
3. Keep one identity owner. Separate current location, last-seen time, visibility, OCR labels/values, and semantic hypotheses. A remembered object can remain in history without remaining clickable.
4. Use role, geometry, scale and tolerant appearance to associate objects one-to-one. Current greedy IoU matching ignores appearance for continuing tracks; returned tracks require exact equality of an 8x8 crop hash. Replace that brittle return criterion with measured patch similarity and ambiguity rejection. Evaluate global assignment if adjacent cards swap identities; use an existing standard implementation rather than writing a tracking framework. No learned embedding model initially.
5. Attach text by unique spatial ownership, not object/text IoU alone: a small title inside a large card naturally has low IoU. Preserve uncertain text as unbound evidence. Same-title cards remain separate identities; indistinguishable instances may need abstention rather than invented continuity.
6. Treat card splitting as a proposal heuristic, not an application ontology. Its current lower-screen, wide-region and OCR-column assumptions need hover, overlap and changing-count tests. If it cannot produce safe per-card evidence, withhold those targets. More detectors do not automatically fix this geometry problem.
7. Keep expensive semantic interpretation in the slower schema/orchestration path, triggered by new objects, unresolved meaning or failed effects. The current optional VLM is called synchronously from perception when configured; that contradicts the desired separation and its latency is absent from the detector replay. Refactor that scheduling before claiming the lean design is implemented.
8. Preserve unknown world targets explicitly. GPA-only is a UI baseline, not a full scene-understanding solution. Evaluate occasional grounded world proposals only when a required interaction is blocked, followed by current-frame visual validation. No proven replacement world-localizer exists in this repository today; that remains a separate bounded experiment.

For the first turn-based product, a measured one-second changed-scene budget is a reasonable engineering target, not a universal safety threshold. Unchanged-scene verification should be much cheaper. Hover and animations can invalidate geometry in less than a second; fresh binding validation is still mandatory. No optical flow, SAM2, TensorRT export or additional GPU model until measurements identify a problem those components actually solve.

## Next work, in order

1. Repair timing and evaluation: propagate true capture time through the live loop; measure capture-to-decision/lease including perception, schema, queues and semantics. Separate startup from warm samples. Record actual providers/devices and peak allocated/reserved and whole-device VRAM with the game and Qwen running.
2. Repair evaluator semantics: oversized rejections are currently counted as `oversized_offers`; log rejected and actually offered regions separately. Record source-level raw proposals too. Stability of empty sets is not coverage. Include missed visible objects in identity denominators and independently annotate intended click regions as well as full boxes.
3. Establish the lean baseline in the same policy environment on diverse menu/combat/hover/hand-change/reward/map/transition sequences. Preserve the frozen E verdict; use separate no-input diagnostic acquisition rather than declaring qualification passed.
4. Fix the smallest demonstrated association/OCR-ownership/card-proposal errors against held-out sequences. Measure before/after on identical captures. Prefer uncertainty over false identity continuity.
5. Measure bounded supervised interactions only after timing and binding checks work: correct target, actual effect, no-op, wrong effect, abstention, and recovery. A successful input API call is not an action success.

[HOTA](https://arxiv.org/abs/2009.07736) motivates separating detection, association and localization quality. For our small corpus, publish those raw counts and failure cases before adopting a large metric framework. Also measure field-value accuracy/freshness and erroneous schema promotions: temporal understanding succeeds when the schema tracks meaningful changes, not merely stable rectangles.

Acceptance requires useful current-target coverage, no observed stale/wrong-target actions on the test set, stable identities across real changes, bounded actual latency and verified effects. Zero observed errors on a finite set is not proof of zero population risk. A larger model returns only after a held-out ablation demonstrates better required-target coverage or verified effects within the measured machine budget.

## Validation of this change

The CLI restriction, existing fused-perception behavior, spatial tracking and evaluator checks passed: **35 tests**, using `.venv/Scripts/python.exe -m pytest tests/loop/test_cli_env.py tests/loop/test_fused_perception.py tests/loop/test_spatial_tracker.py tests/loop/test_visual_object_fusion_eval.py -q`. This is code validation, not GPU model qualification. The earlier full-suite run was 392 passed, 2 failed and 9 deselected; the shutdown-release expectation and pending-Qwen shadow test remain unresolved by this narrow change.
