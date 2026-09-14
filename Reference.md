# AI Game-Playing / QA Agent — Project Plan (Running Doc)

Last updated: 2026-08-28

**Doc map:** operator quick start → [`README.md`](README.md). Collect-loop philosophy + STS2 features → [`system.md`](system.md). GameSense distill → [`research.md`](research.md).

## Status: three live pipelines on one harness

| Pipeline | Package | Driver | Planner |
|---|---|---|---|
| **Phase 2 GSM** (default) | `avatar/` | Unpaused Tetris (`invoke_gsm place_piece`); 2048 / StS / Platinum mixed-pacing | `qwen3.5:4b-instruct-q4_K_M` — one VLM, See + plan, `think: false` |
| **Collect loop** (`feat/collect-loop`) | `loop/` | Turn-based **Slay the Spire 2** (`sts2`) | Same tag; SFT/DPO on `unsloth/Qwen3.5-4B`, GGUF back onto instruct tag |
| **Legacy Phase 1** (ablation) | `avatar/` | L0–L4 See/Understand/Act, paused Tetris, dual-brain | `--dual-brain` or historical `qwen3-vl:4b-instruct-q4_K_M` rows |

Cloud toggle: `gemma4:31b-cloud` via `--model`. No second text brain on the default path.

Phase 1 (L0–L4) proved pause-and-plan: named actions, last-10 memory, bounded constructor, L4 reflection. Kept for ablations (`scripts/run_phase1.py`), not the runner default.

G0 handoff is wired. G1 tags live `detector_family` (grounding-dino or unavailable); do not claim extracted from pixel-blob. Collect contract: `pytest tests/loop` on frozen screenshots in `loop/fixtures/sts2_screens/`; live VLM corpus + `--execute` train are the open gates.

---

## 1. Thesis

Four questions, same fixtures, same computer-use harness:

1. **QA:** Can a VLM be a **usable QA agent** if the high-level loop only
   sees the screen and only acts through OS input — the way a human
   tester perceives and controls a game?
2. **Harness lift:** How much does that improve as we add structure
   (raw VLM → controller → memory → constructor → reflection → **GSM
   handoff**) — *not* as we swap to a fancier model?
3. **Learn like a player:** After fumbling in pixels, can the VLM
   **construct reusable skills and GSMs** from traces (pattern → action /
   reaction), then later use a manual the way a human eventually reads
   the box?
4. **Play at game speed:** If we keep the VLM as planner/developer and
   give reactive work to DINO-rule and RL modules, can the same harness
   play games that pause-and-plan cannot — and can the **reactive**
   layer stay screenshot-only (`extracted` vs `pixels`; see §5.2)?

Literature says (1) without (2) is a known fail. GameSense (ACL 2025)
adds: the VLM should be a **planner + developer**, not a per-frame
joystick. Phase 1 stole that loop without DINO/RL. Phase 2 **adds**
Grounding DINO and DoubleDQN, with guards they skipped (retrieval,
sandbox, cost, no teleports, no game APIs).

Capability order is still **see → understand → act → analyze**.
Constructor and GSM authoring sit on traces, **not every turn**. We are
now also asking the agent to **play**, not only to file QA notes.

Secondary variables (change one at a time): game complexity, local vs
cheap cloud, screenshot budget, **pixels-only vs +manual (late)**, paused
vs realtime, **GSM off / rule / rl**, **state_source extracted vs pixels**.

**Out of Phase 1 (done):** multiplayer, personas, L5 crews, teleports,
game APIs to the model, unbounded module spawn. Those stay out.

**In Phase 2:** realtime without pause (Tetris first), pretrained
open-vocab detection on the **screenshot**, RL on a shared scaffold,
GSM retrieve/merge, sandbox, authoring-cost logs. See §§5.2, 8, 14.

**Pause-and-decide** remains the Phase 1 measurement. It does not save
Elden Ring combat. Unpaused play is a GSM job, not a bigger VLM.

---

## 1.1 Rules stolen from the literature

1. **Raw VLM play is the baseline we expect to lose.** Don’t make “can it
   finish the game?” the headline.
2. **The harness is the independent variable.** Same model, same games,
   L0 → L1 → L2 → constructor → L4 → GSM (G0–G5).
3. **Generic screen + keys.** No game API. One controller, many games
   (SIMA). Skills and GSMs are *data in the harness*, not a per-game fork.
   Extracted screenshot features (DINO boxes, HUD pixels) are allowed on
   the tagged `extracted` arm only.
4. **Pause vs realtime is a measured split**, not a hidden cheat
   (VideoGameBench).
5. **Score the stack + the learning curve**, not the trophy: micro-skills
   vs long-horizon; attempt # → success; skill **reuse**; QA notes a
   human would file.
6. **VLM is planner + constructor / GSM developer** (GameSense). Play →
   notice a pattern → write a reusable tool → call it next time. Humans
   do this; per-frame reasoning does not. Reactive work is a **GSM**, not
   another VLM tick.
7. **Bound the constructor.** GameSense’s free spawn made 12 duplicate
   modules and forced pauses. Ours: named library, merge-not-spawn /
   **retrieve**, cap **3** rewrites per skill or GSM, constructor runs on
   traces **not every turn**. GSM **kinds** are fixed per game at init.
8. **Manual later, not first.** Early play is pixels-only (pick up the
   pad). Later, add the game’s own description/instructions as a measured
   boost — the human who finally reads the manual.
9. **Handoff is the lift, not the trophy model.** Rule-only GSM already
   beat Cradle on Wukong. Measure VLM-calls-avoided before celebrating RL.
