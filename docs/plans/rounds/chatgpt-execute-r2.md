# Prompt for ChatGPT: execute the Avatar harness fix plan (r2)

Paste this prompt alone. The plan is a file in the repo.

---

You are implementing a reviewed plan in the `Avatar` repo (Pokémon Red agent harness, Python, branch `feat/lean-agent`, HEAD `8ef7c3d`). You have repo access and can run commands. The plan is in the repo at `docs/plans/harness-fixes-r2.md` (read it in full before starting; it is untracked, so it is in the working tree, not in git history). It was written and audited by another model; trust its diagnosis, but verify every line number and code excerpt against the real files before editing, because line numbers drift. If the code differs from the plan, stop and report instead of improvising.

## Rules
- Smallest root-cause change, reuse existing code, no new abstractions, no new mechanical gates, no new config. The agent infers goals from game text and acts freely.
- Add no cloud calls beyond what the plan states. Do not change call caps or ledgers.
- Run tests with `pytest -n auto`. Tests cannot read `runs/` inside a worktree, so run anything that reads `runs/m1_h1h4_live` from the main checkout. Never symlink or junction `runs/` or `Rom/` into a worktree.
- One commit per work item, in the plan's order (C1…C7). Each commit message states the work item id, the evidence, and the test added. Do not amend or squash. Do not push.
- After each commit: run the full suite and the plan's go/no-go check for that commit. If a no-go condition triggers, stop, revert that commit with `git revert`, and report. Do not continue past a failed gate.
- Existing test assertions must not change. Only scripted-reply additions to stubs are allowed (plan section W3, blast radius). If an assertion has to change, stop and report.
- Report line counts only; there is no line budget.

## Pre-existing uncommitted work: this is NOT a plan mismatch
`git status` already shows `M scripts/eval_frames.py`, `M scripts/run_agent.py` and `?? scripts/frames_combo.json` before you start. These are the user's own work-in-progress for model-combo evaluation and were approved: `eval_frames.py` has a perceiver/actor split (`--perceiver`, `--actor`, perception cache under `.tmp/ps/percache`), goal injection and `ATTEMPT_CAP = 60`; `run_agent.py` has the raised ledger caps (`TOTAL_CAP, M1_CAP = 5615, 5565`). None of the plan's work items edit these files, so do not stop for them.
- Leave them exactly as they are. Never `git add -A`, `git add .` or `git commit -a`; stage only the files each work item names, by explicit path, so your commits never include them.
- Use them as they are for the W8 gate: run `python scripts/eval_frames.py` (defaults: both roles `gemma4:31b-cloud`). The baseline run uses the existing percache; the after run misses it because the perceiver prompt changed, which is expected. Do not delete `.tmp/ps/percache` or `.tmp/ps/baseline.json`; copy the baseline output aside before the second run.
- Stop-and-report on mismatch applies only to code the plan itself edits (`agent/loop.py`, `agent/perceive.py`, `agent/act.py`, `agent/plan.py`, `scripts/watch_run.py`, `scripts/eval_run.py`, `scripts/check_tracks.py`, and the tests).
- The plan files `docs/plans/harness-fixes-r2.md` and this prompt are untracked; leave them untracked.

## Progress update and corrections (read before continuing)
State on the branch: C1 `432a958` and C2 `8f15d83` are landed; C3 `68374a7` was reverted by `ae66fa0`. Continue from C3; do not redo C1/C2.

**C3 re-land (reviewed by a second model; the diagnosis of the failure was refined):**
- The golden failure was not a consumed scripted reply. The stub returns a malformed plan (no `ops` list) without taking a scripted reply, which yields one `error: ops must be a list` row, and `golden(tmp_path, "act")` pins exactly that single row. W3's follow-up appended a second identical row. Cause: the follow-up fired on a malformed plan.
- Fix: run `git revert ae66fa0` to restore `68374a7` (with its tests), then add one guard so the follow-up only fires when the plan had a non-empty ops list. In the planner block of `agent/loop.py` the condition becomes:
  `if p.ops and self.notebook.focus is None and any(r["result"] != "ok" or r["op"] in ("done", "drop") for r in results):`
  Malformed or empty plans keep today's path (T5 re-arms later). Commit as C3 (message cites the golden and the guard).
