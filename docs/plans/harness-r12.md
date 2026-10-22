# Avatar harness round r12: honest memory, honest failures, a map of what is visible

**Summary (6 lines)**
1. **Source.** This round is the 2026-10-22 audit (`docs/plans/audit-2026-10-22.md`). The user picked every recommendation, D1–D16, including D16 b (patch evidence kept per game).
2. **Memory (A1/S1, A4):** battle, keyboard and menu screens stop writing room memory. They get their own `screens` store, which also keeps the game's reply to each menu choice, so RUN shows "no running from a trainer battle".
3. **Exits and failures (A2, A3):** an uncrossed exit is one `walk onto` row until the player stands on it; only then are push directions offered. A route that stops short is filed as "not reached", never as a failed push.
4. **The ground (V1, R1):** every visible tile's pixels are filed under a look. Looks the agent walked on read as floor and looks it bumped into read as wall, kept per game. Routes avoid look-alike walls, and a bump re-plans to the same target inside the same step.
5. **Prompts, tooling, hygiene (A5–A7, MT, H1):** a re-ask on a malformed perceiver reply, true prompt facts, name rules moved to the brief, A3 cases that cannot pass by construction, a provenance `dirty` flag, dead modes removed, plan docs filed.
6. **Asks:** 0 for every part and gate; then LV12, a clean-boot run of 250 steps at ≤ 400 asks (ledger 7304 / 7854, 550 left). **Executed by** a ChatGPT orchestrator with parallel subagents in separate worktrees; see `docs/plans/execute-r12.md`.

**Baseline (HEAD `a017ba2`):** suite 755 passed, 1 skipped. Measures M0, V0 and P0 are in `.tmp/r12/{m0,v0,p0}/` and summarised in the audit.

---

## Decisions (all picked 2026-10-22)

| id | pick |
|---|---|
| D1 | **B:** a non-world screen writes no `entities` rows |
| D2 | **B:** one `walk onto` row until the exit tile is reached, then push rows |
| D3 | **A:** route facts plus `unreached` evidence (V1 replaces the "count unseen steps" label) |
| D4 | **A:** the game's reply is noted on the modal row |
| D5 | **A:** one immediate re-ask on a malformed perceiver reply |
| D6 | **A:** the stall number, "Recent" from trace outcomes, and the perceiver's place name marked as a picture name |
| D7 | **A:** name preferences move to the brief, "ED" is dropped, typing gets a cost fact, and the sentence-count test is replaced |
| D8 | **A, after r12:** the refactor (one failures store, an options split, an `_exits` cache) is **not** in r12 |
| D9 | commit `scripts/run_agent.py` as the user's change; leave `scripts/eval_frames.py` uncommitted and untouched |
| D10 | move the r2–r11 round plans and executor packets into `docs/plans/rounds/` and commit them |
| D11 | **a:** LV12 is a clean boot, 250 steps, ≤ 400 asks; success = after a party, truth map 40 → 0. Report the rival battle and Viridian as stretch goals |
| D12 | **include** the MT fixes |
| D13 | **a:** V0 measured (done), so V1 is built |
| D14 | **a:** R1 with a bump limit of 3 per move |
| D15 | **a:** S1 (store, items, replies) |
| D16 | **b:** look evidence per game, in `runs/looks/<game>.jsonl`, each fact keeping its source run |

**Not in r12** (and why):
- D8's refactor;
- A8, which is re-measured by TC after A1;
- S1's "select without reading the menu", which needs cursor detection (the exact-frame model cache already covers byte-identical repeats);
- cleaning old `.tmp/` scratch, which needs the user's explicit OK per folder.

---

## Parts

