# Handoff notes

Last written: 2026-10-24. Round r18-fix2 complete; G18G passed; LV18c ran exactly once.

## Code

- Branch feat/lean-agent; base 99c96bc. Production merge c0100c2. Nothing pushed.
- G1 eef05c7, merged --no-ff: hold no-response/shift-None evidence until next World.before().
- World screen folds held presses with original step/place/tile/actor snapshot; other screens drop them.
- JSON checkpoint state carries silent entries; old states default to []; unplaced entries are dropped.
- Production scope: agent/world.py +10/-2. Six new tests, tests/agent/test_world.py +78.
- Ring rule, skill results, travel stopping, no-response counters/notes, space.fold and D2 _held path unchanged.
- Known ceiling: unrelated text discards a real no-response bump; text-only actions never become walls.
- Prior r18-fix post-cut holding/crossing work remains intact; loop.py unchanged.
- Plan/contract 0939eeb; comment indentation correction bf56c55.

## Validation and approved stop

- Wave 0: 892 passed, 1 skipped. G1 focused: 8 passed.
- T-G1: 896 passed, 3 skipped; T18G all PASS.
- G18G merged suite: 898 passed, 1 skipped, 293.06s; T18G all PASS; nested T18F 25/0.
- T18-O2 and T18-O3 output unchanged (0 differing lines); O3 85/85, 77 excluded, 4 map flips.
- Only two pre-approved F1 tests gained next-step world before() calls; assertions unchanged.
- Required stop: original T18F D1 failed because its second press had not reached the next before().
- User approved one world-screen before() after D1's second Loop.step(); assertion unchanged; then 25/0.
- No other existing assertion/golden changes or W0_REGEN. Formal gates and live run were orchestrator-only.
- G1/G2 explicitly gpt-6-luna HIGH, fresh context, parallel, one WP each. Worktrees removed normally.

## LV18c

- runs/m1_r18c_odo copied from m1_r17_boot; one resume from checkpoint 65, 60 steps, max-calls 200.
- Completed steps 66-125 exactly once; truth through 126; boot source hashes unchanged.
- Ledger 10132 -> 10214: 82 requests, 118 below ceiling 10332. Never rerun without authorization.
- One handled MalformedReply at step 111; runner exit 0.
- No-response steps/streak: 5/2 versus LV18b 18/15, LV18 17/6, r17 1/1.
- Blocked-wall rows/tiles: 3/2 versus LV18b 13/11, LV18 0/0, r17 10/7.
- Lab offset (5,11), 8/8 world rows, no changes; return exit (0,0) DOWN maps RAM (5,11).
- Entry crossing p_place_4@7,5>p_place_5 n=1; total 3 crossings, no duplicates.
- Odometer frames/uncertain 24977/2217; cuts/gap/dropped 5/0/0.
- No DOWN wall targeting RAM (5,6). Strict terrain criterion still fails: UP92 and DOWN105 target RAM (5,2).
- Their next trace screens are world93/dialogue106; internal probes mean these are not per-press timestamps.
- Pending no-response at checkpoint105 is a different tile (-1,-5); absent by checkpoint110.
- First party>=1 world step 103; only world steps103,104,105 before dialogue106-109 and battle110-125.
- No lab departure; only two subsequent world steps observed, so 15-world-step deadline not fully tested.
- 103 chose fake northern exit at (0,-10), RAM(5,1), labeled door(top wall); 104 DOWN no response.
- 105 chose genuine outside-house return route, nine inputs, then no response followed by dialogue/battle.

## Open issues and evidence

- Remaining terrain-walkable walls, actor/probe/perception attribution, fake top/northern exit and unproven departure.
- Live findings inform rather than gate commits; no extra filters, game logic or live retries were added.
- Full report: .tmp/r18fix2/report.md; gate logs in .tmp/r18fix2; live analysis in .tmp/r18fix2/lv18c.
- Original five user files remain untouched. No pushes, branch rewrites or forced worktree removals.
