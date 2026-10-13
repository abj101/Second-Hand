# TL: Build the lean tracking layer and get the agent out of the house

Status: gate passed (build, suite and L2 run completed per §0; progress stalled, health red; STOP 5 review, 2026-10-12) · Branch: feat/lean-agent · Program: [program-r1-wm.md](program-r1-wm.md) row 10 · Spec: [tracking-layer.md](tracking-layer.md)

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

## 7. Result (2026-10-12)

**Completion:** B1–B4 built and reviewed, both required full-suite runs green, L2 completed 100 steps (71–170), runner exit 0. Per §0, health/progress report rather than gate. **STOP 5: progress remains stalled; health is red.** No further live spend in this phase.

### L2 progress first
- Command: `.venv-agent/Scripts/python.exe scripts/run_agent.py --run runs/m1_long --resume --from-step 70 --steps 100 --max-calls 110 --stage c`.
- Resumed checkpoint 70 with its memory; finished checkpoint `runs/m1_long/checkpoint/000170/`. Before spending, copied L1 to `runs/m1_long_l1/` and SHA-256 verified all 112 files.
- Truth samples: house 1F (`0x25`) at 71; house 2F (`0x26`) at 72; 1F at 73; 2F at 74; 1F at 76; 2F at 80; 1F at 81 through terminal truth 171. These are before-action sample steps, not exact warp times. Both maps were already known in L1.
- **Town: not reached. Lab: not reached. Party: 0 throughout.** Final truth: map `0x25`, tile `(7,1)`, party 0. No new truth map reached.
- Probes: step 71 = 2 presses, unlocked, stopped on the 1F→2F blank/cut; step 72 = 1 press, locked. Two probes / three presses. Lock starts at 72 and persists to 170.
- Worst no-effect window: **81–170 (90 actions), zero inputs, all blocked `target is the player`, one frame hash**. Longest same-place/action repeat: 84–170 (87 actions).
- Frames: [81](../../runs/m1_long/frames/000081.png), [84](../../runs/m1_long/frames/000084.png), [170](../../runs/m1_long/frames/000170.png). Frames 84 and 170 inspected; the scene is unchanged.
- Step 170 act `why`: “I want to interact with the NPC to see if they provide any information or quests.” Its affordance point `[0.443,0.468]` is on the true player; the corrected entity is named `NPC` but has `kind=player`. Role correction has not repaired the name/affordance association. At 78 the role swap already occurred; the model still selected that NPC affordance.
- The true streak is present in feedback (`recent: no effect x89` before action 170; checkpoint `Stuck.n=90`). The model continued the same choice.

### L2 health, steps ≥ 71
Commands: `.venv-agent/Scripts/python.exe scripts/eval_run.py runs/m1_long --no-write --health --from-step 71` and the same command without `--health` for JSON details. Both exit 0; no evidence summaries overwritten.
- `self_spot_accuracy=1.0`: **99/99** locked world steps measured within 8 px of truth sprite 0; `self_spot_coverage=0.99`: **99/100** world steps locked.
- `place_ids_per_map`: **2 each**, red. 1F = `p_world,p_world_3`; 2F = `p_world_4,p_world_6` (live, non-merged IDs from measured rows).
- `cut_self_loops=0`, `screen_places=0`, `tile_accuracy=1.0` (100/100 reported), green.
- `no_effect_streak_max=90`, `repeat_actions_max_length=87`, red.
- `false_walls=0` detected. This is a **lower bound**: only consecutive collinear end-tile crossings are reconstructable; probes, cuts, place changes, drift fixes and diagonal/multi-axis paths are skipped.
- `probe_unmeasured_world_steps=2`: runner truth is sampled before probe, while act frame/place are after probe. Those rows are excluded from before-place ID counts. Post-action spot uses truth at `step+1`; pixel distances use native PNG dimensions and sprite centre `(x+8,y+8)`.
- Tile accuracy retains the existing per-visit offset method; its 100/100 is not independent evidence that probe-time place alignment or all routes are correct. The two probe rows still appear in that legacy tile metric.
- Execution done **6/100**; 110 successful uncached cloud asks plus 1 cache hit, 110 HTTP attempts, 0 retries. Perception reuse 0.96. Historical duplicate/progress aggregates remain historical; `new_after_resume` in the legacy duplicate metric still means after step 54, not the TL start at 71.