| id | title | lane | files | asks | gate |
|---|---|---|---|---|---|
| W0 | Wave-0 setup: baseline, commit the runner (D9), file plan docs (D10), delete empty root dirs | orchestrator | `scripts/run_agent.py` (commit only), `docs/plans/rounds/`, root dirs | 0 | suite = baseline |
| T1 | Replay tooling: `--points <file>`, an `absent` check, and `scripts/eval_memory.py` (M0 promoted) | C | `scripts/eval_act.py`, `scripts/eval_memory.py`, tests | 0 | suite; M0 numbers reproduced on r11 |
| MT | A3 drops by-construction cases; new `scripts/points_r12.json` | C | `scripts/points.json`, `scripts/points_r12.json` | 0 | `eval_act --om points` dry: no FAIL |
| H1 | Hygiene: dead modes, `decide.py`, dead trace fields, a provenance `dirty` flag, pytest-cov in the dev extras, delete `check_track.py` | C | `agent/loop.py` (`__init__`, `state`, `restore`, row dicts only), `agent/decide.py`, `scripts/run_agent.py`, `scripts/eval_act.py`, `pyproject.toml`, `scripts/check_track.py`, tests | 0 | suite |
| A1 | A non-world screen writes no world entities; one shared `modal()` predicate | A | `agent/world.py`, `agent/memory.py`, `agent/loop.py`, tests | 0 | suite + G-A1 |
| S1 | A `screens` store: a modal screen's items and the game's reply to each choice (includes A4) | A | `agent/memory.py`, `agent/world.py` (modal rows), `agent/loop.py` (`_settle` reply capture), tests | 0 | suite + G-S1 |
| A5 | One re-ask on a malformed perceiver reply | B | `agent/loop.py` (`_see`), tests | ≤ 1 per malformed reply live | suite |
| A6 | True prompt facts: the stall number, "Recent" from trace outcomes, picture-named places | B | `agent/loop.py` (`_evidence`, the focus line), `agent/notebook.py` (`render_focus`), tests | 0 | suite |
| A7 | Name rules leave the act SYSTEM; the brief holds them (the typing-cost note is in S1) | B | `agent/act.py`, `agent/briefs/pokemon_red.json`, `agent/loop.py:174`, `tests/agent/test_act.py` | 0 | suite |
| A2 | An uncrossed exit is one `walk onto` row until the player stands on it, then push rows | D | `agent/world.py` (`options` exit block, `plan`), `agent/loop.py` (action handling, `_approach_route`), `agent/skills.py` (`run_affordance` kwarg), tests, golden, `scripts/points.json` | 0 | suite + G-A2 + G-door′ |
| A3 | A route that stops short is `unreached` evidence on its target tile, shown on every row that targets that tile | D | `agent/memory.py` (a `reach` store), `agent/loop.py` (`_settle` failure branch), `agent/world.py` (`option` notes), tests | 0 | suite + G-A3 |
| V1 | Looks: the visible ground filed by pixel look; per-game evidence; routes avoid look-alike walls; the planner map marks them | E | `agent/space.py`, `agent/memory.py` (looks), `agent/world.py` (`_record_view`, `view` glyph), `agent/loop.py` (`__init__` kwarg), `scripts/run_agent.py` (store path), tests | 0 | suite + G-V1 |
| R1 | Re-plan on a bump: a blocked route step re-routes to the same goal, up to 3 bumps, within the step; on-screen approaches run as one-leg travel | F | `agent/world.py` (`travel`), `agent/loop.py` (the screen-route execution path), tests | 0 | suite + G-R1 |
| G | Integration gates on merged HEAD | orchestrator | `.tmp/r12/` scratch | 0 | all G-* pass |
| LV12 | Clean-boot live run | orchestrator | run data | ≤ 400 | all parts merged; G pass |

---

## Lanes, waves and file ownership

Lanes run in **separate git worktrees** on their own branches (see the executor brief). A lane edits only its files and line regions. The orchestrator merges lanes into `feat/lean-agent` in the order below, and runs the full suite and the gates on the main checkout after each merge.

| wave | lanes in parallel | starts after | owned regions (HEAD `a017ba2` line numbers) |
|---|---|---|---|
| 0 | orchestrator only | — | W0 |
| 1 | **A** (A1 → S1), **B** (A5 → A6 → A7), **C** (T1 → MT → H1) | W0 merged | A: `world.py` 90–111, 148–169, 421–502, 1052–1058; `memory.py` 238–358 plus new store code; `loop.py` 110–116, 279–316. B: `loop.py` 94–104, 174, 502–535; `notebook.py` 303–326; `act.py` 6–11; `briefs/`. C: `loop.py` 6, 28–30, 220, 322–331, 388–398, 542–570; `decide.py`; `scripts/*` except `eval_frames.py`; `pyproject.toml` |
| 2 | **D** (A2 → A3), **E** (V1) | wave 1 merged | D: `world.py` 1010–1100 (exit rows, `option`), 1212–1238 (`plan`); `loop.py` 180–216, 355–380, 445–470; `skills.py` `run_affordance`; `memory.py` (new `reach` store only). E: `space.py` 9–30, 40–56; `memory.py` (new looks code only); `world.py` 260–304, 903–916; `loop.py` `__init__` only; `scripts/run_agent.py` (the `Loop(...)` line) |
| 3 | **F** (R1) | wave 2 merged | `world.py` 1239–1295 (`travel`, `_travel_result`); `loop.py` 240–270 (execution) |
| 4 | orchestrator | wave 3 merged | G, then LV12 |

