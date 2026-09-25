# Native Decider Orchestration Plan

Status: stack-proof note. Vision is `perception-reaction-orchestration-north-star.md`. Execution sequence is `north-star-build-plan.md`.

Date: 2026-09-21

Product path: native window capture, in-process local Decider, verified Win32 input

## Authority and precedence

`system.md` and ADR 0001 remain the product invariants. The north star is the control-system vision. `north-star-build-plan.md` is the only execution sequence. This file keeps the Windows stack proof, pinned revisions, and the audit those pins came from. It does not override the north star flow.

PlayJev and Playwright remain optional benchmark fixtures. They are not dependencies, acceptance gates, input providers, action authorities, or substitutes for the native product path.

The north-star test for every component is:

> Does it help turn the newest trustworthy observation into a valid action before that observation becomes stale, or help the orchestrator provide persistent direction without blocking that action?

If neither is true, it is outside this milestone.

## Outcome

Close one real, fail-closed loop using the adaptive runtime:

```text
target pixels
  -> WindowCapture + capture provenance
  -> SceneAssembler + evidence ledger
  -> proposal / repeated causal validation / immutable schema promotion
  -> Session.observe() / verified ActionSurface
  -> compact reactive state + described opaque candidate IDs
  -> local Decider Choice probabilities
  -> decision and dispatch gates
  -> Session.act()
  -> native Win32 input owner
  -> fresh trusted target pixels
  -> expected-effect verification + execution receipt + telemetry
```

Three independently timed loops share only versioned artifacts:

1. Perception publishes the newest qualified observation.
2. Reaction scores the latest observation against the current verified action surface and dispatches before its deadline.
3. Orchestration publishes a persistent directive asynchronously; it never emits primitives or blocks reaction.

A fresh workspace does **not** begin in reaction mode. It begins with `empty_schema()`, remains in `Observe`, and must pass the existing reversible experiment and promotion path before it can expose any candidate to Decider or enter `ArmedReact`.

## Non-negotiable boundaries

1. `Session.act()` is the only product actuation boundary. Benchmark `step(action_id)` cannot reach it.
2. Pixels and executed input are the only environmental boundary. Privileged fixture state never enters native observations, directives, policy requests, or action selection.
3. Projection from the current immutable schema is the only authority for state and the legal action surface.
4. Decider receives compact state, directive intent, candidate IDs, and candidate descriptions. It never receives or emits coordinates, handles, scan codes, key names, scripts, bindings, or primitive plans.
5. A directive may narrow or rank the current `ActionSurface`; it cannot add an action. Hard constraints are compiled before inference and are rechecked at dispatch.
6. Native input is allowed only for the exact pinned `hwnd`/`pid` instance while it is foreground and capture integrity, target identity, snapshot, schema, catalog, directive, policy, and decision are current.
7. Every input-down operation has an owner and a monotonic release deadline. Focus loss, rejection, timeout, reset, terminal state, exception, or orderly shutdown releases all held input. On startup, the owner also sends safe up events for the configured armed allowlist before actuation is enabled.
8. Shadow mode performs capture, perception, candidate compilation, inference, validation, and hypothetical dispatch, but never calls `Session.act()` and emits no native input.
9. A fresh workspace starts empty. Models may propose hypotheses, but they cannot promote schema revisions or make an unverified action legal.
10. Zero legal candidates means release and observe without inference. One legal candidate bypasses Decider but still passes every dispatch and `Session.act()` gate. Decider is invoked only for 2–255 candidates.
11. Failed or absent expected effects produce negative evidence and recovery behavior, never a positive label and never an automatic retry.
12. Learning, teachers, behavior cloning, DAgger, RL, gamepad support, and a second fixture remain deferred.

## Audit findings incorporated

