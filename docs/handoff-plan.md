# Handoff: write the staged implementation plan

> **Done on 2026-10-10.** The plan now lives in `docs/plans/`, with its index in `docs/plan.md`. Kept as a record of the planning brief. Don't re-run it.

Paste the prompt below into a new Claude Code session opened in this repo on branch `feat/lean-agent`. That session **plans and does not write harness code**. Its output is a staged plan that orchestrator agents can execute to build the lean agent and get it ready for testing.

---

## Prompt

You are planning the implementation of a lean, generic game-playing agent. **Do not write harness code in this session.** Produce a staged plan that orchestrator agents can pick up and execute work package by work package.

### Read first (in this order)
1. `CLAUDE.md`: mandatory tooling rules (Ponytail full, CodeGraph, Headroom). Repeat them in every subagent and orchestrator prompt you write.
2. `docs/spec.md`: the agreed design. It is the source of truth.
3. `docs/plan.md`: current stage status and E1 results.
4. `agent/model.py`, `agent/perceive.py`, `agent/locate.py`, `scripts/e1_perceive.py`, `tests/agent/test_perceive.py`: the code built in E1. Reuse it; don't redesign it.
5. E1 numbers: `.tmp/e1/gemma4_31b-cloud/score_*.json` (gitignored, on local disk).
6. Old harness sources to **copy and trim** (never import), listed in spec §12: `loop/os/window.py`, `loop/os/wininput.py`, `loop/os/keyboard.py`, `loop/os/mouse.py`, `loop/os/profile.py`, `loop/adaptive/game_brief.py`. Locate code with CodeGraph before opening files.

### Settled decisions (do not reopen)
- **Goal:** play any game zero-shot from pixels. Success means three things:
  - perceive every available action;
  - execute the chosen one correctly;
  - build a verified world model (entities, places, relations, patterns).

  Winning doesn't matter. There is no per-game training.
- **First game:** Pokémon Red in PyBoy. The user supplies the ROM path. Emulator RAM and save states are for evaluation and debugging only; the agent never sees them.
- **Start:** a new game, playing through intro and tutorials. A general prompt plus a per-game brief. Defaults: any name prompt → "Hand". The default goal is curiosity; user-set goals come later.
- **Models:**
  - Perceiver and Orchestrator: Gemma 4 31B cloud (`gemma4:31b-cloud` via Ollama, free tier, which throttles).
  - Chooser: Gemma or decider-2b zero-shot, to be decided in E3.
  - Local qwen3.5:4b was too slow and too weak to be the Perceiver.
- **Locating** (spec §6): rough point, then zoom-refine (one call per *executed* action), then a tile-lattice snap for tile games, then an optional crosshair yes/no check. Entities are referenced by world-model id, never by coordinates.
- **E1 measured** (Gemma, 40 STS2 frames):
  - Zoom-refine: median point error from 1.2% to 0.5% of frame width; precision from 0.66 to 0.73.
  - Rulers made points worse, and OCR snapping added nothing. **There is no OCR dependency at runtime.**
  - Best recall is 0.71 against the 0.8 gate. The gap is *listing*: look-alike small icons, enemies as targets, the piles. The plan must include a small **E1b** work package (a magnified second look at small-icon regions, a prompt line about targets, a wider re-refine on "not visible"), with its gate being recall ≥ 0.8 at precision ≥ 0.75.
- **Memory** (spec §8): plain JSONL upsert stores; identity by `(name, kind, place)`; source tracking with `conf = (sup+1)/(sup+con+2)`; model claims expire unless observed; patterns and shortcuts promoted only by code counting. Build entities, places, relations, tasks and the trace first; patterns and shortcuts in E4.
- **Code:** the new `agent/` package, about 1,500 lines plus prompts. `loop/` is frozen and deleted after M1. Environment: `.venv-agent` (Python 3.12). OCR is not needed at runtime. The E1 OCR cache was built offline from `.venv-perception`.

### What to produce
An executable staged plan for **E2 and E3 (M1)** in full detail, with E4 and E5 detailed enough to start. Use this file layout:

| File | Contents |
|---|---|
| `docs/plans/_template.md` | The phase-packet shape. Follow it exactly. |
| `docs/plans/contracts.md` | Item 1 below. The only place signatures live; phase files link to its anchors. The perception and locate sections already exist; extend the file, don't rewrite those. |
| `docs/plans/E1b.md` | Already written. Don't rewrite it; read its Result section if it has been run. |
| `docs/plans/E2.md` … `E5.md` | One self-contained packet per phase, with its paste block at the top. Covers items 2–6 and 8 for that phase. |
| `docs/plan.md` | The index: a phase table (status, gate, link), the test-layer overview, total budgets, item 7, and the existing E1 results kept as history. |

Each phase is run by a different orchestrator from its paste block alone. Every packet must therefore stand on its own: no "see the previous phase" for anything the orchestrator has to do.

1. **Frozen interfaces, first.** Python signatures and data shapes that every work package builds against:
   - the `Env` protocol;
   - the perception output;
   - the locate API;
   - the skill result type;
   - each memory record type;
   - the trace row;
   - the task schema;
   - the model client.
2. **Work packages.** Each is roughly half a day to a day for one orchestrator, and specifies:
   - its goal;
   - files to read;
   - files to create or change, with line budgets;
   - the interfaces it uses and the ones it provides;
   - its dependencies;
   - acceptance tests: pytest for pure logic, plus one runnable check, per Ponytail;
   - a definition of done;
   - its risks;
   - which model tier executes it (see the rules below).
3. **One paste block per phase** (not per work package), at the top of each packet, using the template's wording. The orchestrator delegates the work packages to subagents. Each WP card must be complete enough to paste into a subagent prompt as is: CLAUDE.md rules, files to read, owned files, scope boundaries ("do not touch loop/"), and acceptance commands. Work packages in one phase own disjoint files, so independent ones can run in parallel.
4. **A test strategy**, in layers:
   - unit tests for pure logic;
   - replay tests on saved frames;
   - PyBoy integration, which needs the ROM;
   - the metrics harness for spec §11, including the memory-usefulness checks.

   Name which layer gates each stage.
5. **Stage gates.** The pass criteria from spec §13, the decision each gate produces, and what to do if a gate fails.
6. **Budgets.** Code lines against the target per stage, and cloud calls per step and per run (with the free-tier throttling in mind).
7. **Questions for the user**, to ask before execution starts. Include at least the ROM path and the cloud quota tolerance.
8. **Out of scope**, explicitly: real-time games, per-game training, a trained System-1 model, and deleting `loop/` before M1.

### Rules for you and for the plan
- **Propose before coding.** Fix root causes, not symptoms. Make the smallest design that works.
- **Subagent models:** Sonnet at most, and Haiku for mechanical tasks. **Never Opus.** Always set the model explicitly.
- **Don't commit or push unless the user asks.**
- **Platform:** Windows, an RTX 4060 with 8 GB, and the free Ollama tier.
- Keep every plan file readable by a human: tables and short bullets, not prose walls.
- When done, summarize the plan in chat, list the open questions, and stop.
