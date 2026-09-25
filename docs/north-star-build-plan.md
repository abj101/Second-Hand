# North Star Build Plan

Status: execution sequence
Date: 2026-09-21
Vision: [`perception-reaction-orchestration-north-star.md`](perception-reaction-orchestration-north-star.md)

This plan ends with a functional product loop. Functional means the acceptance list in the north star passes on one fresh workspace: empty schema, one promoted reversible action, shadow with no input, a real Decider call when two or more candidates exist, one leased `Session.act()`, release while scoring is in flight, and a receipt whose after-capture provenance comes from a fresh capture.

`system.md` and ADR 0001 stay the product invariants. `native-decider-orchestration-plan.md` stays the stack-proof note (Python 3.12 profile, pinned Decider commit, 0.8B revision, VRAM smoke). It is not a second build sequence.

## Already built

Do not rebuild these. Later phases only close the holes named below.

- Window capture, provenance, scene graph, evidence ledger, immutable schema, projection, click promotion, `Session.observe`.
- `NativeObservation`, `ActuationLease`, `ExecutionReceipt`, catalog-from-surface, mask, directive register, `Executor.dispatch`.
- `NativeInputOwner`, keyboard, bounded primitive params, `PolicyWorker`, `DeciderPolicy` (tested with a fake `system_one`).
- `NativeShadowLoop` and staged `run_armed_collect`. Shadow does not call `Session.act()`.

## Holes this plan closes

From the code audit and the vision audit:

- Armed stages 4–5 label `DeterministicPolicy` as Decider.
- `Session.act()` does not recheck `hard_mask_digest`. Revision checks run only when attributes happen to exist on the session. A caller without a lease can still inject.
- `after_capture_provenance` is always empty. After-capture hwnd and pid are copied from the bound target.
- A newer snapshot does not invalidate a decision whose bound entity moved.
- Coordinates can be resolved from a scene other than the leased one.
- `degraded` quality does not fail closed.
- `OperatingState` is an enum. Live mode is CLI flags.
- The release path is inside `act()`, so a blocked policy worker is not a proven release clock.
- Live shadow and arm construct `DeterministicPolicy`. Decider is unwired.
- The orchestrator republishes caller intent. It does not treat the directive as a forbid-subset over the surface.
- The benchmark runner is not the product proof. Leave it on its own adapter. Do not cite it as this plan's gate.

## Execution

One parent orchestrator. Implementers and reviewers are Composer 2.5 subagents (`composer-2.5`). The parent does not write phase code.

For each phase:

1. The parent freezes the brief: outcome, files, non-goals, gate command. The brief includes the repo path `C:\Users\Abj1\Documents\GitHub\Avatar` and forbids edits to this plan and the north star.
2. One Composer 2.5 implementer (`generalPurpose`) executes that brief. One phase, one implementer. No best-of-n.
3. The parent runs the gate. The implementer's own test claim is not acceptance.
4. A second Composer 2.5 subagent reviews the diff against the phase outcome and the gate output. It does not expand scope.
5. A failed gate returns to step 2 with the failure attached. The next phase does not start.
6. After the gate, the parent runs `python -m pytest -q` and `codegraph sync` on the repo path.

Parallelism is only the set marked below, and only because the file lists are disjoint. If an implementer touches a file outside its list, the parent stops that phase and reverts the overlap. The parent merges. Subagents do not commit unless the operator asks.

Phases 2, 3, and 4 start together after phase 1. Phase 5 waits for 3 and 4. Phase 6 waits for 2 and 5. Phase 7 waits for 6.

## Phase 1 — Lease tells the truth

Depends on: nothing
Files: `loop/adaptive/session.py`, `loop/adaptive/executor.py`, `loop/adaptive/arm.py`, `tests/loop/test_native_input.py`, `tests/loop/test_native_dispatch.py`

Work:

- Recheck `hard_mask_digest` inside `Session.act()` before the first primitive and before every input-down.
- Require catalog, directive, policy, and episode generation on the lease path. A missing field rejects.
- Remove the path that injects without a lease. `Session.act()` without a lease and an owner returns a rejected receipt and sends no input.
- Fill `after_capture_provenance`, hwnd, and pid from the fresh after-capture, not from the previously bound target.
- Relabel armed stages 4–5 as deterministic until phase 5. `decision_source` names the policy object that scored.

Out of scope: wiring `DeciderPolicy`, state-machine transitions, orchestrator behavior.

Gate: `python -m pytest -q tests/loop/test_native_input.py tests/loop/test_native_dispatch.py`

The tests must fail if the mask digest is stale, if a lease field is missing, if `act()` without a lease injects, or if a deterministic score is recorded as Decider.

## Phase 2 — Release clock

Depends on: phase 1
Files: `loop/os/input_owner.py`, `loop/os/keyboard.py`, `loop/os/mouse.py`, `tests/loop/test_native_input.py`

Work:

- Arm the release deadline at input-down on a clock that does not share the policy worker thread.
- `release_all` stays idempotent. A blocked or dead scorer still releases.
- A later decision must not move an already-armed deadline.

Out of scope: `cli.py`, Decider, orchestrator.

Gate: `python -m pytest -q tests/loop/test_native_input.py`

Include one test where a scored plan is in flight, the worker does not return, and held input is up before the deadline plus a small slack.

## Phase 3 — Drive the operating state

Depends on: phase 1
Files: `loop/adaptive/contracts.py`, `loop/adaptive/cli.py`, `tests/loop/test_native_contracts.py`

Work:

