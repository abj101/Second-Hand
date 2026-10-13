# P3b: With player identity, place cuts and action history fixed, does the act path beat the gated agent live?

Status: not started · Branch: feat/lean-agent · Program: [program-r1-wm.md](program-r1-wm.md) row 6b (STOP 3b after) · Follows: [P3.md §9](P3.md)

## Why this phase exists (user decision 2026-10-12, STOP 3)

P3 did not test the act path; one perception error ran the whole window. Evidence from `runs/m1_act` (0 requests to re-check):

- **Role swap (steps 76–114).** Recorded perception at checkpoints 75 and 100 has `npc` at (0.45, 0.47) and `player` at (0.25, 0.81). `World.anchors` in the same checkpoints has a median of about (0.45, 0.47): the "npc" **is** the player. `World._anchor` already rejects the bad point for `World.pos`, but `skills.run_affordance` (`agent/skills.py:219`) and the act prompt read the raw perception, so the avatar was told to walk to itself. Frames were byte-identical for 77–114, so the cached wrong perception was reused 39 times.
- **Cut kept the place (step 75).** Trace: `scene_cut: true`, skill `scene cut` via `exit: path east`, `exit: null`, `next_place: p_world_2 == place`. So no exit edge or via edge applied, and `Memory.match_place` (`agent/memory.py:86`) fell through to landmark overlap. That overlap is allowed to return `prev`, so p_world_2 got a self-loop edge `exit: path east -> p_world_2`.
- **The model never learns its own history.** `act()` gets only one-step `flags` (`same_action`, `last action blocked: both axes blocked`, `verdict miss`). It never sees what it did on earlier steps, so 38 identical misses read like one.

P3b fixes those three root causes and reruns the P3 comparison unchanged. P4 stays blocked until the user decides at STOP 3b.

## 1. Entry criteria
- P3 Result filled in (it is) and the user chose P3b at STOP 3 (2026-10-12).
- On disk: `runs/m1/` with `checkpoint/000074/`, `runs/m1_act/` (P3 evidence; read only), the ROM, `.cache/model/`. Ledger: M1 bucket ≥ 80 left (411 on 2026-10-12). Ollama reachable.

## 2. Gate

Baseline = `runs/m1` steps 75–94 (unchanged). Candidate = `runs/m1_act2` steps 75–114, resumed from checkpoint 74 with `--act`.

**Offline gate (0 requests; must pass before the live run):**

| Check | Pass | Measured by |
|---|---|---|
| Role fix on recorded data | on every `runs/m1_act` checkpoint ≥ 75 with a world perception, the fixed perception has exactly one `player` within 1 tile of the anchor median | `scripts/check_world.py roles runs/m1_act` |
| Cut fix on recorded data | replaying `match_place` for step 75 (before = checkpoint 74's place, after = checkpoint 75's perception, `cut=True`) returns an id ≠ `p_world_2` | `scripts/check_world.py cut runs/m1_act 75` |
| Suite | `tests/agent` green, W0 act golden unchanged or regenerated with a reviewed diff | full suite |

**Live gate:** the same table as [P3 §2](P3.md), same thresholds, same evaluator (`scripts/eval_run.py`), plus:

| Metric | Report | "Better" if |
|---|---|---|
| Steps whose perceived player is > 1 tile from truth sprite slot 0 (on world screens) | candidate | 0 |
| Scene cuts whose `next_place == place` | candidate | 0 |
| Act calls whose prompt contained the recent-actions line | candidate | all steps after the first |

- **Decision this gate produces (user, at STOP 3b):** delete the gated path (P4) if "better" holds on P3's first three rows, otherwise report and let the user choose again (stronger-model comparison, or stop the act path).
- **If the offline gate fails:** fix within the WP once (one repair round), else stop and report. No live run.
- **If the live run aborts** (exit 3 or 4, crash): checkpoint, report, stop. No second run without the user.

## 3. Read list
- [P3.md §9](P3.md) (bugs and numbers), `agent/loop.py` (`step`: where `_see`, `match_place`, `world.name`, `act`, `self.flags` meet), `agent/world.py` (`_anchor`, `before`, `name`, `view`), `agent/memory.py` (`match_place`, `TRANSIENT`), `agent/skills.py` (`approach`, `run_affordance`), `agent/act.py`, `agent/tasks.py` (`Stuck`).
- `scripts/check_world.py`, `scripts/eval_run.py`, `scripts/truth_pokered.py` (`sprites`).
- [W4.md](W4.md) §2, only to avoid overlapping it: P3b uses the existing anchor history; W4 still adds motion-based classification.

