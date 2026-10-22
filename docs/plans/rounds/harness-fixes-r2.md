# Avatar harness: fix plans from run `m1_h1h4_live`, with an independent audit

**Summary (6 lines)**
1. The goal-less steps have three causes. Game text is only allowed to trigger the planner on the world screen. The planner gets no second try when its reply leaves no open quest. The 3-step replan floor then delays the next attempt.
2. The stale rival-name goal (B) has the same cause. The planner was not called at all between steps 20 and 41, although the rival question was already queued as an event at step 24.
3. The viewer flicker (C) is confirmed. Any failed `/live-frame` fetch clears the canvas to black, and so does every tab hide/show. The fix is two lines in the page script and nothing on the server.
4. The place mislabel (D) is **already fixed** by commit `8ef7c3d`. The run was recorded before that commit, and the mislabel started at a resume boundary (step 90→91). D only needs verification.
5. The blocked loops (E) come from a bug that was not in the brief. A blocked or failed action that also revealed new map cells is labelled `progress`, and that wipes its failure counter. 26 steps were affected. Separately, two eval metrics (`repeated_mistakes`, `edges_found`) are broken.
6. On cost and latency (F): measure first. The one low-risk saving is compact perceiver JSON (about 37% of perceiver output characters are whitespace or code fences). That change needs a paid `eval_frames` run, so it needs the user's approval.

| id | title | files | risk | depends on |
|---|---|---|---|---|
| W1 | Viewer keeps the last frame on a failed fetch or a tab switch (C) | `scripts/watch_run.py` | low | – |
| W2 | Game text (T1) can trigger the planner on menu and keyboard screens (B) | `agent/loop.py` | low (about +3 planner calls per 144 steps) | – |
| W3 | One immediate follow-up planner call when a plan leaves no open quest (A) | `agent/loop.py` | low–med (at most +1 planner call per plan) | W2 |
| W4 | A blocked or failed action counts as a failure even when it revealed new cells (E) | `agent/loop.py`, `scripts/check_tracks.py` | med (changes option notes and states) | – |
| W5 | Fix the `repeated_mistakes` and `edges_found` metrics (E, audit) | `scripts/eval_run.py` | low (eval only) | – |
| W6 | D: verification only, plus an optional real-frame regression test | `tests/agent/` | none | – |
| W7 | F1: record settle frame counts in each trace row (instrumentation only) | `agent/loop.py` | none | – |
| W8 | F2: compact perceiver JSON output | `agent/perceive.py`, `agent/loop.py` (`prompt_version`) | med (accuracy; invalidates the perception cache) | W7, user approval for cloud eval |
| W9 | F3: shorter settle waits | `agent/observer.py` / `agent/loop.py` | high, deferred | W7 data |
| N1 | Not planned: the m1 gate requires player/rival names HAND/BLUE, which nothing tells the agent | `scripts/eval_run.py` | user decision | – |
| N2 | Not planned: keyboard nickname loop (steps 127–137) and `select A`×4 before `type` (steps 26–29) | act/options | separate investigation | – |

---

## Context (no repo access assumed)

**Repo and code layout**
- Repo `Avatar`, branch `feat/lean-agent`. It is a Pokémon Red agent harness running on the PyBoy emulator; every model role uses `gemma4:31b-cloud`.
- Each step of `agent/loop.py` `Loop.step()` does the following:
  1. perceive the frame (`agent/perceive.py`);
  2. maybe call the planner (`agent/plan.py`), which edits a quest notebook (`agent/notebook.py`);
  3. build the numbered options (`agent/world.py` `World.options`) and have the act model pick one (`agent/act.py`);
  4. run the chosen skill;
  5. in `_settle`, judge the outcome and write a trace row.
- The act prompt starts with `Notebook.render_focus()`. That line is `Goal: <first open quest>`, or `Goal: (none)` when no quest is open.

**Planner triggers**
- T1: new game text. T2: arrival somewhere new. T3: a quest closed. T4: stuck. T5: no open quest.
- Events wait in `Loop.events`. A plan may only run once 3 steps have passed since the last one.

**Viewer**
- `scripts/watch_run.py` is an HTTP server with an embedded HTML/JS page.
- The agent publishes `live-frame.bin` atomically (write a temp file, then `os.replace`). The page polls `/live-frame` and draws the PNG it gets onto a canvas.

