# Executor brief: Avatar round r18-fix4, one self-spot calibration, no blind probe (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `af48210` (r18-fix3 result). Scope: every open issue except the fake top-wall door. Check data: `.tmp/r18fix4/` (`c1_probe.py`, `c2_selfat.py`).

## 1. Verified diagnosis (one root cause, three symptoms)
**Root cause: a resume clears the self-spot calibration, and nothing re-acquires it.**
- `Loop.invalidate_live_observation` (`agent/loop.py:80`, run on every live resume after `restore`) sets `self_spot=None, _self_from_motion=False`, although checkpoints save them.
  - m1_r17_boot checkpoint 65 holds `self_spot [0.45, 0.451]`, `self_from_motion True`.
  - Without a resume, r17 stayed locked on 114/150 steps with **1** probe in the whole run.
- After every resume since, the lock never came back: `self_locked` is false on **every** step in m1_r18_odo, m1_r18b/c/d/e_odo.
- Re-acquiring needs `_step`'s residual box to be `accepted`. Offline replays of the lab (`m1_r18d_odo` checkpoint 140) and Pallet (checkpoint 145) give `accepted=False` with **both** the r18 code and pre-r18 `24712d6`. So this is scene-dependent, not an r18 regression in `_residual`.

**Symptom 1, the blind probe (the "random directions").** With the lock off, `Loop.step` runs `World.probe` (`agent/loop.py:138-158`, `agent/world.py` `probe`) on every world step before perception and choice.
- It presses UP, DOWN, LEFT, RIGHT in fixed order, up to 4 presses, ignoring exits, actors and goals. That is 100 unchosen presses in LV18, 88 in LV18b, 72 in LV18d.
- It locked **0** times in about 80 probe steps, so it bought nothing.
- It caused both LV18e lab re-entries (steps 146, 150: UP onto the door), the LV18e `dropped` segments (one each at 146 and 150, both on probe warps), and D's "missed cut" (the probe's endpoint cut test).

**Symptom 2, walls into NPC tiles.** Unlocked, `World.before` uses perception's "player" as the anchor (`pa`, `agent/world.py:212`).
- LV18c step 105 perceived the "player" at (0.65, 0.81), which is the rival, and the real player at (0.45, 0.47) as "NPC 1". Oak ("NPC 2", one tile below) was projected a tile off. The tile row recorded `held:["UP"]` (an actor believed *above*), then `blocked:["DOWN"]` into Oak (`walled.DOWN=105`).
- The DOWN evidence came from probe presses on steps 104 and 105.
- Step 92's UP wall into Oak = a probe silent at step 91 + the chosen bump at step 92.

**Symptom 3, a latent sibling bug.** When the lock is on, `_self_index` treats the entity at `self_spot` as the player. But an entity perceived as "player" elsewhere (step 105: the rival) is not counted as an actor (`ACTOR_KINDS` lacks "player"), so bumping it could still record a wall.

