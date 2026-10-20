# Stairs ping-pong: exit targets close, and ambiguous names are reported

Status: **proposal, no code.** Written 2026-10-19 from `runs/m1_rcr_phase_e` (S59–S150), checked against `runs/m1_rcr_post_r3`. Not an RCR amendment; RCR stays closed (MASTER §8). Design rules D1–D9 apply, with one exception that needs your OK (see Decisions).

## 1. What happened

- **The loop is two stairs.** `p_place_2` (tiled room) tile (3,-4) → `p_place_3` (room) at S61; `p_place_3` (-1,0) → `p_place_2` at S64. The pair repeats from S61 to S143 (`memory/crossings.jsonl`). Trace S62–S143: the act alternates `go_to place:p_place_2` and `go_to place:p_place_3`, and between them uses `exit: stairs`, `door mat` and `vent` picks.
- **The act follows the quest text, not the target.** S63, in the room: *"I need to return to the tiled room to continue exploring for the exit stairs as per the goal."* q2's text is *"Explore the tiled room to find a way out"*. The planner set q2's target to a room-side exit at S141 and the text was never updated; the act pursued the text, which pointed back up.
- **q2 was already achieved when it got its target.** The agent crossed the tiled room's stairs at S61. The planner ran on the arrival at S62 (T1) and set `target: thing:e_exit_stairs`, which is the stairs just used. Quest closure ignores that crossing, because it happened before the target was set.
- **No closing event exists for exit targets.** `Notebook.observe` (`agent/notebook.py:161-176`) closes only place targets, on arrival, and only after `retargeted`/`opened`. `Notebook.heard` (`:144`) closes thing targets only on speech from that thing. So an exit-thing target never closes by being crossed. q2 closed only at S142, after being retargeted to `place:p_place_3` at S141.
- **Two entities share the name `exit: stairs`.** `e_exit_stairs` (p_place_2, tile (4,-4)) and `e_exit_stairs_2` (p_place_3, tile (0,0)). Reproduced offline on the run's memory: `World.ref("exit: stairs")` → `None`, while `ref("thing:e_exit_stairs")` resolves.
- **The error steers the planner to a different exit.** `Notebook._no_target` (`:45-49`) builds its "closest:" list only from labels whose `ref()` is unique. The tied label is dropped, so the message offers `exit: right passage` (`e_exit_right_passage`, status `gone`, p_place_2). The planner retargets to it at S71 and S89, then to `door mat` (room) at S94.
- **Counts.** `notebook.history` has 11 `target` ops, 3 of them errors. (The coordinator's 22 / 6 counted each op twice, because the `last` row echoes it.) There are also 2 `add` errors with a target (S144, S147).
- **Ties are not unique to stairs.** `exit: door` also appears twice in p_place_3 (`e_exit_door`, `e_exit_door_2`). post_r3 has no same-named exits in its entity table, so the tie path did not run there.

## 2. Root cause

Two invariants break in shared functions. Both are visible in the offline replay.

1. **A quest is not closed by the exit it targets.** Exit targets have no closing event, and the one place-arrival event counts only from the retarget step. The agent's achievement (having left the room) is invisible, so the quest keeps directing the act.
2. **A name that matches two things never resolves, and the error hides the tie.** `World.ref` returns `None` on any tie; `_no_target` leaves the tied labels out of its suggestions. The planner is given a valid-looking alternative and a reason to switch.

**Rejected: ties resolve in favour of the current place.** At S62 the current place is the room, and its stairs (`e_exit_stairs_2`) lead back up. The tie-break would have picked the wrong stairs. The planner has to name the exit by ref.

## 3. Proposed invariants

**I1 — an exit target already used closes the quest (narrowed 10-19).** A quest whose target is a thing that is an exit closes with `reason: "crossed"`, when the target is set, if that exit was crossed from the step before the quest opened up to the step the target was set. The crossings are the observed place transitions the notebook keeps (`data["moves"]`, the last 40); the exit's from/to comes from `World.exit_route`. A crossing after the target was set is an event after the target (D5) and closes nothing, so q5 in post_r3 stays open. Closure runs in `apply_ops`, and `eval_plan` replays it per step.

**I2 — an ambiguous name is reported with its candidates.** When `ref()` matches several live things or places, `_no_target` lists each candidate as a ref with its place label, for example: `target "exit: stairs" is ambiguous: thing:e_exit_stairs (tiled room), thing:e_exit_stairs_2 (room); use one of these refs`. Ref-form targets already resolve (`World.known`), so the planner can answer with a ref. Nothing is refused; the error becomes evidence (D2).

## 4. Where (shared functions only, D3)

- `agent/world.py` `World.ref` (`:557-575`): add a candidates function that returns all tied matches; `ref` keeps returning one ref or `None`. Both callers (`Notebook._no_target`, `World.resolve`) go through it.
- `agent/notebook.py` `_no_target` (`:45-49`): list the candidates from that function, not the unique-label set.
- `agent/notebook.py` `observe` (`:161-176`): on each `prev → here` change, close open quests whose exit target was used by that crossing. Inject a `crossed(ref, since)` callable, the same way `ref`, `canon` and `label` are injected (`agent/loop.py:44`, `scripts/eval_plan.py:69`).
- `agent/loop.py:285` already calls `observe` once per step, so the order (observe, then planner on T1) stays as it is.
- Tests: `tests/agent/test_notebook.py` (tie error lists refs; exit target closes on crossing at or after opened, not before), `tests/agent/test_world.py` (`ref` tie returns candidates).

## 5. Offline check (0 calls)

1. `scripts/eval_plan.py --run runs/m1_rcr_phase_e --check all`, before and after. Expected: S59–S83 errors list both `thing:e_exit_stairs` and `thing:e_exit_stairs_2`; q2 closes at S62 with `reason: "crossed"` (was S142); no retarget to `e_exit_right_passage`.
2. Regression: `eval_plan --run runs/m1_rcr_post_r3 --check all` must still exit 0, and q5 must not close.
3. Add one `eval_plan` check: every exit-target closure has a crossing at or after the quest's `opened` step. This is the false-closure check (S5 analogue). The current `_observe` check only synthesises place and talk quests, so it does not cover exit targets.
4. `pytest tests/agent -q -p no:cacheprovider -n auto` green.

**Limit.** `eval_plan` replays the recorded planner ops, not the planner's new choices, and it does not replay the act. It can show the notebook state and closure timing. It cannot show whether the act leaves the room. Only a live run can show that.

## 6. Not in this change (D9: one change, one measurement)

- `World.ref` still resolves an exit with status `gone` (`e_exit_right_passage`). Candidate for a second change if the offline check shows the planner still targets it.
- The planner's quest text can name a place the target is not in ("Explore the tiled room" with a room target). I1 closes this case; general text drift is the planner's job and is not gated.

## 7. Decisions needed

1. **Amend D5 for exit targets.** D5 says closure counts only events *after the target was set*. I1 counts from *opened*, because T1 fires on arrival, so the planner sets a target one step after the crossing it describes. Without this amendment the ping-pong stays. This changes a decided rule, so it needs your approval.
2. **Approve the I2 error format** (candidates listed with their refs).
3. **Live run.** The ledger reads 4626 of 4815 (189 calls left, from `runs/e3_budget.json`). The handoff's "one run, about 300 calls" does not fit. A shorter run (for example 60 steps, ~120 calls) covers S59–S143. Decide after the offline gate passes.

Nothing is committed. Pending your OK, the next step is one change, tested and measured in the order of §5.
