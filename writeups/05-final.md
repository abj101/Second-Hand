# Phase 1 — consolidated experiment report

**Question:** Can a cheap local VLM be a usable QA agent if it only sees pixels and acts through a named-action harness — and how much does structure (L0→L4) help vs swapping models?

**Scope:** 2048 (browser) → paused Tetris → Platinum/StS (commercial, not run) → Elden Ring (stretch, not run). All inference local Ollama on 8 GB VRAM. Pixels-only except Analyze `--manual` ablation.

**Detail per stage:** [01-see](01-see.md) · [02-understand](02-understand.md) · [03-act](03-act.md) · [04-commercial](04-commercial.md) · [06-l4](06-l4.md) · [07-quant-matrix](07-quant-matrix.md) · **Phase 2 plan:** [08-gsm](08-gsm.md)

---

## Executive summary

| # | Experiment | Status | Headline |
|---|---|---|---|
| 1 | **See** (L0 perception) | ✅ Complete | Qwen3-VL **instruct** reads 2048 boards at 16/16 tiles; game-over overlay still missed ~⅓. Locked roster set. |
| 2 | **Understand** (L0, no manual) | ✅ Smoke done | All models infer 2048 goal from pixels; **none** recognize game-over on full board (still suggest swipes). |
| 3 | **Act** (L1/L2 harness lift) | ✅ n≈40/model | **1.00** L1 success on mergeable 2048 micro-tasks (all 5 VLMs). L2: 0.93–1.00. Paused Tetris dry-run OK. |
| 4 | **Commercial** (L3 constructor) | ⚠️ Partial | Platinum/StS skipped (no windows). Constructor: 1 skill spawned, 0 duplicates from traces. |
| 5 | **Analyze** (L4 reflection) | ⚠️ Partial | Nested-verdict parser fixed (26 tests). Live 4b: `partial` in 31s, 0 QA notes — underfill, not schema crash. |
| 6 | **Quant matrix** (model pick) | ✅ Complete | **Locked:** `qwen3-vl:4b-instruct-q4_K_M` — 95% legal vs InternVL 60% on unpaused 2048. [07-quant-matrix](07-quant-matrix.md) |
| 7 | **GSM** (Phase 2) | 📋 Plan | VLM as developer; DINO rule + DQN RL; Tetris-rt first. [08-gsm](08-gsm.md) |

**Bottom line:** The harness works. Cheap VLMs can *see* and *act* on simple 2048 micro-tasks when the board is mergeable and capture includes the Score HUD. The gap is judgment (game-over, strategic moves) and richer fixtures — not wiring.

---

## Model roster (locked)

Single vision model after quant matrix + peer comparison ([07-quant-matrix](07-quant-matrix.md)):

| Role | Model | Why |
|---|---|---|
| Primary | `qwen3-vl:4b-instruct-q4_K_M` | 95% legal swipes, score 40 vs InternVL 24 on unpaused 2048; ~3.5 GB VRAM |

Pull: `ollama pull qwen3-vl:4b-instruct-q4_K_M`

**Excluded after matrix:** 2B/8B Qwen variants (too weak or VRAM-thrash), InternVL3.5 4B (fast but 60% legal), MiniCPM-o 4.5 / Youtu-VL 4B (weaker play), SmolVLM2 (Ollama text-only packaging), bare `qwen3-vl:4b`/`:8b` (thinking-only), Gemma QAT (score-blind from See).

---

## Experiment 1 — See

**Run:** `runs/20260818T215121Z_2048_see` · **Capture default:** 768 / q85 (HUD + board composite)

| Model | Tiles | Score | Status | Latency |
|---|---|---|---|---|
| qwen3-vl:4b-instruct | 16/16 | 100% | 67% | 3.6 s |
| qwen3-vl:8b-instruct | 16/16 | 100% | 67% | 7.3 s |
| qwen2.5vl:7b | ~12/16 | 33% | 100% | 6.9 s |
| gemma3 *-qat | 12–15/16 | 0% | 100% | 9–17 s |

Game-over detection is the main perception failure (models read the grid but miss the overlay). Score reading required including the Score box in-frame.

---

## Experiment 2 — Understand