Line numbers move as lanes merge. Each lane starts from the merged HEAD of the previous wave and re-locates its regions with `codegraph_explore` before editing.

---

## Item specs

### W0: setup (orchestrator)
1. Verify the baseline: `.venv/Scripts/python.exe -m pytest -n auto -q` gives 755 passed, 1 skipped.
2. **D9:**
   - commit `scripts/run_agent.py` alone, exactly as on disk (cap 7854/7804, checkpoints every 5 steps), message `W0 Commit the user's runner edits (caps 7854/7804, checkpoint every 5)`;
   - do not touch `scripts/eval_frames.py`, `scripts/frames_combo.json` or `baseline_eval.txt`.
3. **D10:** `git mv` is not possible for untracked files, so move them with a plain file move:
   - `docs/plans/harness-fixes-r2..r4.md`, `harness-r5-ab.md`, `harness-r6..r11.md`, `chatgpt-execute-r*.md`, `execute-r9..r11.md` and `handoff-r8-planning.md` go into `docs/plans/rounds/`;
   - commit them together with `docs/plans/audit-2026-10-22.md`, `harness-r12.md` and `execute-r12.md`.
4. Delete the empty root dirs `m1_exit_b1_live_150/`, `m1_live_150/`, `m1_rcr_phase_e/`, `m1_rcr_post_r3/` and `Pokemon_ Red Version/`. Each holds only empty folders; verify with `find <dir> -type f | wc -l` = 0 first. They are untracked, so there is no commit.

### T1: replay tooling (lane C)
- `scripts/eval_act.py`:
  - add `--points <path>` (default `scripts/points.json`), replacing the hard-coded `POINTS` at `:787` as the default only;
  - in dry mode, add one check type `absent`: a list of `{tool, label_re}` that must match **no** option at the target step. A match is FAIL;
  - keep every existing check unchanged.
- `scripts/eval_memory.py` (new, about 80 lines). Promote `.tmp/r12/m0/m0.py`:
  - input: a run directory (with `memory/` and `trace.jsonl`);
  - output: the M0 columns (world rows, modal-kind rows, renamed strict/loose, route failures, crossing failures, unreached intents ≥ 2 and later reached);
  - 0 asks, and no import of `agent.model`.
- **Tests:** `absent` FAILs when matched and passes when not; `eval_memory` on a tiny synthetic run dir.
- **Gate:** the suite; `eval_memory.py runs/m1_r11_boot` prints the M0 row recorded in the audit (world 150, modal-kind 43, route 36).

### MT: trustworthy A3 cases (lane C)
- `scripts/points.json`: delete the cases that pass by construction:
  - `m1_r6_live_150:131` and `:104` (`^advance$`);
  - `m1_r6_live_150:132` and `m1_r7_resume115:135` (`right: []`; dry mode never tests a choice).

  Keep the rest unchanged.
- New `scripts/points_r12.json`: the r11 gate cases listed under G (G-A1, G-S1, G-A2, G-A3) with their checks. Each case uses `run: "m1_r11_boot"`, the nearest checkpoint at or below the step (every 5 steps exist), and the recorded truth. Write them now; they are expected to FAIL until their part lands, so they are run by the orchestrator only at G.
- **Gate:** `eval_act --om points` (dry, `scripts/points.json`) shows no FAIL. Report the new PASS / INCONCLUSIVE counts.

### H1: hygiene (lane C)
- **Dead modes.**
  - Delete `act_text` and `decide` from `Loop.__init__` (`loop.py:28-30`), `state()` (`:545`) and `restore()` (`:548-552`). `restore` ignores a saved `modes` key, so old checkpoints still load.
  - Delete `resolve_modes` and the `--decide` / `--act-text` flags from `scripts/run_agent.py` (`:17-29`, and its call sites `:160`, `:169`, `:210`). Keep accepting the flags as no-ops if dropping them would break the LV11 command in docs; simplest is to delete them, and LV12's command omits them.
  - Delete the matching code in `scripts/eval_act.py:622`, `:912`.
  - Manifest rows stop writing `decide`/`act_text`.
