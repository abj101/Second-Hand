# Avatar

A **generic screen-and-keys harness** with a VLM as planner/developer. The live stack is **Phase 2 Game Sense Modules** (GameSense / ACL 2025): one VLM plans from pixels; reactive work is a GSM (Grounding DINO rule or DoubleDQN) that runs at game speed with **no per-tick VLM call**.

Thesis: [`Reference.md`](Reference.md). Paper distill: [`research.md`](research.md). Phase 2 plan: [`writeups/08-gsm.md`](writeups/08-gsm.md). Legacy Phase 1 reports: [`writeups/`](writeups/).

**Current status:** Phase 2 is the default pipeline. The live harness defaults to cloud Gemma (`gemma4:31b-cloud`) for vision/perception and Qwen3.5 9B Q4 (`qwen3.5:9b-q4_K_M`) for reasoning. Override them with `--perceive-model` and `--reasoner-model` (or `--model`). 2048 / StS / Platinum keep an empty GSM registry (mixed pacing). Unpaused Tetris is the GSM lab (`invoke_gsm place_piece`). On branch `feat/collect-loop`, **STS2** is the collect driver ([`system.md`](system.md): turn-based screenshot → click, SFT/DPO on the same instruct tag). Elden Ring chapel is a frozen appendix ([`loop/DESIGN.md`](loop/DESIGN.md)), not this branch’s operator path. Dual-brain, L0–L4 See/Understand/Act sweeps, and paused Tetris are **legacy Phase 1** (`--dual-brain`, `scripts/run_phase1.py`).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m playwright install chromium
copy .env.example .env
# Optional gamepad (Platinum / Elden Ring later): install Nefarius ViGEmBus, then vgamepad is already a dep
```

Local default: Ollama on the 4060. Create the instruct alias (FROM official `qwen3.5:4b-q4_K_M`) and smoke it:

```powershell
python scripts/pull_local_vlms.py
python scripts/smoke_local_vlms.py --model qwen3.5:4b-instruct-q4_K_M
# Ollama Cloud VL:  python scripts/smoke_local_vlms.py --model gemma4:31b-cloud
```

Use the **instruct** tag — bare `qwen3.5:4b` is thinking-only and returns empty JSON under `max_tokens: 1024`. Cap is `num_ctx: 4096`. Model selection: [`writeups/07-quant-matrix.md`](writeups/07-quant-matrix.md).

### OS control smoke (no VLM)

```powershell
python scripts/smoke_control.py
python scripts/smoke_control.py --window Tetris
```

Live pytest (moves the real mouse/keyboard): `$env:AVATAR_LIVE_INPUT=1; pytest tests/test_harness.py -k live`

### Collect loop (branch `feat/collect-loop`)

Screenshot in, mouse/key out. Turn-based **Slay the Spire 2** (`sts2`) is the driver: one VLM chord per step, `turn_reward` from HUD OCR, journal facts in the prompt. Default arm is `no_skill` (matches SFT prior `SYSTEM_TURN_NO_SKILL`); `dump_all` is an opt-in cosine ablation. Weights update sequentially (SFT/DPO) on the 4060, then GGUF back onto `qwen3.5:4b-instruct-q4_K_M`. Heuristics that rewrite the model's chord after it speaks are not a write path.

STS2 baseline collection requires GPA YOLO GUI detection on CPU (`ultralytics` is a core dependency; weights are under `data/loop/weights/gpa-gui/`). The 2 Sep 2026 20-step smoke reached combat; see [`system.md`](system.md) for its evidence and remaining combat-quality gate.

Operator contract: [`system.md`](system.md). Historical fake/Elden baseline: [`writeups/09-collect-loop.md`](writeups/09-collect-loop.md). Chapel-era pad/explore/Ratchet design (out of scope on this branch): [`loop/DESIGN.md`](loop/DESIGN.md).

```powershell
python -m loop smoke
python -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu
python -m loop collect --fixture sts2 --policy heuristic --no-learn
python -m loop inspect-run --fixture sts2 [--all-runs]
python -m loop export-sft --fixture sts2
python -m loop export-dpo --fixture sts2
python -m loop train-sft --fixture sts2
python -m loop train-dpo --fixture sts2
python -m loop eval --fixture sts2 --baseline runs\<base>_sts2_collect --candidate runs\<sft>_sts2_collect
python -m loop cycle --fixture sts2 [--execute]
python -m loop export-frames --fixture sts2
python -m loop vram-probe
pytest tests/loop -q
```

Export and train default `--source vlm` (drops heuristic rows). `--source all` keeps both. F12 panics (`release_all` + exit). Stop file: `data/loop/stop`. `--no-learn` skips post-run curate only.

Phase 2 Tetris/2048 above stays a **separate** product (`scripts/run_gsm.py`, `scripts/run_2048.py`) — not the collect driver.

## Phase 2 pipeline (default)

One VLM. Named actions **or** `invoke_gsm`. Inner GSM ticks never call the VLM. Runs write `runs/<stamp>_<fixture>_<stage>/turns.jsonl`, `frames/`, `scout/`, `harness_lift.md`.

Tag every row: `harness_level`, `gsm` (`off`/`rule`/`rl`), `state_source`, `detector_family`. Claim `extracted` only when `detector_family=grounding-dino`.

```powershell
# 2048 — mixed pacing: single VLM, empty GSM registry, expect invoke count 0.
python scripts/run_2048.py --turns 20
# Ollama Cloud:  python scripts/run_2048.py --turns 20 --model gemma4:31b-cloud

