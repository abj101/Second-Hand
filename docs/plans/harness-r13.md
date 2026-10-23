# Avatar harness round r13: honest targets, honest names, exploration facts

**Summary**
1. **Source.** `docs/plans/diag-r12-lab.md` (N1a–N4a) and `docs/plans/diag-r12-explore.md` (I1a, I2a, I3a, X1a, X2b). The user picked all of them on 2026-10-22.
2. **Targets (N1a, N2a):** the act goal states only true closes. The planner map keeps this place's legend, so every letter it draws can be named.
3. **Names (I1a–I3a, N3a):**
   - a name the planner read in game text renders as "X, maybe NAME" until the game itself names the thing;
   - a reply with a `NAME:` speaker after talking to a thing confirms that thing's name;
   - places can be named too.
4. **Exploration (X1a, X2b):**
   - an actor whose remembered tile is in view, with nobody perceived there, is `gone`;
   - explore rows and the planner map state how much is unseen in each direction.
5. **Cuts (CUT):** the dead `talked()` close path, `Notebook._label`, a write-only loop line, a duplicated speaker regex and a triple-computed unexplored count.
6. **Asks:** 0 for every part and gate. Then LV13, a clean boot of 250 steps at ≤ 700 asks. The ledger is 7854 / 17854; the caps were raised in `3533db0`.

**Baseline:** HEAD `3533db0` on `feat/lean-agent`; the suite is 778 passed, 1 skipped (the orchestrator re-checks this).

**Principles (unchanged):**
- The system states facts and legal options; the act model decides. There are no new gates or hidden options; facts go into labels and notes.
- No game words in `agent/` code or prompts.
- Reduce churn: edit in place, reuse the helpers named below, and add no new modules. Every cut needs a grep showing zero readers first.

---

## Parts

