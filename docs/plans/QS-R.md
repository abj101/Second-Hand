# QS-R: Make quest state robust: bounded planner calls, quest closure code can trust, rules that hold, and measurements that mean what they say

Status: not started   ·   Branch: feat/lean-agent   ·   Follows: [QS.md](QS.md) (gate 266681e, contracts e2e66d4)   ·   Design: [quest-state.md](quest-state.md)

## Paste block

```text
You are the orchestrator (GPT-6.1 Sol) for phase QS-R (quest-state repair) of the lean agent,
repo C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent. Shell: PowerShell.
Python: .venv-agent/Scripts/python.exe -B. There is no AGENTS.md; CLAUDE.md holds the repo rules.

TEMP FIRST, in every shell (yours and every worker's), before any pytest or script:
  New-Item -ItemType Directory -Force .tmp/qsr/tmp | Out-Null; $env:TEMP = "$PWD\.tmp\qsr\tmp"; $env:TMP = $env:TEMP
and confirm `.venv-agent/Scripts/python.exe -B -c "import tempfile; print(tempfile.gettempdir())"`
prints that folder. The sandbox TEMP is not writable.

READ, in order: CLAUDE.md, docs/plans/handoff-notes.md, docs/plans/contracts.md ("notebook
and act" and "tasks"), docs/plans/quest-state.md, docs/plans/QS.md section 9, docs/plans/QS-R.md.
Then only the files QS-R.md section 3 lists.

ENTRY: check QS-R.md section 1. If any item fails, stop and report. Do not fix or stash anything.

WORKERS: dispatch every card to a gpt-6-luna worker with reasoning effort high, set
explicitly and verified before dispatch. Never substitute a tier or omit high; if the
host can't, report the exact missing capability and stop. Use the host's subagent tools,
not user-visible chats. Each worker prompt contains: the CLAUDE.md rules (Ponytail Full,
CodeGraph before reading code, Headroom for large output; report a tool as unavailable
rather than skipping it), the TEMP line above, its card verbatim from QS-R.md section 5,
the finding numbers it fixes (section 0), the files it owns, and its acceptance commands.
A worker writes only its owned files. Never two writers on one file, including tests.

ORDER: QR.2 || QR.4 -> QR.1 || QR.3 -> QR.5 -> QR.6. Review QR.4's prompt text yourself
against its card before accepting it.

ACCEPTANCE: run each card's acceptance commands yourself; a card is done only when they
exit 0. Each card gets one precise repair round, then report the blocker; never broaden
scope or ownership. After each batch run
`.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto`.
A red suite between batches is diagnostic only when the failures are in files a later
card owns; record them. Unit tests use StubModel only. Locate code by symbol, not line.

LIMITS: contracts.md stays unchanged (section 4 text goes into the Result). No game names
in agent/: `rg -i "oak|mom|pallet|gary|daisy" agent` must print nothing. Requests: only
QR.6's two live commands, at most 6 total (cap 8), after the ledger check. No other
network calls, no cap changes, no live game run. Protect runs/ and checkpoints; scratch
goes under .tmp/qsr/.

FINISH: run the section 2 gate. Fill section 9 with every number and its command, and say
plainly which rows are strong, partial or unmeasured (never headline PASS over an
unmeasured row). Set Status, update the QS-R row in docs/plan.md, rewrite
docs/plans/handoff-notes.md per section 10. If the gate passed, commit explicit paths
only (never `git add -A`, never push): "QS-R: <one-line gate result>". Then stop.
```

## 0. Why this phase (post-QS audit, 2026-10-15)
Four read-only auditors reviewed QS at e2e66d4. The key claims were verified against the source. Findings, numbered for the cards:

