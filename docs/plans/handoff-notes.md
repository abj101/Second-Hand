# Handoff notes

Last written: 2026-10-13, after GP §9e and the `m1_gp5` clean-boot run. Branch `feat/lean-agent`, HEAD `43750cc`.
Active packet: [GP.md](GP.md) (goal planner; results §9–9e). [PS.md](PS.md) is superseded: its STOP 6a / L3 never ran.

## 1. Where the agent gets to (clean boot, no hints)
| Run | Steps | Intro done | Left 2F | Out the front door | Party |
|---|---|---|---|---|---|
| `m1_gp` (planner only) | 150 | 45 | never | – | 0 |
| `m1_gp2` (option C; name hints on) | 137 | 46 | 84 | **113** (then bounced at its own door) | 0 |
| `m1_gp4` (hints removed) | 93 | stuck at the name keyboard, steps 21–93 | – | – | 0 |
| `m1_gp5` (name fixes) | 150 | **37** (names RED / BLUE, chosen by itself) | **76** | never: went back upstairs and bounced 1F↔2F | 0 |

Never yet: talking to Oak, a Pokémon, or a battle.

## 2. Still broken (most important first)
1. **After Mom the agent goes back upstairs instead of out the door** (`m1_gp5`, steps 82–150).
   - The planner chose the focus "find Oak's lab", and act took the stairs. In `m1_gp2` the same moment went out the door.
   - **Partly fixed, not yet run live** (`43750cc`): matching on return ignored the bedroom's stored views because the bedroom was first recorded under a dialogue box (screen type "dialogue"). So the bedroom was re-created as `world [p_world_2]` at step 83, and the planner was told "arrived somewhere new". Matching now ignores screen type. Offline, the step-84 frame matches the bedroom.
2. **The cycle detector misses slow cycles.** It needs 4 alternating moves within 15 steps. The 1F↔2F moves in `m1_gp5` came 8–20 steps apart (82, 84, 102, 114, 122), so it never fired. Widen the window, or count A↔B returns without a progress event.
3. **Act ignores "failed here before" when the input changes slightly.** Failures are keyed by the exact `verb target` string, so `buttons UP A`, `buttons UP` and `go_to stairs` count separately. The model gets past each one by rewording.
4. **Act prefers raw buttons.** In `m1_gp5`: 84 `buttons`, 48 `advance`, 7 `thing`, 6 `go_to`, and `go_to "unexplored"` only rarely across runs. Routing and "unexplored" exist but are seldom chosen.
5. **"Next door" is ambiguous.** Outside (`m1_gp2`) the agent re-entered its own house to "find Oak". Descriptive names now work for some places (`bedroom`), but 1F and the town were still named `world`.
6. **Dialogue, menu and title screens become places.** `m1_gp5` has 12 live places, 6 of them `dialogue`, plus `lab` and `battle screen` from the intro.
   - They clutter the known-places list.
   - They fired 8 "arrived somewhere new" replans.
   - They give the starting room a "dialogue" type.
   - Modal screens should probably not create or name places.
7. **The planner still sometimes makes micro-goals**, e.g. `exit_pc_menu`, `interact_with_vent`, `interact_with_desk`. These are outcomes in form, but too low-level. The pre-registered offline P1 case (from scratch, picks stairs) still fails. The P3 eval case is flawed (stale log vs. frame).
8. **Act chooses `type` before the keyboard appears** (on the question box). That fails twice ("keyboard not identified") and then it presses A. It costs about 2 steps; minor.
9. **The log is noisy.** Dialogue is logged once per text fragment ("All boys leave" / "All boys leave home some day."). It is readable, but it fills the 15-entry window shown to act.

## 3. Fixed this session (all on the branch, offline-tested)
- **Place identity:**
  - Pixel view fingerprint on cuts (`1eb6a83`).
  - Views veto name merges, and labels never become aliases (`0e4d005`).
  - The same veto applies to merging a brand-new place (`1fd572d`).
  - Screen type no longer blocks recognition (`43750cc`).
- **Position:** stored views veto false entity-drift fixes (`5b5e1f8`). m1_gp had a (+3, −2) jump with no movement.
- **Goals:**
  - Event-triggered planner with a goal tree, one focus and an append-only log (`aa301bb`).
  - Moves between places are logged (`20f75d3`).
  - Planner goals must be outcomes; stuck replans have an 8-step floor (`5b5e1f8`).
  - Immediate replans on a new place, a cycle, or the same action failing twice. Act sees "failed here before", and a cycle marks the exit taken (`1fd572d`, `7119dda`).
- **Exploration:** `go_to "unexplored"` (`5b5e1f8`); it no longer walks back through inferred exits (`b269d53`).
- **Name entry** (`962661e`):
  - The model's `type` shapes are parsed.
  - `type_text` accepts layouts with cell-list rows or no done key.
  - `type_text` never erases on empty text.
  - Presets are offered in the prompt.
  - Replies with no action are flagged.
  - Verified on the emulator: RAM name = RED.
- **Other:** preferred names removed (`9a5b1bd`); Windows `os.replace` retry in the notebook.

## 4. Repo state and gotchas
- **Uncommitted work from another session, not mine; leave it or ask:**
  - `agent/loop.py`: a `_live()` hook writing `live.json`, and an `"after"` frame in each trace row.
  - `scripts/watch_run.py`, `tests/agent/test_watch_run.py`.
  - `tests/agent/test_world_golden.py`: drops `"after"`.
  - When committing `loop.py` changes, stage only your own hunks. I built the HEAD version plus my hunk and tested it in a clean worktree.
- **Golden snapshot:** `tests/agent/data/` is gitignored but tracked. Regenerate with `W0_REGEN=1`, then `git add -f`.
- **Tests:** `pytest tests/agent`: 252 passed on the working tree. The committed HEAD passed 251 + 1 skipped in a clean worktree before `43750cc`.
- **Live viewer:** `scripts/watch_run.py` on 127.0.0.1:8765 follows the newest run (see CLAUDE.md).

## 5. Budget
- **Ledger** (`runs/e3_budget.json`): total 2104/2175, M1 2056/2125, so **69 M1 left**.
- **Caps** live in `_ledger` in `scripts/run_agent.py`, plus two tests (`tests/agent/test_loop.py`, `tests/agent/test_run_agent_budget.py`). The user has raised them by +200 several times today; raise only on explicit approval.
- **Cost:** about 1.6–1.7 requests per step. A 150-step clean boot costs about 250.

## 6. Suggested next steps
1. Re-run a clean boot (needs caps) to see whether `43750cc` stops the 2F re-mint and the upstairs bounce.
2. Stop modal screens (dialogue/menu/title) from becoming places (§2.6). Root-cause cleanup; it touches `match_place` and the cut/modal logic in `Loop.step`.
3. Detect slow cycles and key failures more loosely (§2.2, §2.3).
4. Then the next milestone: Oak and the starter.

## 7. Working rules in force
- Ponytail, CodeGraph and Headroom on every task.
- Run python via `.venv-agent/Scripts/python.exe`.
- Haiku subagents for mechanical work and research; never Opus.
- Present options before a new approach.
- No new mechanical gates: information and prompts only.
- No game-specific code; no preferred names.
- Commit at gates with explicit paths.
