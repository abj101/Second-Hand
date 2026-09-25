# Lean temporal perception: orchestrator handoff

Status: implementation plan, not execution approval or qualification result.
Date: 2026-09-23.
Coordinator: **GPT-6 Sol (Sol 6)**. Implementation workers: **DeepSeek 4.1 Flash**.
Target: Windows, RTX 4060 8 GB, turn-based games and mostly static interfaces first; retain an application-agnostic pixels-only architecture.

## 1. Assignment to the orchestrator

Implement the [visual-stack decision](lean-visual-stack.md), using the packages and gates below. Own architecture, shared contracts, integration, GPU measurement and final acceptance. Give Flash workers small, explicit file-owned assignments; do not ask one worker to redesign the system. Inspect their diffs and run their checks before accepting a package.

Use the actual model identifiers exposed by the destination provider. Confirm the requested Flash model is available before dispatch. If it is absent, report that fact and ask for a substitute; never silently relabel another model. This document does not require launching those workers in the current planning session.

The result is a lean, measured UI perception/identity pipeline and honest schema/action evidence. It is **not** a promise of arbitrary-game mastery or complete enemy targeting. Do not add another resident detector to make this plan appear complete.

Read in order:

1. This plan and [lean-visual-stack.md](lean-visual-stack.md).
2. [system.md](../system.md) and the [north star](perception-reaction-orchestration-north-star.md), including the distinction between provisional offers and verified actions.
3. [fusion qualification](visual-object-fusion-execution-plan.md), [repair evidence](gate-e-repair-plan.md), and [runbook](runbook.md).
4. The files and tests owned by the current package, plus every caller of a changed contract.

This plan supplies the implementation order for the lean revision. Preserve product invariants. Existing documents disagree about first-click policy; do not silently resolve that disagreement by granting input authority. Keep explicit arming, lease validation, current bindings, independent release and repeated-evidence promotion. Document any required action-policy decision for the coordinator.

## 2. Frozen scope and existing state

Already done; verify rather than rebuild:

- Live `collect --tracker` choices are `none` and `fused-gpa`. DINO, SAM2 and YOLO-World remain research/benchmark adapters. Preserve that boundary.
- GPA-only fusion uses Small OCR and visual-owned identities; detector failure must not fall back to OCR-owned actionable targets.
- The fused path uses one full-frame OCR read, avoiding the old 25–28 crop-call regression.
- Native capture provenance, ledger, schema projection, leases, policy worker and release owner already exist.

Current evidence: three annotated combat frames, 5/5, 3/5, 1/3 matched cards; splitter counts 5, 3, 0; one hover false offer. Two warm GPA-only observations were 731/626 ms. These are historical diagnostics, not production p95. DINO actor centers can be useful despite zero full-box matches; GPA-only does not preserve that world coverage. The normal Qwen environment was missing `ultralytics` at review time. Recheck all these facts at P0.

The prior full test run had 392 passes, two failures and nine deselections. Known failures: shutdown release expectation in `test_native_faults.py`, and a two-frame CLI test ending with pending Qwen in `test_native_shadow.py`. Reproduce once; record rather than hide them. Fix stale assertions with deterministic fixtures only after confirming actual intended behavior. Never remove release-on-close or weaken no-input assertions to make tests green.

Out of scope: DOM/accessibility/game-engine input, application-specific ontology or fixed screen coordinates, new foundation-model downloads, cloud screenshot transmission by default, learned ReID, SAM2/optical-flow actuation, TensorRT conversion, gamepad/real-time play, model fine-tuning, unconditional default promotion. Do not delete existing experiment weights or user work.

## 3. Coordination and ownership

The tree is heavily dirty, including untracked implementation files. Snapshot `git status --short` and preserve all existing edits. No reset, clean, mass formatting, commit, push or recursive deletion. Use the current checkout unless an isolated copy demonstrably includes its uncommitted state; a clean HEAD worktree is not an equivalent baseline.