### Slice checks and repairs
All commands below use `.venv-agent/Scripts/python.exe`, 0 game-model requests.
- **B1:** parent `-m pytest tests/agent/test_memory.py tests/agent/test_space.py tests/agent/test_skills.py tests/agent/test_world.py -q -p no:cacheprovider`: **81 passed in 84.31s**. Strengthened inferred-exit test to exercise the actual DOWN reverse direction; parent rerun **1 passed in 0.16s**.
- **B1 dupes:** `scripts/check_track.py dupes`: furniture 3 rows at distinct tiles `(-2,-3),(-1,0),(0,1)`, RED 1, rival 1. One row per furniture item; historical nested suffix labels remain, base matching no longer cascades.
- **B2:** parent observer/skills/world tests with `-k 'residual or motion_self_spot or self_at or step_remeasures or step_keeps_position_unknown'`: **5 passed, 59 deselected in 2.51s**.
- **B2 self:** `scripts/check_track.py self`: m1 first LEFT locks at **5 px**; m1_long first four compact heat masses `0.792,0.731,0.779,0.709` miss the 0.80 requirement, fifth LEFT locks at **2 px**. Numeric miss carried forward under §0; thresholds stayed 80% and 1.5 tiles. Broad heat boxes printed as required.
- **B3:** initial parent complete loop check found a re-perception reply queue mismatch; a frame spy also proved act had a pre-probe frame with post-probe perception. One repair round corrected frame/size, actual lock checks and streak trimming, with affected fixture inputs adjusted rather than assertions weakened.
- **B3 repaired:** parent `-m pytest tests/agent/test_loop.py -q -p no:cacheprovider`: **45 passed, 1 warning in 137.90s**. Parent focused probe/re-look/frame/streak checks: **4 passed, 75 deselected in 6.20s**.
- **B3 probe:** `scripts/check_track.py probe`: `presses=2 locked=False self_spot=None distance=None`. RAM diagnostic confirmed map 37→38 with a blank/cut, so it safely stopped at a warp; lock target not met, carried forward.
- **B4:** subagent normal focused suite (eval/world/loop/golden): **111 passed, 1 warning in 167.55s**. Parent all-step `eval_run.py runs/m1_long --no-write --health` runs: legacy spot accuracy unknown / coverage 0, tile accuracy 1, streak/repeats 33, screen places 6. Parent `--from-step 71` before L2 correctly reported an unmeasured segment.
- First parent full suite: **1 failed, 232 passed, 1 warning in 250.15s**; only failure was a legacy three-value `_step` mock in `test_type_text.py:116`. One B2 repair updated that fixture to four values, leaving all three scenario assertions. Parent single case: **1 passed in 0.14s**.
- **Full suite before L2:** `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` → **233 passed, 1 warning in 249.27s (0:04:09)**.
- **Full suite at phase end:** same command → **233 passed, 1 warning in 248.98s (0:04:08)**. Read pytest's own summary lines. Only warning: installed `pysdl2-dll 2.32.10` binaries.
- `git diff --check` clean after B1 removed its extra EOF blank line. Line-ending conversion notices are informational.

### Golden data review
Regenerated only via `W0_REGEN=1 .venv-agent/Scripts/python.exe -m pytest tests/agent/test_world_golden.py -q -p no:cacheprovider` (PowerShell environment set only for that invocation, then unset). Subagent confirmed invocation but did not retain a standalone regeneration summary; the later normal 111-test check and both parent full suites verify the result.
- `act/memory/entities.jsonl`: **44→17 upsert rows, same 4 unique entity IDs**. B1 stops observing the same cached perception twice; W4 unknown-anchor classification/evidence writes are removed. Final player/door seen counts 15→3. Genuine tile/status/last-seen updates and still-used screen/region information remain.
- `act/trace.jsonl`: same 8 actions and outcomes. Adds `probe,self_spot,self_locked`, `here:` facts and true no-effect feedback. Fixture starts with an established motion lock so its static perceptions continue to test the original route; probe behavior is tested separately.
- Places, relations, seen and tiles goldens unchanged. Force-stage `tests/agent/data/w0_golden`.

### Exact contract delta proposed for the user
No edits to `contracts.md`; TL §0/§4 authorizes drafting these changes here for later application.

Add to **observer**:
```python
def residual(frames: list[Image], shifts: list[tuple[int, int]]) -> tuple[float, float] | None
    # frames includes the initial frame; shifts has the same length, starts (0,0),
    # and is cumulative content scroll from that initial frame to each frame.
    # A compact box must hold >=80% of residual heat inside 1.5 inferred tiles.
    # Returns a screen-fraction centre, or None.
```
The implemented tile estimate is the largest cumulative shift of one direction press (marked `ponytail:`); midpoint compensation assumes following-camera motion. Fixed-camera tracking and overlays remain deferred.

