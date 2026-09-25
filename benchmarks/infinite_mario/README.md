# Infinite Mario benchmark

Optional headless Chromium benchmark for the adaptive react loop. Mario-specific code lives here only; `loop/adaptive/` stays game-agnostic.

The page source is [robertkleffner/mariohtml5](https://github.com/robertkleffner/mariohtml5) (Unlicense), one entry in `benchmarks/test_games.py`. There is no bundled page server in this repo.

## Install (optional extra)

```powershell
pip install -e ".[dev,benchmark]"
playwright install chromium
```

Core `pytest -q` does **not** require Chromium or a game checkout.

## Run baselines

```powershell
python -m loop react --workspace .tmp/mario --policy baselines --max-steps 20
```

That command needs a local Mario page cache at `.cache/benchmarks/infinite-mario/`. Report: `.tmp/mario/report.json`

## Seeds

Immutable manifests under `seed-manifests/`:

| Split | Seeds | Use |
|-------|-------|-----|
| train | 1–5 | development / baselines |
| val | 11–13 | validation |
| test | 101–103 | **reserved** — set `AVATAR_ALLOW_TEST_SEEDS=1` only for final evaluation |

## Licensing

- Upstream mariohtml5: Unlicense
- **Sprite artwork is Nintendo IP** — do not redistribute frames, datasets, or screenshot-heavy artifacts
