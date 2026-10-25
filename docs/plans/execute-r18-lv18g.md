# Executor brief: Avatar LV18g, continuation run to observe the lab exit and Pallet (ChatGPT orchestrator only)

Repo `C:\Users\Abj1\Documents\GitHub\Avatar`, branch `feat/lean-agent`, base `745f88e` (r18-fix4 result). Measurement only: **no code changes, no subagents, no gates to write**. Scratch: `.tmp/r18g/`.

## 1. Why
LV18f (r18-fix4) was clean on every metric it could score: 0 unchosen presses, lock 12/12, 0 re-entries, 0 dropped, no NPC walls. But it ended at step 150 in post-battle rival dialogue (map 40, RAM (5,6)). The lab exit and Pallet afterwards, which are fix4's main target, were never observed: only 4 world steps came after the party.

Checkpoint 150 of `runs/m1_r18f_odo` is locked (`self_spot [0.45, 0.4513888888888889]`, `self_from_motion True`, `self_contradictions 0`). With fix4, a resume keeps that lock, so this run also tests P2 again, live.

## 2. Run (once)
- **Ledger:** 10430 / 17854 at plan time. Read `runs/e3_budget.json` first; if it differs, stop and ask. Cap **100** calls (LV18f used about 1.4 requests per step). **Stop if the ledger would pass 10530.**
- **Preflight:**
  - `git status` shows only the user's 5 files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief and `.tmp/`. HEAD is `745f88e`.
  - The viewer is up (`curl -s http://127.0.0.1:8765/state`); otherwise start `python scripts/watch_run.py --no-browser` in the background.
  - `runs/m1_r18g_odo` must not exist. Hash every file in `runs/m1_r18f_odo` into `.tmp/r18g/source-hashes.json`.
- **Copy, never resume the original:** copy `runs/m1_r18f_odo` to `runs/m1_r18g_odo`.
- **Command:** `.venv\Scripts\python.exe scripts/run_agent.py --run runs/m1_r18g_odo --resume --from-step 150 --steps 50 --max-calls 100` (steps 151–200).
- No second invocation, no extension and no early stop, whatever happens. If it crashes, report the error and the last completed step.

## 3. Metrics (window 151–200; truth[N+1] vs tile[N], as in fix4)
Reuse `.tmp/r18fix4/metrics.py`. Copy it to `.tmp/r18g/metrics.py` and change only its `ranges` list to `[("m1_r18f_odo", 66, 150), ("m1_r18g_odo", 151, 200)]`. It stays read-only and evaluator-side; no RAM reads in agent/.
1. **Unchosen presses:** presses.jsonl keys minus the chosen skill's inputs. Target 0.
2. **Lock:** `self_locked` on world steps (target at least 90%). Also report any unlock (`self_contradictions` reaching 2) and whether it re-locked, with steps. An unlock tests the documented busy-scene re-acquisition ceiling.
3. **Lab exit:** the step where truth goes map 40 → Pallet. Report the world steps from the first post-dialogue world step to the exit, and from party ≥ 1 (step 113). Target: exit within 15 world steps after the dialogue ends, or why not (the chosen options and `why` per world step).
   - The exit should be the bottom door: RAM (5,11) in map 40, which is inferred tile (0,8) DOWN at offset (5,3). Note any choice of the fake top-wall door (open by scope: report it, don't fix it).
4. **Pallet afterwards:** places reached, all map crossings (from/to RAM, option, choice), new places, and the place names given. Untargeted lab re-entries: target 0. Note whether the agent heads north (toward Route 1) or loops.
5. **Walls:** blocked edges whose RAM target held a sprite at that step. Split them into NPCs and props (Poké Ball/table props block for real, as in LV18f step 102). NPC target 0.
6. **Offsets and odometer:** one RAM−tile offset per (place, map) visit. Odometer gap 0. `dropped` split by segment type: world-type target 0, non-world by design (battle drains). Use segment types, not the trace's before-screen label.
- Also: ledger before/after, call rows vs requests, malformed replies, exit code.

## 4. Stop and ask the user when
- the ledger differs from 10430 at preflight, or would pass 10530;
- `git status` or HEAD differs from §2;
- `runs/m1_r18g_odo` already exists, or any `runs/m1_r18f_odo` hash changes;
- you want to change code, rerun or extend for any reason. Diagnosis comes back to the user; this brief authorizes none.

## 5. Report
- Save `.tmp/r18g/report.md` with the preflight results, the exact command, exit, ledger delta, the §3 metrics with a short LV18f vs LV18g table, and the per-world-step choices from the end of the dialogue to the exit.
- Mark inferred claims as inferred.
- Integrity: user files and `runs/m1_r18f_odo` hashes are unchanged.
- Update `docs/plans/handoff-notes.md` (under 80 lines). Commit this brief and the notes as `docs: LV18g continuation result`; stage those two paths explicitly. Never push.

## 6. Rules
`docs/plans/execute-r18-fix.md` §6 rules 3, 4, 5 and 8 apply to the orchestrator, with scratch at `.tmp\r18g\`. Rule 5's off-limits list includes `runs/e3_budget.json` (only the runner writes it).
