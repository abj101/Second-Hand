# Testing

The suite is `tests/agent/`, about 960 tests. It runs in under a minute on 12 cores with `-n auto`, it is hermetic (no network, no model, no ROM needed), and it covers about 97% of `agent/`. CI (`.github/workflows/tests.yml`) runs it on Linux and Windows for every PR.

## Running

```bash
python -m pytest                      # whole suite, parallel (addopts sets -n auto)
python -m pytest tests/agent/test_world.py -k odometer
python -m pytest -n0 path::test       # serial; use for debugging and cProfile
python -m pytest --cov=agent --cov-report=term-missing
```

Use the `.venv` interpreter. A missing dependency, such as running under system Python, shows up as import errors rather than as skips. Every run prints the 5 slowest tests (`--durations=5`), so look there when something is slow.

## What a test must be

1. **Hermetic.** No network, no model server, no live API calls. Swap in `StubModel` and `GridEnv` from `tests/agent/fakes.py`, or `monkeypatch` the boundary (`run_agent.PyBoyEnv`, `find_rom`, `Ollama`, `_ledger`).
2. **Isolated.** Write only under `tmp_path`. Any code path that writes a relative `runs/...` path needs `monkeypatch.chdir(tmp_path)`. Change globals only through `monkeypatch`, never by hand with `try/finally`.
3. **Fast.** Aim for well under 1 s per test. A test over about 2 s needs a reason (real work in production code, not in the test or the fake). When the slow part is a fake, cache it: `GridEnv` rendering once cost 3 minutes of suite time.
4. **Deterministic.** Seed any RNG. In threaded tests, every wait gets a timeout (`event.wait(10)`, `join(10)`, a `deadline` loop). Never use a bare spin: a `while not done: ...` loop without a sleep starves the other thread of the GIL, and a loop without a deadline hangs CI forever. Use generous timeouts (10 s). They cost nothing when the test passes, and a 1 s timeout flakes under coverage or a loaded machine.
5. **Behavioural.** Name the test after the behaviour it pins (`test_route_failure_marks_target_unreached_and_later_arrival_clears_it`), and assert on outcomes, not merely that nothing crashed. Every bug fix lands with the test that would have caught it.

## Things outside the repo

Some tests need local-only assets. They must skip, not fail, when the asset is absent:

| Needs | Guard |
|---|---|
| Pokémon Red ROM (`Rom/`, never committed) | `try: find_rom() except FileNotFoundError: pytest.skip(...)` |
| a `runs/<name>/` checkpoint | `if not path.exists(): pytest.skip(...)` |
| `node`, `git` | `pytest.skip` / `skipif(shutil.which(...) is None)` |

Run `pytest -ra` to see what skipped and why.

## Shared helpers

- `tests/agent/fakes.py`: `GridEnv` (a deterministic 16 px tile world with a door, an NPC, and optional HUD, wandering and animation) and `StubModel` (returns queued replies and counts calls). Reuse these before writing a new fake.
- Goldens live in `tests/agent/data/w0_golden/`. Regenerate them with `W0_REGEN=1 python -m pytest tests/agent/test_world_golden.py`, then review the diff like code. `GridEnv` frames are pinned to these pixels, so any change to rendering must stay pixel-identical or come with regenerated goldens.

- `tests/agent/conftest.py`: the `stub_runner` fixture drives `scripts.run_agent.main` with a fake env and model (`stub_runner(SavedEnv, model, ledger=True, read=...)`). Use it in any runner test instead of patching `PyBoyEnv`, `find_rom`, `Ollama` and `_ledger` by hand.
- A test module never imports from another test module. A helper that two files need goes into `fakes.py`.

## Keeping the suite honest

- When production code is deleted, delete its tests too. A test whose only caller is the code under test keeps dead code alive.
- A test that skips everywhere, on CI and on every developer machine, is dead. Delete it or make its fixture reproducible.
- The manual tools `scripts/check_*.py`, `eval_frames.py` and `make_fixture.py` have no tests. They are diagnostics that run against local `runs/` data, and are not part of the product.
