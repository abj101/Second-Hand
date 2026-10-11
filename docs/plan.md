# Build plan

This plan builds the system described in [spec.md](spec.md) §13. Each stage gets a detailed task list just before it starts, because its results decide the next stage. This file is updated as each stage ends.

## Phases

Each phase is one packet in `docs/plans/`, and each packet runs in its own orchestrator session: paste the packet's "Paste block".
- The packet shape is defined in [_template.md](plans/_template.md).
- Every signature is in [contracts.md](plans/contracts.md).
- What one orchestrator passes to the next is in [handoff-notes.md](plans/handoff-notes.md). Each phase rewrites it as a compacted digest.

| Phase | Packet | Status | Gate (summary) | WPs | Call cap |
|---|---|---|---|---|---|
| E1 | (this file, below) | done | Perceiver chosen: Gemma; recall 0.71, precision 0.73 | — | — |
| E1b | [E1b.md](plans/E1b.md) | **deferred**: STS2 small icons; run before any mouse game | recall ≥ 0.80 at precision ≥ 0.75 | 2 | ~450 |
| E2 | [E2.md](plans/E2.md) | **done** | 4/4 truth goals; verdict 3/3; registration 17/17; 18 uncached calls, 0 retries | 6 | **60** |
| E3 (M1) | [E3.md](plans/E3.md) | not started | starter obtained from boot with no human input; house, town and lab correct; names Hand and Blue | 7 | **1,000** |
| E4 | [E4.md](plans/E4.md) | not started | 3 doors plus a promoted pattern; a battle and a move; repeat accuracy ≥ first + 0.15; offline memory check | 5 | **600** |
| E5 | [E5.md](plans/E5.md) | **deferred**: Pokémon only for now (game 2 = STS2) | second game with no core changes | 4 | ~1,300 |

**Call policy until the architecture settles:**
- Every run uses the response cache and a `--max-calls` cap.
- Gates use one run, grown in stages from checkpoints.
- Unit tests never call the cloud.
- Live memory ablation is deferred.
- Total for E2–E4: **≤ 1,660 uncached calls.**

### Test layers

| Layer | What it covers | Needs | Gates |
|---|---|---|---|
| Unit | pure logic, plus skills and the loop on the synthetic `GridEnv` with `StubModel` | nothing | every WP |
| Offline replay | the STS2 key; `eval_run --repredict` over a saved trace (0 calls) | `.tmp/`, `runs/` | E1b; E4 (learning, memory proxy, tuning) |
| PyBoy integration | real emulator runs | the ROM in `Rom/` | E2, E3, E4 |
| Window integration | the real game window | game 2 running | E5 (deferred) |
| Metrics | `scripts/eval_run.py`: spec §11, including memory usefulness, with truth from RAM or spot checks | a run directory | E3, E4, E5 |

### Line budget in `agent/`

| | E1 | E1b | E2 | E3 | E4 | E5 |
|---|---|---|---|---|---|---|
| Added | 260 | deferred | +480 | +740 | +180 | +460 (deferred) |
| Running total | 260 | — | 740 | 1,480 | 1,660 | 2,120 |

Pokémon alone (E2–E4) fits spec §12's 1,500 headline almost exactly. The window adapter in E5 is what goes over.

### Decisions from the user (2026-10-10)

1. **ROM:** in `Rom/` at the repo root (gitignored). The agent may use it freely. Code finds it with `find_rom()`.
2. **Cloud usage:** keep it minimal until the architecture is final: caps, caching and staged runs.
3. **Game 2:** Slay the Spire 2, but stay on Pokémon for now, so E5 is deferred.
4. **Commits:** orchestrators commit their phase's files at a passed gate. No push.
5. **`loop/`:** delete it on this branch after M1. It stays on older branches.
6. **Rival name:** "Blue".

### Plan audit (2026-10-10)

[audit-2026-10-10.md](plans/audit-2026-10-10.md) found 2 blockers, 13 major, 21 minor and 6 nit issues. All the fixes are applied to the contracts and to E2–E4. Main changes:
- **Observer:** scroll is a frame-by-frame brute-force search and can return "unsure". Settling uses frame hashes and never stops on a blank frame.
- **Truth:** comes from concrete RAM fields (`screen_text` from `wTileMap`, sprite slots).
- **Places:** follow continuity across the run, battles and menus become candidate places, and identity is keyed by `(name, place)`.
- **Glue:** the code that connects the loop steps now has owners: `resolve`, `PRED`, the view ctx, and `Choice.text`.
- **Runs:** numbered checkpoints, row truncation on resume, and exit codes.
- **Verification:** each effect carries a basis, and only pixel-based effects become observed facts.
- **Exits:** a perception prompt line, plus a budgeted default task, so curiosity finds the way out of a town.
- **Fewer calls:** perception is reused on identical frames, `select` uses 1 call, and the Chooser is skipped for a single option.
- **Cuts:** `ReplayEnv`, FFT, `check_pokemon_perception.py`, `route` and `compact`, and E4 shortcuts and `--compare`.

