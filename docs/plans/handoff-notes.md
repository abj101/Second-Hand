# Handoff notes

Last written by: E3 orchestrator, 2026-10-11; significant traversal wall; user approved wrapping up for planning escalation.

## State of the code
- Branch is `feat/lean-agent`; E3 implementation is uncommitted and M1 has not passed; do not start E4.
- E3.1–E3.6 acceptance commands passed; latest full `tests/agent` suite is 95 passed, including boundaries.
- Memory, tasks/Stuck, chooser/escalation, loop/runner, naming, observed prediction, metrics and offline comparison are implemented within packet budgets.
- `contracts.md` and old `loop/` are unchanged; no harness deletion, phase commit or push occurred.
- Detailed evidence and planning questions are in `docs/plans/E3.md` Result; `docs/plan.md` marks E3 blocked for escalation.
- Latest checkpoint is `runs/m1/checkpoint/000074/`: upstairs, game-menu dialogue, HAND/BLUE, party 0; last bounded run ended normally with exit 0.
- Useful earlier restart is `runs/m1/checkpoint/000054/`: house 1F before later room splits; load its memory and env together.

## Decisions in force
- User authorized further stage-b work after the initial 180 cap, then allowed wrapping up on extensive issues for a stronger model to plan a solution.
- Runner has explicit `--stage-b-cap 300` override, default 180; counts and phase 1,000 / M1 950 / smoke 50 limits remain enforced.
- A new plan must account for further stage-b allocation before cloud work; only 3 requests remain under the current 300 guard.
- Preserve `runs/e3_budget.json`; abandoned attempts count and rewinds never refund requests.
- Cloud runs use `.cache/model/`; unit tests make no cloud calls; all WP subagents used gpt-6-luna with high reasoning effort.
- Perceiver/Chooser stay gemma4:31b-cloud; no code/decider chooser was wired live, and no scripted target hints were added.
- Runtime defaults remain Hand/Blue and curiosity; the default task has budget 150 and subtasks come only from escalation.
- Registration is trusted only on E2's 17-press sample; retain bounded inputs and unsure-scroll handling.
- Current calibration remains `tile_px=16`, `move_frames=16`; unknown player position blocks approach and explicitly escalates.
- Places and observed facts derive from pixels; RAM/state access stays evaluator-only; frozen matching rules remain binding.
- Commit only after a passed gate; old harness deletion is the last gate-passed step; never push.

## Measured numbers
- Durable spend is 457 requests: smoke 18/50; M1 439/950 = a 142/180 + b 297/300 + c 0/590.
- Remaining shared allowances are phase 543, M1 511 and smoke 32; stage c's 590 is further limited by remaining M1 capacity.
- The current run retains 74 completed steps, party 0, 72 unique game_text rows and correct HAND/BLUE names.
- HAND was typed with uppercase fallback; BLUE used the matching preset; stage a completed at step 43.
- Confirmed-place purity is 1.0 for house 2F (0x26) and 1F (0x25); town (0x00)/lab (0x28) are unmeasured; only the 2F–1F edge is verified.
- There are 7 confirmed places across the two visited truth maps; reported purity does not establish stable room identity.
- `runs/m1/summary.json` reports M1 gate false and places_matched false; separate boundary tests passed although its boundary field stays null.
- Retained execution is 64/74 (86.5%); prediction accuracy 83.8%, with 88.0% in steps 1–50 and 75.0% in 51–74.
- Retained cost is 127 asks (1.716/step), 117 uncached (1.581/step), 10 cache hits, 0 retries and 4.988 s/step.
- Retained perception reuse is 93.2%, no-call choices 44.6%, escalations 2, repeated mistakes 6/74, retrieval use 0/2561, refuted model claims 0/74.
- Retained costs exclude discarded attempts; durable totals above are authoritative for caps.
- Three-row smoke comparison: code agreement 0.667, decider 0.333, failed-pick share 0 for all, decider p50 15.655 s including first-use compilation; keep Gemma provisionally.

