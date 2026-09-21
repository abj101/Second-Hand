# Adaptive Orchestrator Architecture Plan

Status: benchmark-only reference; native execution sequencing superseded by `native-decider-orchestration-plan.md`
Date: 2026-09-20
First development environment: PlayJev Infinite Mario

> This document describes the optional PlayJev benchmark path. It is not the product-path execution plan and does not authorize PlayJev or Playwright as production input providers.

## Decision

Build a hierarchical game agent with a slow adaptive orchestrator and a fast fixed-weight reactive core.

The live path is deliberately small:

`pixels -> qualified perception snapshot -> bounded candidates + directive mask -> reactive scorer -> validated action -> benchmark step`

The orchestrator owns mode, schema revisions, strategic intent, recovery, and evaluation. It publishes a versioned directive asynchronously; it does not participate in every frame. The reactive core consumes the latest qualified snapshot and directive, scores only the supplied candidate IDs, and returns structured data that the executor can validate directly. Learning, teachers, DAgger, and reinforcement learning are deferred until this path is reliable.

The existing desktop `ActionSurface` and `Session.act()` contract remain fail-closed and desktop-only. The Mario benchmark gets an explicit `step(action_id)` boundary so benchmark interaction is real, bounded, and independently testable.

## Why Infinite Mario first

PlayJev's Mario environment has the properties needed for rapid iteration:

- A bounded seven-action vocabulary: `noop`, `left`, `right`, `jump`, `right jump`, `right run`, and `right run jump`.
- Seeded deterministic resets and byte-identical replay checks.
- A virtual clock; the game advances only when the harness steps it.
- One environment step is approximately 100 ms of game time: three 30 Hz game ticks. This virtual duration is separate from wall-clock latency.
- A canvas-only pixel observation resized to 448 x 336.
- A monotonic progress score and unambiguous death/win termination.
- Instrumentation that can provide evaluation labels without entering the live policy input.
- Headless Chromium execution with multiple pages stepped concurrently.
- Natural temporal demands: scrolling, inertia, jumping, enemies, gaps, and partial observability.

PlayJev reports roughly 258 environment steps per second with eight headless pages on its test machine, and its teacher runs at roughly 175 steps per second. Those numbers are expectations to reproduce locally, not acceptance facts for this repository.

Mario is also a useful stress test because motion-sensitive decisions require temporal information and one-step observation delay materially damages reactive performance. PlayJev's raw two-frame ablation did not help Mario, so the plan starts with derived temporal evidence and treats raw multi-frame policy input as a later experiment.

## Source and licensing boundary

Use a fetch script to place a complete PlayJev checkout at the pinned revision in an ignored local benchmark cache. Do not commit Mario assets while their redistribution status is unresolved, and do not treat PlayJev as an installable Python package. Our benchmark adapter may load the checkout explicitly or invoke its entry points, but production runtime code must not import it.

Source:

- PlayJev: <https://github.com/OmniJev/PlayJev>
- Upstream game: <https://github.com/robertkleffner/mariohtml5>
- PlayJev source revision inspected for this plan: `2d7a0280841e3244a299c9c640efc8cefc46d476`

The fetch/cache manifest must retain at minimum:

- PlayJev's Apache-2.0 `LICENSE` and required modification notices.
- The upstream license and PlayJev's `UPSTREAM` provenance.
- PlayJev's `pj.json`, `pj_hook.js`, determinism check, notes, and teacher documentation.
- The shared virtual-time/input shim required by the hook.
- A record of the exact PlayJev and upstream revisions, file hashes, and every local patch.

The upstream code is identified as Unlicense. PlayJev's harness, driver, teacher, documentation, and patches are Apache-2.0. PlayJev explicitly warns that Mario sprite artwork belongs to Nintendo and is not covered by the code license. Before distributing game assets, captured frames, datasets, replays, reports containing screenshots, or derived checkpoints, complete a licensing review. A local fetch avoids committing the assets but does not grant downstream redistribution rights. The fallback is PlayJev's MIT-licensed Snake fixture with the same harness contract.

