# Avatar harness round r4: fix plan from run `m1_r3b_live_150_20261021_150630`

**Summary (6 lines)**
1. r3b passes every M1 check except the starter: all four maps have purity 1.0, all three edges are found, the names are RED/BLUE, and the nickname loop is gone. From step 115 to step 150 the agent repeats talk-to-Oak, advance, advance; the Poké Ball table (`check display case`) stays under "Not tried here" every step.
2. **Gap 1:** the planner never saw the line that says where the Pokémon are. t12 ("…They are inside the POKé BALLs… Choose!") is cut to 160 characters in the planner prompt (`BALL` is at character 332). 0 of 26 planner prompts contained `BALL`. So q8 became "Choose a POKéMON from PROF. OAK". Also, the target "OAK" was rejected twice although the planner map lists `Thing B OAK`.
3. **Gap 2:** "done when: OAK speaks to you" cannot fire on a repeated line, because `Notebook.heard` only runs for fresh text. The harness already detects repeated speech (`says the same as before`, 10 rows), but nothing closes a quest from it.
4. **Gap 3:** a stuck replan gets no usable evidence. `_evidence` drops an entry longer than its cap and prints `(N too long)`: 7 of 26 plan prompts in r3b and 7 of 25 in r3 lost their tried/failed lines, including the T4 plan at step 138. The talk loop was never in "failed attempts" anyway (gap 4).
5. **Gap 4:** R2 (same intent and same frame hash 3× in 5 rows) cannot see a three-step talk loop. The interact rows have 9 distinct before-hashes in 12 rows, and the period of 3 puts at most 2 in any 5 rows. The repeated-reply signal is the right one: it fires only on true repeat-talk loops across three runs.
6. **Ledger:** 6208 / 6354, so 146 asks are left. The P1 micro-check (4 asks) plus a resume from checkpoint 100 (about 86 asks) fits with no change. A full 150-step run (about 254–313 asks) needs a raise: **+200, to `TOTAL_CAP, M1_CAP = 6554, 6504`** (user approval).

| id | title | files | risk | depends on | needs user approval |
|---|---|---|---|---|---|
| R0 | Baseline: suite count, eval of r3b, offline rule checks below | none | none | - | no |
| P1a | Planner sees the newest game text in full (budget, not a per-line cut) | `agent/notebook.py`, `tests/agent/test_notebook.py` | low | - | no |
| P1b | A thing's `called` name (OAK) resolves as a target | `agent/world.py`, `tests/agent/test_world.py` | low | - | no |
| P2 | A repeated reply from a quest's target thing closes it ("talked") | `agent/notebook.py`, `agent/loop.py`, tests | low-med | - | no |
| P3 | Stuck evidence: cut long entries instead of dropping them; print the real stall threshold | `agent/loop.py`, `tests/agent/test_loop.py` | low | - | no |
| P4 | A reply that says the same as before counts as "did nothing" for that option | `agent/loop.py`, `tests/agent/test_loop.py` | med | P3 (to be visible to the planner) | no |
| L | Live check: resume from checkpoint 100 (fits now); a full run needs the cap raise | `scripts/run_agent.py` caps (user WIP) | - | P1–P4 | **yes, only for the full run** |

Order: P1a, P1b → micro-check → P2 → P3 → P4 → L. P1 is the fix that can actually get the starter. P2–P4 make the loop visible and short when the goal is wrong anyway.

---

## Context (no repo access assumed)

As in `harness-fixes-r3.md`. Repo `Avatar`, branch `feat/lean-agent`, interpreter `.venv/Scripts/python.exe`, tests `-m pytest -n auto -q`. Landed since r3: R1 / R1b / R1c (act prompt: preset names, answer NO to nickname offers, leave a name keyboard by its end key, grid select), R2 (`7a95daa`, same intent + same frame hash 3× in 5 rows = did nothing), R3 (`7fba2dd`), R5 (`44676de`). Project rules are unchanged: root cause first, reuse, **no new mechanical gates** (options are labelled, never removed), no game names in prompts or code, user WIP files untouched (`scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json`, `docs/plans/*.md`), and ledger caps change only with approval.