## Environment and gotchas
- Use `.venv-agent/Scripts/python.exe` forward-slash commands; Python 3.12/PyBoy 2.8.1; ROM is `Rom/Pokemon - Red Version (USA, Europe) (SGB Enhanced).gb`.
- `.venv-decider` has CUDA/bf16 and cached Mapika/decider-2b revision b37f7e1ba3fbc9238004cf531fabbee2619973fd; always local_files_only=True.
- Runner steps means more steps; resume/from-step restores env and memory together, prunes later checkpoints and truncates later log rows without refunding calls.
- Initial cap branch printed call_cap/checkpoint 54 but the command tool reported exit 1 instead of contractual 3; it was treated as a hard stop before renewed authorization.
- Tool Ctrl-C may bypass Python cleanup; use saved numbered checkpoints; the latest 74 checkpoint came from a clean normal end.
- Loop prompt version e3-perceiver-4 invalidates old cached perceptions on restore; JSON-repair flags also survive restore.
- Evaluator and chooser comparator take positional run paths: `scripts/eval_run.py runs/m1`, `scripts/compare_chooser.py runs/m1`.
- Older logs exist under `runs/m1/attempt1/` through `attempt7/`; their shared frame references may have been overwritten by rewinds.
- Attempt7 separately retains frames 55/56/74 under `runs/m1/attempt7/frames/`; current frames 55/56/74 show the final retry.
- E2 audit 98460ef fixed string text normalization; naming accepts cached rows/layout aliases; do not reintroduce character-splitting.

## Deviations from plan and contracts
- No frozen contract change; effects gained optional scene_cut override with unchanged default behavior, and runtime line budgets still hold.
- Loop shares one cut decision across memory/effects/edges/trace; registered nonzero pad approach suppresses aggregate-only cuts, while explicit skill cuts/fades/interact/pointer cuts remain.
- Prior attempt6 step 58 falsely split a same-map seven-input walk: endpoint changed 0.633, range-4 scroll None, pixel view_scrolled; audit occurred before frames were overwritten.
- Modal prompt blocks world movement only for visible dialogue or selection menus, preserving whole-field keyboard typing.
- Steering reuses a successful sidestep direction when another axis is blocked, but keeps desired direction if none was recorded; skills is exactly +50 lines.
- A broader steering condition regressed an existing E2 test and was corrected; the prior in-memory function hit 24 inputs on a wider wall, corrected code crossed in 10.
- Earlier fixes cover presets/layout aliases, nonconsecutive action repetition, stale landmark grounding and one bounded compact-JSON repair after eight malformed-empty steps.
- Final M1 chooser comparison, ten-surprise review and old-harness cleanup await a completed M1 run.

## Do not retry
- Do not spend remaining stage-b requests on unchanged room cycles, reset/refund the ledger, silently raise guards, or transfer allowances without recording an authorized plan.
- Do not add scripted target names, per-game waypoint hints, RAM-informed choices or change frozen place matching to mask errors.
- Do not equate skill done/verdict hit with having traversed an exit; they can reflect adjacency or any pixel change.
- Do not treat unit repairs or 1.0 place purities as proof that M1 autonomy and stable place identity are solved.

## Open issues for E3
- Planning wall: perceived exits are often camera-edge continuations or inaccurate points; navigation produces motion/adjacency then returns to furniture and stairs without reaching town.
- Final step 55 chose exit down at [0.35,1.0], used 13 inputs and returned done/adjacent/hit, but truth remained 1F at (4,7); current frame 56 shows adjacency near the lower boundary.
- Earlier same choice exhausted 30 inputs; steering repaired that limit failure but did not establish the actual doorway crossing.
- Steps 56–60 chose right exit, MOM, up exit, shelf and stairs; blocked/both-axes step 59 still received hit because its predicted pixel effect occurred.
- Investigate whether untried enforcement diverts useful partial traversal, how to ground generic passage coordinates/resolve exit identities, and how Stuck can detect lack of exploration despite changing frames.
- Room fragmentation persists through modal transitions, label drift and re-entry; any required contract change must be proposed to the user, never applied silently.
- Resume 54 only with its matching memory after a concrete plan; retain 74 as latest evidence; satisfy all remaining M1 gate checks before committing or deleting anything.
