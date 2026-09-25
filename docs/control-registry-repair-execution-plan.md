# Control registry repair: orchestrated execution plan

Status: ready for implementation; this document does not execute the work.

## Objective

Produce stable, uniquely bound control offers from an animated screen, classify uncertain candidates through optional cloud vision, and make Choose consume the exact catalog it was given. Qualify perception, shadow reaction, and reversible experiments in separate stages.

Production code must remain application-agnostic. STS2 is a qualification target and may appear in fixtures and run reports only. Read `system.md` before implementing.

## Evidence and corrections to the previous report

The recorded WGC run is at `C:/Users/Abj1/AppData/Local/Temp/avatar-sts2-qualification-20260921-wgc/`.

- All 30 observations reported trusted WGC capture and the same target identity.
- Five main menu labels and the profile text were consistently recognized. Recognition alone does not establish clickability or reversibility.
- Registry entries largely represented changing logo transcriptions on a nearly full-screen region. The actual menu OCR nodes did not receive registry identities.
- Region counts varied; track stability was 0.9351. OCR-set continuity was 0.1481, and surface stability was 0.2011.
- The last three shadow decisions failed with `catalog_revision mismatch`.
- Shadow Choose **did execute inside the collection command**. The previous statement that it was never attempted was incorrect. Real Qwen execution was not demonstrated; deterministic fallback can conceal model-load failure.
- `_field_keys()` currently measures OCR node IDs, not promoted field availability. Keep that diagnostic, but give it an accurate name.

Relevant current entry points:

- `perceive.py`: `SceneAssembler._apply_registry`, `_registry_id_for`, `_bind_ocr_clusters`, `assemble_scene`.
- `orchestrate.py`: `ProvisionalOffer`, `registry_offers`, `registry_label_set`.
- `runtime.py`: `project`, `project_registry_offers`.
- `candidates.py`: `compile_transient_one_click_plan` and catalog/mask compilation.
- `policy_worker.py`: `_Job`, `submit`, `_loop`; `qwen_policy.py`: `score`.
- `controller.py`: `NativeShadowLoop.process_frame`, `_PendingTurn`, `_apply_worker_decision`.
- `cli.py`: `_shadow_policy_worker`, `_shadow_collect`, `_shadow_metrics`, `cmd_collect`.
- `loop/model/gateway.py`: existing `ProposalGateway`; reuse its image/structured-response interface.

## Orchestrator rules

Use one coordinator and up to three implementation agents. Luna High is the intended worker profile from this session. The coordinator makes contract and integration decisions; workers receive one bounded task each.

1. Record the current dirty worktree and baseline test results. Preserve all existing changes. Do not reset, clean, commit, or push as part of this plan.
2. Freeze the contracts below before dispatch. Workers may suggest changes but must not independently redefine them.
3. Enforce one writer per file, including test files. Shared changes are handed to the coordinator as a proposed patch or explicit request.
4. Dispatch only dependency-ready tasks. Offline implementation may run in parallel; live qualification is strictly sequential.
5. Require each worker to return changed files, behavior, test commands/results, unresolved limitations, and any contract deviation.
6. Inspect code and run boundary tests before accepting a task. A passing mock test is not evidence that the live model or capture backend worked.
7. Stop live progression when a preceding gate fails. Do not lower thresholds, hide noisy objects from reports, or relabel deterministic fallback as Qwen to pass.
8. Keep a checklist in the execution task: P0, A, B, C, D, E, F. A blocker must name the failing assertion and a next diagnostic.

## Frozen contracts

### Identity and observation

- A registry ID identifies one candidate across frames. A scene node ID binds that candidate in one current observation. Neither is a screen coordinate.
- Assign text-candidate identities to OCR nodes. Do not assign a second actionable identity to the enclosing region for the same text.
- Match normalized text, geometry, and appearance jointly, using deterministic one-to-one assignment. Start with existing normalization and appearance tolerances; add geometry-based disambiguation. Do not add unrestricted fuzzy text matching.
- Identical labels in different locations retain distinct identities. Ambiguous matches remain unoffered. Absence removes the current offer immediately; historical identity may remain for a later unambiguous return.
- A real screen replacement invalidates current observations, pending decisions, and semantic confirmations. It must not resurrect cached labels. Historical registry memory never counts as current visibility.
- Registry membership does not assert that a candidate is a control. Separate candidate identity, offer eligibility, and semantic evidence.

