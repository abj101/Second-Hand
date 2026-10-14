# Handoff notes

Last written: 2026-10-13, after OM2 (STOP 9 reached, no live measure). Branch `feat/lean-agent`. Packet: [OM2.md](OM2.md) (results in §9); before it [OM.md](OM.md).

## State of the code
- Exits have a truth status: `World._exits` (`agent/world.py`) is the one source for `options` and `view`: `verified` (crossed either way, counted in the new `crossings` store by `World._cross`, or observed), `unverified` (`possible exit ... (unverified, never crossed)`), `refuted` (`not an exit: ...`). Same-destination records within 1 tile are one exit.
- `World.beyond(place, screen=())` says what lies through each verified exit; labels end `(1 unexplored exit, 11 unexplored spots beyond)`; a dead-end place gets a first note and a `view` line `Everything unexplored from here is through: <exit>`.
- `World.options` (cap 10, free row excluded): exits, characters and things with history, explore (`walk to an unexplored part of this place (N spots)`), known places, one `look at or go to a thing here: <names>` row (action None), `free buttons (not checked against memory): <inputs>`. The `info` row is gone. `act()` prints the tool word once.
- `act()` reads `why`/`expect` nested in `action`; `_nameable` rejects `place <n>`; `plan.SYSTEM` says an unverified exit is a guess.
- Eval: `scripts/eval_act.py --om places|cases [--live]`; `cases` evaluates the dry gates (`dry_gates`) and `--live` refuses unless they pass (it then replays a second time). Act prompts at case steps are dumped to `.tmp/om/om2_prompts/` (scratch).

## Decisions in force
- No new mechanical gates: information and prompts only; options are never removed or reordered by history.
- Ponytail, CodeGraph, Headroom on every task; python via `.venv-agent/Scripts/python.exe`.
- Subagents: Sonnet orchestrator -> Haiku; Sol -> gpt-6-luna high; never Opus.
- No game-specific code in `agent/`; no preferred names. Present options before a new approach.
- Commit at gates with explicit paths; never `git add -A`; never push; ledger caps raised only on explicit approval.
- 2026-10-13 (user, at the size gate): no live measure for OM2; the size question goes to STOP 9.

## Measured numbers
- Unit suite: 310 passed (285 at entry), `pytest tests/agent -q -p no:cacheprovider` (~5 min).
- `--om places`: PASS (3 places, fake exit refuted at step 105, 0 drift, 0 misses, 0 requests).
- `--om cases` (dry, 0 requests): right option present 8/8; exit truth 8/8; options 5-9 per case.
- **Size gate FAIL 0/8**: act prompt 1,861 / 1,966 / 1,982 / 2,045 / 2,065 / 2,012 / 2,140 / 2,052 tokens (limit 1,600). Parts (chars): system 1,159, notebook ~2,000, facts ~2,000, options 570-1,030, reply text 1,638.
- Live decision points: not run. Last measured (OM, old menu): 3/8.
- Ledger `runs/e3_budget.json`: total 2112/2175, M1 2064/2125 (**61 M1 left**), unchanged by OM2.

## Environment and gotchas
- Golden data `tests/agent/data/w0_golden` is gitignored but tracked: regenerate with `W0_REGEN=1`, then `git add -f` (OM2 added `act/memory/crossings.jsonl`).
- `.tmp/om/m1_gp5` is the replay copy (a dry `--om cases` takes ~6 min); `runs/m1_gp5` and `runs/m1_gp2` are untouched. `m1_gp2` cannot be replayed (4 perceiver cache misses); do not raise the limit (3).
- `.claude/` stays untracked on purpose. Live viewer: `scripts/watch_run.py` on 127.0.0.1:8765 (see CLAUDE.md).
- README, `system.md` and older docs still mention the removed `loop/` stack.

## Deviations from plan and contracts
- Contract text for OM2 (`_exits`, `beyond`, option order, `crossings`, `act` parsing) is in OM2.md §9 for the user to merge; `contracts.md` is unedited (OM's text is still unmerged too).
- OM2.5 stopped at the dry size gate; no `--live` call was made.

## Do not retry
- Replaying `m1_gp2` for cases.
- A live measure while the size gate fails (`--live` refuses on purpose).
- Shrinking the options block further: it is no longer the big part.

## Open issues for the next phase (user decides at STOP 9)
1. Size gate: either compact the act prompt (drop the facts' `Thing`/`Landmark`/`E at` lines, which the options repeat; shorten the reply-format text and the notebook log; that alone gives 1,691-1,872) or relax the 1,600 limit (all 8 cases are <= 2,140). Then run the one live measure (<= 10 requests, 61 M1 left).
2. A clean-boot live run needs a cap raise (about 250 requests per 150 steps).
3. The `look at or go to a thing here` row can hold ~50 names on 2F (perceiver noise such as `vent 1`, `vent 2`); shorten duplicates.
4. Crossings seen only by the next observation (no exit tile) are not counted; `Loop._moved` could pass the tile.
5. Still open from before: movement fixes (walking around NPCs, walk-onto-then-push exits), and the perceiver prompt asking to list every passage as `exit:` (changing it invalidates the replay cache).
