# Handoff: plan round r8 (bugs and architectural gaps after r7)

You are an Opus **planning** session for the Avatar harness: `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, HEAD `d81df01`. The deliverable is a plan, not code.

The agent plays Pokémon Red through a generic loop: observe → read → decide → act → verify. A cloud vision model (Ollama, `OM_MODEL`) does perception and acting. The milestone is **M1: get the starter Pokémon**. Not yet passed from a clean run; r4's resume got it once (step 126).

## Your deliverable
1. **`docs/plans/harness-r8.md`**, in the same shape as `docs/plans/harness-r7.md`:
   - summary;
   - an item table (id, title, files, asks, approval);
   - evidence, root cause, change and tests for each item;
   - options where the approach is open;
   - order;
   - a validity check that names the cited code lines on HEAD.
2. **`docs/plans/chatgpt-execute-r8.md`**: the executor prompt, in the shape of `chatgpt-execute-r7.md`:
   - standing rules and the list of files it may edit;
   - the approved assertion changes;
   - the ask caps;
   - the worker split and the report format.

   A ChatGPT session executes it. Claude reviews.
3. **Do not write code.** For each architectural gap, present **2–3 options with a recommendation and wait for the user to pick**. Only the small, clear bugs may go in as "approved pending review".

## Read first
- `docs/plans/harness-r7.md`: last round's plan. The r6 evidence explains why V1, S and G exist.
- `docs/plans/chatgpt-execute-r7.md` and `docs/plans/chatgpt-execute-r7b.md`: the executor format and the resume addendum.
- `.tmp/r7/`: the executor's scratch output for r7:
  - `progress.json`: commits, suites, notes;
  - `gm-verdict.json` and `gm-verbatim.md`: the G1 micro-check;
  - `lv-*.txt`: LV eval-run, eval-moves and eval-entities;
  - `lv-case11*`: case 11 from LV;
  - `a3-dry.txt`;
  - `*-golden-proposal.diff`.
- Runs:
  - `runs/m1_r7_resume115` (LV: r6 resumed from 115, r7 code, 35 steps);
  - `runs/m1_r6_live_150` (r6 from boot);
  - `runs/m1_r3b_live_150_20261021_150630`.

  Each has `trace.jsonl`, `truth.jsonl` (RAM truth; never visible to `agent/`), `calls.jsonl`, `presses.jsonl`, `memory/` (`notebook.json`, `entities.jsonl` journal, …) and `checkpoint/NNNNNN/` every 5 steps.
- Tools (all 0 asks):
  - `scripts/eval_run.py <run>`;
  - `scripts/eval_moves.py <run-basename>`: classifies movement failures;
  - `scripts/eval_entities.py <run> [--from --to]`: entity stability (SD);
  - `scripts/eval_act.py --om points` (`--dry`): regression cases from `scripts/points.json`;
  - `scripts/eval_act.py --om act-size --run R --from-step S`.
- **CodeGraph:** `.codegraph/` is indexed. Use `codegraph_explore` (MCP) or `codegraph explore "<names>"` before grep or Read.

## State after r7
Commits since r6 (`87eb424`):

| commit | item |
|---|---|
| `0bdd6e6` | V1: a repeated reply no longer closes a quest |
| `55fc942` | act-size fix |
| `aea199a` | V3: a name shared across places resolves to the current place's thing |
| `537632d` | SD: `eval_entities.py` |
| `1bb97b6` | SB: static rows are never `gone` from misses; a tile moves only on 2 agreeing sightings |
| `4decea8` | SA: name and kind follow a 3-of-6 recent majority |
| `49c52b6` | SC: a remembered thing in view is offered like a seen one |
| `d81df01` | G3: the newest cited text is shown whole in the act goal |

- **G1** (game words in the perceiver prompt) was **rejected by GM**. Lab BALL 8/8, but control frames gave 4/8 false names: a town NPC named "BLUE", OAK named "RED".
- **Suite:** 729 passed, 1 skipped. Always run `-n auto`.
- **Ledger** (`runs/e3_budget.json`): total 6666 of cap 7854, so **1188 asks left**. Caps are raised only with user approval.
- **LV result:** no starter by step 150. The r6 talk-to-Oak loop is gone, but most steps were blocked routes.
  - **Case 11 (V2) has no passing cut.** From LV checkpoint 130, step 135, OAK still reads `worked` (cause: B1).
  - **A3:** 7 PASS, 3 INCONCLUSIVE.
  - The user's uncommitted `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json` and `baseline_eval.txt` must stay untouched.

## Bugs (verify each against the code and data before planning)

| # | Bug | Evidence | Where |
|---|---|---|---|
| B1 | **Same reply needs an exact match.** A line read at a different page or typing point is "new", so V1 never applies. | LV `notebook.json` t10 @131 `Now, RED, which POKéMON do you want?` vs t11 @134 `Now, RED, which POKéMON do ▼`. Proposed fix: after stripping ▼ and whitespace, one text being a prefix of the other counts as the same. | `agent/loop.py:290-295` |
| B2 | **A thing's first reply closes any quest that targets it**, whatever the reply says. | LV q11 "Select a POKéMON…" targeted `thing:e_player`. The Pokédex text at 140 ("encyclopedia-like…") closed it, reason `talked`. | `Notebook.heard` → `talked` |
| B3 | **SA raised churn.** | LV 116–150: 51 name flips and 158 kind flips (up to 12 per id); r6 had 0 / 0. OAK (`e_item_1`) was shown as "shopkeeper", "OAK", "NPC" and "item". | `Memory.observe` (SA) |
| B4 | **SB did not stop tile drift.** | Static tile moves: 102 in LV vs 55 in r6 for steps 116–150. | `Memory.observe` (SB) |
| B5 | **Route-blocked steps get verdict `progress`**, so `fails` and the stuck replan never trigger. | LV 118–120, 124, 141–143, 148–150. | verdict in `Loop._settle` (`loop.py` ~334-337, `action_result`) |
| B6 | **An adjacent actor is labelled "not on screen".** | LV 137, 141 and 147: `go to OAK, not on screen` right after talking to OAK. | `World.options` / SC |
| B7 | **Routes go through unseen tiles.** | `eval_moves` on LV: `unknown_cell` 29 failed rows, 168 wasted keys. `go to door 5 right 5 up` was blocked at 121, 125 and 127. | `route` / tile map |
| B8 | **The points replay is brittle.** Perception is keyed by (step, role) and choices by entity index, so it misaligns around dialogue and once behaviour diverges. | Case 11 from r6/135 is INCONCLUSIVE at 137; r6 144 from 140 sees a dialogue frame; 3 of 10 A3 cases are INCONCLUSIVE. | `scripts/eval_act.py` `PointsStub`, `_points_case` |
| B9 | **act-size on old checkpoints can't show S's effect** (their rows lack the new fields). | 1435 chars before and after. | measurement method |

## Architectural gaps (options first, user picks)
1. **Grounding: game words ↔ on-screen things.** G1 (prompt-level) over-labels. G2 (aliases in labels) was never tried. Something needs evidence-checked naming, e.g. accept a game word for a thing only after interacting with it produced text naming it, or only when the text and the thing co-occur at its tile. This is the actual M1 blocker (the ball table is still "display case" or "table").
2. **Identity comes from per-frame perception.** Memory only smooths it (SA/SB), and smoothing noisy input churns. The pixel tracker (`agent/track.py`, OT `af4b75c`) exists but does not own identity. Consider tracker-anchored identity, with perception as attributes.
3. **The tile map isn't geometrically consistent.** "Explore 10–15 steps" inside a small lab, routes through unseen cells, tiles projected from a noisy player anchor. Nothing checks against room bounds or walls. The user prefers cheap spatial memory over cloud calls.
4. **The verdict is generous.** "Progress" comes from pixel or text change, not from getting closer to the goal; stuck detection and `fails` inherit this.
5. **Quests close on side-effects** ("talked", "reached", "crossed") without checking the quest's outcome. P2 and B2 are the same flaw.
6. **The eval can't follow changed behaviour (B8).** Regression cases can only come from runs on current code. Consider sequence-aligned or frame-hash-keyed perception replay.

## User preferences (from memory; follow them)
- **Root cause over band-aids.** Architectural fixes; **proposals before code**; ask before any new approach.
- **Agent autonomy over gating.** No new mechanical gates. The agent infers goals from game text and acts freely.
- **Generic.** No game names in `agent/` code or prompts.
- **Small changes, reuse first** (the ponytail ladder). Report line counts; don't enforce line budgets.
- **Minimal cloud asks.** Micro-checks on recorded frames before any live run. Every live run has a cap and is charged to the ledger.
- **Options-only act output.** The act model picks from the given options as strict JSON.
- **Commit at gates**, one commit per item, explicit paths only.
- **Workers:** Haiku for mechanical tasks; the planner and reviewer do the judgment. Tests are always `pytest -n auto`.
- **Never** use junctions or links for `runs/` or `Rom/` in worktrees.

## Suggested shape for r8
- **Quick bug items (0 asks):** B1, B5, B2, then V2 (case 11 re-cut from LV after B1).
- **Decision items:** the B3/B4 rethink, tied to gap 2; grounding (gap 1); the tile map (gap 3). Each needs options, a 0-ask offline measure on r6/r3b/LV, and a micro-check design costed in asks.
- **One capped LV** after the chosen items land, resumed from r6/115 like r7. Success: a party by step 150.

The user decides scope and order. Keep the plan concise, in the r7 style.