| # | Finding | Where |
|---|---|---|
| F1 | T5 "no open quest" has no floor: an empty quest list makes the planner run every step | `Loop._plan_trigger` |
| F2 | Reach checks have no "entered after opened" filter. A quest opened inside its target closes on the next step, then T3 replans with no floor, and the cycle repeats | `Notebook.run_checks`, `_plan_trigger` |
| F3 | Any fresh text (menus, OCR jitter) calls `stuck.progress()`, so T4 never fires during dialogue or menus | `Loop._learn`, `_flush_conversation` |
| F4 | Menu text enters the conversation buffer and fires T1 | `Loop._learn` |
| F5 | `_moved` clears `arrived` on any revisit, so a T2 waiting out the floor is lost | `Loop._moved` |
| F6 | `_planned_closed` and `_planned_top` aren't checkpointed. Trigger state is about 8 loose flags cleared in different places | `Loop.state/restore` |
| F7 | A chapter call that returns None retries on every outcome while notes > 12 | `Loop._after_outcome`, `Notebook.apply_chapter` |
| F8 | Reach checks see only the current place's single label. The ` [id]` suffix on duplicate names breaks the exact match, and the planner's own "done" is then refused | `loop.py` labels lambda, `World.place_label`, `Notebook._check_passes/run_checks` |
| F9 | Text checks match substrings, so an empty arg or "the" closes a quest | `Notebook._check_passes/run_checks` |
| F10 | Planner-supplied `opened`, `check_history` and a same-call check swap are trusted | `Notebook.apply_plan` |
| F11 | Checks were null on 10 of 12 live quest rows, so code-side closure rarely exists | live logs `.tmp/qs/final-plan-*-live.txt` |
| F12 | The parser drops beliefs without `confidence`, which includes every refutation. A refuted belief can also go back to held, and status isn't validated | `plan.plan`, `Notebook._apply_beliefs` |
| F13 | Heard detection isn't transitive (quest → belief → heard fact) | `Notebook._heard_quest` |
| F14 | A re-keyed quest (same text, new key) stays open twice. Parked quests never return and can vanish from the render | `Notebook.apply_plan`, `render` |
| F15 | Rule-4 regex misses "go north" and "reach floor 2" and rejects "Get Badge #1". Buttons are never passed, so the button rule is dead in production | `_TACTICAL`, `Loop.__init__` |
| F16 | The chapter guard can't reject anything (dead code) | `Notebook.apply_chapter` |
| F17 | Facts are all protected and unbounded (60 facts give 11.8k characters against 4–6k), newest notes are dropped first, and the "ruled out:" heading repeats | `Notebook.render/render_focus/_fit` |
| F18 | Act sees no beliefs and no planner note. The non-decide act prompt still asks for notebook edits that are never applied | `render_focus`, `act.SYSTEM/REPLY` |
| F19 | The evidence packet cuts entries mid-object and keeps the oldest. "heard: none" and "steps since progress: 3" under a 20-step stall trigger read wrong | `Loop._evidence` |
| F20 | The migrated chapter 0 carries old tactical goals ("Try house door#4") | `Notebook._migrate` |
| F21 | The stall fallback re-runs `apply_plan` on the same plan, applying beliefs and facts twice, and can collide with a planner key | `Loop._apply_plan` |
| F22 | Measurement gaps: act/decide prompt size unmeasured (HF3 cache misses); no end-to-end loop test. Lifecycle metrics: no quests-per-door, dropped-heard counted as lost, check writes counted as plan calls, stall metric matches `same_action`. No RAM-truth join; viewer FOCUS header blank | `scripts/eval_run.py`, `scripts/eval_act.py`, `scripts/watch_run.py` |

**HF5 place-merge bug 1 is already fixed.** Commit `9a3b5ab` removed the label-keyed fallback in `Memory.match_place`, and `tests/agent/test_memory.py::test_never_crossed_door_with_a_crossed_doors_label_is_a_new_place_not_its_destination` covers it. One auditor's "still in code" claim was stale, so there's no fix card. Remaining place-identity risk (landmark-overlap matching of similar interiors) is unmeasured. QR.5 adds a RAM-truth place-purity metric so the next live run measures it.

## 1. Entry criteria
- HEAD contains `e2e66d4` (QS contracts merged). `git status --short agent scripts tests` is clean. If not, stop and ask.
- Baseline suite with TEMP set as above: expected **564 passed, 0 failed**. Any failure: stop.
- On disk: `runs/m1_ip` (checkpoints 000100, 000125, `truth.jsonl`), `runs/m1_hf_checkpoint_1`, `runs/m1_decide3`, `runs/m1_gp5`, `.venv-agent`. No ROM or emulator needed.
- Ledger: read `runs/e3_budget.json` and caps from `scripts.run_agent` (`TOTAL_CAP`, `M1_CAP`). Last known: total 3222/3265, M1 3174/3215. If fewer than 6 remain, QR.6's live part waits for the user; everything else proceeds.