- One transition function implements the north star state diagram: Boot, Observe, Experiment, ShadowReact, ArmedReact, Halted.
- CLI flags request a transition. They do not jump. Failed model preflight remains Observe.
- Halted reaches Observe only after release and an explicit recovery.

Out of scope: input owner, Decider weights, benchmark `run_episode`.

Gate: `python -m pytest -q tests/loop/test_native_contracts.py`

## Phase 4 — Mask, binding, directive

Depends on: phase 1
Files: `loop/adaptive/candidates.py`, `loop/adaptive/snapshot.py`, `loop/adaptive/policy.py`, `loop/adaptive/orchestrator.py`, `loop/adaptive/executor.py`, `tests/loop/test_native_dispatch.py`, `tests/loop/test_native_faults.py`

Work:

- Directive forbid-subset only. The mask computes the legal set. The directive cannot add an id.
- `degraded` and `untrusted` produce an empty actionable mask.
- If the latest snapshot id differs from the decision, dispatch only when the bound entity id still resolves to the same node. Otherwise reject as a rebound.
- Stamp the scene id into the lease. Coordinate resolution reads that scene only.
- Attention hints affect the model request projection only. The stored snapshot stays intact.
- Orchestrator publishes intent, forbid-subset, and TTL. Wake reasons are schema-field changes, repeated no-effect, quality loss, expiry, and heartbeat.

Out of scope: `cli.py` state transitions, live Decider weights, Mario ontology.

Gate: `python -m pytest -q tests/loop/test_native_dispatch.py tests/loop/test_native_faults.py`

## Phase 5 — Decider in shadow

Depends on: phases 3 and 4
Files: `loop/adaptive/cli.py`, `loop/adaptive/controller.py`, `loop/adaptive/decider_policy.py`, `loop/adaptive/policy_worker.py`, `tests/loop/test_decider_policy.py`, `tests/loop/test_policy_worker.py`, `tests/loop/test_native_shadow.py`

Work:

- Shadow uses `DeciderPolicy` when preflight passes, otherwise `DeterministicPolicy`.
- The recorded source matches the object that scored. A fallback is never named Decider.
- Zero candidates do not submit. One candidate does not submit. Two or more submit one in-flight turn.
- Late, malformed, and timed-out results are policy-unavailable and do not dispatch.
- Shadow still never calls `Session.act()`.

Out of scope: downloading weights inside unit tests, arming, changing the pinned stack.

Gate: `python -m pytest -q tests/loop/test_decider_policy.py tests/loop/test_policy_worker.py tests/loop/test_native_shadow.py`

Unit tests inject a fake `system_one`. The existing opt-in GPU smoke stays opt-in.

## Phase 6 — Functional loop

Depends on: phases 2 and 5
Files: `loop/adaptive/arm.py`, `loop/adaptive/experiment.py`, `loop/adaptive/cli.py`, `tests/loop/test_native_promotion.py`, `tests/loop/test_native_shadow.py`, plus one new focused test module if the existing files cannot host the loop test cleanly

Work:

- CI loop, no real `SendInput`: fresh workspace, empty schema, synthetic promotion of one reversible action, shadow pass with zero input, leased act through a fake owner, release, expected-effect check, receipt provenance populated.
- The same path with two promoted actions runs the fake Decider and records `decision_source` as Decider.
- A rebound between score and act rejects and releases.
- Operator demo, explicit arm only, not part of `pytest -q`: one real window, one promoted click, one leased act, inputs released. No unattended native-input run.

Out of scope: a second application, keyboard discovery, model bake-off, PlayJev.

Gate: `python -m pytest -q tests/loop/test_native_promotion.py tests/loop/test_native_shadow.py`

Phase 6 is the functional milestone. The operator demo is recorded in the phase note. It is not required for the CI gate, and it is required before calling the physical loop done.

## Phase 7 — Faults

Depends on: phase 6
Files: `tests/loop/test_native_faults.py`, `loop/adaptive/telemetry.py`, and the smallest production fix if a fault test fails

Work:

- Inject stale capture, rebound binding, slow and malformed inference, worker death, directive expiry, mask change, target replacement, focus loss, release failure, and shutdown.
- Each case releases and lands in the state the north star names.
- Record capture-to-lease latency on the CI loop. The desktop deadline is that measured interval plus binding stability. Do not import a Mario tick.
- Confidence stays telemetry. Do not add a cutoff in this phase.

Out of scope: a 30-minute GPU soak, 0.8B versus 2B selection, training.

Gate: `python -m pytest -q tests/loop/test_native_faults.py` and `python -m pytest -q`

## Plan complete

The plan is complete when phase 7's full suite is green and phase 6's CI loop matches the north star acceptance list. Decider is the armed policy only if a later, separate comparison beats the deterministic control. Until then, one-candidate deterministic arming is the successful result.

## Subagent brief

Copy this shape for every implementer. Fill the braces from the phase. Do not add goals.

```text
Repo: C:\Users\Abj1\Documents\GitHub\Avatar
Phase: {n} — {title}
Outcome: {one paragraph from this plan}
Files you may edit: {list}
Non-goals: {list}
Gate you must leave green: {command}
Vision: docs/perception-reaction-orchestration-north-star.md
Do not edit the vision, this plan, or files outside the list.
Do not add a dependency. Do not invent noop. Do not label a deterministic score as Decider.
```

The reviewer brief is the same plus the diff and the parent's gate output. The reviewer returns pass or a short defect list. The parent either accepts or opens a repair implementer.