## 4. Contracts
- **Uses:** contracts.md#notebook-and-act (`flags` carries code observations as text), #world, #memory (`match_place`), #trace.
- **Provides:** no signature changes. `act()` is unchanged: recent actions travel as one `flags` string. If any WP finds it needs a contract change, stop and report the proposed text.

## 5. Work packages

| WP | Goal | Owns files | Depends on | Tier | Est. |
|---|---|---|---|---|---|
| P3b.1 | Player identity comes from the learned screen anchor, not the perceiver's label | `agent/world.py`, `agent/loop.py` (call site only), `agent/skills.py` (self-target reason), `tests/agent/test_world.py`, `tests/agent/test_loop.py` | — | gpt-6-luna high | ½ d |
| P3b.2 | A scene cut never lands in the place it left | `agent/memory.py` (`match_place`), `agent/world.py` (`name` guard), `tests/agent/test_memory.py`, `tests/agent/test_world.py` | — | gpt-6-luna high | ½ d |
| P3b.3 | The act call sees its own recent actions and outcomes | `agent/loop.py` (flags line), `tests/agent/test_loop.py` | — | gpt-6-luna high | ¼ d |
| P3b.4 | Offline checks on the P3 run (`roles`, `cut`) | `scripts/check_world.py` | P3b.1, P3b.2 | gpt-6-luna high | ¼ d |
| P3b.5 | Evaluator: role-error steps vs truth, cut self-loops, recent-line presence | `scripts/eval_run.py`, `tests/agent/test_eval_run.py` | — | gpt-6-luna high | ¼ d |
| P3b.6 | The live run | `runs/m1_act2/` | P3b.1–5 + offline gate | orchestrator | 1 h |
| P3b.7 | Compare and report | `.tmp/p3b/*` | P3b.6 | orchestrator | ½ h |

**Run order:** P3b.1 → P3b.2 → P3b.3 (sequential: all three touch `loop.py` or `world.py`) ‖ P3b.5 → P3b.4 → offline gate → P3b.6 → P3b.7

### P3b.1: player identity from the anchor
- **Goal:** every perception the loop uses has the player where the learned anchor says, so skills and the act prompt get the right self.
- **Change:**
  - One `World` method (e.g. `fix_player(per, size) -> per`), applied in `Loop.step` right after **both** `_see` calls (before and after the action), before anything reads `per`. Apply it to the cached perception too, since `_see` returns the cache on identical frames.
  - Rule, only on `screen == "world"` with ≥ 3 anchors and a known `tile_px`: if the perceived `player` is within 1.5 tiles of the anchor median, do nothing. Otherwise relabel. The non-player entity nearest the median within 1 tile becomes `kind: "player"`, and the old `player` entity becomes `kind: "character"`. If no entity is near the median, move the `player` entity's `at` to the median.
  - Record the correction in the trace row (`"player_fix": {"from": [...], "to": [...]}`) so the evaluator can count it.
  - `skills.approach`: when the target is within ½ tile of the player, return `blocked` with reason `target is the player` and 0 inputs. This reports a fact; nothing is filtered.
  - `# ponytail:` comment at the rule: it assumes a camera that follows the player (fixed screen spot); W4's motion anchors replace it for fixed-screen games.
- **Acceptance:** unit tests in `test_world.py` for swap, move-to-median and no-op cases (no anchors, non-world screen, player already right); `test_loop.py`: a GridEnv step whose stub perception swaps roles approaches the right target. `pytest tests/agent/test_world.py tests/agent/test_loop.py -q`.
- **Risks:** a real NPC standing next to the player within 1 tile can be picked as the player. Mitigation: pick only when the perceived player is > 1.5 tiles off and the candidate is the nearest to the median.

### P3b.2: a cut never returns the place it left
- **Goal:** after a real scene cut, identity resolves to a different place: a known exit or via edge, a landmark match among *other* places, or a new id.
- **Change:**
  - `Memory.match_place`: when `cut and prev`, exclude `prev` from the landmark-overlap search. Exit and via edges that point at `prev` (self-loops) are ignored too.
  - `World.name`: when this step's place came from a cut out of `prior`, a model name that resolves to `prior` does not merge back (the model copies labels like `world [p_world_2]` from the view). It becomes an alias candidate, as in the existing `merge_pending` path.
  - First, confirm the cause on recorded data (0 requests): replay `match_place` with checkpoint 75's perception and checkpoint 74's place rows, and write which branch returned `p_world_2` into the Result. If it was not the landmark branch, fix the branch it was and report the deviation.
