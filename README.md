# Adaptive Screen Agent

> **Branch `feat/lean-agent`: the main spec is [docs/spec.md](docs/spec.md).** A lean agent is being rebuilt from that spec. Everything below, and the code in `loop/`, describes the previous harness. It stays for reference until the lean agent reaches milestone M1.

This repository contains one application-agnostic runtime that learns a state schema from pixels and validates actions from observed causal changes. It starts with an empty schema. No application ontology, control name, fixed coordinate, or prewritten state model is accepted as truth.

## Architecture

`pixels → evidence ledger → scene graph → schema proposals → validation → immutable schema revision → state snapshot → legal action surface → verified effect`

The control-system vision is [the north star](docs/perception-reaction-orchestration-north-star.md). The agreed direction and build sequence is [the architecture review](docs/architecture-review.md). Browser fixtures are optional benchmark tooling, not production input providers.

- Perception emits neutral OCR, region, color, motion, and track evidence.
- The orchestrator proposes fields, entity types, modes, selectors, actions, and effects from evidence.
- Promotion requires repeated observations and replayable causal traces.
- Runtime projection is the sole authority for state and legal actions.
- Live capture fails closed when provenance is uncertain.
- Shadow mode never sends input. Armed mode can execute only an action already present on the current snapshot's legal surface.

## Run

```powershell
.\.venv\Scripts\python.exe -m loop collect --workspace .tmp/session --synthetic --steps 12
.\.venv-decider\Scripts\python.exe -m loop collect --workspace .tmp/live --window "Window title" --steps 30 --diagnostics
```

Use `.venv` for tests and CPU-only collection. Use `.venv-decider` for every live reaction, direct, or armed run; it is the Python 3.12 CUDA environment created by `scripts/setup_native_decider.ps1`. Calling its `python.exe` directly is more reliable than assuming an agent's shell has activated the right environment.

The complete operator/agent runbook, including perception, shadow, experiment, direct-actuation, and armed commands for arbitrary games, is [docs/runbook.md](docs/runbook.md).

`--arm` is intentionally unavailable for a fresh live workspace until an action has been learned from causal evidence. Never reuse a schema learned against a different window.

The diagnostics panel is read-only and follows `shadow_react.jsonl` in the selected workspace. It can also be opened independently:

```powershell
.\.venv-decider\Scripts\python.exe -m loop diagnostics --workspace .tmp/live
```

### Game briefs

The optional game brief supplies background to orchestration without preloading a schema or authorizing input. The bundled [STS2 brief](loop/game_briefs/sts2.json) covers the objective, mechanics, general controls, and what remains unverified locally:

```powershell
.\.venv-decider\Scripts\python.exe -m loop collect --workspace .tmp/sts2 --window "Slay the Spire 2" --game sts2 --steps 30
```

Without `--game`, a recognized exact pinned window title selects its brief automatically. An unknown title keeps generic behavior; an unknown explicit game is an error. Only STS2 is bundled initially. Add another game's JSON file under `loop/game_briefs/` to extend selection; no Python game-specific branches are needed. The [north-star contract](docs/perception-reaction-orchestration-north-star.md#game-brief-purpose-and-authority) defines the format, 300–600 word target, 8,000-byte cap, and model-context boundaries. Brief edits take effect in a new run.

## Verify

```powershell
python -m pytest -q
```

## Local Decider on Windows (optional GPU profile)

Decider runs directly on Windows; WSL2 is not required. The model package supports Python 3.11+, but its current `numpy<2` dependency makes Python 3.12 the supported profile because NumPy 1.26 does not publish Python 3.13 wheels. The setup script creates an isolated Python 3.12 environment, installs the pinned CUDA/Triton stack, and executes a real GPU kernel:

```powershell
.\scripts\setup_native_decider.ps1
```

To also download the immutable 0.8B model snapshot and run a real `system_one` decision:

```powershell
.\scripts\setup_native_decider.ps1 -DownloadModel
```

Use `.venv-decider\Scripts\python.exe` for the Decider-backed runtime. A system `python` or another `.venv` may legitimately point at a CPU-only PyTorch build; the smoke check prevents that interpreter mix-up. Re-running the setup command repairs the environment in place and leaves the locked CUDA packages installed last.

## Benchmark (optional)

Bench games are the list in `benchmarks/test_games.py` (the HTML games plus STS2). Do not redistribute captured Mario frames (Nintendo artwork).

```powershell
pip install -e ".[dev,benchmark]"
playwright install chromium
python -m loop react --workspace .tmp/mario --policy baselines --max-steps 20
```

See [benchmarks/infinite_mario/README.md](benchmarks/infinite_mario/README.md).

See [system.md](system.md) for the runtime contract and [ADR 0001](docs/adr/0001-adaptive-perception-kernel.md) for the cutover decision.