| Gap in the prior plan | Correction in this plan |
|---|---|
| It jumped from an empty workspace to a reactive catalog. | Action discovery and repeated causal promotion are now an explicit prerequisite wave. |
| It required a fresh workspace for every armed stage, which would erase the promoted action. | Qualification starts with one fresh target-bound workspace and preserves it from observation through promotion and arming. |
| It treated bare `key_down` / `key_up` as first-slice actions despite the bounded-release invariant. | The first slice exposes only bounded `key_tap` and `key_hold`; raw down/up remain internal to the input owner. |
| It armed mouse move and keyboard stages even though the current generic experiment controller can only promote verified reversible clicks. | Product arming uses promoted actions; keyboard and mouse mechanics are qualified separately in an instrumented window until a matching safe probe is designed. |
| It said to reuse the benchmark dispatcher even though that dispatcher does not check snapshot ID, schema/policy revision, target identity, the current mask, or current surface membership. | The shared types may be reused, but the native dispatch gate must add and test all missing checks. |
| It required dispatch-time checks inside `Session.act()` without defining how current capture, focus, target, and revisions reach that boundary. | `Session.act()` receives a short-lived native actuation lease and revalidates it immediately before the first primitive and before every input-down event. |
| It mistook the global Python 3.13 CPU-only Torch install for a Windows limitation. | The repository's existing Python 3.13 environment already proves native CUDA works. The supported Decider profile is an isolated native Windows Python 3.12 environment with CUDA PyTorch and `triton-windows`; WSL2 and HTTP are unnecessary. |
| It treated Python 3.12 as a model requirement. | Decider declares Python 3.11 or newer. Python 3.12 is selected because Decider 0.9.0 currently pins `numpy<2`, and NumPy 1.26 has Windows wheels for 3.12 but not 3.13. This is a packaging constraint, not a weights or inference constraint. |
| A synchronous `Policy.score()` call could block the reactive loop and cannot enforce an inference deadline. | A single-in-flight policy worker uses a bounded latest-value channel; timed-out or late responses are discarded and never block capture or input release. |
| The model gate could claim success in a one-candidate state where Decider is never called. | Model qualification requires frozen scenarios with at least two verified candidates. One candidate can prove native actuation, not model quality. |
| Bit-for-bit deterministic GPU probabilities were required. | Replay gates require stable choices and probabilities within a declared numeric tolerance under pinned artifacts. |

## Current state and exact gaps

Reusable now:

- `WindowCapture`, exact window targeting, foreground checks, capture provenance, and normalized geometry.
- Neutral perception, the evidence ledger, immutable schema revisions, projection, action promotion, and expected-effect verification.
- `Session.observe()` and a fail-closed `Session.act()` boundary.
- Typed `KeyPrimitive`, `MousePrimitive`, Win32 `SendInput` structures, and a stateful mouse implementation.
- Candidate, mask, directive, decision, telemetry, and benchmark controller types.
- Deterministic and random policies for controls.
- Native CUDA PyTorch already works on the RTX 4060 when the repository interpreter is used instead of global `python`.

Missing or incomplete:

- The live primitive runner ignores every schema primitive except `click` and creates a new `Mouse` per call.
- There is no keyboard executor or unified owner for held key/button state and deadline-driven release.
- `PrimitiveStep` has no validated literal parameter payload.
- `Session.act()` does not currently recheck the active session schema, exact live target, foreground state, capture age/integrity, or a decision lease immediately before input.
- The live product snapshot has no trusted timestamp/quality/target/episode envelope suitable for freshness gates.
- The existing `Executor` is benchmark-only and omits several native dispatch checks.
- Candidate descriptions are not compiled from verified effects and constraints.
- There is no native adapter from verified `StateSnapshot`/`ActionSurface` to a reactive request.
- There is a validated Decider request/response adapter, native setup script, and pinned environment manifest; wiring it into the native latest-value policy worker remains incomplete.
- There is no native shadow/react loop joining perception, policy, dispatch, `Session.act()`, verification, and telemetry.
- The live experiment path promotes reversible clicks only. Keyboard action discovery is not part of this milestone.
- Native fault injection and sustained local qualification have not run.

## Contract decisions

### Runtime and model deployment

