# Round r24 — fields: grounded, owned HUD readings (design + build brief)

Base: `feat/lean-agent` at `4770721` (meter guard). Status: **approved by the user 2026-10-27**, executed by the ChatGPT orchestrator (§6). Direction work starts only after this round.

## 1. Problem (measured, LV21–LV23, zero requests)
- **Text has no positions.** The perceiver returns `text` as an unordered bag, so nothing can pair a label with its value. Requiring the label beside the digits broke 14 of 52 correct HP readings, because text order is not screen order.
- **Unprinted quantities are invented.** An enemy HP bar has no digits, and the perceiver guessed 22 values (e.g. "Pidgey HP 10/100"). The r24-prep guard (`4770721`) now drops any meter whose digits are not in the screen text. That stops the guesses, but bars are then never read at all.
- **Counters are mislabelled.** 8 move-PP counters were reported as HP (e.g. "Player Pokemon HP 29/35"). Their digits are printed, so the guard keeps them.
- **Meters carry no anchor.** A meter is a name plus a value: no box, no kind (bar or number), no owner separate from its label.

What already works: printed player HP was read correctly in 52 of 52 live readings, and the agent used a Potion at 1/21 because of it.

## 2. Offline evidence for the design (evaluator-only RAM, zero requests)
- **Bars can be read from pixels exactly.**
  - A generic reader takes a loose box, finds the bar outline (the longest single-colour horizontal run) and counts the filled run under it.
  - It matched RAM HP within one pixel on **61/61** checkpoint frames where a bar was drawn (LV21–LV23 battles; Pokémon draws 48 px = floor(48·hp/max)).
  - Every miss (21) was a frame with no bar drawn yet (intro, fainted enemy) or the move-menu layout that moves and partly covers the player panel. So boxes must come from the current frame's perception, never from a fixed layout.
  - Script: scratchpad `bars2.py`, to be copied to `.tmp/r24/`.
- **Digit crops repeat.** 181 battle frames held 17 distinct player-HP digit crops, so a crop→value cache would hit 91% of the time. That only pays off once fields are read on frames that are not perceived (stage 2, below).
- **Skipping perception would not pay here.** Of 177 consecutive battle frame pairs, 0 changed only inside the HP panels and 40 (23%) only inside panels plus the text box. Perception-skipping (stage 2) saves little in Pokémon, so it is deferred.

## 3. Design (stage 1)
A **field** is one on-screen readout: `{owner, label, kind, value?, box}`.
- **Perception reports where and what. The harness measures what it can.**
  - **F1 Schema** (`agent/perceive.py`): `meters` (r23, one round old) is replaced by optional `fields`. The prompt sentence (game-neutral, replacing `METERS_SENTENCE`):
    > `- "fields": every on-screen readout that measures something (a bar or gauge, a "value/max" or single number, a counter), as {"owner": the printed name it belongs to or null, "label": its printed label or a two-word description, "kind": "bar" or "number", "value": the digits exactly as printed (numbers only; omit for bars), "box": [x0, y0, x1, y1] tightly around the readout on the 0-1000 grid}; [] when none.`
  - `normalize` keeps a field only if its kind is valid and its box is well-formed (converted to fractions).
    - A `number` field keeps its value only if the digits are in the screen text: the existing `_printed` guard, applied to the value parsed as `v` or `v/max`.
    - A `bar` field never keeps a model value.
- **F2 Bar reader** (`agent/observer.py`, beside the other pixel functions): `bar_fill(image, box) -> float | None`.
  - Pad the box by one tile (8 px when `tile_px` is unknown).
  - Find the outline: the longest single-colour horizontal run of at least 16 px that differs from the box's corner colour.
  - Fill = the leading pixels of the row beneath that differ from that row's empty end, divided by the run length. Full returns 1.0; empty returns 0.0; no outline returns None.
  - `# ponytail: horizontal left-to-right single-colour bars; vertical, segmented (hearts) and gradient bars return None until a game needs them.`
