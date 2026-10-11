# Game agent spec (v0.3, 2026-10-10)

Status: the main spec on branch `feat/lean-agent`. It is agreed but not built yet. It supersedes the [north star](perception-reaction-orchestration-north-star.md) and the [architecture review](architecture-review.md), which stay only as history.

## 1. Goal

Play **any** game from pixels, without training on that game. Success means:

1. **Perceive** every action available on screen: walk to the door, talk to the trainer, pick a battle move.
2. **Execute** the chosen action correctly.
3. **Build a world model** from what happens: things, places, how they relate, and recurring patterns.

Winning does not matter. Understanding does.

First game: **Pokémon Red** (PyBoy emulator; you supply the ROM). Every game after that must work with no core code changes.

## 2. Principles

1. **Generic core.** Only the environment adapter knows which game is running. Game knowledge comes from the brief and from playing.
2. **Models see and propose; the harness remembers, executes and verifies.** Every model call is stateless, and all memory lives in the harness.
3. **Nothing becomes a fact without verification.** What was observed beats what a model claimed.
4. **Learn by predicting.** Every action carries a predicted effect, and the check afterwards scores that prediction.
5. **Get fast by compiling.** Patterns that have been verified often enough become deterministic shortcuts that need no model call.
6. **No per-game training.** Emulator RAM and save states are for evaluation and debugging only. They never reach the agent.

## 3. Starting context

The agent starts a **new game** and plays through the intro and tutorials. It does not start from a save part-way through.

| Input | Contents |
|---|---|
| General prompt | How to approach an unknown game: read all on-screen text, try common UI conventions, explore before committing |
| Game brief | Short advisory notes per game (≤ 8 KB, in the existing `loop/game_briefs/` format) |
| Defaults | Answers to setup prompts, e.g. any name prompt → **"Hand"**; any other setting → the game's default |

Text the game shows (tutorials, dialogue, signs) is saved as knowledge, with the game recorded as its source.

## 4. Architecture

```
            ┌──────────── Orchestrator (cloud VLM, slow, rare) ────────────┐
            │ goals and tasks · proposes patterns · reflects on the trace   │
            └───────────────▲──────────────────────────────┬───────────────┘
                 escalations│                              │ tasks
  frame ─► Observer ─► Perceiver ─► World model ◄─► Chooser ─► Skill ─► input
             (settle)     (VLM)       (harness)      (picks one   (deterministic,
                                          ▲            affordance)  closed loop)
                                          └───────── Verifier ◄────────┘
```

| Component | Kind | Job |
|---|---|---|
| Environment adapter | code | `screenshot()`, `inputs()`, `press()`, `click()`, `tick()`; `ram()` for the evaluator only |
| Observer | code | Pixel diff, emitting `settled` and `changed` events |
| Perceiver | VLM | Frame → visible text, entities and **affordances** (actions possible right now), with positions |
| World model | code | Stores everything below; renders a small view for each model call |
| Chooser | model | Picks one affordance for the current task, along with a predicted effect |
| Skills | code | Turn an affordance into inputs, closed loop |
| Verifier | code + VLM | Compares the predicted effect with what happened; writes the result to the trace |
| Orchestrator | cloud VLM | Sets goals and tasks; proposes patterns; handles escalations and reflection |

## 5. The loop

**Every step:**
1. Wait for a settled frame.
2. Perceive: get text, entities and affordances.
3. Update the world model: match entities to the ones already known, and add new facts.
4. Choose an affordance for the active task.
5. Predict its effect from matching relations and patterns. If none match, the Chooser guesses.
6. Execute the matching skill.
7. Verify: predicted versus actual.
8. Record the step in the trace and update the confidence of each relation involved.

**The Orchestrator wakes when:**
- a task finishes, fails or runs over its step budget;
- a new place or screen type appears;
- a prediction fails in a surprising way;
- the stuck monitor fires;
- about every 50 steps (reflection).

## 6. Perception

Each settled frame produces:

```json
{"screen": "overworld", "text": ["MOM: Right. All boys leave home some day."],
 "entities": [{"kind": "door", "name": "front door", "at": [0.50, 0.85]},
              {"kind": "npc", "name": "mom", "at": [0.62, 0.40]}],
 "affordances": [{"verb": "approach", "target": "front door"},
                 {"verb": "interact", "target": "mom"},
                 {"verb": "press", "input": "START", "why": "open menu"}]}
```

