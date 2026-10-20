# RCR Phase E: brief for an outside executor

Written 2026-10-19 by the RCR coordinator, and meant to be read without any other doc. The planning sessions have stopped. The rest of the work (one small fix, a gate, one live run, closing the program) is done outside Claude, for example in ChatGPT or Codex.

**If you cannot run commands** (plain chat): ask the user to run each command in §6 and paste the output back. Never guess a result. A step is done only when its output has been seen.

## 1. What this project is

This is a game-neutral agent that plays a Game Boy game (Pokémon Red at the moment) from screenshots. Every step makes a few model calls and keeps everything else in local memory.

```
screenshot ─► perceive (VLM: text, screen kind, entities)          agent/perceive.py
          ─► Memory.observe: entities → records with stable ids     agent/memory.py, agent/track.py
          ─► World: places, tiles, exits, routes, the act menu      agent/world.py, agent/space.py
          ─► Notebook: heard game text (t-keys) and quests          agent/notebook.py
             planner (text only) edits quests on events T1-T5       agent/plan.py
          ─► act (text only): picks one numbered menu row {why, i}  agent/act.py
          ─► skill: turns the row into button presses, closed loop  agent/skills.py
          ─► result: done / failed / … ; failures go to Loop.fails  agent/predict.py, agent/loop.py
```

`agent/loop.py` (`Loop.step`) runs that cycle once per step.

**Vocabulary**
- **place:** one map, with an id like `p_place_4`. The label comes from the perceiver.
- **ref:** a stable handle, either `thing:<id>` for a remembered entity or `place:<id>`. Quests target refs, never free-text names.
- **menu row (option):** `{tool, label, action, notes, intent_key, ref, state}`, built by `World.options`. The act model can only pick a row; it never writes its own action.
- **intent key:** `place|family|subject` from `world.fail_key`. Failures (`Loop.fails`), row notes and the trace all share it.
- **quest:** `{key, text, why (t-keys), target (ref or None), status open|done|dropped, opened, closed}`. The focus is the first open quest (`Notebook.focus`, `render_focus`).
- **planner triggers:** T1 new place, T2 new game text, T3 quest event, T4 failure, T5 "no open quest".

**Run folder** (`runs/<name>/`): `trace.jsonl` (one row per step), `calls.jsonl`, `frames/`, `memory/` (`Memory` JSONL tables plus `notebook.json`), `checkpoint/<step>/`, `truth.jsonl` (RAM truth, used only for eval and never shown to the agent).

## 2. Design rules (decided; do not undo them)

Each rule closed a root cause, so breaking one brings that failure back. Before changing code near a rule, run `git log -S '<symbol>'` to see whether an earlier phase already went that way and back.

| # | Rule | Why (root cause it closed) | Where |
|---|---|---|---|
| D1 | No game words in `agent/` (`rg -ni 'oak\|mom\|pallet\|pokemon\|bedroom\|kitchen\|rug' agent/`; ignore `env_pyboy.py`) | earlier fixes were overfit to one scene | everywhere |
| D2 | No new steering gates (refusals, forced choices, prompt sentences that stand in for missing structure). Fixes change **what counts as evidence** or **what exists in memory** | gates hid causes and piled up | everywhere |
| D3 | Fix the shared function every caller goes through, not the caller the bug showed in | sibling callers stayed broken | everywhere |
| D4 | Identity comes from memory, not the VLM's free-text name. A run-start placeholder place is never promoted. One static thing per tile per frame. **Category (exit vs non-exit) is part of identity**, so an exit never merges into a wall | duplicate and mislabelled records; walls offered as exits | memory.py `Memory.observe`, `match_place`, `normalize_kind` |
| D5 | A quest closes on an **event after its target was set**, never on a state: arriving at the target place (the previous place was something else), or hearing text whose speaker is the target thing | quests targeted at the current place closed the moment they were made | notebook.py `observe`, `heard` (`data["here"]` holds the previous place) |
| D6 | The planner may only cite game text it has heard (t-keys). T5 fires **once** and only T1, T2 or T3 re-arm it. A conversation flushes into the notebook whenever the screen kind changes | 17 wasted plan calls in the intro | loop.py `t5_armed`, `_learn` |
| D7 | **Every menu row must be executable.** A picked row that sends 0 inputs counts as `failed` ("did nothing") under its own intent key (`pick_key`), so it shows as failed next step | 7-step dead loop, because the prompt never changed | loop.py `_settle`; world.py `reachable` |
| D8 | The menu hides only off-screen, static, history-less things that are **not the quest target** (user decision). Everything else stays pickable. Grouping by history is presentation, not a gate | 41-row, 3,277-char outdoor prompt | world.py `options(..., targets=())` |
| D9 | One change, one measurement. If the number doesn't move, revert; don't layer more on top | fixes that didn't stick | process |

