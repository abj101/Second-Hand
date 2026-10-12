# Autonomy redesign: let the model set goals and act (proposal, 2026-10-12)

Status: APPROVED 2026-10-12 (all §6 recommendations; gemma4:31b). P0 + P1 built (222 tests pass, uncommitted); P2 next. Inputs: `.tmp/research/prompting-survey.md` (external survey), `.tmp/research/gates-inventory.md` (88 behaviour rules, 46 bypass the model), stage-M runs 55-94.

## 1. Why

- In runs 55-94 the model chose only ~31% of actions; the rest were code: a default goal ("Reach a place not visited yet", `tasks.py`), a forced "goal-directed target test" (`loop.py:64-84`), a walls-first/nearest ranker (`next_test`), candidate filters, wall-bump commits, frontier pushes, and overrides in `choose()`.
- Those gates produced the observed failures: shelf/desk loops, re-entering the house, and being stuck in a closed 2F because the template goal forbade going back through visited places.
- The model never sees a reason to go anywhere: the planner may only emit `new_place / text_seen / entity_known / relation / steps_over` conditions, and is told to "prefer new_place for travel". Dialogue like "Go see Prof. Oak" cannot become a goal.
- External evidence (survey §2): model-written goal stacks/notes beat fixed curricula (Voyager, Cradle, Gemini/Claude Plays Pokémon); harnesses got *better* by removing directives and helper sub-agents as models improved; the main failure is notes treated as truth, fixed by source tags and re-verification, not by rules.

## 2. Target shape

One loop step = **perceive** (existing, cached ~90%) → **act** (new, replaces chooser + planner + code choosers) → **execute skill** → **verify** (existing) → facts back into context.

### 2.1 Notebook (model-owned, code-stored)
A small persisted JSON doc the model edits every act call via ops (`add/update/remove` by key). Code applies ops, caps size (~1,500 tokens), keeps history for rollback.
```
goals:   [{id, text, why, source: saw|heard|assumed, evidence: "quoted text or step", status: active|done|dropped}]
places:  [{name, looks_like, exits: ["stairs → Bedroom", ...]}]
hints:   [{text, source, step, checked: bool}]
lessons: [short Reflexion-style notes, max 10]
```
Top goal is fixed by the system prompt ("finish the game"); everything below it is the model's.

### 2.2 Act call (one structured call per step)
System prompt (game-agnostic, replaces SYSTEM/GENERAL/planner prompt):
> You are playing a video game you have never seen, from screenshots only. Your goal is to finish the game. Work out what to do next the way a new player would: read every piece of on-screen text; when dialogue, signs, tutorials or menus tell you where to go or what to do, turn that into a goal in your notebook with the quote as evidence. Treat your notes as hypotheses: prefer what you see now over what you wrote earlier, and fix notes that turn out wrong. If you are stuck, try something different and write down what you learned.

Context: screenshot, perception (text, entities, affordances), notebook, verified facts from memory (known exits with destinations, recent actions with verdicts, refuted claims), code flags (`loop: same action 3x`, `no progress 25 steps`, `route blocked`), controls, user preferences (player name, rival name — stated as preferences, not overrides).

Reply:
```
{"place": "<notebook place name or new name>", "notebook": [ops], "action": {"i": <affordance index>} | {"buttons": ["UP","UP","A"]} | {"go_to": "<entity or exit name>"},
 "expect": "<effect>", "why": "..."}
```
`go_to` uses the existing route/approach skills as a *tool the model invokes*, including deliberately walking back through a known exit.

### 2.3 Place identity → model-asserted
`place` from the act reply becomes the primary place key on scene cuts (code stores names + aliases + exit edges). Fixes issue 4 (2F under two ids) with zero extra calls; landmark Jaccard stays only as a fallback.

### 2.4 Perception prompt
Keep. Remove the push to invent exits (`Use a bare screen-edge exit…`): name only visible doors, stairs, mats, openings. Invented "exit: path south" rows were the main remaining duplicate source.

## 3. What gets removed / kept

| Remove (gating; inventory id) | Keep (infrastructure) |
|---|---|
| Default goal + new_place done logic (B1, B2, B6) | Memory store, entity/place dedupe, tiles, exit edges |
| Task condition machinery, chaining, caps, filters (B3-B10, B33, B34) | Skills: approach, route, interact, select, type, press (as tools) |
| Forced target test, candidate build/filters, next_test, commit, frontier push (B13-B18, B20, B23) | Effect verification + verdicts, reported as facts |
| Curiosity override, custom-name override, defaults-forced type text, exit-effect forcing (B29-B32) | Loop/stall detectors (B54-B56) → flags in context, not replans |
| Planner wake triggers/throttle (B11, B12) — notebook is updated every act call | Budgets, ledgers, checkpoints, cache, compaction |
| `escalate`, `judge.py`, `decider_worker.py`, `code_choice` (dormant) | Perception cache; one-option shortcut (B26) only for dialogue advance |

Expected net code change: large deletion in `loop.py`, `decide.py`, `tasks.py`; additions ≈ notebook (~40 lines) + act prompt/parse (~40 lines).

## 4. Risks

- **Cost.** Today ~1.39 uncached requests/step because code picked 69% of actions for free. One act call per step → ~1.5-2.0/step. Mitigations: `buttons`/`go_to` let one decision cover many inputs; skip the act call while a multi-step skill is still progressing; perception cache unchanged.
- **Model strength.** The survey's scaffolding-removal wins came with frontier models; we run `gemma4:31b-cloud`. It may waver without goals → the notebook goal stack is exactly the documented mitigation, but a stronger model may be needed. Measure before deciding.
- **Notebook poisoning.** Source tags, `checked` flags, "prefer what you see now", rollback history; one cheap audit every ~50 steps only if needed.
- **Context growth.** Notebook capped; recent events windowed; no raw history.

## 5. Plan (each phase gated by your approval)

| Phase | Work | Calls |
|---|---|---|
| P0 | Memory fixes 3 + 4-hygiene (in progress) | 0 |
| P1 | Notebook + act call + model place names behind `--act` flag; old path untouched; StubModel tests | 0 |
| P2 | Prompt eval on ~15 recorded frames from runs/m1 (Mom/Oak dialogue, 2F, Town): does the notebook gain goals from text, sensible actions, consistent place names? | ~15-30 |
| P3 | Live 40 steps from checkpoint 74 with `--act`; compare progress/cost vs runs 55-94 | ~60-80 |
| P4 | If P3 is better: delete the gated path and dead code (table §3), update tests/contracts | 0 |

## 6. Decisions for you

1. Accept ~1.5-2 requests/step in exchange for model-driven choice? (Alternative: act call only on scene change / new text / failure, skill continuation otherwise.)
2. Names: keep "Hand"/"Blue" as stated preferences in the prompt (recommended, matches existing decision) or let the model choose.
3. Keep the old path behind a flag for one comparison (recommended), or replace it outright.
4. Model: stay on gemma4:31b-cloud for P2/P3, or include one stronger-model comparison in P2.
