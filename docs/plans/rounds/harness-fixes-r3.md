# Avatar harness round r3: fix plan from run `m1_w8_live_150`, with an independent audit

**Summary (6 lines)**
1. The nickname loop (38 identical `cancel` presses on the nickname keyboard, steps 113-150) is real. Its cause is a verdict that cannot see a no-op: a blinking cursor changes pixels, so every row is `changed`/`done`. Stuck did fire (T4 at 119/127/135/143), but the planner can only retarget quests, so nothing changed.
2. `cancel` (B) never leaves that keyboard. Replaying the saved emulator state with no model calls shows `START` then `A`, or the `ED` key then `A`, leaves it with the default name. The fix is generic: the same intent from the same screen a third time within five steps counts as a failure (R2), plus a prompt clause that declines optional naming offers (R1). The rule fires on 0 rows of the old run and only on rows 118-150 of this run.
3. Two goal-less episodes are left: steps 54-63 (planner repeats an invalid target, G2) and 37-39 (a quest added for an exit that was just crossed closes itself at once, G5). Four of the seven W3 follow-ups recovered; three did not.
4. The reported "lab mislabelled" count of 18 is right but the step range is 73-90, not 91-108. The cause is not the run-start place: the cutscene entry has no world frame, and the run-start place `p_place` is not counted in purity at all. Fix the gate (R5, needs user approval): on world rows all four maps are 1.0 pure and all three edges are found.
5. Per-step wall time is still 6.6 s of which only 2.6 s is model time: about 3.9 s per step is non-model, with about 70 settle frames per step. This round only reports it; the shortcut is deferred to a measured round.
6. Three decisions are needed from the user: R3 (changes 4 existing assertions), R5 (changes gate semantics), and the ledger (headroom is only 163 asks; resume-from-100 fits, a full 150-step run does not).

| id | title | files | risk | depends on | needs user approval |
|---|---|---|---|---|---|
| R0 | Baseline: suite count and eval of the recorded run | none | none | - | no |
| R1 | Act prompt declines optional naming offers; open name entry is confirmed empty | `agent/act.py`, `tests/agent/test_act.py` | low | - | no |
| R2 | Same intent from the same screen a third time within five steps counts as "did nothing" (G1) | `agent/loop.py`, `tests/agent/test_loop.py` | low-med | - | no |
| R3 | An `add` whose target names nothing known stays a quest without a target (G2) | `agent/notebook.py`, `tests/agent/test_notebook.py`, `tests/agent/test_loop.py` | med | - | **yes: 4 assertions** |
| R4 | Second follow-up planner call when an all-ok follow-up quest closed at once (G5) | `agent/loop.py`, `tests/agent/test_loop.py` | low | - | no |
| R5 | Eval: place purity on world rows; town->lab edge from consecutive world rows (G3, G4) | `scripts/eval_run.py`, `tests/agent/test_eval_run.py` | low (eval only) | - | **yes: gate semantics** |
| D1 | Deferred: settle overhead (about 3.9 s/step non-model) | none | - | measured data | n/a |

---

## Context (no repo access assumed)

**Repo and layout.** Repo `Avatar`, branch `feat/lean-agent`: a Pokémon Red agent harness on the PyBoy emulator; every model role uses `gemma4:31b-cloud`. `agent/` is the harness, `scripts/` is eval and viewer, `tests/agent/` the tests. Interpreter for everything: `.venv/Scripts/python.exe` (system `python` lacks pyboy). Tests: `.venv/Scripts/python.exe -m pytest -n auto -q` (last result 687 passed, 1 skipped).

**One step** (`agent/loop.py` `Loop.step()`): perceive the frame, maybe call the planner (`agent/plan.py`, edits the quest notebook `agent/notebook.py`), build numbered options (`agent/world.py` `World.options`), have the act model pick one (`agent/act.py`), run the skill, then `_settle` judges the outcome and writes a trace row. The act prompt begins with `Goal: <first open quest>` or `Goal: (none)`.

**Planner triggers:** T1 new game text (held until a world/menu/keyboard screen), T2 arrival somewhere new, T3 quest closed, T4 stuck (`Stuck` in `agent/tasks.py`, STALL = 8), T5 no open quest. Floor: three steps between plans. W3 (landed): one immediate follow-up planner call when a plan with non-empty ops leaves no open quest.