10. **Tag state source.** `extracted` (DINO / screenshot HUD) vs `pixels`
    (policy on JPEG). Never feed DOM, RAM, or a game API into a GSM.
11. **Sandbox VLM-authored logic** before it can SendInput. JSON
    parameters, not `eval()` of model Python.

### 1.2 Collect-loop philosophy (`loop/`, `feat/collect-loop`)

Full feature list and operator commands: [`system.md`](system.md). These rules sit on top of §1.1 for the turn-based collect path:

1. **Curiosity, not a wiki.** One control per step. Thought names the test; outcome writes the fact. Do not paste a UI manual into the system prompt every time a click fails.
2. **Pixels are not progress.** Hover tooltips, pause menus, and parchment flicker change pixels. Training needs HUD or screen change: energy spent, block gained, title→map, tutorial dismissed, Resume from pause.
3. **The model's chord is the write path.** Heuristics that rewrite after the model speaks (`steer_off_danger`, hover→click) are safety, not SFT labels. Export keeps `executed_as_proposed`.
4. **Same model collect-to-train.** Live planner is Ollama `qwen3.5:4b-instruct-q4_K_M`. Weights update on `unsloth/Qwen3.5-4B` (instruct + vision encoder), then GGUF back onto that tag. Never `Qwen3.5-4B-Base`. Never Qwen3-VL for STS2 train.
5. **One live window is not an RL env.** Offline DPO on logged pairs first. Live GRPO only on frozen images + scorer, or after a real snapshot reset exists.
6. **Sensor memory, not cosine thumbs.** The journal keys `kind|heading|prompt` from OCR aim labels and click outcomes. Default arm `no_skill`; `--arm dump_all` is an opt-in cosine ablation.

---

## 2. Constraints

- **Timeframe:** Phase 1 shipped (See → Analyze). Phase 2 is GSM iteration
  on the same repo and harness.
- **Input mode:** The **high-level VLM** never gets DOM or game-state
  APIs. Backend oracles score later, never go to the model. In-session
  memory, **skills**, and **GSM specs** the VLM wrote from screenshots
  (and from GSM logs) are allowed. Phase 2 GSMs may use Grounding DINO
  and/or a CNN **on the screenshot**; they still must not read a game
  API. Manual text is a **late** ablation, not day-one. Tag
  `state_source`.
- **Harness:** One computer-use controller is the product. It should be
  able to play **all** fixtures. Early experiments may bootstrap browser games
  with Playwright; that is scaffolding, not the architecture.
- **Game type:** Single-player. Turn-based (2048, StS 2), hybrid no-twitch
  (Platinum), realtime (Tetris, Elden Ring chapel). Anti-cheat: user's
  call, not a concern. User-owned ROMs only — never vendor Nintendo IP.
- **Compute:** Local RTX 4060 (8GB VRAM) + cheap cloud VLMs. No lock-in
  to Claude / GPT.
- **Output:** Public writeups + GitHub repo + demo video. Screenshots of
  commercial games are fine.

---

## 3. Capability Pipeline (the actual progression)

Tiers are **fixtures**, not gates. Do not wait to "beat" a game before
moving on. Run enough trials to normalize data, then change one variable.

| Stage | Harness level | Question | Start on |
|---|---|---|---|
| **See** | L0 | Can it read the screen? (pixels-only) | 2048 |
| **Understand** | L0 | Goal / sensible next move? | Same shots, still no manual |
| **Act** | L1 then L2 | Named actions → input, then + memory | 2048 → Tetris (paused) → Platinum → StS |
| **Construct** | L3 skills | From traces, write/reuse pattern tools | After L2 has logs (start 2048, then Platinum) |
| **Analyze** | L4 | Fail analysis + QA notes a human would file? | StS and/or Platinum traces |
| **Manual boost** | context | Same loop + official description/instructions | Late Phase 1 ablation, not a new game |
| **Handoff** | G0 GSM | Planner invokes a stub GSM; no VLM tick inside | Unpaused Tetris |
| **Rule GSM** | G1 | DINO (or small open-vocab) + policy, VLM tunes labels | Unpaused Tetris |
| **RL GSM** | G2 | DoubleDQN; VLM JSON reward; train-analyze-optimize ≤3 | Unpaused Tetris |
| **Retrieve / sandbox / $** | G3 | Merge-not-spawn with embeddings; sandbox; authoring cost | After G1 has a spec |
| **Mixed pacing** | G4 | GSM registry empty; planner-only | 2048 / StS / Platinum |
| **Pixels vs extracted** | G5 | Same RL task, two `state_source` arms | After G2 is stable |
| **Collect** | `loop/` | Screenshot → click chord; `turn_reward` from HUD OCR | StS 2 (`sts2`) |
| **Train** | SFT/DPO | `turn_reward > 0` + `executed_as_proposed`; same `prompt_id` | After collect has VLM rows |

Human scores results first. Automate scoring only after the harness exists
and the labels are boring enough to script (DOM/JS oracles on Tiers 1–2
are for that later path, not a prerequisite).

---

## 4. Game Fixtures

Staged by complexity / interface fidelity. Same protocol across fixtures
once a pipeline stage is being measured. The old 2×2 is still true; Platinum
adds a **hybrid** row the 2×2 was hiding:

| | Browser (oracle *possible*, backend-only) | Commercial (vision-only, no fallback) |
|---|---|---|
| **Turn-based** | Tier 1: 2048 | Tier 4: Slay the Spire 2 |
| **Hybrid (explore + menus + turn combat, no twitch)** | — | Tier 3: Pokémon Platinum |
| **Real-time** | Tier 2: Tetris | Tier 5: Elden Ring (chapel appendix) |

