# Phase 2 — Game Sense Modules (plan)

**Status:** G1 is live: `detector_family=grounding-dino` when DINO loads, else unavailable — not pixel-blob pretending to be extracted. 3-way reflection and procedural RAG are wired. G2/G5 train/ablation scripts use an in-memory env, not a live window. Mixed pacing: 2048 registry is empty.

Paper distill: [`research.md`](../research.md). Running thesis: [`Reference.md`](../Reference.md). Scoring: [`eval/rubrics/gsm.md`](../eval/rubrics/gsm.md).

---

## Why this shift

Phase 1 proved the Cradle-class loop on games that wait: screenshot → See JSON → named actions → SendInput, plus memory, a bounded constructor, and L4 reflection. That loop **cannot** play anything that needs a reaction faster than a VLM call. GameSense (ACL 2025) is the evidence: pause-and-plan is ~0% on bosses and FPS shots; handing the same tasks to a specialized module that runs without a per-tick VLM call is 60–70%.

The architecture we steal: **the VLM stops being the joystick.** It becomes a slow planner and a developer of small execution modules. Those modules (GSMs) own reactive work until an end condition, then return.

What we do **not** steal blindly: teleports, game APIs, `eval()` of model text, unbounded module spawn, or pretending DINO boxes are “vision-only magic.” We put DINO and RL **in**, and we **measure** the vision-only vs extracted-state split GameSense never ran.

---

## What we keep (do not rebuild)

| Piece | Where | Role in Phase 2 |
|---|---|---|
| OS harness | `avatar/harness` | Screenshot in, SendInput / vgamepad out. GSMs emit the same primitives. |
| Single VLM planner | `AgentCycle` + `make_eyes` | Phase 2 default: one VLM for See and plan. Dual-brain is legacy `--dual-brain`. |
| Named action tables | `harness/tables.py` | Discrete / menu / turn actions. Planner still uses these when no GSM is needed. |
| Capture profile | crop, 768/q85, settle, scout | Fast GSM loops need cheap frames more than the planner does. |
| Episodic + procedural memory | `avatar/memory` | Last-task, last-10, bans, persisted stats. |
| Bounded constructor | `SkillLibrary`, merge-not-spawn, max 3 rewrites | Same rules apply to GSM specs. Retrieval upgrades `_best_match`. |
| JSON schema, not `eval()` | `avatar/schema.py` | GSM plans, rewards, label lists are Pydantic. |
| Cost / latency logs | `TurnLog` | Extend to GSM **authoring** cost vs inference saved. |
| Backend oracles | 2048 / Tetris JS hooks | Score only. Never drive input. Never become RL state. |
| Locked eyes | `qwen3-vl:4b-instruct-q4_K_M` | Planner backbone on the 4060. Swap only as a measured brain-ablation. |

Phase 1 fixtures stay. **2048, StS, paused Tetris, Platinum battles** are the control: the planner should **not** invoke a GSM. **Unpaused Tetris** is the first GSM lab. Elden Ring remains stretch combat.

---

## Target architecture

```
screenshot
    │
    ▼
High-level VLM (single planner, vision-only)
  1. See          UI vs gameplay, scene, closed enemy/piece line
  2. Reflect ×3   last task / last-10 narrative / action-design
  3. Memory       episodic + procedural RAG + GSM registry
  4. Task plan    one goal, success criteria, location
  5. Action plan  named table  ≤5   OR   invoke_gsm
    │
    ├─ discrete ──► named actions ──► SendInput (today)
    │
    └─ reactive ──► GSM runtime (no VLM per tick)
                      until end / fail / timeout
                      │
                      ├─ rule GSM: Grounding DINO labels → policy
                      └─ RL GSM:   DoubleDQN on VLM-authored S/A/R
                            ▲
                            └── train-analyze-optimize (cap 3)
                                sandbox before enable
```

Two libraries, one planner hook:

- **Skills** (already exist) — short named-action sequences. Menus, swipes, “press A three times.”
- **GSMs** (new) — closed-loop controllers. They read frames (and optionally detector output) and emit primitives at game speed.

The planner’s `ActResponse` already has `invoke_skill`. Add `invoke_gsm`. Executor runs the GSM until `end`, then the cycle resumes See/plan.

---

## Non-negotiables