- Add a 5th case `"malformed"` to `test_plan_followup_before_act_is_bounded_and_reads_errors`: the first plan reply is `{"goals": [], "why": "x"}`; assert exactly one plan prompt and `row["plan"]["ops"] == [{"op": None, "key": None, "result": "error: ops must be a list"}]`.
- Update the "Malformed JSON" bullet in `docs/plans/harness-fixes-r2.md` (edit the file, leave it untracked): a malformed plan gets no follow-up, because the golden test pins that behaviour and it never happened in the recorded run.
- Cost expectation on the recorded run: 7 follow-ups, minus the T5 plans (45, 115, 143) that become unnecessary, so about 18 planner calls versus 14, not 21. Report your own count with the guard applied.
- Go: full suite green, `tests/agent/test_world_golden.py` and all golden files unchanged. Offline check: load checkpoint 50 with `scripts/eval_act._plan_checkpoint("m1_h1h4_live", 50)`; confirm `notebook.focus is None` and that `notebook.render()` shows the step-48 `done q2` result the follow-up prompt reads.

**W7 golden exclusion is approved by the user.** `golden()` in `tests/agent/test_world_golden.py` (line ~12) drops `{"t","ms","entities","scroll","after"}`; add `"settle_frames"` to that set. This changes the comparison filter only: no assertion and no golden file may change. Put `settle_frames` in the no-act row in `agent/loop.py` as well (around line 361), so the "integer before/after" test holds for every row.

**W4 note:** run `tests/agent/test_world_golden.py` before committing C4; the golden act trace must be unchanged.

**W10 is defined in this prompt only** (it is not in the plan file). Its order is C7, after C6.

## ORDERS round 2 (supersede conflicting text above and in the plan; C1-C3 stand, `b9a0b09` stays reverted)

**1. C4 / W4 redo, variant B (no existing assertion changes).** The assertion at `tests/agent/test_harness_reliability.py:184` encodes the bug (it was edited in the R4 gate to follow a side effect: `fails` got popped); leave it untouched. Variant B counts a blocked/failed execution as a failure only when this intent's most recent row in `self.recent` was also blocked/failed. A single exploratory block that revealed ground is not marked failed; a repeat is. In `agent/loop.py` `_settle` (around line 332) replace the `failed =` line with:
```python
last = next((r for r in reversed(self.recent) if (r.get("choice") or {}).get("intent") == intent), {})
failed = v in ("failed", "no_change") or skill.status in ("blocked", "failed") and last.get("execution") in ("blocked", "failed")  # a repeated blocked intent fails even when it revealed cells
```
Mirror it in `scripts/check_tracks.py` (around line 206), keeping `st["recent"]` like the loop keeps `self.recent`:
```python
intent, result, status = (row.get("choice") or {}).get("intent"), row.get("verdict"), (row.get("skill") or {}).get("status")
last = next((r for r in reversed(st.setdefault("recent", [])) if (r.get("choice") or {}).get("intent") == intent), {})
if intent and (result in ("failed", "no_change") or status in ("blocked", "failed") and last.get("execution") in ("blocked", "failed")):
    f = fails.setdefault(intent, {"n": 0}); f.update(n=f["n"] + 1, why=(row.get("skill") or {}).get("reason") or f"verdict {result}", step=s)
elif intent: fails.pop(intent, None)
st["recent"] = [*st["recent"], row][-5:]
```
Test: reuse the body of the test from `b9a0b09` and assert in this order: first step `verdict == "progress"` and `intent not in loop.fails`; second step still `progress`, `loop.fails[intent]["n"] == 1`, and that option has state `failed` with "1×" in its notes; third step `n == 2`. Do NOT adopt the count-every-block rule; that would need the user to approve amending existing case 3, which is not granted.
Go: suite green, `tests/agent/test_world_golden.py` and goldens unchanged.