### Audit questions, answered by the user (2026-10-10)

| # | Question | Default now in the plans |
|---|---|---|
| 1 | Merge the Perceiver and Chooser into one call? | **No** (user, 2026-10-10). E3 compares Gemma against a 0-call code Chooser and decider-2b, offline. |
| 2 | Download decider-2b? | **Yes** (user). Downloaded at the pinned revision `b37f7e1`, 3.8 GB. |
| 3 | Raise E3's cap? | **No** (user). The call cuts are applied, and E3 stops and asks if stage c runs out. |
| 4 | Defer shortcuts out of E4? | **Deferred** (user). |
| 5 | One direction per edge for the E3 gate? | **Yes** (user). |

### Out of scope (whole plan)

- Real-time games.
- Per-game training.
- A trained System-1 model.
- Embeddings.
- User-set goals beyond curiosity.
- Deleting `loop/` before M1.

## E1 — Can a model see what's possible? (now)

**Question:** given only a screenshot, does a vision model list the actions available on screen, and say where to perform each one?

| Task | Output |
|---|---|
| Fresh Python 3.12 venv `.venv-agent` (httpx, pillow, numpy, pytest) | environment |
| `agent/model.py`: Ollama chat client, trimmed from `loop/model/gateway.py` | ~70 lines |
| `agent/perceive.py`: the affordance prompt, schema and output normalization | ~70 lines |
| `scripts/e1_perceive.py`: runs a model over the frozen STS2 key and scores it | ~120 lines |
| Run Gemma 4 31B (cloud), qwen3.5:9b and qwen3.5:4b (local) | `.tmp/e1/<model>/` |

**Data:** the 89 STS2 frames of the frozen Phase 2 key (`.tmp/phase2/key/`). Every clickable object in them is boxed.

**Scoring, automatic:**
- **Recall:** the share of clickable key objects that at least one proposed action points inside.
- **Precision:** the share of proposed points that land on a clickable object (or on a "don't care" box such as potions and relics).
- Seconds per frame, and the parse-failure rate.
- An overlay image per frame, for spot checks by eye.

**Pass:** recall ≥ 0.8 and precision ≥ 0.8 for at least one model, at a latency we can live with (well under 10 s per frame).

**Decision:** which model is the Perceiver. Then, whether small icons need the zoom-in "second look" (spec §6).

### Baseline result (v0, plain points)

| Model | Frames | Recall* | Precision | p50 / p95 per frame |
|---|---|---|---|---|
| Gemma 4 31B (cloud) | 89 | 0.72 | 0.69 | 3.5 s / 5.7 s |
| qwen3.5:4b (local) | 10 | 0.28 | 0.54 | 19 s / 47 s |

\* Recall excludes map nodes. The key boxes every map node, but only reachable ones can be clicked, so map nodes are reported separately.

- **Gemma is the Perceiver.** Local models are too slow and too weak for this role.
- Misses concentrate on the tiny top-right icons, near-miss points, and the draw and discard piles. These are exactly the problems the locating steps target.

### Locating variants (spec §6), same 40 frames

| Variant | What changes | Extra cost |
|---|---|---|
| v0 | plain points | none |
| v1 | rulers in the margins | none |
| v2 | zoom-refine for each affordance | 1 call per affordance |
| v3 | v2 plus snapping to OCR text | none (OCR runs locally) |

Each variant reports recall, map-node recall, precision, median centre error, the refine "not visible" rate, and latency.

| Variant | Recall* | Precision | Centre error (median, fraction of frame width) | Notes |
|---|---|---|---|---|
| v0 plain | 0.70 | 0.66 | 0.012 | |
| v1 rulers | 0.70 | 0.65 | 0.021 | worse: drop |
| **v2 zoom-refine** | **0.71** | **0.73** | **0.005** | 2.4× more precise; 20% of refines answer "not visible" (the rough point is kept) |
| v3 v2 + OCR snapping | 0.71 | 0.73 | 0.005 | no gain over v2: no OCR needed at runtime |

\* Recall excludes map nodes. Dropping the "not visible" refines raises precision to 0.77 but cuts recall to 0.61, so the rough point stays as the fallback.

### E1 conclusions

- **Locating is solved enough.** Zoom-refine is the precision step. In the real loop it costs one call (about 3 s) per *executed* action. Rulers and OCR snapping are dropped.
- **The remaining gap is listing, not locating.** Neither gate number reaches 0.8 yet, and the misses are things never listed at all:
  - the look-alike top-right icons (deck, map);
  - enemies as card targets;
  - the draw and discard piles.
- **Follow-up (E1b, small), before or alongside E2:**
  - a magnified second look at small-icon regions;
  - a prompt line about "targets" (e.g. enemies a card can be played on);
  - a second, wider refine when the first answers "not visible".

  Re-score on the same 40 frames. Gate: recall ≥ 0.8 with precision ≥ 0.75.

## E2–E5

The detailed plans are in the packets listed in the Phases table above.