Only one writer owns a file at a time. At most three workers plus Sol; only Sol runs GPU workloads or operates a live target. Parallel workers run mocked/CPU checks only. Shared contract changes require Sol's written acceptance before dependent edits. Do not permit a worker to edit another package's file to get its test passing.

Execution order:

```text
P0: freeze contracts, fixtures, baseline and ownership
  -> A environment | B time/provenance | C evaluator       (disjoint parallel work)
  -> Q0: corrected same-process baseline, saved unchanged
  -> D identity/OCR ownership | E card proposals | F semantics worker
  -> G: Sol integrates, adds exact-frame reuse and scene/schema wiring
  -> H: action binding, outcome receipts and supervised-test instrumentation
  -> Q1: held-out offline replay + live no-input diagnostics
  -> Q2: separately authorized bounded supervised actions
```

Unavailable local captures or hardware may block Q0/Q1 measurement, but do not prevent isolated contract/unit work. Mark blocked measurements explicitly. Never replace real-scene evidence with synthetic frames in qualification. If Q0 is blocked, freeze the code and record that before/after performance claims remain unavailable.

## 4. Contracts Sol must freeze at P0

Prefer adding necessary fields to existing records over introducing parallel frameworks. Write final field names and callers into the execution log before dispatch.

**Observation identity/time.** Retain the image digest as content identity. Add or reuse a monotonic acquisition timestamp and observation sequence scoped to target/episode generation. Identical pixels from two captures may share a digest but are distinct observations. Record capture time, perception completion, publication, decision submission/completion and dispatch/lease time separately. Do not use a pixel hash as screen-generation identity. Never substitute `now - 0.001` or the post-OCR time for acquisition.

**Acquisition semantics.** Prefer a correctly converted backend frame timestamp. If unavailable, timestamp immediately before a fresh synchronous grab as a conservative acquisition bound and record its origin. Audit WGC buffering: a fresh wrapper-call timestamp cannot establish age of a previously buffered frame. Unknown/stale backend provenance must not qualify for actuation. Repeated-content cache hits still need a fresh trusted capture.

**Track evidence.** Existing `TrackHit` remains the transport unless a specific missing field requires extension. Separate object ID, current box, source/role, visibility, last observation and appearance descriptor from OCR label and semantic hypothesis. Use bounded return memory. Episode/target resets must prevent ID reuse from colliding with forbidden IDs or old leases. Absence immediately removes offer eligibility; uncertain reappearance does not inherit a verified action.

**OCR ownership.** Each token has zero or one owner. Use token containment/center plus geometric ambiguity checks, not only token/object IoU. Preserve unmatched text as evidence. Never assign a boundary-spanning token to multiple cards or combine two same-title cards into one object.

**Async semantics.** A job/result carries target identity, episode/screen generation, observation ID, evidence digest, schema revision and brief digest. Background work receives immutable copies and returns hypotheses. The main session path alone validates/applies results; workers cannot mutate scene graphs, ledger, schemas, offers or input. Bounded latest-value queue, one in-flight semantic request, finite request timeout. It is separate from the Qwen reaction slot and release clock.

Freshness is evidence-based, not strict equality with the latest observation number. A later trusted capture may validate a result's unchanged target/object bindings and relevant evidence under the same generation, schema and brief; record both origin and validation observation IDs. Reject it when relevant evidence or authority changed. Do not starve slow semantics by discarding every answer whenever capture advances. Applying an old answer again is not an independent confirming observation. If existing semantic agreement requires a second model observation, schedule that bounded confirmation explicitly on a distinct fresh observation; duplicate suppression must not make confirmation impossible.

**Diagnostic counts.** Distinguish raw proposals, malformed/oversized rejects, merged objects, current tracks, offer candidates, withheld candidates and eligible offers. Missing diagnostics are unavailable, not zero. Low detector scores are not calibrated probabilities across models. Do not use Qwen's emitted one-hot scores as calibrated confidence.