## 2. Gate
| Metric | Pass | Measured by |
|---|---|---|
| Unit suite | baseline + new tests, 0 failures | `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` |
| Named fix tests | every test named in sections 5 QR.1–QR.4 exists and passes | `... -m pytest tests/agent/test_loop.py tests/agent/test_notebook.py tests/agent/test_plan.py tests/agent/test_act.py tests/agent/test_tasks.py -q` |
| Planner call bound | 200-step StubModel loop with game text every step and empty planner replies: no two plan calls < 3 steps apart; every queued event consumed (none lost) | `tests/agent/test_qs_e2e.py::test_planner_calls_are_bounded_and_events_never_lost` |
| End-to-end | text → T1 plan opens a heard quest with a speak check → later speaker line closes it by check → T3 → one chapter call → next planner prompt holds the chapter; a checkpoint restore mid-run preserves queued events, quests, notes, chapters | `tests/agent/test_qs_e2e.py::test_heard_quest_closes_by_check_and_compacts_across_restore` |
| Act/decide prompt size, 0 requests | ≤ 1,700 tokens (len/4) at m1_ip 100 and 125 | `scripts/eval_act.py --om act-size --run m1_ip --from-step 100` (and 125) |
| Lifecycle, 0 requests | three runs print all metrics incl. quests-per-door and truth place purity; HF replay ≤ 15 calls | `scripts/eval_run.py --lifecycle runs/<run>` ×3 |
| Live planner (≤ 3 requests) | m1_ip 100: ≥ 2/3 tries keep both heard leads open, each heard quest carries a valid check or is flagged `needs check`, 0 rejected quests | `scripts/eval_act.py --om plan --run m1_ip --from-step 100 --tries 3 --live` |
| Live stall (≤ 3 requests) | m1_ip 125 (second stall): ≥ 2/3 tries change quest order or beliefs with a reason; report separately how many revise/refute vs reorder (no pass/fail on that split) | `... --from-step 125 --tries 3 --live` |
| Requests | ≤ 6, ledger before/after printed | the two live commands |

- **Decision this gate produces:** fund a 25-step clean-boot smoke run, then 150 steps (the user decides the caps; estimate ~250–330 requests per 150 steps).
- **If it fails:** commit nothing. Report per row, and leave the uncommitted work for the user.

## 3. Read list
- Design and contract: `docs/plans/quest-state.md` §2, §4–§8; `docs/plans/contracts.md` "notebook and act" (QS part) and "tasks".
- `agent/notebook.py` (all), `agent/plan.py` (all), `agent/tasks.py` (`Stuck`).
- `agent/act.py`: `SYSTEM`, `REPLY`, `SCHEMA`, `DECIDE_SYSTEM`, `_decide`, `act`.
- `agent/loop.py`:
  - `__init__` (notebook construction, `labels` lambda);
  - `step` plan block;
  - `_settle` (`stuck.update`, progress);
  - `_learn`, `_flush_conversation`, `_moved`, `_goal_text`, `_apply_plan`, `_evidence`, `_after_outcome`, `_plan_trigger`;
  - `state`, `restore`.
- `agent/world.py`: `place_label`, `options` (signature only), `view` (goal_text use). `agent/memory.py`: `place_name`, `place_aliases`.
- `scripts/eval_run.py` `_lifecycle`; `scripts/eval_act.py` `om_plan` and its argparse; `scripts/watch_run.py` goals panel.
- Tests: `tests/agent/test_notebook.py`, `test_plan.py`, `test_act.py`, `test_loop.py`, `test_tasks.py`, `test_eval_run.py`, `test_om_eval.py`; fakes in `tests/agent/` (`StubModel`, `GridEnv`).
- Auditor repro scripts, read-only references: `C:\Users\Abj1\AppData\Local\Temp\claude\C--Users-Abj1-Documents-GitHub-Avatar\62f87151-9bb3-41ac-ad46-7e28947dc966\scratchpad\{audit.py,demo_qs.py,probe.py}`. If a path is unreadable from the sandbox, skip it and say so.
- CodeGraph start: `codegraph_explore "Loop._plan_trigger Loop._learn Loop._flush_conversation Loop._moved Loop._evidence Loop._after_outcome Loop._apply_plan Notebook.apply_plan Notebook.run_checks Notebook._check_passes Notebook._heard_quest Notebook.render Notebook.render_focus Notebook._fit plan chapter Stuck.update"`.

