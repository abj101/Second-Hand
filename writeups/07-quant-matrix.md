# Vision model selection — quant matrix + peer comparison

**Stage:** Act (L1), unpaused 2048  
**Fixture:** vendored 2048, Edge `--app` window, SendInput `swipe_*`  
**Prompt mode:** pixels-only — board + swipe in one JSON call  
**Hardware:** RTX-class 8 GB VRAM, `num_ctx: 4096`  
**Canonical peer run:** `runs/20260820T010555Z_2048_quant`

## Decision

**Production vision model:** `qwen3-vl:4b-instruct-q4_K_M` (Ollama official Q4_K_M instruct tag).

Locked in `configs/experiment.yaml`. ~3.5 GB VRAM loaded, ~3.3 GB disk. Use the **instruct** tag with explicit `q4_K_M` — bare `qwen3-vl:4b` is thinking-only and returns empty JSON under `max_tokens: 1024`.

## Protocol

20 unpaused turns per cell, sequential (unload between models). Restart with `R` each cell. Warmup discarded from latency stats. Oracle from `GET /__oracle` (never sent to the VLM). Metrics: tile match 0–16 vs pre-swipe grid, legal move = grid changed, parse-ok = named `swipe_*`, wall-clock = load through last turn.

```powershell
python scripts/pull_quant_matrix.py
python scripts/run_quant_matrix.py --launch --turns 20
```

---

## Phase A — Qwen3-VL size × quant (`runs/20260820T000533Z_2048_quant`)

Compared 2B and 4B across Q8_0 → Q4_K_M → Q3_K_M → Q2_K.

| | Q8_0 | Q4_K_M | Q3_K_M | Q2_K |
|---|---:|---:|---:|---:|
| **2B tiles /16** | 10.6 | 8.6 | 5.8 | 0.7 |
| **4B tiles /16** | **15.0** | 13.3 | 12.4 | 10.8 |
| **2B legal %** | 55 | 40 | 30 | 5 |
| **4B legal %** | **95** | 80 | 55 | 35 |
| **4B wall (s)** | 126 | **96** | 88 | 86 |

**Takeaway:** 4B Q4_K_M is the speed/accuracy pick within Qwen — ~25% faster wall than Q8 with 80% legal moves. 2B is unusable at low quants. 4B Q2 is faster but legal rate collapses to 35%.

---

## Phase B — Qwen 4B vs 8B (`runs/20260820T004013Z_2048_quant`)

| Cell | Tiles /16 | Legal % | Latency | Wall | Score |
|---|---:|---:|---:|---:|---:|
| 4b-q4_K_M | 15.1 | 50% | 3.2s | 90s | 24 |
| 4b-q8_0 | 15.5 | 40% | 4.3s | 116s | 20 |
| 8b-q4_K_M | 14.2 | 50% | **21.7s** | **490s** | 16 |
| 8b-q3_K_M | 15.8 | 40% | 4.3s | 114s | 12 |

**Takeaway:** 8B Q4 thrashes on 8 GB (CPU offload). 8B Q3 fits VRAM but gets stuck on dead swipes. Stay on 4B.

---

## Phase C — Qwen 4B Q4 vs open VLMs (`runs/20260820T010555Z_2048_quant`)

| Model | Ollama tag | VRAM | Tiles /16 | Legal % | Latency | Wall | Score |
|---|---|---:|---:|---:|---:|---:|---:|
| **Qwen3-VL 4B** | `qwen3-vl:4b-instruct-q4_K_M` | **3.5 GB** | 14.4 | **95%** | 3.6s | 99s | **40** |
| InternVL3.5 4B | `blaifa/InternVL3_5:4B` | 3.2 GB | **14.6** | 60% | **2.8s** | **81s** | 24 |
| MiniCPM-o 4.5 | `openbmb/minicpm-o4.5:q4_0` | ~5.9 GB disk | 13.2 | 65% | 3.8s | 112s | 20 |
| Youtu-VL 4B | `youtu/youtu-vl:latest` | ~6.1 GB disk | 10.6 | 30% | 7.1s | 181s | 16 |
| SmolVLM2 2.2B | `richardyoung/smolvlm2-2.2b-instruct:Q4_K_M` | — | — | — | — | — | **fail** |

SmolVLM2 failed at warmup: Ollama registry lists it as text-only (`Multimodal data provided, but model does not support multimodal requests`). Needs a custom GGUF import if retested.

**Takeaway:** InternVL is leaner and faster with comparable board reading, but **legal-move rate and end score favor Qwen3-VL 4B Q4** — the model that actually changes the board 95% of turns. MiniCPM-o and Youtu-VL trail on play quality; Youtu is slow and repeats dead swipes. Qwen wins on the task that matters for Act: pixels → legal OS input.

---

## Consolidated rationale

| Criterion | Winner |
|---|---|
| Board read (tiles /16) | InternVL 14.6 ≈ Qwen 14.4 |
| Legal OS swipes | **Qwen 95%** |
| End score (20 turns) | **Qwen 40** |
| Latency / wall | InternVL (2.8s / 81s) |
| VRAM @ 4096 ctx | Qwen 3.5 GB ≈ InternVL 3.2 GB |
| 8 GB card fit | **Qwen Q4** (MiniCPM/Youtu are 6 GB+ packages) |

For a QA agent that must **see and act**, Qwen3-VL 4B instruct Q4_K_M is the locked choice. InternVL is a credible faster alternative if we later optimize for perception-only benchmarks; it is not the pick for unpaused play on this harness today.

Phase 2 (`writeups/08-gsm.md`) keeps this as the **planner / GSM-author** backbone. Combat lift is supposed to come from DINO/RL modules, not from swapping to 8B.
