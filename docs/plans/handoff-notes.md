# Handoff notes

Last written: 2026-10-13, after OM3 (dry gates 8/8, live 1/8, one measure spent). Branch `feat/lean-agent`. Packet: [OM3.md](OM3.md); before it [OM2.md](OM2.md), [OM.md](OM.md).

## State of the code
- "Unexplored" = untried edges into unseen tiles (`unseen_edges` in `agent/world.py`): explore label (`(N spots lead somewhere unseen)` / `(0 spots lead anywhere unseen)`), `World.beyond`, `dead_end`. The dead-end note is on the exit option.
- `World._exits` is the one source for exits: a destination-less unverified exit within Chebyshev 1 of a verified one merges into it; the record's `head` is the only name in options, view lines and legend. Crossings count on both sides (`_cross`, `_partner`, `_lone` + `mark_exit`, `unseen_cross` at loop.py:62).
- `World.view(goal, place, compact=False)`: default for the planner; `compact=True` is the act facts (no legend, letters, `E at`, `Things`, `Edges`, `Don't believe`, `Flag:`).
- Options: exits, characters/things with history, explore, known places, one things row of THIS place (`vent ×3`), free buttons. `act._option_text` prints the tool word once. Reply text shortened (-474 chars).
- Eval: `scripts/eval_act.py --om places|cases [--live]`; dry gates include the 2F dead-end note and 2F explore label. Prompts at case steps dump to `.tmp/om/om2_prompts/`.

## Decisions in force
- No new mechanical gates: information and prompts only; options never removed or reordered by history.
- Ponytail, CodeGraph, Headroom on every task; python via `.venv-agent/Scripts/python.exe`.
- Subagents: Sonnet orchestrator -> Haiku, foreground; never Opus. No game-specific code in `agent/`.
- Commit at gates with explicit paths; never `git add -A`; never push; ledger caps raised only on explicit approval. No re-measure of the same live cases.

## Measured numbers
- Unit suite: 331 passed (`pytest tests/agent -q -p no:cacheprovider`, ~5 min).
- `--om places`: PASS. `--om cases` dry: right 8/8, exit truth 8/8, size 8/8 (1,345-1,583 tokens, limit 1,600).
- **Live decision points: options arm 1/8 (gate >= 6), old arm 0/8.** 5/8 picks were free buttons.
- Ledger `runs/e3_budget.json`: total 2120/2175, M1 2072/2125 (53 M1 left); OM3 spent 8.

## Environment and gotchas
- Golden `tests/agent/data/w0_golden` is gitignored but tracked: `W0_REGEN=1`, then `git add -f` (OM3 added the first-crossing arrival row).
- `.tmp/om/m1_gp5` is the replay copy (dry `--om cases` ~6 min); `runs/m1_gp5`, `runs/m1_gp2` untouched.
- `.claude/` stays untracked. Live viewer: `scripts/watch_run.py` on 127.0.0.1:8765.
- README, `system.md` and older docs still mention the removed `loop/` stack.

## Deviations from plan and contracts
- D2 merges only destination-less records (see OM3 section 5). Contract text is merged into contracts.md.

## Do not retry
- Replaying `m1_gp2` for cases; re-measuring the same 8 live cases.
- Shrinking the options or facts further: sizes are no longer the problem.

## Open issues for the next phase (user decides)
1. Why the model uses free buttons in 5/8 cases despite a correct option list: wording/order of the prompt, or give free input the same map truth. Pick before any new live spend.
2. `unseen_cross` can miscount after an uncut place change (stale `world.pos`); mirror-case backfill missing.
3. A clean-boot live run needs a cap raise (~250 requests per 150 steps).
4. Movement fixes and the perceiver prompt pass (invalidates the replay cache).