## 4. Contracts
- **Uses:** contracts.md "notebook and act" (QS) and "tasks".
- **Proposed changes**, recorded in the Result for the user to merge:
  - **`Notebook.__init__(root, *, buttons=(), places=None)`.** `places()` returns `[(entered_step, [names...])]` for the current place: `place_name` plus `place_aliases`, with any ` [id]` suffix stripped. It replaces `labels`.
  - **Code-owned quest fields:** `opened`, `closed`, `check_history`. Code-owned belief fields: `history`. The planner can't write them; they're silently dropped from input.
  - **`check`:** may be set or changed only while a quest is open, and not in the same reply that closes it.
  - **New flags:** `needs check <key>` (a heard quest without a valid check) and `merged <new> into <old>` (re-key detected by normalized text).
  - **Parked quests** auto-promote, oldest first, when an open slot frees.
  - **Beliefs:** a belief without `confidence` is kept (`low` for new ones). `refuted` → `held` is refused with a flag. Status ∈ held/revised/refuted.
  - **`Stuck.progress()`** is called only for world progress (first place, first edge, quest closure), never for text.
  - **Loop trigger state** becomes one persisted list `events: [{kind: T1..T5, text, step}]`, consumed whole by one plan call. One 3-step floor applies to every kind; events wait and are never dropped. The plan trace row adds `"events"`.
  - **Act:** `REPLY` and `SCHEMA` no longer ask for or require `notebook`. `Act.ops` stays parsed when present, for old caches.

## 5. Work packages

| WP | Goal | Owns files | Fixes | Depends on | Worker |
|---|---|---|---|---|---|
| QR.2 | Reconcile and checks that hold | `agent/notebook.py` (apply/checks/beliefs/rules only), `tests/agent/test_notebook.py` | F2 (notebook side), F8–F10, F12 (apply side), F13–F16 | — | luna high |
| QR.4 | Planner and act prompts and parse | `agent/plan.py`, `agent/act.py`, `tests/agent/test_plan.py`, `tests/agent/test_act.py` | F11, F12 (parse), F18 (act prompt) | — | luna high |
| QR.1 | One persisted event queue; honest progress | `agent/loop.py`, `agent/tasks.py`, `tests/agent/test_loop.py`, `tests/agent/test_tasks.py` | F1, F3–F7, F19, F21, loop side of F2/F8/F15 | QR.2 | luna high |
| QR.3 | Render budgets and migration | `agent/notebook.py` (render/render_focus/_fit/_migrate only), `tests/agent/test_notebook.py` (new render/migration tests only) | F17, F18 (act view), F20 | QR.2 | luna high |
| QR.5 | Measurement tools | `scripts/eval_run.py`, `scripts/eval_act.py`, `scripts/watch_run.py`, `tests/agent/test_eval_run.py`, `tests/agent/test_om_eval.py` | F22 | QR.1, QR.3 | luna high |
| QR.6 | End-to-end tests and gate | new `tests/agent/test_qs_e2e.py` | gate | QR.5 | luna high |

**Run order:** QR.2 ‖ QR.4 → QR.1 ‖ QR.3 → QR.5 → QR.6. QR.1 and QR.3 share no file: QR.3's edits to `notebook.py` are limited to the render and migration functions, which QR.1 never touches. QR.2 and QR.3 touch `notebook.py` one after the other, never at the same time.

### QR.2: reconcile and checks that hold (`agent/notebook.py`)
- **Writable-field whitelist (F10).** Planner input may set:
  - quests: `key, text, why, done_when, status, evidence, check, approach`;
  - beliefs: `key, text, because, confidence, status, reason`;
  - facts: `key, text, from, source, place`.

  Everything else is dropped before merging. `opened` is set by code on first creation only. A `check` change is refused (flag) in the same reply that sets `done`, or on a quest that isn't open.
