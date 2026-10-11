# Handoff notes

Last written by: E2 orchestrator, 2026-10-11; E2 completed at its passed gate.

## State of the code
- E2 is complete on `feat/lean-agent`; E1 baseline commit is `3eaf994`. E3 has not started.
- All six work packages passed their acceptance commands; final `tests/agent` suite: 37 passed, with no skipped PyBoy smoke test.
- `agent/` has env/PyBoy, observer, model/cache, perception/locate, brief, trace, skills and effects/verdict; 705 Python lines, within packet budgets.
- `scripts/make_fixture.py`, `scripts/truth_pokered.py` and `scripts/run_goal.py` are evaluator-only.
- `loop/` and `docs/plans/contracts.md` are unchanged; never import the old harness or edit frozen contracts.

## Decisions in force
- Registration is trusted on the measured sample: pad approach uses summed scroll without model calls per tile.
- `tile_px=16`, `move_frames=16`; short 4/8-frame presses move one tile after settling, rather than only turning.
- Settling uses quiet=24, max_frames=600 and a frame-hash ring; near-uniform frames set blank_seen and cannot settle.
- Model frames use nearest-neighbor upscale to long side 640; the player-kind and edge-exit prompt lines remain enabled.
- Place IDs advance from pixel cuts, never RAM; RAM and save states are evaluator-only.
- Perceiver is `gemma4:31b-cloud` through local Ollama; all six E2 work packages used `gpt-6-luna`, high effort, per user override.
- Runtime names are Hand/Blue and default goal curiosity; debug fixtures use RED/BLUE.
- Pokémon Red is first; E1b and E5 remain deferred. Commit phases only at passed gates, never push.
- Locating remains rough point then refine once per pointer action; no runtime rulers or OCR snapping.

## Measured numbers
- Full gate exited 0: stairs to 0x25, menu shows TEXT SPEED, Mom opens dialogue, door to Pallet Town 0x00; 4/4 goals.
- Pixel verdict agreement: 3/3 (100%); same-map registration: 17/17 (100%), including 12 nonzero moves and 5 blocked presses.
- Sprite recall: 15/15 (100%), matching unique entity centers within 12 native pixels.
- Player kind: 7/10 perceptions (70%); text token match: 7/25 (28%). These are report-only metrics.
- Mean tiles per same-map press: stairs 1, Mom 0.25, door 0.778; menu navigation is excluded from map registration.
- Phase total: 18 uncached asks and 18 actual HTTP attempts, 0 retries; durable phase cap 60.
- Final gate used 10 cached asks and 1 uncached ask; perceptions are reused through `.cache/model/`.
- Stairs takes 5 inputs; OPTION selection takes 5 inputs and one menu-read call; Mom interaction takes 3 inputs after approach; door takes 10 inputs and zero skill model calls.
- Rectangle-wall regression and a zero-model real-PyBoy door probe validate the sidestep repair; the full gate also passes with it.
- E1 STS2 v2 recall/precision were 0.713/0.728, center error 0.005; local qwen recall was 0.28 and latency 19 s.

## Environment and gotchas
- `.venv-agent` is Python 3.12 with PyBoy 2.8.1, httpx, Pillow, numpy and pytest; no pip. Install with `uv pip install --python .venv-agent/Scripts/python.exe`.
- ROM: `Rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb`; find_rom honors POKERED_ROM and globs only *.gb.
- `.tmp/e2/red_2f.state` is settled map 0x26 at (5,4); `.tmp/e2/red_1f.state` is settled map 0x25 at (4,3).
- `runs/e2_summary.json` holds latest-goal metrics; `runs/e2_stairs/`, `runs/e2_menu/`, `runs/e2_mom/`, `runs/e2_door/` retain trace/call/truth/frame history, including failed attempts.
- `.tmp/e2/cloud_budget.json` records 18 requests; preserve it and the 60-call E2 cap for any E2 diagnostic continuation.
- Runner exits 3 on cap and 4 on unavailable cloud; checkpoint/harness under `.tmp/e2/`. Stop on either, no polling or raised cap.
- Verified RAM: map D35E, y D361, x D362, party D163, battle D057, player D158, rival D34A, tilemap C3A0, sprites C100.
- Sprite slots are 16 bytes; picture +0, hidden image index FF at +2, y byte +4, x byte +6; add (8,8) for screen centers.
- Text box uses border tiles 79–7E in tilemap rows 12–17; unknown charmap tiles decode as ?. Room graphics provide no readable text signal.
- PyBoy screenshot is a live view: always convert RGB. save_state/load_state take file objects; release inputs in finally.
- Tests import fakes as `tests.agent.fakes`; test configuration uses importlib mode.
- `.venv-decider` retains torch/decider for E3; cached Mapika/decider-2b revision b37f7e1ba3fbc9238004cf531fabbee2619973fd must load local_files_only=True.
- E1 data paths still exist: `.tmp/phase2/key/labels/`, `.tmp/phase2/key_sets/`, `.tmp/e1/`.

## Deviations from plan and contracts
- No contract changes. Text-appearance evidence stays model-based because perception has no text-region bounds.
- Perception context adds the scripted target query; runner matcher prefers concrete door/mat names over generic exits.
- Runner forwards actual settling frames through on_frame so evaluator instrumentation preserves fade evidence.
- Boundaries prohibit env RAM/state access; PIL image.save is permitted for image encoding.
- The user authorized the additional full gate after the packet's original one-rerun allowance; it passed with one additional uncached call.

## Do not retry
- Do not reset the E2 request ledger, raise phase caps, or make cloud calls from unit tests; all runners use response caching.
- FFT scroll correlation ties on repeating floors; use frame-by-frame brute-force registration.
- Old gateway has no retry code and old settling is wall-clock based; do not copy them.
- Margin rulers worsened E1 center error; OCR snapping added nothing; small local VLMs were weak.

## Open issues for E3
- Player-kind labeling and text reading remain weak; raw sprite recall does not imply reliable semantic labels.
- Handle target-not-listed, unknown player, scroll unsure, malformed menu JSON, blocked axes, input limits and scene cut explicitly.
- Approach remembers successful sidestep direction when alternate-axis delta is zero, avoiding obstacle-induced left/right oscillation.
- Registration trust is based on one deterministic run per goal; retain bounded inputs and uncertainty handling outside the measured sample.
- E1b STS2 recall remains below its separate 0.80 gate; deferred, not a Pokémon blocker.