## Target architecture

```text
Frame capture
    |
    v
Perception -> qualified snapshot -> candidate compiler + hard directive mask
    |                                      |
    |                         latest directive from async orchestrator
    v                                      v
Reactive Decider scorer -> dispatch gate -> input executor -> Mario step(action_id)
    ^                                      |
    |                                      v
    +------------- outcome/event bus <-----+
                           |
                           v
                    async orchestrator

Deferred: trajectory collection, teacher labels, behavior cloning, DAgger, RL
```

## Component responsibilities

### 1. Environment adapter

The environment contract must preserve the lifecycle and provenance needed by training and evaluation:

```text
async reset(seed, episode_id, episode_generation) -> StepResult
actions() -> action catalog revision + current bounded candidates
async step(action_id) -> StepResult
async close()

StepResult:
  observation, frame_digest, episode_id, episode_generation, step, tick, virtual_game_time
  score, score_delta, terminated, truncated, termination_reason
  environment_revision, action_catalog_revision, errors, privileged_info
```

`step` advances virtual game ticks. Each result also carries monotonic wall-clock capture and dispatch timestamps for latency accounting; wall time never drives game time. `episode_generation` increments on reset and invalidates in-flight state from prior episodes. The reset result uses `score_delta = 0`, `reward = 0`, `terminated = false`, `truncated = false`, and `termination_reason = null`.

For Mario, adapt PlayJev's hook contract rather than driving a normal browser window with OS input. The virtual clock and synthetic key events make training deterministic and much faster than real time.

`privileged_info` is evaluation data. It may be read by offline schema evaluators and debugging tools. It must never enter the live snapshot, reactive request, model prompt, or action-selection path. For the first fixture, reward is explicitly `score_delta`; it is not assumed to be supplied independently by the game.

The adapter must expose both virtual tick counts and monotonic wall-clock timestamps so observation-to-action delay can be measured and deliberately injected during evaluation. Start with one page and one sequential loop. Vectorized pages and process isolation are later optimizations, not prerequisites.

### 2. Baseline perception

Do not begin with a broad visual ontology. The first Mario profile contains only what the reactive decision needs:

- controlled-player box and stable track identity;
- horizontal and vertical displacement over the last three frames;
- grounded/airborne estimate when supported;
- visible platform spans and the nearest forward gap or obstacle;
- camera-scroll estimate and terminal/title state; and
- confidence, unknown status, and evidence provenance for every field.

OCR, generic region proposals, and wider object labels are implementation tools, not required policy fields. Add another field only when a recorded failure shows the current profile cannot distinguish the needed action.

Frame history must be available to perception, but raw policy input is an experimental choice. Mario's baseline compares current-frame-only, two-raw-frame, and current-frame-plus-derived-motion variants. PlayJev reported that a separate previous image helped Breakout but hurt Mario and Racer, so two raw frames are not a mandatory Mario input.

Bootstrap is qualified on recorded Mario frames using privileged state only as evaluation labels. The live snapshot must be usable without those labels before the reactive path is armed. A VLM may later propose schema names and relationships, but tracked geometry and decoded values remain grounded in deterministic perception evidence.

### 3. Canonical world state

Every discovered game schema compiles into a versioned representation, initially `StateSnapshotV2` or an equivalent sidecar that does not silently change the current runtime type:

```text
global fields
entities[type, stable_id, attributes, confidence]
relations[src, kind, dst, confidence]
mode
recent events
observation age
confidence and provenance per value
```

Game-specific fields are allowed, but the reactive policy consumes them through this common container. Schema revisions remain immutable and evidence-linked.

The orchestrator may retain Mario's privileged `info` only for termination and offline perception evaluation. It cannot copy those values into the live state supplied to the reactive core. Serialization, schema migration, and conversion into the existing verified runtime snapshot must be explicit and tested.