**Checked and needing no code:**
- `dropped=33` at LV18d step 131 is battle-screen drains, counted by contract design (non-world-type segments).
- The lab's name now sticks ("Pokémon Lab", first name wins).
- The endpoint `cut()` check in `approach`/travel has no evidenced miss outside the probe (step 145's exit was detected).

## 2. Design (deletions and single conditions only; stop and ask if any cannot work)
- **P1, delete the probe.** Remove `World.probe` and the loop's probe block (`agent/loop.py` ~137-158, with its probe-only variables). The step perceives and chooses from its own before-frame.
  - The trace keeps `"probe": null` (the golden data already has null on all 8 rows; `scripts/eval_run.py` reads it null-safely).
  - The lock is still acquired by the existing path: `World.observe_after` locks from any chosen move with a full-tile shift and an accepted `self_at` (`agent/world.py:643-647`). That is the same `_step` and `_residual` computation the probe used.
- **P2, keep the calibration across a live resume.** `invalidate_live_observation` stops clearing `self_spot`, `_self_contradictions` and `_self_from_motion`.
  - They describe the screen geometry, not pixel history, and the checkpoint already restores them.
  - The existing two-contradictions rule (`agent/world.py:648-650`) still unlocks a wrong spot.
  - Everything else the function clears stays cleared.
- **P3, label vs motion identity.** While locked, an on-screen entity perceived with kind "player" that is not the self index counts as an actor in `World.before`'s `actor_tiles` (`agent/world.py:221`). One extra `or` condition. The self index itself is never an actor.
- **Ceiling** (a `# ponytail:` comment at the observe_after lock): acquisition still needs an accepted residual, which fails in busy scenes. If contradictions unlock the spot there, the agent falls back to perception's anchor until a calm scene. Upgrade path: odometer-compensated residual.

### 2.1 Contract amendment (the user approves it by issuing this brief; the orchestrator commits it in wave 0)
Append under `## world` in `docs/plans/contracts.md`:
```
        # r18-fix4 (approved 2026-10-25): no probe presses. The self spot is acquired only from the agent's own chosen
        # full-tile moves (observe_after) and survives a live resume (it is screen geometry, restored from the checkpoint);
        # only two contradicting moves unlock it. While locked, an entity perceived as "player" away from the self spot is
        # another actor. The trace keeps "probe": null.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| S1 | P1 + P2 + P3 + tests (one lane: P1 and P2 both edit loop.py's step/resume path) | agent/loop.py, agent/world.py, tests/agent/test_loop.py, tests/agent/test_world.py | wave 0 | gpt-6-luna HIGH |
| S2 | gate script T18I (uncommitted), written red first | `.tmp/r18fix4/g/t18i.py` only | wave 0 | gpt-6-luna HIGH |
S1 and S2 run in parallel. S2 never edits agent/ or tests/.

### S1 tests and pre-approved assertion changes
- **Delete (pre-approved):** `test_world.py::test_probe_locks_after_a_move` and `test_loop.py::test_probe_act_uses_post_probe_frame_and_perception`. They test the removed probe. The "probe stage" tests in test_loop (run stage "p") are unrelated and stay.
- **Change (pre-approved):** `test_loop.py::test_live_resume_invalidation_clears_pending_but_keeps_durable_world`. The `self_spot is None and not _self_from_motion` assertion becomes `self_spot == [.5, .5] and _self_from_motion`. Every other assertion in it stays.
- **New tests:**
  1. An unlocked world step makes zero presses before the choice (env press log), and the act uses the before-frame's perception.
  2. A chosen move with a full-tile shift and an accepted `self_at` locks the spot (observe_after). Add it only if no existing test covers this.
  3. `restore` of a locked state followed by `invalidate_live_observation` keeps the lock, and the next `before()` uses `self_spot` as `pa`.
  4. P3: while locked, a second "player"-kind entity one tile below is in `actor_tiles`. Two no-response DOWN presses into it on two steps give `held`, never `blocked`.
- Any other failing assertion: stop, revert, report. Never set `W0_REGEN`; the golden data should not change.

### S2 gate T18I (zero model requests; pattern `.tmp/r18fix3/g/t18h.py`)
- **G-a:** a GridEnv Loop step with the lock off. Presses before the chosen action = 0 (wrap `env.press`).
- **G-b (real LV18c state, zero model calls):**
  - Build World on a copy of `runs/m1_r18c_odo/checkpoint/000105/memory` (copy to `.tmp/r18fix4/S2/`; never write in runs/), locked with `self_spot [0.45, 0.4513888888888889]` (from m1_r17_boot checkpoint 65).
  - Feed `before()` the recorded step-105 perception: the trace row's `entities`, as a perception dict with `screen: "world"`.
  - Assert the tile below the player is in `actor_tiles` and the tile above is not.
  - Then fold a no-response DOWN on two steps through prepare/after/before and assert `held` contains DOWN, and `blocked` does not.
- **G-c:** `restore(harness state of m1_r17_boot checkpoint 65)` + `invalidate_live_observation()` keeps `_self_from_motion` True.
- **G-d:** T18H, T18G and T18F still pass.
- Print PASS/FAIL per assertion and exit non-zero on any FAIL. Expected on base: G-a FAIL, G-b FAIL, G-c FAIL, G-d PASS.

## 4. Gates
| Gate | Pass |
|---|---|
| T-S1 | suite green (baseline 902 passed, 1 skipped; minus 2 deleted, plus new); T18I all PASS with PYTHONPATH=S1 worktree |
| G18I | merged HEAD: full suite green; T18I, T18H, T18G, T18F all PASS; T18-O2 and T18-O3 output unchanged; golden unchanged |

## 5. LV18f (one live run, orchestrator only, after G18I)
It starts from the locked r17 checkpoint so it exercises the full lab → battle → exit → Pallet stretch.
- **Ledger:** 10313 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap 250 calls; **stop if the ledger would pass 10563**. Viewer up (`curl -s http://127.0.0.1:8765/state`), else `python scripts/watch_run.py --no-browser` in the background.
- **Run:** copy `runs/m1_r17_boot` to `runs/m1_r18f_odo` (never resume an original), then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18f_odo --resume --from-step 65 --steps 85 --max-calls 250`.
- **Metrics** (truth[N+1] vs tile[N]; truth sprites to convert screen to RAM):
  1. Unchosen presses: presses.jsonl keys minus the chosen skill's inputs. Target 0.
  2. `self_locked` on world steps. Target at least 90% (r17: 114/150).
  3. Walls into an actor's tile (blocked direction whose RAM target held a sprite at that step). Target 0.
  4. Lab re-entries not targeted by the chosen option. Target 0.
  5. The lab exit through the bottom door within 15 world steps after party ≥ 1, or why. What it does in Pallet afterwards: places reached, crossings.
  6. One RAM−tile offset per (place, map) visit. Odometer gap 0. `dropped` reported separately for world-type steps (target 0) and non-world steps (by design).
- LV18f informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r18fix4\<WP>\` and gate scripts at `.tmp\r18fix4\g\`.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `<BASE>` and the §6 rules pasted. You alone review diffs, run gates, merge and run LV18f.
1. **Wave 0:**
   - `git status` must show only the user's files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`.
   - Apply §2.1 and commit it with this brief as `docs: r18-fix4 plan and approved contract amendment`.
   - Run the suite (expect 902 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - S1 and S2 in worktrees `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r18iS1` (`-b r18i/S1`) and `...\r18iS2` (no commit).
   - Copy `t18i.py` to the main `.tmp/r18fix4/g/` and run it on base: it must give G-a/G-b/G-c FAIL and G-d PASS. Otherwise review the script against §3 before going on.
   - Review S1: the diff must be deletions plus the two single conditions; no new class, module, gate or option filter. Run T-S1. Allow one repair round with the exact failing output; stop if it fails twice. Merge `--no-ff`.
3. Run **G18I**, then **LV18f** once.
4. **Report:** save `.tmp/r18fix4/report.md` with commits, net lines per file (expect a net deletion), tests deleted/added/changed (before/after), the gate table, LV18f metrics vs LV18c/d/e and r17, deviations and open issues (the fake top-wall door stays open by scope). Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r18-fix4 result`. Never push.
- **Stop and ask the user when:** a §2 point cannot work as written; a non-pre-approved assertion fails after one repair; the golden data changes; T18F/G/H, T18-O2 or T18-O3 change; the ledger would pass 10563; `git status` differs from wave 0's expectation; a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### S1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix4, WP S1 (one self-spot calibration, no blind probe). Worktree: <WORKTREE> (branch r18i/S1 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix4.md §1, §2, §3 S1; agent/loop.py step (probe block ~137-158), invalidate_live_observation, restore; agent/world.py probe, before, observe_after, _self_index, _actor, state, restore; tests named in §3.
Build exactly §2: P1 delete World.probe and the loop probe block (trace keeps "probe": null); P2 stop clearing self_spot/_self_contradictions/_self_from_motion in invalidate_live_observation; P3 one extra condition in before()'s actor_tiles. Add the ponytail ceiling comment. No new class, module, gate or filter.
Pre-approved test changes are exactly §3 S1 (two deletions, one assertion change). Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN; golden must not change.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix4\S1\]
Done when: suite green; one commit "S1 one self-spot calibration, no blind probe"; report the diff per file with net lines, tests deleted/added/changed before/after, and the suite summary line.
```

### S2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix4, WP S2 (offline gate T18I). Worktree: <WORKTREE> (from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix4.md §1, §2, §3 S2; .tmp/r18fix3/g/t18h.py (pattern); .tmp/r18fix4/c1_probe.py; agent/loop.py step, invalidate_live_observation, restore; agent/world.py before, prepare, after, probe; tests/agent/fakes.py.
Write .tmp/r18fix4/g/t18i.py with G-a..G-d. Copy every checkpoint file you read to .tmp/r18fix4/S2/ first; never write in runs/. Zero model requests (feed recorded trace perceptions); print PASS/FAIL per assertion; exit non-zero on any FAIL; never edit agent/ or tests/; never run scripts/run_agent.py. Expected on <BASE>: G-a, G-b, G-c FAIL; G-d PASS.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix4\S2\]
Done when: the script gives that result; report its path, its output on <BASE>, and why each FAIL is the defect and not the harness.
```
