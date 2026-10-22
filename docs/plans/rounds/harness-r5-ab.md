# Avatar harness round r5: (A) decision-point regression set, (B) movement audit

**Summary (6 lines)**
1. Both items are **measurement first, zero asks**. Neither changes `agent/` code. Fixes that come out of B are proposals, and they wait for user approval.
2. **A** freezes about 10 hard moments from recorded runs (the Oak choice, the nickname offer, the name menus, the name keyboard, house and lab exits). One command replays them with today's code from the nearest checkpoint and checks what the harness offers and says (options, sections, notes, goal, planner text). It costs 0 asks; an optional live mode costs about 1 ask per case.
3. Why A needs a new replay mode: today's whole-run replay (`eval_act.py --om truth`) stops at **step 3** of `m1_r3b_live_150` (4 perceiver cache misses; press divergence at step 4). Live runs use the continuous runtime, so frames never match the cache. A must stub recorded calls **by step and role**, not by cache key, and check RAM truth at each step to catch divergence.
4. **B, what we know (offline, measured):** world movement fails a lot.
   - r3b: 27 of 36 movement rows blocked (exits 2/13 succeeded, unexplored 0/11, things 4/5). w8: 14 of 16 blocked.
   - Position tracking is **not** the cause. The truth − agent-tile offset is constant per place (97–100% of rows), and within-place move-tracking errors are 0 in both runs.
5. **B, leading hypothesis:** `space.route` (`agent/space.py:39-53`) treats every cell with no tile row as walkable. Only actor tiles are obstacles (`World.occupied`), and perceived static things (shelves, counter, table, signs) are not. So a route walks into unseen walls and furniture, learns one blocked edge per push, and fails again. B measures this per failure before anyone proposes a fix.
6. **Ledger:** the user raised the caps to 7854 / 7804 (1522 asks of headroom, enough for about 4–5 full 150-step runs). A-dry and B cost 0. A4 live (about 12 asks) is approved. D1 is done (checkpoints every 5 steps from now on).

| id | title | files | risk | asks | needs user approval |
|---|---|---|---|---|---|
| A1 | Recorded-call replay mode `--om points` (checkpoint, step-keyed stubs, truth divergence check) | `scripts/eval_act.py`, `tests/agent/test_om_eval.py` | low (eval only) | 0 | no |
| A2 | Case file `scripts/points.json` (about 10 cases) built from recorded prompts and truth | `scripts/points.json` | none | 0 | no |
| A3 | Baseline report on HEAD `1a663de`: per case PASS / FAIL / INCONCLUSIVE | none | none | 0 | no |
| A4 | Live act/plan scoring of the rebuilt prompts | none | none | about 12 | approved |
| B1 | Movement-failure classifier `scripts/eval_moves.py` (trace + memory journal + RAM truth) | `scripts/eval_moves.py`, `tests/agent/test_eval_moves.py` | low (eval only) | 0 | no |
| B2 | Audit: per-class counts on r3b, w8, r4_resume100; ranked root causes | none (in the report) | none | 0 | no |
| B3 | Fix proposals with offline counterfactual numbers; **no code** | none (in the report) | none | 0 | approval before any code |
| D1 | Future runs: checkpoint every 5 steps instead of every 25 | `scripts/run_agent.py:243` (user WIP) | low | 0 | done (user approved, uncommitted WIP) |

Order: A1 → A2 → A3, and B1 → B2 → B3. A and B are independent (different files) and can run in parallel. A4 runs only if approved.

---

## Context (no repo access assumed)