## 3. Where things stand (verified 10-19)

- **Branch:** `feat/lean-agent`. HEAD `7ec8255`, working tree clean, nothing pushed. Phase E code is in `49d8b82`.
- **Tests:** 630 passed, 1 skipped.
- **Ledger:** `runs/e3_budget.json` stands at **4436 / 4815** (379 calls left). That covers **one** confirmation run.
- **Evidence run:** `runs/m1_rcr_post_r3` (clean boot, 150 steps, on 3db80df, before Phase E). The four root causes in §2 D5-D8 were found in it.
- **Offline rebuild of that run on the current code:**
  - max act prompt **1,541** (S144 1,546; live was 3,277), rows max 18;
  - 0 duplicate rows, 0 walls as exits, 0 dialogue-box rows;
  - `eval_plan --check all` exits 0 with no same-step closures; q5 stays open;
  - intro replay: 2 plan calls instead of 18.

**Superseded:** R3-decide.md §9 "Handover for an outside executor" was written before `49d8b82` ("nothing committed, 3 failed"). Its root-cause notes are still correct, but its state is not; this section is the current state. Item 4's "OUTSTANDING" loop bug in it is done (D7).

## 4. The one open code change: item A

**Problem.** `World.options(..., targets=())` keeps an off-screen quest target in the menu (D8), but `agent/loop.py:159` doesn't pass `targets`. A live run therefore drops a history-less, off-screen static quest target, which is exactly the thing the agent was told to go to.

**Root fix.** The menu and the focus line must agree on the current quest, so both should read it from one place.
1. Add one `Notebook` method that returns the focused quest. It is the first open quest, the same one `focus()` and `render_focus()` use; make `render_focus` call it.
2. At `agent/loop.py:159`, pass `targets=(q["target"],) if (q := self.notebook.<that method>()) and q.get("target") else ()`.

Don't pick "the first open quest that *has* a target": that can be a different quest from the one the act prompt shows.

**Test.** Add one test to `tests/agent/test_loop.py`: the focused quest targets a remembered static thing that is off screen and has no history, and the built menu still contains a row with that thing's ref. Base it on the existing off-screen tests in `tests/agent/test_options.py -k off_screen`.

**Known gap, leave alone:** `reachable` is only checked for off-screen rows. On-screen and exit rows rely on D7's failure recording instead.

## 5. Diagnose offline first (0 calls)

When anything looks wrong, reproduce it from a recorded run before editing code or spending live calls.

| Question | Command | Reads |
|---|---|---|
| What did the agent see, pick and get at step N? | `sed -n 'Np' runs/<run>/trace.jsonl` (fields `choice`, `options`, `result`, `plan`, `act.why`) | trace |
| Scorecard for a run | `<py> scripts/eval_run.py runs/<run> --health` | trace, truth, memory |
| When did each quest open and close, and was any closure empty? | `<py> scripts/eval_plan.py --run runs/<run> --check all` | trace, notebook |
| What menu would the current code build at each step, and how big? | `<py> scripts/check_tracks.py <run> --menu --first 1 [--dump DIR]` | frames, cached perception; needs the ROM in `Rom/` |
| Do the identities match RAM truth? | `<py> scripts/check_tracks.py <run> [--places] [--exits]` | same |
| Is a ref reachable? | Copy `runs/<run>/memory` to `.tmp/x`; `w = World(Memory(".tmp/x"), 16); w.current_place, w.pos = "<pid>", (x, y); w.reachable("thing:<id>", {"screen": "world", "entities": []}, "<pid>")` | memory |

`<py>` = `.venv-agent/Scripts/python.exe -B`. Run from the repo root in Git Bash. For a missing TEMP, use `export TEMP=$PWD/.tmp TMP=$PWD/.tmp PYTHONIOENCODING=utf-8`.