**Outcomes.** Retain execution acceptance separately from effect verification. Record `verified`, `no_effect`, `wrong_effect`, `unobservable` or `not_executed`, with before/after observation references, provenance and evidence IDs. If the schema cannot specify an observable expected effect, do not invent success from a click API return or any pixel change.

Freeze a bounded post-action observation/settling policy per admitted experiment before execution: expected predicate, positive/negative evidence needed, maximum observation window and capture cadence. A valid effect may appear after animation. Verify on sufficient fresh evidence; record `no_effect` only after adequate trusted observation over the declared window, and `unobservable` when evidence is missing/ambiguous. Record `wrong_effect` only with positive contradictory evidence. Release on schedule and withhold further dependent actions while the outcome is unresolved; do not block release waiting for settlement. Never issue another action merely to discover whether the first one worked.

## 5. Packages for Flash workers

The package ownership lists below are **ownership envelopes, not single worker prompts**. Sol must issue the following sequential micro-jobs within each envelope, reviewing and testing each before the next. These splits are mandatory for Flash; at most a few production files plus focused tests per job, with an accepted interface rather than an open architectural question:

- A1 pinned environment/setup; A2 smoke/load diagnostics; Sol performs actual installation and GPU acceptance.
- B1 capture provenance and time contract; B2 CLI-to-shadow propagation; B3 armed/experiment propagation and dispatch-age checks; B4 Sol reconciles telemetry and all callers. Transfer shared files explicitly between these jobs.
- C1 metric/count/matching repairs; C2 Sol independently freezes datasets; C3 configurable same-process replay/timing harness; Sol validates baseline reports.
- D1 appearance/matching/identity lifecycle; D2 OCR-to-object ownership; Sol accepts sequence results.
- E1 source diagnostic counters; E2 card failure diagnosis and smallest proposal fix. Do not couple a speculative splitter rewrite to counter plumbing.
- F1 bounded worker lifecycle/result contract; F2 semantic invocation/coalescing; Sol performs F3 session-owner integration in G.
- G1 exact-frame cache; G2 semantic/evidence/schema integration; G3 admission/telemetry and reference parity. These run serially under Sol's ownership.
- H1 lease/current-binding route repair; H2 bounded post-action observation and outcome receipt; H3 Sol integrates negative evidence/promotion and resolves the two known test-fixture failures.

If one micro-job still requires a cross-cutting contract change, return it to Sol and split again. Do not infer permission to edit all envelope files at once.

### A — Reproducible lean runtime environment

Own: new `requirements-perception-windows.txt`, `scripts/setup_perception.ps1`, `scripts/check_perception.py`, focused script tests. Read existing Decider setup, `qwen_policy.py`, `gui_yolo.py` and `pyproject.toml`; do not edit them concurrently.

Build a separate Python 3.12 environment (for example `.venv-perception`) using the proven CUDA stack, current Qwen revision, cached GPA weights and explicit RapidOCR Small/version/provider. Resolve and record exact package versions and model hashes from the real environment; do not invent pins or substitute latest releases. Include the existing tracking dependencies needed for GPA. Keep legacy setup usable. Do not blindly remove FLA/Triton if Transformers' actual Qwen path requires or benefits from them; validate the smaller dependency set empirically.

The smoke tool must load the **normal Qwen policy loader**, GPA and callable OCR in one process, execute a multi-offer Qwen decision, actual GPA inference/NMS and OCR on a supplied local frame, and report actual devices/providers, elapsed load/inference, allocated/reserved peak GPU memory and whole-device memory. Disable automatic downloads/cloud calls; absent weights/dependencies are explicit blockers. CPU fallback must be reported, not labeled CUDA success. Setup performs dependency consistency checking. Only Sol runs this smoke on GPU.

Accept A when the script's failure cases pass mocked tests and Sol records a real same-process result. A mock pass alone is `CODE_READY`, not environment qualification. Package installation is allowed for this bounded environment; new model downloads require a separate decision.

### B — True capture age through every native route