Run data: `runs/m1_r3b_live_150_20261021_150630/` (`trace.jsonl`, `calls.jsonl`, `truth.jsonl`, `memory/{notebook.json,relations.jsonl}`, `checkpoint/{000000..000150}`).

## What r3b shows (verified offline, 0 requests)

`eval_run.py --no-write`: `m1_starter` false; purity 1.0 on 0x26 / 0x25 / 0x00 / 0x28; all three edges true; names match; execution 116 done / 34 failed; `repeated_mistakes` 10; 254 uncached asks; 5.94 s per step.

The lab, steps 92–150 (truth map 0x28 throughout):
- 92–97: six blocked approaches (Oak still walking in).
- 98–111: Oak's and BLUE's lines; t15 at 111 is "Now, RED, which POKéMON do you want?".
- 112 (plan + W3 follow-up): `done q7`, then `top q8` twice → "key is not open". No goal until 115.
- 115: `add q8 "Choose a POKéMON from PROF. OAK" target "OAK"`. The target is dropped (R3 behaviour) and the act goal is `Choose a POKéMON from PROF. OAK`.
- 118, 122: `target q8 "OAK"` → "matches no known place or thing; closest: MOM, HOUSE OF THE SAGE, SAGE'S HOUSE".
- 138, 146 (T4): `target q8 "Thing B"` → ok (thing:e_customer = Oak).
- 115–150: `interact e_customer` → advance → advance, 12 times. Act prompt at step 139: `4. talk to OAK … talked step 134` is under **Worked here**, and `2. check display case, 3 right, 2 up` is under **Not tried here**. The perceiver names that object `table with three spheres` in the same frame.

---

## P1: the planner must see the game text that names the goal (gap 1)

### P1a: newest game text in full

**Root cause.** `Notebook.render` (`agent/notebook.py:257-280`) shows the last 8 text rows that no open quest cites as `self._text_line(row, 160)`, i.e. cut at 160 characters. A conversation is stored as one joined row, so a long cutscene (t12, 506 characters) loses everything after its first sentence. The T1 trigger is also cut (`f"new game text: {joined[:200]}"`, `loop.py` `_flush_conversation`).

**Offline evidence.**
- t12 rendered line: 534 characters; `BALL` at 332, `Choose` at 447; the planner saw `…You need your own POKéMON for your protection. I know! Here, come with me! BLUE: Gramps`.
- r3b: 0 of 26 planner prompts contain `BALL`. In r3 (`m1_w8_live_150`), 1 of 25 did, and that run did walk to the display table at step 101.
- No open quest cites t12 (q7 cites t13, q8 cites t15), so the uncut "cited" path never shows it.

**Proposal.** Drop the per-line cut for the recent rows and cut the block instead: newest first, whole lines, about 1500 characters in total; older lines go first. This is the same "whole entries, newest first, within a cap" idea as `fit()` in `loop.py:502`. Cited rows stay as they are. The trigger cut stays: the block now carries the full line.

**Expected effect.** Every plan from step 92 to step 146 shows t12 whole (it stays among the last 8 rows; the block at step 115 is about 900 characters).

**Blast radius.** `render` is used by `plan()` and is part of the plan cache key, so cached plan replies miss once. Tests: `tests/agent/test_notebook.py` render tests and `tests/agent/test_plan.py::test_prompt_sections_are_ordered_and_have_no_chapter_or_log_history`. Check whether any test asserts the 160 cut; that would be an assertion change (report it).

**Test to add.** A 500-character newest text row renders whole; with nine rows over the budget, the oldest is dropped, not cut.

### P1b: the name the map shows is a name the resolver accepts

**Root cause.** The planner's legend prints a thing by its `called` name (`world.py:880`: `called or e['name']`, giving `Thing B OAK`), but `World._named_things` (`world.py:601-605`) matches only `name` and `aliases`. So `ref("OAK")` fails although the map says OAK.

**Offline evidence.** Two rejected targets (steps 118, 122) and one target dropped on add (115), all "OAK", while the same prompt listed `Thing B OAK`. The act options label the same thing "talk to OAK".

**Proposal.** Add `e.get("called")` to the name set in `_named_things` (one token). `ties()` uses the same function, so two things both called the same name still come back as a tie.

