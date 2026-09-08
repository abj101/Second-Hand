# Plan: LLM1 schema, anonymous targeting, and reasoning-route fixes

## Purpose

Address the three failures observed in run `20260905T003242Z_sts2_collect`:

1. LLM1's first response repeatedly fails schema validation.
2. A valid Strike cannot bind to the anonymous enemy exposed as `mark_9`.
3. The router sends an unidentified enemy directly to ACT instead of discovery.

This plan stops at the boundary of enemy discovery. The full hover/capture/OCR system is specified separately in `11-discovery-system-plan.md`.

## Evidence from the run

- Every first LLM1 attempt omitted required fields. Character and enemy rows lacked `inspectable` and sometimes `confidence`; hand-card rows lacked `role`, `confidence`, and `playable`.
- The retry succeeded on every observed turn: runtime logs reported `schema_valid=True`.
- The accepted reasoner output used `choice="Strike", target="mark_9"`.
- `mark_9` was presented as if it were a legal target label, but action synthesis resolves labels or stable object IDs, not strings that merely resemble mark IDs.
- The target was dropped, producing `card requires target enemy`.
- Later steering allowed the enemy/player mark to become the primary mark, producing `card mark 14 is enemy, not a typed hand card`.
- The router reported `ACT (confirmed-action-surface)` with no epistemic blockers because a hostile anonymous bbox is currently treated as sufficient for action.
- The player and duplicate enemy geometry appeared in the target inventory, making automatic target repair unsafe.

## Invariants

- Never execute input from a schema-invalid LLM1 response.
- Never make a first-attempt schema warning look like a terminal perception failure when a retry succeeds.
- Labels, stable object IDs, and frame-local mark IDs are distinct types. Do not serialize one as another.
- A card transaction must contain one typed hand-card instance and, when required, one typed enemy instance from the same frame.
- The player can never appear in the enemy target inventory.
- Steering may refine coordinates for an already validated semantic action; it may not change card into enemy, enemy into card, hover into click, or one object role into another.
- Unidentified entities must be routed according to an explicit policy rather than accidentally disappearing from the blocker set.

## Phase 1: make LLM1 schema compliance deterministic

### 1.1 Define one canonical response schema

- Move the required perception shape into a single schema object used by prompts, validation, tests, logging, and any gateway structured-output support.
- Require the following on the initial response, not only in the retry hint:
  - `legal_actions`, `unresolved`, `hand_cards`, `characters`, and `enemies` as lists.
  - Every hand card: `id`, `role="card"`, `confidence`, `playable`, `bbox`, `label`, `name`, and `name_source`.
  - Every character/enemy: `role`, `confidence`, `inspectable`, `bbox`, `label`, `name`, and `name_source`.
- Make nullability explicit. For example, unknown names should be represented consistently as either `label=null` with `name_source="unresolved"`, or a designated placeholder. Do not support both conventions throughout the pipeline.
- Keep the response compact enough to fit the configured output budget.

### 1.2 Enforce the schema at generation when supported

- Extend the VLM gateway to pass a native JSON schema/structured-output contract when the backend supports it.
- Retain post-generation validation because native structured output does not prove semantic correctness.
- If the backend lacks schema-constrained generation, place a minimal canonical example immediately beside the field requirements in the initial prompt.

### 1.3 Improve retry behavior and observability

- Compute a compact, exact repair prompt from the actual validation errors instead of always sending the generic “required fields missing” hint.
- Persist each raw attempt separately with:
  - attempt number,
  - validation errors,
  - token budget,
  - latency,
  - whether it was accepted.
- Record final metrics such as `llm1_schema_attempts`, `llm1_first_pass_valid`, and `llm1_final_schema_valid`.
- Do not overwrite `perceive_raw` with a post-grounded representation and call it raw. Store `llm1_raw`, `validated_perception`, and `grounded_scene` separately.
- Revalidate the grounded representation after OCR/entity fusion so fields such as `label="Strike"` cannot remain paired with `name_source="unresolved"`.

### 1.4 Tests and acceptance gate

- Add fixtures reproducing Gemma's abbreviated first response and corrected retry.
- Verify an invalid first response causes no reasoning or input before a valid retry exists.
- Verify post-grounding output remains schema-valid.
- Verify logs distinguish first-attempt failure from final failure.
- Gate: at least 95% first-pass schema validity over 100 representative combat frames; 100% final validity or a clean no-action fallback.

## Phase 2: repair the card-to-target identity contract

### 2.1 Use stable IDs in the reasoning contract

- Give every typed card and enemy a stable object ID plus a human-readable label.
- Emit target instances in the prompt, for example:
  - `enemy_1=unidentified_enemy`
  - `card_1=Strike`