- Positions are normalized screen coordinates. Models answer on their native 0–1000 grid, which code converts.
- Text is copied verbatim, because tutorials are a primary source of knowledge.
- Nothing here is treated as fact until it is verified or seen again on later frames.

### Locating precisely

Precision matters only for the action actually being executed, so the expensive steps run once per action, not once per frame. Grid cells are not used as the precision mechanism: a cell grid caps precision at the cell size, and the lines cover the content.

1. **Rough:** the perception call lists affordances with approximate points.
2. **Zoom:** a built-in tool. It crops around the target, upscales the crop, and asks the model to point inside it. Code maps the answer back to the full screen. Any model may call `zoom(region)` at any time.
3. **Snap (deterministic):** tile games snap to the tile lattice, found from how far the screen scrolls.
4. **Check (optional):** show the crop with a crosshair and ask "is this *target*? yes/no". Act only on a yes.

**Across steps, things are referenced by world-model id, never by coordinates.** Each new frame re-resolves where an entity is.

**Measured in E1** (Gemma, 40 STS2 frames):
- Zoom-refine cut the median point error from 1.2% to 0.5% of the frame width and raised precision from 0.66 to 0.73.
- Rulers in the margins made points worse.
- Snapping to OCR text added nothing on top of zoom-refine.
- So: no rulers, and no OCR at runtime.

## 7. Skills

A small set of generic, closed-loop skills, the same for every game.

| Skill | What it does |
|---|---|
| `approach(target)` | Step toward the target's screen position, checking after each step that the view moved, until adjacent. On a mouse game this is a click |
| `interact(target)` | Face the target, press confirm, wait for a change |
| `select(item)` | Ask which item is highlighted, move the cursor until it is the target, confirm |
| `advance()` / `cancel()` | Move through dialogue, or back out |
| `type_text(text)` | Enter text on a text field or an on-screen keyboard (names use the defaults) |
| `press(input)` | Raw input, for anything the other skills don't cover |
| `wait_until(change)` | Wait until the screen settles or changes |

A skill reports `done`, `failed` or `blocked`, with a reason. It never retries forever.

## 8. World model

| Store | Holds | Example |
|---|---|---|
| Entities | id, kind, name, attributes (each with source and time), place, first and last seen | `mom: npc, Red's house 1F` |
| Places | Nodes, with edges that record which action led where; visit counts; blocked moves | `house 1F —(approach door)→ Pallet Town` |
| Relations | `subject, predicate, object, evidence, confidence` | `bug catcher starts_battle_when player_in_sight` |
| Patterns | Rules generalized across instances; proposed by the Orchestrator, promoted only by counted evidence | `kind:door leads_to kind:place` |
| Knowledge | Game text and learned facts, with source | Tutorial: "Press START to open the menu" |
| Tasks | The task tree (see §9) | |
| Trace | Every step: state, options, choice, prediction, outcome, verdict | |

### Storage

- Everything is held in memory while the agent runs.
- Each store is a JSONL file of upserts, where the last row for an `id` wins. The files are replayed on start and compacted at checkpoints.
- `trace.jsonl` is append-only and is never loaded whole.
- No database, and no embeddings in v1.

```
entities.jsonl  {"id":"e_mom","kind":"npc","name":"mom","place":"p_red_1f","at":[0.62,0.40],
                 "attrs":{"role":{"v":"gives_items","src":"text","step":41}},"first":12,"last":88,"seen":14}
places.jsonl    {"id":"p_pallet","name":"pallet town","visits":3,"landmarks":["e_lab_door"],
                 "edges":[{"verb":"approach","via":"e_front_door","to":"p_red_1f","n":2,"ok":2}]}
relations.jsonl {"id":"r17","s":"e_front_door","p":"leads_to","o":"p_pallet","src":"observed",
                 "sup":2,"con":0,"ev":[203,330],"state":"live"}
patterns.jsonl  {"id":"pt3","s":"kind:door","p":"leads_to","o":"kind:place","verb":"approach",
                 "sup":3,"con":0,"inst":["r17","r31","r40"],"state":"promoted"}
knowledge.jsonl {"id":"k9","text":"Press START to open the menu","src":"game_text","step":7}
trace.jsonl     {"step":331,"place":"p_pallet","choice":"approach e_lab_door",
                 "pred":{"basis":"pattern","id":"pt3","effect":"place_changes","conf":0.67},
                 "used":["pt3","r17"],"outcome":{"effect":"place_changes","to":"p_lab"},"verdict":"hit"}
```

### Identity

Entities are matched without a tracker.

