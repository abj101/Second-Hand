# Handoff notes

Last written by: E3R orchestrator, 2026-10-11; R.5 probe failed; live work stopped as instructed.

## State of the code
- Branch is `feat/lean-agent`; E3 WIP is committed at `9b598dd`, and E3R changes remain uncommitted because M1 has not passed.
- R1-R4 ran in the required order on explicitly selected gpt-6-luna at high effort; each WP passed the orchestrator's targeted acceptance.
- Final `tests/agent` suite passed 115 tests, including boundaries, with one existing SDL warning.
- Net agent growth is +44 lines: R1 +14, R2 +5, R3 +15, R4 +10; all individual budgets pass.
- Exit refinement/crossing, exit predictions, blocked verdicts, passage identity, screen enum, modal continuity, interact cuts and persisted no_progress are implemented.
- Latest matching environment/memory checkpoint is `runs/m1/checkpoint/000069/`, house 1F at (6,7), party 0.
- `runs/m1/summary.json` reports M1 false, places_matched false and no Town/Lab edges; do not start E4.

## Decisions in force
- The exact R.5 probe restored checkpoint 54 with its memory and ran 15 more steps with max-calls 30; it ended normally with exit 0 and failed.
- Failure means stop: no retry, stage c, final chooser comparison, ten-surprise review, harness deletion, commit or push occurred.
- Preserve `runs/e3_budget.json`; abandoned attempts count and rewinds never refund requests.
- Phase 1000 and M1 950 caps remain; runner stage p is capped at 30, stage c at 481, stage-b default 180 with the existing approved 300 override.
- Keep Gemma provisionally; Perceiver/Chooser remain gemma 4:31 b-cloud, with no live code/decider chooser or scripted target hints.
- Runtime defaults remain Hand/Blue and curiosity; default task budget 150; subtasks come only from escalation.
- Frozen contracts are unchanged; the already approved Stuck no_progress reason is now implemented.
- Commit only after a passed gate; deleting old `loop/` is the final gate-passed step; never push.

## Measured numbers
- Durable spend is 482 requests: smoke 18/50; M1 464/950 = a 142 + b 297 + p 25 + c 0.
- Shared remaining allowances are phase 518, M1 486 and smoke 32; p has 5 left but its failed probe must not be retried under the current instruction.
- R.4 used 0 calls: retained steps 45-74 hits 21/30 to 16/30;55/56/58/59 become surprise, while 57 remains hit for a completed MOM approach.
- R.4 replay confirmed places fell 7 to 5 (2F3,1F2), failing the expected maximum 3; legacy labels were translated only for audit and frozen landmark overlap was preserved.
- R.4 is a saved-action audit seeded at 45 with a fresh store; original scroll sums/blank flags were not logged; it does not prove repaired landing.
- Current retained run has 69 steps, party 0,71 game_text rows and correct HAND/BLUE names; HAND was typed with uppercase fallback and BLUE selected by preset.
- Dominant-place purity is 1.0 for house 2F/1F; Town/Lab unmeasured;3 confirmed places remain in this rewound run; only 2F-1F is verified.
- Retained execution is 55/69 (79.7%); prediction hits 56/69 (81.2%); first 50 88.0%, last 19 63.2%.
- Retained cost is 116 asks (1.681/step),105 uncached HTTP requests (1.522/step),11 cache hits,0 retries and 4.369 s/step.
- Retained perception reuse is 92.8%, no-call choices 44.9%, escalations 5, repeated mistakes 13/69, retrieval use 0/2606 and refuted model claims 0/57.
- Retained costs exclude discarded attempts; the durable ledger is authoritative; summary boundaries stay null although boundary tests passed.
- Earlier three-row smoke comparator was inconclusive (code agreement 0.667, decider 0.333, decider p 50 15.655 s); final comparison remains pending.

## Environment and gotchas
- Use `.venv-agent/Scripts/python.exe` forward-slash commands; Python 3.12/PyBoy 2.8.1; ROM is `Rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb`.
- Registration is trusted only on the E2 17-press sample; calibration remains tile_px 16/move_frames 16; unsure scroll stays bounded.
- Cloud runs use `.cache/model/`; unit tests make no cloud calls; perception version is e 3-perceiver-5 and old checkpoint perceptions are invalidated.
- Resume loads environment and memory together, prunes later checkpoints and truncates later logs without refunding the ledger.
- Checkpoint 54 remains at `runs/m1/checkpoint/000054/`; pre-repair checkpoint 74 was preserved at `.tmp/e3r/pre-repair/checkpoint74/` before rewind pruned its original path.
- Original retained logs, memory and all frames are in `.tmp/e3r/pre-repair/`; older attempts remain under `runs/m1/attempt1/` through `runs/m1/attempt7/`.
- Offline audit/script are `.tmp/e3r/offline-result.json` and `.tmp/e3r/offline.py`; checkpoint evaluation is `.tmp/e3r/probe-result.json`.
- Current blocking frames are `runs/m1/frames/000055.png`, `000056.png`, and `.tmp/e3r/probe-final.png`; inspect those before planning.
- `.venv-decider` remains local-only at pinned Mapika revision b 37 f 7 e 1 ba 3 fbc 9238004 cf 531 fabbee 2619973 fd.

## Deviations from plan and contracts
- No contract edits, old-harness changes, phase commit or push occurred; optional exit_target/blocked parameters preserve existing callers.
- CodeGraph CLI was used; named Headroom tools were unavailable and the local proxy timed out, so tool reads were bounded.
- R.4 expected results were not fully met; step 57 is not an exit and landmark name/kind drift still fragments re-entry under the frozen matcher.
- Modal opening/closing preserves a world place while explicit fades/cuts remain transitions; new pixel-verified edges exclude self/repeated edges and count candidate destinations.

## Do not retry
- Do not spend the remaining probe allowance or enter stage c after this failed R.5 probe without a new concrete repair instruction.
- Do not reset/refund the ledger, silently raise caps, add game waypoint hints, use RAM-informed choices or change frozen matching to mask splits.
- Do not equate skill done or verdict hit with exit traversal, and do not treat unit coverage or house purity 1.0 as an autonomy gate pass.

## Open issues for E3
- Step 55 chose exit: path south at[0.5,0.99]; refine returned at:null;30 inputs ended blocked/exit not crossed, with no cut; the visible mat is left of the rough crop.
- Later perceptions repeatedly placed approach at the player ([0.453,0.458]) and put destinations in to, despite the contract limiting to to drag; approaches then used 0 inputs.
- Exit failures now score miss/surprise, but persistent IDs and untried ranking did not prevent later re-selection after menu cycles.
- Same-action escalations at 66/68/69 produced invalid condition strings or malformed replies and no useful subtask; no_progress had only 13 eligible world steps, below 25.
- Grounding/coordinate normalization, refinement fallback coverage and escalation output need a concrete generic repair plan before further cloud allocation.
- The final M1 chooser comparison, ten-surprise review, old-harness deletion and gate commits remain pending; do not start E4.