- **Checks (F2, F8, F9).**
  - **Matching:** one shared matcher used by both `_check_passes` and `run_checks`.
  - **reach:** passes when the current place was entered at a step > `quest.opened` and `arg` matches any of its names, casefolded, with the ` [id]` suffix stripped, at edit distance ≤ 1 (as `World` matches place names).
  - **speak:** the speaker equals `arg`, casefolded, after `opened`.
  - **text:** `arg` is at least 4 characters with at least one word of 3+ letters, matches on word boundaries, after `opened`. Shorter args are rejected at apply time with a flag.
  - **Inputs:** take the current place from `self.places()` (`[(entered_step, [names])]`); keep `labels` as a deprecated alias for one release.
- **Heard (F13, F11).** `_heard_quest` follows `why` → beliefs → `because` → facts transitively (depth ≤ 3, cycle-safe). A heard open quest without a valid check gets the flag `needs check <key>`; the quest is kept.
- **Re-key (F14).** A new open quest whose normalized text (casefold, punctuation and whitespace collapsed) equals an existing open quest's text is merged into the old key with the flag `merged <new> into <old>`.
- **Un-park (F14).** After reconcile, while fewer than `MAX_OPEN` quests are open, promote the oldest parked quest.
- **Rule 4 (F15).** Replace `_TACTICAL` with:
  - coordinates `\(\s*-?\d+\s*,\s*-?\d+\s*\)`;
  - numbered exits `\b(?:door|exit|stairs|mat|rug|gate)\w*\s*#\d`;
  - movement `\b(?:press|move|walk|go|step|head)\s+(?:up|down|left|right|north|south|east|west)\b` (anywhere in the text);
  - buttons: only names of 2 or more characters, on word boundaries.

  "Get Badge #1" and "Find a rival" pass; "Go north" and "Try door#4" fail.
- **Beliefs (F12, apply side).** Status must be held, revised or refuted (anything else is refused and flagged). `refuted` → `held` is refused with the flag `relapse <key>: refuted beliefs stay ruled out`.
- **Chapter guard (F16).** Delete the dead guard. `apply_chapter` accepts any dict with a string `summary`.
- **Acceptance:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_notebook.py -q`. The existing 23 tests still pass (update only the ones the contract change requires, and name each in the Result). New named tests:
  - `test_planner_cannot_write_code_owned_fields`
  - `test_check_swap_with_done_is_refused`
  - `test_reach_needs_entry_after_opened_and_matches_aliases_without_id_suffix`
  - `test_text_check_rejects_short_args_and_matches_words`
  - `test_speak_check_ignores_lines_before_opened`
  - `test_heard_is_transitive_through_beliefs`
  - `test_heard_quest_without_check_is_flagged`
  - `test_rekeyed_quest_merges_into_existing_key`
  - `test_parked_quest_promotes_when_slot_frees`
  - `test_rule4_compass_and_numbered_exit_but_not_badge_number`
  - `test_refuted_belief_cannot_relapse`
  - `test_chapter_with_summary_is_accepted`

### QR.4: planner and act prompts and parse (`agent/plan.py`, `agent/act.py`)
- **Parse (F12).** Keep beliefs without `confidence` (set `low` only for new keys; never drop a revised or refuted record). Non-list `quests` returns `None`, so reconcile can flag "no quests" instead of treating it as an empty list.
- **Planner SYSTEM (F11).** At most 14 sentences, game-neutral. Add one sentence: "Give every quest that names a place, a person, or words to hear a check: reach <place name>, speak <speaker>, or text <words>." Keep every existing rule. Don't let the reply format appear twice; keep the inline REPLY and drop any duplicate schema text.
- **Act (F18).** In `SYSTEM`, remove "When you are stuck, write down what you learned." In `REPLY`/`SCHEMA`, remove the `notebook` edits and drop `notebook` from `required`; parsing tolerates it when present. `DECIDE_SYSTEM` is unchanged apart from wording that names beliefs and note, if QR.3 adds them to the view.
- **Acceptance:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_plan.py tests/agent/test_act.py -q`. New named tests:
  - `test_refutation_without_confidence_is_kept`
  - `test_non_list_quests_parse_as_none`
  - `test_system_asks_for_checks_and_stays_under_fifteen_sentences`
  - `test_act_reply_no_longer_requests_notebook_edits`

