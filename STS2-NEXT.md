# STS2 next — agent brief

Point an agent at **this file**. It is the operator runbook for the next work, not a redesign of the harness.

Contract and code map live in [`system.md`](system.md). Historical collect-only writeup: [`writeups/09-collect-loop.md`](writeups/09-collect-loop.md). Do not treat either as the todo list.

**Goal:** produce a held-out eval delta on Slay the Spire 2 (`sts2`) after a real SFT then DPO train. Success is not “cycle finished” or “loss went down.”

**As of 2 Sep 2026:** the loop contract passes (`447 passed`), and bounded live run [`20260902T220245Z_sts2_collect`](runs/20260902T220245Z_sts2_collect/) reached combat at step 12 with GPA YOLO enabled on CPU. It earned 1.30 host-derived reward across tutorial/navigation transitions, but no verified positive combat transition. There is still no trainable live VLM corpus. Unsloth `--execute` has not been run. Do not write more pipeline architecture unless a gate below fails for a code reason.

---

## How to use this file

1. Read this whole file before acting.
2. Follow the steps in order. Stop at the first failed gate. Report the gate, the evidence, and the smallest fix. Do not skip ahead to train.
3. Default to **no code changes**. The remaining work is live collect, GPU train, and a second collect for eval.
4. If you must change code, change only the sensor or a trainer bug that a gate proved. Do not reopen chapel, Tetris GSM, or GRPO-as-the-learning-path.
5. Windows: use `.venv\Scripts\python.exe -m …`. Bare `pytest` / system Python will miss the package.

Supervisor: an agent that edits `loop/` before a live `inspect-run` on new STS2 lives is off-brief unless a listed gate failed with a traceback.

---

## Do not

- Do not collect with `--arm dump_all` unless someone explicitly asked for that ablation.
- Do not train with `--source all` or `--source heuristic`. Default `--source vlm`.
- Do not mix `prompt_id` hashes across runs you pool. Export and cycle refuse mixed hashes.
- Do not declare a win from training loss, dry-run JSONL, or `cycle` without `--execute`.
- Do not run `train-grpo` until step 5 shows a DPO eval delta. Frozen GRPO scores mark/layout on still frames; it is not HUD `turn_reward`.
- Do not pass `--live` to GRPO. Path A refuses a live Steam window.
- Do not train `Qwen3.5-4B-Base` or Qwen3-VL. Live tag is `qwen3.5:4b-instruct-q4_K_M`. Weights: `unsloth/Qwen3.5-4B`, vision LoRA off, then GGUF `q4_K_M` back onto that same Ollama tag.
- Do not co-resident Ollama and 4-bit train on the 4060. Unload planner before `--execute`.
- Do not lower the SFT gate (`executed_as_proposed` and `turn_reward > 0`) because yield is thin.
- Do not work chapel / pad / `train-rl` / Tetris GSM on this pass.

---

## Invariants (already implemented)

Keep these. Do not “simplify” them.

| Rule | Why |
|---|---|
| Default collect arm `no_skill` | `prompt_id` must hash `SYSTEM_TURN_NO_SKILL`, the prior SFT trains. |
| `score_sts(image)` is OCR-only | Planner JSON must not mint `turn_reward`. Merge VLM `choice` only into logs. |
| Export/train `--source vlm` | Heuristic seed is not the write path. |
| `sft_chat_row` matches row `prompt_id` | Unknown hash → refuse. |
| DPO: `chosen_reward > rejected_reward`, same `prompt_id` | Inverted pairs refuse. Prefer same-frame `vlm.jpg`. |
| Eval compares **run directories**, not model tag strings | `eval --baseline DIR --candidate DIR`. Same `prompt_id` inside each run and between runs. |
| Pause Give Up is not death | False-fail poisons eval. |

---

## Environment

- Game: Slay the Spire 2, borderless window, focused. Title `Slay the Spire 2` or `AVATAR_STS2_WINDOW`. Steam app `2868840`.
- Planner: Ollama `qwen3.5:4b-instruct-q4_K_M` (`think: false`, `num_ctx: 4096`).
- Panic: F12 (`release_all`, exit 130). Stop file: `data/loop/stop`.
- VRAM: PLAN (Ollama) → unload → TRAIN (4-bit LoRA). `python -m loop vram-probe` before first `--execute`. YOLO GUI detect defaults to **CPU** (`--gui-device cpu`). Do not put it on CUDA next to Ollama. HUD/aim OCR is RapidOCR ONNX on CPU (Tesseract fallback). `AVATAR_OCR=tesseract` or `off` to override.
- YOLO GUI is required for STS2 baseline collection: `ultralytics` and `huggingface_hub` are core dependencies and GPA weights live under `data/loop/weights/gpa-gui/`. Use `--gui --gui-device cpu`; reserve `--no-gui` for diagnosis only. Smoke: `python scripts/smoke_gui.py --run-dir runs\20260902T220245Z_sts2_collect --limit 8 --device cpu`.
- Runs write under `runs/<stamp>_sts2_collect/` (`turns.jsonl`, `episodes.jsonl`, `frames/`). `runs/` is gitignored; keep paths in the session notes.