Keep capture, projection, gating, Decider inference, and Win32 input native on Windows. Decider loads once inside the dedicated policy worker; the capture loop and release supervisor never wait on that worker. There is no WSL2, loopback HTTP server, or duplicate wire protocol in the first slice.

The native model profile is:

- a dedicated Python 3.12 virtual environment created by `scripts/setup_native_decider.ps1`;
- CUDA PyTorch 2.14.0+cu130 and `triton-windows` 3.8.0.post28, matching the upstream compatibility table;
- Flash Linear Attention 0.5.2 and Decider 0.9.0 installed from immutable source commit `c4daaac28af9fea95d627015cffa2dd5a5926ee6`;
- NumPy 1.26.4, satisfying Decider's current `numpy<2` metadata without a source build;
- 0.8B weights materialized from Hugging Face revision `1ea54127d3bd52f6d753d9257b32a6380b873907`; the optional 2B comparison uses revision `b37f7e1ba3fbc9238004cf531fabbee2619973fd`;
- a checked-in exact environment manifest in `requirements-decider-windows.txt`;
- BF16, `independent=True`, and `layout="state_first"`; start with `use_graphs=False` for correctness qualification, then enable the upstream graph engine only after choice/probability parity and latency tests pass; and
- a startup preflight that verifies the interpreter, exact installed distributions, CUDA device, a Triton GPU kernel, model revision, `decider_config.json`, and one frozen `system_one` response before Decider may be armed.

Compiled graph shapes must be warmed before `ArmedReact`; first-shape compilation is startup work and may not consume a reaction deadline. The eager and graph engines must return the same winning choice and probabilities within the declared tolerance on the frozen qualification set before graph mode becomes the default.

The weights do not require a special Python version. Python 3.12 is the narrowest upstream-faithful solution to the package graph. Do not remove Decider's `numpy<2` cap locally or install the unrelated PyPI package named `decider` (version 0.0.1). If preflight fails, the runtime remains deterministic-only and unarmed for Decider; it never silently changes model, dtype, layout, backend, or revision.

### Native stack proof completed on this host

The audit verified these facts rather than inferring them from package names:

- global `python` is Python 3.13 with CPU-only Torch, but the repository `.venv` already runs native CUDA Torch on the RTX 4060; the original CPU-only conclusion was an interpreter-selection error;
- a clean native Windows Python 3.12.13 environment resolves the exact checked-in profile without building NumPy from source;
- CUDA Torch 2.14.0+cu130, `triton-windows` 3.8.0.post28, Flash Linear Attention 0.5.2, and Decider 0.9.0 import together and execute a correct Triton GPU kernel;
- the pinned 0.8B snapshot answers the frozen two-choice `system_one` request in eager BF16 mode and in the upstream compiled CUDA-graph engine with the same choice and close probabilities;
- measured peak allocated VRAM for that smoke request was about 1.5 GiB eager and 1.7 GiB with graphs; and
- the model-free suite passes in both the ordinary environment and the native Decider environment.

This proves stack compatibility and request correctness. It does not prove the native policy-worker deadline, long-run latency, 2B fit, or safe actuation path; those remain explicit later gates.

### Trusted action compilation

Extend `PrimitiveStep` with a serializable `params` mapping, but validate exact keys, types, allowlists, and ranges for each operation when a schema revision is compiled. Unknown keys or operations fail schema compilation.

Supported schema operations in the first slice are:

- `click`: one trusted button plus a verified entity binding; coordinates are resolved from the current scene only inside the native owner;
- `move`: a verified entity binding, for instrumented input tests or an action with a promoted visible hover effect;
- `key_tap`: one trusted allowlisted key and a bounded duration;
- `key_hold`: one trusted allowlisted key and a required bounded duration; and
- `sleep`: a bounded duration, with no held input lacking an already-scheduled release.

The compiler maps those schema operations to existing `MousePrimitive` and `KeyPrimitive` values. Bare key/button down operations, arbitrary coordinates, scripts, macros, gamepad operations, and unbounded holds are not schema operations in this milestone.

