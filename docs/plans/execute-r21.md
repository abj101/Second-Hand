# Executor brief: Avatar round r21: walls that are really walls, honest exits, clean place memory, cheaper menus (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `b24b7d2` (the r20 result). Check data: `runs/m1_r20_odo` (LV20, steps 151–210, has `segments` and `seqs`), `runs/m1_r19_odo` (battle selects), all read-only. `.tmp/r20/` holds `report.md`, `walls.json` and `D/diagnosis.md`.

## 1. Verified diagnosis (Opus, from `runs/m1_r20_odo`; inferred claims are marked)
**I1, a door bump during its own crossing becomes a wall (r19 brief P5, never built):**
- LV20 step 160: the door push DOWN at seq 15511 is a `(0,0)` bump inside the segment that ends with cut `"blank"` at 15541.
- `_odometer_drain` files it under `_bumps`. `_odometer_fold` turns every `(0,0)` press (bump *or* press) into a move. Then `space.fold` writes `blocked DOWN` on `p_place_6@0,0`, the door it just crossed.
- The same happened in LV18g step 155.

**I2, an NPC goes "gone" after a single missed perception, and its tile becomes a wall:**
- Step 201: the agent talks to `e_npc_6`, one tile up (`[0,-18]`). Steps 202–208 are dialogue.
- Step 209 (world screen, no text): perception lists only the player and a sign.
- `World._follow` (`world.py:251-256`) marks every actor whose remembered tile is in the view rect but was not perceived as `status: "gone"`. That takes **one** omission and no pixel evidence. Checkpoints confirm it: 205 says `seen`, 210 says `gone`.
- `actor_tiles` (`world.py:223`) comes only from this frame's perceived actors. So when the act picks "check stone, 1 up" and presses UP (step 210 shows the NPC's dialogue again), `fold` receives no actor tile and writes `blocked UP` on `[0,-17]`.
  - The "stone" `e_stone_9` at `[0,-18]` is a top-left-corner perception from step 199's post-battle frame (`at` [30,20], "top wall"). Its tile is a correct projection of that point. It is the remembered row the option came from.
- Static things already use the right rule: a miss counts only on a distinct world frame, inside the inner view rect, and `MISS_TO_STALE` = 2 misses make a thing `stale` (`world.py:371-377`). Actors skip it.

**I3, screen-edge "exits" never stop being offered:**
- `e_exit_south` at `exit:0,8` ("bottom wall") is past Pallet's south wall line.
  - Travel stopped short at steps 157, 171 and 172 (`reach` row n=2: stopped at (2,8), then (1,8)). The walls recorded are `[2,7]` DOWN and `[1,7]` DOWN.
  - `_refuted` counts only blocked edges *into the exit tile itself*, so the exit stays "unverified (never crossed)". The planner targeted it at 171.
- `e_exit_south_2` at `[5,-5]` (placement "bottom wall", `at` y 0.85) is the screen's bottom edge, perceived at step 181 while the player walked north. The ground south of it is floor the agent had just walked (`[5,-4]`…`[5,-1]`). It is not a boundary, yet step 188 travelled back to it and failed.
- Options never hide rows (by design). The fix is the *status* evidence: what the exit row and the view say about it.

**I4, lab objects written into Pallet's memory (verified from the inherited boot steps):**
- Step 69: `scene_cut` true during dialogue (RAM map 0 → 40, the scripted walk into the lab). `cut_carry` correctly keeps `p_place_4` while steps 70–87 stay dialogue (`world.unsettled` is true: "where you are is not known yet").
- Step 87: the after-frame is the lab with no text box. `observe_after` (`world.py:650`) calls `mem.observe` under `next_place = p_place_4` anyway. That writes 15 lab rows into Pallet (`first: 87`: shelves, display case, posters, "house"…).
- Step 88: `match_place` sees the carried cut and mints `p_place_5`. That is correct, but too late.
- Later, position identity (`STATIC_GATE`) re-matched some of these rows to real Pallet things. For example, `e_shelf_5` is now named "sign" with aliases barrel/shelf/device/control panel/mailbox/fence. That is why steps 166–170 "checked a control panel / display case / shelf" outdoors.
- Root cause: an observation is written while its frame's place is unresolved.

**I6, battle cost (from r20 D2, `.tmp/r20/D/diagnosis.md`):**
- LV19: 16 of 78 battle requests were `select`'s menu read ("items, cursor, layout"), and every one repeated a menu already read on the same screen id.
- The user chose **option (a): reuse the stored menu layout per screen id, plus current cursor evidence.**