As in `harness-fixes-r4.md`: repo `Avatar`, branch `feat/lean-agent`, HEAD `1a663de` (r4: P1b, P2, P1a, P4, P3). Interpreter `.venv/Scripts/python.exe`; tests `-m pytest -n auto -q` (701 passed, 1 skipped). Project rules are unchanged: reuse first, no new abstractions, **no new mechanical gates in `agent/`**, no game names in `agent/` code or prompts (eval scripts may use RAM truth and game facts; `scripts/truth_pokered.py` is the evaluator-only RAM reader), and user WIP stays untouched (`scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, `docs/plans/`).

**Run data** (`runs/<run>/`):
- `trace.jsonl`: one row per step: `step`, `screen`, `place`, `next_place`, `tile` (agent tile after the step), `choice` (`verb`, `target`, `intent`), `execution`, `verdict`, `skill` (`status`, `reason`, `inputs`, `failure` {`stage`, `direction`}), `entities`.
- `calls.jsonl`: every model call, with `step`, `role` (perceiver / act / plan / skill), `system`, `user`, `schema`, `response`, `key`, `cached`.
- `truth.jsonl`: the RAM state *before* step s: `map`, `x`, `y`, `party`, `sprites`, ….
- `presses.jsonl`: `{step, keys}`.
- `memory/*.jsonl`: append journals (`tiles`, `entities`, `places`, `relations`, …).
- `checkpoint/<NNNNNN>/{harness.json, memory/, pyboy.state}` every 25 steps.

**Runs used:**
- `m1_r3b_live_150_20261021_150630` (r3b): checkpoints 0–150.
- `m1_r4_resume100` (r4): rows 1–100 copied from r3b; checkpoints 100, 125 and 147.
- `m1_w8_live_150` (w8): checkpoints 0–150.

**Existing replay machinery to reuse, in `scripts/eval_act.py`:**
- `om_reliability` (line 696): restores a checkpoint (memory, `harness.json`, `pyboy.state`) into a scratch dir, builds `Loop` with a role-routing `Stub`, guards HTTP (`no_http`), captures `act` options, and reads RAM with `truth_read`.
- `om_branch` (line 518): steps from a checkpoint and picks options by label.
- `dry_gates`, `is_right` and `hits` (lines 118–122, 324): right/wrong matching by tool regex and label regex.
- `om_outcomes` / `classify` (lines 462–486): executes an option and classifies the RAM result as crossed_right / closer / no_move.
- `scripts/om_cases.json`: the old 8-case format (`run`, `step`, `truth`, `situation`, `right[]`, `wrong[]`). Reuse that shape.

---

## A: decision-point regression set

### Why the existing replay does not work (measured)
`eval_act.py --om truth --run m1_r3b_live_150_20261021_150630` printed:

```
replay stopped: 4 perceiver cache misses (limit 3), first at step 3, last at step 23
steps replayed=22 of 150; first press divergence=4; perceiver misses=4
```

The recorded live runs used the continuous runtime (`run_agent.py` without `--headless`), so replayed frames differ from the recorded ones and the cache keys miss. Cases must therefore start at a checkpoint, take model replies from the recorded calls by **(step, role, order)**, and stop as INCONCLUSIVE when RAM truth diverges from `truth.jsonl`.

### A1: `--om points` replay mode (`scripts/eval_act.py`)
Add one mode next to `om_reliability` and reuse its restore, stub and guard code. No new module.

For each case `{run, step, checkpoint, …}`:
1. Copy `runs/<run>/checkpoint/<checkpoint>` into a scratch dir (as `_hf5_path` does), then build a headless `PyBoyEnv` from its `pyboy.state`, and `Loop` with `restore(harness.json)`.
2. **Stub, by step.** Index `calls.jsonl` as `{(step, role): [responses in file order]}`. For replayed steps (`checkpoint < s < step`):
   - `perceiver`, `skill` and `plan` calls return the next recorded response for `(s, role)`, parsed as the live code would parse it (reuse `agent.model.parse_json`).
   - With no recorded response left: `plan` returns `{"ops": [], "why": ""}` and is counted as `plan_extra`; `perceiver` or `skill` stops the case as INCONCLUSIVE ("no recorded perception").
   - `act` returns the option whose `action` equals the recorded row's `act.action`, as `om_reliability` does. If it is not offered, the case is INCONCLUSIVE ("recorded choice not offered at s").
   - HTTP is always refused.
3. **Divergence check.** Before each replayed step, compare `truth_read(env)` `map/x/y` with `truth.jsonl[s]`. If they differ, the case is INCONCLUSIVE ("diverged at s"). Report the first divergence step.
4. **At the case step,** capture without acting:
   - the act prompt (system + user) and the option list (`n`, `tool`, `label`, `state`, `notes`);
   - `notebook.render_focus` (the goal line);
   - if a planner call happens on this step, the planner user prompt.
   Then evaluate the case's checks (below). With `--live` (A4), send the captured act prompt (and the plan prompt if the case has plan checks) through the real model with the ledger hook, and score the reply.
5. Print one block per case and a final table: `case | status | failed checks | first divergence | plan_extra`. Exit code 0 only if every case is PASS or INCONCLUSIVE, and the number of INCONCLUSIVE cases is at most the `--max-inconclusive` flag (default 3).

**Checks** (each a list in the case; all optional):
- `right` / `wrong`: the existing `{tool, label_re}` entries. PASS needs a right option offered. With `--live`, the chosen option must be right and not wrong.
- `section`: `{label_re, state}`: the matching option is in state `new` / `worked` / `failed` (the "Not tried / Worked / Failed here" groups).
- `note_re`: `{label_re, re}`: the matching option's notes match `re` (for example `same reply`).
- `goal_re`: a regex on the goal line (for example, it must not match `from PROF\. OAK`).
- `plan_re`: a regex on the planner prompt of that step (for example `BALL`). If no planner call happened on that step, the result is NOT APPLICABLE, not a failure.

**Tests** (`tests/agent/test_om_eval.py`):
- (a) The step-keyed stub returns recorded responses in order and raises INCONCLUSIVE on exhaustion for perceiver.
- (b) The divergence check stops at the first mismatching step.
- (c) Check evaluation on a hand-made option list (section, note, goal, right/wrong).

Use small fakes: no ROM, no runs.

### A2: case file `scripts/points.json`, about 10 cases
Build each case from the recorded data, not from memory. Read the recorded act prompt at that step in `calls.jsonl` to get the exact option labels, and confirm the truth map at that step. Starting set (the executor verifies each step and label and may move a case by ±2 steps to the cleanest frame; it reports every move):

| # | run | step | ckpt | situation | checks |
|---|---|---|---|---|---|
| 1 | r3b | 18 | 0 | player name menu | right `select RED`; wrong `NEW NAME` |
| 2 | r3b | 24 | 0 | rival name menu | right `select BLUE`; wrong `NEW NAME` |
| 3 | r3b | 36 | 25 | 2F, leave by the stairs | right `stairs`; outcome check optional (see B) |
| 4 | r3b | 51 | 50 | 1F, leave by the door mat or rug | right `door mat\|rug\|exit` toward map 0 |
| 5 | r3b | 112 | 100 | lab, "which POKéMON do you want?" | `plan_re` `BALL`; right `display case\|ball\|sphere\|table`; wrong `talk to OAK` |
| 6 | r3b | 139 | 125 | lab talk loop, 8th repeat | `section` talk to OAK = `failed`; `note_re` `same reply`; wrong `talk to OAK` |
| 7 | r4 | 119 | 100 | lab, ball reachable | right `ball` |
| 8 | r4 | 126 | 125 | "give a nickname?" | right `select NO`; wrong `select YES` |
| 9 | w8 | 113 | 100 | open name keyboard | right `ED\|END\|OK`; wrong `select [A-Z]$\|advance` |
| 10 | r4 | 136 | 125 | after the starter, leave the lab | right exit toward map 0; record the outcome class |

Case 6 also tests P2 and P4 offline for the first time: by step 139, today's code should show talk to OAK as failed with `same reply`.

### A3: baseline report on HEAD
Run `--om points` (dry) on HEAD `1a663de`. Report the table. Every INCONCLUSIVE needs its reason (divergence step, or missing recorded call). This table is the number that every later round must not make worse.

### A4: optional live scoring (needs user approval, about 12 asks)
`--om points --live`: one act ask per case, plus one plan ask for case 5. Report the choice per case. It is the cheapest live signal that exists. It is not approved by default.

---

## B: movement audit

### What is already measured (offline, 0 asks)
| run | movement rows | blocked or failed | exit success | unexplored success | thing success | longest failed streak |
|---|---|---|---|---|---|---|
| r3b | 36 | 27 | 2 / 13 | 0 / 11 | 4 / 5 | 11 (steps 57–67, town) |
| w8 | 16 | 14 | 1 / 8 | 1 / 7 | n/a | 5 (steps 44–48, 1F) |
| r4 101–147 | 9 | 5 | 0 / 2 | n/a | 3 / 6 | 2 |

- Top reasons: `route blocked` (r3b 17, w8 8); `travel: leg 1 route blocked` (6, 5); `exit mismatch` (3); `scene cut` (2–3); `exit not crossed` (1–2).
- Position tracking: the truth − agent-tile offset is constant per (place, map). r3b `p_place_5`/40 (5,3) 97%, `p_place_4`/0 (5,6) 100%, `p_place_3`/37 (7,1) 100%, `p_place_2`/38 (3,6) 57%, the only off-mode rows being steps 46–48 after a crossing. w8: 100% for all four. Within-place move-tracking errors: 0. So routes fail against the real map, not against a drifted position.
- Code facts (`agent/space.py:39-53` `route`): a cell with no tile row is passable, a cell is excluded only when `floor is False` or the edge is in `blocked`, and the only other obstacles are `occupied` = actor tiles (`agent/world.py:1133`). `fold` learns `blocked` only from a push with no shift. Perceived static things (furniture, signs) never become obstacles.

### B1: classifier `scripts/eval_moves.py <run> [--from S --to S]`
This is a new evaluator script. It reads, and never writes, run data. For each world movement row whose `execution` is not `done`:
1. **Blocked cell.** The agent tile where the walk stopped: replay `presses.jsonl` against the truth positions, or take the step's truth after (`truth[s+1]`) mapped into agent coordinates with that place's mode offset. Add `skill.failure.direction` to get the blocked cell.
2. **Memory state at that step.** Rebuild it from the `memory/tiles.jsonl` and `memory/entities.jsonl` journals (rows up to step s). If the journals are not step-ordered append logs, use the nearest earlier `checkpoint/*/memory` and say so.
3. **Class** (the first that applies):
   - `unknown_cell`: the blocked cell had no tile row (the route assumed it was walkable).
   - `static_thing`: a non-actor perceived entity (`kind` not npc/character/person/player) had its tile on the blocked cell.
   - `actor`: a RAM sprite (`truth.sprites`) stood on the blocked cell, or an actor tile was there.
   - `known_floor`: the cell was a known `floor: True` cell, yet blocked (a ledge, a one-way tile or a tracking issue).
   - `crossing`: the failure stage was `crossing` (an exit push did not warp: `exit not crossed` / `exit mismatch`).
   - `other`: with the reason text.
4. **Waste:** presses spent on failed rows; the number of consecutive failures with the same target.
5. **Explore rows:** for each `explore:*` row, where its frontier target was, and whether that target was reachable at all according to later truth (did RAM ever reach that map cell in the run).

Output: one line per failed row (`step | intent | class | blocked cell (agent) | blocked cell (RAM) | reason`), then counts per class, per target kind, per run.

**Test** (`tests/agent/test_eval_moves.py`): a small hand-made trace, truth and tiles journal in `tmp_path`, with one row of each class. The classes come out right.

### B2: audit (in the report)
Run B1 on r3b (1–150), w8 (1–150) and r4 (101–147). Report:
- the class table;
- the top three root causes by failed rows and by wasted presses;
- the town streak 57–67 explained row by row;
- whether `unknown_cell` + `static_thing` together cover most `route blocked` rows, which would confirm the hypothesis in the summary.

### B3: proposals, no code
For the top one or two classes, propose the smallest generic change (at most two options each). Give the offline counterfactual for each: how many of the measured failed rows it would have avoided. For example:
- **For `static_thing`:** count the failed rows where the blocked cell held a perceived static thing that the route could have avoided.
- **For `unknown_cell`:** compare routing that prefers known floor first and falls back to unknown cells (as `strict` already does for inferred exits).

Respect the rules: options are never removed, and there are no game names in `agent/`. **Stop after B3 for user approval.**

---

## D1: checkpoint every step (user decision; user edits `scripts/run_agent.py:243`)
`if loop.step_no % 25 == 0: _checkpoint(env, loop, run)` → every step (or every 5) would make every future step a case without replaying forward, and removes most INCONCLUSIVE cases. Cost: one `pyboy.state` + `harness.json` + `memory/` copy per step. Measured: `checkpoint/000100` of r3b is **1.2 MB**, so about 180 MB per 150-step run if every step is kept, or about 36 MB every 5 steps.

Note for B1: `memory/tiles.jsonl` in r3b has 116 lines for about 116 cells, so it looks like a per-id snapshot, not an append journal. Expect to use checkpoint memories (every 25 steps) plus each row's `step` stamp to bound the state at step s.

## Report format
The plan table with a status per row, the A3 table, the B2 class tables, the B3 proposals with counterfactual numbers, asks spent (expected 0), and the files edited.
