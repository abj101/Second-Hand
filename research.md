# Research distill — GameSense (ACL 2025)

Source: [GameSense (ACL 2025)](https://aclanthology.org/2025.acl-long.643/)  
Lu, He, Zhang, Guo, Zang. *Cultivating Game Sense for Yourself: Making VLMs Gaming Experts.* ACL 2025 (Long).  
https://arxiv.org/pdf/2503.21263 · Code: https://github.com/ipsss2/GameSense  
Indexed: PDF is **not** in Codegraph. This file is the working distill. Implementation plan: [`writeups/08-gsm.md`](writeups/08-gsm.md).

---

## One-line

Pause-and-plan VLMs (Cradle) can click menus. They **cannot** fight. GameSense keeps the VLM as a **slow planner/developer** and ships **fast execution modules** (GSMs) for shoot/combat/reflex. Same GPU class as us: **Qwen-VL on a 4060**.

**Avatar take (locked in `Reference.md`, plan in `writeups/08-gsm.md`):** Phase 1 stole the planner + constructor + feedback loop and kept DINO/RL out. **Phase 2 puts them in.** The VLM stays the backbone (See, 3-way reflection, memory, task/action plan, GSM authoring). Rule GSMs use Grounding DINO; RL GSMs use a shared DoubleDQN scaffold the VLM parameterizes. We do **not** copy teleports, game APIs, `eval()`, or unbounded spawn. We **do** add retrieval, sandbox, cost logs, mixed-pacing fixtures, and a pixels-vs-extracted ablation they never ran.

---

## Core idea

Standard VLM game agents pause every action, screenshot, reason, act, re-pause. That fails on fast reactions (FPS) and dynamic adaptation (action combat), and only works in games that pause on demand.

GameSense’s fix: stop using the VLM as a per-action controller. The VLM **builds and refines** small specialized modules (GSMs) that run autonomously, in real time, with no pause and no per-tick VLM call. A high-level VLM stays vision-only for scene analysis, reflection, and planning; it **delegates** when a reactive task appears.

---

## Their setup (map to us)

| Them | Us |
|---|---|
| No API, screen → keys/mouse | Same (`OsController`) |
| **No pause** as a goal | Phase 1 paused on purpose. Phase 2 unpaused Tetris is the first GSM lab |
| Qwen 2.5-VL, Windows, **RTX 4060** | Same card; eyes locked `qwen3-vl:4b-instruct-q4_K_M` |
| Games: Black Myth: Wukong, DOOM, Flappy Bird | 2048, Tetris, Platinum, StS, ER stretch. Tetris-rt ≈ their Flappy |
| Baseline: Cradle ± pause | Keep citing Cradle; our L2-realtime is the in-house Cradle-no-stop |
| Metric: task success + explore-to-death | Plus QA notes, $, GSM authoring cost, retrieve/merge |
| Rules + full action table at init | Named tables we already have; GSM **kinds** also fixed at init |
| GSM types frozen per game | Same lesson. Retrieval is how we retry autonomy without their 12-module blowup |

---

## Architecture

| Component | Role |
|---|---|
| High-level VLM agent | Vision-only. Scene analysis, 3-way reflection (last task / rolling 10-task summary / action-design critique), episodic + procedural (RAG) memory, task & action planning |
| Rule-based GSM | Well-defined reactive tasks (shooting, mob fights). Grounding DINO (open-vocab: text prompt in, boxes out). VLM tunes the **label list** from misses |
| RL-based GSM | Adaptation (boss, Flappy). VLM defines state space (they used OpenCV HP/status), action space, reward; trains on a shared DoubleDQN; VLM rewrites reward against curves (“train-analyze-optimize”) |

GSM **kinds** are fixed per game at initialization, not invented online. Optimization capped at **3** iterations (more degraded from accumulated bad cases in the VLM’s own analysis).

**High-level VLM, every cycle:**

1. **See** — screenshot → text (UI vs gameplay; camera; enemies in a fixed phrase)
2. **Reflect (3 parallel)** — last task; last **10** as a narrative; action-code quality (timing, key, duration)
3. **Memory** — episodic + procedural RAG
4. **Task plan** — one goal, success criteria, location
5. **Action plan** — names from a **predefined table**, **≤ 5**, no “wait/observe”
6. **Construct** — VLM-executed = key/duration list; GSM-executed = `invoke(module)`

Then either fire the key sequence **or** hand off to a GSM until an end condition.

---

## Numbers that matter

Single-task, n=20. Non-realtime (UI, unstick, interact): Cradle ≈ GameSense, **~50–100%**.  
Combat/shoot: Cradle **0–25%** (0% if you take pause away). GameSense **65–95%** trash, **60%** Wukong boss.

| Task | Cradle | Cradle (no stop) | GameSense |
|---|---|---|---|
| Boss Battle (Wukong) | 0% | 0% | **60%** |
| Harder Mob Battle (Wukong) | 10% | 0% | **70%** |
| Harder Mob Shot (DOOM) | 5% | 0% | **65%** |
| Rule-based GSM only, no RL (Boss) | — | — | 10% success, 34.6% avg boss HP left (Cradle: 90%+ HP left, 0% success) |

Full flow, n=20, score until death:

- Wukong explore: GS avg **4.5** / max 6 vs Cradle near-fail
- DOOM: GS avg **3.5** / max 5
- Flappy pipes: GS avg **28.3** / max 35; Cradle cannot play (no pause)

Ablations:

- Rule-only boss still clears Cradle by a wide margin. The **handoff off the VLM** is the leap, not only RL.
- GSM opt iterations: mobs peak at 2; boss still climbing at 3; Flappy saturates at 1.
- **Unfixed GSMs:** VLM spawned **12** duplicate “fight this mob” modules, avg **0.17** opts vs **2** fixed modules with 3 opts. Autonomy **forced pauses** and killed reuse.

Wukong does not pause in combat; Cradle retries pause **5 times** then gives up — also a compatibility warning.

---

## Strengths

- Large effect size on reactive tasks, not a marginal win
- Architecture (delegate out of the per-tick loop) drives most of the gain — rule-only already beats Cradle
- Reusable: one module handles every instance of a task type for the rest of the session
- Self-correcting via execution feedback (curves, detection misses), not static hand-tuning

---

## Limitations (theirs)

1. **Not vision-only end-to-end.** High-level agent is pixels; GSMs run on DINO boxes, OpenCV HP, numeric RL state. That is extracted perception, not a game API — but it is also not “the VLM played.”
2. **Fixed module set is load-bearing.** Autonomous design produced redundant modules and reintroduced pausing. A human still picks reactive categories per game.
3. **RL GSMs work and are hard to generalize.** Stable S/A/R is non-trivial even with shared scaffolding.
4. **VLM-authored code isn’t self-verified.** Documented case: VLM introduced a logic bug while “optimizing” a reward function.
5. **60–70% is far from reliable.**
6. **No cost/latency for module development.** One VLM backbone; no ablation of whether a stronger brain writes better GSMs.
7. **Privileged training.** In-game teleports to reset Wukong for RL. Flappy (click restart) is the cleaner case — closer to our Tetris `restart`.
8. **Huge prior** in prompts + mapping table. Opposite of pixels-only discovery.
9. **Parse-via-eval** on model text is a footgun.
10. **8 GB + ResNet50 + DINO + VLM** is tight; they don’t report VRAM or $.

---

## Techniques we already copied (Phase 1)

- Named action table → keys (L1)
- Structured JSON (their `literal_eval` equivalent)
- Three-level failure analysis (L4)
- Last-10 + last-task (L2); procedural stats
- Screenshot cadence: before/after discrete; ~2s for holds
- UI vs gameplay as first classification
- Constructor **not** every turn; merge-not-spawn; cap 3 rewrites

---

## What Phase 2 copies (with guards)

| GameSense piece | How we take it |
|---|---|
| Grounding DINO | Rule GSM detector **on the screenshot**. Tag `state_source=extracted`. CPU fallback if VRAM fights Ollama |
| OpenCV / HUD readouts | Allowed only on the **screenshot**, only on the extracted arm, never from a game API |
| DoubleDQN parent + VLM reward | Shared scaffold; VLM edits **JSON parameters**, not Python source |
| Train-analyze-optimize, cap 3 | Same cap; stop if curves degrade |
| Init-time GSM kinds | Frozen per fixture in `configs/experiment.yaml` |
| Teleports | **Never** |
| `eval()` of model text | **Never** — Pydantic only |
| Autospawn new kinds | **Never**. Retrieval may reuse/tune an existing kind |

---

## Opportunities we are actually running

1. **Vision-only reactive layer.** Same GSM runtime, `state_source=pixels` (CNN / policy on JPEG, no DINO, no HUD numbers). GameSense never measured this. Capture crop / pixel budget / change detection are the infrastructure.
2. **Mixed pacing.** Platinum (turn battles + slow overworld), 2048, StS — GSM registry empty. Does the paradigm still make sense when most of the game should stay on the planner?
3. **Retrieval before authoring.** Embed task text vs GSM descriptions. Their autonomy ablation failed because this check was expensive/absent.
4. **Sandbox.** Synthetic transitions for rewards; known-good frames for detection. Catch the documented reward bug before live input.
5. **Cost.** Authoring $ and ms vs inference saved by not calling the VLM every tick. Natural local-vs-cloud ablation on **GSM quality**, not just See accuracy.

---

## Code & prompt structure (paraphrased — do not paste Appendix B/D)

**Reward template (RL GSMs):** previous state, next state, action index, `done`, action history, a table of action→state-change, timing. Episode-end: tiered bonus from how much a progress scalar moved. Mid-episode: linear progress + self-HP/death term. Action-specific shaping (e.g. clean dodge). Combo helper over a trailing window (reward useful patterns, penalize spam). The VLM edits **thresholds, magnitudes, window size** from logs — e.g. dodge-spam → lower dodge reward, shorten the overuse window 15→10.

**Prompt chain (six stages):**

1. Environment analysis — UI vs gameplay; UI text; gameplay camera / enemy line / summary, fixed format
2. Previous-task reflection — success + evidence
3. Historical summary — last 10 tasks
4. Action-design reflection — timing / mapping / sequencing (or GSM label/reward critique)
5. Task planning — goal, success criteria, location
6. Action planning → construction — names from a table, then keys; GSM path is `invoke(name)`

---

## Cite / don’t overclaim

Use GameSense as: *pause-VLM fails combat; module-handoff works; free-form module spawn does not; Qwen-VL+4060 is a known planner; rule-only already beats Cradle; RL is the part that works well and generalizes poorly.*

Do not use it as: *we reproduced 60% Wukong, therefore Tetris is solved.* Do not say the VLM “played” if DINO/DQN chose the keys. Tag `state_source`. Report authoring cost.