- **Things in the world** (NPCs, doors, signs) are keyed by `(name, kind, place)`. **Recurring types** (moves, items, menu options) are keyed by `(kind, name)`, globally.
- Names are case-insensitive. Within edit distance 1, or a known alias, they count as the same.
- If two candidates are about equally close, **create a second record** (`bug catcher#2`) instead of guessing.
- Two sightings of the same name on one frame are two entities.
- Places are matched by on-screen name. Failing that, by landmark overlap of 0.6 or more. Failing that, a new `unnamed_k` place is created.

### Confidence and provenance

- The source of every fact is `observed` (verified by code), `text` (game text, kept verbatim) or `model` (a model's claim).
- `conf = (sup + 1) / (sup + con + 2)`. A verified hit adds a `sup`; a miss or surprise adds a `con`.
- **Model claims** are shown as "(unverified)". They are never used to make a prediction, and they expire after 200 steps unless observed.
- **Refuted** means `con ≥ 2` and `con > sup`. A refuted belief is kept, with the reason, and at most two relevant ones are shown under "don't believe".
- An observed fact with `sup ≥ 3` is not overturned by one contradiction. The contradiction is logged as a surprise and wakes the Orchestrator.
- The trace is never summarized away. Summaries are derived from it, so a summary is never the only copy of a fact.
- Routes between places are found by graph search in code, not by a model.

### Patterns and shortcuts

These are counted by code. The Orchestrator may name or refine a pattern, but never promotes one.

- **Key:** `(subject kind, predicate, object kind, verb)`. **Support** is the number of distinct instances with a verified hit.
- **Candidate** at support ≥ 2. **Promoted** at support ≥ 3 with contradictions ≤ 10%. **Demoted** if 2 of its last 5 uses miss.
- **Shortcut:** a skill sequence that has run 3 times with identical results and every step a hit. It then replays without a model call. It is still verified on every step and aborts on the first non-hit.
- These thresholds are starting points, to be tuned on the first E4 run.

### Prediction and verification

- **Prediction:** the best match among relations on the target, then patterns for the target's kind and verb. If neither matches, the Chooser guesses. Each prediction is labelled with its basis: `instance`, `pattern` or `guess`.
- **Effects** come from a small, game-neutral list: `place_changes`, `text_appears`, `screen_changes`, `entity_appears`, `entity_disappears`, `view_scrolled`, `no_change`.
- **Verdict:** `hit`, `partial` (right kind of effect, wrong details), `surprise` (a different effect), `miss` (nothing happened) or `unobservable`.

### What each model sees

Chosen by code, not similarity search: on screen or in the current place, then tied to the active task, then recent, then important.

| Caller | Budget | Contents |
|---|---|---|
| Perceiver | ~400 tokens | Current place; known names expected here ("reuse these names") |
| Chooser | ~1.2k tokens | Active task; exits from this place; relations for on-screen entities; matching patterns with predicted effects; last 5 actions and verdicts; affordances that already failed here; recent surprises |
| Orchestrator | ~3k tokens | Task tree; place graph and unexplored affordances; least-confident relations; surprises since the last wake; prediction accuracy; pattern candidates counted by code |

The Chooser returns the ids of the memory items it `used`. These are logged so retrieval can be measured.

### Build in stages

- Entities, places, relations, tasks and the trace come first (E2–E3).
- Patterns and shortcuts wait until E4, when there is data to count.

## 9. Goals and tasks

- **Default goal:** expand and test the world model. Prefer untried affordances, unexplored exits and relations with low confidence.
- **Your goals (later):** "go fight a trainer" is added as a task alongside curiosity.
- **Task:** `goal, done_when, fail_when, parent, status, budget, attempts, last_failure`.
- **The harness decides "done"**, from world-model checks confirmed on 2–3 settled frames, never from a model's claim.
- **A task is broken into subtasks only when it fails.**
- **Stuck monitor (code):** no change for N steps, the same action repeated on the same frame, or the budget exhausted. Any of these escalates to the Orchestrator.

## 10. Models

| Role | Candidate | Chosen by |
|---|---|---|
| Perceiver | **Gemma 4 31B (cloud)**. Local qwen3.5:4b was too slow and too weak in E1 (19 s per frame, recall 0.28) | E1 (decided) |
| Chooser | the Perceiver's model, or decider-2b zero-shot over the described affordances | E3 |
| Orchestrator | Gemma 4 31B (cloud, free tier) | fixed |

- **Speed comes first from compiled shortcuts**: verified skill-and-pattern sequences replay without a model call.
- A trained fast model (System 1) is an optional later step. It would be trained on the agent's own trace, never pre-trained per game.

## 11. Measuring

Truth comes from emulator RAM and from spot checks by hand. The agent never sees it.

| Metric | Question |
|---|---|
| Affordance recall | Of the actions possible on this screen, how many were listed? |
| Execution success | Did the chosen action happen as intended? |
| World-model accuracy | Do places, connections and entities match the truth (e.g. the game's warp table)? |
| Prediction accuracy over time | Is it learning? This should climb as patterns form |
| Coverage | How many distinct affordance types and places have been tried? |
| Cost | Model calls and seconds per step; the share of steps handled by shortcuts |

To check that memory is actually useful:

| Metric | Question |
|---|---|
| Memory-ablated run | With memory written but not shown to the models, how much worse are prediction accuracy and steps per goal? |
| Repeated mistakes | How often is an action retried in the same state after it already failed? |
| Retrieval use | What share of the memory shown to a model is reported in its `used` list? Prune what is never used |
| Poison rate | What share of model claims are later refuted, and how quickly? |

## 12. Code

- A new package, `agent/`, is created on branch `feat/lean-agent`.
- Code from `loop/` is **copied and trimmed** into `agent/`, never imported. `loop/` is deleted once M1 is reached.
- Target: about 1,500 lines plus prompts. About 600 of those are plumbing and about 550 are memory.
- Environment: one fresh Python 3.12 venv with `httpx`, `pillow`, `numpy`, `windows-capture`, `pyboy` and `pytest`. OCR and Playwright are optional extras.

| File | Source | Lines (est.) |
|---|---|---|
| `env.py` | new: the adapter protocol plus `ReplayEnv` over saved frames (for E1) | 40 |
| `env_pyboy.py` | new | 60 |
| `env_window.py`, `capture_win.py` | `loop/os/window.py`: DPI awareness, client-area capture via WGC, ImageGrab and PrintWindow, focus handling, black-frame check | 290 |
| `input_win.py` | `loop/os/wininput.py`, `keyboard.py`, `mouse.py`: scan codes, multi-monitor mapping, release-all on start, timed holds | 170 |
| `model.py` | `loop/model/gateway.py`: the Ollama chat client (cloud and local), JSON mode, timeouts | 90 |
| `observer.py` | `loop/os/profile.py` (frame diff), plus new code for upscaling, the grid overlay and a pixel-fraction check for tiny sprites | 60 |
| `brief.py` | `loop/adaptive/game_brief.py`, trimmed | 35 |
| `perceive.py`, `locate.py` | built in E1: the perception prompt and zoom-refine. Rulers and OCR snapping exist only to reproduce E1; neither is used at runtime | 220 |
| `memory.py`, `predict.py`, `tasks.py` | new (§8–§9) | 550 |
| `skills.py`, `loop.py`, `trace.py` | new | 250 |

Things the old code solved that must not be lost:

- Set DPI awareness before any window call.
- Capture the client area, not the whole window.
- Map clicks to the true last pixel.
- Send keys as scan codes. Arrow keys also need the "extended key" flag.
- Never leave a key held.
- Focus the window once, then check it is still in the foreground before sending input. Capture does not need focus.

Gitignored data (`data/samples/`, `runs/`, `.cache/`) stays on disk, outside version control. It is never brought into this branch.

## 13. Build order

| Step | Deliverable | Pass when |
|---|---|---|
| E1 | Affordance perception, zero-shot, on saved STS2 frames now and Pokémon frames once the ROM is available | Affordance recall and position accuracy are good enough to act on; pick the Perceiver model |
| E2 | PyBoy adapter, Observer, the `approach` and `select` skills, trace | A scripted goal ("walk to the door", "pick menu item X") is executed and verified |
| E3 | Full loop from a new game: intro, name "Hand", tutorial text saved | **M1:** starter Pokémon obtained with no human input; world model holds the house, the town and the lab |
| E4 | Curiosity run with patterns and shortcuts | Door → place, trainer → battle and move selection are all executed, and prediction accuracy rises over the run |
| E5 | Second, unfamiliar game | Same metrics with no core code changes |

## 14. Open questions

- **Position from pixels:** can image registration (measuring how far the screen scrolled) give a reliable player position in tile games? To be measured in E2.
- **Cloud dependence:** free-tier throttling. Is a local Perceiver good enough to keep the cloud model for planning only?
- **Generality:** Pokémon Red is in every model's training data. E5 is the real test.
