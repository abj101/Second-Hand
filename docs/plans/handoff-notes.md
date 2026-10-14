# Handoff notes

Last written: 2026-10-13, after OM (STOP 8 reached). Branch `feat/lean-agent`. Packet: [OM.md](OM.md) (results in §9).

## State of the code
- Memory tells the truth: only `screen == "world"` screens are places (`Memory.match_place`, `agent/memory.py`); labels are the model's name or `place <n>` (`World.place_label`); screen types and `label [id]` are never names/aliases (`_nameable`, `SCREEN_NAMES`).
- Failed actions against a remembered thing write `(thing, does_nothing, "")`; refuted at `con>=2 and con>sup`, shown under `Don't believe:` in `World.view` and as `refuted:` on its option; a later success resets it (`Loop.step` failure block).
- `World.options(per, place, fails, step, inputs)` (`agent/world.py`) builds the numbered menu; `act(..., options=)` shows `Options:` and takes `{"i": n}` (`agent/act.py`). Buttons and the `info` overflow row have `action: None`.
- `fail_key` is a module function in `agent/world.py`; `Loop._fail_key` delegates. Slow-cycle detection (`Loop._moved`) and the 46920f2 fixes are intact.
- Replay eval: `scripts/eval_act.py --om places|cases [--live]`, cases in `scripts/om_cases.json` (8, all `m1_gp5`), tests in `tests/agent/test_om_eval.py`.

## Decisions in force
- No new mechanical gates: information and prompts only; options are never removed or reordered by history.
- Ponytail, CodeGraph, Headroom on every task; python via `.venv-agent/Scripts/python.exe`.
- Subagents: Sonnet orchestrator -> Haiku; Sol -> gpt-6-luna high; never Opus.
- No game-specific code in `agent/`; no preferred names. Present options before a new approach.
- Commit at gates with explicit paths; never `git add -A`; never push; ledger caps raised only on explicit approval.

## Measured numbers
- Unit suite: 285 passed (254 at entry), `pytest tests/agent -q -p no:cacheprovider` (~5 min).
- Places gate: PASS, `--om places`: 3 places, 0 modal places, 0 screen-type names; replay of `m1_gp5` identical to the recording (150/150 presses and frames), 0 perceiver misses, 0 requests.
- Fake exit `exit: doorway#2` refuted at step 105 (2 failed attempts), 20 attempts in total by step 135.
- Options contain a right option: 8/8 (dry). Old arm right: 0/8.
- **Options arm right: 3/8 (gate >= 6): FAIL** (`--om cases --live`, 8 requests). Misses: free `buttons` at 82, 110, 113, 137; the unrefuted fake exit at 103.
- Act prompt: +91 tokens (747 -> 838) on a 9-entity synthetic room.
- Ledger `runs/e3_budget.json`: total 2112/2175, M1 2064/2125, so **61 M1 left**. Cost: about 1.6 requests per live step.

## Environment and gotchas
- Golden data `tests/agent/data/w0_golden` is gitignored but tracked: regenerate with `W0_REGEN=1`, then `git add -f`.
- `.tmp/om/m1_gp5` is the replay copy (about 6 min per 150-step replay); `runs/m1_gp5` and `runs/m1_gp2` are untouched.
- `m1_gp2` cannot be replayed: 4 perceiver cache misses (older brief). Do not raise the miss limit (3) in `scripts/eval_act.py`.
- `.claude/` stays untracked on purpose. Live viewer: `scripts/watch_run.py` on 127.0.0.1:8765 (see CLAUDE.md).
- README, `system.md` and older docs still mention the removed `loop/` stack.

## Deviations from plan and contracts
- Contract text for memory, world, notebook-and-act is in OM.md §9 for the user to merge; `contracts.md` is unedited.
- OM.2's "one success clears it" resets the failure row instead of counting sup (keeps `predict` unchanged).
- Extra `agent/` changes beyond the cards are listed in OM.md §9 (Memory.excursion, `_nameable`, Loop cut guards, thing-only exits in the view table).

## Do not retry
- Replaying `m1_gp2` for cases (cache misses).
- Per-case replays in `--live` (now one replay per run; 8 requests in total).
- A second measure without changing the prompt/labels: the model already ignores listed right options at 4 of 8 steps.

## Open issues for the next phase (user decides at STOP 8)
0. **Decided 2026-10-13: next phase is [OM2.md](OM2.md)** (verified exits, beyond-summaries, less noise, parsing; dry gates then one live measure; paste block inside). Diagnosis in OM2 §0.
1. Model picks free `buttons` over a listed exit (4/8). Options: stronger prompt wording; exit options with their outcome first; dropping the free-input option when an unused exit exists (a gate: needs approval); or move on to the movement packet.
2. The model echoes `place 3` as its place name; `_nameable` should reject `place <n>`.
3. `why`/`expect` are nested inside `action` in 7 of 8 replies; `act()` could read them there.
4. Furniture noise in the menu (4 x "approach stool"); used-N counts duplicated in `World.options` and `World._exit_rows`.
5. Still open from before: modal screens as places is fixed, but Act prefers raw `buttons` (84/150), "next door" is ambiguous, and planner micro-goals remain.
6. Next live run (clean boot) needs a cap raise; about 250 requests per 150 steps; 61 M1 left.