### 4. Candidate action surface and executor boundary

Separate actions that are available from skills whose effects are already known.

**Candidate action surface**

- The actions the environment or controller can execute now.
- Used by the reactive core, deterministic baseline, and telemetry recorder.
- Mario begins with the seven actions declared by the fixture.
- Each candidate has a stable ID, adapter-local command handle, semantic description, availability, input budget, and retry/no-change policy.
- The current directive compiles hard constraints into a typed `ActionMask` before model scoring. Goals and strategy remain context; safety, focus, terminal, cooldown, and forbidden-control rules are masks.
- Zero candidates means `halt`/`recover` and an orchestrator event. One candidate bypasses model ambiguity and selects that candidate after the same freshness and dispatch checks.
- The first milestone executes candidates only through `benchmark.step(action_id)`.
- `step(action_id)` is the early executor boundary: it resolves the candidate, applies its input transition, advances virtual ticks, and returns a `StepResult`.
- The desktop `Session.act()` path remains separate, fail-closed, and desktop-only; the benchmark adapter never calls it.
- Candidate execution on unknown desktop environments is explicitly out of scope until a later safety design and ADR approve it.

**Verified skill surface**

- Reusable actions or action sequences with learned bindings, preconditions, and expected effects.
- Used for reliable exploitation and planning.
- Populated from repeated causal evidence.

All policies score candidate IDs. They never emit free-form coordinates, arbitrary key names, or raw input scripts. Candidate actions cannot enter the verified `ActionSurface` or desktop executor without normal promotion and arming.

The executor owns explicit input state: `press`, `hold`, and `release` transitions per control. A watchdog releases every held control after its bounded deadline and on reset, termination, stale decision, executor error, or loss of focus. A rejected decision leaves the previous transition under watchdog control or emits an explicit safe release; it never silently means “release everything” or “continue forever.”

This plan does not silently supersede the current adaptive-kernel ADR. Phase 1 must add an ADR that documents the benchmark-only candidate executor and explicitly preserves the desktop fail-closed rule.

### 5. Adaptive orchestrator

The orchestrator is the only component allowed to change operating mode. Its state machine is:

1. `observe` — establish perception confidence and schema coverage.
2. `map` — compile the current bounded action candidates.
3. `qualify` — establish snapshot confidence, action availability, and executor readiness.
4. `react` — run the fixed-weight reactive core at gameplay frequency.
5. `evaluate` — run fixed seeds and delayed-observation tests.
6. `recover` — reset an episode, invalidate prior `episode_generation` state, or stop input after loss of control.

The orchestrator must distinguish perception uncertainty from execution authorization. Low perception confidence produces a safe candidate or halt event. Explicit arming, target identity, bounded candidates, input budgets, dispatch-time focus checks, and environment reset provide the execution safety boundary. The orchestrator publishes directives asynchronously; the reactive loop never waits for a new directive.

Every episode begins with an ordered reset barrier:

```text
environment.reset(seed, episode_id, episode_generation + 1)
perception.reset()
action_retry_filter.reset()
executor.release_all()
directive_store.invalidate_prior_generation()
decision_store.invalidate_prior_generation()
```

Termination closes the episode before any component is reused. This prevents tracks, prior frames, held actions, retry suppression, directives, and decisions from leaking between episodes.

### 6. Reactive core

Define one model-neutral policy interface:

```text
score(snapshot, directive, candidate_actions) -> StructuredDecision
```

Use a fixed-weight Decider checkpoint as the current reactive baseline. Keep model choice provisional between Decider-2B and Decider-0.8B: select using combined closed-loop quality, p95 wall-clock latency, and total VRAM while the perception and orchestrator workers are running. Compare both against a deterministic policy on the same seeds; random or teacher scores are secondary references.