Invocation bindings may supply only parameter names declared by the promoted `ActionSpec`, such as a current verified entity ID. Decider selects an `action_id` and cannot create or modify `params` or bindings.

### Native input owner

Add one process-lifetime native input owner around the existing mouse and Win32 code. It:

- owns the only `Mouse` and keyboard instances used by the product path;
- tracks held keys/buttons and suppresses duplicate downs;
- schedules every release from monotonic time on a supervisor independent of perception, inference, and orchestration;
- caps tap, hold, sleep, and complete-plan duration in trusted code;
- checks exact target identity and foreground state before each injecting primitive and before each down event;
- exposes idempotent `release_all(reason)`; and
- records a primitive receipt for every attempted down/up/move/click and deadline release.

No model adapter, orchestrator, benchmark module, or action-discovery proposer imports the native owner directly.

### Native observation and actuation lease

Do not promote benchmark `StateSnapshotV2` into a second product truth. Add a thin immutable native observation envelope around the verified `StateSnapshot`. It carries:

- episode ID and monotonically increasing episode generation;
- monotonic capture/publication timestamps and observation age;
- capture integrity and provenance;
- exact current `hwnd`/`pid` plus the workspace-stable target identity;
- snapshot ID and schema revision; and
- quality status and missing-required-field reasons.

The compact policy payload is a redacted projection of that envelope and snapshot. The envelope itself remains trusted runtime metadata.

An accepted decision produces a short-lived `ActuationLease` containing the episode generation, snapshot ID, schema revision, catalog revision, directive revision, policy revision, exact target instance, expiry, selected action ID, and the current hard-mask digest. `Session.act()` validates the lease against the active session and a fresh pre-dispatch check immediately before execution. Window replacement, focus loss, schema/catalog/directive change, terminal state, untrusted capture, missing surface membership, or expiry rejects and releases.

`Session.act()` returns a structured execution receipt rather than only a Boolean. The receipt includes primitive results, release status, fresh after-capture identity/provenance, and expected-effect outcome. Expected-effect failure is not retroactive execution failure; it is negative evidence and a reason to suppress retry or enter recovery.

### Candidate and directive compilation

Compile candidates only from the current verified `ActionSurface`. Candidate descriptions are deterministically derived from promoted expected effects and trusted constraints; they never expose the primitive plan. Candidate order is canonicalized by `action_id` before inference.

The catalog revision is a content hash of the schema revision plus the canonical candidate IDs, descriptions, and trusted constraints. Dynamic availability is represented by the current surface and hard mask, not by mutating a catalog behind an unchanged revision.

Promotion must persist conservative native constraints for risk, retry, cooldown, and maximum plan duration. Missing or invalid native constraints fail catalog compilation rather than receiving an implicit permissive default.

The mask is the intersection of:

- current surface membership and action preconditions;
- capture quality, mode, cooldown, recovery, and terminal rules;
- directive allowed/forbidden IDs; and
- trusted per-action risk and confidence policy.

The orchestrator may only narrow this set. A missing or expired directive releases and observes unless an explicit constant bootstrap directive is active. A release-only safe transition is executor behavior, not an invented `noop` action.

### Decider request and response

For 2–255 candidates, make exactly one call to the upstream `Decider.system_one(state, questions, independent=True, layout="state_first")` API with this logical request:

```json
{
  "state": {
    "observation": {},
    "directive": {},
    "last_verified_outcome": {}
  },
  "questions": {
    "action": {
      "type": "choice",
      "instructions": "Which currently valid action best advances the directive from this observation?",
      "criteria": {
        "opaque_action_id": "observable intent and expected effect"
      }
    }
  },
  "independent": true,
  "layout": "state_first"
}
```

The compact state includes only stable verified fields/entities/relations needed to distinguish the current candidates, confidence/unknown markers, directive intent/revision, and at most one prior verified outcome. It excludes raw images, scene-graph dumps, coordinates, primitives, handles, scan codes, key names, credentials, and privileged fields.