- **F3 Readings → status** (`agent/loop.py`, where r23's `notebook.status(per.get("meters"), …)` is called on a fresh perception):
  - For each field: number → `{name: "<owner> <label>", value, max}`; bar → `bar_fill(frame, box)`, giving `{name, value: round(100·f), max: 100, unit: "%"}`. A bar returning None is skipped.
  - Names are owner plus label as printed, so the PP counter can no longer claim "HP" unless the screen labels it so.
- **F4 Notebook** (`agent/notebook.py`): a status row may carry `unit`, and `%` renders as `Pidgey HP 56%`. Everything else is unchanged: latest per name, at most 4 shown, most recent first, step-tagged.
  - `# ponytail: readings from an earlier screen (a finished battle's enemy) stay visible with their step; add expiry when it misleads.`
- **Modularity:** inert when no fields are perceived (renders byte-identical). No game words, no RAM. No new module, class, store or config key. The bar reader is one function next to `changed`/`scroll`.
- **Bump** `Loop.prompt_version`.

## 4. Deferred, with measured value
- **Stage 2, tracking between perceptions:** keep field boxes per screen layout, re-read bars from pixels and digits from a crop cache on frames that are not perceived (walk frames, after-frames), and skip a full perception when only field boxes changed.
  - Measured: 91% crop-cache hits, but only 0–23% of battle steps change only inside fields.
  - Build it when a game has a continuously changing HUD (action games) or when an event trigger needs per-frame values.
- **Stage 3, learned font:** on tile-font games, learn digit glyphs from cached readings so numbers are read with no model call at all.
- **Merge** the r12 `World._hud` crop-change flag into fields once stage 2 exists.

## 5. Work and gates
| Step | Work | Gate |
|---|---|---|
| W1 | F1–F4 + tests | Suite green. Tests: bar_fill on a synthetic bar (full / empty / 27/48 / no outline / loose box); normalize keeps valid fields and drops bar values, unprinted numbers and bad boxes; status `%` render; inertness (no fields gives byte-identical renders). |
| G-off | Rerun `bars2.py` against `bar_fill` itself | 61/61 drawn bars within 1 px of RAM. |
| G-paid | Re-perceive 15 recorded frames with the frozen F1 prompt: 8 battle-base, 3 move-menu (PP), 2 dialogue-only, 2 world. At most 20 requests. | (a) ≥ 13/15 frames: every drawn HP bar is reported as a `bar` whose box gives `bar_fill` within 1 px of RAM; (b) printed HP numbers exact; (c) 0 PP counters named HP; (d) 0 fields on dialogue/world frames without a HUD. |
| LV24f | Live battle check: copy LV23, `--resume --from-step 215 --steps 15 --max-calls 45` | Status lines carry enemy % and player numbers matching frames, with no invented values. |

**Budget:** `runs/e3_budget.json` total is 11223. The r23 H1 measurement's 16 requests were charged outside that file, so the effective ledger is 11239. The user raised the round ceiling to **11600**. Caps: G-paid ≤ 20, LV24f ≤ 45. Nothing else is spent.
**Stop and ask:** G-paid fails; any non-test-fixture assertion change outside `meters`→`fields`; golden data changes; T18F/G/H/I, O2 or O3 change; the ledger would pass 11600.

### Pre-approved test changes
- r23 meter assertions become field assertions:
  - `test_meters_are_optional_and_normalized`;
  - `test_meters_need_their_digits_on_screen`;
  - the HP meter fixture in `test_twenty_steps_have_contract_keys_and_reuse`;
  - `test_captured_act_prompt_contains_status_meter`;
  - notebook status tests (a `unit` is added).
- Prompt-version assertions `e5-perceiver-12` → `e5-perceiver-13`.
- List every change before/after. Any other failing assertion: stop, revert, report. Never set `W0_REGEN`.

### Contract amendments (approved; the orchestrator commits them in wave 0)
- `## perception`: replace the r23 meters line with:
  `- r24: perception may carry "fields": [{owner, label, kind: bar|number, value?, box}]; a number keeps its value only when its digits are in the screen text; a bar's value is never taken from the model.`
- `## notebook and act`: after the r23 status line, add:
  `- r24: status readings come from fields; a bar's reading is its pixel fill (bar_fill) as a percentage with unit "%".`
- `## observer`, at the end:
  `- r24: bar_fill(image, box) -> fraction | None: the longest single-colour horizontal run in the padded box is the outline; fill is the leading run under it.`

## 6. Execution (ChatGPT orchestrator; one gpt-6-luna HIGH worker)
1. **Wave 0:**
   - `git status` must show only the user's five files (`scripts/eval_frames.py`, `scripts/watch_run.py`, `tests/agent/test_watch_run.py`, `baseline_eval.txt`, `scripts/frames_combo.json`) plus this brief, `docs/plans/prompt-r24.md` and `.tmp/`. HEAD is `4770721`.
   - Apply the contract amendments. Commit them with this brief and the prompt as `docs: r24 fields plan and approved contract amendment`.
   - Run the suite (expect 959 passed, 1 skipped). Record the ledger.
2. **Wave 1:**
   - Worker F, in `C:\Users\Abj1\Documents\GitHub\Avatar-wt\r24F` (`-b r24/F`), builds W1. It is given the F block below.
   - The orchestrator reviews the diff: F1–F4 only; no new module, class or store; inert without fields.
   - Run the suite and G-off (`.tmp/r24/bars2.py`, edited only to call `agent.observer.bar_fill`).
   - Allow one repair round with the exact failing output. Merge `--no-ff`.
3. **Wave 2 (orchestrator only):**
   - Freeze the G-paid prompt (the merged perceiver prompt), frame list and scorer in `.tmp/r24/g/`, then run G-paid within its cap. The scorer uses evaluator-only RAM HP from the nearest battle checkpoint state, plus manual frame truth where no checkpoint matches, with reasons recorded.
   - If G-paid passes: full suite, then T18F/G/H/I, O2, O3 and golden checks, then LV24f once (viewer up).
4. **Report:** `.tmp/r24/report.md` covers:
   - commits and net lines;
   - test changes before/after;
   - the G-off and G-paid tables;
   - LV24f status lines against frames;
   - deviations.

   Update `docs/plans/handoff-notes.md` (under 80 lines) and commit `docs: r24 result`. Never push.

### F block (paste to the worker)
```
You are a gpt-6-luna HIGH worker on Avatar round r24, WP F (fields: grounded, owned HUD readings). Worktree: <WORKTREE> (branch r24/F from <BASE>). Main checkout: C:\Users\Abj1\Documents\GitHub\Avatar.
Read first: CLAUDE.md; docs/plans/fields-r24.md (all); .tmp/r24/bars2.py (the proven bar algorithm); agent/perceive.py (PROMPT, METERS_SENTENCE, SCHEMA, normalize, _printed); agent/observer.py (changed, scroll); agent/loop.py (prompt_version ~36, the notebook.status call after _see ~123); agent/notebook.py (status, render, render_focus); tests/agent/test_perceive.py, test_notebook.py, test_loop.py (meter tests).
Build exactly F1-F4. Replace meters with fields (delete METERS_SENTENCE and the meters schema/normalize code). Bar values are never taken from the model. No new module, class, store, config key, gate or filter; no game words; no RAM. One ponytail comment per known ceiling (bar shapes; stale readings).
Pre-approved test changes are exactly §5 "Pre-approved test changes". Any other failing assertion: stop, revert uncommitted edits, report. Never set W0_REGEN.
Rules: [paste docs/plans/execute-r18-fix.md §6 rules 1-9 verbatim, scratch .tmp\r24\F\]
Done when: suite green; the W1 tests listed in §5 exist and pass; .tmp/r24/bars2.py (pointed at agent.observer.bar_fill) gives 61/61; one commit "F fields: grounded owned HUD readings, bars read from pixels"; report the diff per file with net lines, tests changed (before/after) and added, and the suite line.
```
