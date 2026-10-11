# E1b: Can a second look close the listing gap?

Status: **deferred** (2026-10-10): the project is Pokémon-only for now, and this phase only fixes small-icon listing on STS2. Run it before any mouse game.   ·   Branch: feat/lean-agent

## Paste block

    You are the orchestrator for phase E1b of the lean agent, in repo
    C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

    1. Read, in order: CLAUDE.md, docs/plans/handoff-notes.md, docs/plans/contracts.md,
       docs/plans/E1b.md.
       Then read only the files that packet lists.
    2. Execute the work packages in the order its "Run order" gives. Delegate each WP to a
       subagent with the model tier the packet names (Haiku or Sonnet; never Opus; always set it
       explicitly). Paste the CLAUDE.md rules and the WP card into every subagent prompt.
    3. After each WP, run its acceptance commands yourself. A WP is done only when they exit 0.
       Cloud calls are scarce. Never exceed the packet's call cap. Unit tests use StubModel only,
       and every run goes through the response cache. A WP's acceptance runs only that WP's test
       files; you run the full `tests/agent` suite after each parallel batch. Use forward-slash
       commands (`.venv-agent/Scripts/python.exe ...`); they work in PowerShell and Git Bash.
    4. Stay inside the files the packet lists. Never touch loop/. Never edit contracts.md.
       If a contract has to change, stop and report it.
    5. Run the phase gate, fill in the packet's "Result" section, set its Status line, and update
       the phase table in docs/plan.md.
    6. Hand off: rewrite docs/plans/handoff-notes.md as a semantically compacted digest,
       following section 10 of the packet.
    7. If the gate passed, commit this phase's files: `git add` the paths the packet lists,
       plus docs/. Never `git add -A`, never push. Message: "E1b: <one-line gate result>".
       Then stop. Do not start the next phase.

## 1. Entry criteria

- **Previous gate:** E1 is done (`docs/plan.md`, "E1 conclusions"). Gemma is the Perceiver, and zero-shot points plus zoom-refine are the locating method.
- **Required on disk.** If anything is missing, stop and ask:
  - `.venv-agent/`
  - `.tmp/phase2/key/labels/` and `.tmp/phase2/key_sets/`, which hold the frozen STS2 key
  - `.tmp/e1/gemma4_31b-cloud/perceive_v0.jsonl` and `score_v2_n40.json`, which are the baseline
- **Required running:** the Ollama daemon, with `gemma4:31b-cloud` reachable.

## 2. Gate

The baseline is v2 on the first 40 key frames: recall 0.713, precision 0.728, refine "not visible" rate 0.198. The misses, all of them never listed in the first place:

| Missed category | Count |
|---|---|
| `deck_button` | 23 |
| `map_button` | 13 |
| `enemy` | 6 |
| `draw_pile` and `discard_pile` | 10 |
| `profile_button` | 4 |

| Metric | Pass | Measured by |
|---|---|---|
| Recall (map nodes excluded) | ≥ 0.80 | `score gemma4:31b-cloud --variant v4 --n 40` → `.tmp/e1/gemma4_31b-cloud/score_v4_n40.json` |
| Precision | ≥ 0.75 | same file |
| Refine "not visible" rate | report only (expect < 0.198) | same file |
| Cloud calls per frame, p50 / p95 latency | report only | same file |

- **Decision this gate produces:**
  1. Whether `second_look` over the edge strips goes into the runtime perception step (it adds about 2 calls per perceived frame).
  2. Whether `refine` retries wider at runtime.
  3. Whether the "targets" prompt line stays.
- **If it fails:**
  - Record the per-category misses and the ablation rows (WP E1b.2, step 4), then stop.
  - Do **not** tune the prompt toward STS2 names; the point is a game-neutral fix.
  - The user then chooses between two options:
    - accept the current recall and move to E2, because Pokémon has few small HUD icons;
    - add a model-flagged "dense region" look as a further WP.

## 3. Read list

- `CLAUDE.md`
- `docs/spec.md` §6 (Perception, Locating precisely)
- `docs/plan.md`, the E1 sections
- `agent/model.py`, `agent/perceive.py`, `agent/locate.py`: read these with `codegraph_explore "perceive refine zoom crop_box normalize"`
- `scripts/e1_perceive.py`, the `run` and `score` functions
- `tests/agent/test_perceive.py`