1. **No game API, DOM, emulator RAM, or teleports** to anything that chooses keys. Oracles score offline.
2. **High-level VLM is pixels-only.** Same as Phase 1.
3. **GSM types are fixed per game at init.** Do not let the VLM invent a new module class mid-session (GameSense 4.5.3). It may **tune** labels / rewards / thresholds inside a registered type.
4. **Merge / retrieve, don’t spawn.** Before authoring, embed the task text and compare to existing GSM descriptions. Cap **3** optimize passes. Soft cap on live modules per fixture.
5. **Sandbox before enable.** New or rewritten reward / detect logic must pass synthetic tests (or known-good frames) before it can send input.
6. **Tag the state source.** Every GSM run is `extracted` (DINO boxes ± screenshot HUD) or `pixels` (policy/CNN on the JPEG). Both are allowed; they are different experiment arms, not a sneaky mix.
7. **VRAM is sequential.** Do not try to co-resident Qwen3-VL 4B + DINO + DQN train on 8 GB. Swap: PLAN → DETECT → TRAIN.
8. **JSON / Pydantic only.** No `ast.literal_eval` on model output.

---

## How we differ from GameSense (the actual bets)

| GameSense | Avatar |
|---|---|
| Reactive layer uses DINO + OpenCV HP as numeric RL state | Same **extracted** arm, plus a **pixels** arm nobody published |
| Three realtime games only | Mixed pacing: 2048 / StS / Platinum (GSM off) vs Tetris-rt (GSM on) |
| Autonomous GSM spawn failed; they froze the list | Frozen list **plus** retrieval so we can retry autonomy without duplicate-spawn |
| VLM-authored reward bugs shipped to training | Sandbox on synthetic transitions first |
| No $ / latency for module development | `gsm_author_ms`, `gsm_author_usd`, `gsm_ticks`, `vlm_calls_avoided` |
| One VLM backbone | Locked 4B local; optional cheap-cloud brain ablation on **authoring quality** |
| Teleports to reset Wukong for RL | Restart via the game’s own UI (`restart` named action / click). Tetris `KeyR` already exists |
| Per-game forks | Same `OsController`; GSM is data + a shared runtime, not a new harness |

The headline we can actually own: **does a GameSense-style handoff work when the reactive layer is also restricted to pixels, and does it still make sense on games that mostly wait?**

---

## Code changes (by package)

### `avatar/schema.py`

- `HarnessLevel`: add `"GSM"`.
- `ActResponse`: add `invoke_gsm: str | None = None`. Planner sets this **or** `actions` / `invoke_skill`, not all three.
- `GsmKind = Literal["rule", "rl"]`.
- `GsmStateSource = Literal["extracted", "pixels"]`.
- `GsmSpec`: name, kind, fixture, recognize, end, labels, state_space, action_space, reward_ref, rewrites, enabled, embedding, state_source.
- `GsmTickLog` / extend `TurnLog.result`: `gsm_name`, `gsm_ticks`, `dino_labels`, `author_ms`, `author_usd`, `state_source`.

### `avatar/gsm/` (new)

```
avatar/gsm/
  __init__.py
  spec.py          # GsmSpec load/save; registry on disk under data/gsm/
  runtime.py       # GsmRuntime.step(frame) -> (primitives | done)
  registry.py      # retrieve (embed recognize), merge-not-spawn, rewrite cap 3
  sandbox.py       # reward: fake (s, s', a, done); rule: labeled frames
  cost.py          # authoring vs saved VLM calls
  rule/
    detector.py    # Grounding DINO wrapper; CPU/GPU; text prompts → boxes
    policy.py      # boxes + labels → named actions / mouse point-and-click
  rl/
    env.py         # gymnasium env: mss frame → action table → SendInput
    dqn.py         # shared DoubleDQN (CNN on ROI first; LSTM later if needed)
    reward.py      # load VLM JSON reward; never exec raw Python from the model
    loop.py        # train → dump curves → VLM rewrite reward → sandbox → train
```

**Detector choice (8 GB):** start with a small open-vocab detector (Grounding DINO-tiny / YOLO-World-s). If CUDA OOM, run detector on CPU at Tetris rates (1–10 Hz is enough). Do not load it in the same process as Ollama if VRAM fights.

**Reward functions:** the paper’s template (prev/next state, action index, done, action history, timing, combo window) becomes a **JSON parameter block** the VLM edits (tier thresholds, magnitudes, window sizes). Python code is ours; the VLM does not rewrite the file. That kills the documented “logic bug while optimizing” class.

### `avatar/constructor/`

- Keep `Skill` for sequences.
- `SkillLibrary._best_match` today is string-contains. GSM registry uses embedding similarity (hashing / sentence-transformers small / brain-model embed — pick one that fits 8 GB, even CPU).
- `from_traces.py` stays for discrete skills. GSM authoring is a **separate** job: VLM sees a task description + failures, retrieves or fills a `GsmSpec` of a **pre-registered kind**.

### `avatar/harness/cycle.py` + `executor.py`

