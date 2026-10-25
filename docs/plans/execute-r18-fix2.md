# Executor brief: Avatar round r18-fix2, a press answered by text is not a wall (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `99c96bc` (r18-fix result). Read `.tmp/r18fix/report.md` first.

## 1. Why (verified on runs/m1_r18b_odo)
- Steps 88 and 90 (party 0 in truth): the route DOWN reached RAM (5,6). The game's script froze the player, opened a text box ("don't go away yet") and moved the player back to (5,5) on step 89/91.
- `skills._step` (`agent/skills.py:54-80`) called that press "no response". The text box lies outside the 1.5-tile ring around the player, and the ring rule deliberately ignores changes outside it (animated tiles), so `reacted` stayed False.
- F1 made no-response presses reach `space.fold`. So p_place_5 (0,-5) got `silent.DOWN=88`, then `walled.DOWN=90`, `blocked:["DOWN"]`: a false wall on a walkable tile.
- After the starter (party 1 at step 111), that wall cut the only route to the lab's return exit (0,0). The planner then picked the fake top exit (0,-11) = RAM (5,0) and pressed into the top wall at steps 111–125 (a 15-step no-response streak).
- The evidence is written while the press happens (inside travel legs at `agent/world.py` ~1499–1559, and in `prepare`), before the after-perception exists. A filter at fold time cannot see the answer, so the decision must be deferred.

## 2. Design (implement exactly this; stop and ask if it cannot work)
Rule: **a press is only evidence of a wall if the game showed nothing in reply. If the next perceived screen is not a world screen (dialogue, menu, battle…), the game answered and the press leaves no silent or wall evidence.** This is generic: screen type comes from perception, with no game words or new gates.
1. **Hold.** In `World._odometer_fold`'s kind==1 branch, a move with `no_response` (shift None) is no longer appended to `moves`. Instead append `{"place": place, "tile": move["_tile"], "dir": move["dir"], "step": step, "actors": sorted(self.actor_tiles)}` to a new `self._silent = []`. Bumps with shift (0,0) are unchanged (folded immediately).
2. **Resolve once, in `World.before()`** at the start of the next step, using that step's `per` (the first fresh look after the press, on every path: main step, probe, travel; the agent has not pressed anything in between):
   - `per["screen"] == "world"`: for each held entry, call `fold(self.mem, e["place"], tuple(e["tile"]), [{"dir": e["dir"], "shift": None, "no_response": True}], self.tile_px, e["step"], actors=frozenset(map(tuple, e["actors"])))` and ignore the returned pos. The original step is kept, so `space.fold`'s two-separate-steps rule is unchanged.
   - Any other screen: drop the entries, since the game answered.
   - Clear `_silent` either way. Resolve before the place-change branch, because entries carry their own place.
   - If `place` is the unplaced place for an entry, drop it, as fold does today.
3. **Checkpoint.** `state()`/`restore()` carry `_silent` (plain JSON). A missing key restores as `[]`.
4. Unchanged: `skills._step` and its ring rule, the skill's "no response" result, travel stopping on no response, the `self.no_response` counter and notes, `space.fold`, the D2 `_held` path (held segments fold later through the same branch, so their no-response entries resolve at the following `before()`).
5. Ceiling, marked with a `# ponytail:` comment at the hold: an unrelated text that appears right after a real wall bump drops that evidence (costs one re-bump), and a press answered only by text (a locked door) never becomes a wall; its travel-failure notes still persist.