Own: `loop/os/window.py`, `loop/adaptive/contracts.py`, `cli.py`, `controller.py`, `arm.py`, `executor.py`, native observation callers in `experiment.py`, timing fields in `telemetry.py`, and affected capture/native tests. Exclusive ownership until B is accepted. Paths without a prefix in package ownership lists are relative to `loop/adaptive/`; test names are under `tests/loop/` unless stated otherwise.

Trace all production callers, including `_live_observe_factory`, `NativeShadowLoop.process_frame`, armed `_compile_frame`, experiments and after-captures. Both controller and arm currently synthesize acquisition time. Carry the frozen real timestamp and observation sequence through all of them. At dispatch, recompute age from acquisition; log capture-to-publication, queue/inference, capture-to-lease and capture-to-effect separately. Missing timing must fail closed for real input; synthetic fixtures supply explicit clocks. Never increase allowed age to conceal a slow path.

Accept B with controlled-clock tests: a 1.2-second perception delay appears in age/e2e and exceeds a 0.5-second configured limit; delayed Qwen cannot rejuvenate that frame; buffered/unknown-age capture cannot be armed; identical new captures do not reuse old observation timestamps; shadow sends no input; armed compilation receives the same original acquisition time. Preserve focus, release, reset, lease and transition tests. Run affected callers, not just one regression.

### C — Honest evaluator and frozen dataset protocol

Own: `benchmarks/visual_object_fusion.py`, `tests/loop/test_visual_object_fusion_eval.py`, and ignored versioned report/annotation manifests. No production edits. Coordinate diagnostic field names with Sol; fields not yet emitted stay unavailable until G.

Fix `oversized_offers` counting rejected boxes as actual offers. Report source rejection counts separately. Use deterministic maximum one-to-one matching or clearly document and test any remaining assignment approximation; annotations must not change to fit detections. Validate frame hashes/sizes and handle unavailable model/inference per frame. Never allow missing required rows, roles or diagnostics to silently pass a gate.

For temporal metrics use reviewed physical IDs on ordered sequences, with sequence boundaries. Count expected visible opportunities, missed observations, correct associations, switches, collisions, fragmentation and correct/incorrect returns separately. Do not compute retention solely over successful detection pairs. Measure click-region localization separately from box IoU. Partial annotations cannot yield population precision. Annotate required fields and intended effect evidence as well as boxes.

Benchmark fresh cold processes separately from warmed identical workloads; do not give the first arm all initialization cost. Warm-up is fixed before measurement. Add a configurable, offline-only same-process harness using A's normal policy route and the actual callable OCR/assembler path. No precomputed OCR substitution. Include Qwen activity on multi-offer scenarios, not only idle residency. Never overwrite historical E results: write a new run directory with revision/diff identity, versions, hashes, config and commands.

Accept C with fixture tests for rejected-oversized vs offered-oversized counts; unavailable/empty/missing sequences; an ID switch; disappear/return; order-ambiguous box assignment; digest mismatch; and cold/warm accounting. Sol independently reviews labels before Q0.

### Q0 — Coordinator baseline before perception changes

Run A+B+C together on the existing three-frame set, retaining the exact same annotations and an untouched report. Record both old frozen E and new diagnostic metrics without rewriting either criterion. Prepare development and held-out sequences before D/E tuning; split by sequence/session, not alternating near-identical frames. Do not expose held-out failure details to workers during tuning. Additional collection is no-input, target-only, with operator navigation; never script clicks merely to populate the dataset.

### D — One identity owner and correct text attachment

Own: `loop/adaptive/tracker.py`, `proposal_fusion.py`, `tests/loop/test_spatial_tracker.py`, `test_proposal_fusion.py`. Read card proposals and scene application code; request integration changes from Sol.

Replace exact-hash-only return matching with a compact normalized appearance descriptor and tolerant distance. Gate on role, spatial/scale plausibility and elapsed observation time. Use appearance as evidence for continuing objects too. Hashes can still key exact caches; never treat a SHA digest as a distance feature. Preserve existing benchmark adapters or explicitly adapt their descriptor contract with Sol.