The model receives serialized canonical state, a directive, and ordered candidate descriptions. It returns only structured choice data: selected candidate ID, confidence, and probabilities for supplied IDs. It never generates keys, coordinates, rationale, or executable code. The wrapper validates the response against the candidate set and action mask.

Pin both candidate model revisions and define the exact `StateSnapshotV2` JSON serialization, candidate option schema, action-order shuffling, temporal fields, tokenizer limits, and GPU budget. Mario initially uses the constant directive `progress right while surviving`; strategic directives become variable only after the fixed loop proves that directives affect choices.

The PlayJev visual checkpoint is a harness-integration reference only. Jevlike and NanoJev-style models remain later experiments after the structured-state baseline is measurable.

The dispatch gate rechecks, at action dispatch time, snapshot `episode_generation`, snapshot age, directive revision and TTL, action-catalog revision, candidate availability, focus/target identity, and terminal state. A newer snapshot alone does not cancel an in-flight decision. The decision remains eligible until its wall-clock age deadline, then is dispatched only if `episode_generation`, catalog, directive, and action preconditions still match; otherwise it is safely released according to the executor watchdog.

### 7. Runtime telemetry

Record one immutable row per decision:

```text
episode_id, episode_generation, seed, step, environment and action-catalog revisions
frame reference and frame-history references
schema revision and structured state
perception confidence and observation age
strategic directive and compiled action mask
ordered candidate actions
reactive structured decision
selected, dispatched, and executed action
next structured state
score, score delta, reward, terminated, truncated, termination reason
capture and action provenance
perception, policy, and end-to-end latency
```

Store raw frames once and reference them by digest. Split train, validation, and test data by seed and episode, never by individual frame, to prevent temporal leakage.

### 8. Deferred learning appendix

Teacher rollouts, behavior cloning, DAgger, and reinforcement learning are intentionally outside the initial playable path. Once the fixed-weight reactive loop has a stable state/action contract and repeatable closed-loop metrics, future work may add teacher labels and on-policy data to the telemetry schema. Any learner must consume the same perceived snapshots and candidate IDs; it must not change the executor boundary or introduce privileged state into the live input.

### 9. Reaction loop

The deployed loop has two timescales:

- Reactive core: one bounded score-and-dispatch cycle per environment step, with a measured wall-clock budget.
- Strategic orchestrator: event-driven or low-frequency, updating intent rather than individual key presses.

The initial implementation is one sequential `perceive -> compile -> score -> dispatch -> benchmark.step` loop plus one asynchronous orchestrator worker. Use latest-value state and directive stores; do not build multi-process or shared-memory machinery until profiling shows a need.

Perception updates must not cancel a decision that is already scoring. A decision has a wall-clock age deadline and is revalidated at dispatch. Newer snapshots may supersede an unstarted decision, but they cannot starve the loop by repeatedly cancelling work. If scoring misses its deadline, the executor watchdog applies the safe transition and the orchestrator receives a timeout event.

No-change suppression is candidate metadata, defaults to false, and applies only when the adapter declares it correct. Mario's meaningful `noop` and repeated held inputs must not be suppressed generically. Every evaluation includes zero-delay and one-step-delayed observations, plus a distinction between virtual game ticks and wall-clock latency.

## Repository integration plan

Keep benchmark assets and application-specific teachers outside the product runtime:

```text
benchmarks/
  infinite_mario/
    README.md
    source-manifest.json  # commits, hashes, licenses, browser/dependency versions
    seed-manifests/
    adapter.py
    tests/

scripts/
  fetch_playjev_mario.py  # populates ignored .cache/benchmarks/playjev/<commit>/

loop/adaptive/
  environment.py         # generic reset/actions/step protocol
  controller.py          # runtime state machine; avoids conflict with orchestrate.py
  candidates.py          # dynamic candidate action contract
  executor.py            # press/hold/release, watchdog, dispatch checks
  policy.py              # model-neutral scoring protocol
  telemetry.py           # runtime decisions, outcomes, and latency
```

