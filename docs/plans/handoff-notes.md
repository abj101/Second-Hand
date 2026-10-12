# Handoff notes

Last written by: planning session, 2026-10-11, before the E4 kickoff. E3 is parked with M1 open; its gate moves into E4.5 stage M (user decision; E3.md §R4.8, E4.md).

## State of the code
- Branch feat/lean-agent; E3R3–E3R5 and option A are in the latest WIP commit (`git log -1`); nothing is pushed.
- `tests/agent`: 191 passed (about 5 minutes), with one existing SDL warning.
- `agent/` is 1,740 lines. E4 adds about +135 (E4.md §7), and the user has seen the overrun against spec §12's 1,500.
- **Goals:** `decide.plan` sets model goals (≤ 2 tasks), and its prompt states the JSON answer shape. Plan triggers live in the loop (`pending_plan_reason`, 15-step throttle).
- **Fallback goal:** "Reach a place not visited yet", `done_when new_place`, budget 40.
- **Spatial memory:** `agent/space.py` plus the `tiles` store, player tile `pos`, BFS routes, `outward` pushes, and frontier pushes; contracts.md#space.
- **Navigation:** code order only (wall or edge placement first, then distance). The decider ranker is unwired; `agent/judge.py` and `scripts/decider_worker.py` remain on disk, unused.
- **Exit tests:** an id-less candidate uses its own projected tile (option A). A route blocked by a newly learned wall before its target records no failed test and keeps the same target (`self.commit`).
- **Robustness:** a stable player anchor (median of recent world samples) survives a perceiver mislabelling another sprite as the player.
- **Identity:** `resolve(..., tile=)` breaks name ties by tile; `match_place` strips the `exit:` prefix and follows live `leads_to` relations (`via_id`).
- **Trace:** rows carry `tile`, `drift_fix`, `ranker` (now always null) and the optional `exit`.
- **Dead code:** `decide.escalate` has no callers; it is a deletion candidate together with the judge files.

## Decisions in force
- **Order:** build E4.0–E4.4 first; E4.5 stage M gates M1 on `runs/m1`, then stage E runs E4 on `runs/e4`.
- **Tiers (user):** Sonnet orchestrator; Sonnet for E4.1 and E4.2; Haiku for E4.0 and E4.4 (escalate to Sonnet after two failed acceptances). Never Opus; always set the model explicitly.
- **E3 ledger** `runs/e3_budget.json`: caps are phase 1,100 / M1 1,050 / q 60 / c 546 / smoke 50. Never reset, refund or raise; abandoned requests count.
- **E4 gets its own ledger** (`runs/e4_budget.json`, cap 600), built in E4.0.
- **Resume M1 only** with `--resume --from-step 54`. Checkpoint 54 is merged and re-migrated (backups in `.tmp/e3r3/pre-merge/` and `.tmp/e3r4/pre-remigrate/`).
- **Stage M early stop:** no Pallet Town within its first 40 requests means stop and report.
- **Boundaries:** no target hints, no RAM truth in agent choices, nothing game-specific in `agent/`.
- **Commits:** after a passed gate only (except user-approved WIPs), explicit paths, never `.claude/`, never push.

## Measured numbers
- **E3 ledger:** 542 = smoke 18 + M1 524 (a142 / b297 / p25 / q60 / c0). M1 has 526 left; the phase has 558.
- **Offline exit check** (`.tmp/e3r4/offline.py`, 0 cloud calls): Pallet Town reached; 1 reached exit test; 12 inputs.
- **Perception check:** the rug is placed on the bottom wall. **Plan check:** valid but generic goal ("Reach a place not visited yet").
- **Probe 1** (20 requests): the perceiver labelled Mom as the player, so tiles shifted 3 rows; 17 duplicates; the stairs were taken; upstairs became the new place `p_world`.
- **Probe 2** (20 requests): the anchor held (tile accuracy 21/21).
  - The ranker scored the door mat 0.12 against a shelf 0.55, and invented "exit: screen edge" targets 0.62–0.72.
  - At step 62 an id-less target walked to the stairs (bug fixed by option A).
  - 39 duplicates, mostly perceiver inventions.
- **Retained run metrics at 75 steps:**
  - Execution 0.613.
  - Prediction 0.64 (0.88 on steps 1–50, 0.16 on steps 51–100).
  - Cost: 1.467 asks per step and 5.15 s per step.
  - Perception reuse 0.933; no-call choices 0.693.
- **Places:** only the 2F–1F edge is verified; M1 false; places_matched false.

## Environment and gotchas
- Use `.venv-agent/Scripts/python.exe` with forward-slash paths. Unit tests use StubModel/GridEnv and make 0 cloud calls.
- The cloud perceiver (`gemma4:31b-cloud`, prompt `e3-perceiver-7`) ignores the `format` schema, so every prompt must state its JSON shape.
- Preserve `.cache/model/`: a resume from checkpoint 54 replays cached replies for free until the run diverges.
- Evidence: `.tmp/e3r5/probe1/`, `.tmp/e3r5/probe2/` (trace, truth, frames, checkpoints), `.tmp/e3r5/probe2-eval.json`, `.tmp/e3r4/replay_55_57.py`.
- `runs/m1` currently holds checkpoints 0–54 plus the failed probe's 75; `--from-step 54` prunes 75.
- `scripts/eval_run.py` has the M1 and spatial metrics; E4.4 adds the learning metrics.

## Deviations from plan and contracts
- M1 is not passed; its gate moved into E4 (user decision 2026-10-11).
- The contracts.md E4 entries were revised the same day: reflection is an extension of `plan`, wake reasons live in the loop trigger (no `Tasks.wake`), and pattern kinds are normalized.
- `agent/` is over the spec's line headline; deletion candidates are reported in E4.

## Do not retry
- Do not re-probe the house outside E4.5 stage M; stage M's early stop bounds the cost.
- Never rescue a failed live check with a mid-check code patch; stop and report.
- Never re-wire the decider ranker or add game-specific exit rules.

## Open issues for E4
- **House exit (stage M risk):** perceiver-invented exits ("exit: screen edge" indoors) and furniture may be tested before the real door mat.
- **Re-entry by a new route** restarts a place frame at (0, 0); drift checks compare consecutive perceptions, not memory.
- **Duplicates** stay high from perceiver inventions, so the 0-duplicates metric is report-only.
- **Deletion candidates:** `agent/judge.py`, `scripts/decider_worker.py`, `decide.escalate`.
- **Deferred:** shortcuts, the live ablation, E1b, E5, perception skipping on known views, tile-based landmark matching.
