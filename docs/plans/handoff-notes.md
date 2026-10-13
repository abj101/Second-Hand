# Handoff notes

Last written by: Codex, 2026-10-12, after TL and L2. Next: the user's STOP 5 review; no further phase or live spend authorized.

## State of the code
- Branch feat/lean-agent; TL B1–B4 built and L2 completed; see TL.md §7 for checks, data review and exact proposed contract text.
- Tracking: observer.residual, dict moves with self_at, World motion lock/unlock, bounded probe, once-per-streak re-perception, true recent/here feedback.
- W4 classify/_learn/evidence/cam_px and median identity rule removed; still-used HUD/regions and W3 statuses retained.
- Cached perceptions are role-corrected; Memory observes once per perception; successful crossing clears blocked; inferred exits do not block route/frontier.
- Resume state and memory: runs/m1_long/checkpoint/000170; L1 evidence preserved in runs/m1_long_l1 (112 source files SHA256 verified).
- New checker: scripts/check_track.py dupes/self/probe; evaluator --from-step and self_spot/false_walls metrics.

## Decisions in force
- STOP 5: progress first, then health; checks report, not progress gates; no further implementation or spend before the user's next approved packet.
- Every subagent gpt-6-luna HIGH, one slice each; never Opus; orchestrator reviews and runs acceptance.
- No game-specific code in agent/, no new goals/filters/gates, no contracts.md or loop/ edits.
- TL cap consumed: 110/110; M1 191 remaining is not authorization to resume TL.
- Proposed contract delta is in TL.md §7; user applies it.

## Measured numbers
- Full prelive: 233 passed,1 SDL2 warning,249.27s; closing: 233 passed,1 warning,248.98s.
- L2 runner exit0; 100 steps71–170; 110 HTTP requests,0 retries,1 cache hit.
- Truth maps: 1F0x25 at71/73/76/81; 2F0x26 at72/74/80; no Town/lab; party0; final1F(7,1).
- Self spot accuracy99/99=1.0, coverage99/100=.99; lock at72; two probes/three presses.
- Health red:2 live IDs per house map, no-effect max90, same-place/action repeats87; false walls0 is a lower bound.
- Worst window81–170:90 blocked NPC actions,0 inputs,one frame hash; reason target is the player.
- B2 checker: m1 locks firstLEFT5px; m1_long fifthLEFT2px after four compact-heat misses; probe checker stops2presses on real1F→2F cut.
- Lines agent2263→2369(+106),scripts2892→3122(+230),tests3525→3775(+250); report only.
- Ledger runs/e3_budget.json: total877/smoke18/M1859/a142/b297/p25/q60/c306; M1 remaining191.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; only warning is pysdl2-dll2.32.10.
- Use `scripts/eval_run.py runs/m1_long --no-write --health --from-step 71` for TL; the stored summary was not overwritten.
- Truth is sampled before probe, act frame/place after probe: two probed rows excluded from before-place ID counts; spot scored against next truth.
- False-wall reconstruction skips probes/cuts/drift/place changes and diagonal/multi-axis paths; it cannot prove absence of all false walls.
- Goldens changed only entities upserts44→17(same4IDs) and trace tracking/feedback fields; regen only W0_REGEN=1, force-stage data.

## Deviations from plan and contracts
- Packet CLI spacing corrected: `110--stage c` became `--max-calls 110 --stage c`; no cap changed.
- One B3 repair fixed stale probe frame and fixture call queues; one B2 repair fixed a legacy three-return _step mock in test_type_text.py.
- Exact approved TL contract proposals await the user; contracts.md SHA256 unchanged.

## Do not retry
- Never overwrite runs/m1_long_l1, runs/m1, runs/m1_act; copy TL evidence before any future live continuation.
- No more TL requests, ledger reset/refund/cap increase, git add -A, runs/.tmp commits or push.
- Do not tune residual thresholds to hide m1_long's initial misses, filter model choices or add progress gates.

## Open issues for STOP 5 review
- Player location is correct, but role-swapped NPC name/affordance still points to the player (trace78/84/170); model repeats despite explicit feedback.
- Inspect cached entity IDs across place merge: at84 choice id e_npc_2 while perception NPC id e_npc_3.
- Places fragment and act echoes readable labels such as world[p_world_6], leaving merge_pending; live IDs/map2.
- Decide the next correctness packet for target/name consistency and place ownership before any new live spend.