## 2. Design (approved by the user 2026-10-25; stop and ask if any point cannot work as written)
**P5, a crossing press is not a bump (I1).**
- In `_odometer_drain`, a bump (`(0,0)` or no-response) is a *crossing press* when it is the last press or bump of a segment that ends with a cut. Mark it `crossing: True` and file it under `_presses`.
- `_odometer_fold` never turns a `crossing` press into a move. It stays in the segment log's `presses`.
- One condition. No change to cut detection or travel.

**P6, actors leave on evidence, and a remembered actor holds its tile (I2).**
- In `_follow`, a perception omission of an actor is a **miss**, counted the same way as static things: a distinct world frame, inside the inner view rect, `misses += 1`.
  - The actor becomes `gone` only at `MISS_TO_STALE` misses, or at once when the pixel tracker reports its track lost (`tracker.gone()`, already real evidence).
  - Being matched resets `misses` to 0 (check `mem.observe`; add it if it does not).
- `actor_tiles` also holds the tiles of remembered actors in the current place whose status is not `gone`/`stale` and whose `last` is within `ACTOR_STALE_STEPS`.
  - Held means what it already means in `fold`: a bump into it writes no wall. Routes treat it as `occupied`, as now.
- Ponytail ceiling: a remembered actor that silently walked away holds its old tile until 2 misses. A real wall there goes unrecorded for those steps.

**P7, exit status tells the truth about unreachable and scrolled-past exits (I3).** In `_exits`, an unverified exit (`uses == 0`) is also `refuted` when either:
- (a) its tile's `reach` row has `n >= 2`, meaning travel stopped short twice and was never reached since; or
- (b) a known floor tile of the same place lies beyond it in its outward direction (`space.outward` of its placement; same column for UP/DOWN, same row for LEFT/RIGHT). The ground continues past it, so the screen edge was not a boundary.

Rules that go with it:
- A crossing (`uses > 0`) always wins.
- `mark_reached` resetting `reach` un-refutes (a).
- The refuted text in `view`/options gives the reason: "not reached N×" or "ground continues beyond it", next to the existing "walked into N×".
- The row stays listed with its state; no hiding.
- Ponytail ceiling: (b) uses known floor only, not reachability.

**P8, nothing is written to place memory while the place is unresolved (I4).**
- While `world.unsettled` is true (a carried cut, place not yet matched), `observe_after` and the after-path `_record_view` write no entities, views, looks or tiles.
- They also leave `_observed_per` unset, so the next step's `before()` observes the same frame after `match_place` has resolved the place. Check the reuse path at `world.py:221`.
- The `unsettled` flag is already set by the loop (`loop.py:135`); no new state.
- Also verify that an after-frame inside the same step as a fresh `scene_cut` with an unresolved `next_place` is covered, or report the gap.

**C1, clean the LV21 run copy (data, not code).** A scratch script `.tmp/r21/C1/clean.py <run>`:
- removes from the run's resume source (the `checkpoint/000210/memory/` that `--resume --from-step 210` loads; confirm in `scripts/run_agent.py`) every `entities` row of `p_place_4` with `first` in 70–87;
- removes every `relations`/`knowledge` row whose subject or object is one of those ids.

It uses the store's own removal mechanism if `agent/memory.py` has one; otherwise it rewrites the jsonl without those ids, keeping the format. It writes the removed ids and rows to `.tmp/r21/C1/removed.json`. It runs only on the LV21 copy, never on `m1_r20_odo`.

**P9, `select` reuses a known menu (I6, option a).**
- The screens store (keyed by `Memory.screen_id`) gains, per screen: `layout`, the model's ordered `items`, and `cursors`, a map from cursor index to a pixel signature of the menu region.
  - The menu region is the bounding box of the perceived selectable entities' `at`, padded by one tile (or 8px when `tile_px` is unknown), cropped from the start frame.
- `run_affordance` passes the perception to `select`. The loop passes the screen's stored row in, and saves what `select` learned back to the screens store.
- In `select`:
  - If the stored row has a layout other than `grid`, and exactly one stored signature matches the current crop (`observer.changed(a, b) <= .05`, as the HUD uses), take the cursor from it and the order from the stored `items`. Press with **no model call** (`calls` 0).
  - Otherwise read the menu with the model exactly as today and store the signature under the read cursor index.
  - Grids (keyboards) always read.
- The act still chooses the item; nothing auto-picks. The step's post-perception verifies the result as today.
- Ponytail ceiling: a menu region with changing numbers never matches and falls back to the read.

