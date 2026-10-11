# Handoff notes

The compacted memory passed between phase orchestrators. Each phase **rewrites** this file at its end, following section 10 of its packet. Read it before the contracts and your packet.

Last written by: the planning session, before E2 (E1b deferred).

## State of the code
- `agent/`: `model.py` (85 lines, Ollama client), `perceive.py` (85), `locate.py` (90). Built in E1.
- `scripts/e1_perceive.py`: run and score variants v0–v3 on the frozen STS2 key.
- `tests/agent/test_perceive.py`: 5 tests.
- `loop/` is the old harness. It is frozen: copy and trim from it, never import it.

## Decisions in force
- **Perceiver:** `gemma4:31b-cloud` via the local Ollama daemon. Local qwen3.5:4b was too slow (19 s per frame) and too weak (recall 0.28).
- **Locating:** rough point, then `refine` (zoom), once per executed action. Rulers and OCR snapping are dropped at runtime.
- **The first game is Pokémon Red in PyBoy.** RAM and save states are for evaluation only.
- **Names:** the player is "Hand" and the rival is "Blue" (Pokémon brief `defaults`). The default goal is curiosity.
- **Scope:** Pokémon Red only. E1b (STS2 small icons) and E5 (second game) are deferred.
- **Cloud calls are scarce** until the architecture settles:
  - use the response cache (`.cache/model/`) and `--max-calls` on every run;
  - build the smallest gate run first, and grow it by resuming from its checkpoint;
  - unit tests never call the cloud.
- **Commits:** orchestrators commit their phase's files at a passed gate. They never push.
- **`loop/`:** may be deleted on this branch after M1. It stays on older branches, and E5 copies sources from `git show e860812:loop/...`.

## Measured numbers
On 40 STS2 frames:

| Variant | Recall | Precision | Centre error | "Not visible" rate |
|---|---|---|---|---|
| v0 plain | 0.70 | 0.66 | 0.012 | — |
| v2 zoom-refine | 0.713 | 0.728 | 0.005 | 0.198 |

- Gemma latency: p50 3.5 s, p95 5.7 s per perception call.
- The v2 misses were never listed at all: `deck_button` 23, `map_button` 13, piles 10, `enemy` 6.

## Environment and gotchas
- **`.venv-agent`** (Python 3.12) has httpx, pillow, numpy and pytest, but **no pip**. Install with `uv pip install --python .venv-agent/Scripts/python.exe <pkg>`.
- **pyboy is not installed yet.** E2 installs it.
- **ROM:** the user puts it in `Rom/` at the repo root (gitignored). Use `find_rom()`; `POKERED_ROM` overrides it. Never commit ROMs, saves or states. `Rom/` also holds a FireRed `.gba` (GBA; PyBoy can't run it), kept for later. `find_rom()` must glob `*.gb` only, never `.gba`. Red is present: `Rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb`, SHA-1 `ea9bcae617fdf159b045185467ae58b2e4a48b9a`, the USA/Europe release that pokered builds.
- **E1 is uncommitted** as of 2026-10-10. E2's entry step commits it first.
- **PyBoy:** `screen.image` is a live view, so use `.convert("RGB")`. `save_state` and `load_state` take file objects.
- **Tests** import fakes as `from tests.agent.fakes import ...` (`pyproject.toml` uses importlib import mode).
- **`.venv-decider`** holds torch cu130 and `decider`, for the offline Chooser comparison in E3. `Mapika/decider-2b` is cached at revision `b37f7e1ba3fbc9238004cf531fabbee2619973fd` (3.8 GB). Load it with `local_files_only=True`.
- **E1 data** lives in `.tmp/phase2/key/labels/`, `.tmp/phase2/key_sets/` and `.tmp/e1/` (gitignored).
- **Free-tier cloud throttles.** All scripts that make many calls are resumable; re-run them instead of shrinking `--n`.

## Deviations from plan and contracts
- None yet.

## Do not retry
- FFT phase correlation for scroll (it ties 0 with ±16 on repeating floors). Use the frame-by-frame brute-force search.
- Copying retry code from `loop/model/gateway.py` (it has none), or settling from `loop/os/profile.py` (it uses wall-clock time).
- Rulers in the margins made the centre error worse (0.012 → 0.021).
- OCR text snapping added nothing on top of zoom-refine.
- Small local VLMs as the Perceiver.

## Open issues for E2
- STS2 listing recall is 0.71 against a 0.80 gate. Parked in E1b (deferred); it isn't a blocker for Pokémon.
- How well Gemma perceives Game Boy frames is unmeasured. E2 measures it on 10 frames.
