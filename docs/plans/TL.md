# TL: Build the lean tracking layer and get the agent out of the house

Status: not started · Branch: feat/lean-agent · Program: [program-r1-wm.md](program-r1-wm.md) row 10 · Spec: [tracking-layer.md](tracking-layer.md)

## 0. How this phase works (user decision 2026-10-12: build and progress first)
- **Build in small slices.** Each slice ends with one quick check that it works; the full suite runs only at the end of the phase.
- **Checks report; they do not block.** A failing check is fixed if cheap, or written down and carried forward. Only a broken suite or a crash stops the phase.
- **The live run is the real test.** It comes as soon as B1–B3 work, and its result decides what to build next.
- No new contracts work up front. The orchestrator writes the contract delta (§4) into the Result for the user to apply afterwards.

## 1. Entry criteria
- L1 committed (`353d560`). Spec approved (lean version).
- On disk: `runs/m1_long/checkpoint/000070/` (fresh-run memory, safe to resume), the ROM, `.cache/model/`. M1 ledger ≥ 110 left (301 on 2026-10-12).

## 2. Slices

| Slice | What | Files | Quick check | Est. |
|---|---|---|---|---|
| B1 | Audit bug fixes: naming suffix, one observe per perception, partial-`None` scroll, crossing clears a wall, inferred exits don't block | `agent/memory.py`, `agent/world.py`, `agent/skills.py`, `agent/space.py`, tests | unit tests for each fix + `check_track.py dupes` (below) | ½ d |
| B2 | Self spot from motion: `observer.residual`, `self_at` on moves, `World.self_spot` lock/unlock, `pa` from the self spot | `agent/observer.py`, `agent/skills.py`, `agent/world.py`, tests | `check_track.py self` | 1 d |
| B3 | Probe, re-perception with a changed prompt, true streak, "here:" facts | `agent/world.py`, `agent/loop.py`, tests | `check_track.py probe` | ½ d |
| B4 | Evaluator: `self_spot_accuracy`/`coverage`, `--from-step`, false walls; drop seen-in-bounds from health. Delete W4 evidence code | `scripts/eval_run.py`, `agent/world.py`, tests, goldens | `eval_run.py runs/m1_long --no-write --health` runs | ½ d |
| L2 | Live: resume `runs/m1_long` from checkpoint 70, ≤ 110 requests | `runs/m1_long` | health on steps ≥ 71 + progress | 1 h |

**Order:** B1 → B2 → B3 → B4 → full suite → L2. If B2's check looks bad on real frames, still finish B3; the probe report shows whether the lock works.

### `scripts/check_track.py` (one script, three commands; 0 requests; RAM truth for reporting only)
- `dupes`: replay `runs/m1_long` trace perceptions for steps 38–70 through `Memory.observe` on a scratch copy. Print the rows per base name (target: 1 per furniture item).
- `self`: from `runs/m1_long/checkpoint/000070` and `runs/m1/checkpoint/000054`, press a scripted sequence (LEFT, DOWN, RIGHT, UP, LEFT) through `skills._step`. Print each move's scroll, `self_at`, the lock state and the distance to truth sprite 0's centre.
- `probe`: from checkpoint 70 with the self spot unlocked, run `World.probe`. Print presses, the lock and the distance to sprite 0.

## 3. Slice cards

### B1: audit bug fixes
- `memory._base`: `re.sub(r"(#\d+)+$", "", ...)`. Check the callers of `_base` (`world.py` view grouping already peels nested suffixes; simplify it if it becomes redundant).
- One observe per perception:
  - `World.before` calls `mem.observe` only when this perception was not already observed by the previous step's `observe_after`, i.e. when the perception was not reused from the cache;
  - otherwise it reuses `self.entity_ids`.
  - Confirm with `codegraph_explore` which caller sees the reused flag.
- `skills._step`: if any per-frame shift is `None`, use `scroll(before, end.frame, max_shift=...)` for the whole move. If that is `None` too, return `None` (no position update).
- `space.fold`: on a successful move in direction d from `here`, remove d from `here`'s `blocked`.
- `space.route` and `space.frontier`: an exit only blocks its direction when its `src != "inferred"`.
- **Checks:** a unit test per bullet; `check_track.py dupes`.

### B2: self spot from motion
- `observer.residual(frames, shifts) -> (cx, cy) | None`, per spec §3.1. Use numpy only; reuse `observer.changed`'s threshold idea.
- `skills._step`:
  - compute `self_at` from the frames it already holds;
  - store each move as a dict `{"dir", "shift", "self_at"}` (`fold` already accepts dict moves);
  - add `SkillResult.self_at` = the last non-`None` value.
