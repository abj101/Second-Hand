# Handoff notes

Last written by: Codex, 2026-10-13, after PS S1 failed its one repair. Next: user review; do not retry or spend from the failed packet.

## State of the code
- Branch feat/lean-agent; PS Status gate failed; Result in PS.md §9 records checks and proposed S1 contract text.
- S1 partial build: World.fix_player owns player kind/name when locked; World.mark copies a fixed red outline onto model inputs.
- Loop tracks cached_marked and after-frame eligibility; raw images remain the observation/storage/hash inputs; trace gains marked.
- S0, S2, E and L3 unstarted; perception still proposes options, act still picks option indexes.
- Prior TL motion/probe/HUD, recent/here, routing/merge and memory fixes retained by diff review.

## Decisions in force
- One gpt-6-luna HIGH subagent implemented S1 and its one repair; no Opus, no orchestrator implementation.
- S1 failed twice; user kickstart requires stop; a revised packet or explicit new repair authorization is required.
- PS §1 checks report, but a broken suite stops; no live approval or spend occurred.
- No game-specific code in agent/, new decision gates, loop/ or contracts.md edits.

## Measured numbers
- Targeted command: .venv-agent/Scripts/python.exe -m pytest tests/agent/test_world.py tests/agent/test_loop.py -q -p no:cacheprovider.
- First orchestrator: 3 failed,78 passed,1 warning,161.58s; repair orchestrator: 1 failed,81 passed,1 warning,276.95s.
- Closing command: .venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider.
- Closing pytest summary: 2 failed,236 passed,1 warning,394.76s; outputs in .tmp/ps/closing_suite.log.
- Remaining failure: test_loop.py:138 expects marked second step, receives False; test_world_golden trace also differs (5 memory items identical).
- Lines: agent2369→2400(+31),scripts3122 unchanged,tests3775→3872(+97); budgets report only.
- PS requests0/160; ledger before/after total931/smoke48/M1883/a142/b297/p25/q60/c306; M1 remaining167.
- Historical L2 progress: house only,party0; spot99/99,coverage99/100; no new PS gameplay measurement.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe and forward-slash paths; warning only pysdl2-dll2.32.10.
- Failing cut/cache test uses a uniform black frame; blank-frame settling versus eligibility is an unverified cause.
- _see keeps its four-value return; current model image/flag are loop.seen_image and loop.seen_marked.
- Checkpoint state adds perception_marked and frame_mark_eligible; S2 prompt-version bump has not happened.
- Headroom configured proxy unreachable; local compression/store usable.
- Offline eval must exclude m1_long71/72 probe frames and m1 frames above94 (stale leftovers).

## Deviations from plan and contracts
- Stopped at S1 before baseline, as user two-failure rule requires; no second design or further repair.
- Exact proposed S1 world/trace/cache contract text in PS.md §9; no S2 delta implemented.
- contracts.md SHA256 unchanged: cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70.
- No golden regeneration or data changes; S2 must review/regenerate only with W0_REGEN=1 after S1 is resolved.

## Do not retry
- No additional repair, S0/E cloud eval or live continuation under this failed PS packet.
- Never overwrite runs/m1_long_l1,runs/m1_long,runs/m1,runs/m1_act; copy evidence before future continuation.
- Never reset/refund/raise a ledger cap, commit runs/ or .tmp/, use git add -A or push.

## Open issues for PS (stopped)
- Resolve remaining cut/cache test expectation under revised authorization; distinguish fixture blank-frame effects from marker eligibility.
- Golden trace mismatch recorded, not accepted; S0 baseline and S2 split remain undone.
- Existing place fragmentation, stale rival/player goals and misleading memory remain PS §7 exclusions.