- Require `choice_id` whenever duplicate card labels exist.
- Require `target_id` for targeted cards. Treat `target` as an audit label, not the executable identity.
- Do not expose `mark_9` under `Allowed targets` unless the action schema explicitly has a numeric `target_mark` field bound to the current frame.

### 2.2 Resolve IDs to the current frame exactly once

- Resolve `choice_id` and `target_id` through `ScreenObjectStore` after reasoning and immediately before dispatch.
- Require both objects to share the current `run_id` and `step_id`.
- After clicking the card and taking the fresh frame, rebind only the target by stable tracked identity and bbox. Never reuse its old mark number.
- If rebinding is ambiguous or the target disappeared, cancel the transaction and request a new observation.

### 2.3 Correct target inventory construction

- Exclude player/ally rows before they enter `ScreenObjectStore.enemies()`, `targeting_options`, the game-state reconciler, and reason prompts.
- Collapse duplicate enemy observations by bbox IoU and center distance before assigning stable IDs.
- Separate these concepts:
  - HP/text anchor,
  - sprite/entity bbox,
  - executable target point.
- Never turn an HP label or upper-screen OCR anchor into a second enemy.
- Require an enemy role plus board geometry; position alone must not convert a generic icon into an enemy.

### 2.4 Make steering role-preserving

- Attach the intended source role and target role to the action before any steering.
- Reject any steering result that changes:
  - primary object ID,
  - primary role,
  - target object ID,
  - target role,
  - hover/click mode.
- Remove generic `fill_from_aim` recovery from typed combat transactions. Recovery should return a fresh semantic proposal, not a different clickable mark.
- Ensure verification retries replay the validated semantic transaction rather than clicking the next available card or target.

### 2.5 Tests and acceptance gate

- Reproduce `choice="Strike", target="mark_9"` and assert it is rejected at prompt validation rather than silently losing the target.
- Verify `choice_id="card_1", target_id="enemy_1"` produces two released clicks: card, then freshly rebound enemy.
- Verify duplicate Strikes require `choice_id`.
- Verify player and HP anchors never appear in target inventory.
- Verify no steering path can produce “enemy mark used as card.”
- Gate: 100 consecutive targeted-card transactions with zero role swaps, zero stale-mark dispatches, and zero targetless attack clicks.

## Phase 3: make routing policy explicit

### 3.1 Separate targetability from identification completeness

- Replace the current rule “anonymous hostile bbox is not a blocker” with two independent facts:
  - `targetable`: enough evidence exists to click the entity safely.
  - `identified`: the entity has a verified name/identity.
- Add a policy setting for first encounter:
  - `identify_before_act=true` for unknown-first collection and evaluation.
  - An optional later fast path may allow anonymous attacks only after the targeting-ID contract is proven reliable.

### 3.2 Route first-seen unknown enemies to Explorer

- When an inspectable hostile entity is unidentified, route to Explorer before ACT.
- Keep player/ally entities out of this decision.
- After Explorer returns verified identity evidence, rebuild scene, object inventory, game state, and reason prompt before ACT.
- If identity cannot be recovered after bounded attempts, mark the entity `identity_unresolved` and apply an explicit fallback policy; do not silently declare the surface confirmed.

### 3.3 Make route decisions auditable

- Log every entity with `targetable`, `inspectable`, `identified`, blocker status, and the rule that determined it.
- Log `route_before_discovery` and `route_after_discovery`.
- Assert that `ACT (confirmed-action-surface)` cannot coexist with an unidentified inspectable enemy when `identify_before_act=true`.

### 3.4 Tests and acceptance gate

- First-seen unknown enemy with bbox: Explorer.
- Known enemy restored from verified current evidence: ACT.
- Player with unresolved model name: never Explorer and never target.
- Unknown HUD icon: neither enemy discovery nor attack target.
- Gate: in held-out combats, every first-seen hostile follows `observe -> inspect -> identify/unresolved -> reason`; no ACT call happens before that sequence completes.

## Recommended implementation order

1. Canonical schema and attempt-level logging.
2. Player exclusion and enemy deduplication.
3. Stable `choice_id`/`target_id` transaction contract.
4. Role-preserving steering and retry removal.
5. Explicit `identify_before_act` routing.
6. Integrate the discovery engine from `11-discovery-system-plan.md`.

## Definition of done

- First-attempt schema failures are rare and final failures are explicit no-action turns.
- Raw, validated, and grounded perception are separately inspectable.
- The reasoner never receives a pseudo-label such as `mark_9` as an executable target.
- A Strike binds to one card instance and one non-player enemy instance from the same frame.
- First-seen unidentified enemies enter Explorer under unknown-first policy.
- The original run's failure signatures are all covered by regression tests.