### Offers and semantics

- Keep the public `ProvisionalOffer(offer_id, label, node_id)` contract unless the coordinator approves a necessary change. Eligibility metadata can live on scene attributes/evidence.
- Reject degenerate/out-of-bounds boxes and oversized background regions from offers. Initial generic maximum offer-box area: 25% of client area, a named configurable parameter with boundary tests.
- Record the reason each candidate is eligible, withheld, or rejected. Decorative and unknown candidates remain visible in diagnostics.
- Cloud confirmation returns candidate IDs and one class per ID: `control`, `decoration`, or `unknown`, with a short rationale. It cannot return actions, coordinates, replacement IDs, or permission to actuate.
- Bind confirmation to target identity, screen generation, candidate IDs, and cited frame evidence. Count agreement only across two distinct current trusted observations; cached responses are not independent confirmations.
- Default semantic mode is off. When enabled and required for eligibility, unresolved or conflicting classifications remain withheld. Cloud classification alone never proves action safety, reversibility, or effect.
- Existing local provisional-offer behavior may remain available for shadow evaluation with its provisional status explicit. Armed execution retains the existing experiment/admission requirements.

### Choose and qualification

- Every job freezes its snapshot, candidate catalog revision, ordered candidate IDs, directive revision, generation, and image/crop when applicable. Results must refer to that job's context.
- Do not update shared model fields or patch a result to the latest revision to make it pass validation. Stale jobs are rejected.
- Zero candidates release/refuse; one chooses deterministically; two or more submit one model request per decision job. Polling that job must not resubmit it.
- Cloud semantic confirmation and local Qwen reaction are distinct roles with separate telemetry.
- Separate perception-only and shadow-reaction commands/modes. Perception-only must not construct or call a reaction worker.
- Existing armed invariants in `system.md` remain mandatory. A transient offer or hypothetical lease is not a promoted action.

## Dependency schedule

P0 freezes contracts and fixtures. Then run A, B, and C in parallel. After all three are accepted, run D and E in parallel with separate file ownership. The coordinator integrates D/E and executes F.

### P0 — Baseline and replay fixture contract (coordinator)

Deliverables:

- Capture baseline `git status --short` and `python -m pytest -q` results.
- Inspect stored frame encoding before attempting replay; `.bin` is not guaranteed to be PNG. If replay cannot recover frames, document that limitation and use generated fixtures plus fresh target-only capture at F.
- Specify shared generated fixtures: five neutral menu labels, an animated/noisy logo, intermittent version text, two identical labels at different locations, remove/return, and an unrelated replacement screen.
- Define expected control identities independently of implementation output. Keep all application-specific fixture data under tests.

Acceptance: agents receive this document, their exact ownership, and agreed fixture expectations. No runtime changes in P0.

### A — OCR identity ownership (agent A)

Own: `loop/adaptive/perceive.py`, new `tests/loop/test_control_registry_identity.py`. Do not edit offer extraction or controller files.

Implementation:

1. Assign registry IDs from OCR records using their own boxes and image appearance, before scene assembly.
2. Remove duplicate actionable ownership on enclosing tracks; preserve region observations for diagnostics.
3. Add geometry and one-to-one matching to label/appearance lookup. Refuse ambiguous duplicate-label matches instead of choosing the first entry.
4. Preserve stable OCR-node identity for unchanged tokens. Keep current-frame visibility separate from durable history.
5. Expose minimal candidate metadata needed by offer extraction: ownership source, normalized label, and current evidence/geometry.

Required tests: 30 identical frames; 30 frames with changing background/logo; two identical labels; bounded geometric jitter; remove/return; immediate old-label removal on screen replacement; no OCR/region duplicate ownership.

Acceptance: fixture controls retain the same IDs in all visible frames; simultaneous distinct controls never share an ID; old controls are absent on the first replacement frame. Logo noise can remain raw evidence without corrupting control IDs.

Handoff: exact scene attributes and example nodes for D/E. Do not claim these candidates are safe clicks.

### B — Request-scoped Choose context (agent B)

Own: `loop/adaptive/policy_worker.py`, `loop/adaptive/qwen_policy.py`, new `tests/loop/test_choice_context.py`. Request coordinator review before changing the shared policy interface.

Implementation:

1. Trace the recorded catalog mismatch. Audit all policy implementations and callers before changing signatures.
2. Carry immutable catalog context through the worker job and result. Eliminate reliance on a model-instance catalog revision for per-request decisions.
3. Freeze optional crop/image data per job; audit mutable `_crop` state for cross-job races.
4. Return explicit unavailable/error outcomes on inference failure. Preserve enough error information for diagnostics without logging credentials.

Required tests: catalogs A/B submitted sequentially and with delayed A completion; stale A rejected under B; snapshot/directive/generation change rejection; no cross-job crop swap; exact allowed ID parsing; no coordinate fields in reaction requests.

Acceptance: valid current jobs validate; stale jobs fail for the correct reason. Coordinator implements controller/CLI adaptations in E.

### C — Optional semantic confirmation component (agent C)

Own: new `loop/adaptive/semantic_confirmation.py`, new `tests/loop/test_semantic_confirmation.py`. Use `ProposalGateway` through injection; no provider SDK or new model framework.

Implementation:

1. Implement strict request/result validation for the frozen semantic contract. Send trusted target-only images with candidate IDs and annotated boxes; boxes are evidence supplied by runtime, never coordinates generated by the model.
2. Reject missing/duplicate/unknown IDs, extra action/coordinate fields, invalid classes, stale generation, or changed target identity.
3. Cache classifications by target, screen generation, candidate identity, and appearance compatibility. Invalidate affected candidates on material change.
4. Request on discovery, hard screen changes, or materially changed candidates. Stable confirmed frames make zero requests. Bound to one outstanding batch, at most two confirmation attempts per candidate per generation, and a finite timeout; expose exhausted/unavailable states.
5. Require two agreeing classifications from distinct observations for confirmed status. Any conflict remains unresolved until a new valid confirmation cycle is explicitly triggered.
6. Record model identity, evidence IDs, latency, outcome, and available usage metrics. Do not infer cloud usage from the presence of configuration.

Required tests use a fake gateway: success, partial/invalid responses, conflict, timeout, missing configuration, stale target/generation, repeated-frame caching, and request-budget exhaustion.

Acceptance: component works offline through injection; it has no input side effects and cannot promote schema/actions. Use an explicitly configured provider/model for future live calls; do not guess model availability or download a model.

### D — Eligible offers and Steer stability (agent D, after A/C)

Own: `loop/adaptive/orchestrate.py`, `loop/adaptive/orchestrator.py`, `loop/adaptive/runtime.py`, `loop/adaptive/candidates.py`, new `tests/loop/test_control_offer_pipeline.py`.

Implementation:

1. Extract offers from current OCR-owned identities and reject duplicate/ambiguous ownership. Apply geometry, visibility, forbidden-ID, and configured semantic eligibility checks.
2. Preserve raw observations and explain exclusions. Keep oversized region tracks out of the actionable surface.
3. Project eligible offers through runtime's single legal-surface authority. Compile one-click transient plans against the exact current node without creating durable actions.
4. Make Steer signatures depend on the eligible control set and material eligibility changes. Raw logo text and background region changes must not count as screen transitions.
5. Coalesce identical signatures. Distinguish a signature-triggered directive revision from TTL renewal or an explicit intent/forbid change.

Required tests: five eligible controls remain five offers under logo animation; decoration/unknown cases in semantic mode; duplicated labels; forbidden IDs; true removal and return; whole-screen rejection; empty offers; unique frozen binding; one initial Steer signature wake and one per real control-set change.

Acceptance: raw noise is still reported while eligible control IDs, catalog membership, and Steer signature remain stable on the fixture. No control names are hardcoded in production.

### E — Integration, metrics, and explicit run modes (coordinator or integration agent)

Own: `loop/adaptive/controller.py`, `loop/adaptive/cli.py`, `loop/adaptive/telemetry.py`, new `tests/loop/test_qualification_modes.py`. Do not edit D's files concurrently.

Implementation:

1. Connect B's immutable context to submission, completion, validation, and hypothetical dispatch. Preserve exact lease binding.
2. Wire C's optional confirmation component outside the reaction hot path; apply results only to matching current context. Reuse an existing background mechanism where practical.
3. Add explicit CLI qualification stages (`--stage perception`, `--stage shadow`) and semantic mode (`--semantic-confirmation off|required`). Preserve documented existing invocation behavior unless a migration is necessary.
4. Perception stage performs capture, local perception, optional confirmation, and metrics without constructing a reaction worker. Shadow stage permits hypothetical dispatch only.
5. Replace silent Qwen-load fallback with visible backend/load status. When real Qwen is required, unavailable Qwen fails that gate; deterministic one-candidate choice still works.
6. Retain legacy diagnostics with accurate labels: `raw_region_track_stability`, `ocr_node_set_continuity`, and raw counts. Add control identity stability, expected-control retention in fixtures, ambiguity count, exclusion reasons, promoted-field coverage, and eligible-surface stability.
7. Compute promoted-field coverage from defined schema fields and valid current values. If none exist, report not-applicable with numerator/denominator, never a fabricated pass. Keep warm-up length fixed and visible (three frames); report whole-run and post-warm-up values.
8. Persist stage, effective configuration, actual model/backend, gate booleans/reasons, capture identity/source, per-frame IDs, and metric denominators. Exit nonzero when an explicitly requested qualification gate fails. Persist operating state in the summary file itself.

Required tests: perception mode makes zero reaction calls; shadow sends zero native input; zero/one/many branches; exactly one inference per job; missing/load-failed Qwen visible; stale completion rejected; metrics match hand-calculated sequences; raw instability cannot silently disappear; required semantic unknown blocks eligibility.

Acceptance: separate stages are enforceable in code, summaries match execution, and existing shadow/armed safety checks remain intact. Integration tests must include a real worker with a fake model adapter, not just standalone helper calls.

### F — Qualification and handoff (coordinator only, after D/E)

1. Run targeted tests, then the default full suite. Review import boundaries, dirty-file preservation, and schema/action promotion paths.
2. Resolve the target from currently visible windows and pin its current HWND. Never reuse the historical HWND from this document. Allocate a genuinely new workspace without overwriting previous evidence.
3. Run 30 perception frames with semantic mode off first. Require 30/30 trusted frames, unchanged target identity/focus, no stale labels, no control-ID collisions, control identity stability >=0.95, and eligible-surface stability >=0.95. Report raw metrics alongside these new metrics; changing a metric definition alone cannot qualify the system.
4. Verify stable observed menu controls by inspecting target-only frames/overlay against logged candidates. In generated fixtures require all five expected controls and zero offered decorations. For live data, establish the visible expected set independently; do not use the system's own filtered set as its ground truth.
5. If required semantic mode is configured, repeat perception with cloud confirmation, verify two-observation evidence and request bounds, and show actual model-call telemetry. If provider configuration is absent, mark cloud qualification blocked and preserve offline results.
6. Restart against the same workspace and require no schema revision churn. Check a real screen replacement only through an already authorized reversible interaction or operator navigation; do not manufacture success by resetting caches between frames.
7. After perception passes, run shadow reaction. Cover zero/one/many branches offline; exercise whatever cardinality the live screen actually supplies. A live multi-candidate gate requires actual pinned Qwen execution, current catalog validation, and zero native input. Do not remove valid live offers to simulate a one-candidate surface.
8. Armed qualification requires an admitted reversible experiment with a known rollback and nonempty expected effect, plus the existing arming authority. Do not guess that an arbitrary menu item is reversible. Collect fresh before/after/rollback captures and release receipts. Durable action promotion still requires two verified causal trials.
9. Finish with changed-file links, tests, run directories, actual stage outcomes, remaining gates, and any provider/model limitation. A blocked live stage means partial implementation qualification, not full completion.

## Copyable worker instruction

> Execute task {ID} from docs/control-registry-repair-execution-plan.md. Read system.md and applicable repository instructions first. You own only the files listed for that task. Preserve all existing dirty changes. Implement the frozen contracts and required behavioral tests; do not change other agents' files, run live input, make cloud calls, install models, or weaken gates. If a required interface differs from the plan, report the exact mismatch to the coordinator and continue independent work. Return changed files, behavior, test commands and outcomes, unresolved limitations, and proposed cross-file integration changes. Do not mark downstream live gates complete.

## Completion criteria

Implementation is complete when A–E pass integration checks and the coordinator has reviewed the resulting behavior. Full system qualification is complete only when F's applicable live stages have evidence. Cloud confirmation, live Qwen, and armed execution each need their own explicit result; none can be inferred from unit tests or a successful process exit.
