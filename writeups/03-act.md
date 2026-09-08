# Act experiment — L0 vs L1 vs L2

**Stage:** Act  
**Fixtures:** 2048 (Playwright bootstrap) then paused Tetris.

## Protocol

- L1: named JSON actions (`swipe_*` / Tetris table), max 5, no wait/observe. The VLM plans from the screenshot.
- L2: **eyes** (locked VLM) emit See JSON; **brain** (local text LLM, no image) emits Act JSON from See JSON + memory. After `wait_settled`, pixel delta + optional See-JSON diff become a verdict (`success` / `partial` / `noop` / `failure`). Last-task + last-10 + banned actions + persisted procedural stats go back into the next plan. Constructor stays offline.
- Micro-tasks, n=20 per cell when collecting the table.
- Cadence: before/after for discrete swipes.
- Tetris: `?pause_and_decide=1` so gravity is not a confound.
- Headline L2 metrics: noop/stuck rate, unique actions per 10 turns, recovery after first noop — not tile score.

```powershell
python scripts/smoke_dual_models.py
python scripts/run_act.py --level L1 --n 20 --launch
python scripts/run_act.py --level L2 --n 20 --launch
python scripts/run_tetris.py
```

Requires `OLLAMA_MAX_LOADED_MODELS=2` on the Ollama **service**. Dual-resident: do not unload the VLM between See and plan. Brain fallback is `phi4-mini` if `qwen3.5:4b` cannot sit next to the VLM.

## Results so far (dry-run, no VLM)

The mid fixture originally packed the board with no colliding equals, so `swipe_left` could not move tiles (success 0.00). Mid is now a mergeable board (`2,2,4,…`). After that fix:

| level | dry-run (n=2 × 2 tasks) |
|---|---|
| L1 | 1.00 |

Run: `runs/20260818T202241Z_2048_act`.

Paused Tetris L1 (`games/tetris` Vite at `:5173?pause_and_decide=1`): oracle reported `status=paused`, piece `T` at col 3, action `shift_left` executed. Run: `runs/20260818T202148Z_tetris_act`.

L0 vs L1 vs L2 model table is the next fill-in after vision SKUs. n=20 per cell when collecting the public table.

Paused vs realtime Tetris is an optional extra row (`?fall_ms=` without pause), not a Phase 1 gate. Unpaused Tetris is the **Phase 2 GSM lab** ([08-gsm.md](08-gsm.md)): this realtime script is the Cradle-no-stop baseline GSMs must beat on VLM-calls-per-episode.

## Realtime ablation (gravity on)

Unpaused Tetris: pieces keep falling while the VLM thinks. How-to + named controls are in the prompt (`--manual`). Stop on game over or `--turns` (default 20).

```powershell
python scripts/run_tetris_realtime.py --headed --model qwen3-vl:4b-instruct
python scripts/run_tetris.py --realtime --manual --fall-ms 2500 --turns 20 --headed
```

Runs land under `runs/*_tetris-rt_act`. Native gravity is ~1000 ms/row at level 1, so a 3–8 s VLM call often misses the piece — that latency tax is the point of the row.