- **`agent/decide.py`:** replace `Choice` with a small dict or a local namedtuple in `loop.py`. Its fields `effect`, `detail`, `used` and `called` are constants: keep only what the trace row writes (`i`, `why`, `text`), and keep the row keys `called` and `used`, which `eval_run.py:1147` reads, as `False` and `[]`.
- **Dead trace fields:** remove `task`, `pred`, `shortcut` and `ranker` from the row dicts (`loop.py:322-330`, `:391-396`), only after a grep proves no script or test reads them. Keep `action_step`, `view_ids` and `used`: `eval_run.py:57`, `:1145` and `:1147` read them.
- **Provenance:** `scripts/run_agent.py` `manifest_record` (`:50`) adds `"dirty": bool(source.dirty entries other than "skipped")`. `provenance` stays "complete" or "incomplete" as now; `dirty` is a separate field. Test it in `tests/agent/test_run_provenance.py`.
- Add `pytest-cov` to `[project.optional-dependencies].dev` in `pyproject.toml`. Do not install it.
- `git rm scripts/check_track.py`, after a grep shows no import. Tests reference `check_tracks.py`, not `check_track.py`.
- **Pre-approved test changes:** delete or adapt the tests that exercise only the removed modes:
  - `test_run_provenance.py:35-44` (`resolve_modes`);
  - `test_loop.py:1795-1799` (drop the `decide` parametrize; keep the test body for one value);
  - `test_loop.py:2159-2162` (the modes round-trip; replace it with "a saved `modes` key is ignored");
  - `test_om_eval.py:246`, `:275-277` (the mode plumbing).

  Report each one, before and after.
- **Gate:** the suite; `eval_run.py runs/m1_r11_boot` output is unchanged except for removed fields (diff it).

### A1: non-world screens write no world entities (lane A)
- **One predicate.** `modal(per)` lives in `agent/world.py` beside `FURNITURE`. It is true when `per["screen"] != "world"` or any entity kind normalises to `dialogue_box`, `menu`, `keyboard` or `input_field` (today repeated at `world.py:265-266`, `:447`). Use it at those two sites.
  - `loop.py:115` and `:279` test a *transition* between screens. Leave their logic alone; only replace the inline kind test, if there is one, with the predicate.
- **The rule.** On a non-world screen (`per["screen"] != "world"`):
  - `World.before` (`:162`) and `World.observe_after` (`:449`) do **not** call `Memory.observe`, and `entity_ids` becomes `{}` for that frame;
  - `World._tiles` (`:104`) returns `{}` when `per["screen"] != "world"`, as a guard for any other caller.

  A world screen with a dialogue box over it still observes, because the room is visible. Its text-box and menu kinds are already transient.
- **Knock-on:**
  - Modal options (`world.py:1053-1058`) build `select` rows from the perception. Their `thing_ref(self.entity_ids.get(i))` becomes `None`, so their fail keys fall back to the item name (`fail_key(place, "select", name)`); that is intended.
  - `Loop._settle`'s `says` relations need a `tid`, so they stop for modal rows; S1 replaces them.
- **Tests:**
  - a battle-screen perception with an `hp_bar` and a `button` writes no entity row and renames no existing row;
  - a keyboard screen likewise;
  - a world screen with a dialogue box still binds its world things;
  - a modal `select` row's intent key is name-based.
- **Pre-approved:** none. If an existing assertion fails, stop the part and report it with before/after.
- **G-A1 (0 asks, orchestrator after merge):** `eval_act --om points --points scripts/points_r12.json --steps 117`.
  - Case `r11:117` (checkpoint 115, replaying 115–117) has `absent: [{"tool":"interact","label_re":"^check [^ ,]{1,3}(,|$)"}]`.
  - Plus `eval_memory.py` on the replay's memory copy: modal-kind rows created at steps ≥ 105 = 0.

### S1: the screens store (lane A, after A1; includes A4)
- **Store.** Memory store `screens`; row id = `<screen>|<sorted selectable names joined by |>`. For example `battle|FIGHT|ITEM|PKMN|RUN`.
  - Row: `{"id", "screen", "items": [names in screen order], "seen": n, "first", "last", "replies": {name: [{"step", "text"}]}}`, with at most the last 3 replies per item.
  - Written by `Loop._settle` for every step whose before-perception is non-world and has at least one selectable entity.
- **The reply.**
  - When the chosen row was `select <name>` on that screen, and the after-perception has text that was not on the before-perception (the same "new text" rule as `loop.py:293`), append `{step, text: first new line[:80]}` to `replies[name]`.
  - Nothing is judged; no verdict changes.
