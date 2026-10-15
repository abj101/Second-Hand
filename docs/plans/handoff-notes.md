# Handoff notes

Last written: 2026-10-15. QS-R is built and gated, uncommitted on `feat/lean-agent`. Evidence: [QS-R Result](QS-R.md#9-result-filled-in-by-the-orchestrator). Earlier QS evidence: [QS Result](QS.md#9-result-filled-in-by-the-orchestrator).

## State of the code
- **Event queue.** `Loop.events` is a persisted list of `{kind T1..T5, text, step}`. Producers: T1 dialogue flushed or world-screen text, T2 first navigable place (`_moved`), T3 quest closed (planner or check), T4 `no_progress` stall, T5 no open quest (once while pending). `_plan_trigger` fires only when an event waits and `step - last_plan >= 3`, for every kind (T1 only on the world screen). One plan call consumes the queue whole; its trace row carries `events`. Old checkpoints convert their loose flags once (`_old_events`).
- **Progress.** `Stuck.progress()` is called only for a first world place, a first verified edge or a quest closing. Text never resets the stall counter. Menu-screen text goes to knowledge only (no conversation buffer, no T1).
- **Checks.** One matcher for `_check_passes` and `run_checks`. reach: current place entered after `quest.opened` and any name (`place_name` + `place_aliases`, ` [id]` stripped) within edit distance 1; speak: exact speaker after opened; text: arg >= 4 chars with a 3+ letter word, word-boundary match. `Loop.entered` (place -> step last entered) feeds `places()`.
- **Whitelist and flags.** Planner can write only the whitelisted quest/belief/fact fields; `opened`, `closed`, `check_history`, belief `history` are code-owned. New flags: `needs check <key>`, `merged <new> into <old>`, `relapse <key>: ...`, refused check swaps. Parked quests auto-promote oldest first.
- **Render budgets.** Planner `render()` keeps open and parked quests, held beliefs, last 5 notes, cited (transitive) plus 8 newest facts (`(+N older facts)`), one "ruled out:" heading; drops older notes, then chapters, closed quests, ruled-out beliefs. `render_focus()` adds up to 3 beliefs, the latest planner note (400 chars) and the facts cited by the top quest plus 4 newest, with the newest log lines that fit 4,000 chars. Migrated chapter 0 omits tactical and `try_` goals.
- **Evidence packet.** 600 chars, whole entries newest first per section; labels `new text since last plan:` and `steps since last progress: N (stall at 20)`. Stall fallback reconciles only the fallback quest, once. A failed chapter call backs off until 6 more notes (`chapter_after`).
- **Act.** `REPLY`/`SCHEMA` no longer ask for `notebook` edits; old replies still parse `Act.ops`. Planner SYSTEM is 14 sentences with the check instruction.
- **Tools.** `eval_act.py --om act-size --run R --from-step N` (0 requests, exit 2 without perception). `eval_run.py --lifecycle` adds `truth_join` (place purity, reach-closure maps; trace S vs truth S+1), `quests_per_door`, `heard_leads.dropped`, plan calls from trace plan rows only, stall = `no_progress`/T4 only, replay under the event rules. Viewer FOCUS header reads the first open quest of notebook v2.

## Decisions in force
- Ponytail Full, CodeGraph first, Headroom for large output. No game names in `agent/`. `docs/plans/contracts.md` is not edited here; the section 4 proposal (all implemented) is in the QS-R Result for the user to merge.
- The golden `tests/agent/data/w0_golden/act/trace.jsonl` was regenerated twice, user-authorized, with only plan/flags/events/trigger fields changed. It is git-ignored but tracked: commit with `git add -f`.
- No cap changes, no live game runs, no push. Workers were not used for the finish (one inline agent).

## Measured numbers
- Suite `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto`: **598 passed, 0 failed** (baseline 564).
- Planner bound (e2e, 200 steps): **67 plan calls**, gaps >= 3, none of 258 events lost. End-to-end heard-quest chain with restore passes.
- Act/decide prompt at m1_ip: step 100 **1,168.8 tokens**, step 125 **1,111.5** (len/4, cap 1,700).
- Lifecycle replay calls: hf_checkpoint_1 10, decide3 13, gp5 12, m1_ip 11. Place purity: hf_checkpoint_1 **0.75** (`p_place_5` spans RAM maps 0x27 and 0x28), others 1.0. Quests-per-door: hf 6/15, decide3 0/11, gp5 0/11, m1_ip 5/16.
- Live at m1_ip 100 (3 tries): **3/3**; each try 2 heard quests, both with valid checks, 0 rejected, no flags.
- Live at m1_ip 125 (3 tries): harness **0/3**. Revise/refute **0**, belief-cited reorder **0**. By hand: 1 reorder with a reason (no belief cited), 2 new held beliefs with reasons. Literal gate wording ("order or beliefs change with a reason") would read 3/3.
- Ledger: total 3222 -> **3228**, M1 3174 -> **3180** (+6). Caps 3265/3215, so 37/35 remain.

## Environment and gotchas
- Every shell first: `New-Item -ItemType Directory -Force .tmp/qsr/tmp | Out-Null; $env:TEMP = "$PWD\.tmp\qsr\tmp"; $env:TMP = $env:TEMP`. Python `.venv-agent/Scripts/python.exe -B`. Scratch `.tmp/qsr/`.
- Windows: Python text-mode writes turn LF into CRLF in files that are LF in git; write with `newline=""`. Heredocs through the shell can mangle `\n` inside Python string literals; use the Write/Edit tools for source edits.
- Truth join: compare trace step S with truth step S+1. The m1_ip checkpoints predate the new progress counter, so `--om plan` evidence at 100/125 reads "steps since last progress" 3 and 12, not a faithful 20-step stall.
- `GridEnv.screenshot` is slow (~0.9 s/step); long loop tests should use a cached-frame subclass (see `StillEnv` in `tests/agent/test_qs_e2e.py`).

## Deviations from plan and contracts
- The first-place-is-not-T2 rule and the `_chapter_due` transient bool are choices of this build (see the Result). Row 8's live criterion was left unchanged; it measures strict revise/refute or belief-cited reorder only.

## Do not retry
- Do not spend more live requests on the 100/125 checkpoints without first deciding what counts as a stall response.
- Do not regenerate the golden again without a field-by-field diff (only plan fields may move).
- Do not change caps, launch live games or push without the user.

## Open issues for the next phase (user decides)
1. Row 8: decide whether a bare reorder or a new held belief with a reason counts; then re-run, or accept and fund the smoke.
2. Place identity: re-measure `p_place_5`-style merges on a fresh run (the label-fallback fix `9a3b5ab` postdates hf_checkpoint_1); landmark-overlap interiors and moving NPCs remain unmeasured; reach-closure truth has no real data yet.
3. Funding: **25-step clean-boot smoke first** (`scripts/run_agent.py --run runs/m1_qsr_smoke --steps 25 --decide`), then 150 steps; estimate ~250-330 requests per 150 steps, so 150 steps needs a cap decision (37/35 remain).