- If `invoke_gsm` and the spec is `enabled`, skip per-tick See/plan. Capture at GSM cadence (`force_every` / settle already exist). Log a compact turn when the GSM returns.
- Executor grows `run_gsm(spec) -> CycleResult` alongside `execute_plan`.
- Focus-lost and `release_all` still apply. A GSM cannot type into another window.

### `avatar/prompts/library.py`

Split the current one-shot See/Act/Reflect into GameSense’s six **parseable JSON** stages (paraphrased; do not paste the paper appendix):

1. **Environment analysis** — UI vs gameplay; UI text; gameplay: camera, enemy/piece presence in a **closed template**, scene summary.
2. **Previous-task reflection** — success/fail + screenshot/state evidence.
3. **Historical summary** — last 10 tasks as a short narrative (we already store last-10; this is the prompt that condenses it).
4. **Action-design reflection** — timing, key mapping, sequencing; separate from task verdict. For GSMs: label-list / reward critique, not key-tap quality.
5. **Task planning** — `goal`, `success_criteria`, `screen_position`.
6. **Action planning** — names from the table, max 5, **or** `invoke_gsm` from the registry. Empty `actions` when invoking.

Wire 2–4 as optional L2 cost knobs (`reflect_every`). Default: last-task every turn (cheap), last-10 + action-design every N turns (we already condense).

### `avatar/eval/` + `scripts/`

- `scripts/run_gsm.py` — Tetris realtime: planner until `place_piece` / `survive`, then GSM.
- `scripts/run_gsm_train.py` — RL train-analyze-optimize, cap 3, sandbox gate.
- `scripts/smoke_dino.py` — one screenshot, print boxes, no input.
- Extend `harness_lift_table` with GSM vs L2-realtime vs pause-and-plan.

### `configs/experiment.yaml`

Add a `gsm:` block (fixture → registered kinds, detector model, `state_source`, `max_opt_iters: 3`, `sandbox: true`, `retrieve_threshold`). Do not enable GSMs on 2048/StS/Platinum by default.

### Tests

- Registry: retrieve-merge, rewrite cap, reject unknown kind.
- Sandbox: a deliberately broken reward (always-dodge) fails the synthetic check.
- Executor: `invoke_gsm` on a stub GSM that taps `hard_drop` once then `done`.
- Cycle: while GSM runs, eyes/brain are **not** called (assert call counts).

---

## VRAM / process schedule (4060 8 GB)

| Mode | Loaded | Unloaded | Used for |
|---|---|---|---|
| **PLAN** | Ollama eyes 4B + brain 4B (`OLLAMA_MAX_LOADED_MODELS=2`) | DINO, DQN | See / reflect / plan / author JSON |
| **DETECT** | Grounding DINO (GPU or CPU) | Ollama weights if GPU DINO | Rule GSM inner loop |
| **TRAIN** | DoubleDQN + replay | Ollama, DINO | RL GSM |
| **PLAY-RL** | Policy net only | Ollama | Deployed RL GSM |

Authoring is offline-ish (between episodes), same as today’s constructor. The inner loop must not wait on Qwen.

---

## Implementation stages

Do these in order. Each stage has a stop condition. Do not start RL until the handoff is real.

### G0 — Handoff (no DINO, no RL)

**Build:** `GsmSpec` + stub runtime + `invoke_gsm` in schema/executor/cycle + JSONL fields.

**Stub GSM (`tetris.place_piece`):** read nothing; emit a fixed short sequence (e.g. `hard_drop`) or a scripted shift+drop; return `done`.

**Prove:** unpaused Tetris, planner may invoke the stub; during the stub, `eyes_ms`/`brain_ms` are null; input still hits the game window.

**Stop when:** one live run + unit tests. No detector yet.

### G1 — Rule GSM + open-vocab detector

**Build:** `gsm/rule/detector.py` + policy that maps boxes to Tetris named actions (shift toward a column, rotate, drop). VLM edits the **label list** only (JSON), after misses.

**Fixture:** unpaused Tetris. Labels start as a human-fixed set (`I tetromino`, `O tetromino`, …) — GameSense init-time types.

**Baseline:** existing `run_tetris_realtime.py` (VLM every piece / every settle). Same gravity, n=20.

**Stop when:** live `detector_family` is `grounding-dino` (or explicitly unavailable), not pixel-blob claiming extracted; rule GSM plays **without** a VLM tick; we log `vlm_calls_avoided`. Success rate can still be low.

### G2 — RL GSM scaffold

**Build:** gymnasium wrapper around `OsController` + Tetris window. Action space = Tetris table (or a subset: left/right/rotate/drop). State = ROI JPEG → small CNN (`pixels`) **or** DINO summary vector (`extracted`). Shared DoubleDQN. Reward JSON + our interpreter. Train-analyze-optimize, max 3, sandbox first.

