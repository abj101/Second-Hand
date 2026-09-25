# Runbook

Run commands from the repository root on Windows PowerShell. Do not use bare `python` for live reaction: agents may inherit the system interpreter or `.venv`, both of which can be CPU-only.

## Environments

| Environment | Use | Create or repair |
|---|---|---|
| `.venv` | tests, synthetic runs, CPU-only perception | `uv venv .venv; uv pip install --python .venv\Scripts\python.exe --editable ".[dev]"` |
| `.venv-decider` | live shadow, direct actuation, armed runs, CUDA Qwen/Decider | `.\scripts\setup_native_decider.ps1 -DownloadModel` |
| `.venv-perception` | experimental GPA + RapidOCR Small + Qwen no-input diagnostics | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_perception.ps1` |

Prefer an explicit interpreter in automation:

```powershell
.\.venv-decider\Scripts\python.exe -m loop --help
```

Interactive activation is optional:

```powershell
. .\.venv-decider\Scripts\Activate.ps1
python -m loop --help
```

The native setup must finish with `uv pip check` and `scripts/check_native_decider.py`. If either fails, do not substitute the system interpreter.

The lean setup pins the installed CUDA stack and checks all cached Qwen, GPA and OCR assets without downloading model weights. It finished with 76/76 pins, `pip check`, and 8/8 asset hashes on 2026-09-24. Its one-process offline smoke is:

```powershell
.\.venv-perception\Scripts\python.exe scripts\check_perception.py --frame LOCAL_FRAME.png
```

That smoke loaded CUDA Qwen, CUDA GPA and CPU RapidOCR Small together on one saved frame. It does not establish warm p95, target coverage, field freshness or action safety; historical Gate E remains failing.

The CLI automatically loads the repository `.env` without replacing variables already supplied by the shell. The default full model split is:

- `gemma4:31b-cloud` through Ollama for image-grounded semantic hypotheses used by perception/schema orchestration;
- local CUDA Qwen 0.8B for low-latency selection among the currently legal opaque action IDs.

Set `OLLAMA_BASE_URL=http://127.0.0.1:11434/v1` and `OLLAMA_MODEL=gemma4:31b-cloud` in `.env`. Confirm the tag exists with `Invoke-RestMethod http://127.0.0.1:11434/api/tags`; a missing or unreachable Ollama service disables semantic hypotheses but does not silently replace the reaction policy.

## Inputs shared by live runs

- `--window "TITLE"` selects one visible window by title substring. If it matches more than one window, add `--hwnd NUMBER`.
- `--workspace PATH` is the durable schema, logs, frames, and target binding for that run. Use a fresh path for a new target process or experiment. Reuse it only for the same pinned target when intentionally testing restart continuity.
- `--game ID` loads `loop/game_briefs/ID.json` into semantic confirmation and orchestration. Omit it for an unprofiled game. An exact title/alias can auto-select a brief, but explicit `--game` is preferred in reproducible commands.
- `--diagnostics` opens the read-only live panel for that workspace. It never enables input.
- `--steps N` is the observation-frame budget. Direct mode instead stops at `--duration-seconds`, which is capped at 30 seconds.

To add a game brief, copy the data shape in `loop/game_briefs/sts2.json` to `loop/game_briefs/<game_id>.json`; no Python switch is required. The brief is advisory context and never grants input authority.

## Run types

Replace `GAME`, `WINDOW`, and `WORKSPACE` in these commands. Add `--game GAME` only when that brief exists.

### Synthetic smoke test

```powershell
.\.venv\Scripts\python.exe -m loop collect --workspace WORKSPACE --synthetic --steps 12
```

### Perception only

Captures and measures without constructing a reaction worker or sending input.

```powershell
.\.venv-perception\Scripts\python.exe -m loop collect --workspace WORKSPACE --window "WINDOW" --game GAME --tracker fused-gpa --stage perception --steps 30 --diagnostics
```

### Shadow reaction

Runs the native Qwen reaction worker and orchestrator but sends no input.

```powershell
.\.venv-perception\Scripts\python.exe -m loop collect --workspace WORKSPACE --window "WINDOW" --game GAME --tracker fused-gpa --stage shadow --steps 30 --diagnostics
```

Add `--decider-graphs` only after `.\scripts\check_native_decider.py --model --graphs` passes.

### Reversible experiment

`--experiment` proposes/adjudicates a bounded reversible interaction. Adding `--arm` permits the admitted experiment to execute; without it the run remains non-actuating.

```powershell
.\.venv-decider\Scripts\python.exe -m loop collect --workspace WORKSPACE --window "WINDOW" --game GAME --experiment --arm --steps 30 --diagnostics
```

### Direct experiment (observation only; input disabled)

`--direct-experiment` still runs a bounded Qwen reaction loop against the pinned window, but it does **not** send clicks. The previous owner bypass used one scene graph as both the leased and current binding; that route is disabled until a frozen lease plus a later distinct capture can revalidate the target. Use shadow for no-input diagnostics. Do not treat this flag as Q2 actuation.

```powershell
.\.venv-perception\Scripts\python.exe -m loop collect --workspace WORKSPACE --window "WINDOW" --game GAME --direct-experiment --duration-seconds 30 --steps 30 --decider-timeout 5 --diagnostics
```

### Qualified armed run

This strict path only executes actions already promoted in the workspace and only after the operating-state gates pass. A fresh workspace normally refuses it.

```powershell
.\.venv-decider\Scripts\python.exe -m loop collect --workspace WORKSPACE --window "WINDOW" --game GAME --arm --steps 30 --diagnostics
```

## Optional components

Semantic confirmation and spatial trackers are independent of the run type; they are not silently implied by "full."

- `--semantic-confirmation required` needs `PROPOSAL_API_KEY` or `OPENAI_API_KEY`, plus optional `PROPOSAL_API_URL` and `PROPOSAL_MODEL`. Without a credential, candidates stay withheld.
- `--tracker fused-gpa` selects the experimental GPA UI detector, Small OCR, card proposals, and shared identity tracker. It needs the tracking extra in the same interpreter: `uv pip install --python .venv-decider\Scripts\python.exe --editable ".[track]"`, plus the local GPA weights. A failed model load is unavailable, never a successful empty result. This path remains unqualified for autonomous play.
- DINO (`fused`, `fused-base`), YOLO-World, and SAM2 are benchmark-only; the live CLI rejects them. Saved comparisons did not establish enough usable accuracy benefit to carry their cost and complexity into the turn-based-first live profile. The default remains `none`, the legacy baseline, not an accuracy recommendation. See [the visual stack decision](lean-visual-stack.md).

Example of no-input shadow diagnosis with both explicitly enabled:

```powershell
.\.venv-decider\Scripts\python.exe -m loop collect --workspace WORKSPACE --window "WINDOW" --game GAME --stage shadow --steps 30 --decider-timeout 5 --diagnostics --semantic-confirmation required --tracker fused-gpa
```

## STS2 full run, one command

This activates the lean perception interpreter, creates a fresh timestamped workspace, loads the STS2 brief, enables diagnostics, and runs a no-input shadow profile:

```powershell
Set-Location (git rev-parse --show-toplevel); $run = ".tmp/sts2-full-$(Get-Date -Format yyyyMMdd-HHmmss)"; .\.venv-perception\Scripts\python.exe -m loop collect --workspace $run --window "Slay the Spire 2" --game sts2 --tracker fused-gpa --stage shadow --steps 30 --decider-timeout 5 --diagnostics
```

The summary is written to `$run\summary.json`; reaction telemetry is written in the same workspace. The diagnostics panel may remain open after the bounded collector exits and can be closed normally.