- **Acceptance:** `test_memory.py`: a cut with identical landmarks returns a new id; a self-loop edge is ignored; the existing non-cut and known-exit cases are unchanged. `test_world.py`: a name equal to the left place does not merge. `pytest tests/agent/test_memory.py tests/agent/test_world.py -q`.
- **Risks:** a cut that truly returns to the same place (warp in place, fade within one map) now creates a duplicate. That is acceptable: W1's merge reunites places the model later names the same, and a duplicate is cheaper than a false merge.
- **Out of scope here:** dialogue/menu screens recorded as places (`p_dialogue_box_2` is really 1F). Report how often it happens in the P3b run. It gets its own fix only if the user asks.

### P3b.3: recent actions in the act call
- **Goal:** the act model sees what it already tried and what happened, compressed so repeats are obvious.
- **Change:** in `Loop.step` (act mode), add one flags string built from `self.recent` (last 5 trace rows, already kept). Group runs of the same verb and target: `recent: approach npc -> blocked (both axes blocked) x5 since step 77; screen unchanged x5`. Use plain words, no detector ids. Keep the existing flags. The view and the `act()` signature stay as they are.
- **Acceptance:** `test_loop.py`: after three identical blocked steps the act prompt contains `x3` and the reason. `pytest tests/agent/test_loop.py -q`.
- **Risks:** prompt growth. One line, capped at 200 characters.

### P3b.4: offline checks
- `scripts/check_world.py roles <run>`: for each checkpoint ≥ 75 with a world perception and anchors, apply `fix_player` and print before/after player positions plus the pass/fail line for the offline gate.
- `scripts/check_world.py cut <run> <step>`: replay `match_place` on copies (never the original memory) and print the returned id and branch.
- **Acceptance:** both commands exit 0 on `runs/m1_act` and meet the offline gate rows.

### P3b.5: evaluator fields
- `player_role_errors`: world steps where the trace's player position (after `player_fix`) is > 1 tile from truth sprite slot 0, using the existing stationary offset method. Null if truth has no sprites.
- `cut_self_loops`: steps with `scene_cut` and `next_place == place`.
- `recent_line_steps`: act steps whose recorded flags contain `recent:`.
- **Acceptance:** `pytest tests/agent/test_eval_run.py -q`; `eval_run.py runs/m1 --no-write` and `eval_run.py runs/m1_act --no-write` exit 0 with earlier fields unchanged. P3's run should show `cut_self_loops ≥ 1` and `player_role_errors ≥ 38`, which confirms the metric.

### P3b.6: the run
1. Copy `runs/m1` → `runs/m1_act2` (whole directory; the name keeps the M1 ledger bucket). Never modify `runs/m1` or `runs/m1_act`.
2. Record the ledger, then:
   `.venv-agent/Scripts/python.exe scripts/run_agent.py --run runs/m1_act2 --resume --from-step 74 --steps 40 --max-calls 80 --stage c --act`
3. Exit 0 → continue. Exit 3/4 → report what ran and stop.

### P3b.7: compare
- `eval_run.py runs/m1 --no-write`, `eval_run.py runs/m1_act`, `eval_run.py runs/m1_act2` → `.tmp/p3b/{baseline,p3,candidate}.json`. Use the same windows as P3.
- `.tmp/p3b/compare.md`: P3's table with **three** columns (baseline, P3, P3b), the new rows, every repeat/reentry case, the 5 most informative P3b steps (frame, act `why`, flags line, notebook ops, outcome), and the final notebook.

## 6. Test layers
| Layer | Gates this phase? | Command |
|---|---|---|
| Unit | yes | `pytest tests/agent -q` |
| Offline replay on P3 data | yes | `check_world.py roles/cut runs/m1_act` |
| PyBoy live (ROM + model) | yes | `run_agent.py ... --act` |
| Metrics | yes | `eval_run.py` |

## 7. Budgets
- **Lines:** report only (budgets suspended). Expected `agent/` ≈ +40, `scripts/` ≈ +60.
- **Cloud calls:** ≤ 80, M1 bucket, stage c. Expected ≈ 45.

## 8. Out of scope
- `loop/`, contracts.md, deleting the gated path (P4), motion anchors (W4), dialogue-as-place cleanup, perceiver prompt edits, model changes, a second live run.

## 9. Result (filled in by the orchestrator)
- Gate (offline + live), each metric with its number and command; P3b.2 branch diagnosis; decision pending at STOP 3b; deviations; line counts; requests and ledger before/after; open issues.

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` per [_template.md §10](_template.md).