**Project rules**
- Fix the root cause with the smallest change and reuse existing code.
- No new mechanical gates: the agent infers its goals from game text.
- Spatial memory must stay cheap (no extra cloud calls).
- Run tests with `pytest -n auto`.
- Never link data directories into worktrees: tests cannot read `runs/`.
- Changing the call ledger or caps needs the user's approval.

**Run facts**
- 144 steps over 3 invocations.
- Invocation 1: steps 1–90, code `0bd53eb`.
- Invocation 3: resumed from checkpoint 90, code `2430653`.
- Commit `8ef7c3d` was made after the run.
- Checkpoints exist at steps 0, 25, 50, 75, 90, 100, 125 and 144 (`runs/m1_h1h4_live/checkpoint/<step>/harness.json` plus `memory/`).

---

## Independent audit (each claim checked against the trace, calls log and code)

| # | Claim | Verdict | What the data shows |
|---|---|---|---|
| A1 | 50 of 144 act steps and 24 of 48 world steps had `Goal: (none)` | **Right, but misleading** | 50 is correct (counted from the act prompts in `calls.jsonl`). The goal-less runs are 1–18, 42–44, 48–64, 70–75, 112–114 and 140–142. Steps 1–18 are title screens and intro before any quest could exist, so 32 goal-less steps are fixable, not 50. |
| A2 | Handoff pattern: the planner closes a quest at step N and only opens the next on a later T5 | **Right; the cause is the 3-step floor** | After `done`, T5 is queued on the next step but has to wait until `last_plan + 3`. Planner rows land exactly 3 apart (42→45, 48→51→54). Checkpoint 50 holds `events=[T5 @49]`, `last=48`. |
| A3 | Correct plans lost to the validator at steps 51, 54, 70, 112 | **Overstated** | Step 51: the model left out `why` entirely (it put "t3" in the text), so rejection was correct. Steps 54 and 70: target `"PROF. OAK"`, a person not yet seen. The validator follows its contract, but it throws away the whole `add`. Step 112: the model sent op `target` with a new key, which was correctly rejected. The plans were malformed. The real defect is that nothing asks again until the next trigger. |
| A4 | Furniture poking at steps 53–63 | **Right; a second cause was missed** | Steps 56–62 were `blocked` but got verdict `progress` because each revealed new cells (see E). That cleared each failure counter, so the same desk, table and computer were offered again as untried. |
| B | Stale goal on "RIVAL's NAME?"; both names RED; quest only changed at step 45 | **Right on symptom, wrong on cause** | Truth confirms both names are RED. The planner was **not called at all** between steps 20 and 41. At step 24 the dialogue ended and queued T1 ("…what is his name again?"), but `_plan_trigger` holds T1 until a world screen appears (step 42). q1 was closed at 42. q2 ("name the rival") was opened at 45, after the rival had been named at step 30, and closed at 48: a quest created after the fact. Checkpoint 25 holds that waiting T1 event. |
| C | Viewer flicker mechanism | **Right**, plus a second source | Confirmed in the page code (excerpt below). Every `visibilitychange` (tab hide/show) also calls `resetFrames()`, which clears the canvas. A generation change after `load_state` resets correctly. The 96/67k stress figure was not re-run. |
| D | Lab mislabelled as town; caused by `8ef7c3d` re-identification | **Wrong attribution** | Truth says map 0 (town) at step 90 and map 40 (Oak's lab) from step 91. Step 91 is the resume boundary of invocation 3 (code `2430653`, which skipped the cut test when there was no prior frame). `8ef7c3d` was committed after the run and fixes exactly this: on resume it compares against `frames/000090_after.png`, carries the cut across dialogue screens, and treats a zero best shift with more than 50% of pixels changed as a cut. Current code on the real frames gives `changed=0.569`, `scroll=(0,0)`, `cut=True`. The perceiver even reported `"place": "laboratory"`. |
| D2 | "No edges found", Oak's lab mapping null | **Partly a metric bug; summary is stale** | `summary.json` was written at 21:12, after invocation 1, so it covers only 90 rows. A read-only re-evaluation of all 144 rows gives: 108 done / 36 failed, steps_to_starter 124, and Pallet Town mapping also null (purity dragged down by the lab steps). `edges_found` reads `places[src]["edges"]`, a field the agent no longer writes (crossings now live in `crossings.jsonl` and the trace field `verified_edge`). It is therefore always false on live runs, and the viewer's edges panel is always empty too. |
| E | 26 failed/blocked steps; "approach unexplored" blocked 10× in a row at 76–85; `repeated_mistakes` 0 | **Numbers off; root cause missed** | The full trace has 36 failed or blocked executions. **26 of them have verdict `progress`** (23 blocked, 3 failed). `predict.result` returns `progress` whenever new cells were seen, and `_settle` only counts verdicts `failed`/`no_change` as failures, so it pops the intent's failure counter (code below). At steps 76–85: 76–77 were exits and 78–85 were `explore`, mostly RIGHT. The act prompt at step 80 lists "explore RIGHT" under "Not tried here" with no notes. `repeated_mistakes` keys on `frame_hash`, which changes every live frame, and ignores blocked steps carrying the `progress` verdict. A corrected metric gives 23. |
| F | Wall 1141 s vs model 438 s; perceiver 84%; ~670 output and 740 prompt tokens | **Right** | Perceiver 369 s of 438 s. 117 uncached perceiver calls: median 672 output tokens, 740 prompt tokens, 2.7 s. Per screen type: dialogue 70 steps, average 5.3 s wall vs 2.0 s model (steps 1–41 were cache hits at about 0 ms and still took about 3 s). World 48 steps, 11.0 s vs 3.4 s, so about 7.6 s of non-model overhead (position probe and real-time walking), which is bigger than dialogue's. Keyboard 16 steps, 11.8 s, median 2075 output tokens because all 46 keys are listed as entities. Perceiver replies are pretty-printed inside a markdown code fence; compact JSON is 63% of the characters (51% if `placement` were also dropped). |

**Gaps found that were not in the brief**
- **G1.** Verdict `progress` masks failures (E above). This is the main driver of the loops.
- **G2.** Two eval metrics are broken (`edges_found`, `repeated_mistakes`), and `summary.json` was stale. Re-run `python scripts/eval_run.py runs/m1_h1h4_live` (it writes `summary.json`) after W5.
- **G3.** The m1 gate requires player name HAND and rival name BLUE, but nothing tells the agent those names, so the gate cannot pass. This is the user's decision; no fix is planned.
- **G4.** `scripts/check_tracks.py --menu` (the offline act-prompt replay) rebuilds failure counters with the same verdict-only rule (line 207). It must change together with W4.
- **G5.** Keyboard behaviour is inefficient. The agent picked `select A` four times before `type` (26–29). After choosing YES to the nickname prompt at 126, it spent 127–137 pressing advance and select on the keyboard. Out of scope here.

**Risks in the fixes proposed in the brief**
- Letting T1 fire on every screen, including dialogue, would call the planner every 3 steps through long conversations. W2 limits it to menu and keyboard.
- Relaxing `why` citation would let quests in without game-text grounding (memory poisoning). Don't. The add-without-target relaxation is also unnecessary once W3 asks again.
- "Surface blocked-count history to the act options" already exists in the option notes (`world.py:1013`). It is fed by `Loop.fails`, which the novelty verdict empties. Fix the counter (W4); add no new prompt content.
- Serving the last good frame from the server would hide a real disconnect and is unnecessary.
- A shorter settle wait risks perceiving half-printed text. Measure first (W7).

---

## Plan 1: planner gaps (A + B) → W2, W3

### Root cause, with code

`agent/loop.py:504-509`:
```python
def _plan_trigger(self, per):
    if self.notebook.focus is None and self.t5_armed: self._event("T5", "no open quest", self.step_no + 1); self.t5_armed = False
    if self.last_plan is not None and self.step_no + 1 - self.last_plan < 3: return None
    if not any(e["kind"] != "T1" or per.get("screen") == "world" for e in self.events): return None   # <- B
    return "; ".join(e["text"] for kind in ("T3","T1","T2","T5","T4") for e in self.events if e["kind"] == kind)
```

`agent/loop.py:150-161` (planner block inside `step()`). The plan is applied once; nothing re-asks before the act call at line 168:
```python
if (trigger := self._plan_trigger(per)):
    events, self.events = self.events, []
    evidence = self._evidence()
    p = plan(self.model, trigger, self.notebook, view, per.get("text", []), [f"... -> {r.get('verdict')}" for r in self.recent], evidence=evidence)
    self.new_places, self.planner_evidence = [], []
    results = self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
    changed = any(r["result"] == "ok" for r in results)
    ...
    self._plan_row = {"trigger": trigger, "events": events, "focus": self.notebook.focus, "ops": results, "why": p.why}
    view = self.world.view(self._goal_text(), place)
```

`Notebook.render()` (`agent/notebook.py:257`) already shows the planner its own last results ("Your last ops (step N): - add q3: error: target "PROF. OAK" matches no known place or thing; closest: …"). A second call can therefore read its own errors with no prompt change.

- **B.** The rule "hold T1 until a world screen" was meant to stop replanning mid-dialogue. But when a conversation ends, the next screen is often a decision screen (a naming menu, the keyboard, YES/NO). The existing test `test_dialogue_ends_when_its_screen_kind_changes` (tests/agent/test_loop.py:1968) already intends T1 to be produced there. It is just never consumed.
- **A.** A plan that closes the current quest without adding one, or whose `add` is rejected, leaves `Goal: (none)` for at least one act call. The T5 that would fix it waits for the floor. If that T5 plan fails too, T5 stays disarmed until new text arrives (48–64).

### Minimal change

**W2.** At `loop.py:508`, change `per.get("screen") == "world"` to `per.get("screen") in ("world", "menu", "keyboard")`. Update the docstring to say T1 waits while dialogue or battle is still on screen. Dialogue and battle screens keep waiting (the test at test_loop.py:1062 still holds).

**W3.** Inside the planner block, bind the recent-actions list to a local `recent`, then right after `results = self.notebook.apply_ops(...)` add:
```python
if self.notebook.focus is None and any(r["result"] != "ok" or r["op"] in ("done", "drop") for r in results):
    p = plan(self.model, "no open quest", self.notebook, self.world.view("", place), per.get("text", []), recent)  # one follow-up; render() shows the first call's results
    results += self.notebook.apply_ops(p.ops, self.step_no + 1, p.why)
```
- `changed`, `stuck.replanned`, `_plan_row["ops"]` (now both calls' results) and `_plan_row["why"]` (the latest) work unchanged.
- No new state and no new persisted fields. `t5_armed` is untouched: if the follow-up also fails, T5 retries after the floor exactly as today.

### Blast radius
- `_plan_trigger` has one production caller (`Loop.step`). Tests that call it: test_loop.py lines 908–929, 1062–1064, 1188–1196, 1364–1380 and 1913–1926. All pass `"world"`, `"dialogue"` or `{}`, so they are unaffected.
- `plan()` callers: `Loop.step` and `scripts/eval_plan.py`. `apply_ops` callers: `Loop.step` and `scripts/eval_plan.py:245`. `scripts/eval_run.py:846` reads `r.get("result") == "ok"`, which is unchanged.
- Tests with a scripted `StubModel` (`tests/agent/fakes.py`) whose plan reply closes a quest or errors without adding one will now consume one extra scripted reply. Fix those by adding a plan reply to the script, not by changing logic. Likely files: test_loop.py, test_qs_e2e.py, and the `--om` stubs in test_eval_act.py / test_om_eval.py (their stub plan returns `{"goals": ...}` with no `ops` list, which yields an error row and so one follow-up).

### Edge cases considered
- **Cost.** The follow-up fires at most once per plan, and only when the plan closed a quest or errored and no quest is open. Replaying the recorded plan rows, it would fire at 42, 48, 51, 54, 70, 112 and 140. It removes T5 plans that would otherwise follow (45, 115, 143). Net about +3–4 calls per 144 steps; W2 adds about +2–3 (screen transitions at 25, 65, 120, 127). Run caps (`max_calls`) are unchanged, so a capped run may stop slightly sooner. Flag it; do not change caps without approval.
- **Flapping or loops.** There is no recursion: one follow-up, and the floor still applies to every trigger.
- **A deliberate no-op plan** (empty `ops`) does not trigger a follow-up (`any([])` is False).
- **A wrong close target.** The follow-up may pick a "closest" target the error message suggested (e.g. MOM). That risk exists today on the next plan; it is not introduced here.
- **A goal that never closes** is unchanged; T4 stuck still applies.
- **Malformed JSON** (`ops` not a list) gives an error row and no follow-up: the golden test pins that behaviour, and it never happened in the recorded run. Only a non-empty ops list can trigger a follow-up.

### Tests (pytest -n auto)
Add to `tests/agent/test_loop.py`:
1. `test_dialogue_text_replans_on_a_naming_menu`. Setup: q1 open, `last_plan=19`, `step_no=24`, and a T1 event carrying "Erm, what is his name again?" (B evidence).
   - `_plan_trigger({"screen":"menu"})` and `({"screen":"keyboard"})` return a trigger containing that text.
   - `({"screen":"dialogue"})` and `({"screen":"battle"})` return None.
2. `test_plan_that_closes_without_adding_asks_once_more_in_the_same_step`. Monkeypatch `module.plan` with two replies: the step-42 reply (`[{"op":"done","key":"q1","reason":"…"}]`), then an `add q2` citing `t1`.
   - Assert exactly 2 plan calls, `notebook.focus == "q2"`, and the act prompt starts with `Goal:` but not `Goal: (none)`.
3. `test_rejected_target_add_gets_one_followup`. The first reply is the verbatim step-54 reply (`target:"PROF. OAK"`), so the first result starts with "error: target".
   - Assert that the second call's prompt contains that error text (it comes via `render()`).
4. `test_followup_happens_at_most_once`. Both replies are invalid (the step-51 add with no `why`).
   - Assert 2 calls and focus None. On the next step, still inside the floor, assert no plan call.
5. `test_no_followup_for_empty_ops`. Assert one call.

### Verification on recorded data (0 cloud requests; run from the main checkout, because worktrees have no `runs/`)
- **B, before vs after.** Load checkpoint 25 with `scripts/eval_act.py`'s `_plan_checkpoint("m1_h1h4_live", 25)` and call `loop._plan_trigger({"screen": "keyboard"})`.
  - Today: None.
  - After W2: a trigger containing "what is his name again".
  - This is exactly the state the agent was in at steps 26–30.
- **A.** Load checkpoint 50 (`events=[T5@49]`, `last=48`) to show the floor-blocked T5 that W3 makes unnecessary. W3 itself is validated by tests 2–4.
- **Optional live check** (spends cloud calls, needs approval): a short run from checkpoint 0 to step 50. Go criterion: no `Goal: (none)` act prompt after step 19 except during a plan's own floor, and a quest about naming the rival exists before step 26.

### Rollback
Revert W3 and W2 (two separate commits). Neither adds persisted state, so old checkpoints resume unchanged.

---

## Plan 2: viewer flicker (C) → W1

### Root cause
`scripts/watch_run.py` page script:
```js
function resetFrames(){frameEpoch++; if(frameAbort) frameAbort.abort(); ... displayedSeq=-1; ... $("live").getContext("2d").clearRect(0,0,160,144)}
...
    const res=await fetch("/live-frame",{cache:"no-store",signal:controller.signal});
    if(!res.ok) throw Error("frame unavailable");
...
  }catch(e){if(!controller.signal.aborted&&frameConnected){resetFrames()}}          // line 347
...
document.addEventListener("visibilitychange",()=>{visible=!document.hidden;resetFrames();if(visible)pullFrame()});   // line 352
```
- The server returns 503 whenever `read_live_frame()` returns None (`watch_run.py:256-257`). That happens when a read races the publisher's `temp.replace(path)` (on Windows a sharing violation raises OSError, which is caught and turned into None).
- One 503 clears the canvas, and the page shows black for at least 250 ms until the next pull.
- Every tab hide or show also clears the canvas.
- The legitimate reset is the poll noticing a run or generation change (line 358), which stays.

### Minimal change (2 lines, page script only)
- Line 347: change to `}catch(e){if(!controller.signal.aborted) frameConnected=false}`. This keeps the last drawn frame; the loop already backs off 250 ms when not connected.
- Line 352: change to `document.addEventListener("visibilitychange",()=>{visible=!document.hidden;if(visible)pullFrame()});`.
  - `pullFrame` already idles while hidden.
  - `frameBusy`/`frameWake` prevent double fetches.
  - `displayedSeq` stays valid because sequence numbers only increase within a generation, and a generation change still goes through the poll's `resetFrames()`.
- Server: no change.

### Blast radius
- `resetFrames` callers: line 347 (removed), line 352 (removed), line 358 (kept: run or generation change).
- Test: `tests/agent/test_watch_run.py::test_browser_frame_loop_rejects_late_epoch_and_closes_bitmaps` (a Node harness that slices `PAGE` from `let frameEpoch=` to the `visibilitychange` line). It calls `resetFrames()` directly and is unaffected.
- `test_live_frame_endpoint_sends_source_metadata_and_explicit_unavailable` (expects 503 when no frame exists) is unchanged.

### Edge cases considered
- **Real disconnect or run end.** The last frame stays on screen. The header badge already switches LIVE→IDLE from `/state` (`live = age < 250 ms`). The `fstep` text keeps its last "LIVE / FRAME n / x MS". Acceptable, because the badge is the liveness signal. If wanted later, a one-line "NO SIGNAL" label can be added.
- **Generation change after a resume or `load_state`.** The poll still resets. Between the publisher's new header and the next poll, new-generation frames are skipped (not drawn, not cleared), so the screen freezes for at most 500 ms instead of going black.
- **Run switch in follow mode.** The poll resets as before.
- **Windows file-lock race.** It now only costs one skipped pull.

### Tests
Extend the Node harness in `test_watch_run.py` with a new test in the same style:
1. Draw seq 2 (connected). Then make `fetch` resolve `{ok:false}`.
   - Assert `drawn` is still `[2]` (the stub `clearRect` empties `drawn`), `live.dataset.frameSeq==="2"`, and that the next pull was scheduled via `setTimeout` (not connected).
   - Then resolve seq 3 and assert `drawn` is `[2,3]`.
2. Widen the slice to include the `visibilitychange` line, capturing the listener through `document.addEventListener`. Fire it with hidden=true, then hidden=false.
   - Assert the canvas was never cleared and a pull was started.

### Verification
- Open `python scripts/watch_run.py --run runs/<any live run>` during a run, or alongside the existing stress publisher that reproduced 96/67k unavailable reads.
- Use the browser devtools: `$("live").dataset.frameSeq` must never return to `""` unless the run or generation changes.
- Switch tabs: no black frame.

### Rollback
Revert the single commit.

---

## Remaining gaps (D, E, F): only the parts not fixed by Plans 1 and 2

**Subsumed by Plans 1 and 2:**
- The goal-less part of the furniture loop at 53–63 is covered by W3.
- The "goal reasoned from the wrong room" part of D is covered by `8ef7c3d`, already committed, not by Plans 1/2.

**Not covered by either plan:**
- E's counter, metric and replay tool (W4, W5).
- D verification (W6).
- All of F (W7–W9).

### W4: a blocked or failed act is a failure even when it revealed new cells (E root cause)

`agent/predict.py:12`:
```python
def result(status, effects, novel):
    if novel: return "progress"
    if status in ("blocked", "failed"): return "failed"
```

`agent/loop.py:328` (`_settle`), then line 344 on the "success" branch:
```python
failed = v in ("failed", "no_change")
...
else:
    if intent in self.fails and not str(self.fails[intent].get("why","")).endswith(": cycle"): self.fails.pop(intent)
```
`world.novel` is the number of newly seen cells (`world.py:270`). In the run, 26 blocked or failed acts were labelled `progress`, so their counters were popped. The option row therefore never showed "route blocked N×" and stayed under "Not tried here". The prompt at step 80 shows "explore RIGHT" with no notes after two blocks.

**Change (smallest, at the decision point):**
- `loop.py:328`: change to `failed = v in ("failed", "no_change") or skill.status in ("blocked", "failed")`.
- Same rule in the offline replay, `scripts/check_tracks.py:207`: `if intent and (result in ("failed","no_change") or (row.get("skill") or {}).get("status") in ("blocked","failed")):`.
- The verdict stays `progress`. `Stuck` and the planner evidence are unchanged, and the test `tests/agent/test_predict.py::test_result_prioritizes_novelty_then_failure_then_no_change` still holds. Changing the verdict itself was rejected: it would touch the stuck detector, the eval metrics and that test.

**Effect:**
- `Loop.fails[intent]` counts up.
- `self.cause` and the flags are set.
- The option note `"route blocked N× (last step k)"` appears and the row's state becomes `failed`, so it moves under "Failed here:". All of these are existing mechanisms (`world.py:1001-1022`).
- No new gate: options are never removed.

**Risks:**
- More options marked failed.
- A blocked action with `stage` not in (route, interaction) now writes a `does_nothing` relation on the thing (`loop.py:342`). That is correct for a real failure, but watch `poison_rate`.

**Tests** (`tests/agent/test_loop.py`): `test_blocked_with_novelty_still_counts_as_failure`, a sibling of `test_blocked_exit_without_novelty_reports_failed` at line 502.
- Monkeypatch `loop.world._record_view` to set `novel = 5`.
- Assert `row["verdict"] == "progress"` and `loop.fails[intent]["n"] == 1`.
- Run a second identical step and assert n == 2, with that option's notes containing "2×" and state `"failed"` in `world.options(...)`.

**Verification (0 requests):**
- Use checkpoint 75 and fold recorded failures from steps 76 to each checked step, retaining the last five trace rows and counting repeated blocked/failed intents (variant B in `chatgpt-execute-r2.md`). The `check_tracks --menu` replay does not fold views into seen/tiles, so it cannot emit exploration rows and is not a valid exploration gate.
- Go: the old rule gives state `new` with no notes at steps 80 and 84. The new rule gives state `failed`, with "route blocked 1× (last step 79)" at 80 and "route blocked 4× (last step 82)" at 84.

**Rollback:** revert one commit; no persisted schema change.

### W5: eval metrics (`scripts/eval_run.py`)

**`repeated_mistakes`** (lines 1128–1136; it keys on `frame_hash`, which never repeats in live play). Change to:
```python
key = (row.get("place"), choice.get("intent") or (str(choice.get("verb","")).casefold(), str(choice.get("target","")).casefold()))
bad = row.get("verdict") in ("miss","surprise","failed","no_change") or (row.get("skill") or {}).get("status") in ("blocked","failed")
if choice.get("verb") and key in failed: repeated += 1
if bad: failed.add(key)
elif choice.get("verb"): failed.discard(key)
```
- The existing fixture in `tests/agent/test_eval_run.py` still gives 3.
- On this run it gives 23 (repeats at steps 2, 43, 55, 57–62, 67, 72, 77, 79–82, 84, 85, 113, 114, 117, 119, 144).

**`edges_found`** (around line 1100). Change to `has_ok_edge = row.get("verified_edge") is True or any(...existing places-edges check...)`. The old fixture still passes.
- Expected on this run: `{"0x26->0x25": true, "0x25->0x00": true, "0x00->0x28": false}`. The last one stays false because D happened in this run.

**Tests:** add to the fixture one blocked row with verdict `progress` and the same intent repeated (count goes up), and one row with `verified_edge: true` but no `places.edges` (edge found).

**Verification:** `python scripts/eval_run.py runs/m1_h1h4_live --no-write` prints 23 and the edge dictionary above. Then run it once without `--no-write` to refresh the stale `summary.json`.

**Optional, same commit or skip:** the viewer's edges panel reads `p.edges` too (`watch_run.py:450`). It will stay empty until it reads crossings. Not needed for any fix.

### W6: D verification only (already fixed by `8ef7c3d`)
- No code change.
- **Optional regression test:** copy `runs/m1_h1h4_live/frames/000090_after.png` and `000091.png` (two 160×144 PNGs) into `tests/agent/data/` (tests cannot read `runs/` in worktrees). Assert `observer.cut(a, b)` is True; today `changed=0.569` and `scroll=(0,0)`. The synthetic test from `8ef7c3d` already covers the logic, so this only pins it to real frames.
- **Verification:** `python scripts/check_tracks.py runs/m1_h1h4_live --places` (0 requests). Go: no exception, and the output matches what was recorded at `8ef7c3d`. The real proof is the next live run's `place_mapping["Oak's lab"]` being non-null with purity ≥ 0.9.

### W7: F1, measure the non-model time (no behaviour change)
- Store `before_set.waited` in the `s` dict (`loop.py:238`).
- Add `"settle_frames": {"before": s["before_waited"], "after": after_set.waited}` to the row dict in `_settle` (`loop.py:296`).
- Optional: in `scripts/run_agent.py:228-240`, time the pre-step truth `wait_settled` and the motion GIF save. That time falls outside the row's `ms`.
- Today every step runs four "quiet ≥ 24 frames" waits, each at least 0.4 s at real-time speed: run_agent before the truth read, the loop's before-settle, the skill's own settle, and the after-settle. Some of these are probably duplicates; W7 data decides which.
- Test: assert that a stepped row has integer `settle_frames.before` and `.after` (extend any existing `loop.step()` test).

### W8: F2, compact perceiver JSON (accuracy risk, gated, needs approval)
- In `agent/perceive.py`, change the first line of `PROMPT` from `Return JSON with:` to `Return compact one-line JSON (no code fence, no indentation) with:`.
- Bump `self.prompt_version` in `loop.py:36` (`"e5-perceiver-10"` → `"-11"`) so cached perceptions are invalidated.
- Expected: about a third fewer perceiver output tokens (whitespace and fences are 37% of characters), so roughly −0.8 s per uncached perceiver call.
- **Risks:**
  - The perception change could alter entity recall.
  - **Every recorded perceiver cache entry misses after this**, so the zero-cost offline replays (`eval_act --om`, which needs cached perception) stop working for old runs. Do W8 last.
- **Gate (cloud spend, needs user approval within `scripts/eval_frames.py` `ATTEMPT_CAP=60`):** `python scripts/eval_frames.py` against the stored baseline (`.tmp/ps/baseline.json`).
  - Go only if the pass count is ≥ baseline on every screen type and the median perceiver output tokens fall by at least 25%.
- Rejected:
  - Dropping `placement` (another 12%): it is consumed by the world model; no evidence it is safe.
  - Skipping perception on unchanged frames: already implemented (`Loop._see` hash cache).

### W9: F3, shorter settle (deferred)
Only after W7 shows which waits are duplicates. Candidates:
- drop run_agent's pre-truth settle (this affects only eval truth timing);
- reuse the skill's settled frame instead of the extra after-settle when `skill.settled` is true.

Lowering `quiet` below 24 risks perceiving half-printed text. It would need a recorded-stream replay to prove safe, so it is not proposed now.

---

## Commit order and go/no-go gates

0. **Baseline.** On HEAD (`8ef7c3d`), run `pytest -n auto` and record the count (last recorded: 673 passed, 1 skipped). Run `eval_run.py runs/m1_h1h4_live --no-write` and save the output.
1. **C1 = W1 (viewer).** Go: the suite passes, the new Node tests pass, and the manual tab-switch shows no black frame.
2. **C2 = W2 (T1 on menu/keyboard).** Go: the suite passes, and the checkpoint-25 replay returns a trigger. No-go if any dialogue-screen test now replans.
3. **C3 = W3 (planner follow-up).** Go: the suite passes, with only scripted-reply additions in existing tests (no assertion changes), and the new tests 2–5 pass. No-go if any existing test needs a logic change to pass.
4. **C4 = W4 (failure counter + checkpoint replay, variant B).** Go: the suite passes, and checkpoint 75 options with folded recorded failure history show explore RIGHT as failed with 1× at step 80 and 4× at step 84; the old rule gives new with no notes. No-go if `tests/agent/test_world_golden.py` changes (the golden files must be unchanged).
5. **C5 = W5 (metrics).** Go: the eval prints 23 and the expected edge dictionary, and the old fixture is still 3 with all edges found.
6. **C6 = W7 (instrumentation).** Go: the suite passes.
7. **C7 = W8 (only with user approval of the cloud eval).** Go: `eval_frames` meets the rule above. Otherwise do not merge.
8. **Optional live confirmation** (needs approval): a run of about 150 steps from checkpoint 0. Targets:
   - no `Goal: (none)` after step 19 except within one plan's floor;
   - a rival-naming quest exists before the keyboard;
   - W5's `repeated_mistakes` well below 23;
   - `place_mapping["Oak's lab"]` non-null;
   - no black viewer frames.

Each commit is independently revertible with `git revert <sha>`. None changes `harness.json`, memory schemas or caps. W7 only adds trace fields.

Line-count impact (reported only, no budget): W1 −2/+2, W2 1, W3 about +4, W4 2, W5 about +6, W7 about +3, W8 2, plus tests.

### Critical files for implementation
- `agent/loop.py` (`_plan_trigger` lines 504–509, planner block 150–161, `_settle` failure rule 328, row 296)
- `scripts/watch_run.py` (page script lines 347 and 352)
- `scripts/eval_run.py` (`repeated_mistakes` 1128–1136, `edges_found` 1087–1104)
- `scripts/check_tracks.py` (line 207, replay failure rule)
- `tests/agent/test_loop.py`, `test_watch_run.py`, `test_eval_run.py` (new and extended tests)