**Reset:** `restart` named action, not a cheat.

**Stop when:** one train-analyze-optimize loop completes; curves land under `runs/`; a rewritten reward is sandboxed; policy can be invoked as a GSM.

### G3 — Retrieval, sandbox, cost (the GameSense fixes)

**Build:** embed `recognize` / task text; skip spawn if cosine > threshold. Sandbox pack for Tetris (frames with known piece, synthetic HP-like scalars if we add HUD later). Cost: authoring $ and ms vs later VLM calls skipped.

**Stop when:** a duplicate task description merges instead of spawning; a bad reward is rejected; the writeup can quote authoring cost.

### G4 — Mixed pacing (Platinum / 2048 / StS)

**Build:** registry for those fixtures is **empty**. Planner has no GSM to invoke. Same L2 loop as Phase 1.

**Question:** does the GameSense paradigm still make sense when most of the game should never leave the planner? Report invoke rate (expect ~0) and that we did not regress L2 2048.

**Optional later:** one Platinum overworld rule GSM (`walk_until_menu`) — only if G1 Tetris is clearly better than L2-realtime.

### G5 — Vision-only ablation (the paper-shaped claim)

Same RL task, two `state_source` arms: `extracted` vs `pixels`. Same action space, same rewrite cap, same n. This is the gap GameSense left open. Do not run this until G2 is stable; it is an experiment, not a rewrite of the runtime.

---

## Prompt / reward shape (ours, not a paper dump)

**Reward JSON (RL GSM)** — VLM fills numbers, not code:

- Episode-end: tiered bonus from a progress scalar (lines cleared, boss-HP proxy, pipes, …).
- Step: linear progress term + self-damage / death penalty.
- Action shaping: small bonus for a “clean” action; combo helper over a window; penalty for overused actions (dodge-spam analog: over-rotate, over-hold).
- Optimizer edits **thresholds, magnitudes, window size** from training logs. Cap 3.

**Planner JSON** — extend `ActResponse`; keep `literal` parsing via Pydantic like today.

---

## Metrics

Tag every run: `harness_level`, `fixture`, `paused`, `gsm` on/off, `gsm_kind`, `state_source`, `opt_iter`, `prompt_mode`.

| Metric | Notes |
|---|---|
| Task success (n=20) | Same micro-task language as Phase 1 |
| Lines / survival (Tetris-rt) | Descriptive; compare pause-plan vs L2-rt vs rule-GSM vs RL-GSM |
| `vlm_calls_per_episode` | Should collapse when GSM is on |
| `gsm_author_ms` / `gsm_author_usd` | Development cost GameSense omitted |
| Retrieve merge rate / duplicate spawn | Fix for 4.5.3 |
| Sandbox reject rate | Guardrail working |
| Opt-iter curve | Expect peak ≤ 3; more is a bug if it degrades |

QA notes stay L4. Playing better is the Phase 2 headline; “would a tester file this?” remains a column, not the only column.

---

## Success bar (honest)

- **G0** is a wiring win. Ship it even if the stub plays worse than L2.
- **G1** beats L2-realtime on Tetris **latency** (VLM calls) even if win rate is similar. If it also clears more lines, that is the GameSense “handoff, not RL” result on our fixture.
- **G2** is a success if opt-iter 1→3 does not **degrade** and sandbox catches at least one bad rewrite in tests. 60% “boss” numbers are Wukong’s, not Tetris’s — do not import them.
- **G5** is the writeup claim if `pixels` is within shouting distance of `extracted`, or if we can say clearly that it is not.

---

## Risks

| Risk | Mitigation |
|---|---|
| 8 GB cannot hold DINO + VLM | Sequential modes; CPU DINO; never train with Ollama loaded |
| VLM writes a broken reward | JSON params + sandbox; cap 3 |
| Planner invokes GSM on 2048 | Empty registry; tests that invoke_gsm is ignored when unset |
| Detector on Tetris is a toy | Still the right first lab; commercial ACT is stretch |
| YOLO-World ≠ Grounding DINO | Tag detector family in the run; do not overclaim “we reproduced DINO” |
| RL on a live window is slow / noisy | Pause gravity during **train** if needed; play eval is unpaused. Tag `paused` |

---

## Out of scope until G2 is real

- Elden Ring combat GSM
- Flappy Bird (not in the fixture set; Tetris-rt is the reflex analog)
- Per-game Python forks of the harness
- Prestige cloud VLMs as the default author
- Feeding `GameManager` / ROM state into DQN
- Autonomous invention of new GSM **kinds**