**Method:**
1. Find the first step where the trace goes wrong.
2. Name the stage it went wrong in (perceive, memory, world/menu, notebook/planner, act, skill, result).
3. Find the shared function behind that stage.
4. Write a failing test from the recorded data, fix the function, and re-run the offline command to show the number moved.

## 6. What to run, in order

1. Item A (§4). Full suite: `<py> -m pytest tests/agent -q -p no:cacheprovider -n auto` must be green. Always use `-n auto`.
2. Golden: `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py`. In the `git diff`, only plan, quest, verdict and flag fields may change.
3. Offline gate:
   - `eval_plan --run runs/m1_rcr_post_r3 --check all` exits 0 and q5 does not close;
   - `check_tracks m1_rcr_post_r3 --menu --first 1` reports dup 0, max act prompt ≤ 2,000, and 0 rows with no action.
4. Commit: `RCR Phase E: <one-line gate result>`. Commit at gates and never push.
5. Viewer: `curl -s http://127.0.0.1:8765/state > /dev/null || <py> scripts/watch_run.py --no-browser &`.
6. **One** confirmation run (about 300 calls): `<py> scripts/run_agent.py --run runs/m1_rcr_phase_e --steps 150 --max-calls 300 --decide`.
7. `<py> scripts/eval_run.py runs/m1_rcr_phase_e --health` and `eval_plan --run runs/m1_rcr_phase_e --check all`. Fill in §7.
8. Close RCR (§8).

## 7. Pass/fail for the confirmation run

The baseline is `m1_rcr_post_r3`. Codes S1-S14 are eval_run metrics. A failure means stop and diagnose offline (§5); it does **not** mean a second live run, because the budget doesn't allow one.

| Root cause / metric | post_r3 | Pass | If it fails, look at |
|---|---|---|---|
| D7 dead loop: longest run of steps with the same pick, 0 inputs and the same prompt | 7 | ≤ 1 (the repeat must show the row as failed) | loop.py `_settle` `idle`; the trace `result` for that step |
| D5 empty closures (closed in the step they opened, or with no arrival or speech event) | 5 of 7 | 0 | `eval_plan --check observe`; notebook.py `observe` |
| D6 intro planner waste: plan calls before the first heard t-key, and "none heard yet" errors | 17 / 17 | ≤ new-text events / 0 | loop.py `t5_armed`, the `_learn` flush |
| S8 act prompt max (chars) | 3,008 user / 3,277 total | ≤ 2,000 | `check_tracks --menu --dump` at the max step; world.py `options` |
| Menu rows that don't execute | ≥ 1 | 0 | world.py `reachable`, `beside`, `plan` |
| S1 ids per NPC (Pallet NPC / Mom) | 4 / 2 | ≤ 4 / 1 (known open, don't chase) | — |
| S2 records in 30 Pallet steps | 58 | ≤ 58 | memory.py join paths |
| S5 false closures | 0 | 0 | notebook.py |
| S6 failures not scored as failed | 0 / 14 | 0 | predict.py `result` |
| S4 re-picks of a failed row while an untried one exists | not measured live | ≤ 2 | menu notes and state for that step |
| Progress: first step on map 0 (Pallet); left Pallet northward? | S98; no | ≤ S98; report only | — |

Pass means every row above the S1 line passes. Report the whole table in MASTER.md §8 as "Phase E (date)".

## 8. Closing RCR

Judge the run against the audit (`docs/plans/audit-2026-10-15.md`): repeated mistakes, pointless actions, not following directives, plans that aren't concise, misrecognition made fatal, and "already talked to / already done" tracking. For each one, write "fixed (evidence)", "improved (number)" or "open". Put the verdict in MASTER.md §8, update the §1 rows, and commit.

**Anything further starts as a new program, not another amendment round.**

## 9. Hard rules

- **Never use git worktrees with symlinks or junctions to `runs/` or `Rom/`.** On 10-16, `git worktree remove --force` followed them and deleted both. They are git-ignored and can't be restored. Use `git stash`, or copy files.
- Never delete or overwrite anything under `runs/` except the new run's own folder.
- Don't change `agent/perceive.py` prompts: that invalidates the perception cache that every offline replay depends on.
- Line counts are report-only.
- Known open items, out of scope:
  - **S1:** outdoor walking NPCs get several ids.
  - **F4:** a re-entry can land 1 tile off at a warp, and corrects itself.
  - **F1:** settling near water costs 2-8 s per press.
  - **2b:** the perceiver calls water an "exit".