Replace **skills** move field and add the last-motion result:
```python
moves: list[dict] = field(default_factory=list)
    # one direction press: {"dir": str, "shift": tuple[int,int] | None,
    #                       "self_at": tuple[float,float] | None}
self_at: tuple[float, float] | None = None
    # last non-None self_at across the skill, including sequence/travel aggregation
```
Private `_step` now returns `(settle, shift_or_none, blank_seen, self_at)`; `fold`/World prepare still accept legacy tuple move records. Any uncertain frame shift causes a whole-move remeasurement; if also unknown, no position is advanced.

Replace the W4 learned-self semantics under **world** with:
```python
def probe(self, ctx: Ctx) -> dict
    # Loop invokes before act only on a world screen with no modal, known tile size,
    # and no motion lock. Up to 4 distinct currently-unblocked direction presses.
    # Folds moves; stops on lock or a pixel-observed cut/blank/screen transition.
    # Returns {"presses": int, "locked": bool}; a performed probe also carries
    # internal "skill" and "scene_cut" fields for Loop's place update.
```
`self_spot` locks on the first residual box from a >=1-tile scrolled move, survives cuts/menus, and unlocks after two consecutive scrolled boxes more than one tile away. An unknown box breaks the contradiction streak. Persist `self_spot,self_from_motion,self_contradictions`; unlocked `pa` falls back to the current model player point. `fix_player` still repairs roles on cached perceptions using the motion lock. Remove `classify,_learn,cam_px,evidence,POS_TOL,ANCHOR_EVIDENCE` and the three-sighting median identity rule. Old state keys are ignored; `anchors` remains round-tripped for compatibility, with no median identity learning. Still-used HUD/region/transient information remains.

Add to **trace**, always present:
```json
{"self_spot": [0.45, 0.4444444444444444], "self_locked": true,
 "probe": {"presses": 1, "locked": true}}
```
`self_spot` is null when absent; `probe` is null when no probe ran; spot/lock are the post-action state. Checkpoint harness additionally persists `reperception_used` (one forced re-look per continuous >=2 no-effect streak, reset below 2).

Add evaluator fields and option:
```text
--from-step N: select trace/call/per-step health measurements with step >= N;
              historical map/party progress aggregates remain historical.
self_spot_accuracy = correct within 8px / measured locked world steps (None if none).
self_spot_coverage = locked world steps / world steps (None if none).
self_spot_correct, self_spot_measured, self_locked_world_steps, world_steps: counts.
false_walls: distinct recorded blocked sides later contradicted by a measurable crossing;
             a lower bound from collinear end-tile evidence.
probe_unmeasured_world_steps: count of moving-probe rows with no aligned pre-act truth.
```
Health uses self-spot accuracy/coverage in place of player-role errors; removes seen-in-bounds from health (legacy JSON diagnostic retained). No health row controls actions or further run authorization.

### Budgets, scope and remaining issues
- Lines: `agent/` **2263→2369 (+106)**, scripts **2892→3122 (+230)**, tests **3525→3775 (+250)**. Report only; no trimming gate.
- Game-model HTTP requests: **0 through B4; L2 110/110**. Ledger before: total 767 / M1 749 / stage_c 196; after: **total 877 / M1 859 / stage_c 306**; M1 remaining **191**. Full after ledger: `{"total":877,"smoke":18,"m1":859,"stage_a":142,"stage_b":297,"stage_p":25,"stage_q":60,"stage_c":306}`. No reset/refund/cap increase. Runner exit 0; no exit-3/4 event.
- Four subagents, each `gpt-6-luna` HIGH, one slice each. Orchestrator reviewed diffs and ran acceptance; code fixes stayed delegated. No Opus execution.
- Scope clarifications: shared check script belongs to packet §2; B4 trace additions touch `agent/loop.py` per the explicit card. Affected loop/golden fixtures and the late `test_type_text.py` mock are within the cards' tests scope. Fixed the packet command's missing space `110--stage` to `110 --stage`. Read-only evaluation used `--no-write` throughout.
- Protected L1 backup retained; `runs/m1`, `runs/m1_act`, model cache, `loop/` and contracts preserved. Untracked `.claude/` preserved outside the commit. No runs/.tmp staging or push.
- **Open for STOP 5:** repair name/affordance consistency after player-role correction, and inspect cached IDs after place merging; fix place fragmentation/readable-label echo (`act.place="world [p_world_6]"`, persistent `merge_pending`); determine why the act model ignores the player-target/streak feedback. These are follow-up issues, not new goals/filters/gates in this phase.
- No additional live spend; next work needs the user's STOP 5 decision and an approved packet. Contracts above await the user's application.

## 8. Handoff
Rewritten in `docs/plans/handoff-notes.md`, under 80 lines with the fixed headings.