**Landed commits C1-C8 (round r2):** C1 `432a958` viewer, C2 `8f15d83` T1 on menu/keyboard, C3 `6f79945` W3 follow-up, C4 `e3f09c1` W4 variant B (a blocked/failed execution counts as failure only when the same intent's previous row was also blocked/failed), C5 `e188492` metrics, C6 `4d4713d` `settle_frames` in rows, C7 `87ba841` act prompt prefers preset names, C8 `aafd4db` compact perceiver JSON (`e5-perceiver-11`).

**Project rules.** Smallest root-cause change, reuse existing code, no new abstractions, **no new mechanical gates** (the agent infers its goals from game text and acts freely; options are never removed), cheap spatial memory, `pytest -n auto`, never link data directories into worktrees (tests cannot read `runs/` there), changing ledger caps needs user approval. The user's own uncommitted WIP, never edit or stage: `scripts/eval_frames.py`, `scripts/run_agent.py` (ledger caps `TOTAL_CAP, M1_CAP = 6015, 5965`), `scripts/frames_combo.json`, `docs/plans/*.md`.

**Run data layout.** `runs/m1_w8_live_150/`: `trace.jsonl` (one row per step: `choice.intent`, `verdict`, `execution`, `plan`, `frame_hash` = sha256 of the raw before-frame, `settle_frames`), `calls.jsonl` (every model call with `step`, `role`, `user`, `response`, `cached`, `ms`, `output_tokens`), `truth.jsonl` (RAM truth: `map`, `party`, `player_name`, `rival_name`; the record with `step` s is the state when step s's frame was taken), `presses.jsonl`, `checkpoint/<step>/{harness.json,memory/,pyboy.state}` at 0, 25, 50, 75, 100, 125, 150, `summary.json`. Older baseline: `runs/m1_h1h4_live` (144 steps, pre-r2).

---

## Independent audit

### A. Verification of the reported numbers for `runs/m1_w8_live_150`

| Reported | Verdict | What the data shows |
|---|---|---|
| Starter at step 109 (old 139) | **Right for new, wrong for old** | `truth.jsonl` first non-empty party is step 110, so `steps_to_starter` = 109. The old run gives 124 (first party step 125), not 139. |
| Names RED/BLUE preset and distinct (old RED/RED) | **Right** | Truth at step 150: RED / BLUE. Act prompts at steps 18 and 24 listed `NEW NAME, RED, ASH, JACK` and `NEW NAME, BLUE, GARY, JOHN`; the model chose RED and BLUE. |
| 30 goal-less act steps, 13 after step 19 (old 32 after 19) | **Right** | Counted from the `Goal: (none)` act prompts in `calls.jsonl`: steps 1-17 (title and intro, before any quest can exist), 37-39, 54-63. Old run: 50 total, 32 after step 19. |
| repeated_mistakes 11 (old 23) | **Right** | `eval_run.py runs/m1_w8_live_150 --no-write` prints 11; old run 23. |
| Lab: own place `p_place_5`, purity 1.0, 18 lab steps still town | **Right on count, wrong on range** | The 18 mislabelled rows are steps **73-90** (truth map 0x28, label `p_place_4`), not 91-108. Oak's cutscene runs entirely on dialogue screens; step 72 is a pixel scene cut, but `match_place` never names a place on a non-world screen (`agent/memory.py` `match_place`: "dialogue, menu, title, battle: a state of the current place, never a place"). The first world frame is step 91, which mints `p_place_5`. |
| `m1_gate_pass` false; town null because `p_place` and `p_place_4` share town steps | **Wrong cause** | `p_place` is state `candidate`, and purity only counts places with state `place` (`scripts/eval_run.py` ~line 1070), so it is not in the mapping at all. Town is null because `p_place_4` holds 23 town rows plus 18 lab rows (purity 0.56 < 0.9). The edge `0x00->0x28` is missing because the town to lab move is a scripted cutscene on dialogue screens: no exit crossing is recorded (`crossings.jsonl` has only the two house edges) and the cut row has `next_place == place`. |
| `p_place` absorbs 27 steps of house 2F and 4 town steps | **Right** | 31 rows. The 4 "town" rows are the title/boot screens (RAM map is still 0 before NEW GAME). It is harmless for the gate. |
| Perceiver 2 bad replies in 169 uncached calls, median 197 output tokens | **Right** | Steps 111 and 112, both on the keyboard screen. Raw replies are corrupt JSON (`..."name the-")","at"...`), a model glitch while listing 46 keys, not a result of the compact-JSON change. Median 197 output tokens, 1.2 s per uncached call. |
| 6.6 s/step; ledger 5852/6015 | **Right** | `time_per_step_seconds` 6.578; `runs/e3_budget.json` = `{"total": 5852, "smoke": 50, "m1": 5802}`; caps 6015 / 5965 leave **163 asks** of headroom. |

**Gaps G1-G5 checked:**
- **G1 nickname loop: confirmed, with one correction.** Step 108 and step 111 both answered YES (the first prompt was the starter confirmation, the second `give a nickname to SQUIRTLE?`). Step 111's act prompt had `Goal: Choose a POKéMON ... (done when: display table speaks to you)`, options `1. select YES 2. select NO 3. advance 4. cancel`, and the model reasoned "selecting YES will proceed with the selection process". Then from step 113 it chose option 46 `cancel` 38 times. Rows had verdict `changed`, execution `done`. **Stuck did see it**: T4 `stuck: no_progress` fired at 119, 127, 135 and 143, but each time the planner replied `target q9 -> "Thing O"` (the NO button of the earlier YES/NO), result `ok`, which calls `Stuck.replanned(changed=True)` and resets the backoff. The planner cannot press buttons, so the loop never ended.
- **G2 planner target rejection: confirmed.** See section B (a).
- **G3 lab mislabel: partial, see table above.**
- **G4 run-start place: not the cause of the town null.** See table above.
- **G5 steps 37-39: root cause found.** Plan call 1 at step 37 closed q3. The W3 follow-up added q4 with target `exit: stairs`, result `ok`, and `Notebook.apply_ops` immediately ran `_close_crossed`, which closed q4 because the stairs had been crossed at step 36 (`s >= opened - 1`; trace evidence "q4 done at step 37 - went through exit: stairs"). So focus was None after an all-ok plan. The next plan came only at step 40 (T5 after the floor).

### B. Audit of the old gaps against the landed commits

| Gap | Handled? | Evidence in this run |
|---|---|---|
| (a) planner handoff / goal-less steps | **Mostly.** Goal-less after step 19: 13 (old 32). The 25 plan calls are 18 plan rows plus 7 W3 follow-ups (steps 32, 37, 40, 54, 57, 91, 113). 4 of 7 recovered (32, 40, 91, 113). 3 did not: **37** (all-ok add closed itself, G5), **54** and **57** (invalid target repeated, G2). At 54 the model sent op `target` on the unopened key q6 (an add mislabelled), the follow-up repeated the identical op although `render()` showed "target q6: error: key is not open"; at 57 `add q6 target "PROF. OAK"` was rejected ("closest: MOM, place 1, door mat") and the follow-up changed it to target `"1"`, also rejected. So the r2 claim that W3 made relaxing the validator unnecessary is contradicted: gemma does not repair its own target errors. Steps 54-63 had no goal until the T1 plan at step 64. |
| (b) stale goal on the rival-name screen | **Fixed.** Plan at step 24 (trigger "Right! So your name is RED ... what is his name again?", screen `menu`) added q2 "Give the rival a name"; the act prompt at step 24 reads `Goal: Give the rival a name`. Same at step 18 (q1). |
| (c) viewer flicker | **Fixed (code check only).** `scripts/watch_run.py` line 347 is `catch(e){if(!controller.signal.aborted) frameConnected=false}` and line 352 is `visibilitychange ... {visible=!document.hidden; if(visible) pullFrame()}`: neither clears the canvas any more. `resetFrames()` remains only on run/generation change (line 358). |
| (d) place mislabel | **Partly.** The resume-boundary bug is fixed (`8ef7c3d`). Oak's lab got its own place with purity 1.0, but the 18 cutscene rows (73-90) keep the town label by design, and the cutscene entry creates no edge. This is a gate/metric question (R5), not a harness bug. |
| (e) blocked loops, W4 variant B limit | **Mostly.** 26 blocked/failed executions in 150 steps (old: 36 in 144). 12 of them (steps 32, 34, 44, 46, 47, 50, 51, 55, 57, 59, 60, 64) were not counted as failures: first block of an intent in the last-5-row window, as predicted. No long blocked loop appeared; the only loop is the keyboard one, which variant B cannot see because its executions are `done`. |
| (f) metrics | **Fixed.** `repeated_mistakes` 11 (old 23) via intent keys; `edges_found` reads `verified_edge`. |
| (g) cost/latency F | **Compact JSON worked, the settle overhead is still there.** Wall 987 s vs model 396 s: 3.94 s per step is non-model (60%). `settle_frames` per step: before median 33 (mean 32.2; 37% of steps at the 24-frame floor), after median 34 (mean 37.8); no timeouts. By screen: dialogue 68 steps 6.3 s (before 32, after 39 frames), world 35 steps 10.2 s (32/34), keyboard 38 steps 3.4 s with only 0.37 model calls per step, i.e. a no-model step still costs about 2.5 s (the floor). Perceiver 252 s of 396 s model time. |
| (h) default names W10 | **Works for the two naming menus** (see above), but it does not stop the optional nickname offer: the clause says "pick a preset or default name", and the nickname prompt is a YES/NO with no preset. |

**Newly introduced or still open:**
- **New, small:** the keyboard screen listed 46 keys as entities, so the planner prompt carried "Thing A ... Thing O NO ..." (letters as things), and the stale `NO` thing from the YES/NO prompt became the planner's target at 119 ("done when: NO speaks to you"). Noise only; not fixed here.
- **Note leak, watch only:** on the name menu the option `select NEW NAME` carried the note `checked step 4: Hello there!` (history of the earlier `NEW GAME` thing, probably the same entity id by screen position). It did not change the choice (RED was picked), but a "checked" note makes the NEW NAME option look worked. Check with the offline snippet in R1; no code change planned.
- **C2 on the keyboard:** it triggered one useful plan at step 113 (T1 with the nickname text). No harm found. The 4 T4 plans at 119-143 were wasted calls (one planner call each).
- **Ledger:** this run used 313 HTTP asks (386 logged calls, 73 cache hits); 25 were planner calls (7 follow-ups). The follow-ups are not a ledger problem; the remaining headroom is.
- **Default act prompt order:** modal menus keep screen order, so `YES` is listed before `NO` and `NEW NAME` before the presets. The wrong YES at step 111 came from the goal text ("Choose a POKéMON") and from the perceiver dropping "Do you want to" (text was `YES; NO; give a nickname; to SQUIRTLE?`), not from ordering: at the two naming menus the model picked the preset although `NEW NAME` was first.

### C. Zero-model emulator replay of the nickname keyboard (done during this audit)

Loading `runs/m1_w8_live_150/checkpoint/000125/pyboy.state` (the keyboard, empty name, map 40) in headless PyBoy and pressing keys:

| Key sequence | Result |
|---|---|
| `B`, `B` | nothing changes (B deletes a letter; on an empty name it does nothing) |
| `START`, `A` | keyboard closes, back in the lab; party nickname stays `SQUIRTLE` |
| `DOWN`x4, `RIGHT`x8, `A` (cursor onto `ED`) | keyboard closes; nickname bytes decode to `SQUIRTLE` |

So the keyboard is only left by confirming the (empty) entry; `cancel` can never do it. Whether the skill `select ED` reaches `ED` is not verified offline (that skill asks the model to read the menu), so the live check covers it.

---

## Plan

### Directive built into the plan (user)

The agent must never create a nickname or any custom name: use the game's preset or default names for the player and the rival, and decline every optional naming offer (e.g. NO to "give a nickname to X?"). Design: a **prompt-level, generic** change inside the existing act sentence (R1), the harness safety net for the case where the keyboard is entered anyway (R2), and no game names, no gate, no option removal. The planner `SYSTEM` (`agent/plan.py:5-12`) needs no change: it already says quests are outcomes in the world, the nickname quest it wrote ("Decide whether to give SQUIRTLE a nickname") is harmless once act answers NO, and its sentence budget (`SYSTEM.count(".") <= 7`, now 6) is better kept.

### R0: baseline (MECHANICAL)

```
.venv/Scripts/python.exe -m pytest -n auto -q                      # expect 687 passed, 1 skipped
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_w8_live_150 --no-write > baseline_eval.txt
```
Record: pass count; `repeated_mistakes.count` (11), `place_mapping`, `edges_found`, `m1_gate_pass` (false). `--no-write` leaves `summary.json` untouched.

### R1: act prompt declines optional naming offers

**Root cause.** `agent/act.py:9` has the clause "pick a preset or default name or setting when offered instead of typing your own". It covers a naming menu with presets but not a YES/NO offer and not an already-open keyboard.

**Current code (`agent/act.py:6-11`):**
```python
SYSTEM = (
    "You choose the next action in an unfamiliar video game. "
    "Use the goal, current screen, and numbered options to choose what to do. "
    "Each option includes an available action and relevant history; choose one option; pick a preset or default name or setting when offered instead of typing your own. "
    "Return only JSON with a brief why and the option number i."
)
```

**Edit (line 9 only).** Extend the same sentence; add no `". "`:
```python
    "Each option includes an available action and relevant history; choose one option; pick a preset or default name or setting when offered instead of typing your own, and decline any optional naming offer (an open name entry is confirmed empty with its end key). "
```
Constraints kept: still 4 parts under `SYSTEM.split(". ")`, the pinned clause substring `"; pick a preset or default name or setting when offered instead of typing your own"` is untouched, no banned words (`pokemon`, `oak`, `mom`, `rug`), length about 450 chars (limit 2000), no game names.

**Why this wording.** "Decline" is the YES/NO case (answer NO). "End key" is the open keyboard case: the label the harness shows is `ED`, and the verified exits are the end key or START. If the model never opens the keyboard (NO chosen) the second half is not used.

**Blast radius (CodeGraph).** `SYSTEM` is used by `act()` and `_ask_valid` (one module), and appears in the act cache key, so every cached act response misses once (offline act replays of old runs lose their cache; acceptable, listed in R1 verification). Tests touching it: `tests/agent/test_act.py` `test_act_shape_and_system_are_generic` (`len(SYSTEM.split(". ")) == 4`, banned words) and `test_default_choices_clause_is_sent_and_changes_act_cache_key` (pinned substring). **No existing assertion changes.**

**Tests to add** (`tests/agent/test_act.py`, after the existing clause test):
```python
def test_system_declines_optional_naming_offers():
    assert "decline any optional naming offer" in SYSTEM and "end key" in SYSTEM
    assert len(SYSTEM.split(". ")) == 4 and len(SYSTEM) <= 2000
```

**Offline verification (0 requests).**
```
.venv/Scripts/python.exe -c "from agent.act import SYSTEM; print(len(SYSTEM)); print(SYSTEM)"
.venv/Scripts/python.exe - <<'E'
import json
c=[json.loads(l) for l in open('runs/m1_w8_live_150/calls.jsonl')]
for s in (18,24,111):
    x=next(x for x in c if x['step']==s and x['role']=='act'); print(s); print(x['user'][:900].encode('ascii','replace').decode())
E
```
Expected: the three prompts list `NEW NAME` first then the presets, and (111) `1. select YES 2. select NO`. Note for the report: whether the `NEW NAME` option carries the `checked step 4` note.

**Live micro-check (cloud, approved, 10 asks counted in the ledger; JUDGMENT to judge).** Re-ask the recorded act prompts with the new SYSTEM and read the choice; it does not run the game. Every HTTP post goes through the same ledger as `run_agent.py` (imported read-only; the folder name contains `m1`, so the m1 bucket is charged), and there is no response cache, so the two tries are independent samples. The fifth prompt is the step-118 keyboard prompt with the note R2 will add to `cancel`, to check that a failed `cancel` does not push the model into typing a letter:
```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe - <<'E'
import json
from pathlib import Path
from agent.model import Ollama
from agent.act import SYSTEM
from scripts.run_agent import _ledger
m=Ollama("gemma4:31b-cloud"); post=m._post
m._post=lambda p: (_ledger(Path("runs/m1_r3_micro"), None, 10), post(p))[1]
c=[json.loads(l) for l in open('runs/m1_w8_live_150/calls.jsonl', encoding='utf-8')]
act={s: next(x for x in c if x['step']==s and x['role']=='act') for s in (18,24,111,113,118)}
act[118]['user']=act[118]['user'].replace('\n46. cancel\n', '\n46. cancel — did nothing 1× (last step 118)\n')
assert 'did nothing' in act[118]['user']
for s,x in act.items():
    for _ in range(2): print(s, m.ask(SYSTEM, x['user'], [], x['schema'], role='act').data)
E
```
Go: 18/24 pick RED/BLUE (option 2), 111 picks NO (option 2), 113 and 118 pick `select ED` (option 44) or the free-buttons row, never `select A`..`select Z`, never `advance` (it types the letter under the cursor). No-go: a letter key or `advance` at 113/118, or YES at 111 in both tries: report the replies verbatim and stop before the live run (the user decides on stronger wording).

**Rollback.** Revert the commit; no persisted state.

### R2: the same action from the same screen a third time counts as "did nothing" (G1)

**Root cause.** `Loop._settle` (`agent/loop.py` ~lines 318-320) derives the verdict from the pixel/text effects. On the keyboard the cursor arrow blinks and the perceiver text jitters (`P_K M_N` vs `PK MN`), so every `cancel` yields effects `screen_changes` (pixel) plus `text_appears` (model) and verdict `changed`; `execution` is `done`, so neither the verdict rule nor W4 variant B (blocked/failed only) counts a failure. The evidence that it is a no-op is in the trace already: the before-frame hash alternates between only two values over rows 113-150 (`37d3e28e` x19, `e0ef899d` x18, plus one first frame) for the same intent `p_place_5|cancel|modal:cancel`.

**Current code (`agent/loop.py`):**
```python
318        idle = bool((choice or s.get("pick_key")) and not skill.inputs and skill.status == "done")  # a picked option that sent no input yet "succeeded" did nothing: a failure of that option, ...
319        v = "progress" if closed else "failed" if idle else action_result(skill.status, outcome["effects"], novel) if choice else "unobservable"
...
335            failed = v in ("failed", "no_change") or skill.status in ("blocked", "failed") and last.get("execution") in ("blocked", "failed")
...
            f.update(n=f["n"] + 1, why=("did nothing" if idle else skill.reason or f"verdict {v}")[:40], step=self.step_no, act=f"{verb} {target}".strip())
```
`idle` already means "the option did nothing": it sets verdict `failed`, makes `Loop.fails[intent]` count up with why "did nothing", and the existing option notes then show `did nothing 3× (last step 117)` and state `failed` (`agent/world.py` `option()`, notes from `fails`). Reusing `idle` needs no new state.

**Edit (add one line after line 318):**
```python
        idle |= bool(choice and sum((r.get("choice") or {}).get("intent") == intent and r.get("frame_hash") == s["frame_hash"] for r in self.recent) >= 2)  # the same action from the same screen for the third time in five steps changed nothing that lasts
```
`self.recent` is the last five rows (`loop.py` `self.recent = [*self.recent, row][-5:]`), each row carries `frame_hash` and `choice.intent`; `s["frame_hash"]` is this step's before-frame hash.

**Why it is safe (offline evidence, 0 requests).** Rule applied to the recorded traces:
```
.venv/Scripts/python.exe - <<'E'
import json
for run in ('m1_w8_live_150','m1_h1h4_live'):
    rows=[json.loads(l) for l in open(f'runs/{run}/trace.jsonl')]; recent=[]; fire=[]
    for r in rows:
        it=(r.get('choice') or {}).get('intent')
        if it and r['verdict']!='progress' and sum(1 for x in recent if (x.get('choice') or {}).get('intent')==it and x['frame_hash']==r['frame_hash'])>=2: fire.append(r['step'])
        recent=(recent+[r])[-5:]
    print(run, len(fire), fire)
E
```
Measured: `m1_h1h4_live` 0 firings; `m1_w8_live_150` 33 firings, steps 118-150, all `keyboard cancel`. (A stricter-than-needed variant that also compares the after-frame fired one step earlier but also fired on a legitimate dialogue `advance` at step 99, so the three-in-five before-frame rule is the chosen one.) The `progress` verdict is excluded in the offline check; in the code `closed` already wins over `idle` (`v = "progress" if closed else ...`).

**What the model then sees.** At step 118 the `cancel` option reads `46. cancel — did nothing 1× (last step 118)` and Loop.fails keeps counting; the act model can pick the end key (R1) or any other option. No option is removed. `Stuck` is unchanged: it already fired for this loop (T4), and R2 does not rely on it.

**Risk this creates (and how it is covered).** Once `cancel` is marked failed, the model may pick `advance` (A) or a letter on the keyboard, which types a letter: exactly the custom name the directive forbids. R2 alone must therefore not ship without R1, and the micro-check's step-118 prompt (the keyboard with `cancel — did nothing 1×`) tests this before any live run. The live run also checks `presses.jsonl` for any `A` press on a keyboard row other than the one that confirms `ED`.

**Blast radius (CodeGraph).** `_settle` has one production caller (`Loop.step`/`_act`); `idle` is local. Anything that reads the verdict (`Stuck.update`, planner evidence, `eval_run` `repeated_mistakes`, `predict.result`) sees `failed` for the rows R2 marks, which is correct (these are repeated mistakes). Tests that run the same intent with identical static frames three times and expect no failure would conflict: run the full suite; `tests/agent/test_loop.py` uses static `GridEnv`/`Busy` frames in many tests. **If any existing assertion fails, STOP and report the test name and output (JUDGMENT); do not edit assertions.**

**Tests to add (`tests/agent/test_loop.py`, template: `test_blocked_with_novelty_still_counts_as_failure` at line 529).** `test_third_identical_action_from_the_same_screen_counts_as_did_nothing`: monkeypatch `module.perceive` to return `{"screen": "keyboard", "text": [], "entities": []}`, monkeypatch `module.act` so it picks the option with `tool == "cancel"` (use `fake_act`/`SimpleNamespace(action=dict(row["action"]), option=row["n"], why="t", text=None)` as in the test at line 1301), and monkeypatch `module.run_affordance` to return `SimpleNamespace(status="done", reason="", inputs=1, calls=0, before=frame, after=frame2, scroll=(0, 0), blank_seen=False, moves=[])` where `frame2` is `frame` with a few pixels changed (so the verdict would be `changed`). Assert: steps 1 and 2 give verdict `changed` and `intent not in loop.fails`; step 3 gives verdict `failed` and `loop.fails[intent]["n"] == 1` with `why == "did nothing"`; the `cancel` option from `loop.world.options(per, loop.place, loop.fails, 4, inputs=list(loop.env.inputs()))` has state `failed` and a note containing `did nothing`. Second test, `test_two_identical_actions_are_not_yet_a_failure`: steps 1-2 only (covered by the first test's first assertions; one test is enough).

**Rollback.** Revert the commit; no persisted state.

### R3: an `add` whose target names nothing known stays a quest without a target (G2) — NEEDS USER APPROVAL

**Why a change is needed.** Evidence, steps 54 and 57 (see audit B(a)): the planner proposed the person `PROF. OAK`, who is not on the map yet. `Notebook._apply` rejects the whole add, and the W3 follow-up repeated the same mistake twice, leaving 10 goal-less steps (54-63). The target is only an auto-close helper: the quest text carries the goal, and the quest is also closed by `done`/`drop`. Rejecting a game-text-grounded quest because a helper field is unresolvable throws away the plan; the r2 plan chose to keep this strictness only because it assumed W3 would repair it, which the run disproves (4 of 7 follow-ups recovered, none of the target errors did).

**Current code (`agent/notebook.py:84-86`):**
```python
            target = op_data.get("target")
            resolved = self._resolve(target) if target is not None else None
            if target is not None and resolved is None: return self._no_target(target)
```
**Edit.** Keep the error only for an ambiguous target (the planner can pick a listed ref), and drop an unknown one:
```python
            target = op_data.get("target")
            resolved = self._resolve(target) if target is not None else None  # an add whose target names nothing known keeps its text and why: the quest just has no target
            if target is not None and resolved is None and self.ties(target): return self._no_target(target)
```
The `target` op (retargeting an existing quest, line 116) keeps its error. `why` citation rules are untouched (no memory poisoning).

**Blast radius.** `Notebook._apply` is reached through `apply_ops` (callers: `Loop.step`, `scripts/eval_plan.py:245`). **Four existing assertions conflict and must be changed only if the user approves R3:**
1. `tests/agent/test_notebook.py:34` case `add-unresolved-target`: expected result changes from the `matches no known place or thing` error string to `None` (ok).
2. `tests/agent/test_notebook.py:107-108`: `nb.apply_ops([add("unknown", target="Thing Z")], 3)[0]["result"].startswith('error: target "Thing Z" ...')` becomes `== "ok"`, and `nb.data["quests"][-1]["target"] is None`.
3. `tests/agent/test_notebook.py:286-287`: `add("q", target="Hal")` expects `error: target "Hal" ... closest: Hall, MOM, Guide`; becomes `"ok"` (the target op test at line 47 and the ambiguous test at 311-313 stay as they are).
4. `tests/agent/test_loop.py` `test_plan_followup_before_act_is_bounded_and_reads_errors`, case `rejected_target` (lines ~1120-1167): the first reply (`add q3 target "PROF. OAK"`) now succeeds, so there is one plan call and focus `q3`. Move this case's expectations: `len(plan_prompts) == 1`, `loop.notebook.focus == "q3"`, `row["plan"]["ops"][0]["result"] == "ok"`, and remove the `startswith("error: target")` / `in plan_prompts[1]` lines. The `invalid_twice` case (invalid target `"B"` with no `why`, errors from the `why` rule) is unaffected.
Add one test: `test_add_with_unknown_target_keeps_the_quest_without_a_target` in `tests/agent/test_notebook.py` (asserts result `ok`, `quest["target"] is None`, an ambiguous target still errors).

**Offline verification (0 requests).** The unit tests above. Replay of the recorded step-54 and 57 replies is not needed: both are `add` ops with an unknown target on a notebook whose last text is `t4` (the test reproduces them verbatim).

**If the user declines R3.** Keep G2 open; it costs up to about 10 goal-less act steps per occurrence, and the next T1 or the floor eventually repairs it. A no-assertion alternative exists but is uglier: pre-clean unresolved targets in `plan()` (`agent/plan.py`, after `ops = data.get("ops")`) using `notebook._resolve`; not recommended (it calls a private method from another module).

**Rollback.** Revert the commit; no persisted state.

### R4: second follow-up when an all-ok follow-up closed at once (G5)

**Root cause.** Section A, G5. In the planner block (`agent/loop.py:150-163`, shown here from the repo) the W3 follow-up runs at most once, and an `add` whose thing-target was already crossed closes inside `apply_ops` (`_close_crossed`), so a valid plan can still leave `focus is None`:
```python
            results = self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
            if p.ops and self.notebook.focus is None and any(r["result"] != "ok" or r["op"] in ("done", "drop") for r in results):
                p = plan(self.model, "no open quest", self.notebook, self.world.view("", place), per.get("text", []), recent)
                results += self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
```
**Edit.** Add a second follow-up only when the first follow-up was entirely ok yet no quest is open (its quest was already accomplished; `render()` then shows it under "Recently closed ... went through exit: stairs"). Invalid replies do not get a third call, so the existing bounded test is untouched:
```python
            if p.ops and self.notebook.focus is None and any(r["result"] != "ok" or r["op"] in ("done", "drop") for r in results):
                p = plan(self.model, "no open quest", self.notebook, self.world.view("", place), per.get("text", []), recent)
                more = self.notebook.apply_ops(p.ops, self.step_no + 1, p.why); results += more
                if p.ops and self.notebook.focus is None and all(r["result"] == "ok" for r in more):  # ok, but its quest was already accomplished
                    p = plan(self.model, "no open quest", self.notebook, self.world.view("", place), per.get("text", []), recent)
                    results += self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
```
(`results` already feeds `changed`, `_plan_row["ops"]`; `_plan_row["why"]` is the latest call's why. At most 3 planner calls per trigger, only in this narrow case; it occurred once in 150 steps, i.e. +1 call.)

**Blast radius.** `Loop.step` only. `test_plan_followup_before_act_is_bounded_and_reads_errors` asserts exactly 2 plan prompts for `closes_without_adding`, `rejected_target`, `invalid_twice` and 1 for `empty_ops`, `malformed`: R4 does not change those (first follow-up either leaves a quest open or has an error). **No assertion changes expected; if one fails STOP and report.**

**Test to add (`tests/agent/test_loop.py`, copy the setup of `test_plan_followup_before_act_is_bounded_and_reads_errors`: `short_settle`, `scripted_world`, real `act`, a `Capture(StubModel)` that serves scripted plan replies).** `test_followup_accomplished_at_once_gets_one_more_call`. The notebook's callbacks are plain instance attributes, so stub them to make `_close_crossed` close a quest that targets a thing which leads to a place already entered:
```python
    loop.notebook.ref = lambda t: "thing:door" if t == "door" else None
    loop.notebook.canon = lambda v: v
    loop.notebook.route = lambda r: None
    loop.notebook.leads_to = lambda r: ["b"] if r == "thing:door" else []
    loop.notebook.data["moves"] = [[1, "a", "b"]]  # entered b at step 1; the plan runs at step 1, so s >= opened - 1
    plans = iter([[{"op": "done", "key": "q1", "reason": "named"}],
                  [{"op": "add", "key": "q2", "text": "Go through the door", "why": ["t1"], "target": "door"}],
                  [{"op": "add", "key": "q3", "text": "Talk to the professor", "why": ["t1"]}]])
```
Seed q1 open (as the `closes_without_adding` case does) and queue a T1 event. Assert: 3 plan prompts; q2 has status `done` with reason `entered`; `loop.notebook.focus == "q3"`; the act prompt does not start with `Goal: (none)`. Second assertion block: the same setup but the third reply is invalid (`add` without `why`) gives exactly 3 prompts and focus None (no fourth call).

**Rollback.** Revert the commit.

### R5: eval counts place identity on world rows, and the cutscene edge (G3, G4) — NEEDS USER APPROVAL (gate semantics)

**Root cause.** Place identity is defined only on world screens (`agent/memory.py` `match_place`: non-world screens are "a state of the current place, never a place"), but `scripts/eval_run.py` counts every row, including dialogue, in place purity, and decides an edge only from a single row whose truth map changes between step and step+1. For Oak's cutscene (always a scripted entry in this game) the 18 dialogue rows carry the previous label and the town->lab edge can never be recorded, so `m1_gate_pass` cannot become true however well the harness behaves. Measured on this run, using only world rows (`screen == "world"`): `p_place_2` 5 rows all 0x26, `p_place_3` 8 rows all 0x25, `p_place_4` 14 rows all 0x00, `p_place_5` 8 rows all 0x28 (all four 1.0), and consecutive world rows whose label changes give the truth edges `0x26->0x25`, `0x25->0x00`, `0x00->0x28`.

**Current code (`scripts/eval_run.py` `evaluate`, ~lines 1070-1103):**
```python
    for row in full_trace:
        t = truth.get(row.get("step"))
        pid = row.get("place")
        if t and pid in places and places[pid].get("state") == "place" and t.get("map") is not None:
            step_places[pid][map_id(t["map"])] += 1
    ...
    edges_found = {f"{a}->{b}": ((a, b) in valid_edges or (b, a) in valid_edges) for a, b in EDGES}
```
**Edit.**
1. In the purity loop add `and row.get("screen", "world") == "world"` to the condition (rows without a `screen` key, as in old fixtures, still count).
2. Just before `edges_found = ...` add:
```python
    world = [r for r in full_trace if r.get("screen", "world") == "world"]
    for a, b in zip(world, world[1:]):  # a scripted entry (a cutscene on dialogue screens) has no crossing row: the label change between world frames is the edge
        ta, tb = truth.get(a.get("step")), truth.get(b.get("step"))
        if a.get("place") != b.get("place") and ta and tb and ta.get("map") is not None and tb.get("map") is not None:
            valid_edges.add((map_id(ta["map"]), map_id(tb["map"])))
```
**Blast radius.** `evaluate` is called by `scripts/eval_run.py` main and `tests/agent/test_eval_run.py`; existing assertions to keep: `test_eval_run.py:59` (purity all 1.0), `:64` (`all(edges_found.values())`), `:68` (gate pass), `:89`, `:192`, `:202-203`. They must still pass without edits (rows in those fixtures have the maps they assert). **If one fails, STOP and report (JUDGMENT).** The README/`summary.json` of older runs is unaffected until re-evaluated.

**Tests to add (`tests/agent/test_eval_run.py`).** `test_place_purity_ignores_dialogue_rows`: a fixture with 2 world rows on map A labelled `p_a` and 3 dialogue rows on map B labelled `p_a` -> purity of A is 1.0. `test_edge_is_found_across_a_cutscene`: world row (p_a, map A), dialogue rows (p_a, map B), world row (p_b, map B) with no `verified_edge` -> edge A->B found.

**Offline verification (0 requests).**
```
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_w8_live_150 --no-write
```
Expected after R5: `place_mapping` has non-null places for all four, `edges_found` all true, `m1_gate_pass` true (player RED, rival BLUE, starter 109, game text 139). For `runs/m1_h1h4_live` expect no regression of other fields (note its gate stays false: names RED/RED).

**Honesty note for the report.** This changes what the gate measures; state it plainly in the commit message ("purity on world rows; cutscene edge from consecutive world labels").

**Rollback.** Revert the commit.

### D1: deferred, settle overhead

Data (audit B(g)): non-model 3.94 s/step of 6.58; loop settles 32 + 38 frames per step on average; a keyboard `cancel` step with 0.37 calls still costs about 2.5 s, so the floor is frame work, not model work. Candidate (not planned now): reuse the previous step's settled after-frame as this step's before-frame when no input was sent in between (saves about 33 frames per step); measure on a recorded stream first. `scripts/run_agent.py` is the user's WIP file, so run_agent-side timing cannot be added by this plan. No work in this round.

---

**User decisions (recorded):** R3 approved (the 4 listed assertion edits only), R5 approved, micro-check and live resume confirmation approved, ledger cap raise approved in principle but the user edits `scripts/run_agent.py` themselves.

## Order, gates, cost

| Order | Commit | Gate to proceed |
|---|---|---|
| 0 | R0 baseline | suite 687 passed, 1 skipped; eval recorded |
| 1 | `R1 act prompt declines optional naming offers` | suite green; new test passes; pinned assertions unchanged |
| 2 | `R2 identical repeated action counts as did nothing` | suite green **with zero assertion edits**; offline rule check prints 0 firings for `m1_h1h4_live` and only steps 118-150 (keyboard `cancel`) for `m1_w8_live_150` |
| 3 | `R4 second follow-up for an accomplished-at-once quest` | suite green, no assertion edits |
| 4 | `R3 unknown add target keeps the quest` | **only after the user approves**; suite green after exactly the 4 listed assertion edits |
| 5 | `R5 eval: place identity on world rows; cutscene edge` | **only after the user approves**; suite green with no assertion edits; offline eval shows gate true |
| 6 | live confirmation (resume from 100; full run only after the user raises the caps) | see below |

Rules for go/no-go: any unexpected existing-assertion failure = STOP, report test name and output, do not edit it (JUDGMENT). Each commit is independently revertible (`git revert <sha>`); none changes `harness.json`, memory schema, ledger or caps. Commit with explicit paths only (`git add <file> <file>`), never `git add -A`, never the user's WIP files.

**Cost.** R1-R5 add no model calls except R4 (+1 planner call in the narrow all-ok-but-closed case; about +1 per 150 steps) and R2 (changes which option gets picked, not call counts). The 4 wasted T4 plans of the keyboard loop disappear. Ledger: `runs/e3_budget.json` total 5852, caps 6015 (total) and 5965 (m1 bucket): **163 asks of headroom**. A full 150-step run costs about 313 asks (2.1 uncached asks per step) and does not fit; a 50-step resume from checkpoint 100 costs about 105-130 and fits (still tight: a `CallCap` ends the run early). Raising caps is the user's decision and edit (`scripts/run_agent.py:16`, their WIP file).

### MECHANICAL vs JUDGMENT

| Step | Who |
|---|---|
| R0 commands and results table; running the offline rule check; running the offline eval before/after; collecting the final live-run numbers into the table; creating the run folder copy | MECHANICAL (Haiku can do these from the exact commands) |
| Writing R1, R2, R4 code and tests, deciding anything when a test fails | JUDGMENT (executor with repo access; stop and report on mismatch) |
| R3 and R5 (they change assertions or gate meaning), reverting a commit, running the optional live micro-check, the final go/no-go | JUDGMENT, and R3/R5 additionally need the user's explicit yes |

### Final live confirmation (cloud calls; evaluation runs are generally approved but flag it; check ledger first)

1. Viewer: `curl -s http://127.0.0.1:8765/state > /dev/null || python scripts/watch_run.py --no-browser &` (the user normally keeps it open).
2. Copy, never link, the recorded run; the resume keeps checkpoints up to step 100 and replays from there (`scripts/run_agent.py --run` needs the full path `runs/<name>`; a bare name creates the folder in the repo root; `check_tracks.py` takes the run NAME):
```
cp -r runs/m1_w8_live_150 runs/m1_r3_resume100
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r3_resume100 --resume --from-step 100 --steps 50 --max-calls 140 --decide
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_r3_resume100
```
Resumed rows 1-100 are the old run's, so only rows 101-150 test the new code: starter, nickname offer, keyboard. If the user approves a cap raise to at least about 6350 (total) / 6300 (m1), run the full 150 from a clean start instead: `--run runs/m1_r3_live_150 --steps 150 --max-calls 400 --decide` (a new folder; no `--resume`).

**Success targets (full run / resume rows 101-150):**

| Metric | Target |
|---|---|
| Starter received (steps_to_starter) | at most 109 (full run) |
| Goal-less act steps after step 19 | 0-3 with R3+R4 landed; at most 13 without R3 |
| repeated_mistakes | at most 11 |
| Nickname loop length (max consecutive identical `cancel` on the keyboard) | **0** (the nickname offer is answered NO); if the keyboard still opens, at most 5 |
| Player and rival names | preset and distinct (RED/BLUE or equivalent preset pairs); no typed letters in `presses.jsonl` on a keyboard screen |
| Lab place and purity | own place, purity 1.0 |
| Town mapping and m1 gate | town non-null; `m1_gate_pass` true (requires R5; without R5 expect false) |
| Perceiver bad-reply rate | at most 2% of uncached perceiver calls |
| s/step | at most 6.6 (no regression; the shortcut is deferred) |
| Ledger | total stays below the cap; no `CallCap` abort |

Failure handling: a typed letter on the keyboard, or YES chosen at the nickname prompt, is a no-go for R1 (report the prompt text from `calls.jsonl`); a keyboard loop longer than 5 means R2 is not firing (compare `fails` notes in the act prompts).

---

## Round r3b: after the first execution (R1, R3, R4, R5 landed; R2 stopped; micro-check failed)

**Status.** Committed: R1 `6943d44`, R4 `d8951df`, R3 `7fba2dd`, R5 `44676de`; suite 692 passed, 1 skipped. R2 was not committed (2 existing assertions failed). The R1 micro-check failed (10 asks; ledger now 5862/6015, m1 5812/5965, **153 asks left**), so the live run was skipped. Recorded-run gate is true under R5.

### What the failures show

**R2 conflicts (both real, both mine).** The planned rule counted *any* earlier row with the same intent and before-frame. Both tests repeat an action that was **blocked** before and then **works**:
- `test_harness_reliability.py::test_duplicate_exit_records_pick_the_observed_door_...` (line 201): three blocked exit attempts kept their real reason `exit mismatch`, but R2 relabelled them `did nothing`.
- `test_loop.py::test_route_stage_failure_is_no_evidence_about_the_exit` (line 1726 in the scratch copy): after three blocked crossings, the fourth one succeeds (`status done`, screen changes), and R2 marked that success as a failure, so the exit's `does_nothing` relation stayed `refuted`.
The keyboard loop is different: every repeated row was itself `done`, stayed in the same place and had no scene cut.

**R2' (corrected rule, verified with zero requests).** Count only earlier rows whose execution was `done`, and only for a `done` action that stayed in the same place without a cut:
```python
        idle |= bool(choice and skill.status == "done" and next_place == place and not scene_cut and sum((r.get("choice") or {}).get("intent") == intent and r.get("frame_hash") == s["frame_hash"] and r.get("execution") == "done" for r in self.recent) >= 2)  # the same action from the same screen for the third time in five steps changed nothing that lasts
```
(one line after the existing `idle = ...` line in `Loop._settle`, `agent/loop.py` ~line 319; `next_place`, `place`, `scene_cut`, `intent`, `s` are all locals there.)
Evidence: applied to a `git archive HEAD` copy with the preserved `.tmp/r3-r2.patch` test, the full suite gave 690 passed, 3 skipped, 1 failed; the one failure (`test_run_provenance.py::test_resume_hands_the_checkpoint_modes_...`) comes from the copy having no `.git` (provenance "incomplete") and passes in the repo. Both earlier conflicts pass. On the recorded traces the corrected rule still fires on 0 rows of `m1_h1h4_live` and on exactly steps 118-150 (33 rows) of `m1_w8_live_150`. **No assertion changes.** Use the test from `.tmp/r3-r2.patch` unchanged.

**R1 micro-check (verbatim result, 2 tries each).** 18: RED, RED. 24: GARY, BLUE (both presets, distinct from RED: fine). **111: YES, YES** ("asking for confirmation to select Squirtle"). **113: cancel, cancel** ("cancel declines the optional naming offer"). **118 (cancel already marked failed): advance, advance** ("advance to confirm the empty name entry") — `advance` presses A on the highlighted letter, i.e. it types a nickname. Reading: "decline any optional naming offer" is too abstract for gemma; it does not map the fragment `give a nickname; to SQUIRTLE?` to it, does not know that `cancel` cannot leave a keyboard, and does not know which key is the "end key".

### R1b: concrete wording, chosen by a 12-ask prompt test before any commit

Two candidate clauses, each replacing R1's clause inside the same sentence (4 parts under `split(". ")`, no banned words, the pinned preset substring unchanged):

- **A (fully generic, 498 chars total):**
  `; pick a preset or default name or setting when offered instead of typing your own, answer no when asked whether to give or change a name or nickname, and never type letters into a name keyboard (leave it by choosing its end key). `
- **A+ (A plus common UI labels for the end key):** same as A but the parenthesis reads `(leave it by choosing its end key, often labelled END, ED or OK)`. `ED` is the label this game shows; it is a UI label, not a character or place name, but it is closer to game-specific: **user decision**.

Test (12 asks, ledger-charged, no cache; run from the repo root):
```
PYTHONIOENCODING=utf-8 PYTHONPATH=. .venv/Scripts/python.exe - <<'E'
import json
from pathlib import Path
from agent.model import Ollama
from agent.act import SYSTEM
from scripts.run_agent import _ledger
m=Ollama("gemma4:31b-cloud"); post=m._post
m._post=lambda p: (_ledger(Path("runs/m1_r3b_micro"), None, 12), post(p))[1]
old=SYSTEM[SYSTEM.index('; pick a preset'):SYSTEM.index('Return only')]
A='; pick a preset or default name or setting when offered instead of typing your own, answer no when asked whether to give or change a name or nickname, and never type letters into a name keyboard (leave it by choosing its end key). '
V={'A': SYSTEM.replace(old, A), 'A+': SYSTEM.replace(old, A.replace('its end key)', 'its end key, often labelled END, ED or OK)'))}
assert all(len(v.split('. ')) == 4 for v in V.values())
c=[json.loads(l) for l in open('runs/m1_w8_live_150/calls.jsonl', encoding='utf-8')]
act={s: next(x for x in c if x['step']==s and x['role']=='act') for s in (111,113,118)}
act[118]['user']=act[118]['user'].replace('\n46. cancel\n', '\n46. cancel — did nothing 1× (last step 118)\n')
for name, system in V.items():
    for s, x in act.items():
        for _ in range(2): print(name, s, m.ask(system, x['user'], [], x['schema'], role='act').data)
E
```
Go for a variant: 111 picks option 2 (NO) in both tries, 113 and 118 pick option 44 (`select ED`) or option 47 (free buttons) in both tries, never a letter and never `advance`/`cancel`. Choose A if it passes; A+ only if A fails, A+ passes and the user has approved A+. If neither passes: stop, report verbatim, do not commit R1b, skip the live run (the keyboard would still be typed into).

Commit R1b: edit `agent/act.py:9` to the chosen clause; update R1's own test (`tests/agent/test_act.py:85-86`, added this round) to assert `"answer no when asked whether to give or change a name or nickname" in SYSTEM and "end key" in SYSTEM` (this assertion was created by R1 in this round, so changing it is in scope).

### Order and budget

| Order | Step | Asks |
|---|---|---|
| 1 | R2' commit (`R2 identical repeated done action counts as did nothing`) | 0 |
| 2 | R1b prompt test, then commit the passing variant | 12 |
| 3 | Live resume from checkpoint 100 (commands in "Final live confirmation" above, run folder `runs/m1_r3_resume100`) | about 105-130 |

Total about 117-142 of the 153 left: it fits, but a `CallCap` could end the resume early. A cap raise (the user's edit in `scripts/run_agent.py:16`, e.g. +300 on both) removes that risk and allows the full 150-step run.

Live success targets are unchanged, plus: no `A` press on a keyboard screen except the one confirming the end key (`presses.jsonl` joined with trace `screen`), and the nickname prompt answered NO.