The benchmark may know Mario. Nothing under `loop/adaptive/` may import it or encode Mario-specific labels, actions, physics, or coordinates. `loop/adaptive/orchestrate.py` retains schema hypothesis/validation/promotion; `controller.py` owns runtime coordination.

Playwright and model libraries belong in optional development extras. The benchmark environment initially targets Python 3.12 and pins Playwright 1.63.0 plus its Chromium revision to match the inspected PlayJev setup. Running the existing core tests must not require Chromium, CUDA, model weights, or benchmark assets. Development setup must use an editable install such as `pip install -e ".[dev]"` so plain `pytest -q` resolves the package.

## Development phases and gates

### Phase 0 — Reproducible fixture and real step boundary

- Pin the PlayJev revision.
- Resolve the asset-distribution decision.
- Implement the ignored-cache fetch path and record all source-file hashes.
- Bring up one Mario page through the hook.
- Implement `step(action_id)` with explicit press/hold/release transitions and the watchdog.
- Run PlayJev's actual `games/mario/check_det.py` and our adapter-level replay test.
- Record the full environment fingerprint: PlayJev and upstream commits, patch digest, action catalog, viewport, JPEG/resize settings, `step_frames`, Python lock, Playwright version, and Chromium revision.
- Define immutable training, validation, and test seed manifests.
- Establish deterministic and random baselines on training and validation seeds only; reserve the test manifest for final selected-policy evaluation.

Gate: within one pinned environment fingerprint, same-seed frame digests, scores, privileged state, and termination are identical; resets are reliable; no network access is needed after setup. Cross-browser byte identity is not assumed. These are adapter-level baselines and replay traces, not orchestrator demonstrations or student-training examples.

### Phase 1 — Perception and reactive contracts

- Implement the generic environment and candidate-action interfaces.
- Add the benchmark-only candidate-execution ADR and boundary definition.
- Implement the complete per-page episode reset barrier.
- Define the minimal Mario perception profile and qualify it against labeled frames.
- Define snapshot, directive, typed mask, structured decision, and dispatch-time validation contracts.
- Add `episode_generation`, freshness, focus, and target checks.

Gate: a single sequential perceive-to-step run produces valid structured decisions; `episode_generation`/reset invalidation works; no privileged field enters the live snapshot; candidate actions cannot reach `Session.act()` or the desktop executor.

### Phase 2 — Fixed-weight reactive play

- Run the selected fixed-weight Decider checkpoint through the real perception path.
- Keep the orchestrator asynchronous and directive-driven.
- Measure perception, policy, dispatch, input, and total wall-clock latency separately from virtual game time.
- Compare Decider-2B and Decider-0.8B against deterministic and random baselines.
- Evaluate PlayJev's released visual checkpoint only as a pixel-policy harness reference.

Gate: choose the provisional model only after combined quality, p95 wall-clock latency, and total VRAM are reported; p95 end-to-end latency fits the measured local environment-step budget; stale or unfocused decisions never dispatch.

### Phase 3 — Orchestrator and recovery

- Run event-driven directive updates without blocking reactive steps.
- Exercise low-confidence, timeout, terminal, focus-loss, and reset recovery.
- Confirm hard directive constraints compile to typed masks and zero/one candidate behavior is deterministic.
- Compare current-frame and derived-motion perception variants.

Gate: orchestrator latency or failure cannot starve the reactive path; recovery releases held inputs and invalidates old generations; every executed action has a matching dispatch receipt.

### Phase 4 — Evaluation and transfer preparation

- Evaluate offline structured-choice validity, calibration, option-order invariance, and closed-loop score.
- Evaluate closed-loop score and win rate on unseen seeds.
- Record the telemetry required for later learning without starting training.

Gate: the fixed-weight core is repeatable, bounded, and measurable on held-out seeds; test seeds remain untouched.

### Deferred Phase — Learning

