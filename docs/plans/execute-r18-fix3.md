# Executor brief: Avatar round r18-fix3, routes never step onto a crossed exit (ChatGPT orchestrator + gpt-6-luna HIGH subagents)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `c33bb04` (r18-fix2 result). Evidence run: `runs/m1_r18d_odo` (read-only; a copy of LV18c resumed at step 125 for 30 steps; ledger 10214 → 10292).

## 1. Why (verified on runs/m1_r18d_odo)
- Step 145: the agent left the lab through the bottom door. Truth went map 40 → 0 at (12,12), the trace shows `place_changes` → p_place_4 with `verified_edge` true, and crossing `p_place_5@0,0>p_place_4` was recorded. Pallet pos after arrival: (7,6).
- Step 146: it picked "explore UP, 15 steps". `presses.jsonl` step 146 = `UP UP UP LEFT×5 UP UP`. The first UP stepped onto p_place_4 (7,5), whose tile row holds exit `{"dir": "UP", "to": "p_place_5", "src": "observed"}`. Stepping onto a door warps, so the agent was back in the lab after one press, and the remaining keys were pressed there (truth ends at lab RAM (0,8); crossing `p_place_4@7,5>p_place_5` n=2).
- **Root cause (confirmed in code):** before r18, `World.prepare` folded all moves but the cut move, so an exit was recorded on the tile you press *from*. Since r18 the odometer folds the scroll onto the warp tile, so a crossed exit is recorded on the warp tile itself (r18's accepted exit-tile convention). `space.route` (`agent/space.py:75`) still encodes the old meaning: it forbids pressing `edge.dir` *from* the exit tile, but it lets a route *enter* the exit tile as plain floor. `World.explore_dirs` (`agent/world.py` ~1445) and every other caller plan with `route`, so any path over a door steps through it.
- **Second defect (seen, cause unknown):** `skills.approach` with a route stops on `cut(before, end.frame, saw_blank)` (`agent/skills.py:123`), and it did at step 145. At step 146 it kept pressing 9 more keys after the warp, although the odometer recorded `cuts: 2`. Lane D diagnoses this read-only; no fix is approved yet.

## 2. Design (implement exactly this; stop and ask if it cannot work)
### 2.1 R1: a route never enters an observed exit tile unless it is the goal (`agent/space.py`)
- In `route`'s neighbour loop, also skip `nxt` when its tile row holds `exit` with `src == "observed"` and `nxt != goal`. This applies in both strict and non-strict passes, so the fallback cannot re-open it.
- Unchanged:
  - The start tile may be an exit (you are already standing there).
  - Pressing `edge.dir` from an exit tile stays forbidden, as today.
  - Inferred exits (the guessed way back, recorded on a tile you can stand on, such as the one in front of a door) stay enterable. Their current strict-then-fallback rule is unchanged.
  - `frontier`, `unexplored`, `explore_dirs`, `plan` and the travel legs are unchanged: they inherit the rule through `route`.
- A `# ponytail:` comment at the rule names the ceiling. Observed exits recorded before r18 sit on the tile pressed from, so routes detour around those tiles too; this costs a detour only where such a tile is the only way through. Upgrade path: record whether the cut came with the move onto the tile, if detours matter.

### 2.2 Contract amendment (the user approves it by issuing this brief; the orchestrator commits it in wave 0)
Append under `## space` in `docs/plans/contracts.md`:
```
- **r18-fix3 (approved 2026-10-25):** since r18 a crossed (observed) exit is recorded on the tile the camera reached
  at the cut, i.e. the warp tile itself. `route` never steps onto an observed exit tile unless it is the goal; starting
  on one and the no-press-in-the-exit-direction rule are unchanged; inferred exits stay enterable.
```

## 3. Work packages
| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| R1 | §2.1 + tests | agent/space.py, tests/agent/test_space.py, tests/agent/test_world.py (only if an assertion needs the pre-approved change) | wave 0 | gpt-6-luna HIGH |
| R2 | gate script T18H (uncommitted), written red first | `.tmp/r18fix3/g/t18h.py` only | wave 0 | gpt-6-luna HIGH |
| D | read-only diagnosis of the missed cut at step 146 | `.tmp/r18fix3/D/` only | wave 0 | gpt-6-luna HIGH |
R1, R2 and D run in parallel. R2 and D never edit agent/ or tests/.

### R1 tests (test_space.py)
1. An observed exit at (0,0) UP, with floor at (0,1), (1,1), (1,0), (1,-1), (0,-1). The route (0,1) → (0,-1) detours via (1,·) and never visits (0,0).
2. A route whose goal is that exit tile still reaches it: (0,1) → (0,0) = `["UP"]`.
3. An inferred exit tile (the arrival side of `mark_exit`) can still be entered on the way to another goal.
4. The step-146 shape: exit (7,5) UP observed; start (7,6); an explore target north of the door with floor around it. `explore_dirs` gives UP a route whose first key is not UP onto (7,5), or no UP route if none exists.
- **Pre-approved assertion changes:** none expected. `test_route_and_frontier_never_cross_known_exit` starts *on* the exit and must stay green unchanged. If a test_world/test_options test routes *through* an observed exit tile to a different goal, stop and report it with the test name and why. Do not edit it.

### R2 gate T18H (zero model requests; pattern `.tmp/r18fix2/g/t18g.py`)
- **S1 (live state):** load `runs/m1_r18d_odo/checkpoint/000145/memory` read-only (copy it to `.tmp/r18fix3/R2/mem` first; never write in runs/) and restore World pos (7,6) in p_place_4. Assert no `explore_dirs(p_place_4)` route and no go-to route to a non-exit goal enters (7,5). Assert a route to (7,5) itself exists.
- **S2:** T18G and T18F still pass (`.tmp/r18fix2/g/t18g.py`, `.tmp/r18fix/g/t18f.py`).
- Print PASS/FAIL per assertion and exit non-zero on any FAIL. Expected on base: S1 FAIL, S2 PASS.

### D (diagnosis only; no fix)
- Offline and read-only: load `runs/m1_r18d_odo/checkpoint/000145/pyboy.state` headless (`window="null"`), from a copy under `.tmp/r18fix3/D/`.
- Replay step 146's keys through the same press and settle path the skill uses (`skills._step`, `_settled`, `wait_settled`), recording per press: `cut(before, end.frame, saw_blank)`, `end.blank_seen`, `_blank` on the frames, `no_response`, the shift, and the odometer segment cut flags.
- Find the press where the warp happened, and explain why the skill's cut test was false while the odometer saw a cut (e.g. the settle ended mid-fade, a non-blank palette fade, or a threshold).
- Compare with step 145's exit press, whose cut was detected: replay from checkpoint 140 only if 145's keys are reachable deterministically, else say so.
- Deliver `.tmp/r18fix3/D/diagnosis.md` (under 40 lines: evidence per press, root cause or the best-supported hypothesis marked as such, and a proposed fix with its one test). No code edits.

## 4. Gates
| Gate | Pass |
|---|---|
| T-R1 | suite green (baseline 898 passed, 1 skipped + new tests); T18H all PASS with PYTHONPATH=R1 worktree |
| G18H | merged HEAD: full suite green; T18H, T18G, T18F all PASS; T18-O2 and T18-O3 output unchanged (diff 0 lines) |

## 5. LV18e (one live run, orchestrator only, after G18H)
- **Ledger:** 10292 / 17854 at plan time. Read `runs/e3_budget.json` first. Cap 80 calls; **stop if the ledger would pass 10372**. Viewer up (`curl -s http://127.0.0.1:8765/state`), else `python scripts/watch_run.py --no-browser` in the background.
- **Run:** copy `runs/m1_r18d_odo` to `runs/m1_r18e_odo` (never resume an original), then `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18e_odo --resume --from-step 145 --steps 20 --max-calls 80`. The agent starts in Pallet at (7,6), just out of the lab.
- **Metrics:**
  1. Number of re-entries into the lab (truth map 0 → 40) that were not the chosen option's target. Target 0.
  2. Explore/go-to routes whose planned keys pass over a known observed exit tile (trace option + `presses.jsonl`). Target 0.
  3. Where the agent goes in Pallet within 20 steps (places reached, crossings).
  4. One RAM−tile offset per (place, map) visit; odometer gap 0, dropped 0.
- LV18e informs; it does not gate the commits.

## 6. Standing rules
Use `docs/plans/execute-r18-fix.md` §6 rules 1–9 verbatim, with scratch at `.tmp\r18fix3\<WP>\` and gate scripts at `.tmp\r18fix3\g\`.

## 7. Orchestrator protocol
You are the orchestrator. Every subagent is **gpt-6-luna at HIGH effort**, set explicitly on every spawn, fresh context, one WP each, given its §8 block with `<WORKTREE>`, `<BASE>` and the §6 rules pasted. You alone review diffs, run gates, merge and run LV18e.
1. **Wave 0:**
   - `git status` must show only the user's files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`.
   - Apply §2.2 and commit it with this brief as `docs: r18-fix3 plan and approved contract amendment`.
   - Run the suite (expect 898 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - R1, R2 and D in worktrees `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r18hR1` (`-b r18h/R1`), `...\r18hR2` and `...\r18hD` (no commits for R2 and D).
   - Copy `t18h.py` to the main `.tmp/r18fix3/g/` and run it on base: it must give S1 FAIL, S2 PASS. Otherwise review the script against §3 before going on.
   - Review R1, run T-R1. Allow one repair round with the exact failing output; stop if it fails twice. Merge `--no-ff`.
3. Run **G18H**, then **LV18e** once.
4. **D's result:** copy `diagnosis.md` into the report. Do **not** implement its proposed fix: it needs the user's approval.
5. **Report:** save `.tmp/r18fix3/report.md` with commits, tests, assertion changes, the gate table, LV18e metrics, D's diagnosis, deviations and open issues (the fake top-wall exit and walls into NPC tiles stay open). Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r18-fix3 result`. Never push.
- **Stop and ask the user when:** a §2 point cannot work as written; a non-pre-approved assertion fails after one repair; T18F, T18G, T18-O2 or T18-O3 change; the ledger would pass 10372; `git status` differs from wave 0's expectation; a worktree refuses removal without `--force`.

## 8. Lane paste blocks
### R1 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix3, WP R1 (routes never step onto a crossed exit). Worktree: <WORKTREE> (branch r18h/R1 from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix3.md §1, §2, §3 R1; agent/space.py route, frontier, mark_exit; agent/world.py explore_dirs, plan; tests/agent/test_space.py exit tests (around lines 150-175).
Build exactly §2.1: one extra skip condition in route's neighbour loop, applied in both passes, with the ponytail comment. Add the 4 §3 R1 tests.
Pre-approved assertion changes: none. If an existing test routes through an observed exit tile to another goal, stop and report it without editing it.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix3\R1\]
Done when: suite green; one commit "R1 routes never step onto a crossed exit"; report the diff per file, tests added, and the suite summary line.
```

### R2 block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix3, WP R2 (offline gate T18H). Worktree: <WORKTREE> (from <BASE>; no commit). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix3.md §1, §2, §3 R2; .tmp/r18fix2/g/t18g.py (pattern); agent/world.py explore_dirs, restore; agent/space.py route; agent/memory.py Memory.
Write .tmp/r18fix3/g/t18h.py with S1 and S2. Copy checkpoint memory to .tmp/r18fix3/R2/ before loading; never write in runs/. It must make zero model requests, print PASS/FAIL per assertion and exit non-zero on any FAIL, and never edit agent/ or tests/. Expected on <BASE>: S1 FAIL, S2 PASS. Do not run scripts/run_agent.py.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix3\R2\]
Done when: the script gives that result; report its path, its output on <BASE>, and why S1's FAIL is the defect.
```

### D block
```
You are a gpt-6-luna HIGH worker on Avatar round r18-fix3, WP D (read-only diagnosis of the missed scene cut at step 146). Worktree: <WORKTREE> (from <BASE>; no commit, no code edits). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/execute-r18-fix3.md §1, §3 D; agent/skills.py _step, _settled, approach; agent/observer.py cut, _blank, wait_settled; agent/track.py Odometer; runs/m1_r18d_odo trace rows 145-146 and presses.jsonl (read-only).
Replay offline from a copy of runs/m1_r18d_odo/checkpoint/000145/pyboy.state (headless, window="null"), pressing step 146's keys through the same press and settle path, and log the per-press evidence listed in §3 D. Find why the skill's cut test missed the warp while the odometer saw it. Zero model requests; never run scripts/run_agent.py; never write in runs/.
Rules: [paste execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r18fix3\D\]
Done when: .tmp/r18fix3/D/diagnosis.md exists (under 40 lines, evidence per press, root cause or a marked hypothesis, and a proposed fix with one test); report its summary.
```