Keep deterministic one-to-one association. Start with scored, ambiguity-aware matching; if reviewed adjacent/reordering cases still swap IDs, use an available standard global assignment implementation. Request a dependency decision rather than writing a bespoke Hungarian solver. Do not add motion/ReID models. Reject ambiguous return identities, do not pick the first equal-looking item. Keep labels mutable and memory bounded. Ensure resets cannot reissue an old actionable ID within the same authority scope.

Use unique text containment/center ownership with overlap ambiguity rejection. Keep state values separately observable. Report same-name instance identity honestly; truly indistinguishable full occlusions cannot be solved by asserting an ID.

Accept D with synthetic sequences covering minor pixel noise, hover scale changes, label/value edits, same-name adjacent cards, reorder/crossing ambiguity, disappearance/return, changed object occupying the old box, and episode/screen reset. Require no duplicate identity, no old offer after absence and no arbitrary identity transfer on ambiguous evidence. Sol then measures development-sequence deltas against Q0.

### E — Conservative card proposals and source diagnostics

Own: `loop/adaptive/card_proposals.py`, `gui_yolo.py`, `fused_perception.py`, `tests/loop/test_card_proposals.py`, `test_gui_yolo.py`, `test_fused_perception.py`.

Diagnose original ordinary/hover/reduced-hand failures before editing. Separate upstream missing/coarse proposal, missing OCR title, split geometry, duplicate suppression and semantic withholding. Repair the smallest generic cause. Avoid hardcoded five-card counts, title lists, STS2 coordinates, unconditional lower-screen regions as cards, or the previously rejected hand expansion that introduced false offers. Make assumptions explicit and reject decorative lower-screen panels. Do not claim every OCR column is a physical card boundary.

Preserve distinct visible card support and a conservative interior target region; verify overlapping/hovered neighbors are not hit by that point. Ambiguous geometry stays withheld. Emit frozen raw/accepted/rejected/merged source counters and actual backend/fallback status. Clear source outputs on any failed inference; no stale box resurrection.

Accept E with fanned/overlapping/same-title cards, hover, changing count, partial occlusion, different image sizes, decorative text columns and failed-detector fixtures. Replay original development frames: no regression on 5/5 ordinary cards, eliminate the hover false offer without hiding it by dropping all offers, improve incomplete cases where evidence permits. Missing cases remain measured blockers, never relabeled success.

### F — Move semantic work off observation and release

Own: `loop/adaptive/orchestrator.py`, `semantic_confirmation.py`, `loop/model/gateway.py`, semantic/orchestrator tests; a small new worker module only if existing runner machinery cannot cleanly host the work. Do not edit CLI/controller/perceive until Sol integration.

The optional VLM currently runs synchronously from perception, and optional semantic confirmation can also block the live loop. Move both expensive calls behind the frozen bounded semantic job/result contract. Reuse the existing runner/lifecycle pattern where possible; do not put semantic work on Qwen's reaction queue or block directive publication/release behind network calls. Trigger on new unresolved objects, screen-generation change or a new failed-effect event, coalescing repeats. Unchanged frames must not repeatedly enqueue the same request.

Use the configured existing semantic provider only; no new resident vision model. Default tests and offline replay use a fake or disabled provider, with no screenshot transmission. Apply valid results only on the session owner path as evidence-backed proposals. Apply P0's origin-versus-fresh-validation rule; a genuinely stale result is discarded, while an unchanged object can accept a validated result from an earlier observation. It cannot mutate a cached graph or directly authorize an offer. Required-semantic mode keeps unresolved controls withheld while pending. Shutdown and timeouts cannot freeze capture/release or create an unbounded backlog.

