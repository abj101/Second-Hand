# Infinite Mario benchmark (PlayJev)

Optional headless Chromium benchmark for the adaptive react loop. Mario-specific code lives here only; `loop/adaptive/` stays game-agnostic.

## Fetch fixture

PlayJev is **not** vendored in this repository. Populate the ignored cache:

```powershell
python -m loop fetch-playjev
```

Cache path: `.cache/benchmarks/playjev/2d7a0280841e3244a299c9c640efc8cefc46d476/`

## Install (optional extra)

```powershell
pip install -e ".[dev,benchmark]"
playwright install chromium
```

Core `pytest -q` does **not** require Chromium or this cache.

## Run baselines

```powershell
python -m loop react --workspace .tmp/mario --policy baselines --max-steps 20
```

Report: `.tmp/mario/report.json`

## Seeds

Immutable manifests under `seed-manifests/`:

| Split | Seeds | Use |
|-------|-------|-----|
| train | 1–5 | development / baselines |
| val | 11–13 | validation |
| test | 101–103 | **reserved** — set `AVATAR_ALLOW_TEST_SEEDS=1` only for final evaluation |

## Licensing

- PlayJev harness: Apache-2.0 (pinned revision in `source-manifest.json`)
- Upstream mariohtml5: Unlicense (see PlayJev `games/mario/UPSTREAM`)
- **Sprite artwork is Nintendo IP** — do not redistribute frames, datasets, or screenshot-heavy artifacts

If Mario assets cannot be used, PlayJev’s MIT Snake fixture is the documented fallback (not implemented in this repo yet).
