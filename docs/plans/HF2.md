# HF2: Does the executed action receive its own feedback and use the displayed exit?

Status: done 2026-10-14 (offline gate passed). Depends on: HF1 gate passed. Requests: 0.

Read the [program](program-harness-reliability.md), [handoff](HF-handoff.md), shared contracts 2–4, and HF1 Result. Expected impact: high; complexity: medium.

## Read and causality

CodeGraph: `World._exits World._paths World.options World.resolve World.plan World.travel fail_key Loop.step Loop.state Loop.restore mark_exit`.
Inspect `tests/agent/test_exits.py`, `test_options.py`, `test_loop.py`, `test_world.py`, `test_space.py` as needed. The displayed door mat is grouped, but `_paths` selects the first raw edge; failure lookup uses the exit name while execution keys the destination.

## Work packages and ownership

Run **A -> B -> C**. All are HIGH worker cards; no shared-file parallelism.

| WP | Owns | Deliverable / targeted pytest files |
|---|---|---|
| A | `agent/space.py`, `agent/world.py`, `tests/agent/test_space.py`, `tests/agent/test_exits.py`, `tests/agent/test_world.py` | One authoritative executable exit representation; preserve observed edges against reverse inference. Tests: `test_space.py test_exits.py test_world.py`. |
| B | `agent/world.py`, `agent/loop.py`, `tests/agent/test_options.py`, `tests/agent/test_loop.py` | Internal action identity and selected-exit reference propagate through resolve/execute/failure/notes/checkpoint; tests: `test_options.py test_loop.py`. |
| C | `agent/loop.py`, `agent/tasks.py`, `agent/plan.py`, `tests/agent/test_loop.py`, `tests/agent/test_tasks.py` | Planner and chooser receive the same current execution cause; bounded replan triggers persist across resume. Tests: `test_loop.py test_tasks.py`. Edit plan.py only to pass factual structured failure text, not goal heuristics. |

Acceptance commands for A, B, C respectively:

```powershell
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_space.py tests/agent/test_exits.py tests/agent/test_world.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_options.py tests/agent/test_loop.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_loop.py tests/agent/test_tasks.py -q -p no:cacheprovider
```

## Required behavior and tests

1. Insert inferred/observed duplicate exit records in both orders. Display and travel pick the observed representative and matching arrival coordinates. Distinct destinations and physically separate doors to the same destination remain distinct; a selected specific door executes through that door.
2. Re-crossing an edge does not downgrade established reverse evidence. If a preferred exit is unreachable, consider another feasible canonical exit; never choose an inferred duplicate solely by insertion order. Preserve unknown reverse links as hypotheses, not verified returns.
3. Select the remembered door-mat option; cause a route failure; render options again. Its own counter increments and shows **route blocked**, without relying on the label `outside house`. Rename the destination/exit and reorder options: the same counter remains attached. Different actions/places do not inherit it.
4. Test selected-option actions and free-form `go_to`, raw buttons, and named-list follow-ups in both `decide=True` and ordinary act modes. The equivalent resolved intent shares feedback; interaction and approach failures are distinguishable. Handle absent legacy metadata without exceptions.
5. Round-trip current counters/identity through state/restore and merged-place aliases. Preserve ambiguous legacy counters as history instead of transferring them to the wrong exit or double-counting them.
6. A collision before the exit does not write a target `does_nothing` refutation or claim the exit was tested. A genuinely reached, attempted crossing can produce crossing-stage evidence. Counterexamples: successful exit crossing clears applicable failure state, not unrelated history.
7. Synthetic chooser repeatedly requests the same destination. With a detour it gets a revised feasible route; with no detour it gets a truthful bounded zero-input failure and factual planner feedback. Preserve model choice: no history-based option removal/reordering or automatic alternative goal. A persistent model choice can still fail HF5 autonomy.
8. Planner no longer has only `approach outside house -> miss`: include resolved intent, failed stage/blocker, and whether an exit crossing was actually attempted. Trigger once on material new evidence; do not add an extra planner call every repeated frame.