**Run:** `runs/20260818T215926Z_2048_understand` (5 models × 3 states)

- **Start / mid:** Models correctly infer “merge tiles toward 2048” and propose legal-sounding swipes (e.g. swipe left on mid).
- **Game-over:** All models still propose moves (“swipe left to combine 32s and 64s”) despite a full, terminated board — the expected failure mode for pixels-only reasoning without manual text.
- No automated rubric yet; qualitative split matches protocol intent.

---

## Experiment 3 — Act

**Runs:** `runs/20260818T221034Z_2048_act` (L1), `runs/20260818T224312Z_2048_act` (L2)

Micro-tasks on mergeable mid board (`swipe_left` / `increase_empty_or_merge`), n≈40 per model:

| Level | gemma3:4b | gemma3:12b | qwen2.5vl:7b | qwen3-vl:4b-inst | qwen3-vl:8b-inst |
|---|---|---|---|---|---|
| **L1** | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| **L2** | 1.00 | 1.00 | 0.93 | 0.95 | 1.00 |

Paused Tetris (`runs/20260818T202148Z_tetris_act`): dry-run executed `shift_left` on piece `T` at col 3. No VLM Tetris table yet.

**Caveat:** Tasks are easy and board-shaped; this measures harness + named-action compliance, not “beats 2048.”

---

## Experiment 4 — Commercial / constructor

**Run:** `runs/20260818T201959Z_all_constructor`

| Metric | Value |
|---|---|
| Skills spawned | 1 |
| Merged / rewritten | 0 / 0 |
| Invokes | 0 |

Merge-not-spawn and rewrite-cap covered by unit tests. Platinum (melonDS) and StS (Steam) windows not available on this machine.

---

## Experiment 5 — Analyze (L4)

**Run:** `runs/20260819T225231Z_l4_analyze` (parser fix + live 4b)

- Nested `verdict` / wrapped payloads / string `qa_notes` now coerce in `parse_reflect` (unit tests).
- Attempt→success from L2 traces: all models ≥0.93 after the mergeable-mid fix.
- Live `qwen3-vl:4b-instruct`: verdict `partial`, 0 QA notes, empty reflection strings — **not** `unparseable` (schema mismatch is gone; 4b underfilled on 2048 micro-task dumps).
- Manual-boost (`--manual`) not A/B'd yet.

---

## What we proved vs what we didn't

**Proved**
- End-to-end harness: capture → VLM JSON → named actions → oracle scoring → JSONL logs.
- Capture ladder + HUD composite; 768/q85 is sufficient for 2048.
- Local Qwen3-VL instruct can read boards and scores on canned fixtures.
- L1/L2 lift on trivial 2048 micro-tasks is ~1.0 across all five local VLMs.
- Constructor merges traces without duplicate spawn.

**Not proved / not run**
- Strategic play or high tile achievement.
- Game-over / terminal-state judgment (See status, Understand moves).
- Platinum, StS, Elden Ring.
- Meaningful L4 QA notes or manual-boost delta.
- Unpaused / realtime Tetris as a **GSM** (handoff without per-tick VLM). Plan: [08-gsm.md](08-gsm.md).

---

## Reproduce

```powershell
python scripts/run_phase1.py --model qwen3-vl:4b-instruct-q4_K_M   # See → Understand → Act
python scripts/analyze_see_run.py                            # oracle-score See runs
python scripts/run_analyze.py --traces runs\<act>\turns.jsonl
```

Config: `configs/experiment.yaml` · Demo: [avatar_phase1_demo.webm](figures/avatar_phase1_demo.webm)

## Non-goals (Phase 1 — kept)

No privileged state to the **planner**, no unbounded skill spawn, no realtime-without-pause **inside Phase 1**.

## Phase 2 (next)

The planner stays. Reactive work moves to GSMs (VLM as developer, DINO + RL as hands). Plan: [08-gsm.md](08-gsm.md). Distill: [`research.md`](../research.md). Rubric: [`eval/rubrics/gsm.md`](../eval/rubrics/gsm.md).

**G0 next:** `invoke_gsm` stub on unpaused Tetris. Do not install DINO or start DQN until the handoff is real.
