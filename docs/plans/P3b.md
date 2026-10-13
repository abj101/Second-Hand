# P3b: Are player identity, place cuts and action history fixed on recorded data?

Status: gate passed (offline; completed by the user's Claude session, 2026-10-12) · Branch: feat/lean-agent · Program: [program-r1-wm.md](program-r1-wm.md) row 6b (continue to P4) · Follows: [P3.md §9](P3.md)

## Why this phase exists (user decision 2026-10-12, STOP 3)

P3 did not test the act path; one perception error ran the whole window. Evidence from `runs/m1_act` (0 requests to re-check):

- **Role swap (steps 76–114).** Recorded perception at checkpoints 75 and 100 has `npc` at (0.45, 0.47) and `player` at (0.25, 0.81). `World.anchors` in the same checkpoints has a median of about (0.45, 0.47): the "npc" **is** the player. `World._anchor` already rejects the bad point for `World.pos`, but `skills.run_affordance` (`agent/skills.py:219`) and the act prompt read the raw perception, so the avatar was told to walk to itself. Frames were byte-identical for 77–114, so the cached wrong perception was reused 39 times.
- **Cut kept the place (step 75).** Trace: `scene_cut: true`, skill `scene cut` via `exit: path east`, `exit: null`, `next_place: p_world_2 == place`. So no exit edge or via edge applied, and `Memory.match_place` (`agent/memory.py:86`) fell through to landmark overlap. That overlap is allowed to return `prev`, so p_world_2 got a self-loop edge `exit: path east -> p_world_2`.
- **The model never learns its own history.** `act()` gets only one-step `flags` (`same_action`, `last action blocked: both axes blocked`, `verdict miss`). It never sees what it did on earlier steps, so 38 identical misses read like one.

P3b fixes those three root causes and checks them offline against the P3 recording (0 requests). **Revised 2026-10-12 (build-first, program §1):** the live comparison (P3b.6, P3b.7) is dropped. P4 follows without a performance decision, and the next live run is L1's long run after W4.

## 1. Entry criteria
- P3 Result filled in (it is) and the user chose P3b at STOP 3 (2026-10-12).
- On disk: `runs/m1/` with `checkpoint/000074/`, `runs/m1_act/` (P3 evidence; read only), the ROM, `.cache/model/`. Ledger: M1 bucket ≥ 80 left (411 on 2026-10-12). Ollama reachable.

## 2. Gate

Data = the P3 recording `runs/m1_act` (read only).

**Offline gate (0 requests):**

| Check | Pass | Measured by |
|---|---|---|
| Role fix on recorded data | on every `runs/m1_act` checkpoint ≥ 75 with a world perception, the fixed perception has exactly one `player` within 1 tile of the anchor median | `scripts/check_world.py roles runs/m1_act` |
| Cut fix on recorded data | replaying `match_place` for step 75 (before = checkpoint 74's place, after = checkpoint 75's perception, `cut=True`) returns an id ≠ `p_world_2` | `scripts/check_world.py cut runs/m1_act 75` |
| Suite | `tests/agent` green, W0 act golden unchanged or regenerated with a reviewed diff | full suite |

- **Decision this gate produces:** none. P4 follows (program §1).
- **If the offline gate fails:** fix within the WP once (one repair round), else stop and report.

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

**Run order:** P3b.1 → P3b.2 → P3b.3 (sequential: all three touch `loop.py` or `world.py`) ‖ P3b.5 → P3b.4 → offline gate

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

### P3b.6, P3b.7: dropped (2026-10-12)
- The live rerun and its comparison were removed when the program moved to build-first. Live behaviour is measured once, in L1.

## 6. Test layers
| Layer | Gates this phase? | Command |
|---|---|---|
| Unit | yes | `pytest tests/agent -q` |
| Offline replay on P3 data | yes | `check_world.py roles/cut runs/m1_act` |
| Metrics | yes | `eval_run.py runs/m1_act --no-write` |

## 7. Budgets
- **Lines:** report only (budgets suspended). Expected `agent/` ≈ +40, `scripts/` ≈ +60.
- **Cloud calls:** 0.

## 8. Out of scope
- `loop/`, contracts.md, deleting the gated path (P4), motion anchors (W4), dialogue-as-place cleanup, perceiver prompt edits, model changes, a second live run.

## 9. Result (filled in by the orchestrator)
- **Gate (first orchestrator pass):** failed offline, 2026-10-12. P3b.1, P3b.2 and P3b.5 were accepted; P3b.3 failed its parent acceptance and its allowed repair. Stopped under program §3 (a WP failing twice is reported, not taken over). P3b.4 was not started, so the `roles`/`cut` gate and final full suite were not run. No gate-pass commit or push.
- **User steering:** "no live run needed" during this phase. P3b.6 and P3b.7 were therefore omitted; no `runs/m1_act2` candidate was created. There are no P3b live gate numbers or behavior-better verdicts. Superseded the same day by the build-first decision: P4 follows without a performance decision.
- **P3b.1 parent acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_world.py tests/agent/test_loop.py -q` → **76 passed**, one existing SDL warning, **232.59s**, exit 0 (`.tmp/p3b/gate-player-repair.log`). Initial parent run was 75 passed / 1 failed in the new integration fixture; the one repair gave it an explicit target task and removed unrelated fake obstacles. Both fresh perceptions are corrected, and self-approach has zero inputs. The pre-acceptance review also fixed a short-circuit that skipped after-perception correction.
- **P3b.2 diagnosis:** a parent Python replay on a temporary copy of checkpoint 74 memory, with checkpoint 75 perception and trace action, returned **p_world_2**. No matching via edge or live via-id relation existed; the only qualifying landmark match was **p_world_2 at 0.60**. Evidence: `.tmp/p3b/cut-diagnosis-before.json`. This confirms the packet's landmark branch diagnosis without requests or source-run writes.
- **P3b.2 parent acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_memory.py tests/agent/test_world.py -q` → **41 passed**, **18.47s**, exit 0 (`.tmp/p3b/gate-cut.log`). Tile exits, via edges, via-id relations and landmarks cannot return the departed place on a cut. The naming guard applies to the immediate cut-arrival naming call; a later valid merge remains available.
- **P3b.5 parent acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_eval_run.py -q` → **26 passed**, **0.51s**, exit 0 (`.tmp/p3b/gate-eval.log`). `.venv-agent/Scripts/python.exe scripts/eval_run.py runs/m1 --no-write` and the same command for `runs/m1_act` both exit 0; **all 35 earlier root fields equal** their saved pre-change outputs. P3 reports **player_role_errors=39, cut_self_loops=1, recent_line_steps=0**. Baseline reports 0/0/0, but legacy missing `entities` means the role zero is not evidence of measured correct identity. Outputs: `.tmp/p3b/baseline.json`, `p3.json`, `pre-baseline.json`, `pre-p3.json`.
- **P3b.3 parent acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_loop.py -q` → **53 passed / 1 failed**, one existing SDL warning, **219.80s**, exit 1 (`.tmp/p3b/gate-recent.log`). `test_modal_overlay_at_step_start_keeps_place` exhausted its StubModel replies: the first cut now correctly creates a distinct place and triggers an extra planner call.
- **P3b.3 repair and final evidence:** the subagent added `reply({"tasks": []})` before the dialogue reply. Parent `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_loop.py -q -k 'modal_overlay_at_step_start_keeps_place or act_prompt_summarizes_recent_repeated_blocked_action or act_recent_groups_by_action_and_caps_mixed_outcomes'` → **1 failed / 2 passed / 51 deselected**, **7.20s**, exit 1 (`.tmp/p3b/gate-recent-repair-focused.log`). The remaining assertion at `tests/agent/test_loop.py:333` compares `second["place"]` (**p_world_2**) with `first["place"]` (**p_world**, before the valid cut). The recent prompt checks pass, including repeated attempts, mixed outcomes, inherited trace history and the 200-character cap.
- **Exact proposed follow-up (not applied):** in `test_modal_overlay_at_step_start_keeps_place`, change `assert second["place"] == first["place"]` to `assert second["place"] == first["next_place"]`. This preserves the modal-place assertion against the actual post-cut place. It needs a new authorized repair, then P3b.3 acceptance, P3b.4 and the full suite. No contract change is proposed.
- **Scope/review:** one explicitly configured `gpt-6-luna` high subagent per P3b.1/2/3/5; no other subagent model. Edits stay within their listed files. No game-specific additions to `agent/`, no goal/option gates, no motion-anchor implementation or perceiver prompt changes. Recent flags are a transient list and are recorded exactly in `trace.act.flags`.
- **Deviations:** the evaluator compares perceived screen centers directly with sprite slot 0 (`x+8,y+8`, 160×144 frame, 16px threshold), rather than applying the map-position stationary offset, which would hide the P3 role swap. Missing legacy player positions are skipped. Recorded `runs/m1_act` evaluation uses `--no-write` to preserve it. Existing modal-fixture repair remains incomplete. An attempted diagnostic using two stale pytest-cache node names collected no tests (pytest exit 4); it was not a runner/model failure and spent no requests.
- **Line counts (packet §7: report only):** tracked Python `agent/` **2508 → 2560 (+52)**, `scripts/` **2710 → 2743 (+33)**, `tests/agent/` **3962 → 4161 (+199)**. Changed-length files: loop223→249, world528→554, evaluator609→642, test_loop769→875, test_world476→515, test_memory219→245, test_eval463→491. Memory and skills were changed without net line growth. Checker unchanged (P3b.4 not started).
- **Requests/ledger:** **0 requests spent**. Before and after: total657 / M1 639 / smoke18 / stage_a142 / stage_b297 / stage_p25 / stage_q60 / stage_c86; **411 M1 left**. No ledger resets/refunds/cap increases; model cache preserved.
- **Protected data:** **554/554 files** under `runs/m1` and `runs/m1_act` hash-identical, **0 changed/missing, 0 added** (`.tmp/p3b/protected-after.json`). `contracts.md` and `loop/` have no diff. Pre-existing untracked `.claude/` preserved. All phase edits are uncommitted because the gate failed.
- **Open issues:** authorize the exact modal-test follow-up if continuing; finish P3b.4 and full suite before accepting offline work. Learned-anchor role correction assumes a following camera; fixed-camera identity remains W4 work. The cut-arrival naming marker is transient and not checkpointed. Dialogue-as-place cleanup remains out of scope and has no new live count. No evidence supports a P4 behavior-better decision from this phase.

- **Completion (user's Claude session, 2026-10-12; the user asked to finish P3b and move to build-first):**
  - **P3b.3 repair:** `tests/agent/test_loop.py:333` now asserts `second["place"] == first["next_place"]`. The fixture forces every frame change to be a cut, so step 1 correctly ends in a new place; the test's claim is that a modal overlay at step 2 keeps that place.
  - **P3b.4:** `scripts/check_world.py roles <run>` replays recorded trace `entities` (steps ≥ 75, world screens) through `World.fix_player` with checkpoint 74's anchors and scores against truth sprite slot 0 via `_p3b_metrics`. `check_world.py cut <run> <step>` replays `match_place` on a scratch copy of the previous checkpoint's memory.
  - **Offline gate:** `.venv-agent/Scripts/python.exe scripts/check_world.py roles runs/m1_act` → **exit 0**: 40 world steps, 0 anchor failures, truth role errors **39 → 0** (`.tmp/p3b/roles.log`). `scripts/check_world.py cut runs/m1_act 75` → **exit 0**: replay returns **p_world_3**; the recorded value was p_world_2 (`.tmp/p3b/cut.log`).
  - **Full suite:** `pytest tests/agent -q` → 267 passed / 3 failed (`.tmp/p3b/gate-suite-final.log`). All 3 were P3b consequences that no WP acceptance had covered. (a) `test_skills.py` still expected the old self-target result `failed / already there`; it was renamed `test_approach_at_player_is_blocked_without_pressing` and now expects `blocked / target is the player`, as the P3b.1 card specifies. (b) Both W0 goldens were regenerated (`W0_REGEN=1`) and the diff reviewed. In the act golden, the old file recorded the door cut as a self-loop `door → p_world` (the P3 bug, since the fake's perception is identical in both scenes); now `door → p_world_2`, with entities re-homed accordingly. The gated golden only gains `player_fix: null` per trace row. Rerun: `pytest tests/agent/test_skills.py tests/agent/test_world_golden.py -q` → **23 passed** (`.tmp/p3b/gate-suite-fix.log`). No non-test code changed after the full run.
  - **Gate:** passed offline. Requests 0. `scripts/check_world.py` 373 → 414 (+41).

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` per [_template.md §10](_template.md).