# Tetris realtime — planner may invoke_gsm place_piece.
cd games\tetris; npm install; npm run dev -- --host 127.0.0.1 --port 5173
python scripts/run_gsm.py --turns 20 --model qwen3.5:4b-instruct-q4_K_M
# Standalone window: --launch. Wiring smoke (no planner):
python scripts/run_gsm.py --turns 5 --force-gsm --skip-models
python scripts/smoke_dino.py --fake
```

Keep the 2048 window visible; capture fails if the app window is closed or covered.

RL train/ablation (in-memory FakeGsmEnv, not a live window):

```powershell
python scripts/run_gsm_train.py
python scripts/run_gsm_ablation.py
```

VLM-every-tick unpaused Tetris (Cradle-no-stop ablation, not the GSM path):

```powershell
python scripts/run_tetris_realtime.py --model qwen3.5:4b-instruct-q4_K_M
```

## Legacy Phase 1

Pause-and-plan stack (L0–L4). Dual-resident eyes + text brain, See/Understand sweeps, paused Tetris, quant matrix. Kept for ablations; not the default.

```powershell
# dual-brain 2048 (legacy: same qwen3.5 for eyes + brain)
python scripts/run_2048.py --dual-brain --model qwen3.5:4b-instruct-q4_K_M
python scripts/smoke_dual_models.py

python scripts/run_see.py --model qwen3.5:4b-instruct-q4_K_M
python scripts/run_understand.py --model qwen3.5:4b-instruct-q4_K_M
python scripts/run_2048.py --level L1 --turns 20
python scripts/run_tetris.py --turns 5 --model qwen3.5:4b-instruct-q4_K_M
python scripts/run_phase1.py --model qwen3.5:4b-instruct-q4_K_M
python scripts/run_analyze.py --traces runs\<act-run>\turns.jsonl
python scripts/run_commercial.py --model qwen3.5:4b-instruct-q4_K_M
python scripts/record_demo.py
```

## Layout

- `avatar/harness` — OS capture (`mss` client crop), named actions → SendInput / vgamepad, see-plan-act cycle; `invoke_gsm` runs a GSM until end then resumes
- `avatar/models` — OpenAI-compatible gateway (OpenRouter + Ollama), JSON responses; `make_eyes` (Phase 2 planner) / `make_brain` (legacy Phase 1)
- `avatar/memory` — last-task + last-10, action bans, persisted procedural JSON, periodic condense
- `avatar/constructor` — merge-not-spawn skill library, max 3 rewrites, offline from traces (discrete sequences; GSM registry is the reactive sibling)
- `avatar/eval` — JSONL logs, rubrics, 3-level reflection, plots
- `avatar/prompts` — pixels-only prompts; `manuals/` are the late Analyze-experiment boost
- `avatar/gsm` — Game Sense Modules: registry, sandbox, rule (Grounding DINO / pixel-blob) and RL (JSON reward + DoubleDQN scaffold)
- `games/2048`, `games/tetris` — vendored MIT clones + backend oracles (not sent to the VLM)
- `configs/experiment.yaml` — n=20, temperature 0.7, capture, locked VLM, GSM kinds
- `writeups/` — experiment reports; Phase 2 plan is `08-gsm.md`; `01`–`07` are legacy Phase 1

Playwright remains for the legacy See/Understand 2048 capture sweep only. Act uses the OS controller: `mss` + `SendInput` + optional `vgamepad` (ViGEmBus).

## What we do not do

Teleports, game APIs / DOM / RAM to anything that chooses keys, `eval()` of model Python, unbounded GSM kind-spawn, prestige-model sprawl.

Phase 2 **does** add Grounding DINO and RL, on the screenshot, behind `invoke_gsm`, with retrieve / sandbox / cost logs. High-level VLM stays vision-only. 2048 / StS / Platinum battles keep an empty GSM registry (mixed pacing). See [`writeups/08-gsm.md`](writeups/08-gsm.md).