### 2.1 Contract amendment (the user approves it by issuing this brief; the orchestrator commits it in wave 0)
Append under `## world` in `docs/plans/contracts.md`, after the r18-fix comment:
```
        # r18-fix2 (approved 2026-10-25): a no-response press is held, not folded, until the next World.before().
        # If that perception is a world screen it reaches space.fold with its original step (silent, then an unconfirmed
        # wall); any other screen means the game answered (text, menu, battle) and the press leaves no evidence.
        # World.state carries the held presses.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| G1 | §2 + tests | agent/world.py, tests/agent/test_world.py | wave 0 | gpt-6-luna HIGH |
| G2 | gate script T18G (uncommitted), written red first | `.tmp/r18fix2/g/t18g.py` only | wave 0 | gpt-6-luna HIGH |
G1 and G2 run in parallel. G2 never edits agent/ or tests/.

### G1 tests (test_world.py)
1. A no-response DOWN, then the next `before()` sees screen "dialogue": no `silent`. Repeating that on a second step leaves no `blocked`, no `walled`.
2. A no-response DOWN, then the next `before()` sees "world", on two steps: `silent` then `blocked:["DOWN"]`. This is the F1 behaviour, reached through the hold.
3. A travel leg stopping on a no-response press followed by "dialogue": no evidence.
4. `state()`/`restore()` with `_silent` set, then resolution gives the same tiles.
5. An entry's place differs from the resolving step's place (a cut in between): it folds into its own place.
- **F1's two existing tests** (`-k odometer_no_response`): if they assert `silent` right after `prepare`/`after` without a following `before()`, the only pre-approved change is to add one `before()` with a world-screen perception before their assertions. Report before/after. Any other failing assertion: stop, revert, report.

### G2 gate T18G (zero model requests; pattern `.tmp/r18fix/g/t18f.py`, real Loop + GridEnv listener frames + StubModel)
- **S1 (LV18b shape):** a world screen, an agent route DOWN; the press at a trigger tile freezes the player and the next perception is "dialogue". One dialogue step, then a forced move back 1 UP, then the route DOWN again with the same freeze and dialogue. Assert no `blocked`/`walled`/`silent` on that tile. After a third world step, a route DOWN through that tile is planned (`space.route` returns a path).
- **S2 (real wall):** two separate steps pressing UP into a wall with no text. Assert `blocked` contains UP.
- **S3 (regression):** run `.tmp/r18fix/g/t18f.py` and expect 25 PASS / 0 FAIL.
- Print PASS/FAIL per assertion and exit non-zero on any FAIL. Expected on base: S1 FAIL, S2 PASS, S3 PASS.

## 4. Gates
| Gate | Pass |
|---|---|
| T-G1 | suite green (baseline 892 passed, 1 skipped + new tests); T18G all PASS with PYTHONPATH=G1 worktree |
| G18G | merged HEAD: full suite green; T18G all PASS; T18F 25/0; T18-O2 and T18-O3 output unchanged (diff 0 lines) |

## 5. LV18c (one live run, orchestrator only, after G18G)
- **Ledger:** 10132 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap 200 calls; **stop if the ledger would pass 10332**. Viewer up (`curl -s http://127.0.0.1:8765/state`), else `python scripts/watch_run.py --no-browser` in the background.
- **Run:** copy `runs/m1_r17_boot` to `runs/m1_r18c_odo` (never resume an original), then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18c_odo --resume --from-step 65 --steps 60 --max-calls 200`.
- **Metrics** (scripts may read truth; reuse `.tmp/r18fix/lv18b/` scripts):
  1. **No wall on a walkable tile:** every `blocked` direction in the lab, converted to RAM with the per-visit offset, must point into a non-walkable RAM tile. Specifically, no DOWN wall at RAM (5,6). List any violation with its step and the screen that followed.
  2. **Free step** = the first world step with truth party ≥ 1. Report whether the agent leaves through the bottom door (truth map 40 → 0) within 15 world steps after it. If not, say why: the options offered, the choices made, the skill results, and whether the fake top exit was chosen.
  3. No-response steps and the longest streak vs LV18b (18 / 15) and r17 (1 / 1); wall rows vs LV18b (13 / 11).
  4. One RAM−tile offset per (place, map) visit; the lab crossing and return exit at RAM (5,11); odometer gap 0, dropped 0.
- LV18c informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r18fix2\<WP>\` and gate scripts at `.tmp\r18fix2\g\`.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `<BASE>` and the §6 rules pasted. You alone review diffs, run gates, merge and run LV18c.
1. **Wave 0:**
   - `git status` must show only the user's files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`.
   - Apply §2.1 to `docs/plans/contracts.md` and commit it with this brief as `docs: r18-fix2 plan and approved contract amendment`.
   - Run the suite (expect 892 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - G1 and G2 in worktrees `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r18gG1` (`-b r18g/G1`) and `...\r18gG2` (`-b r18g/G2`, no commit).
   - Copy `t18g.py` to the main `.tmp/r18fix2/g/` and run it on base: it must give S1 FAIL, S2 PASS, S3 PASS. Otherwise review the script against §3 before going on.
   - Review G1, run T-G1. Allow one repair round with the exact failing output; stop if it fails twice. Merge `--no-ff`.
3. Run **G18G**, then **LV18c** once.
4. **Report:** save `.tmp/r18fix2/report.md` with commits, tests, assertion changes (before/after), the gate table, LV18c metrics vs LV18b/LV18/r17, deviations and open issues (the fake top-wall exit stays open: it is out of scope here). Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r18-fix2 result`. Never push.
- **Stop and ask the user when:** a §2 point cannot work as written; a non-pre-approved assertion fails after one repair; T18F, T18-O2 or T18-O3 change; the ledger would pass 10332; `git status` differs from wave 0's expectation; a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### G1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix2, WP G1 (a press answered by text is not a wall). Worktree: <WORKTREE> (branch r18g/G1 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix2.md §1, §2, §3 G1; .tmp/r18fix/report.md (LV18b section); agent/world.py before, _odometer_fold, travel (~1499-1559), state, restore; agent/space.py fold; agent/skills.py _step; tests/agent/test_world.py tests matching odometer_no_response.
Build exactly §2: hold no-response presses in _odometer_fold, resolve them once in World.before() by that perception's screen, carry them in state/restore. Do not change skills._step, space.fold, the travel stop rule or the _held path. Add the §3 G1 tests.
Pre-approved assertion change: only adding one world-screen before() to F1's two odometer_no_response tests, if they need it. Any other failing assertion: stop, revert uncommitted edits, report.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix2\G1\]
Done when: suite green; one commit "G1 a press answered by text leaves no wall"; report the diff per file, tests added, assertion changes before/after, and the suite summary line.
```

### G2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix2, WP G2 (offline gate T18G). Worktree: <WORKTREE> (branch r18g/G2 from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix2.md §1, §2, §3 G2; .tmp/r18fix/g/t18f.py (pattern: real Loop, GridEnv listener frames, StubModel); tests/agent/fakes.py; agent/loop.py Loop.step; agent/space.py route.
Write .tmp/r18fix2/g/t18g.py with scenarios S1-S3. It must make zero model requests, print PASS/FAIL per assertion and exit non-zero on any FAIL, and never edit agent/ or tests/. Expected on <BASE>: S1 FAIL, S2 PASS, S3 PASS. Do not run scripts/run_agent.py; run only your own script.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix2\G2\]
Done when: the script runs with that expected result; report its path, its output on <BASE>, and why S1's FAIL is the defect and not the harness.
```