**2. W4 replay check: the plan's `--menu` check is invalid; use this instead.** `check_tracks.menu` calls only `world.before` and never folds views, so the seen/tiles stores stay empty, `explore_dirs` returns `{}`, and no explore row can appear at any step (the live prompts did have it). Do not patch the replay. Run this from the main checkout (0 requests, repeatable):
```python
import json; from scripts.eval_act import _plan_checkpoint
rows = {r["step"]: r for r in map(json.loads, open("runs/m1_h1h4_live/trace.jsonl"))}
def fold(upto, new):
    fails, recent = {}, []
    for s in range(76, upto):
        r = rows[s]; k = (r.get("choice") or {}).get("intent")
        last = next((x for x in reversed(recent) if (x.get("choice") or {}).get("intent") == k), {})
        if k and (r["verdict"] in ("failed", "no_change") or new and r["execution"] in ("blocked", "failed") and last.get("execution") in ("blocked", "failed")):
            f = fails.setdefault(k, {"n": 0}); f.update(n=f["n"] + 1, why=r["skill"]["reason"], step=s)
        elif k: fails.pop(k, None)
        recent = [*recent, r][-5:]
    return fails
loop, env = _plan_checkpoint("m1_h1h4_live", 75)
for upto in (80, 84):
    for new in (False, True):
        o = next((o for o in loop.world.options(loop.prev, loop.place, fold(upto, new), upto, inputs=list(env.inputs())) if o["intent_key"] == "p_place_4|approach|explore:p_place_4:right"), None)
        print(upto, "new" if new else "old", o and o["state"], o and o["notes"])
```
Go: old rule gives state `new` and no notes at both steps; new rule at 80 gives `failed` with "route blocked 1× (last step 79)"; at 84 gives `failed` with "route blocked 4× (last step 82)". The plan's "2× at 84" is wrong for any rule; use 4×. If the option is None or numbers differ, report it; do not tune. Correct the W4 gate numbers and note the replay limitation in the untracked plan file.

**3. C5 / W5.** Use the plan's code. Do not add rows to the existing eval fixture (its `count == 3` would change); put the blocked-`progress` repeat and the `verified_edge` case in a new test that reuses the existing `write()` helper. The plan's "23" for `repeated_mistakes` is the reviewer's own count; if your measured number differs, report the measured step list and do not tune.

**4. C6 / W7.** Put `"before_waited": before_set.waited` in the `s` dict (around line 242) and add `"settle_frames": {"before": s["before_waited"], "after": after_set.waited}` to the `_settle` row. The only `_hold_row` call is around line 215: pass `settle_frames={"before": before_set.waited, "after": None}` through `**fields`; add no parameters. Add `"settle_frames"` to the `golden()` exclusion set (user approved). Assert integer before/after on an action row only.

**5. C7 / W10.** `tests/agent/test_act.py:71` pins `len(SYSTEM.split(". ")) == 4`: add the clause inside an existing sentence, never as a new one; for example sentence 3 ends "...choose one option; when the game offers preset or default names or settings, pick one instead of typing your own." Introduce no new ". " sequence. There is no act `prompt_version`: `Model._key` hashes `system`, so cached act replies already miss; do not bump the perceiver version here. Keep the act prompt at or under 2000 characters (`check_tracks --menu` exits 1 above that; last recorded max 1,909). Run the offline naming replay (steps 19-30) before C8. Eval gate change from the W10 section above still applies (accept preset names, require player != rival, keep HAND/BLUE).

**6. C8 / W8.** Run the `eval_frames` baseline first, before editing `perceive.py`, and copy `.tmp/ps/baseline.json` aside. The user approved changing exactly two test literals: `tests/agent/test_loop.py:383` and `tests/agent/test_trace_brief.py:31` from `"e5-perceiver-10"` to `"e5-perceiver-11"` (only the version string; nothing else in those assertions). After C8, `_plan_checkpoint` restores drop their cached perception (`cached_version` no longer matches), so do every offline check (order 2, the W10 naming replay, planner call counts) BEFORE C8.

**7. Then:** the W6 `--places` verification, the approved live run of about 150 steps from a clean boot, and `eval_run.py` on the new run. Report numbers; do not tune anything to hit targets.