**Test to add.** An entity named `scientist` with `called: "OAK"` → `ref("OAK") == "thing:<id>"`, and `ref("oak")` the same.

### P1 micro-check (cloud, 4 asks, JUDGMENT)

P1 assumes that once the planner reads "inside the POKé BALLs… Choose!" it will link that text to `Landmark … display case` (the perceiver never says "ball"). This is the one assumption that cannot be checked offline. Re-ask the recorded planner prompts at steps 112 and 115 with the t12 line restored in full, two tries each (same ledger hook as the r3 micro-check, run folder `runs/m1_r4_micro`).
- **Go:** in at least 3 of 4 replies the quest text or target names the balls, the display case or its legend letter.
- **No-go:** the replies still say "from PROF. OAK". Report them verbatim and stop; the next question is perceiver vocabulary (out of scope here).

---

## P2: a repeated reply from the target closes "speaks to you" (gap 2)

**Root cause.** The act goal promises `(done when: OAK speaks to you)` (`notebook.py:293`). The only code that closes a quest by speech is the loop inside `Notebook.heard` (`notebook.py:159-166`). `heard` runs only from `_flush_conversation` and from world-screen fresh text, and `_learn` (`loop.py:387-388`) admits only text that is not yet in knowledge. A thing that repeats a known line never reaches `heard`, so the quest cannot close. The harness already knows this happened: `_settle` (`loop.py:281-288`) finds the earlier identical `says` relation and writes `says the same as before (step N)`.

**Offline evidence.** `relations.jsonl` in r3b has `says the same as before` on `e_customer` at steps 115, 118, 121, 125, 128, 131, 134, 139, 145 and 148. q8 has target `thing:e_customer` from step 138 (retargeted 146) and stays open to the end.

**Proposal.** Move the close loop of `heard` into one method (`Notebook.talked(step, who)`), and call it from `heard` and from `_settle` where `said_step is not None`. It appends to `_heard_closed`, which `Notebook.observe` already drains (`notebook.py:174`). So the quest shows up in `closed` at `loop.py:317` with verdict `progress` and T3 replan, with no new plumbing.

**Expected effect on r3b.** q8 closes at step 139 (the first repeat after the retarget at 138). On its own this only moves the loop up a level: the T3 replan sees "q8 done: talked". That is why P1 comes first.

**Blast radius.** `heard` callers: `loop.py:397` and `loop.py:423`. Tests: `tests/agent/test_notebook.py` (heard closes a targeted quest) and `tests/agent/test_qs_e2e.py`. No existing assertion should change.

**Test to add.** A quest targeting thing X, opened before an interact whose reply equals X's earlier reply, closes with `reason="talked"` on that step. A quest opened after the earlier reply but retargeted later does not close on a reply older than the retarget (the existing `since` rule).

---

## P3: the stuck planner must see what was tried (gap 3)

**Root cause.** `Loop._evidence.fit()` (`loop.py:502-507`) keeps whole entries only. If the newest entry alone exceeds the cap (60 characters for `failed attempts:`, 100 for `tried by place:`), the line becomes `(N too long)` and says nothing. Typical entries are about 55–70 characters (for example `shop: approach thing:e_character_2 -> route blocked x1`). The line `steps since last progress: N (stall at 20)` hard-codes 20, while the real threshold is `STALL * 2**u` = 8, 16, 32 or 64 (`agent/tasks.py:4,23`). The T4 plan also prints it right after `Stuck.update` reset it to 0.

**Offline evidence.** Plan prompts whose evidence collapsed:
- r3b, 7 of 26: steps 36, 48, 51, 92, 99, 112 and 138. At 138, the T4 plan of the loop, both `tried by place: (1 too long)` and `failed attempts: (2 too long)`.
- r3, 7 of 25: steps 37, 50, 57, 64, 91, 101 and 113.

**Proposal.** In `fit()`, when no whole entry fits, keep the newest one cut to the cap with `…` instead of `(N too long)`. Print `(stall at {STALL * 2**self.stuck.u})`. No new evidence kinds: P4 puts the talk loop into `failed attempts`.

