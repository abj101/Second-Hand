# Adaptive Screen Agent

This repository contains one application-agnostic runtime that learns a state schema from pixels and validates actions from observed causal changes. It starts with an empty schema. No application ontology, control name, fixed coordinate, or prewritten state model is accepted as truth.

## Architecture

`pixels → evidence ledger → scene graph → schema proposals → validation → immutable schema revision → state snapshot → legal action surface → verified effect`

- Perception emits neutral OCR, region, color, motion, and track evidence.
- The orchestrator proposes fields, entity types, modes, selectors, actions, and effects from evidence.
- Promotion requires repeated observations and replayable causal traces.
- Runtime projection is the sole authority for state and legal actions.
- Live capture fails closed when provenance is uncertain.
- Shadow mode never sends input. Armed mode can execute only an action already present on the current snapshot's legal surface.

## Run

```powershell
python -m loop collect --workspace .tmp/session --synthetic --steps 12
python -m loop collect --workspace .tmp/live --window "Window title" --steps 30
```

`--arm` is intentionally unavailable for a fresh live workspace until an action has been learned from causal evidence. Never reuse a schema learned against a different window.

## Verify

```powershell
python -m pytest -q
```

## Benchmark (optional)

Infinite Mario uses a pinned [PlayJev](https://github.com/OmniJev/PlayJev) checkout in `.cache/` (not committed). Do not redistribute captured Mario frames (Nintendo artwork).

```powershell
python -m loop fetch-playjev
pip install -e ".[dev,benchmark]"
playwright install chromium
python -m loop react --workspace .tmp/mario --policy baselines --max-steps 20
```

See [benchmarks/infinite_mario/README.md](benchmarks/infinite_mario/README.md).

See [system.md](system.md) for the runtime contract and [ADR 0001](docs/adr/0001-adaptive-perception-kernel.md) for the cutover decision.