- **Options.** A modal `select <name>` row whose screen row has replies gets the note `chosen step s: "<text>"` (the latest reply), the same shape as the `talked step …` notes (`world.py:1031-1034`).
- **No game words.** The screen kind comes from the perceiver enum, and the names from the screen.
- **Typing cost (moved here from A7):** the modal `type text into the field` row (`world.py:1057`) gets the note `typing costs about 3 presses per letter`. A fact, not a rule.
- **Tests:**
  - selecting `RUN` and then seeing new text records the reply;
  - the next time the same item set is on screen, the `select RUN` row carries the note;
  - a different item set is a different row.
- **G-S1:** points case `r11:137` (checkpoint 135): the `select RUN` row has `note_re: "no running"`.

### A5: one re-ask on a malformed perceiver reply (lane B)
- **Where:** `Loop._see` (`loop.py:94-104`). When `reply.data is None` (the MalformedReply case), call `perceive` once more with `ctx + "\nYour last reply was not valid JSON: <first 120 chars of the parse error>. Reply again with compact valid JSON."`.
  - Get the parse error from `json.loads(reply.text)` in a try/except.
  - Use the second reply if it parses; else keep today's path (`parse_failed`, and the repair after 8 empty frames).
- **Do not** add salvage parsing (D5 = A).
- **Tests:** a stub model whose first reply is non-JSON and whose second is valid gives the second perception and exactly 2 model calls; two invalid replies give today's behaviour.
- **Asks:** in LV11 this would have added 7 asks.

### A6: true prompt facts (lane B)
1. **Stall number.** `loop.py:532` prints `(stall at {STALL * 2 ** self.stuck.u})`, importing `STALL` from `agent.tasks`.
   - **Pre-approved:** `test_loop.py:1987` changes to the computed value. Report it before and after.
2. **Recent in the act focus.**
   - `notebook.render_focus` (`notebook.py:324`) takes an optional `recent` list. `Loop.step` (`loop.py:174`) passes the last 4 rows of `self.recent` as `"step s: <verb> <target> -> <verdict>"`.
   - With no `recent` (or `None`), the old `self.log[-4:]` path stays, so `test_notebook.py:228-290` keep passing.
3. **Picture-named places.**
   - In the act focus only (`loop.py:174`, `Here: …`), when the place's name came from the perceiver's `place` field, append ` (a name read from the picture)`.
   - Today all place names come from that field (`memory.name_place`, `world.py:1296-1298`), so the suffix is unconditional for named places and absent for "place N".
   - `test_loop.py:1462` asserts `"Here: Hall" in focus`; that still holds.
- **Tests:** the stall number follows `u`; the act focus shows trace outcomes; the suffix appears.

### A7: name rules leave the act prompt (lane B)
- **`act.py:9`:** remove "pick a preset or default name … often labelled END, ED or OK);". Keep the rest of that sentence and the F1 wall clause.
- **`agent/briefs/pokemon_red.json` `notes`:** add the user's preferences as generic text:
  - "When asked for a name, prefer a preset or default name over typing one.";
  - "When asked whether to give a nickname, answer no.";
  - "To leave a name keyboard, choose its end key."

  The brief already reaches the perceiver context (`loop.py:99`). Also show it to the act model: `Loop.step` appends one line, `Game notes: <the brief's notes joined by a space>`, to the `focus` string it builds at `loop.py:174`. `act()`'s signature does not change, so `scripts/eval_frames.py` (protected) and the other callers are untouched. This is one new line, no new prompt sentence.
- **Test:**
  - **Pre-approved:** `test_act.py:71` and `:91`, `len(SYSTEM.split(". ")) == 4`, are replaced by: `SYSTEM` contains none of the banned words (`test_act.py:72`) and `len(SYSTEM) <= 1200`;
  - a new test: the act focus contains the `Game notes:` line when the brief has notes.

### A2: an uncrossed exit is walked onto before it is pushed (lane D)
- **Where:** `World.options`, the uncrossed-exit block (`world.py:1078-1096`).
  - Let `here = tuple(self.pos) == x["tile"]` (the player stands on the exit tile, and the place is not unplaced).
  - **Not here, and the exit has a tile:**
    - one row, label `walk onto <label>{ (wall)}{where}`;
    - action `{"thing": idx, "verb": "approach", "onto": True}` on screen, or `{"go_to": exit_ref(x), "onto": True}` remembered;
    - notes `["exit, never crossed"]` plus the tried-push facts already recorded on that tile (`pressed D: blocked` / `moved, no exit`);
    - fail key `fail_key(place, "approach", target, inputs, ref)` (**no** `>DIR`);
    - state `failed` if that key is in `fails` or the A3 `reach` row says unreached, else `new`.
  - **Here:** keep today's per-direction rows (F1 labels and facts), but relabel `go to <label>…, then press D: …` as `press D here: <facts>`. The action keeps `push`; the fail key keeps `>DIR`.
  - **No tile:** unchanged.
  - **Refuted:** unchanged (one failed row).