Write-up note: Tier 2 changes complexity *and* time pressure vs Tier 1 —
call that out. Platinum is the long-horizon / memory fixture (SIMA’s
“find then use”) without paying Tetris/ER latency.

### Tier 1 — 2048 (locked)

- **Clone:** [gabrielecirulli/2048](https://github.com/gabrielecirulli/2048)
  (MIT). Live: https://gabrielecirulli.github.io/2048/ (also play2048.co).
  **Vendor a copy** so the page and scoring hooks stay stable.
- **Why:** fully turn-based, 4-action space, clean win/lose. Tiles are DOM
  (`tile-{value}`, `tile-position-{x}-{y}`); `GameManager` grid is hookable
  later as a backend oracle.
- **Early interface:** Playwright screenshot + key input (bootstrap).
- **Later interface:** same game through the computer-use harness
  (capture the window, send keys) so Playwright vs harness can be compared.

### Tier 2 — Tetris (locked)

- **Clone to vendor:** [fkucukkara/tetris](https://github.com/fkucukkara/tetris)
  (MIT, TypeScript, game logic / renderer / input already split). Fork it
  and add: pausable / configurable gravity, an explicit pause-and-decide
  mode, and a backend-only board-state hook for later scoring.
- **Backup if that clone is awkward:** [sen-ltd/tetris](https://github.com/sen-ltd/tetris)
  (vanilla JS, isolated `board.js`, pause + gravity table) — **confirm
  LICENSE before using**. Do not use [chvin/react-tetris](https://github.com/chvin/react-tetris)
  as the fork target (popular, but **no license**).
- **Why:** rotation, stacking, planning, a tick. Pause-and-decide removes
  inference latency as a confound. Unpaused Tetris is the **first GSM lab**
  (G0–G5): pause-and-plan vs L2-realtime vs rule-GSM vs RL-GSM.

### Tier 3 — Pokémon Platinum (locked, NDS emulator)

- **Why it dropped, then came back:** never made this doc. The 2×2 jumped
  from Tetris to StS/ER and ate the JRPG. Restored as the hybrid rung:
  overworld nav + NPC/menus + turn-based battles. Looks like a “game,”
  evaluates like a turn-based one — battles wait, walking is not twitch.
- **What it uniquely tests:** long-horizon (leave town → route → fight →
  next town). L2 memory matters here more than on 2048. Dual screens.
  Mixed pacing (G4): GSM registry stays **empty** unless we later add a
  slow overworld walker — battles wait, so they stay on the planner.
- **Score (don’t need the Elite Four):** micro = walk, talk, open bag,
  pick a move. Long-horizon = reach the next town, win a trainer fight,
  first gym if cheap. Getting *unstuck* is a first-class metric.
- **Interface:** computer-use harness on an emulator window (`mss` +
  keys). Default **melonDS**, backup DeSmuME/RetroArch. Capture the
  stacked dual screen. No Playwright. Emulator pause is optional, not
  required.
- **Legal:** user-owned dump only. Do not commit ROMs, BIOS, or rips.

### Tier 4 — Slay the Spire 2 (locked, Steam)

- **Why:** turn-based commercial game, zero DOM/API, dense UI (card text,
  numbers, intents). Richer QA surface than 2048/Tetris/Platinum combat
  menus — this is where “usable QA agent” can actually be true or false.
- **Interface:** computer-use harness only (`loop/fixtures/sts2.py`). No
  Playwright, no pause-and-decide. Mouse chords with aim overlay; DINO off
  for turn pace.
- **Collect driver:** on `feat/collect-loop`, STS2 is the live collect
  fixture. One VLM chord per step; `turn_reward` from HUD OCR; Notebook +
  journal in the prompt. Operator contract: [`system.md`](system.md).

### Tier 5 — Elden Ring (chapel appendix)

- Chapel collect (pad, explore GSM, Ratchet librarian) is frozen on
  `feat/collect-loop`. Combat bosses stay stretch G2+.
- Score navigation and verified heading (`view_changed`), not survive-K. A
  combat GSM is stretch, same kinds as Wukong, no teleport reset.

**Status:** five fixtures locked. STS2 is the live collect driver on this
branch; Elden chapel remains in-tree as appendix. Anti-cheat ruled out
(user's call). Platinum = emulator + owned ROM, not Steam anti-cheat.

---

## 5. Harness (the product)

The harness is a **generic computer-use controller** (SIMA-style):
screenshots in, keyboard/mouse out, no game API. Same interface for every
fixture. Game knowledge lives in prompts, a **named action table**, a
**skill library**, and **GSM specs** the VLM fills — not per-game control
forks.

```
screenshot → VLM (plan | invoke skill | invoke GSM) → OS input
                 ↑                                      ↑
            traces → constructor                    GSM runtime
                 (offline-ish)                      (no VLM / tick)
```

**The experiment is the ladder**, same games/tasks. Raw VLM is the
baseline we expect to fail (VideoGameBench). GameSense is the proof that
**handoff off the VLM** is what lifts combat, not a bigger model. Our
writeup is that delta **plus** QA findings **plus** (Phase 2) GSM
authoring cost and a pixels-vs-extracted split they never ran.

| Level | What it is | Phase |
|---|---|---|
| **L0 QA** | Screenshot → “what’s happening / what should I do?” Pixels-only. | 1 — See/Understand |
| **L1 controller** | Named actions (`swipe_left`, `confirm`) → keys. JSON, not prose. | 1 — Act |
| **L2 memory** | Last-task + last-10 + outcome feedback + action bans; persisted procedural stats. Legacy: dual-resident eyes + text brain (`--dual-brain`). | 1 — wired |
| **L3 constructor** | From traces, propose/reuse **skills** (pattern + action/reaction). | 1 — after L2 logs |
| **L4 reflect** | 3-level fail analysis (task / plan / execution+timing) + QA notes. Plot attempt #. | 1 — Analyze |
| **Manual boost** | Same stack + game description / how-to-play text. | 1 — late ablation |
| **G0–G5 GSM** | Invoke a real-time module; rule (DINO) then RL (DQN); retrieve; sandbox; mixed pacing; pixels vs extracted. | **2 — in scope** |
| **L5 multi-agent QA** | Player / observer / critic / analyst | No |

**Control path:** Windows OS only. Client-area `mss` capture, `SendInput` keyboard/mouse, optional virtual Xbox 360 pad (`vgamepad` + ViGEmBus). Named actions **and GSMs** compile to primitives. Playwright is L0 See/Understand capture leftover, not Act.

**Pause-and-decide:** realtime fixtures. Isolates brains from lag. One
Tetris realtime row shows the latency kill. Constructor / GSM authoring
must not reintroduce a pause-per-thought by running every frame — same
failure GameSense hit with unfixed GSMs.

Expect UI/menu/interact in the Cradle range (~50–95%). Do not sell that
as beating GameSense. Our gap vs them: **QA notes**, **turn-based /
hybrid fixtures**, **cheap-model sweep**, **manual-as-later-boost**,
**generic skill/GSM library instead of per-game forks**, **retrieval +
sandbox + $**, **pixels reactive arm**.

Closed on both sides: no teleports, no game APIs. Phase 2 **opens**
pretrained detectors and RL, tagged and sandboxed.

---

## 5.1 Constructor — learn like a human (steal GameSense’s loop, fix their spawn)

A human does not reason about every key. They play, notice “when I see
X I do Y,” and that becomes a reflex. GameSense’s GSM is that idea.
Their **unfixed** version failed (12 duplicate fight-modules, 0.17 opts
each, forced pauses). We keep the constructor and **bind it**.

**What a skill is** (JSON in the harness, reusable across turns):

- **Recognize:** a pattern on the screenshot (UI vs gameplay, “merge
  puzzle,” “trainer battle,” “intent: attack,” “stuck on wall”).
- **Do / react:** a small named-action sequence or policy
  (`if recognize(battle) → pick_move`; `if stuck → rotate_camera`).
- **End:** when to stop and return to the planner.
- **Provenance:** which traces taught it; rewrite count (max **3**).

**Loop (not every turn):**

1. Play with L1/L2 (slow, like a new player).
2. Constructor reviews traces: “this happened more than once.”
3. **Reuse or merge** if a skill already covers it. Spawn only if none
   match. Soft cap: ~5–8 skills/game.
4. Planner may **invoke** a skill instead of emitting raw keys.
5. L4 reflection can rewrite the skill (≤3) or mark it harmful.
6. Human glance before a skill is “on” — also a QA artifact.

**Not in Phase 1 skills (still true for L3 sequences):** Grounding DINO,
ResNet, OpenCV HP readers, reward functions, DQN. Those live in **GSMs**,
not in `Skill.do` lists.

**Manual boost (later, measured):** after pixels-only has a curve, add
the game’s own description and instructions (2048 how-to, Tetris SRS
blurb, StS rulebook excerpt, Platinum manual pages we own). Same harness.
This is the human who reads the manual after a weekend of fumbling —
GameSense gave the bible at *init*; we treat that as a confound to
isolate, then use on purpose.

---

## 5.2 Game Sense Modules — Phase 2 (VLM backbone + DINO + RL)

Full modification map: [`research.md`](research.md). Rubric:
[`research.md`](research.md).

The high-level agent stays the Phase 1 loop (See, 3-way reflect, memory,
task/action plan). When the task is reactive, it **invokes a GSM** and
does not call the VLM again until the module returns.

| GSM kind | Perception | Policy | VLM’s job |
|---|---|---|---|
| **Rule** | Grounding DINO (open-vocab boxes on the screenshot) | Label → named action / click | Tune the **label list** from misses (≤3) |
| **RL** | `extracted`: DINO summary ± screenshot HUD; or `pixels`: CNN on JPEG | Shared DoubleDQN | Fill state/action/**reward JSON**; rewrite from curves (≤3) |

**Fixed kinds per fixture at init** (yaml). Tetris-rt starts with
`place_piece` (rule then RL). 2048 / StS / Platinum battles: **empty
registry** (G4). Elden Ring chapel: `explore` (rule; RL later). Combat
GSM remains stretch, same kinds as Wukong, no teleport reset.

**VRAM:** sequential residency on the 4060 — PLAN (Ollama) / DETECT
(DINO) / TRAIN (DQN). Do not co-resident all three.

**Guards GameSense skipped:** retrieve-before-spawn; sandbox (synthetic
rewards, known frames); JSON reward params (the VLM does not rewrite
Python); `gsm_author_ms` / `usd` vs `vlm_calls_avoided`; restart via the
game’s own UI.

G0 (stub handoff) before G1 (DINO) before G2 (RL) before G5 (ablation).

---

## 5.3 Collect loop — turn-based learn path (`loop/`)

Screenshot in, mouse/key out. Separate product from Phase 2 GSM
(`avatar/`). Game-agnostic agent; fixture plugins supply scorer,
`turn_reward`, and chord execution.

```
screenshot → aim overlay → VLM chord → click (or click-click) → HUD OCR
  → turn_reward → journal + notebook → StepLog (prompt_id)
  → export SFT/DPO → train on 4060 → eval vs baseline
```

**Play:** fixture `sts2`, two-click chords, selected-card state, blocked
hints, pause is not death. Default arm `no_skill`; `--arm dump_all` is
cosine ablation only.

**Train:** SFT gate = `executed_as_proposed` and `turn_reward > 0`. DPO on
same-frame pairs. GRPO Path A = frozen frames only — never a live Steam
window. VRAM schedule: **PLAN (Ollama) → unload → TRAIN (4-bit LoRA)**.

Full commands, memory stores, reward inequalities, code map:
[`system.md`](system.md).

---

## 6. Models (cheap first)

**Locked (Phase 2 planner + collect loop):** `qwen3.5:4b-instruct-q4_K_M`
— one VLM for See + plan (`think: false`, `num_ctx: 4096`). Create the
instruct alias FROM official `qwen3.5:4b-q4_K_M`; bare `qwen3.5:4b` is
thinking-only and returns empty JSON under `max_tokens: 1024`. Cloud
toggle: `gemma4:31b-cloud` via `--model`.

**Collect train weights:** `unsloth/Qwen3.5-4B` (instruct + vision
encoder, vision LoRA off, 4-bit). Merge + GGUF `q4_K_M` back onto the
same Ollama instruct tag. Never `Qwen3.5-4B-Base`. Never Qwen3-VL for
STS2 SFT/DPO.

**Legacy Phase 1 ablation:** `qwen3-vl:4b-instruct-q4_K_M` and
`--dual-brain` with `qwen3.5:4b` / `phi4-mini` text brain. Not the
default runner.

GameSense already ran **Qwen-VL on a 4060** as the planner; that is
enough to *plan and author*. GSMs (DINO + DQN), not a bigger LLM, are
how they got combat. A prestige cloud row is only for **GSM-authoring
quality** vs 4B (G3), not for per-tick play.

8GB is tighter in Phase 2 and collect: **sequential** PLAN / DETECT /
TRAIN. Do not co-resident Ollama + DINO + DQN train or Ollama + 4-bit
LoRA.

Run via Ollama or llama.cpp (vLLM only if it actually fits 8GB).

**Cloud: cost-first, not prestige.** Route through one OpenAI-compatible
gateway ([OpenRouter](https://openrouter.ai) is the default) so swapping
models is a config change. Prefer models that **actually accept images**
— several "Kimi / Qwen / MiniMax" SKUs are text-only.

Cheap-cloud candidates to smoke-test (confirm current IDs + vision + $):

- **Qwen3-VL Flash / Plus** — currently the cheap high-volume vision row
- **MiniMax M3** — native image (+ video) input, computer-use flavored
- **Kimi K2.5** (`moonshotai/kimi-k2.5`) — multimodal; do **not** assume
  K2 / K2.6 / K3 text SKUs can see screenshots
- Optional extra-cheap vision: Gemini Flash / Flash-Lite class

Claude / GPT-4o-class are **optional expensive baselines**, not the
default. Add one only if a writeup needs a prestige comparison.

Pick 1 local + 2 cheap cloud after the See-stage smoke test. Don't sprawl.

---

## 7. Capture efficiency (first-class variable)

Screenshots dominate cost and latency. Treat capture as an experiment,
not an afterthought.

**Ollama mapping (not HuggingFace `min_pixels`/`max_pixels`):** inference
sends JPEG bytes through `VlmGateway`. Lever A is Lanczos `longest_side`
plus a vision-only crop in `CaptureProfile` (`avatar/harness/profile.py`).
See already swept `native/768/512 × q85/q70` and locked **768/q85** for
uncropped frames. After a tight crop, 2048 Act may drop to **512**.

Two levers, wired in the OS harness:

- **Lever A — fewer pixels per call.** Per-fixture presets in
  `configs/experiment.yaml` (`gameplay` vs `ui`): normalized crop rect
  `[left, top, width, height]` in client fractions, plus `longest_side`.
  Crop is **vision-only** — mouse/gamepad stay in full client 0–1 coords.
  Scout (cheap full-frame See every `scout_every` turns) selects a preset
  from `screen_kind`; the VLM does not invent crop coordinates.
  Placeholders: 2048 tight score+grid; Tetris board+next/hold; Platinum/StS
  full client until the commercial spike.
- **Lever B — fewer calls.** Mean absdiff on a 64×64 ROI thumb (PIL, no
  OpenCV). After discrete Act, `wait_settled` polls until the ROI moves
  then goes stable (or times out). **Never skip re-planning after a no-op
  action** (illegal 2048 swipe looks unchanged). Realtime Tetris may skip
  inference+act while the ROI is still, with `force_every` so we cannot
  skip forever. JPEG cache hits (`skipped_frame`) are not the same as
  `skipped_inference`.

Cadence (GameSense): before/after for discrete acts; every ~2s only for
long holds. Log `width/height/bytes`, crop, preset, scout, settle_ms.

Goal: cheapest frame that still lets the model See.

---

## 8. Experiment roadmap

### See experiment
- [x] Vendor 2048; Playwright (or raw screenshot) capture
- [x] Feed frames to local VLMs: "what is the board / score / status?"
- [x] Human-score perception; start a resolution/compression sweep
- [x] **Output:** report — "Can cheap VLMs read this game?"

### Understand experiment
- [x] Same captures: ask for goal + next move (**pixels-only**)
- [x] Classify UI vs gameplay (GameSense prompt shape)
- [x] Still human-scored; optional backend oracle for later automation
- [x] **Output:** report — can it understand without a manual?

### Act experiment (2048 + paused Tetris)
- [ ] L1: named action table + JSON actions on 2048 (Playwright ok)
- [ ] JSON logs per turn: `{screenshot_ref, capture_settings,
      model_reasoning, action, result}`
- [ ] Vendor Tetris + pause-and-decide; same L1 loop
- [ ] Real harness (OS capture/input) on at least one fixture
- [x] L2: last-task + last-10 memory; eyes/brain split; no-op bans; compare to L1 (first harness-lift table)
- [ ] Local model sweep: latency — descriptive, not a gate
- [x] **Output:** report — L0 vs L1 vs L2

### Commercial experiment (Platinum / StS 2 + constructor)
- [ ] Pokémon Platinum through the real harness (long-horizon / L2)
- [x] Slay the Spire 2 collect harness wired (`loop/fixtures/sts2.py`); live VLM corpus + train gates open ([`system.md`](system.md))
- [x] **L3 constructor** on traces: propose/merge skills; cap 3 rewrites;
      log reuse vs duplicate-spawn (2048 traces; Platinum/StS windows missing)
- [ ] **Output:** report — fixtures + did skills get reused?

### Analyze experiment (L4 + manual boost)
- [x] 3-level reflection parser + live 4b underfill documented
- [ ] Plot attempt # → success; skill reuse rate (partial)
- [ ] Agent + human: traces → QA reports; “would I file this?”
- [ ] **Manual boost ablation:** same tasks with official
      description/instructions added; compare to pixels-only curve
- [ ] **Output:** harness-lift table + sample QA reports + manual delta

### GSM experiment (Phase 2 — in scope)
Plan: [`research.md`](research.md). Rubric: [`research.md`](research.md).
- [ ] **G0** stub `invoke_gsm` on unpaused Tetris; no eyes/brain during GSM
- [ ] **G1** rule GSM + open-vocab detector; VLM label-list edits ≤3
- [ ] **G2** DoubleDQN + reward JSON + train-analyze-optimize ≤3 + sandbox
- [ ] **G3** retrieve-before-spawn; authoring $ / ms vs VLM calls avoided
- [ ] **G4** mixed pacing: empty GSM registry on 2048 / StS / Platinum
- [ ] **G5** `state_source` ablation: extracted vs pixels (after G2)
- [ ] **Output:** GSM vs L2-realtime vs pause-and-plan table + cost

### Collect-loop experiment (`loop/`, `feat/collect-loop`)
Operator runbook: [`system.md`](system.md). Contract: [`system.md`](system.md).
- [x] STS2 fixture: aim overlay, two-click chords, `turn_reward`, notebook, journal
- [x] SFT/DPO export + train scaffolding; `prompt_id` hash gate
- [x] Collect pytest is frozen screenshot replay (`loop/fixtures/sts2_screens/` + `labels.jsonl`)
- [ ] Live VLM collect corpus on STS2
- [ ] `--execute` SFT then DPO on 4060; GGUF back onto instruct tag
- [ ] Held-out eval delta (`loop eval --baseline … --candidate …`)
- [ ] **Output:** before/after `turn_run_metrics` on same `prompt_id`

### Ship
- [x] Tier 5 (Elden Ring) chapel collect — frozen appendix on `feat/collect-loop`; STS2 is the collect driver ([`system.md`](system.md))
- [x] Demo video, Phase 1 report, README
- [ ] Phase 2 writeup + GSM demo
- [ ] Collect-loop eval writeup (after held-out delta)

**Personas / L5** are **not** in this roadmap. **DINO / RL** are G1–G5.

---

## 9. Tech Stack

- **See/Understand capture (browser leftover):** Playwright screenshots of vendored 2048
- **Harness capture:** `mss` client-area grab, vision-only crop + Lanczos/JPEG in `encode_image`; settle/scout in `CaptureProfile`
- **Harness input:** Windows `SendInput` (keyboard/mouse) + `vgamepad` / ViGEmBus (Xbox 360)
- **Local inference:** Ollama (`qwen3.5:4b-instruct-q4_K_M` default) or llama.cpp (8GB)
- **Cloud inference:** OpenRouter (OpenAI-compatible); `gemma4:31b-cloud` via `--model`
- **Logging:** structured JSON + screenshot refs (see Act experiment)
- **Action table + skill library + GSM registry:** named controls compile to OS primitives; VLM-written skills as JSON; GSM specs (rule/rl) the planner can invoke (GameSense mapping table, without eval())
- **GSM stack (Phase 2):** Grounding DINO or a small open-vocab detector on screenshots; gymnasium + DoubleDQN; sequential VRAM (PLAN / DETECT / TRAIN)
- **Collect loop (`loop/`):** RapidOCR aim overlay, HUD OCR scorer, FactJournal + Notebook, SFT/DPO export (`loop/eval/`), Unsloth QLoRA on `unsloth/Qwen3.5-4B`

---

## 10. Metrics (track from See experiment)

Human-scored until automation exists. **Do not reduce to “did it win.”**
(BALROG: games are useful because they mix perception, space, explore,
planning.) Always tag **harness level**, **paused vs realtime**, **pixels-only vs
+manual**, **skill-library on/off**, **gsm off/rule/rl**, and
**state_source**.

Split **micro-skills** (swipe, drop, walk, talk, pick a move, click a card)
from **long-horizon** (reach 2048, clear N lines, next Platinum town/gym,
win a StS fight). SIMA’s eval was ~10s; Platinum is where longer goals
show up. GameSense’s UI row is our early bar (~50–95%); combat is not.

- Perception accuracy
- Action selection / decision sanity
- Exploration efficiency + recovery from failure (stuck, death, misclick)
- Attempt # → success (OmniGameArena + GameSense opt-iters)
- **Skill / GSM reuse / duplicate-spawn / rewrite count** (fix their 4.5.3)
- Loop health: actions executed, crashes, stuck
- $/session, latency/decision, capture settings
- **GSM authoring $ and ms vs VLM calls avoided**
- Finding quality (L4): would a human file this?
- Manual boost: delta vs pixels-only on the same tasks
- Tetris-rt: lines / survival, pause-plan vs L2-rt vs rule-GSM vs RL-GSM

Win rate / actions-to-done are descriptive, not gates. Do not import
Wukong 60% as a Tetris target.

---

## 11. Decisions Log

| Date | Decision | Why |
|---|---|---|
| 2026-08-17 | Phase 1 scope, commercial + local model comparison | User constraints |
| 2026-08-17 | Turn-based/puzzle game over action game | Avoids reflex/latency rabbit hole |
| 2026-08-17 | Baba Is You proposed as game | Turn-based, single-player, no anti-cheat, discrete win states |
| 2026-08-17 | Switched to 4-tier complexity structure (browser → browser → commercial → stretch) | Faster start, cheaper iteration, complexity as a built-in variable, T1 DOM oracle available later |
| 2026-08-17 | Tier 1 = 2048, Tier 2 = Tetris (Snake dropped) | T1 stays pure turn-based; Tetris takes the tick at T2 |
| 2026-08-17 | Pure vision always — DOM/state hooks backend-only | Mimics real player perception at every fixture |
| 2026-08-17 | T3 platformer ideas rejected; T3 = Slay the Spire, T4 = Elden Ring (explore, not combat) | Completes 2×2; ER combat infeasible at VLM latency |
| 2026-08-17 | Real-time without pause deferred to Phase 2 | Keeps Phase 1 on vision QA, not latency engineering |
| 2026-08-17 | Games locked; tiers are fixtures not graduation gates | Pipeline is See → Understand → Act → Analyze; run trials, change one variable, move on |
| 2026-08-17 | Thesis = "is a VLM a usable QA agent?" | 2048/Tetris are thin bug surfaces; StS is where the claim can be true |
| 2026-08-17 | Score by human first; automate later | Don't block on oracles; harness maturity first |
| 2026-08-17 | Test both rules-in-prompt and pixels-only | Separate "can't read" from "wasn't told the rules" |
| 2026-08-17 | Cheap VLMs over Claude/GPT (Qwen-VL, MiniMax M3, Kimi K2.5, etc. via OpenRouter) | Cost; prestige models optional baseline only |
| 2026-08-17 | Personas deferred until harness + pipeline work | Too early; not a Commercial-experiment deliverable |
| 2026-08-17 | Screenshots OK in public artifacts; capture efficiency is a first-class variable | Crop/downscale/compress to save $ and time |
| 2026-08-17 | Elden Ring stays stretch | Keep the 2×2 cell; don't promise it |
| 2026-08-17 | One computer-use harness for all games; Playwright is early bootstrap only | Product is the controller, not per-game glue |
| 2026-08-17 | 2048 clone = gabrielecirulli/2048 (MIT, vendor it) | Canonical, DOM tiles, stable to fork |
| 2026-08-17 | Tetris clone = fkucukkara/tetris (MIT, vendor + add pause-and-decide) | Licensed, logic/render/input split; chvin/react-tetris has no license |
| 2026-08-17 | Experiment = harness ladder (L0–L2 + L4), not “can model X play” | VideoGameBench: raw VLMs fail even paused; GamingAgent: harness is the eval |
| 2026-08-17 | Generic screen+keys only (SIMA). L3 pixel-tools and L5 multi-agent deferred | Stay comparable to a human tester; don’t sneak in game APIs |
| 2026-08-17 | Score micro-skills and long-horizon; plot attempt # → success | SIMA 10s ceiling + OmniGameArena improvement dynamics + BALROG |
| 2026-08-17 | Pokémon Platinum restored as Tier 3 (hybrid, emulator); StS → T4, ER → T5 stretch | 2×2 had dropped the JRPG. Hybrid play, no twitch, long-horizon eval. User-owned ROM only |
| 2026-08-18 | VLM is planner + constructor; skills from play traces, not pretrained DINO/RL | GameSense loop, human-like learning. Their unfixed spawn failed — we merge, cap 3 rewrites, run constructor on traces not every turn |
| 2026-08-18 | Pixels-only first; official game description/instructions as a late measured boost | Isolates discovery vs “read the manual.” GameSense gave the bible at init; that’s a confound we delay on purpose |
| 2026-08-18 | Steal GameSense planner (named actions, last-10, 3-level reflection, screenshot cadence). Keep QA metric they omitted. | Address our shortcoming (no constructor/feedback) without inheriting theirs (per-game forks, teleports, eval()) |
| 2026-08-20 | Phase 2 in scope: VLM backbone + Grounding DINO rule GSMs + DoubleDQN RL GSMs. Frozen kinds, retrieve, sandbox, cost, no teleports. Tetris-rt first; mixed-pacing fixtures keep empty GSM registries. Pixels vs extracted is a measured ablation, not a silent drop of vision-only. | GameSense report as backbone. Handoff is the lift; we iterate toward play. See [`research.md`](research.md). |
| 2026-08-28 | Default planner → `qwen3.5:4b-instruct-q4_K_M` (single VLM, no dual-brain). Collect driver → StS 2 (`sts2`) on `feat/collect-loop`. Train on `unsloth/Qwen3.5-4B`, not Qwen3-VL. Elden chapel frozen; operator contract in `system.md`. | Qwen3.5 instruct is the production tag; collect loop is a separate learn path from Phase 2 GSM. |

---

## 12. Open Questions / To Verify

- [x] Game fixtures locked (incl. Platinum as T3 hybrid)
- [x] Anti-cheat (StS, Elden Ring) — not a concern, user's call
- [ ] Confirm emulator (melonDS default) + dual-screen crop + owned ROM on disk
- [x] Scoring philosophy — human first, automate later
- [x] Prompt contract — pixels-only first; manual is a **late** boost, not an Understand-experiment A/B
- [x] Constructor in Phase 1 (bounded skill library) — GameSense loop, their spawn bug fixed
- [ ] Skill schema (recognize / do / end / provenance) — draft when L1 logs exist → **GSM spec** in G0 ([`research.md`](research.md))
- [x] Cloud posture — cheap VLMs, not locked to mainstream
- [ ] Confirm Tetris fork is easy to pause / expose board state; swap to sen-ltd/tetris only if LICENSE is clean and fkucukkara is awkward
- [ ] Confirm real VRAM footprint of local VLMs on 8GB
- [ ] Smoke-test cheap cloud IDs for **vision** + current $ (OpenRouter)
- [x] Competing-project map for the writeup (see §16). Still verify
      versions/numbers before citing; don’t claim a unique “gap” we don’t have
- [ ] Optional: one paused-vs-realtime Tetris row after L1 works
- [ ] G0 `invoke_gsm` stub + tests
- [ ] Detector that fits 8 GB (DINO-tiny / YOLO-World-s / CPU fallback)
- [ ] Reward JSON interpreter (no `exec` of model Python)

---

## 13. Next Action

**Collect loop (branch `feat/collect-loop`).** Follow [`system.md`](system.md)
in order. Open gates: live VLM collect on STS2 → `--execute` SFT → DPO →
held-out eval delta. Do not skip to train without corpus; do not declare
win from loss or dry-run JSONL.

**Phase 2 GSM (`avatar/`).** G0 stub is wired; continue G1 rule/DINO and
G2 RL on unpaused Tetris per [`research.md`](research.md).

Phase 1 remaining fill-ins (Platinum window, manual-boost A/B, L4 QA notes)
stay useful; they do not block collect or GSM iteration.

---

## 14. Phase 2 — Game Sense (in scope)

**Goal:** realtime — no pause — so Tetris (then ER if reached) run at the
game’s speed. GameSense already showed the move: **fast modules the
planner invokes**, not Qwen on every frame.

This is no longer “own project after Phase 1.” It is the current
iteration target. Sequence, VRAM schedule, and file map:
[`research.md`](research.md).

- G0 stub handoff → G1 rule/DINO → G2 RL/DQN → G3 retrieve/sandbox/$ →
  G4 mixed pacing → G5 pixels vs extracted
- Same harness. GSM is a runtime + JSON spec, not a per-game fork
- Restart via in-game UI (`restart` / `KeyR`), never teleports
- Pretrained detectors and RL **are** in, tagged `state_source`
- No game APIs, no unbounded kind-spawn, no `eval()` of model code

---

## 15. Later experiments (after G2)

- **L5** multi-agent QA: player / observer / critic / analyst
- Personas (cautious vs chaos)
- Playwright vs real harness on the same 2048/Tetris task
- One prestige model row (Claude or GPT) for **GSM-authoring** quality vs 4B
- Automate the human rubric with T1/T2 oracles
- Transfer: a Tetris GSM invoked on a held-out clone
- Elden Ring combat GSM (stretch)

---

## 16. Related work (steal, don’t re-litigate)

Distilled: **raw VLMs fail; harness + constructor + GSM handoff is the
variable; QA findings and vision-only reactive play are our slices.**
Cite; don’t clone stacks. Full GameSense notes: `research.md`.

| Source | Steal |
|---|---|
| [VideoGameBench](https://arxiv.org/abs/2505.18134) (2025) | Raw VLMs: ~0.48% realtime / ~1.6% paused. Separate intelligence from latency. |
| [GamingAgent / lmgame](https://github.com/lmgame-org/GamingAgent) | Vanilla VLM is not an eval. Harness ladder is. |
| [SIMA](https://deepmind.google/blog/sima-generalist-ai-agent-for-3d-virtual-environments/) | Screen + keys, no API. One generic agent > per-game specialists. Eval was ~10s micro-tasks. |
| [SIMA 2](https://deepmind.google/blog/sima-2-an-agent-that-plays-reasons-and-learns-with-you-in-virtual-3d-worlds/) | Observe → act → evaluate → reflect → retry. |
| [GameSense](https://aclanthology.org/2025.acl-long.643/) + `research.md` | VLM as planner+developer. Named actions, last-10, 3-level reflection, rule+RL GSMs, cap 3 opts, freeze kinds. **Do not** copy unfixed spawn, teleports, or eval(). **Do** copy DINO/RL with sandbox, retrieve, cost, and a pixels arm. Qwen-VL + 4060 is a known planner. |
| [Cradle](https://arxiv.org/abs/2407.01574) | Pause-VLM baseline. Fine on UI; dead in combat. Phase 1 *is* this class; Phase 2 is the handoff. |
| [BALROG](https://mlanthology.org/iclr/2025/paglieri2025iclr-balrog/) | Score perception, action, explore, plan, recover — not just win. |
| [OmniGameArena](https://arxiv.org/abs/2606.09826) (2026) | Improvement Dynamics: attempt # → success; transfer to held-out variants. |

Also on the map: modl.ai, Regression Games.
Do not claim a unique gap these already fill. Our slice is **cheap VLMs +
generic harness + bounded constructor + GSM handoff (DINO/RL) + QA-finding
quality + delayed manual + pixels-vs-extracted + mixed pacing** on this
fixture set.