### 2.1 Contract amendments (approved by the user issuing this brief; the orchestrator commits them in wave 0)
Under `## world`, below the r20 lines:
```
    # r21 (approved 2026-10-25): the last bump of a segment that ends in a cut is that crossing's press, never a wall (P5).
    # An actor omitted by perception counts misses like a static thing; gone at MISS_TO_STALE misses or on pixel-track
    # loss; a remembered live actor's tile is held (no wall) (P6). An unverified exit is refuted when travel stopped short
    # of it twice, or known floor lies beyond it outward (P7). While unsettled, nothing is written to place memory (P8).
```
Under `## skills`, replace the line ``- `select` uses one model call (read the items and cursor), then the presses, then A.`` with:
```
- `select` reads the items and cursor with one model call, unless the screen's stored menu (layout, items, cursor
  signatures) matches the current menu region exactly once; then it uses no call (r21, approved 2026-10-25). Grids always read.
  Then the presses, then A. The step's own post-perception verifies it.
```
Also amend the `select` signature line to `def select(ctx, item: str, perception: dict | None = None, menu: dict | None = None) -> SkillResult` (or the shape B1 builds, reported).

## 3. Work packages
| WP | Goal | Owns | Depends on | Tier |
|---|---|---|---|---|
| M1 | P5 + P6 + P8 + tests | agent/world.py (`_odometer_drain`, `_odometer_fold`, `_follow`, the `actor_tiles` line in `before`, `observe_after`, `_record_view`), agent/memory.py (`observe` misses reset only), tests/agent/test_world.py, tests/agent/test_loop.py | wave 0 | gpt-6-luna HIGH |
| X1 | P7 + tests | agent/world.py (`_exits`, `_refuted`, `view`'s `exit_text`, option notes for exits), tests/agent/test_exits.py, tests/agent/test_options.py | wave 0 | gpt-6-luna HIGH |
| B1 | P9 + tests + offline LV19 check | agent/skills.py (`select`, `run_affordance`), agent/loop.py (screens save around line 357 and the skill call), agent/memory.py (screens row only), tests/agent/test_skills.py, tests/agent/test_loop.py (select tests only) | wave 0 | gpt-6-luna HIGH |
| C1 | clean.py, zero code in agent/ | `.tmp/r21/C1/` only | wave 0 | gpt-6-luna HIGH |

All four run in parallel. M1 and X1 both edit `world.py` in disjoint functions, and M1 and B1 both touch `memory.py`/`test_loop.py` in disjoint places. Merge in the order M1, X1, B1. A merge conflict means stop and report; never resolve it by rewriting another lane's code.

### Pre-approved test changes
- **M1:**
  - Any assertion that an actor turns `gone` after one omitted perception now expects `misses` 1 and the status unchanged, then `gone` at the second distinct miss.
  - Assertions on `actor_tiles` contents may add remembered live actors.
  - List every one before/after.
- **X1:** assertions on exit `status` or exit text, only where the fixture has a `reach` n ≥ 2 or floor beyond the exit. List them.
- **B1:** assertions that pin `select`'s call count to 1 now expect 1 on the first read and 0 on a matching known menu. The `select` signature may gain the optional arguments.
- Any other failing assertion: stop, revert, report. Never set `W0_REGEN`. If golden data changes, stop and ask (P5/P6 may change T18-O3 wall rows; see the gates).

### New tests (minimum)
- **M1:**
  1. A segment ending in a cut whose last press is a `(0,0)` bump: no wall, and the log shows it under `presses` with `crossing`.
  2. The same bump not last, or in a segment without a cut: wall as today.
  3. An actor omitted once stays `seen`/`remembered` with misses 1; omitted on a second distinct frame it becomes `gone`; tracker loss makes it `gone` at once.
  4. A remembered live actor's tile with no perception of it: a bump into it writes no wall.
  5. With `unsettled` true, `observe_after` on a world frame writes no entity row. The next `before()` after a new `match_place` writes the rows under the new place.
- **X1:**
  - An exit with `reach` n=2 is refuted with the "not reached" reason, and un-refuted after `mark_reached`.
  - A bottom-wall exit with known floor below it is refuted. Without floor below, it is unverified.
  - A crossed exit is never refuted by either rule.
- **B1:**
  - First select on a screen id: 1 call, and the signature is stored.
  - Second select on the same screen with the same cursor crop: 0 calls and correct keys.
  - A different crop: 1 call.
  - Grid layout: always 1 call.

### B1 offline check (zero model requests)
Using `runs/m1_r19_odo` (copy what you read to `.tmp/r21/B1/` first):
- Replay the store over every select step in order. Use the trace row's perceived entities, the before frame, and the logged skill reply in `calls.jsonl`.
- Report how many reads would be skipped, and for each skipped one whether the reused cursor equals the cursor the model read on that frame.
- Pass: 100% agreement on skipped reads. Report the count saved (the ceiling is 16).

## 4. Gates
| Gate | Pass |
|---|---|
| T-M1 | suite green (baseline at `b24b7d2`: 922 passed, 1 skipped); changed assertions only as pre-approved. **Live-log check:** feed LV20 step 160's logged segments (trace `segments` + `presses.jsonl` `seqs`) through the M1 drain/fold: no `blocked DOWN` on the crossed door tile. |
| T-X1 | suite green; on a copy of `m1_r20_odo/checkpoint/000210/memory`, `_exits(p_place_4)` keeps the actual `e_exit_south` at `(1,8)` wall-refuted and makes `e_exit_south_2` at `(5,-5)` refuted because ground continues beyond it; Pallet's verified exits remain unchanged. User-approved correction during execution: `(0,8)` is the twice-unreached target, but no exit entity/tile exists there; keep P7 exact-tile, with no adjacent inference. |
| T-B1 | suite green; offline check 100% agreement. |
| T-C1 | `removed.json` lists only `p_place_4` rows with `first` 70–87 and their relations; a dry run on a copy of checkpoint 210 then loads with `Memory` without error. |
| G21 | merged HEAD: full suite green. T18F/G/H/I PASS. T18-O2 unchanged. T18-O3 unchanged, or only wall rows removed that are crossing bumps (P5) or remembered-actor tiles (P6), each listed with RAM evidence. Golden unchanged (if it changes, stop and ask). |

## 5. LV21 (one live run, orchestrator only, after G21)
- **Ledger:** 10768 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap **150** calls; **stop if the ledger would pass 10918**. Viewer up, else `python scripts/watch_run.py --no-browser` in the background.
- **Run:**
  1. Copy `runs/m1_r20_odo` to `runs/m1_r21_odo` (must not exist).
  2. Run `.venv\Scripts\python.exe .tmp\r21\C1\clean.py runs\m1_r21_odo` and keep its output.
  3. Run `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r21_odo --resume --from-step 210 --steps 60 --max-calls 150`.

  One invocation; no rerun, extension or early stop.
- **Metrics** (window 211–270; reuse `.tmp/r20/metrics.py`, `analyze.py`, `walls.py`, `extra.py` with the run/range lists changed):
  1. **Walls:** walls split NPC/prop/scenery; walls on exit tiles crossed that step (target 0); walls on a tile a remembered actor held (target 0); RAM-confirmed sprite overlaps (target 0).
  2. **Actors:** every `gone` transition with its cause (misses, or track loss); actors that came back after `gone`.
  3. **Exits:** exit options chosen while refuted, with the act's `why`; refutations by reason; travel to refuted exits.
  4. **Place memory:** entity rows written while `unsettled` (target 0); options naming things whose `first` lies in an unsettled window (target 0).
  5. **Menus:** selects, skill calls per select, reads skipped, selects whose post-perception showed a different item than chosen (target 0); battle requests per battle step (LV19 3.0, LV20 2.5).
  6. **Progress:** furthest Route 1 RAM y (LV19 24, LV20 25); new places minted; plan rows by trigger.
  7. Unchosen presses 0; lock ≥ 90%; odometer gap 0; false travel crossings 0; per-visit offsets as in r20.
- LV21 informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r21\<WP>\` and gate scripts at `.tmp\r21\g\`. agent/ stays game-agnostic. C1's script may read RAM-free run data only; it must not touch `runs/` except the path it is given.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `b24b7d2` and the §6 rules pasted. You alone review diffs, run gates, merge and run LV21.
1. **Wave 0:**
   - `git status` must show only the user's five files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`. HEAD is `b24b7d2`.
   - Apply §2.1 to `docs/plans/contracts.md` and commit it with this brief as `docs: r21 plan and approved contract amendment`.
   - Run the suite (expect 922 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - Start M1, X1 and B1 in `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r21<WP>` (`-b r21/<WP>`), and C1 in `...\r21C1` (no commit; output under `.tmp/r21/C1/` in the main checkout).
   - Review each diff: its P only; no new class, module, production gate or option filter; options never hidden.
   - Run each T-gate. Allow one repair round with the exact failing output; stop if it fails twice.
   - Merge `--no-ff` in the order M1, X1, B1.
3. Run **G21**, then **LV21** once.
4. **Report:** save `.tmp/r21/report.md` with:
   - commits and net lines per file;
   - tests deleted/added/changed (before/after);
   - the gate table, including the B1 offline numbers and the C1 removed list;
   - LV21 metrics against LV20 and LV19;
   - deviations and open issues. The lab "shop" label, the `replan` misuse and segment fates left `held` stay open by scope.

   Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r21 result`. Never push.
- **Stop and ask the user when:**
  - a §2 point cannot work as written;
  - a non-pre-approved assertion fails after one repair;
  - a merge conflicts;
  - the golden data changes;
  - T18F/G/H/I or T18-O2 change, or T18-O3 changes other than as allowed;
  - the ledger would pass 10918;
  - `git status` differs from wave 0's expectation;
  - a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### M1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r21, WP M1 (crossing press is not a wall; actors leave on evidence; no writes while unsettled). Worktree: <WORKTREE> (branch r21/M1 from b24b7d2). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r21.md §1 I1/I2/I4, §2 P5/P6/P8, §3 M1; docs/plans/contracts.md "world"; agent/world.py _odometer_drain, _odometer_fold, _follow, before (actor_tiles line), _record_view (miss counting), observe_after, MISS_TO_STALE/ACTOR_STALE_STEPS; agent/space.py fold (actors); agent/memory.py observe; agent/loop.py step lines 118-137 (cut_carry, unsettled); agent/track.py gone.
Build exactly P5, P6 and P8 with ponytail ceiling comments. No new class, module, gate or option filter; no game words.
T-M1 live-log check: copy runs/m1_r20_odo trace row 160 and presses.jsonl to .tmp\r21\M1\ and feed the logged segments through your drain/fold in a scratch script; show no blocked DOWN on the crossed door tile.
Pre-approved test changes are exactly §3 M1. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r21\M1\]
Done when: suite green; one commit "M1 crossing presses, actor misses, no writes while unsettled"; report diff per file with net lines, tests changed (before/after) and added, the live-log check output, and the suite summary line.
```

### X1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r21, WP X1 (exit status for unreachable and scrolled-past exits). Worktree: <WORKTREE> (branch r21/X1 from b24b7d2). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r21.md §1 I3, §2 P7, §3 X1; agent/world.py _exits, _refuted, view (exit_text), options (exit rows and notes); agent/space.py outward, push_dirs; agent/memory.py mark_unreached, mark_reached; tests/agent/test_exits.py.
Build exactly P7 with ponytail ceiling comments. Rows are never hidden; only status and text change. No new class, module, gate or option filter; no game words.
T-X1: on a copy of runs/m1_r20_odo/checkpoint/000210/memory in .tmp\r21\X1\, print _exits("p_place_4") before and after your change.
Pre-approved test changes are exactly §3 X1. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r21\X1\]
Done when: suite green; one commit "X1 unreachable and scrolled-past exits are refuted"; report diff, tests changed/added, the before/after _exits table, suite line.
```

### B1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r21, WP B1 (select reuses a known menu). Worktree: <WORKTREE> (branch r21/B1 from b24b7d2). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r21.md §1 I6, §2 P9, §3 B1 and the offline check; .tmp/r20/D/diagnosis.md (F); docs/plans/contracts.md "skills"; agent/skills.py Ctx, select, run_affordance; agent/memory.py screen_id, observe_screen, screen_reply; agent/loop.py where run_affordance is called and lines 356-363; agent/observer.py changed.
Build exactly P9 with a ponytail ceiling comment. The act still picks the item; no auto-pick, no new gate or module; no game words.
Offline check: copy what you read from runs/m1_r19_odo to .tmp\r21\B1\ first; zero model requests.
Pre-approved test changes are exactly §3 B1. Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r21\B1\]
Done when: suite green; one commit "B1 select reuses a known menu"; report diff, tests changed/added, the offline table (selects, skipped, agreement), suite line.
```

### C1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r21, WP C1 (clean the LV21 run copy; no agent/ code). Worktree: <WORKTREE> (from b24b7d2; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r21.md §1 I4, §2 C1; agent/memory.py (store format, any removal mechanism); scripts/run_agent.py resume path (which memory a --resume --from-step loads).
Write .tmp\r21\C1\clean.py <run> as specified. Test it only on a copy of runs/m1_r20_odo/checkpoint/000210 under .tmp\r21\C1\dry\; never write in runs/. Zero model requests.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r21\C1\]
Done when: removed.json from the dry run, a Memory load of the cleaned copy without error, and a 5-line summary (rows removed, relations removed, which resume path it targets).
```