Validate `answers.action` before wrapping it as a `StructuredDecision`:

- response shape and type are exact;
- choice is one of the offered IDs;
- probability keys exactly equal the offered IDs;
- every probability is finite and in `[0, 1]`;
- the rounded probabilities sum to one within `max(1e-3, candidate_count * 5e-5 + 1e-6)`, after which a local normalized copy may be used for comparison;
- reported confidence matches the selected probability within the same tolerance; and
- the selected choice is maximal within rounding tolerance.

The trusted wrapper, not the model response, adds snapshot, episode, catalog, directive, policy, and timing provenance.

Use one inference in flight. Capture and release supervision continue while inference runs. A worker deadline drops the response and marks the policy unavailable for that turn; it does not retry, queue another stale request, or fall back to a different model. A late result is discarded by snapshot/deadline checks even if the policy worker finishes it.

## Operating state machine

```text
Boot -> Observe -> Experiment -> ShadowReact -> ArmedReact
  |        ^           |             |             |
  |        +-----------+-------------+-------------+
  |              on unqualified state / reset
  +---------------------------------------------> Halted
                    on policy or executor fault

Halted -> Observe only after explicit recovery and release/reset checks
```

- `Boot`: verify manifests, bind one exact target instance, release the input allowlist, and health-check dependencies.
- `Observe`: capture and learn without requesting actions.
- `Experiment`: perform an explicitly armed reversible probe and promote only repeated causal effects.
- `ShadowReact`: run policy and hypothetical dispatch with no call to `Session.act()`.
- `ArmedReact`: execute only a current leased action through `Session.act()`.
- `Halted`: release, preserve evidence, reject normal actuation, and require explicit recovery.

## Execution graph

```text
Wave 0: freeze contracts + prove native CUDA/model preflight
   |\
   | +--> Wave 1: native input owner + Session.act lease
   | |         |
   | |         +--> Wave 2: qualify perception + promote native click action(s)
   | |
   | +--> Wave 3: native reactive contracts and dispatch gate
   |
   +----> Wave 4: in-process Decider worker + policy adapter

Waves 2 + 3 + 4
   |
   v
Wave 5: native shadow loop
   |
   v
Wave 6: bounded arming
   |
   v
Wave 7: orchestration + hardening
```

Later gates are meaningless until every incoming dependency has passed.

## Wave 0 — Freeze boundaries and prove the stack

Work:

- Keep this plan discoverable from `README.md`.
- Relabel generic candidate, policy, executor, controller, snapshot, and telemetry modules so the benchmark is an adapter rather than their owner.
- Freeze the native observation envelope, candidate catalog, decision, actuation lease, execution receipt, and state-machine contracts.
- Add import-boundary tests proving native control imports neither PlayJev nor Playwright and benchmark modules cannot import `Session.act()` or native input.
- Keep the native Windows Decider lock/manifest, source commit, and 0.8B model revision immutable.
- Run the Triton kernel smoke check and one BF16, state-first `system_one` decision in the Python 3.12 Windows profile.
- Preserve all repository-purity and privileged-data leak tests.

Gate:

- Host `pytest -q` passes without Playwright, model weights, CUDA, or benchmark assets.
- The native profile reports CUDA PyTorch, the RTX 4060, Triton 3.8, Flash Linear Attention, and Decider 0.9.0 from their pinned artifacts.
- The pinned 0.8B snapshot loads locally and answers the frozen request through `Decider.system_one` with exactly the offered probability keys.
- Failure of model preflight leaves the runtime in `Observe` with no native input.

## Wave 1 — Own native input and the actuation boundary

Likely files:

- `loop/adaptive/schema.py`
- `loop/adaptive/session.py`
- `loop/adaptive/runtime.py`
- `loop/os/keyboard.py` (new)
- `loop/os/mouse.py`
- `loop/os/primitives.py`
- a small unified native input-owner module
- focused native-input and actuation-lease tests

Work:

- Add validated `PrimitiveStep.params` with backward-compatible serialization and hashing.
- Implement allowlisted key-code compilation and Win32 tap/hold using existing `key_event()` and `send_input()`.
- Add the single input owner and release supervisor.
- Replace the click-only live runner with the complete bounded primitive compiler/runner.
- Add the `ActuationLease` checks and structured receipt at `Session.act()`.
- Release in `finally` on every normal and exceptional path; recovery-lock on any release failure.

Gate:

- Unit tests cover down/up ordering, duplicate-down suppression, deadlines, duration caps, focus loss, target replacement, exception, reset, shutdown, startup release, and `release_all()` idempotence.
- A locally instrumented window receives a bounded mouse click, key tap, and key hold through `Session.act()` with an explicit test-only verified action and lease.
- No native-input integration test runs unattended or without an explicit arm flag.

This wave proves mechanics, not action discovery. It does not make keyboard actions legal in an unknown workspace.

## Wave 2 — Qualify perception and promote native actions

Work:

- Start one fresh workspace and bind it to one exact target. Preserve that workspace through observation, experiment, promotion, shadow, and arming.
- Pass the existing 30-frame trusted-capture, track-stability, field-continuity, restart, and no-revision-churn gates.
- Complete the generic reversible experiment flow so acceptance does not require a remote service: use the existing local VLM path or a reviewed evidence-referencing plan artifact. Neither path may contain raw coordinates.
- Admit only a low-risk reversible click whose selector resolves uniquely and whose expected field effect can be observed and rolled back.
- Execute the probe twice under explicit experiment arming; promote only matching causal deltas with successful rollback.
- Persist conservative `risk`, `retry`, `cooldown`, and maximum-plan-duration constraints with the promoted action.
- Reproject and prove the promoted action is stable on the legal surface and still verifies through `Session.act()`.
- Repeat for a second distinct verified action only if the target supports one safely; two candidates are required for Decider quality qualification, not for the first native closed loop.

Gate:

- An empty schema exposes no actions and emits no input outside the explicitly armed experiment.
- Every promoted action cites two matching causal traces, a unique selector, a non-empty expected effect, and an immutable schema revision.
- Restarting the same target-bound workspace preserves the action; a different target identity is rejected.
- At least one reversible click action is promoted for product-loop integration.

## Wave 3 — Join verified state to reactive contracts

Likely files:

- `loop/adaptive/candidates.py`
- `loop/adaptive/snapshot.py`
- `loop/adaptive/policy.py`
- `loop/adaptive/executor.py`
- `loop/adaptive/runtime.py`
- focused adapter and gate tests

Work:

- Add the native observation envelope without making `StateSnapshotV2` a second source of truth.
- Generalize `Policy.score()` to consume one compact reactive-state value. The native adapter derives it from the verified envelope/snapshot; the benchmark adapter derives the same shape from `StateSnapshotV2`.
- Compile `ActionCatalog` and deterministic descriptions only from the current verified `ActionSurface`.
- Canonicalize candidate order and compute a content-addressed catalog revision.
- Make zero candidates release/observe without inference and one candidate deterministic without weakening later gates.
- Extend the native dispatcher to validate exact snapshot ID, episode generation, observation/decision age, schema/catalog/directive/policy revisions, current mask digest, current surface membership, target identity, focus, quality, recovery, and terminal state.
- Create an `ActuationLease` only after all checks pass; recheck it inside `Session.act()`.

Gate:

- Table-driven and seeded-fuzz tests prove out-of-surface IDs, stale snapshots/decisions, mismatched revisions, changed masks, expired directives, unfocused/replaced targets, untrusted capture, recovery lock, and terminal state cannot execute.
- Serialized model requests contain no coordinates, handles, key codes/names, primitive operation names, bindings, credentials, or privileged fields.
- `noop` is never invented when absent from the current verified surface.

## Wave 4 — Add the local Decider policy

Likely files:

- `loop/adaptive/decider_policy.py`
- CLI policy/model configuration under `python -m loop collect`
- the native Decider manifest and setup/smoke scripts
- frozen request/response tests plus an opt-in GPU/model smoke test

Work:

- Use the upstream in-process `Decider.system_one` API; do not reimplement its prompt rendering or call the unrelated `decider` package from PyPI.
- Load the pinned local model once in the policy worker and keep exactly one inference in flight.
- Enforce the request/response validation and deadline rules above.
- Record environment/model manifest hashes, load latency, inference latency, device, dtype, engine settings, and peak VRAM.
- Keep core tests independent of CUDA and model weights by injecting a fake `system_one` implementation; keep the real GPU/model smoke test opt-in.

Gate:

- Frozen requests produce stable selected IDs and probabilities within declared tolerance under the pinned runtime.
- Candidate reorder trials show no unacceptable position sensitivity on the frozen validation set.
- Malformed, partial, non-finite, wrong-key, timed-out, OOM, failed-worker, and late results all produce policy-unavailable/rejected outcomes; the controller releases or observes.
- `pytest -q` remains model-free.

## Wave 5 — Run the native shadow loop

Likely files:

- `loop/adaptive/controller.py`
- `loop/adaptive/cli.py`
- `loop/adaptive/telemetry.py`
- native shadow integration tests

Work:

- Add native flags under the only top-level command, `python -m loop collect`; do not add another product CLI.
- Run capture/perception, policy inference, and orchestration as independent workers connected by size-one latest-value channels.
- Start with one explicit constant directive. The policy worker consumes only the newest qualified snapshot and current directive.
- Sequence candidate/mask compilation, Decider scoring, response validation, hypothetical dispatch, and replay logging.
- Never call `Session.act()` in shadow. Log the expected trusted primitive-plan digest, not primitive details in the model payload.
- Compare deterministic and Decider decisions on the same recorded native snapshots only when at least two candidates exist.

Gate:

- Sustained shadow operation against the real target emits zero input.
- Every accepted hypothetical decision was current, legal, target-bound, and executable at its would-be dispatch time.
- p95 capture-to-decision is below the measured useful control interval; stale decisions are discarded rather than queued.
- Occlusion, focus loss, resize instability, unknown mode, capture-integrity loss, and policy-worker loss reduce quality or transition to `Observe`/`Halted` as declared.

## Wave 6 — Arm in bounded stages

Use the same fresh, target-bound workspace qualified in Wave 2. Every stage requires a new explicit `--arm`; stop at the first failed effect, stale decision, identity/focus mismatch, release failure, or recovery lock.

Progression:

1. boot and release-only check;
2. one promoted action selected deterministically from a one-candidate surface;
3. repeated deterministic actions with verified effects and no blind retry;
4. if at least two promoted actions are simultaneously legal, one Decider-selected action under a constant directive;
5. repeated Decider decisions under that constant directive; and
6. event-driven directive revision while the reactive loop continues independently.

Mouse move, key tap, and key hold are not product arming stages until they have their own promoted expected effects. Their Win32 mechanics were already qualified in the instrumented Wave 1 test.

Gate:

- Executed stale, out-of-surface, masked, or wrong-target decisions: 0.
- Unreleased inputs after any tested stop path: 0.
- Every execution has matching capture, snapshot, schema, catalog, directive, policy, decision, lease, primitive, release, after-capture, and effect receipts.
- Failed expected effects create negative evidence/recovery state and do not automatically retry.

## Wave 7 — Add asynchronous orchestration, qualify, and harden

Work:

- Add event detection and versioned directive publication after the constant directive path is stable.
- Ensure orchestrator delay, failure, or restart cannot stall capture, reaction, or input-release supervision.
- Inject stale capture, slow/malformed inference, policy-worker failure, directive expiry, mask/catalog/schema change, target replacement, focus loss, exception during input, release failure, reset, terminal transition, and shutdown.
- Run 30 minutes of concurrent perception, Decider, orchestration, and native input ownership in shadow; then repeat a bounded armed soak only after shadow passes.
- Compare pinned 0.8B and 2B models on the same frozen states and multi-candidate native scenarios.

