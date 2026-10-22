# Executor prompt addendum: round r7, resume after the V2 stop

Continue `docs/plans/chatgpt-execute-r7.md` / `docs/plans/harness-r7.md` from HEAD `537632d`. Everything in r7 still holds except what this note changes. Where they differ, this note wins.

## Done since your report (by Claude)
- **`55fc942`: `--om act-size` fixed.**
  - `_PlanCheckpointEnv` has no-op `press` and `tick`, which the tracker needs.
  - `_plan_checkpoint` builds the Loop with the checkpoint's modes (r6 was saved with `decide=True`).
  - Baseline at r6/140: `act prompt chars=1435`, 10 options, `requests=0`. The measure calls `act()` directly, while the live decide prompt at 141 was 1687 chars. Use it only to compare before and after.
- **`aea199a`: V3.** `_named_things` narrows a tie across places to the current place's thing, so `ref()` picks it and `ties()` is `[]`. Ties within one place stay ambiguous. Test: `test_name_shared_across_places_resolves_to_the_one_here`. Skip V3.
- **`537632d`: SD.** `scripts/eval_entities.py <run> [--from S --to S]` plus one test. Treat the baseline below as given; re-run only on LV.
  - **Metric 6 deviates:** the trace stores only the chosen option, not the offered list, so "option label churn" counts changes of the chosen thing-option label on unchanged-position steps.

| metric | r6 full | r6 116–150 | r3b full |
|---|---|---|---|
| perceived count, static steps (mean / min / max, n) | 11.18 / 4 / 22, 11 | 13.57 / 8 / 22, 7 | 11.00 / 4 / 21, 19 |
| name flips / kind flips | 0 / 0 | 0 / 0 | 0 / 0 |
| ids with kind `player` | 1 | 1 | 3 |
| false gone / static gone at end | 1 / 10 | 1 / 5 | 7 / 5 |
| static tile moves | 136 | 55 | 231 |
| chosen-label churn (changes / thing steps) | 2 / 5 | 2 / 5 | 4 / 10 |

## V2: deferred (user decision, option C)
Case 11 is out of `scripts/points.json`, because the r6 checkpoints cannot exercise V1:
- checkpoint 140's harness already lists OAK as `worked, talked step 138`;
- from 135, the replay's option grouping differs at 137, so it reads INCONCLUSIVE;
- 144 from 140 misaligns the perceiver rows, which are keyed by (step, role), around the dialogue.

**New last step, after LV:** cut case 11 from `runs/m1_r7_resume115`'s own checkpoints.
- Pick the first world step after a repeated talk to OAK, with a checkpoint before that talk.
- Use the plan's V2 checks: `section` failed, `note_re` same reply, wrong `talk to OAK`, right `display case|ball|sphere|table`. Adjust `label_re` to that run's names.
- Dry `--om points --steps <n>` must PASS. Then commit `V2 Regression case 11 from the r7 LV run`.
- If LV never repeats a talk to OAK, report that; then there is no commit.

## G3: drafted; assertion change APPROVED by the user (2026-10-21)
**Approved assertion change (in addition to r7's two):**
- update the 4 parametrizations of `test_render_focus_labels_target_and_limits_cited_text` to expect the newest cited row whole, not cut at 160;
- in the new test `test_render_focus_shows_newest_cited_text_whole_and_cuts_older`, use a row of about 300 characters so the block fits in 600, and keep the older row's 160 cut.

Finish the draft with exactly these changes, run the suite, and commit `G3 Act goal shows the newest cited text whole`. Any other failing assertion is still a stop.

Background on why it was blocked:
An uncommitted G3 draft is in `agent/notebook.py` and `tests/agent/test_notebook.py`. Its rule: show the newest cited row whole if the focus block stays within 600 characters, else keep the 160 cut. Two conflicts:
1. The existing `test_render_focus_labels_target_and_limits_cited_text` (4 parametrizations) asserts that a single cited row is cut at 160. G3 makes that newest row whole, so these assertions must change, and r7 did not approve that change.
2. The plan's G3 test (a 500-character row whole *and* an older row at 160) cannot fit within 600 characters. The draft's new test fails for that reason.


## Working tree
- The G3 draft above is uncommitted.
- The user's own changes to `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json` and `baseline_eval.txt` stay untouched.

## Order from here
SB → SA → SC → G3 (approved, finish the draft) → GM → G1 if GM is a go → A3 dry on the 10 cases → LV → SD on LV → V2 from LV.

Re-run the suite once before SB with the G3 draft stashed or set aside, and use that count as the baseline (722 expected: 721 + V3 + SD). Ledger: 6570 / 7854 unchanged.

In the report table, show V3 `aea199a`, SD `537632d` and the act-size fix `55fc942` as done by Claude, and add a row "V2 (from LV)".