## Manual and GUI checks never block you
Do not use Computer Use or any desktop/browser automation. Any check in the plan that needs a human or a GUI (the W1 tab-switch check, the viewer "no black frames" check, devtools inspection) is replaced by the automated Node tests the plan specifies plus this scripted check: run a `FramePublisher` and `read_live_frame` stress loop like the one in the plan's evidence (about 8 s at 60 fps) to confirm the server side still returns valid frames. Record the manual items as "unverified: user to check visually" in your report and keep going. A gate is only a stop condition if it is automated and fails.
- If C1 is already done in the working tree (47 lines added, 2 removed, viewer tests passing, suite 673 passed / 1 skipped), review the diff against the plan, then commit it as C1 with an explicit path list (`scripts/watch_run.py` and its test file only) and continue with C2. Do not redo it.

## Decisions already made by the user (these override the plan where they differ)
1. **Cloud calls are approved.** W8 (compact perceiver JSON) is allowed to run its `scripts/eval_frames.py` gate within the script's existing `ATTEMPT_CAP=60`. Run it once for the baseline and once after the change. Merge W8 only if it meets the plan's go rule; otherwise revert it and say so.
2. **Default names (new work item W10, replaces the plan's N1).** When a game offers preset or default choices, the agent should pick one instead of entering custom text.
   - Add one short, game-agnostic sentence to the act model's system prompt in `agent/act.py`, for example: "When the game offers preset or default choices for a name or setting, pick one of them instead of entering your own text." Do not mention Pokémon, specific names, or buttons. Do not add a gate, filter or option-removal.
   - Add the same idea to the planner's `SYSTEM` in `agent/plan.py` only if the stale-goal evidence shows the planner steering toward "enter a name"; otherwise leave the planner alone.
   - Bump the act `prompt_version` (or the equivalent cache key) so cached act replies do not mask the change, as the plan does for the perceiver. Check how `prompt_version` is used before touching it.
   - Eval gate in `scripts/eval_run.py` (lines ~1173–1174 hard-code `player == "hand"` and `rival == "blue"`): change the match to accept the game's preset names. Pokémon Red presets are player RED / ASH / JACK and rival BLUE / GARY / JOHN. Keep HAND and BLUE accepted so old runs still evaluate. Add a requirement that player and rival names differ (both RED was a real failure in the last run). Update `tests/agent/test_eval_run.py` fixtures by adding cases, not by changing existing assertions.
   - Test: an act-prompt test asserting the sentence is present; an eval test with RED/BLUE passing, GARY/JOHN passing, RED/RED failing, HAND/BLUE still passing.
   - Verify offline first: replay the naming screens from `runs/m1_h1h4_live` (checkpoint 0 or 25, steps 19–30) to see the new act prompt. Cloud use for this item is limited to the final live confirmation run.
3. Everything else in the plan stays as written: W1–W5, W6 (verification only), W7, W8, W9 deferred.

## Order of work
C1 W1 viewer · C2 W2 T1 on menu/keyboard · C3 W3 planner follow-up · C4 W4 failure counter (+ `check_tracks.py`) · C5 W5 metrics · C6 W7 settle instrumentation · C7 W10 default names · C8 W8 compact perceiver JSON (with the paid `eval_frames` gate, last because it invalidates cached perception) · then W6 verification.

## Final live confirmation (approved)
After C8, run one live run of about 150 steps from a clean boot with `scripts/run_agent.py`. First check the live viewer is up (`curl -s http://127.0.0.1:8765/state > /dev/null`); if not, start `python scripts/watch_run.py --no-browser` in the background. Success targets from the plan: no `Goal: (none)` after step 19 except inside one plan's floor; a rival-naming quest exists before the keyboard; both names are presets and differ; corrected `repeated_mistakes` well below 23; `place_mapping["Oak's lab"]` non-null; no black viewer frames. Then run `python scripts/eval_run.py runs/<new run>` to refresh its summary. Report the numbers; do not tune anything to hit them.

## What to report back (in this order)
1. A table: commit sha, work item, tests added, suite result (`passed/failed/skipped`), gate result (go / no-go and the measured evidence).
2. Any place the code differed from the plan, and what you did.
3. Planner call count before vs after on the replayed run data, and the `eval_frames` baseline vs after numbers for W8.
4. Live-run results against each success target, with the run folder name.
5. Anything you decided not to do and why. No other prose.