Accept F with a deliberately blocked fake provider: observations/directives/release still progress; at most one inference and one latest pending job; changed-evidence/generation/brief results rejected; later unchanged fresh evidence accepts a valid delayed result; the required second confirmation completes without replaying the first answer as new proof; no duplicate request storm; invalid replies produce no schema mutation; known no-input behavior retained. Include focused lifecycle tests rather than real networking.

### G — Sol integrates perception, caching and schema freshness

Own after B/D/E/F acceptance: `perceive.py`, `cli.py`, `orchestrate.py`, `telemetry.py`, relevant `schema.py`/`snapshot.py`/`runtime.py`/`session.py` changes only if needed, plus integration tests. Sol may delegate one bounded edit at a time under exclusive ownership.

Wire D/E diagnostics and F submission/polling. Fix live admission so empty stable sets cannot qualify useful perception. Use explicit required-target coverage from evaluation; do not make all scenes require a nonempty offer set, because transitions legitimately have none. Per-frame safety and population qualification are different checks.

Implement **exact-frame** reuse first: fresh trusted capture, same target/generation/dimensions and pixel bytes may reuse expensive OCR/detector outputs. Still create a fresh observation and reproject through the current schema/directive/forbidden/semantic state. Do not replay old evidence IDs as independent repeated proof. Retain the origin of measurements and the fresh verification time separately. A schema update must reproject unchanged pixels; a newly forbidden control must disappear immediately despite a cache hit.

Changed frames run one full-frame OCR plus GPA baseline. Per-region optimization is conditional: only pursue it after exact-frame replay quantifies a material miss in the desired latency budget. Require output parity for essential labels/targets and no new false offers; coalesce reads instead of per-card detector calls. Defer it explicitly if the exact baseline suffices. Animation-only changes may defeat full-frame caching; report that limitation rather than claiming event-driven performance.

Verify stale required fields become unknown/unavailable after change, disappearance or expiry; remembered values cannot satisfy current-state requirements without fresh validation. Screen generation must reflect actual replacement/reset, not every changed hash. Preserve schemas across legitimate same-target restarts without treating persisted acquisition times as current process monotonic times.

Accept G with end-to-end fixtures for unchanged frames (no repeat OCR/GPA), one changed title/value (fresh read), new forbid/schema revision on identical pixels, black transition, focus/target replacement, stale semantic reply, duplicate titles and vanished controls. Scene/evidence/schema outcomes must match an uncached reference aside from explicit cache/verification metadata. No artificial repeated causal proof from cache reuse.

### H — Fresh action bindings and truthful outcomes

Own after G: `controller.py`, `arm.py`, `executor.py`, `session.py`, `runtime.py`, `telemetry.py`, `contracts.py` (explicitly transferred from B for outcome/receipt extensions), and related dispatch/input/receipt tests. Coordinator-controlled; one worker at a time.

Inspect all input routes, including direct experiments: current code has a controller branch calling the input owner directly and using the same graph for leased/current context. Ensure executable product actions use the authorized session/lease boundary with a frozen leased scene and a genuinely newer capture used for revalidation. Do not merely pass the leased graph twice. Revalidation may confirm the target or reject it; it cannot silently re-aim a frozen lease. Keep release independent throughout capture/perception/model delays. If direct mode cannot preserve these guarantees, disable that route explicitly pending repair; do not leave it as an unchecked alternative.

Record the frozen expected effect and the bounded sequence of fresh after-captures defined at P0. The current immediate single-after-frame check must not classify a delayed animation as failure before the observation window ends. Link receipt to choice, target, schema/catalog/lease, primitive status, release and the outcome contract. Keep shadow `not_executed`/unknown distinct from real no-effect. Do not label failed perception as a verified wrong effect. Apply negative evidence through existing rules with correct identity scope; never promote from cached repetitions or duplicate receipts.

Accept H with fake-clock/input-owner tests: expired frame, moved/replaced/disappeared target, focus loss and delayed decision cannot act; legal current action acts once; a release failure stops further input and recovery-locks the session (it cannot undo input already sent). Verified effect/no-op/wrong-effect/unobservable result are distinguishable; an effect arriving after several trusted frames verifies within the window; insufficient evidence at deadline is unobservable; after-captures belong to the same target and are later than execution. Real actuation remains Q2, separately armed by the operator.