- **Executing `onto`:**
  - `Loop` (`loop.py:184-216`): an action with `onto` routes to the exit tile and stops there. In `_approach_route` (`:452-470`), `onto` means `push=None` and route to `t` (not beside). `_screen_route` accepts `push=None` when `onto`. Skip the recorded-exit shortcut (`:455-467`) when `onto`.
  - The skill: `run_affordance(..., onto=True)` passes `exit_target=None` to `approach`. This is one new kwarg in `skills.py:288`; nothing else in `skills.py` changes.
  - `World.plan` (`:1231-1236`): for `target["kind"] == "exit"` with `onto`, append a plain walking leg `{"place", "dirs", "push": None, "expect": None, "target"}`, like a memory target without facing.
- **Effect:** a route that stops short is a route failure of the `walk onto` row. A push is only ever offered, and failed, from the tile, so E3–E5 (`failed_push`, refutation, demotion) finally see real pushes.
- **Tests:**
  - away from the tile, one `walk onto` row with no `then press`;
  - standing on it, four `press D here` rows in `push_dirs` order;
  - picking `walk onto` routes to the tile with no push on both the screen and the remembered paths;
  - a route failure of `walk onto` leaves the push rows untouched;
  - a crossing failure from the tile still writes `pushed` (E3).
- **Pre-approved (report each):**
  - the O1/F1 exit-row label/count/order class in `test_options.py`, `test_exits.py`, `test_world.py` and `test_loop.py`, for rows of an uncrossed exit with a tile;
  - `scripts/points.json` `label_re` for `m1_r6_live_150:64`;
  - the golden act trace, regenerated by the **orchestrator** with `W0_REGEN=1` after merge, with the diff limited to that exit row's label, action, intent key and numbering (memory stores identical).

  Anything else: stop.
- **G-A2:** points case `r11:116` (checkpoint 115):
  - `right: [{"tool":"approach","label_re":"^walk onto door \\(top wall\\)"}]`;
  - `absent: [{"tool":"approach","label_re":"then press|press (UP|DOWN|LEFT|RIGHT) here"}]`.
- **G-door′** (`.tmp/r11/gdoor.py`, adapted by the orchestrator): on the r10 checkpoint-50 copy with the player placed on the door tile, the rows are `press D here` with `DOWN` first under "bottom wall".

### A3: unreached evidence (lane D, after A2)
- **Store.** Memory store `reach`; id `<place>@<x>,<y>` of a target tile; row `{"id", "place", "tile", "n", "last", "at": [x, y], "step"}`.
- **Write.** In `Loop._settle`'s failure branch (`loop.py:363-372`), when `stage == "route"` and the action had a target tile (`s["aff"]`'s projected tile for on-screen targets; the travel target's tile otherwise): `n += 1`, `last = step`, `at` = the tile where the route stopped (`held`).
- **Clear.** When a later step ends with the player on that tile, or beside it for interact, delete nothing: set `n = 0` and `reached = step`. Evidence is kept.
- **Show.** `World.option` (`world.py:1016-1041`) adds the note `not reached: stopped at (x,y) n× (last step s)` to any row whose target tile has `n > 0`, whatever its verb or ref. So the on-screen row and the remembered row of one target share the fact.
- **Tests:** two different rows to one tile share the note; a later arrival zeroes it.
- **G-A3:** points case `r11:119` (checkpoint 115): the `walk onto door` row has `note_re: "not reached"`.

### V1: the visible ground by look, kept per game (lane E)
- **Per-game store.**
  - A small class `Looks` in `agent/memory.py`: path `<runs>/looks/<game>.jsonl`, append-only rows `{"id": "<look>|<run>|<place>@<x>,<y>|<label>", "look", "label": "floor"|"wall", "run", "place", "cell", "step"}`.
  - Duplicate ids are ignored on add and on load.
  - `says(look)` returns `"floor"`, `"wall"`, `"mixed"` or `None` from the counts.
  - **Wiring:** `Loop(..., looks=None)`. When given a path, `Loop.__init__` sets `self.mem.looks = Looks(path, run=self.mem.root.parent.name)`. `Memory.looks` defaults to `None`: tests, replays and the golden trace stay unchanged. `scripts/run_agent.py` passes `looks=Path("runs/looks") / f"{a.game}.jsonl"`.