### QR.1: one persisted event queue; honest progress (`agent/loop.py`, `agent/tasks.py`)
- **Event queue (F1, F5, F6, F7).** Replace `text_pending`, `arrived`, `focus_closed`, `stall_pending`, `_planned_closed`, `_planned_top` (and `urgent` if present) with:
  - `self.events: list[{kind, text, step}]`. Producers append: T1 dialogue flushed, T2 first navigable place, T3 quest closed, T4 stall, T5 no open quest. T5 is added at most once while it's pending.
  - `_plan_trigger` returns a trigger only when `events` is non-empty and `step - last_plan >= 3` (or `last_plan` is None). The trigger text joins the pending events in priority T3, T1, T2, T5, T4.
  - The plan call consumes the queue whole. Nothing else clears events.
  - `state()`/`restore()` persist `events`; old checkpoints convert their old flags into events once.
  - Chapter backoff: after a chapter call returns None or is rejected, the next chapter attempt waits until the notes count has grown by 6.
- **Progress (F3, F4).** `stuck.progress()` is called only on a first world place, a first verified edge, or a quest closing; never from `_learn` or `_flush_conversation`. Text on a `menu` screen goes to knowledge only. It never enters the conversation buffer or produces T1. Dialogue and world-screen sign text still do.
- **Checks wiring (F2, F8, F15).** Construct `Notebook(..., buttons=self.env.inputs(), places=...)`. `places()` returns `[(entered_step, names)]` for the current place, where `entered_step` is the step this place was last entered (track it in `_moved`) and `names` is `place_name` plus `place_aliases`, with no id suffix.
- **Evidence (F19).** One 600-character budget filled with whole entries, newest first, per section. Rename the labels: "new text since last plan: none|…" and "steps since last progress: N (stall at 20)".
- **Stall fallback (F21).** Append the fallback quest to a copy and reconcile only that quest. Skip it when its key or normalized text already exists.
- **`tasks.py`:** no threshold change. Keep the `progress()` semantics as documented in section 4.
- **Acceptance:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_loop.py tests/agent/test_tasks.py -q`. New named tests:
  - `test_empty_quest_list_does_not_replan_every_step`
  - `test_events_wait_out_the_floor_and_are_never_lost`
  - `test_revisit_does_not_drop_pending_new_place_event`
  - `test_events_round_trip_through_checkpoint`
  - `test_text_does_not_reset_stall_counter`
  - `test_menu_text_is_not_a_game_text_trigger`
  - `test_reach_check_uses_entry_step_and_all_names`
  - `test_evidence_keeps_whole_newest_entries_within_budget`
  - `test_stall_fallback_is_added_once_without_reapplying_the_plan`
  - `test_failed_chapter_backs_off`

### QR.3: render budgets and migration (`agent/notebook.py` render/migration functions)
- **Planner `render()` (F17).** One character budget with these rules:
  - **Always shown:** open and parked quests (one line each), held beliefs, and the last 5 notes.
  - **Facts:** show those cited, directly or transitively, by open quests or held beliefs, plus the 8 most recent. Other facts collapse to `(+N older facts)`.
  - **Ruled out:** one "ruled out:" heading for all refuted beliefs.
  - **Notes:** keep the newest; drop the oldest first.
  - **Chapters:** drop the oldest first.
  - Cut only on line boundaries.
- **Act `render_focus()` (F18).** Top quest; other open quests, one line each; up to 3 held beliefs cited by the top quest (else the newest 3); the latest planner note (≤ 400 chars); the facts cited by the top quest plus the 4 most recent; and the last 10 log lines. Stay within its 4,000-character limit.
- **Migration (F20).** Chapter 0's summary omits v1 goals that match rule 4 or start with `try_`. It keeps the rest, one line each.
- **Acceptance:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_notebook.py -q`. New named tests:
  - `test_render_bounds_facts_by_relevance_and_recency`
  - `test_render_keeps_newest_notes_and_one_ruled_out_heading`
  - `test_render_focus_shows_beliefs_and_latest_note_within_limit`
  - `test_migrated_chapter_omits_tactical_goals`