- `World`:
  - `self_spot` is set and unlocked per spec §3.1, from the step's `SkillResult` in `observe_after` or `after`, whichever already receives the skill;
  - it is persisted in `state()`/`restore()` (the key already exists).
  - `before()` uses `pa = self_spot` when locked, else today's `_anchor`.
- `fix_player`: already prefers `self_spot`. Make sure it runs on the cached perception too (it does in `Loop.step`).
- **Check:** `check_track.py self` prints a lock on the first scrolled move, within 8 px of sprite 0 on both checkpoints. If it doesn't, print the residual heat bounding boxes and continue to B3.

### B3: probe and information reflexes
- `World.probe(ctx)` per spec §3.2. `Loop.step` (act mode) calls it before `act()` when the conditions hold, and puts the result in the row as `probe`.
- **Re-perception:** when `Stuck.n >= 2` (consecutive no-effect actions), the next `_see` bypasses the frame-hash cache once and appends the spec §3.3 line to the perceiver context.
- **`recent:` flag:** the head shows `no effect xN` from `Stuck.n` when N > 0.
- **`here:` flag** from the `tiles` row at `World.pos`, per spec §3.4.
- **Check:** `check_track.py probe` locks from checkpoint 70 in ≤ 4 presses; a GridEnv unit test shows the flag lines.

### B4: evaluation and deletion
- `eval_run.py`:
  - `self_spot_accuracy` and `self_spot_coverage` from the trace `self_spot` vs truth sprite 0 (centre x+8, y+8);
  - `--from-step N` limits per-step metrics and health to steps ≥ N;
  - `false_walls`;
  - health drops `seen_in_bounds` and adds the self-spot rows.
- Trace: `Loop.step` writes `self_spot` (or null) and `self_locked` each step.
- Delete W4's `classify`, `_learn`, `evidence` and the old `self_spot` writer. Keep `_anchor` only as the unlocked fallback for `pa`.
- Regenerate goldens with `W0_REGEN=1` where needed, and explain the diffs in the Result.
- **Check:** `eval_run.py runs/m1_long --no-write --health` runs and prints the new rows. Then the full `pytest tests/agent -q -p no:cacheprovider` (read the summary line). It must be green before L2.

### L2: live
1. Read the ledger (stop if M1 < 110). Back up `runs/m1_long` to `runs/m1_long_l1` first (L1 evidence).
2. Run:
   `.venv-agent/Scripts/python.exe scripts/run_agent.py --run runs/m1_long --resume --from-step 70 --steps 100 --max-calls 110--stage c`
   (act is the only path since P4; there is no `--act` flag).
3. Then: `eval_run.py runs/m1_long --health --from-step 71`, and `check_track.py self` on the new frames if useful.
4. **Report:**
   - **Progress first:** truth maps reached and when; Town, lab, party.
   - **Then health:** self-spot accuracy and coverage, no-effect streak, repeats, false walls.
   - **Probes run.**
   - The worst stuck window, with frames and the act `why`.

## 4. Contract delta (orchestrator drafts it in the Result; the user applies it)
- `SkillResult.self_at`, and move records as dicts `{dir, shift, self_at}`.
- `World.self_spot` lock/unlock semantics, `World.probe`.
- W4 `classify`/anchor-evidence fields removed.
- Trace fields `self_spot`, `self_locked`, `probe`.
- Evaluator fields `self_spot_accuracy`, `self_spot_coverage`, `false_walls`, `--from-step`.

## 5. Budgets
- **Lines:** report only. Expected `agent/` net ≈ +60 (B2/B3 add, B4 deletes W4 evidence).
- **Requests:** 0 until L2; L2 ≤ 110 (M1 bucket, stage c).

## 6. Out of scope
- Object tracks, NCC tracking, fixed-camera mode, overlay mask, tile hashing, appearance memory: spec §5.
- `loop/`. Edits to contracts.md (the user applies the drafted delta).

## 7. Result (filled in by the orchestrator)
- Each slice's check output (pass, or a note carried forward).
- Suite summary line.
- L2: progress, health on steps ≥ 71, probes, the worst stuck window, requests and ledger.
- Contract delta text, line counts and open issues.

## 8. Handoff
Rewrite `docs/plans/handoff-notes.md` per [_template.md §10](_template.md).