## 6. Qualification and stop conditions

**Do not silently relax historical Gate E.** Its full-world requirements remain failing when actor coverage is absent. Publish its unchanged verdict alongside a separately named **Lean UI diagnostic gate**. Passing the latter allows a scoped supervised UI trial, not full combat qualification or a default switch. World-target-dependent scenarios remain unsupported until independently localized and validated.

P0 freezes these proposed engineering targets before tuning; they are task requirements, not published model guarantees:

- At least six scenario categories: menu, ordinary combat UI, hover/hand change, reward, map, and transition/replacement. At least 30 consecutive trusted observations for each applicable sequence and at least 100 warm changed-scene measurements overall. Include multiple independent sequences/sessions, with development/held-out separation. Report unavailable categories rather than filling them with copies.
- On the reviewed held-out UI set: at least 95% required actionable-object coverage overall, no persistently missing essential control in any declared-supported scenario, zero false/decorative/stale/duplicate eligible targets, and at least 95% correct unambiguous identity associations over **all expected visible adjacent pairs**. Report abstentions and ambiguous cases separately; do not remove missed detections from the denominator. Averages cannot hide an unusable hover scenario.
- Required field/value correctness and freshness must pass each declared scenario's independent expected values; unknown is a miss, not an incorrect known value. No false schema/action promotion in deterministic replay. Report per-field counts, not a single mean over mostly irrelevant fields.
- Warm changed-frame perception p95 <=1,000 ms on this machine with normal Qwen resident and active where there are multiple offers; >=100 timed samples, fixed warm-up, sample count and maxima alongside percentile. Separate capture-to-lease latency must meet the chosen per-scenario age/deadline with real fresh-binding checks; freeze that deadline at P0 and do not widen it to rescue results. Cache-hit latency is separate and must measurably beat recomputation without changing essential outputs.
- No OOM, no sustained capture/release starvation, approximately 10% whole-device VRAM headroom while the target game and chosen models run. Record CPU usage, backend fallback and actual peak memory; PyTorch allocated bytes alone are insufficient.

Zero observed false offers/actions is a result on that finite test set, not a guarantee of zero population error. Publish sample counts, misses and remaining uncertainty alongside every pass.

Q1 consists of offline held-out replay and live **no-input diagnostic acquisition**. This is distinct from historical formal F, which remains gated by E. Sol selects the exact window, records backend/model names and checks saved target-only frames. If operator navigation is needed, pause that scenario while continuing other work. No game screenshots leave the machine in these tests. A passing CPU/mock suite cannot satisfy Q1.

Q2 requires explicit operator arming for a bounded named target/scenario. Use the existing supported action primitives, a small frozen action budget and an immediate stop on wrong target, stale dispatch, focus/identity loss or release failure. Record every attempt and outcome, including abstention. Do not use `--direct-experiment` to bypass H or qualification. A scene change alone is not the expected effect; visual evidence must establish the intended state change. If world targeting is required but unresolved, withhold that action and record the task as unsupported.

Failure handling: classify code defect, data absence, environment/backend failure, or accuracy/latency miss. Fix the smallest identified cause and rerun affected checks. After two unsuccessful attempts at the same approach, return the evidence to Sol for a decision; do not add a model, broaden scope or relax annotations. Do not repeatedly tune on held-out results; a changed approach requires fresh held-out evaluation.

## 7. Worker dispatch and return templates

Sol sends each worker this complete packet, replacing every bracketed value:

```text
You are DeepSeek 4.1 Flash implementing package [ID] from
docs/lean-perception-implementation-plan.md.
Goal: [one concrete behavior change].
Read: [specific files and accepted contract excerpt].
Owned files: [exact paths]; no other files may be edited.
Existing dirty changes to preserve: [summary].
Accepted upstream artifacts: [paths/revisions or none].
Implement: [bounded ordered steps].
Do not: [package-specific exclusions, no downloads/input/network].
Acceptance: [exact assertions and test command].
Stop and report: [missing contract/dependency/fixture conditions].
Return the diff summary, tests actually run and results, measured evidence,
unresolved failures and any proposed contract changes. Mock data must be labeled.
Do not commit, push, rewrite annotations, change thresholds or edit other owners' files.
```

Worker return format:

```text
Package / status: CODE_READY | VERIFIED | BLOCKED | FAILED
Changed paths and behavior:
Commands actually executed / exact results:
Model/backend actually loaded (or mocked/not run):
Before/after evidence and denominators:
Contract deviations / unresolved risks:
Requested coordinator decision:
```

Sol accepts a packet only after checking file ownership, reading the diff, executing targeted checks and confirming no threshold/authority weakening. Run the full ordinary suite after integration; fix/reclassify known test-fixture failures explicitly, do not present a failing suite as green. GPU/native/soak tests are separate and never enabled indiscriminately.

## 8. Deliverables and completion report

Maintain a short coordinator log under ignored `.tmp/lean-perception/<run-id>/` with package statuses, accepted contracts, immutable baseline/results and execution commands. Keep publishable decisions/run instructions under `docs/`; keep captured game art, credentials, environment files and weights out of Git.

Required final report: changed files, actual environment/model hashes, test counts, before/after metrics with denominators, examples of corrected and remaining failures, historical E/F status, Lean UI status, and explicitly supported/unsupported interactions. Link raw local evidence. Update runbook to the validated interpreter and actual CLI; do not advertise the older Decider smoke as proof of Qwen+GPA readiness.

Completion has three independent statuses: **code integrated**, **Lean UI measured**, **supervised actions verified**. Missing data/operator authorization may leave later statuses blocked without invalidating completed code work. No status can be inferred from another. Keep `none` as default and `fused-gpa` opt-in throughout this plan; a product-default change requires its own explicit scope/qualification decision.

## 9. Copyable starting prompt for Sol 6

> Implement `docs/lean-perception-implementation-plan.md` in the current dirty checkout. You are the Sol 6 orchestrator; use DeepSeek 4.1 Flash workers for the bounded file-owned packages, verifying that model is actually available before dispatch. Start at P0, preserve all existing changes, freeze the shared contracts, and follow the dependency waves. Keep GPA+Small OCR+one identity tracker as the lean experimental visual path. Do not reintroduce resident DINO/SAM2/YOLO-World, download new models, relax frozen evidence gates, or send native input without explicit operator arming. Keep temporal identity, required-field freshness, real capture age and verified action effects central. Report code, measurement and supervised-action completion separately, with exact tests and evidence links. If a fixture/provider/model is unavailable, continue independent work and name the blocked gate rather than fabricating a pass.

## 10. Independent audit and re-audit

Completed 2026-09-23 by a separate read-only audit subagent, with code inspection of acquisition, controller/armed timing, track association, semantic confirmation, session receipts and evaluation. The reviewer found the architecture and interpretation of existing evidence sound, with three required clarifications and three smaller corrections. All were incorporated:

1. Bounded post-action observation distinguishes delayed effects, no-effect and unobservable evidence.
2. Earlier semantic answers can apply when later trusted evidence revalidates unchanged content; advancing observation IDs alone does not invalidate them.
3. Mandatory micro-jobs prevent oversized cross-cutting Flash assignments.
4. Release failure blocks subsequent input and records the actual prior attempt rather than pretending to undo it.
5. H explicitly receives ownership of receipt/outcome contracts from B.
6. Zero observed errors is labeled as a finite-test result.

Re-audit verdict: **all six findings addressed; no remaining must-fix issues identified; approved for orchestrator handoff**. This is approval of the plan's consistency and approach, not evidence that its implementation, hardware metrics or autonomous-play gates have passed. No runtime implementation or native input was performed as part of this planning/audit task.
