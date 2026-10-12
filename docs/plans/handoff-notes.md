# Handoff notes

Last written by: E3R2 orchestrator, 2026-10-11; R2.3 perception check failed; live work stopped as instructed.

## State of the code
- Branch is `feat/lean-agent`; prior WIP commits are `9b598dd` and `d8b89d6`, and E3R2 changes remain uncommitted because M1 has not passed.
- The single F1–F5 WP ran on explicitly selected gpt-6-luna at high reasoning effort.
- Orchestrator acceptance passed 123 tests in tests/agent, including boundaries, with one existing SDL warning.
- E3R2 net agent growth is +12/15 lines; prior E3R growth was +44 lines.
- GENERAL adds only a generic top-down floor-patch/opening heuristic, with no game, place, character, item or route named.
- Exit refine None fails with zero inputs, non-drag to supplies at, and already-there approaches fail even without tile calibration.
- Zero-input verdicts cannot hit except no_change, and escalation uses typed object conditions plus supported kind(arg) strings.
- Latest matching environment/memory checkpoint is runs/m1/checkpoint/000055/, house 2F at (7,1), party 0.
- runs/m1/summary.json reports M1 false and places_matched false; E4 must not start.

## Decisions in force
- The exact R2.3 first check resumed checkpoint54 with matching memory and ran stage q for one step with max-calls 32.
- Missing the required floor-patch exit failed the first check, despite a successful stair traversal and normal exit 0.
- No 14-step probe, stage c, live retry, sentence rewording, final chooser comparison, ten-surprise review, harness deletion, commit or push occurred.
- Preserve runs/e3_budget.json; abandoned requests count and rewinds never refund them.
- Caps remain phase 1000 and M1 950; runner q is capped at 32 and c at 454.
- Stage p stays closed at 25/30, and stage b retains default 180 with the existing approved 300 override.
- Keep Gemma provisionally; Perceiver/Chooser remain gemma4:31b-cloud, with no live code/decider chooser or scripted target hints.
- Runtime defaults remain Hand/Blue and curiosity; default task budget is 150 and subtasks come only from escalation.
- Frozen contracts are unchanged; old loop/ remains intact because the gate has not passed.
- Commit only after a passed gate, delete old loop/ only after the gate, and never push.

## Measured numbers
- Durable spend is 486 requests: smoke18/50 and M1 468/950 = a142 + b297 + p25 + q4 + c0.
- Remaining shared allowances are phase514, M1 482 and smoke32; q has28 unused requests but cannot continue after this failed check under the current packet.
- The first q step used four uncached requests, four inputs and one refine call, with no retry or new escalation.
- Current retained run has55 steps, party0,66 game_text rows and correct HAND/BLUE names.
- Names were established by uppercase typing for HAND and a matching preset for BLUE.
- Dominant-place purities are1.0 for house2F/1F; Town/lab remain unmeasured, with three confirmed places across two visited maps.
- Only the2F–1F evaluated edge is true; no Town or lab edge exists, and the new upstairs destination p_world remains a candidate.
- Retained execution is46/55 (83.6%); prediction hits48/55 (87.3%), first50 88.0% and last5 80.0%.
- Retained cost is91 asks (1.655/step),84 HTTP requests (1.527/step),7 cache hits,0 retries and4.370s/step.
- Retained perception reuse is90.9%, no-call choices56.4%, historical escalations2 and repeated mistakes6/55.
- Retrieval use is0/1176 and refuted model claims0/54; summary boundaries remain null while the full-suite boundary tests pass.
- Retained costs exclude discarded attempts; the durable ledger is authoritative.
- Earlier three-row smoke chooser comparison was inconclusive; the final comparison remains pending.

## Environment and gotchas
- Use .venv-agent/Scripts/python.exe forward-slash commands; unit tests make no cloud calls.
- Runtime perception version is e3-perceiver-6; old checkpoint perceptions are invalidated and responses use .cache/model/.
- Resume loads environment and memory together, prunes later checkpoints and truncates later logs without refunding the ledger.
- Checkpoint54 remains at runs/m1/checkpoint/000054/ for a newly authorized repair restart.
- Pre-rewind checkpoint69, logs, memory, frames and ledger are preserved under .tmp/e3r2/pre-repair/.
- First perception is .tmp/e3r2/perception55.json; replies, trace, chooser, truth, checkpoint and ledger are in .tmp/e3r2/evidence.json.
- Blocking before frame is runs/m1/frames/000055.png; the read-only final checkpoint screenshot is .tmp/e3r2/final.png.
- The one-off evidence collector is .tmp/e3r2/report.py and makes no cloud calls or gameplay inputs.
- .venv-decider remains available for the pending offline chooser comparison at the existing pinned revision.

## Deviations from plan and contracts
- No contract edits, old-harness changes, phase commit or push occurred; verdict inputs is an optional parameter preserving callers.
- CodeGraph CLI was used; named Headroom tools were unavailable and its local proxy timed out, so reads were bounded.
- The first full unit run found only a stale version5 assertion; after updating it, the final full suite passed123 tests.
- The evidence collector initially shadowed Python inspect during screenshot collection; it was renamed and the screenshot captured read-only, with no live retry.
- A verified pixel edge back upstairs does not establish a Town traversal or an M1 gate pass.

## Do not retry
- Do not spend the unused q or p allowance or enter stage c without a new concrete user instruction.
- Do not reword GENERAL and repeat the perception check under the failed R2.3 packet.
- Do not reset/refund the ledger, raise caps, add game waypoint hints, use RAM-informed choices or alter frozen matching to mask place splits.
- Do not equate unit coverage, a successful stair traversal or house purity1.0 with an autonomy gate pass.

## Open issues for E3
- First fresh perception sees rug at[200,930] as an object but gives it no exit affordance; its only exit is stairs at[660,260].
- Step55 chooses those stairs and reaches house2F 0x26,(7,1), returning done/scene cut and surprise, not Town.
- The post-action perception again invents generic south/west exits upstairs, but no further action was run.
- The first-check nomination failure needs a new concrete generic repair instruction before further cloud allocation.
- Current no_progress counter is0 after the new pixel-verified stair edge; no escalation occurred in this one-step check.
- Final M1 chooser comparison, ten-surprise review, old-harness deletion and gate commits remain pending; do not start E4.