| id | title | lane | files (regions) | gate |
|---|---|---|---|---|
| N1 | The act goal states only true closes | B | `agent/notebook.py` (`render_focus` 308-315), `agent/plan.py` (SYSTEM line 10) | suite |
| CUT1 | Remove the dead talk-close path | B | `agent/notebook.py` (`talked` 171-182, `_heard_closed` 38/180/188, `_label` 67-71), `tests/agent/test_notebook.py` | suite |
| T13 | Replay checks on prompt text | B | `scripts/eval_act.py` (the points checker near 840-860), `scripts/points_r13.json` (new) | suite; the cases load |
| N2 | This place's legend comes first in the planner map | C | `agent/world.py` (`view` 995-1023) | suite |
| X1 | A remembered actor that is not where memory says is `gone`; off-screen actors say when they were last seen | C | `agent/world.py` (`_follow` 189-196, the remembered-rows block 1176-1194) | suite |
| X2 | Explore rows and the planner map state the unseen ground per direction | C | `agent/world.py` (`explore_dirs` 1220-1235, the explore label 1196-1199, the `Here:` line 858-862, the count sites 548/977) | suite |
| CUT2 | Remove a write-only loop line | C | `agent/loop.py:81` | suite |
| I1 | A guessed name reads as a guess | A | `agent/world.py` (`label` 762-771, `place_label`, the view's `called` sites 910-911, 955-958, 987-993) | suite |
| I2 | A speaker line after talking confirms a name; one speaker regex | A | `agent/loop.py` (`_settle` 331-336, `_flush_conversation` 466-477) | suite |
| N3 | The `name` op names places too | A | `agent/notebook.py` (name op 120-127), `agent/loop.py:46`, `agent/memory.py` (`call` 187-193) | suite |
| I3 | The focus target shows its name's provenance | A | test only (follows from I1 through `render_focus` → `World.label`) | suite |
| G13 | Gates: suite, golden, the T13 replay proof | orchestrator | `.tmp/r13/g/` | all pass |
| LV13 | Clean-boot live run | orchestrator | `runs/m1_r13_boot` | report |

**Lanes and waves:**
- Wave 1 runs lanes B and C in parallel. They touch no common file regions: B owns `notebook.py` (focus and cuts), `plan.py` and `eval_act.py`; C owns `world.py` (view, explore, `_follow`, remembered rows) and `loop.py:81`.
- Wave 2 is lane A, branched after both are merged, because it edits `World.view`'s `called` sites (C's file) and `notebook.py`'s name op (B's file).
- Lane B owns all of `plan.py` (A's prompt sentence is written by B, see N3).

---

## Item specs

### N1: true closes (N1a)
- `render_focus` (`notebook.py:312-315`):
  - an exit target keeps "(done when: you go through X)";
  - a thing with `leads_to`, and a place, keep "(done when: you enter X)";
  - a plain thing target changes from "(done when: X speaks to you)" to "(target: X; close this quest with done when it is achieved)".
- `plan.py:10` becomes, in this exact text: "Give a quest a target when it means being in a place, going through an exit, or dealing with something the map names (someone to talk to or a thing to check). A place or exit target closes by itself when you arrive; any other target stays open until you close it with done."
- **Pre-approved assertion change:** `test_notebook.py:213` and its `done_when` assert at `:230` (the `"Mira speaks to you"` row) change to the new wording.
- **New test:** a plain thing target renders no "speaks to you" and contains "close this quest with done".

### CUT1: dead talk-close path
- Delete `Notebook.talked`, `_heard_closed` (its init at 38, its append at 180 and its use in `observe` at 188; `observe` keeps its arrival closes) and `Notebook._label`. There are no production callers; check first with `grep -rn "talked(\|_heard_closed\|_label(" agent scripts`.
- **Pre-approved:**
  - delete `test_talked_closes_targeted_thing_quest` (`test_notebook.py:353`) and `test_talked_does_not_close_for_reply_before_retarget` (`:368`);
  - in `test_closed_thing_quest_shows_current_label_and_who_said` (`:337`), replace the `nb.talked(39, …)` and `observe` close with `nb.apply_ops([{"op": "done", "key": "q", "why": [...]}], 39)`, keeping its label assertions.

### T13: replay checks on prompt text
- The points checker (`eval_act.py` near `_point_entries`, `:840-860`) gains `text_absent` and `text_re` entries: lists of regexes matched against the rendered act focus line plus the planner `World.view` of that replayed step.
  - `text_absent` passes when no regex matches; `text_re` passes when every regex matches.
  - Reuse the existing row tuple format.
- `scripts/points_r13.json` (new), all on `runs/m1_r12_boot`. If a step cannot be replayed, use the nearest step with the same situation and say so in the report.

  | step from checkpoint | situation | checks |
  |---|---|---|
  | 56 from 55 | guessed name | `absent` label `^talk to PROF\. OAK,`; `right` label `maybe PROF\. OAK` |
  | 101 from 100 | stale actor | `absent` label `not on screen, here$` |
  | 107 from 105 | stale actor | same as 101 |
  | 111 from 110 | explore facts | `right` tool `explore` label `unseen cells` |
  | 179 from 175 | place name | `text_absent` `speaks to you` |
  | 196 from 195 | legend | `text_absent` `speaks to you`; `text_re` `Things here:.*(ball\|sphere\|item)` (match the legend's actual wording at that step: read it first, and write the regex from the recorded memory, not from guesses) |

- The cases must FAIL on the wave-1 base, before the lanes. Record that as the "before" column.

### N2: legend first (N2a)
- In `World.view` (`world.py:995-1023`), write in this order:
  1. the map grid;
  2. this place's "Things here" legend and detail;
  3. other places' headers;
  4. other places' things;
  5. flags.
- A legend letter is drawn on the grid only if its legend line fits the budget. When it does not fit, the cell shows the plain glyph.
- `self.legend` stays "only shown lines", so every drawn letter resolves.
- The budget (`VIEW_TOKENS`) is unchanged.
- **New test:** a view with many other places and many things keeps every drawn letter in the legend, and `len(view) <= VIEW_TOKENS*4`.
- **Pre-approved:** order-only assertions in `test_world.py:759` (`…all_place_exits_before_any_other_place_things`) and `:782`, if they assert that other places come before "Things here". Report each change before and after.

### X1: stale actors (X1a)
- In `_follow` (`world.py:189-196`), under the same guard (a world screen, with no text or dialogue box):
  - take every live entity row of the current place where `_actor` is true, status is not `gone`, and the tile is inside `self._view_rect(anchor, size)` (the existing helper);
  - if its id is not among this frame's perceived ids and no live track holds it, upsert `status: "gone"`. This is the same status a lost track gets. The player's own tile is inside the rect, so the r12 `here` rows end.
  - Static things are not touched: the perceiver misses static things often, and they do not move.
  - `_follow` needs the anchor and size. Pass them from both of its callers:
    - `before` (`world.py:166`), where the anchor is `pa`;
    - `observe_after` (`world.py:485`), where the anchor is `aa`.
    With no anchor, skip the check.
- In the remembered off-screen rows (`world.py:1191-1192`), an actor row's label gains ", last seen N steps ago", with N = current step − `row["last"]`.
- **New tests:**
  - an actor remembered at the player's tile, with the frame showing nobody, ends `gone` and gets no "go to" row;
  - the same while a text box is up does not mark it;
  - a static thing in view and unperceived stays;
  - the label carries "last seen".

### X2: exploration facts (X2b)
- Add one helper next to `open_cells`: `unexplored(mem, pid)`, which returns the set of candidate cells (`open_cells`, plus the target cell of each `unseen_edges` edge). It replaces the three inline `len(unseen_edges)+len(open_cells)` sites (`world.py:548`, `:860`, `:977`) and the candidate build in `explore_dirs:1223-1224`.
  - Counts change slightly, because a cell counted twice becomes one. That is acceptable; report any assertion it moves (none expected).
- `explore_dirs` adds `"unseen": n` per direction: the number of candidate cells whose direction by the existing axis rule is `d`.
- The label (`world.py:1197`) becomes `f"explore {d}, {steps} steps, {unseen} unseen cells that way"`. Here `steps` is the route length *without* the half-screen overshoot. The overshoot stays in `dirs`.
- The planner `Here:` line (`:862`) appends `; unseen by direction: UP n, DOWN n, LEFT n, RIGHT n`, listing only the non-zero directions. It uses the same helper, with the axis rule computed from `self.pos`.
- **Pre-approved:**
  - `test_options.py:399`, the explore labels, change to the new format. Their mocked `explore_dirs` gains `"unseen"`; `.get("unseen", 0)` in the label keeps old mocks working.
  - `scripts/points.json:57-66`, the `label_re` for `m1_r6_live_150:41`, becomes `^explore RIGHT, \d+ steps, \d+ unseen cells that way$`.
- **New tests:**
  - per-direction counts on a small room with open cells on two sides;
  - the `Here:` line lists them.

### CUT2
- Delete `agent/loop.py:81` (`self.world._cut_arrival = self.world._lone = None`). Both names are written there and read nowhere; grep first.

### I1: guesses read as guesses (I1a)
- Add one helper in `world.py`: `_shown(row, base)`. It returns `row["called"]` when that name is confirmed, meaning `called_src` is missing or its tier is `"said"`. It returns `f"{base}, maybe {row['called']}"` when the tier is `"text"`. It returns `base` when there is no `called`.
- Use it in `World.label`, replacing the bare return at 765:
  - for things, `base` is the perceived name with its existing "(or: …)" logic;
  - for places, `place_label` uses it with `base` = the place's own name.
- Also use it at the view's direct `called` reads (910-911, 955-958, 987-993), so the planner sees the same provenance.
- `World.history` is **unchanged** (`test_world.py:284` asserts its exact dict).
- Resolution is **unchanged**: `resolve` and `matches` (`world.py:631`, `694`, `715-731`) keep matching on the raw `called`, so "PROF. OAK" in a planner op still resolves.
- **New tests:**
  - a text-tier name renders "NPC, maybe PROF" in the option label and in the view;
  - a said-tier name, and a `called` with no `called_src`, render bare;
  - a planner op naming "PROF" still resolves.

### I2: speaker confirmation (I2a)
- Move the `said` claim from `_settle` (`loop.py:333-336`) to `_flush_conversation` (`:466-477`). That function already parses the speaker over the joined pages and knows `conversation["who"]`.
- When `speaker` is set, and `who` is a `thing:` whose entity is an actor (`self.world._actor`), call `self.mem.call(ident, speaker, step)` (default tier `said`).
- Delete the duplicate regex at `:334-336`, and keep `conversation["who"]` set at `:332`. A reply with no speaker confirms nothing.
- **Test:** the existing said-tier tests must pass. If one asserts the claim lands at the `_settle` step rather than at flush, it is **pre-approved** to move to the flush step; report it before and after.
- **New tests:**
  - a two-page reply "PROF: … / …" after talking to an actor confirms "PROF";
  - a reply with no speaker leaves a text-tier name as a guess.

### N3: name places (N3a)
- The name op (`notebook.py:120-127`) accepts `place:` refs as well as `thing:` refs. The error text becomes `"{key}" names no known thing or place`.
- `Memory.call` gets `store="entities"`; the places store works the same way, with `called` and `called_src`.
- The `name` callable at `loop.py:46` dispatches on the ref kind: `thing` → `mem.call(ident, …, tier="text")`, and `place` → `mem.call(ident, …, tier="text", store="places")`.
- Resolution by a place's called name: add `called` to the names that place resolution matches. Find it with `codegraph explore "World.resolve place_aliases"`; it is a one-term addition next to `place_aliases`.
- The label shows it through I1's `_shown` ("house, maybe LAB").
- `plan.py`'s name sentence (lane B writes it, below) covers both.
- **Pre-approved:** `test_notebook.py:448-453` (`test_name_op_names_a_thing_from_cited_text`), only for its error-message string.
- **New tests:**
  - naming a place resolves by the new name and renders "…, maybe …";
  - an unknown ref still errors.

**`plan.py` name sentence (written by lane B in wave 1, because B owns `plan.py`):** "when game text names something or somewhere you can see on the map, you may name it with op name, citing the text; such a name stays a guess (shown 'maybe') until the game itself names it when you talk to it."

### I3
- Test only, in lane A. A quest targeting a text-tier thing renders its focus with "maybe NAME"; this comes from `render_focus` calling `World.label`.

---

## G13: gates (orchestrator, 0 asks)
1. Full suite after each merge, with `-n auto`.
2. **Golden.** If `tests/agent/test_world_golden.py` fails after lane C or A, regenerate with `W0_REGEN=1`. Accept the diff only if it changes:
   - explore labels;
   - "last seen" notes;
   - `maybe` labels;
   - fewer or more `gone` remembered rows.
   Commit as `X2g` or `I1g`. Any other diff class: stop.
3. **T13 replay.** Run `eval_act --om points --points scripts/points_r13.json` (dry). Every case must PASS. Save the output to `.tmp/r13/g/points.txt`, with the before (wave-1 base) and after columns.
4. The r12 cases still behave as before: run `eval_act --om points --points scripts/points_r12.json` and compare with r12's result.

## LV13: live run (orchestrator)
- Check that the viewer is up, then run:
  ```
  .venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r13_boot --steps 250 --max-calls 700
  ```
  Before running, check the exact flag names with `--help` and the r12 command in `.tmp/r12/report.md`.
- Run it once. No resume or retry without asking the user.
- **Report** to `.tmp/r13/report.md`, using `scripts/eval_memory.py` and the r12 diag scripts in `.tmp/r12/diag/explore/` against the new run, alongside the r12 numbers:
  - party yes/no and its step;
  - steps from the first map-0 step to entering truth map 40;
  - `here` go-to picks;
  - option labels with a bare text-tier name;
  - the longest map-0 coverage plateau;
  - distinct map-0 tiles and revisit steps;
  - asks per step;
  - route failures;
  - the ledger before and after.