- **Cell looks (per run).**
  - Memory store `looks` (per run), id `<place>@<x>,<y>`, row `{"id", "place", "x", "y", "look", "step"}`.
  - Written in `World._record_view` (`world.py:260-275`) only when `self.mem.looks is not None`, `rect` exists, the screen is world and not `modal(per)` (A1's predicate), and `frame` is given.
  - For each cell in `rect` except the player's tile (`self.pos`) and `self.actor_tiles`: look = `hashlib.blake2b(frame.convert("L").crop(cell box).tobytes(), digest_size=8).hexdigest()`, with the cell box at `((x - x0) * t, (y - y0) * t)`, the same grid `_view_rect` assumes.
  - Upsert only when the look changed.
  - `ponytail:` comment: the grid is assumed aligned at pixel 0 when settled; true for the Game Boy (V0: 1,083 of 1,083 frames); a scrolling-phase game needs the tracker phase.
- **Evidence.**
  - `space.fold` (`space.py:9-30`) calls `mem.learn_look(place, cell, "floor", step)` for every cell it marks floor, and `mem.learn_look(place, here + dir, "wall", step)` for a blocked push (the existing actor exception at `:15` stays).
  - `Memory.learn_look` is a no-op without `self.looks` or without a per-run look row for that cell; otherwise it adds to the per-game store.
- **Routing.**
  - `space.route` (`space.py:40-56`), strict pass only: a cell is excluded like a static thing when `mem.looks` says `"wall"` for its look, the cell's own tile row is not `floor: True`, it has no exit, and it is not the goal.
  - The non-strict fallback (`:56`) ignores looks, so a route still exists when looks are wrong.
  - `ponytail:` comment: an O(cells) scan per call; cache per step if routing gets slow.
- **The planner map.** In `World.view`'s glyph loop (`world.py:903-913`), an otherwise blank or `,` cell that looks like a wall prints `x`.
  - The legend line (`:916`) gains `, x looks like a wall` **only when** an `x` is drawn, so `test_world.py:632` keeps passing.
- **Seeding (part of D16 b).** `.tmp/r12/v1_seed.py` (scratch, 0 asks) builds the per-game store from the 16 past runs' **own** evidence, never RAM truth:
  - for each run in time order, replay its frames (`frames/NNNNNN.png`) with the agent's own geometry: trace `tile`, `self_spot`, `place`, `screen`;
  - feed its `memory/tiles.jsonl` floor and blocked rows in step order through the same `Looks`/`learn_look` code;
  - write `.tmp/r12/looks/pokemon_red.jsonl`.

  The orchestrator copies it to `runs/looks/pokemon_red.jsonl` before LV12.
- **Tests:**
  - a fake frame with two identical wall tiles: a bump into one makes `route` avoid the other;
  - a look-alike wall that is later walked gives `mixed`, and stops being avoided;
  - `looks=None` writes no `looks` store (golden unchanged);
  - the fallback still routes through look-alike walls when no other route exists;
  - the map shows `x`.
- **G-V1 (0 asks):** run the seeding script, then the V0 measure re-pointed at the agent-geometry store (`.tmp/r12/v0/v0.py`, adapted). Pass when:
  - wall and floor precision are ≥ 0.95;
  - per-game coverage of distinct bumped walls is ≥ 60% (V0: 68% with truth geometry);
  - report r11's route-failure bumps that would have been predicted.

### R1: re-plan on a bump (lane F)
- **In `World.travel`** (`world.py:1239-1276`): when a leg's approach returns `route blocked` with no cut (`:1252`, `:1271`):
  - fold its moves (already done at `:1250`);
  - then, while `bumps < 3`, the input budget remains, and `route(...)` from `self.pos` to the leg's goal (the exit tile, or the target or beside tile) exists: run `approach` again on the new route and fold;
  - stop at the first success, cut, or no route.
  - The final result carries `failure` from the last attempt, plus `"bumps": n`.
  - Module constant `BUMPS = 3` beside `TRAVEL_MAX_INPUTS`.
- **The on-screen path through travel.**
  - In `Loop.step` (`loop.py:246-248`), an `approach` choice that has `act_route` (on-screen thing, exit `walk onto`, exit push from the tile, or explore) runs as `self.world.travel(skillctx, [leg], ...)` with one leg: `{"place": place, "dirs": act_route["dirs"], "push": act_route.get("push"), "expect": None, "cross": bool(act_route.get("push")), "exit_tile": t, "target": {"kind": "screen", "tile": t}}`.
  - `travel_legs` is set, so `prepare`'s travelled branch avoids a double fold, `_travel_push` carries the push for E3, and `scene_cut` uses `_travel_cut` (`loop.py:253`).
  - `interact` keeps its current path (it presses A after facing); not in R1.
- **Invariants (tests):**
  - every move is folded exactly once;
  - a wall bumped mid-route is recorded and the next attempt goes around it within the same step;
  - after 3 bumps the step ends blocked with `bumps: 3`;
  - a crossing-stage failure (push from the tile, no cut) still reaches `failed_push`;
  - a scene cut mid-route ends the leg as today.
- **Pre-approved:** trace-shape changes caused only by on-screen approaches now being travel (`travel: true`, `push` from `_travel_push`) in `test_loop.py` / `test_world.py` assertions about those two fields. Report each. The golden act trace is regenerated by the orchestrator after merge, with the diff limited to the `travel`, `push`, `route_len` and `world.travelled` fields of approach rows (memory stores identical).
- **G-R1:** unit tests, plus `eval_memory.py` on LV12, where route failures per 150 steps should fall from r11's 36 (reported, not gating).

### G: integration gates (orchestrator, merged HEAD, 0 asks)
1. The suite is green; report counts against the baseline.
2. `eval_act --om points --points scripts/points_r12.json`: G-A1, G-S1, G-A2 and G-A3 PASS. INCONCLUSIVE is allowed only with a stated replay reason (RAM divergence or perception fallback), at most 1.
3. `eval_act --om points` (the cleaned `scripts/points.json`): no FAIL.
4. G-V1 passes; the seed store exists in `runs/looks/pokemon_red.jsonl`.
5. G-door′ passes.
6. The ledger is unchanged at 7304.

### LV12 (orchestrator, ≤ 400 asks)
```
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r12_boot --steps 250 --max-calls 400
```
- **Precondition:** every part is merged, G passes, and the viewer is up (`CLAUDE.md`).
- **Success:** after the first party, truth map 40 → 0 (leaving the lab). Stretch goals: winning the rival battle (truth `battle` back to 0 with the party alive), then reaching Viridian.
- **Report:**
  - the first party step and the lab-exit step;
  - `m1_gate_pass`;
  - `eval_memory.py runs/m1_r12_boot` (A1: modal rows 0);
  - route failures and bumps per step;
  - every `walk onto` and `press D here` pick with its outcome;
  - screens-store replies;
  - asks and seconds per step.

---

## Validity check (HEAD `a017ba2`; re-locate after each merge)
- `agent/world.py`:
  - `:105` `_tiles` place-screen gate;
  - `:148-169` `before` (`:162` observe);
  - `:260-304` `_record_view`;
  - `:421-502` `observe_after` (`:447` modal kinds, `:449` observe);
  - `:1010-1176` `options` (`:1016-1041` `option`, `:1052-1058` modal rows, `:1078-1096` uncrossed exit rows);
  - `:1212-1238` `plan`;
  - `:1239-1276` `travel`.
- `agent/loop.py`:
  - `:28-30` modes;
  - `:94-104` `_see`;
  - `:115` and `:279` modal transition;
  - `:174-175` focus and act call;
  - `:180-216` action handling;
  - `:246-248` skill execution;
  - `:281` `failed_push`;
  - `:290-316` `says`;
  - `:322-331` the row;
  - `:363-372` the failure branch;
  - `:445-470` `_screen_route`, `_approach_route`;
  - `:502-535` `_evidence` (`:532` stall text);
  - `:542-570` `state`/`restore`.
- `agent/memory.py`: `:238-358` `observe` (`:251` the tile rule).
- `agent/space.py`: `:9-30` `fold`; `:40-56` `route`.
- `agent/act.py`: `:6-11` `SYSTEM`.
- `agent/notebook.py`: `:303-326` `render_focus`.
- `agent/skills.py`: `:288` `run_affordance`.
- `agent/tasks.py`: `:4` `STALL`.
- `scripts/eval_act.py`: `:787` `POINTS`; `:867` `_points_case`.
- `scripts/eval_run.py`: `:57`, `:1145` and `:1147` read `action_step`, `view_ids` and `used`.
- `scripts/run_agent.py` (uncommitted until W0):
  - `:16` caps;
  - `:19-29` `resolve_modes`;
  - `:50` `manifest_record`;
  - `:210` the `Loop(...)` call.
- **Tests that pin today's text:**
  - `test_act.py:71`, `:91`;
  - `test_loop.py:1462`, `:1987`;
  - `test_world.py:631-632`;
  - `test_notebook.py:228-290`.