### QR.5: measurement tools (`scripts/`)
- **`eval_run.py --lifecycle` (F22):**
  - Add quests-per-door: open or created quests whose text matches rule 4, per run.
  - Count a heard quest dropped with a reference as dropped, not lost.
  - `plan_calls` counts only plan events (trace `plan` rows), not check writes.
  - The stall metric counts only `no_progress`/T4.
  - Truth join from `truth.jsonl`, comparing trace step S with truth step S+1 (HF handoff gotcha):
    - **place purity:** agent places that span more than one RAM map, with the maps listed;
    - **reach-closure truth:** the RAM map at each reach-check closure.
  - Replay uses QR.1's event rules, including the 3-step floor for every kind.
- **`eval_act.py --om act-size --run R --from-step N` (F22):** 0 requests. Restore the checkpoint like `om_plan`, build the decide prompt from the checkpoint's cached perception and options with a capturing stub model, and print characters and len/4 tokens. Exit 2 with a clear message if the checkpoint lacks perception; never call HTTP.
- **`watch_run.py`:** the FOCUS header reads the first open quest from `notebook.json` v2, falling back to the trace `plan.focus`.
- **Acceptance:** `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_eval_run.py tests/agent/test_om_eval.py -q`, with one new test each for the truth join, the quests-per-door metric and `--om act-size`. Also run `scripts/eval_run.py --lifecycle runs/m1_ip`, which must print place purity.

### QR.6: end-to-end tests and gate (`tests/agent/test_qs_e2e.py`)
- **`test_planner_calls_are_bounded_and_events_never_lost`:** 200 StubModel loop steps. Game text arrives every step, the planner returns empty quest lists, and there's a new place every 7 steps. Assert: plan-call gaps ≥ 3; every produced event appears in some plan row's `events`; the plan count is ≤ 67.
- **`test_heard_quest_closes_by_check_and_compacts_across_restore`:** the full chain from the section 2 gate row, using `GridEnv` and StubModel. It includes a `state()`/`restore()` plus a `Notebook` reload in the middle.
- Then run the full section 2 gate, live commands last.

## 6. Test layers
| Layer | Gates? | Command |
|---|---|---|
| Unit + e2e | yes | `pytest tests/agent -q -p no:cacheprovider -n auto` (TEMP set) |
| Replay / offline tools (0 requests) | yes | `eval_run.py --lifecycle` ×3, `eval_act.py --om act-size` ×2, `eval_act.py --om plan` dry ×2 |
| Live planner (checkpoints) | yes, ≤ 6 requests | `eval_act.py --om plan ... --live` |
| Live game run | no (user decides: cap raise, 25-step smoke first) | `scripts/run_agent.py --run runs/m1_qsr_smoke --steps 25 --decide` |

## 7. Budgets
- **Lines:** report only. Expect agent/ about +150 net, scripts/ about +150, tests about +400.
- **Requests:** ≤ 6, cap 8. Don't change caps.

## 8. Out of scope
- Place identity beyond measuring it: landmark-overlap matching, moving-NPC identity, and an option row toward a named but unmapped place. Record anything the truth join shows as open issues.
- Cross-episode memory. Any change to the world, options, perception or skills. Game names in agent/. Live game runs.

## 9. Result (filled in by the orchestrator)
- **Gate:** each row with its number, command and strength (strong/partial/unmeasured).
- **Decision taken:** ...
- **Deviations from the packet:** ...
- **Contract changes needed:** the section 4 text as implemented.
- **Open issues for the next phase:** ...

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` (≤ 80 lines, the template headings). Include:
- the event queue and floor;
- the progress semantics;
- the check matcher;
- the whitelist and new flags;
- the render budgets;
- the act-size tool and truth-join metrics, with their numbers;
- the live tries, with the revise/refute vs reorder split;
- the ledger;
- the remaining place-identity issues;
- the recommended smoke-run size and the cap estimate.