**Blast radius.** Only the planner prompt. Tests: `tests/agent/test_loop.py::test_evidence_counts_all_post_plan_attempts_and_only_new_failures` and `::test_repeated_identical_failure_plans_once_and_new_evidence_plans_once_more`. Check them for the literal `too long` or `stall at 20`; that would be an assertion change (report it).

**Test to add.** A single 90-character failed entry renders as one entry cut to the cap; the stall number follows `u`.

---

## P4: a reply that says the same as before is "did nothing" (gap 4)

**Root cause.** R2 (`loop.py:322`) keys on the same intent and the same before-frame hash, within the last 5 rows. A talk loop interleaves two `advance` dialogue rows, so one intent appears at most twice in 5 rows. NPCs moving on screen also change the hash. The verdict for each talk is `changed` (text appears), so the option sits under **Worked here** with the note `talked step 134`, which pulls the act model back to it.

**Offline evidence.**
- r3b `interact thing:e_customer` rows: 12, with 9 distinct before-hashes. Under R2, 0 firings in steps 115–150.
- The repeated-reply rule (`said_step is not None` on an interact) fires on:
  - r3b: steps 115, 118, 121, 125, 128, 131, 134, 139, 145 and 148, all in the loop.
  - r3 `m1_w8_live_150`: steps 94 and 98 (`e_npc_1`). That was the same talk-to-Oak loop, steps 91–100; at step 101 it moved to the display table.
  - `m1_h1h4_live`: 0.

  There are no firings outside a repeat-talk loop.

**Proposal.** In `_settle`, add `idle |= said_step is not None` (with `said_step` lifted out of the effects loop), unless a quest closed on that row (P2 makes `closed` win: `progress` is checked before `failed`). The fail why becomes `same reply` instead of `did nothing`, so the option note reads `same reply 2× (last step N)`. The option is labelled and moved to **Failed here**, never removed (no gate). The failure feeds `failed attempts` in P3's evidence.

**Risk.** Some games need a second talk with the same line (for example, the NPC waits for a condition). The option stays available with its note, so the model can still choose it. The first repeat already counts, by design: the loop period here is 3 rows.

**Blast radius.** `_settle` verdict and `self.fails`. Tests: `tests/agent/test_loop.py` verdict tests and `tests/agent/test_harness_reliability.py`. Check whether any fixture repeats an interact with an identical reply and expects `changed`.

**Test to add.** Two interacts with the same thing and the same reply text: the second has verdict `failed`, and the fail note says `same reply`.

---

## R0 and verification (mechanical, 0 requests)

```
.venv/Scripts/python.exe -m pytest -n auto -q
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_r3b_live_150_20261021_150630 --no-write > baseline_eval_r4.txt
```
After each item, rerun the suite. Check the planner prompt offline: rebuild the step-115 `render()` from `checkpoint/000100` plus the recorded text rows (or by unit test) and confirm that t12 appears whole.

## L: live check and the ledger ask

| | asks | fits in 146? |
|---|---|---|
| P1 micro-check | 4 | yes |
| Resume from `checkpoint/000100` to step 150 (r3b used 86 uncached asks after step 100) | about 90 | yes, with about 50 to spare |
| Full 150-step run from boot (r3b 254, r3 313) | 260–320 | **no** |

**Recommendation.** Micro-check first, then the resume from 100, with no cap change. **Success:** truth `party` non-empty by step 150, and no talk loop longer than 2 repeats. A full clean-boot confirmation afterwards needs **`TOTAL_CAP, M1_CAP = 6554, 6504` (+200)**. Your call; `scripts/run_agent.py` is your WIP file, so you edit it or approve the edit.

## Out of scope / seen but not planned

- `top q8` on a key that is not open, repeated by the W3 follow-up (step 112): the same class as r3 G2, three goal-less steps. Watch it in L.
- Town movement: 11 of 11 approaches failed (55–67), and the cutscene rescued it. Exit records in `p_place_4` are mostly "unverified (never crossed)". This is worth its own audit if L reaches Route 1.
- Perceiver vocabulary ("display case" vs "table with three spheres" vs the game's "POKé BALL"). This is only needed if the P1 micro-check fails.
- Settle overhead (r3 D1): 5.9 s per step, still deferred.
