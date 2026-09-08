# Goal: Vision-Only Turn-Based Game Harness

## North-star outcome

Build and verify a harness that lets agents learn to play turn-based games using screenshots as input and mouse/keyboard harness tools as output—without game APIs or game-code integration. The system must retain sensor-grounded memories, learn patterns and strategies from observed behavior, and improve policies and tool capabilities through controlled, evaluated training and approval-gated changes.

## Current system and active driver

The active implementation is the `loop/` package on the `feat/collect-loop` branch. Its initial game fixture is Slay the Spire 2 (`sts2`); the current branch driver is not the Chapel/Pad/Elden path.

The verified architecture is:

```text
screenshot → letterbox crop → OCR/GUI aim marks → VLM action proposal
  → bind/safety/stale check → mouse or key chord → HUD OCR/reward
  → episode log + sensor memory → SFT/DPO data → offline evaluation
```

`overlay` is the default architecture. `two_stage` uses a raw-image perception model plus an MCP text reasoner, a grounded frame-object graph, and entity tracking for unknown sprites.

## What already exists

- Vision-and-tool-only STS2 collection: screenshot capture, 16:9 geometry checks, OCR, GUI detection, overlay coordinates, mouse/key chords, stale-observation protection, turn gate, and panic stop.
- Screen-grounded rewards: host OCR computes HUD facts and `turn_reward`; VLM output cannot mint reward values.
- Traceable artifacts: step and episode JSONL logs, observation/action frames, SFT/DPO exports, `prompt_id`, and architecture attribution.
- Sensor memory: `FactJournal`, `MarkLexicon`, `GroundedStore`, `Notebook`, working-memory blocked hints, perception cache, entity tracking, overlay state, and two-stage object storage.
- Training path: SFT and DPO/ORPO on `unsloth/Qwen3.5-4B`, then controlled merge/GGUF deployment to `qwen3.5:4b-instruct-q4_K_M`.
- Evaluation path: same-`prompt_id` baseline/candidate comparisons using held-out collect runs; frozen-frame-only GRPO is permitted later, while live multi-sample RL is explicitly prohibited.

## Non-negotiable constraints

1. **No game API or game-code dependency.** Game state, actions, labels, and reward evidence must come from rendered screens and harness-mediated input only.
2. **Sensor evidence over invented facts.** OCR/GUI/visual outcomes ground labels, memory, reward, and training examples. Pixel changes alone are not evidence of progress.
3. **Model proposal is the training write path.** Safety rewrites are logged as safety interventions; only `executed_as_proposed` data may become ordinary SFT examples.
4. **Freeze and compare prompt contracts.** Do not mix `prompt_id`, notebook schema, or architectures in exports/evaluations.
5. **Offline/sandbox learning first.** DPO on logged same-frame/state pairs precedes RL. GRPO/GSPO trains only against frozen screenshots and a scorer until a real snapshot-reset environment exists.
6. **Controlled weight updates.** Train the specified Qwen3.5 vision-capable instruct lineage, evaluate candidate versus baseline on comparable held-out runs, and retain rollbackable artifacts. Never substitute Base or Qwen3-VL models.
7. **Controlled tool evolution.** A newly proposed MCP capability requires a narrowly scoped interface, sandboxed tests, trace/audit evidence, regression evaluation, and explicit activation approval. It must not bypass the screenshot→tool boundary.
8. **Generalize through abstractions, not hidden per-game code.** Fixtures may supply window/input/terminal contracts; perception, action binding, memory, reward evidence, learning, and evaluation must remain reusable.

## Evidence required to declare success

### 1. Reliable visual play

- On STS2, the system completes a representative sequence such as tutorial/title transition, map navigation, combat card→target interaction, enemy-turn waiting, and terminal/success recognition from screenshots and input tools alone.
- Logged actions contain a visual before/after trace, tool execution outcome, host-derived metrics, reward, and `executed_as_proposed` status.
- Uncertain, stale, enemy-turn, unsafe, and no-op conditions prevent or correct actions without contaminating training data.

### 2. Durable memory and strategy formation

- Later episodes retrieve sensor-written control/object facts and grounded Known/Hunch/Lesson records relevant to the visible screen.
- Unknown sprites can be discovered through visible inspection/hover behavior, named from screen evidence, and reused by the object library.
- Pattern/strategy claims are traceable to episode evidence, can decay or be invalidated, and do not become prompt lore merely from model assertion.

### 3. Measured learning

- SFT and DPO exports only contain valid, attributable examples under the relevant gates.
- Candidate weights are trained offline, versioned, and compared with a compatible baseline on held-out runs with the same architecture and `prompt_id`.
- Promotion requires evidence of improvement in operational metrics—not training loss alone—and preserves the prior model for rollback.
- Any RL phase is restricted to frozen-frame scoring until snapshot-reset episodes make live group sampling valid.

### 4. Safe MCP capability evolution

- Tool gaps are identified from logged failure modes, then implemented as scoped, sandboxed MCP proposals with tests and versioned capability contracts.
- New tools are not used in live collection until approval and regression evidence show they preserve the visual-only boundary and do not reduce safety or evaluation integrity.

### 5. Cross-game generalization

- At least one additional turn-based fixture demonstrates the same core loop with no game API or game-code integration.
- Shared components handle observation, confidence/act gating, action execution, trace logging, memory, training-data validity, and evaluation; fixture-specific details remain declarative/configurational where possible.
- Cross-game results report the limits of transfer rather than claiming universal performance from one game.

## Near-term focus

1. Establish a reproducible STS2 visual-play baseline with short controlled collection runs, preserving run artifacts and reviewing failure traces before broad runs.
2. Exercise and evaluate `overlay` and `two_stage` separately; never compare or pool mismatched `prompt_id` data.
3. Validate the SFT/DPO export gates and held-out baseline/candidate evaluation before any model promotion.
4. Use collected failure evidence to prioritize missing sensory, memory, or MCP capabilities; keep tool creation approval-gated.
5. Define and run a second turn-based fixture only after the STS2 loop has evidence-backed stability.

## Explicitly out of scope for this branch

- Elden exploration DQN / `train-rl`.
- Live eight-sample GRPO on a mutable Steam process.
- Qwen3-VL or Qwen3.5 Base training substitutions.
- Drag behavior absent screen evidence that click-click is rejected.
- Adding a static UI wiki to the turn prompt in place of curiosity and sensor memory.