- Add teacher rollouts, behavior cloning, DAgger, and RL only after the preceding phases are complete.
- Preserve the same perception, candidate, directive, `episode_generation`, and executor contracts.
- Keep teacher and privileged state out of the deployed reactive input.

### Phase 5 — Transfer

- Add a second fixture without changing the environment, candidate, telemetry, policy, or orchestrator contracts.
- Let the orchestrator create a new schema and later learning workspace.
- Reuse the policy architecture with a game-specific adapter or checkpoint.

Gate: adaptation requires new fixture/teacher configuration and data, not a new agent runtime.

## Evaluation matrix

Measure every policy on the same held-out seeds:

| Dimension | Required measurements |
|---|---|
| Outcome | score, progress, win rate, death cause, episode length |
| Policy | structured-choice validity, calibration error, entropy, deterministic-baseline delta |
| Perception | field accuracy, track identity switches, relation accuracy, confidence calibration |
| Timing | capture, perception, policy, input, and end-to-end p50/p95 latency |
| Robustness | one-step delay, altered resolution, option shuffle, visual variation, recovery states |
| Ablation | current frame only, derived motion, pixels, no directive, deterministic baseline |
| Systems | steps/second, GPU memory, reset time, deterministic replay failures |

Closed-loop outcome is authoritative. Offline agreement may improve while gameplay gets worse because the student visits a different state distribution.

Seed manifests are immutable and versioned. Model and threshold selection use validation seeds only, and final claims use untouched test seeds. Future learning may reserve training seeds, but it cannot alter the initial playable evaluation. Every comparison is paired by seed and reports confidence intervals in addition to means.

## Explicit non-goals for the first milestone

- Multi-game joint training.
- Reinforcement learning.
- Autonomous experimentation on arbitrary desktop applications.
- Adding a teacher, DAgger, or RL loop before the fixed-weight reactive path is reliable.
- Training a new perception backbone from scratch.
- Building a custom policy architecture before the data and evaluation loop works.

## Principal risks

1. **Asset licensing:** Mario code and artwork do not share the same licensing status. Resolve before vendoring assets.
2. **Privileged-state leakage:** instrumentation can make results meaningless if teacher data enters student inputs.
3. **Harness overfitting:** a policy may exploit option order, static backgrounds, or timing artifacts rather than perceive the game.
4. **Temporal blindness:** one frame does not expose velocity; temporal evidence is required even when supplying a second raw frame hurts the policy.
5. **Latency mismatch:** offline quality does not guarantee correct delayed action.
6. **Schema churn:** learned IDs must remain stable across frames, episodes, and process restarts.
7. **Metric substitution:** teacher agreement is not gameplay performance.
8. **Scope leakage:** Mario-specific code must remain in the benchmark adapter and profile layers.

## First deliverable

A local command should fetch the pinned fixture into the ignored cache, launch one deterministic headless Infinite Mario page, run the sequential `perceive -> compile mask -> score -> dispatch -> step(action_id)` loop, and produce a report containing deterministic/random baseline score, reactive score, termination reasons, episode length, virtual ticks, wall-clock p50/p95 latency, model choice, VRAM, environment fingerprint, and invalidation/watchdog failures.

The orchestrator runs as one asynchronous worker that publishes a versioned directive. No multi-process, shared-memory, teacher, or model-training path is required for this deliverable.

## References

- PlayJev repository and training methodology: <https://github.com/OmniJev/PlayJev>
- PlayJev harness contract: <https://github.com/OmniJev/PlayJev/blob/main/docs/HARNESS.md>
- PlayJev Infinite Mario notes: <https://github.com/OmniJev/PlayJev/blob/main/games/mario/NOTES.md>
- PlayJev Mario teacher: <https://github.com/OmniJev/PlayJev/blob/main/games/mario/TEACHER.md>
- Decider: <https://github.com/Mapika/decider>
- Jevlike: <https://github.com/vinnylarouge/jevlike>
- NanoJev: <https://github.com/TianyuCodings/NanoJev>