Model selection gate:

- Freeze the scenario manifest and primary metrics before comparison.
- Require at least two legal candidates in every scenario used to claim Decider quality.
- Require zero safety-gate regressions, at least 10% free VRAM at peak, no sustained capture starvation, and p95 capture-to-dispatch below the measured useful interval.
- Select the model with higher verified-effect/goal score, then lower p95 latency, then lower peak VRAM.
- A model must beat the deterministic control on the frozen multi-candidate scenarios to become the armed policy. If neither model does, retain deterministic-only armed operation and keep Decider in shadow.

## Work ownership and verification

Use small reviewable waves. One implementer owns a wave end to end. Wave 1, Wave 3, and the offline portion of Wave 4 may proceed independently only after Wave 0 contracts land; Wave 2's armed experiment depends on Wave 1.

After each source wave, run focused tests plus:

```powershell
python -m pytest -q
python -m pip check
python -m compileall -q loop tests
codegraph sync
```

Keep one passing commit per wave. Do not mix benchmark expansion, training, gamepad, or second-fixture work into these commits.

## Required telemetry

Every reactive turn records or references by immutable hash:

- target identity, exact target instance, and focus result;
- frame digest, capture provenance/integrity, capture/publication time, and observation age;
- episode, snapshot, schema, catalog, mask, directive, and policy revisions;
- candidate IDs, deterministic descriptions, and mask reasons;
- environment/model manifest hashes and complete returned probabilities;
- capture, perception, request-build, model-queue, inference, gate, input, verification, directive-age, and end-to-end monotonic timings;
- decision, lease, dispatch, primitive, release, and execution receipts;
- before/after snapshot IDs and expected-effect result; and
- abstention, rejection, transition, recovery, or terminal reason.

Never log chain-of-thought, secrets, raw credentials, privileged fixture state, or model-visible primitive data.

## Definition of done

The product-path milestone is complete only when one fresh native workspace can, in order:

1. bind one exact real target window and reject replacement;
2. start from `empty_schema()` in `Observe` with no legal action;
3. qualify perception and promote at least one reversible action from repeated pixel/input causal evidence;
4. expose only current verified opaque IDs to the policy path;
5. run the complete local Decider path in shadow when two or more candidates exist, without emitting input;
6. reject stale, illegal, masked, unqualified, wrong-target, or malformed decisions;
7. execute an accepted promoted action through `Session.act()` and the native input owner;
8. release every held input within its deadline on every tested stop path;
9. verify the expected effect from a fresh trusted capture; and
10. write a complete replayable telemetry chain.

At least one action is sufficient to prove the native closed loop because the one-candidate rule intentionally bypasses Decider. Claiming Decider as the selected armed policy additionally requires the Wave 7 multi-candidate comparison gate. Otherwise deterministic-only armed mode is the correct safe result.

No PlayJev checkout, Playwright browser, teacher, remote proposer, or training loop may be required to demonstrate this result.

## Deferred until done

- Keyboard-action discovery and promotion beyond instrumented executor tests.
- PlayJev/Playwright benchmark expansion.
- Model training, teacher rollouts, behavior cloning, DAgger, RL, or online updates.
- Gamepad support.
- Multi-process/shared-memory isolation beyond the in-process policy worker.
- A second application or game fixture.
- General autonomous probing of keyboard controls outside an explicitly armed target and bounded allowlist.

## Upstream interface basis

- Decider implementation and `system_one` API: <https://github.com/Mapika/decider>
- Native Windows Triton compatibility: <https://github.com/triton-lang/triton-windows>
- Flash Linear Attention installation contract: <https://github.com/fla-org/flash-linear-attention/blob/main/INSTALL.md>
- Decider 0.8B weights: <https://huggingface.co/Mapika/decider-0.8b>
- Decider 2B weights: <https://huggingface.co/Mapika/decider-2b>