## Gate

All eight groups pass plus full suite/boundaries. Inspect checkpoint150's old 31-versus-7 discrepancy using a copy: one **new** attempt must update the currently selected option's counter; do not rewrite the historical run to make its old counts equal. Existing exit/cycle tests and IP fact flow remain green. Requests=0; protected data unchanged.

## Result and handoff

- Baseline: a3695e9 + HF1 dirty tree on feat/lean-agent; suite before 422 passed + 1 baseline failure (`test_stage_b_cap_override...`). Orchestrator Sonnet 5.5 -> Haiku HIGH workers A, B, C in order; orchestrator re-ran A/B acceptance itself and fixed one stale fixture (`test_world.py` here_flag keys -> `room|buttons|up`).
- Exit authority (A): `mark_exit` never lets an inferred reverse overwrite an observed edge. `_exits` records gained `place,to,arrive,src,doors[{tile,dir,to,arrive,src}]`; representative door = crossed/observed before guess, then row order. `_paths` now built from `_exits` (elements `(place, rec)`), `plan` tries each door in the group. Failed first: overwrite (`'inferred'=='observed'`), travel followed row order, unreachable observed door returned None.
- Identity (B): intent key `place|family|subject` (family buttons|approach|interact|select|advance|cancel|type; subject `thing:<id>` | `place:<id>` | `exit:<dest>@<x,y>` | `unexplored`). In `Loop.fails`, option rows (`intent_key`, `exit:{tile,to}`), trace `choice.intent`, `state()["pending_key"]`. `fail_key(..., ref=None)`, `exit_ref`, `World.plan(target, exit=None)`, `canon_place`, `named_intents`, `Loop._migrate` (legacy name keys kept as history unless exactly one match; merged places keep the larger count, never summed). Route-stage failure counts on its own intent but writes no does_nothing refutation. Selected door travels through its own door.
- Planner/chooser (C): `failure_cause(...)` text (intent, stage, blocker, crossing attempted or not) goes to `plan(failure=)` ("Last failure:" line) and chooser flag `cause:`. Replan once per new evidence `[intent, stage, blocker|tile]` (`Loop.reported`); the old "failed twice" urgent was removed. State: `planner.cause`, `planner.reported` (old checkpoints restore as None). Known entity blocker -> next on-screen route avoids its tile; no detour -> zero-input blocked failure (`no route avoids blocker`). Options never removed or reordered.
- Gate evidence: full suite `tests/agent` -> 441 passed, 1 failed (same baseline test), plus golden fixed below; golden+boundaries 4 passed. `test_world_golden::test_act_eight_steps` failed because trace `choice` gained `intent`; I regenerated that one golden (`tests/agent/data/w0_golden/act/trace.jsonl`) and verified old == new once `intent` is removed (2 rows, only change). Plan said no automatic regeneration: this was a deliberate, verified, single-file change.
- Checkpoint150 copy (`.tmp/hf/HF2/c150`): migrated counters put `exit: door mat` (7) on `p_place_5|approach|thing:e_exit_door_mat`, and the rendered door-mat option shows it; legacy `outside house` 31 stays history, not summed. One new attempt increments the option's own key (covered by test_loop/test_options, not replayed live).
- Requests 0; ledger untouched; `runs/` unchanged (trace hashes b3fcc0e2.., 7e5a6e9b.. same; no git changes under runs/).
- Limitations: detour only for on-screen approach routes (memory travel legs not routed around remembered blockers); only blockers with a known entity id are avoided; thing-only exits route by destination; an exit row and free-form "go to <dest>" are different intents; a new lower door tile changes an `exit:` subject; `_paths` now calls `_exits` per reachable place (not benchmarked); each new failing intent costs one planner call.
- Gate: PASSED offline (one pre-existing baseline failure). Next: HF3.