---

## Step 1 — Collect base lives

Need 5–10 `--until-fail` lives with the **base instruct** tag, after short YOLO-enabled combat smoke runs confirm card/target progress. Game focused. Countdown so the operator can click the window.

```powershell
.\.venv\Scripts\python.exe -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3 --gui --gui-device cpu
```

`--no-learn` skips post-run curate only. Do not pass `--arm dump_all`. Do not use `--policy heuristic` for the train corpus.

**Gate 1 — stop if any of these fail:**

- Window never grabbed / clicks miss the letterbox (wrong title, fullscreen exclusive, overlay).
- Every step is title-only; never reaches map or combat. (The 2 Sep 20-step smoke passed this: combat began at step 12.)
- YOLO fails to load, or its marks omit the lower-right character-select checkmark. Stop and repair GUI setup before collecting a baseline.
- `metrics.energy` / HP missing on combat frames (OCR blank). Then `turn_reward` stays 0 and SFT will be empty.
- Pause / Give Up tagged `terminal=fail` (`false-fail`).
- Mixed `prompt_id` inside the run.

Record the run dir path. Repeat until you have several lives, not one lucky Continue.

---

## Step 2 — Inspect, do not train

```powershell
.\.venv\Scripts\python.exe -m loop inspect-run --fixture sts2
# or pin: --run-dir runs\<stamp>_sts2_collect
```

Look at `vlm.jpg` (grid + marks), `choice`, `pixel_changed`, `metrics.screen`, `score_delta`.

**Gate 2 — pass only if:**

- Single `prompt_id` (or you will pass explicit `--run-dir`s that share one hash).
- Combat screens exist, not only title Continue.
- OCR numerics present on combat (energy/HP). VLM-claimed energy with OCR absent must **not** create combat reward (already tested; if you see it in a live run, that is a regression — stop and fix `loop/reward/sts.py`, do not collect more).
- SFT yield: rows with `turn_reward > 0` and `executed_as_proposed`. Menu Continue can exist; it must not be the only mass.
- DPO yield: some same-frame or same-`state_key` pairs with chosen > rejected.

If Gate 2 fails, go to **Sensor failures** below. Do not `--execute`.

Dry-run export is allowed (writes JSONL, sandbox, no GPU):

```powershell
.\.venv\Scripts\python.exe -m loop export-sft --fixture sts2
.\.venv\Scripts\python.exe -m loop export-dpo --fixture sts2
.\.venv\Scripts\python.exe -m loop cycle --fixture sts2
```

`cycle` without `--execute` must sandbox SFT and DPO and exit 0. If sandbox refuses, fix the data or the proven bug. Do not weaken the sandbox.

---

## Step 3 — SFT on the 4060

```powershell
.\.venv\Scripts\python.exe -m loop vram-probe
.\.venv\Scripts\python.exe -m loop train-sft --fixture sts2 --execute
```

Or fold into step 4 via `cycle --execute`.

**Gate 3:**

- Ollama unloaded before train.
- Model is `unsloth/Qwen3.5-4B` (instruct + vision encoder), vision LoRA off, `fast_inference=False`.
- Merge + GGUF `q4_K_M` published back onto `qwen3.5:4b-instruct-q4_K_M`.
- `ok: true`. If VRAM OOM or Unsloth missing, report that; do not silently dry-run and call it trained.

---

## Step 4 — DPO on the same corpus

Same runs, same `--source vlm`, same `prompt_id`.

```powershell
.\.venv\Scripts\python.exe -m loop train-dpo --fixture sts2 --execute
```

Preferred one-shot after Gate 2:

```powershell
.\.venv\Scripts\python.exe -m loop cycle --fixture sts2 --execute
```

That sandboxes SFT, writes JSONL, trains SFT, then trains DPO.

**Gate 4:** DPO `ok: true`. Pairs still `chosen_reward > rejected_reward`. Never Base. Vision frozen.

Note which GGUF / adapter is now on the Ollama tag. Collect after this point is the **candidate**, not more base data mixed into the same run dir.

---

## Step 5 — Held-out collect and eval

Collect **new** lives with the trained tag (same commands as step 1). Do not reuse the base run dir as candidate.

```powershell
.\.venv\Scripts\python.exe -m loop eval --fixture sts2 --baseline runs\<base>_sts2_collect --candidate runs\<trained>_sts2_collect
```

`--baseline` and `--candidate` are **directories**, not model names.