## 4. Contracts

- **Uses:** [perception](contracts.md#perception) and [locate](contracts.md#locate), as built in E1.
- **Provides:** the E1b additions listed in those two sections: `second_look`, `merge`, `EDGE_STRIPS`, `magnify`, `to_frame`, and `refine(retry_frac=)`.

## 5. Work packages

| WP | Goal | Owns files (line budget, added) | Depends on | Tier | Est. |
|---|---|---|---|---|---|
| E1b.1 | Library changes: targets line, second look, merge, wider retry | `agent/perceive.py` (+40), `agent/locate.py` (+20), `tests/agent/test_perceive.py` (+35) | — | Sonnet | ½ d |
| E1b.2 | Add variant v4 to the E1 script, run it on 40 frames, score it | `scripts/e1_perceive.py` (+35) | E1b.1 | Sonnet | ½ d (mostly waiting on cloud calls) |

**Run order:** E1b.1 → E1b.2. Both are small, so there is no parallelism.

### E1b.1: perception and locate additions

- **Goal:** add the three game-neutral listing fixes behind the contract signatures, and keep v0 to v3 reproducible.
- **Read:** `agent/perceive.py`, `agent/locate.py`, `tests/agent/test_perceive.py`, and `docs/plans/contracts.md`.
- **Create or change:**
  - `agent/locate.py`:
    - Add `magnify(image, box)` and `to_frame(p, px_box, size)`.
    - Rewrite `zoom` to call `magnify`, and make `refine` map its point through `to_frame`. Behaviour must stay identical.
    - Add the keyword `retry_frac` to `refine`: on a "not visible" answer, retry once with `zoom(image, at, retry_frac)`. Return the last `Reply` with `ms` summed.
  - `agent/perceive.py`:
    - Add one sentence to `PROMPT`: *"If an action needs a target (for example a card played on an enemy), list each valid target as its own affordance. Also list every pile, deck, counter or icon that can be opened or inspected."* Keep it game-neutral, with no STS2 names beyond that example.
    - Add `EDGE_STRIPS = ((0, 0, 1, 0.15), (0, 0.85, 1, 1))`.
    - Add `second_look(model, image, box, known)`. It sends the magnified crop with a prompt that asks for every clickable thing in that enlarged part of the screen, reusing the affordance schema, and passes the names in `known` as "already listed: …". It maps the points back with `to_frame`, keeps only rows `merge` would add, and runs them through the same row normalization as `normalize`. Reuse that normalization; don't copy it.
    - Add `merge(base, extra, min_dist=DEDUP_DIST)` with `DEDUP_DIST = 0.03`.
  - `tests/agent/test_perceive.py`: add these tests:
    - `to_frame` round trip on a corner crop;
    - `magnify` keeps the aspect ratio and the long side;
    - `merge` drops near-duplicates and same-name rows, and keeps genuinely new rows;
    - `refine` with a stub model that answers `{"at": null}` and then a point retries exactly once and returns the point; with `retry_frac=None` it does not retry.

    A stub model is a 5-line class with `ask()`. Don't use a mocking library.
- **Implements:** the E1b additions to [perception](contracts.md#perception) and [locate](contracts.md#locate).
- **Acceptance:**
  - `.venv-agent/Scripts/python.exe -m pytest tests/agent -q` passes, with all old tests still green.
  - `.venv-agent/Scripts/python.exe scripts/e1_perceive.py score gemma4:31b-cloud --variant v2 --n 40` gives the same recall (0.713) and precision (0.728) as before, which proves the refactor is behaviour-neutral.
- **Done when:**
  - both commands pass;
  - the added lines are within budget;
  - nothing imports from `loop/`;
  - there is no new dependency.
- **Risks:**
  - The `zoom` refactor shifts points by a pixel, and the v2 score changes. Keep the rounding exactly as it is in `crop_box`.
  - The prompt line inflates false targets. E1b.2 measures this through precision.

### E1b.2: v4 run and score

- **Goal:** measure the combined fix on the same 40 frames that E1 used.
- **Read:** `scripts/e1_perceive.py` and the new functions from E1b.1.
- **Create or change:** `scripts/e1_perceive.py` only. Add variant `v4` to `--variant`, and update the module docstring to describe it.
  - **v4 `run`** does, for each frame:
    1. `perceive()`, with the new prompt;
    2. `second_look` on each strip in `EDGE_STRIPS`;
    3. `merge` the results;
    4. `refine(..., retry_frac=0.5)` on each merged affordance, keeping the rough point if refine returns None.

    Each row stores `affordances` (refined, with `"rough"` and `"src": "perceive" | "second_look"`) plus `ms`, `calls` and `refine_null`.
  - **v4 `score`** reuses `score()` unchanged, and additionally reports:
    - `calls_per_frame`;
    - `recall_without_second_look`, scored on the rows with `src == "perceive"`. This is the free ablation.
- **Acceptance:**
  1. `.venv-agent/Scripts/python.exe -m pytest tests/agent -q`
  2. `.venv-agent/Scripts/python.exe scripts/e1_perceive.py run gemma4:31b-cloud --variant v4 --n 40`. It resumes after throttling; re-run until all 40 frames have rows without `error`.
  3. `.venv-agent/Scripts/python.exe scripts/e1_perceive.py score gemma4:31b-cloud --variant v4 --n 40`
  4. Spot-check 5 overlays in `.tmp/e1/gemma4_31b-cloud/overlays_v4/`. Pick frames that previously missed `deck_button` or `enemy`, and say in the Result whether the new points sit on the right objects.
- **Done when:**
  - `score_v4_n40.json` exists with 40 frames;
  - the Result section holds the gate numbers and the ablation.
- **Risks:**
  - **Free-tier throttling.** The run is about 40 × (1 + 2 + ~8 refines) ≈ 450 calls. The run is resumable, so wait and re-run instead of lowering `--n`.
  - **Overfitting to these 40 frames.** Only if the gate passes **and** the user approves the quota, also run `--n 89` and report it. The 49 unseen frames show whether the fix generalizes.

## 6. Test layers for this phase

| Layer | Gates this phase? | Command |
|---|---|---|
| Unit (pure logic) | yes: `merge`, `to_frame`, `magnify`, the retry | `.venv-agent/Scripts/python.exe -m pytest tests/agent -q` |
| Replay (saved frames) | **yes: the gate** | `e1_perceive.py score … --variant v4 --n 40` |
| PyBoy integration | no (no ROM needed) | — |
| Metrics (spec §11) | affordance recall only | same as replay |

## 7. Budgets

- **Lines:** about +95, of which about 60 are in `agent/` and the rest is the script and tests. The `agent/` running total goes from about 260 to about 320, against the 1,500 target.
- **Cloud calls:**
  - Gate run: about 450 calls (40 frames).
  - Optional held-out run: about 1,000.
  - At runtime, if adopted: +2 calls per perceived frame for the second look, and +0.2 per executed action for the retry.

## 8. Out of scope

- Never touch `loop/`. Also out: real-time games, per-game training, and a trained System-1 model.
- Game-specific prompt text, such as STS2 icon names or hard-coded STS2 regions.
- OCR, rulers, a crosshair check, and any change to `score_frame` or the key.
- Wiring the second look into a runtime loop. That loop doesn't exist yet; E2 does it if this gate adopts it.

## 9. Result (filled in by the orchestrator)

- **Gate:**
- **Decision taken:**
- **Deviations from the packet:**
- **Contract changes needed:**
- **Open issues for the next phase:**

## 10. Handoff notes (the last thing the orchestrator does)
Rewrite `docs/plans/handoff-notes.md`. Don't append to it. Keep it under 80 lines, under its fixed headings, with one fact per bullet. Merge in what E2 must know:
- the v4 numbers and the ablation;
- which of the three fixes are in force at runtime, and their call cost per frame;
- any change to the `refine` or `perceive` behaviour;
- the frames or categories still missed.

Drop anything superseded, such as "Open issues for E1b". The test: could the E2 orchestrator start correctly from this file plus `E2.md` alone? Check that every path you mention exists.
