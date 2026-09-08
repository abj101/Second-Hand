# See experiment — Can cheap VLMs read 2048?

**Stage:** See (L0)  
**Fixture:** vendored gabrielecirulli/2048  
**Independent variable:** capture settings (longest-side × JPEG quality), then model  
**Prompt mode:** pixels-only  
**Canonical run:** `runs/20260818T215121Z_2048_see` (HUD + board composite, 5 local VLMs smoke-tested)

## Protocol

1. Canned boards: `start`, `mid`, `gameover` via `loadAvatarFixture` (backend only).
2. Capture sweep: native / 768 / 512 × JPEG 85 / 70. Screenshot stacks `.heading` (title + Score/Best) on `.game-container` at device scale 2 (native ≈ 1000×1224).
3. Ask: board / score / status; classify UI vs gameplay.
4. Oracle-score tiles (0–16), score, status (`scripts/analyze_see_run.py`; human rubric in `eval/rubrics/see.md`).
5. Smoke all five local Ollama VLMs; lock the winners in `configs/experiment.yaml` `models.locked`.

## How to run

```powershell
python scripts/run_see.py --skip-models   # frames only
python scripts/run_see.py                 # all configured local VLMs
python scripts/analyze_see_run.py         # oracle score all See runs
python scripts/analyze_see_run.py --run 20260818T215121Z_2048_see --json
```

## Capture results (bytes)

HUD composite is larger than board-only crops but unlocks score reading. Downscale + JPEG still cut bus cost:

| state | native q85 | 768 q85 | 512 q70 |
|---|---|---|---|
| start | 30641 | 20286 | 9593 |
| mid | 48366 | 30988 | 13842 |
| gameover | 70237 | 44022 | 18995 |

Human glance: start/mid 768/q85 fully readable. Game-over 512/q70 still readable (diagonal 2…128). **Default capture for later experiments: 768 / q85** (~20–44 KB), not native.

Sample frames: [start](figures/see_start_768_q85.jpg), [mid](figures/see_mid_768_q85.jpg), [gameover 512/q70](figures/see_gameover_512_q70.jpg).

## VLM results (oracle automation)

Smoke cells: `start` / `mid` / `gameover` at native + 768, JPEG q85 (6 frames per model). Scored with `scripts/analyze_see_run.py` against backend oracle (tiles 0–16, score exact match, status vs `over` flag).

### All local models (4 runs, 60 turns)

| model | tiles (avg) | score | status | perfect† | latency |
|---|---|---|---|---|---|
| `qwen3-vl:4b-instruct` | **15.7/16** | **67%** | 83% | 33% | **3.3s** |
| `qwen3-vl:8b-instruct` | **16.0/16** | 50% | 83% | 33% | 7.0s |
| `qwen2.5vl:7b` | 12.2/16 | 33% | 100% | 0% | 6.9s |
| `gemma3:12b-it-qat` | 15.3/16 | 0% | 100% | 0% | 16.5s |
| `gemma3:4b-it-qat` | 12.3/16 | 0% | 100% | 0% | 9.0s |
| `qwen3-vl:4b` ‡ | 1.3/16 | 0% | 8% | 0% | 19.4s |
| `qwen3-vl:8b` ‡ | 2.7/16 | 0% | 17% | 0% | 34.8s |

† perfect = 16/16 tiles + correct score + correct status. ‡ thinking-only tags; return `{}` under `max_tokens: 1024` — do not use.

### Canonical run `20260818T215121Z_2048_see` (HUD composite)

| model | tiles | score | status | perfect | latency |
|---|---|---|---|---|---|
| `qwen3-vl:4b-instruct` | 16/16 | **100%** | 67% | 67% | 3.6s |
| `qwen3-vl:8b-instruct` | 16/16 | **100%** | 67% | 67% | 7.3s |

**Board reading:** Qwen3-VL **instruct** tags read every tile on all three fixtures once the Score box is in-frame. Gemma QAT models often misplace tiles or hallucinate scores. Qwen2.5-VL reads some boards but drifts on tile positions and score.

**Score reading:** Failed on board-only captures (pre-HUD fix). With HUD composite, both instruct Qwen3-VL models hit 100% on smoke cells. Gemma never matched oracle score.

**Status / game-over:** Main residual error. Both instruct models label the `gameover` fixture as `gameplay` / `playing` on 2/3 smoke cells — they read the full board correctly but miss the “Game over!” overlay (or infer “still playing” from a full grid). Qwen2.5-VL and Gemma sometimes infer game-over from a full board even when the overlay is absent in the crop.

**Latency:** `qwen3-vl:4b-instruct` is ~2× faster than `8b-instruct` at equal tile accuracy on the canonical run.

## Model lock

Locked in `configs/experiment.yaml` for Understand → Act → Analyze:

1. **`qwen3-vl:4b-instruct`** — primary (best speed + tile/score accuracy)
2. **`qwen3-vl:8b-instruct`** — secondary (same tiles, slower)
3. **`qwen2.5vl:7b`** — fallback (GameSense 4060 baseline; weaker board/score)

Excluded: bare `qwen3-vl:4b` / `:8b` (thinking-only), both Gemma QAT tags (score blind).

## What not to claim

Reading the board is not “the agent plays 2048.” This write-up is perception + capture cost only.