**Gate 5 — win:**

Candidate beats baseline on **combats started** and **mean floor** (and tutorial-clear / title→map if those were the bottleneck). Same **architecture** (`overlay` vs `two_stage`) and same `prompt_id` in both runs — hashes differ across architectures. Mixed hash → eval refuses; do not force it.

**Not a win:** `false-fail` up, two-click rate up with floor down, or only Continue rate up.

If candidate loses: collect more **verified combat** chords (Gate 2), retrain. Do not switch to GRPO to “make it learn.”

---

## Sensor failures (fix these, not the trainer)

| Symptom | Likely cause | What to do |
|---|---|---|
| Clicks miss UI | Window title, exclusive fullscreen, letterbox, not 16:9 | Fix grab/crop; collect refuses client aspect far from 16:9; do not retune reward |
| Combat but `energy` null, `turn_reward` 0 | HUD OCR | RapidOCR should read `N/N` energy on combat; if still blank, check `ocr=rapidocr` in collect log. Do not fill from planner JSON |
| Character select treated as combat; HP/gold from class card; never clicks check | Select OCR + unlabeled check icon; fused glow click; example `mark: 4` | `screen=select`, drop starting stats as HUD, label lower-right check `Confirm` and lower-left `Back`, clip pennant click. Thought/choice bind to labeled ids |
| Title thought says Singleplayer, JSON is `mark: 4` on the logo | Schema example ids; CV art fills mark slots before OCR words | OCR menu words first; `steer_to_labeled` rewrites thought/choice onto `:Singleplayer`. Do not change hashed system prior |
| Icon Confirm/Back/Got it have no OCR; 343 pennant clicks still miss | Blob detector is not a button detector; click sat on fused glow | YOLO GUI boxes (GPA, CPU) merge into aim; OCR labels attach by IoU; Confirm/Back click the upper 40% of the box; menu labels use a longer hover/hold. DINO stays off. Smoke: `python scripts/smoke_gui.py --run-dir runs/<stamp>_sts2_collect --limit 8` |
| Pause = fail | `fail_screen` on Give Up | Pause is not death; fix classifier |
| Empty JSON / logo spam | Planner / empty-fill | Empty JSON fills an unused aim mark, never a stick. Inspect `choice` vs executed |
| Yield is all title Continue | Did not reach combat | More lives is OK only if OCR works; do not train on Continue-only |
| Mixed `prompt_id` | Arm/prompt changed mid-corpus | Do not pool. Collect a fresh set on current `no_skill` |
| Heuristic rows in SFT | `--source all` or forgotten flag | Re-export `--source vlm` |

If you change reward or HUD code: add a test that VLM `energy: 2` with OCR energy absent does **not** mint combat reward. Run `.venv\Scripts\python.exe -m pytest tests/loop -q`.

---

## After a win — still later

Only if Gate 5 passed:

1. Optional: `export-frames` then `train-grpo --frames … --execute` as a **layout** polish on frozen images. Hard-refuse live window. If `GRPOConfig` TypeError on GSPO kwargs, fail closed — do not drop `loss_type="dr_grpo"` / `importance_sampling_level="sequence"`.
2. Do not treat frozen-score inequalities as the same objective as HUD `turn_reward`. They share Resume > Escape, card+target > card-only, Continue > logo. They are not equal scores.

---

## Supervisor checklist

Before merging or calling the pass done, confirm:

- [ ] Agent did not edit chapel / `train-rl` / Tetris GSM.
- [ ] Live STS2 run dirs exist with combat OCR and one `prompt_id`.
- [ ] `inspect-run` showed SFT/DPO yield beyond title Continue.
- [ ] `--execute` actually ran (not dry-run). GGUF on `qwen3.5:4b-instruct-q4_K_M`.
- [ ] Eval used two **run dirs**. Candidate delta on combats / mean floor.
- [ ] Default `--source vlm`. No `dump_all` in the train mix.
- [ ] GRPO skipped unless Gate 5 already passed.

---

## Commands (copy)

```powershell
.\.venv\Scripts\python.exe -u -m loop collect --fixture sts2 --until-fail --no-learn --countdown 3
.\.venv\Scripts\python.exe -m loop inspect-run --fixture sts2
.\.venv\Scripts\python.exe -m loop cycle --fixture sts2
.\.venv\Scripts\python.exe -m loop vram-probe
.\.venv\Scripts\python.exe -m loop cycle --fixture sts2 --execute
.\.venv\Scripts\python.exe -m loop eval --fixture sts2 --baseline runs\<base>_sts2_collect --candidate runs\<trained>_sts2_collect
.\.venv\Scripts\python.exe -m pytest tests/loop -q
```

Export/train also accept `--run-dir` and `--all-runs` (same hash only). `--source` default `vlm`.
