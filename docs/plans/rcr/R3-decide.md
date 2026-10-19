# R3: Decision interface (act prompt and option menu)

Status: **gate passed 10-19 on m1_rcr_baseline** (suite 612/1 skip, LV 20/20, menu max prompt 1,909, dup 0, 74/74 steps). Grouping applied, revert if S4 does not move. Phase D (R3.7) awaits ledger cap · Branch feat/lean-agent

## 1. Mechanism: the missing invariant

**A row is not a thing.** Menu rows are keyed by the VLM's name. What the agent knows about one thing is spread over five channels that use different names: the row label, the row note, the compact view's `E at` lines, `Flags` (recent, cause, here) and the log. The model must join them by name, which is R1's bug rebuilt inside the prompt. Everything outside the rows is unbounded and is not conditioned on the screen.

- **Channels disagree.** At S70, row 1 says `tried here 1×` while `E at (-4,-1) door: possible, never crossed`. Flags name the same tile `approach exit: window [p_place_3|approach|thing:e_exit_window]`, an internal key.
- **Rows are not distinct.** At S65-74, twin labels read `approach possible exit door (unverified, never crossed)` (world.py:580; the label is the VLM name with no position). A failure note lands on one twin and the other reads untried: S70 failed row 1, and S71 picked its twin.
- **The guess is the headline and the evidence is a footnote.** The model matched the label "door" to the quest's "next door" (S70 why).
- **Order works against the evidence.** The order is fixed and history-free (world.py:781-787, by the OM.md:59 rule), and the sort is by screen row (world.py:40). So failed rows sat at positions 1-2 and the door mat at 5. Option 1 was picked 44 of 99 times. **15 of 99** picks were a noted-failed row while an untried row existed: S38, 49, 50, 70-73, 75, 79, 82, 84-86, 88, 103.
- **The context is not about the choice.** At S70 the options are 879 of 4,925 chars (18%). The rest:
  - 4 stale quests (notebook.py:492, uncapped);
  - a 1,096-char log, one untruncated intro line;
  - 722 chars of facts, from the newest-4 fallback (notebook.py:499);
  - uncited beliefs (`held[-3:]`, :495) and the planner note;
  - an ASCII map, `E at` lines and 423 chars of flags.

  The maximum is 5,595 chars (S102).
- **Dialogue presses come from stale context, not from a lost sentence.** S36 and S38 are dialogue screens, yet the prompt carried the world map, `here: … untried DOWN, LEFT, RIGHT` (world.py:308) and an explore quest. The model's why: "explore the untried LEFT and RIGHT edges", followed by a free-buttons press.
- **Name-list rows cost a second call** (world.py:866-875, act.py:87-92): 11 follow-ups at S41-57. Their things live inside one label, so none of them carries state. In Pallet such a row listed up to 36 names.
- **Two prompt paths.** The SYSTEM/REPLY free path (act.py:9-22, 100-125) is still the `run_agent` default (`--decide` is opt-in) and still carries the dialogue line (act.py:18) that `DECIDE_SYSTEM` (act.py:54-57) lacks. In decide mode, `Act.place`, `Act.ops`, `model_frame` and `places=` are computed and discarded (loop.py:160-164).

**Invariant:** one row per distinct real thing (R1 id) and action, and that row is the only place the thing appears. The row says where the thing is relative to me and what happened when I last acted on it. The rest of the prompt is only the goal and the current screen.

## 2. Solution

1. **Row = thing.** `World.options` emits one row per `ref` (R1 id of a thing, exit or place).
   - **Label:** `<verb> <name>, <where>`, where `<where>` is the offset from me (`1 left, 6 down`; the compact view's `away()` moves to a module function), `not on screen, …` for a remembered thing, or `via <exit>` for a place. R1's (place, tile, kind) key makes name plus where unique, so twins cannot occur.
   - **One verb per thing, the one that subsumes the others.** `interact` already approaches first (skills.py:150-163), so actors and things get one `talk to` / `check X` row instead of approach plus interact. Exits get `go to` / `go through`.
   - **Same-name things with no history** (fence posts) share one row with a concrete action on the nearest member: `check fence post (nearest of 7), 2 left`. Things with history keep their own row, as today.
   - **Deleted:** the name-list rows (`look at or go to a thing here: …`, `go to another known place: …`), their follow-up call, and `OPTION_CAP`.
2. **State is shown as grouping, never as removal.**
   - Each row gets `state` from its ref's latest outcome (R1 per-id history plus `Loop.fails[intent_key]`): `new`; `worked` (talked and what was said, crossed N×, checked and what showed); or `failed` (blocked or no effect N×, with steps).
   - Rows print under `Not tried here:`, `Worked here:` and `Failed here:`, numbered straight through and all pickable. Evidence follows ` — `.
   - Inside a group the existing fixed order holds: exits, actors, things, explore, places, then `_yx`.
   - Free buttons is always last. Its note takes over `here_flag`'s content (blocked directions, failed combos).
3. **Bounded context: the goal plus the screen.** `render_focus` gives:
   - `Goal:` the top quest's text and done_when;
   - `Because:` the facts and beliefs the top quest cites, as `from, step: "text"`, at most 160 chars each;
   - `Recent:` the last 4 log lines, at most 120 chars each.

   The act prompt is that, plus `Here / Screen / Text on screen`, plus the grouped options. These leave the act prompt:
   - other open quests and the planner note;
   - the uncited and newest-N fact and belief fallbacks;
   - the compact view (map, `E at` lines, other places);
   - all act flags (recent, cause, here, red box, merge, travel);
   - known places and prefs.
4. **One path.** `act()` becomes today's `_decide`, matching the user's 10-14 decision (options only, strict `{why, i}`).
   - Deleted: the image argument, the free action shapes, place/ops/expect parsing, REPLY*, SCHEMA, `_pick`, `_action` and `_option_text`. Labels print as built.
   - `DECIDE_SYSTEM` drops its failure sentence and its other-quests sentence; structure carries both now.
   - Follow-up calls remain only for free buttons and typing, which are open answers by nature.
   - `Loop(decide=, act_text=)` stay as accepted no-ops, because `run_agent.py` is frozen.
5. **Quest target missing from the menu:** no fallback row. The `Because:` quote (heard text, from R2) carries the meaning, and so do exit rows reading `never crossed` or `crossed → X (N unexplored beyond)`. The model infers; there is no gate.

**Why this is root-level:** it removes the join-by-name inside the prompt, and it removes the context the screen does not need. No rule is added, and the prompt shrinks by about 65%.

**Rejected:**
- Restoring only the dialogue sentence: the S36/S38 why cites the stale context, which the sentence would have to fight. It stays as a measured fallback (§6.4).
- One trailing "failed" row (audit): it needs a second call and loses per-thing evidence.
- Hiding or capping failed rows: that is a gate.
- Ranking by goal words: that is steering, and literal (it rebuilds the object tour).
- Keeping the map: the model is text-only, and per-row `where` holds the same facts.
- Capping other quests at N: still stale lines, and priority belongs to the planner (R2).
- A score sort: a preference order, not presentation.

**Mock of S70, assuming R1 (tiles merged, MOM binding kept), R2 (stale quests closed, source cited) and R4 (blocked = failed):**
```
SYSTEM: You choose the next action in a video game. You do not see the screen: the lines below come from the game's
map memory and a screen reader. Work toward the goal. Pick exactly one numbered option. Return only JSON.
Goal: Find and speak with PROF. OAK          (no target yet: OAK is not a known id; with one: "done when: talked to <name>")
Because: MOM, step 63: "PROF. OAK, next door, is looking for you."
Recent:
- step 54: went from room to tiled room via doorway
- step 63: MOM at tiled room: "Right. All boys leave home some day. It said so on TV. PROF. OAK, next door, is…"
Here: tiled room. Screen: world. Text on screen: none
Not tried here:
1. go to door mat, 1 left, 6 down — exit, never crossed
2. go to rug, 2 left, 6 down — exit, never crossed
3. check shelving unit, 3 right, 1 up
4. check bowl (nearest of 2), 2 right, 2 down
5. explore DOWN, 3 steps to unseen ground          (one row per open direction, R4 explore_dirs)
Worked here:
6. talk to MOM, 2 right — talked step 63: "Right. All boys leave home some day…"
7. go through doorway, 3 right — crossed 1× (step 54) → room
Failed here:
8. go to door, 1 left, 1 up — walked into 2× (steps 66-67), not crossed
9. go to door, 1 right, 1 up — walked into 2× (steps 68-69), not crossed
10. go to bookshelf, not on screen — route blocked 1× (step 60)
11. free buttons: UP, DOWN, LEFT, RIGHT, A, B, START, SELECT — blocked here: UP, LEFT
Reply JSON: {"why": one sentence, "i": the option number}
```

## 3. Deletions (approximate lines)

| Where | What | − | + |
|---|---|---|---|
| act.py | free path: SYSTEM, REPLY*, OPTION_NOTE, SCHEMA, `_pick`, `_action`, `_option_text`, `act()` body 100-125, name-list follow-up 87-92, 2 DECIDE_SYSTEM sentences | ~75 | ~8 |
| world.py `options` | name-list and places-collapse rows, `OPTION_CAP`, approach+interact pairs (830-875) | ~25 | ~25 |
| world.py `view` / `here_flag` | `compact` and its branches (668-686, 697, 724, 763, 775); `here_flag` 308-325 | ~35 | ~3 |
| world.py `_exits` | label strings 573-580 (status stays; `options` builds the label) | ~8 | ~3 |
| notebook.py `render_focus` | other quests, planner note, fallbacks, JSON blobs | ~22 | ~12 |
| brief.py | `DEFAULTS`, `defaults()` (always `{}`), and the prefs plumbing | ~6 | 0 |
| loop.py (R4 applies) | act_flags and recent summary 129-158, red-box flag, `places=`, `model_frame`, `a.place`/`a.ops` | ~35 | ~2 |

That is net about −210 / +53 in `agent/`. Scripts calling the free path change with it: eval_act.py:92, 231, 646; eval_frames.py:208-209; eval_plan.py:173-175. Tests: the REPLY cases in test_act and the name-list rows in test_options/test_exits. `decide.Choice` stays (live, loop.py:197).

## 4. Interfaces (for MASTER §7)

- **Need from R1:**
  - (a) A stable `ref` for every thing, exit and place, on screen and remembered. A tile carrying both door and window labels becomes one exit record.
  - (b) One per-ref history accessor, with steps for each entry: `talked` (with text), `checked` (with text or nothing), `crossed n`, `walked_into n` and refuted.
  - (c) A display name: `called`, else the canonical alias.
- **Need from R2:**
  - (a) The top quest's `text` and `done_when`, citing its source fact (always, for heard quests).
  - (b) Stale quests closed.

  R3 no longer renders the other open quests or the planner note.
- **Need from R4:**
  - (a) `Loop.fails[intent_key] = {n, why, step}` means "did not achieve its effect", and a blocked move counts as one. A success still clears it.
  - (b) R4 applies the loop.py act call-site hunk that R3 specifies; MASTER §2 should list R3 in loop.py's hunk column.
- **Give:**
  - Option rows add `ref` and `state` and remove nothing.
  - `act(model, per, focus, options, buttons, controls) -> Act(action, text, why, option)`.
  - `render_focus(limit)` keeps its signature; its content changes.
  - `view(goal_text, place)` loses `compact`.
  - The trace `option` adds `state`; watch_run reads no option keys.
- **contracts.md** (coordinator): the "notebook and act" act signature, with the free path removed. In "world" OM additions, the order becomes three state groups with the fixed order inside each; the name-list rows, `OPTION_CAP` and the compact view go. The 1,600-token act limit stays.

## 5. Scorecard effect and offline verification

- **S4** (15/99 counted on any note; the coordinator counts 14) → ≤ 2. This is R3's main metric.
- **S8 act** max 5.6k → ≤ 2.0k chars.
- **S9:** name follow-ups 11 → 0.
- **S10:** about −160 net lines.
- **S7** (1F to Pallet): partly, live only.
- **Offline, 0 calls:** over every step of an `eval_act` cached replay from checkpoints 0/25/50/75 on R1+R2+R4 code, check:
  - no two rows share a label;
  - no `failed` row precedes a `new` row;
  - no label lists names;
  - prompt ≤ 2,000 chars;
  - generality grep clean;
  - rows per step reported.
- **Model replay, 20 calls:** the new prompt, one call each, at the 15 re-pick steps, S36, S70 and 3 control steps where the old pick was right. Optional ablation, flat vs grouped on the same prompts: +20 calls, only if ordering is to be measured on its own (MASTER §4.8).

## 6. Risks and open questions for the coordinator

1. **Oscillation (§4.7).** OM (OM.md:59) and OM2 (OM2.md:157) ruled reordering by history a gate. What differs now:
   - history was keyed on churning names then, so ordering by it meant ordering by noise; after R1 it is true of the thing;
   - it is three fixed groups, not a preference score.

   MASTER §4.4 allows order as presentation. Does that supersede the OM rule, or should the user confirm? Revert if S4 does not move.
2. **Removing the free path changes the default for runs without `--decide`.** It follows the 10-14 options-only decision. Confirm.
3. **Menu length.** Expanded rows grow the menu (S70 is about 11-18 rows). The Pallet name-list row listed up to 36 names, which R1 dedup and name groups must shrink. Row counts are measured offline, and no cap is proposed.
4. **Dialogue sentence: not restored.** The S36/S38 replay (2 of the 20 calls) decides it. Add one line only if both still pick free buttons.
5. **`approach` → `check` (interact) for things** gives evidence on each visit, but may open harmless text boxes. Is that R3's call or R4's (skills)?
6. **Dropping other open quests from act** gives up opportunistic completion; the planner reorders on events (R2/R4).
7. **S4 is provable only live.** The replay is a proxy and needs R1's memory, and R3 lands last.
8. **Out of scope:** `brief.GENERAL` holds a top-down exit hint for the perceiver ("distinct floor patch … name that patch … as the exit"). Editing it invalidates the perception cache, so it is left for R1/R5.

**Phase A rulings (coordinator, 10-15):**
- Q2, Q4, Q6: approved.
- Q3: no cap; row counts are reported.
- Q5: the verb choice is R3's; skill behaviour stays with R4.
- Q8: becomes MASTER D1.
- Q1: grouping by history is approved by the user, and it supersedes OM rule 3 for RCR (MASTER §10). It is revert-on-S4: it stays its own WP (R3.6), so a revert is one WP.
- Interface changes against §4 above:
  - R2 has no `done_when`; the Goal line shows the quest's target label or nothing.
  - `Because:` shows only the cited verbatim game-text lines, no beliefs.
  - Explore rows come one per direction from R4's `World.explore_dirs`.
  - Actions carry refs.

---

# R3 Phase B: implementation packet

Status: **partial, gate failed 10-16; R3.2 passed after overseer fixes, final R3.4 one-test failure** · Branch `feat/lean-agent`

## Paste block

    You are the orchestrator for RCR section R3 (act prompt and option menu), repo
    C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent.
    1. Read, in order: CLAUDE.md, docs/plans/rcr/MASTER.md, docs/plans/handoff-notes.md, docs/plans/contracts.md
       ("notebook and act", "world"), then this file (docs/plans/rcr/R3-decide.md). Read only the files the WPs list,
       and use CodeGraph first.
    2. Check §1 entry criteria. If any fails, stop and report.
    3. Run the WPs in the run order below. Delegate each WP to a Haiku subagent (model "haiku", set explicitly;
       never Opus or Sonnet). Paste the CLAUDE.md rules (ponytail full, codegraph first, headroom) and the WP card
       into every prompt.
    4. After each WP, run its acceptance commands yourself. A WP is done only when they exit 0. Run the full
       suite (pytest tests/agent -n auto) after R3.4 and again at the gate.
    5. Build on top of the user's uncommitted live-view edits (MASTER §3) and preserve every one of their hunks.
       Each WP re-reads its files from the working tree right before editing. Edit only the files and hunks a WP
       owns. Never touch loop/, perceive.py, brief.GENERAL, run_agent.py or watch_run.py. Do not edit contracts.md;
       put the contract text in §9 for the coordinator.
    6. Phase C makes 0 cloud calls. R3.7 runs only in Phase D, after the user approves a ledger cap.
    7. Fill in §9 Result, update MASTER §8/§9 rows, rewrite handoff-notes.md (§10), then commit the §2 commit
       list plus docs/: "R3: <one-line gate result>". Never git add -A, never push. Never git add a file that
       still carries live-view hunks (marked LV in the list) unless the coordinator has asked the user. Then stop.

Every shell starts with: `New-Item -ItemType Directory -Force .tmp/rcr/r3/tmp | Out-Null; $env:TEMP = "$PWD\.tmp\rcr\r3\tmp"; $env:TMP = $env:TEMP`.
`PY` = `.venv-agent/Scripts/python.exe -B`. `PT` = `PY -m pytest -q -p no:cacheprovider -n auto`.
`LV` (the live-view tests) = `PT tests/agent/test_realtime_env.py tests/agent/test_observer.py tests/agent/test_track.py tests/agent/test_watch_run.py -k "test_live or realtime_env"`.

## 1. Entry criteria

- The R1, R4 and R2 gates have passed and their hunks have landed in `world.py`, `loop.py` and `notebook.py`. In particular these exist:
  - `World.history(ref)`, `World.ref(text)`, `World.place_label(id)`;
  - `World.explore_dirs(place)`;
  - `fails[intent] = {n, why, step, stage}`;
  - R2's notebook `text` section and quest `target`.
- The user's live-view work stays uncommitted, and R3 builds on top of it (user decision, MASTER §3). `LV` is green before R3.1 starts.
- `runs/m1_bedroom_live` and R1's frame-replay harness (`scripts/check_tracks.py`) are on disk.

## 2. Gate

| Metric | Pass | Command |
|---|---|---|
| unit suite | 0 failed | `PT tests/agent` |
| live-view tests | 0 failed | `LV` |
| S8 act prompt max over replayed steps | ≤ 2,000 chars | `PY scripts/check_tracks.py m1_bedroom_live --menu --first 1` |
| duplicate row labels / name-list rows / non-free rows with no action (S9 follow-ups) | 0 / 0 / 0 steps | same |
| rows per step (Q3) | reported, max and mean | same |
| with R3.6: a `failed` row before a `new` row | 0 steps | same |
| generality: no game words in R3's added lines | no output | bash: `git diff -U0 HEAD -- agent/ ':!agent/env_pyboy.py' \| grep '^+' \| grep -iE 'pok[eé]mon\|\boak\b\|\bmom\b\|\brug\b\|next door\|door ?mat'` |
| R3 net `agent/` lines | report only (plan ≈ −150) | `wc -l agent/*.py` before/after |

- **Commit list:**
  - `agent/act.py`, `agent/brief.py`, `agent/world.py`, `agent/notebook.py`;
  - `agent/loop.py` **(LV)**;
  - `scripts/eval_act.py`, `scripts/eval_frames.py`, `scripts/check_tracks.py`;
  - `tests/agent/test_act.py`, `test_options.py`, `test_exits.py`, `test_world.py`, `test_notebook.py`, `test_qs_e2e.py`, `test_om_eval.py`;
  - `docs/`.

  Before staging, any of these that `git diff` shows still carrying live-view hunks is also marked **(LV)**. Files marked LV are not staged unless the coordinator has asked the user.
- **This gate decides** whether R3 is ready for the post-R3 live run and the Phase D replay.
- **If it fails:**
  - prompt over 2k: report the section sizes. Do not cap rows (Q3).
  - duplicate labels: an identity bug. Report it to R1, and add no label patches.
  - an invariant broken on a few replayed steps only: list the steps in §9 and stop.

## 3. Read list

- `agent/act.py` (all), `agent/brief.py`, `agent/decide.py`
- `agent/world.py`: `options`, `_exits`, `view`, `here_flag`, `_notes`, `_tried`, `_yx`, `place_label`, `history`, `explore_dirs`, `resolve`
- `agent/notebook.py`: `render_focus`, plus R2's `text` and `quests` shapes
- `agent/loop.py`: the act call site (on 10-15 with the live-view edits, 163-194: `act_flags` up to the `act(` call) and the trace `act` row (265)
- `scripts/eval_act.py` call sites at 92, 219-231, 548, 646 and 813; `scripts/eval_frames.py` 208-210
- R1's `scripts/check_tracks.py` replay API
- `scripts/watch_run.py:379`, which reads `act.place`, `action`, `ops` (missing reads as empty) and `why`

Line numbers drift after R1, R4 and R2 land, and `loop.py` carries the live-view edits. Find each spot by symbol with CodeGraph, and re-read the file right before editing it.

## 4. Contracts

- **Uses:**
  - MASTER §7 rows R1→R3: refs, `history`, `ref`, `place_label`, no screen-edge exits.
  - MASTER §7 rows R2→R3: top quest, `target`, cited `text` lines.
  - MASTER §7 rows R4→R3: `fails`, `result`, `explore_dirs`.
- **Provides** (the text goes to §9 for the coordinator to merge):
  - `act(model, per, focus, options, buttons, controls) -> Act(action, text, why, option)`. One options-only path, with follow-ups only for free buttons and typing.
  - `World.options(per, place, fails, step, inputs=()) -> [{"n","tool","label","action","notes","intent_key","exit","ref","state"}]`. `state` is one of `new|worked|failed`, and every non-free row has an action.
  - `World.label(ref) -> str`.
  - `World.view(goal_text, place)` with no `compact`.
  - `Notebook.render_focus(label) -> str`.
  - The trace `option` adds `state`, and the trace `act` row keeps `place`, `action` and `why`.
  - Removed: `here_flag`, `OPTION_CAP`, name-list rows, the REPLY/free path, `Act.place/ops/expect`, `brief.defaults`/`DEFAULTS`.

## 5. Work packages

| WP | Goal | Owns, with planned net lines (report only) | Depends on | Tier |
|---|---|---|---|---|
| R3.1 | One options-only act path | `agent/act.py` (≈ −65), `agent/brief.py` (≈ −6), `tests/agent/test_act.py` | entry | Haiku |
| R3.2 | Row = thing | `agent/world.py` R3 hunks (≈ −40), `tests/agent/test_options.py`, `test_exits.py`, `test_world.py` (compact cases) | entry (R1, R4 world hunks) | Haiku |
| R3.3 | Bounded focus | `agent/notebook.py` `render_focus` (≈ −5; `render_log` deleted), the render_focus tests in `tests/agent/test_notebook.py` | entry (R2) | Haiku |
| R3.4 | Wire the call site and scripts | `agent/loop.py` R3 hunk (≈ −30), the act/options call sites in `scripts/eval_act.py` and `scripts/eval_frames.py`, the R3 hits in `tests/agent/test_qs_e2e.py` and `test_om_eval.py` | R3.1-R3.3 | Haiku |
| R3.5 | Offline menu check | a `--menu` mode in R1's `scripts/check_tracks.py` (≈ +60), plus a `--menu` case in `tests/agent/test_options.py` | R3.4; after R1's check_tracks edits (serial) | Haiku |
| R3.6 | State grouping (user-approved; revert if S4 is flat) | `agent/world.py` `options` sort (≈ +5), `agent/act.py` headers (≈ +5), tests | R3.5 | Haiku |
| R3.7 | Phase D model replay | `check_tracks.py --menu --live` (≈ +25) | the gate; user ledger cap | Haiku |

**Run order:** R3.1 ‖ R3.2 ‖ R3.3 → R3.4 → full suite → R3.5 → R3.6 → gate and commit. R3.7 runs in Phase D.

**Every WP:**
- re-reads its files from the working tree before editing;
- preserves every live-view hunk;
- lists `LV` in its acceptance, after the commands the card gives, and `LV` must be green.

### R3.1: One options-only act path
- **Change:**
  - `act()` becomes today's `_decide`, with signature `act(model, per, focus, options, buttons, controls)`. The prompt is `focus`, then `Screen: …`, `Text on screen: …`, then the rows as `n. label — notes`, then the `{why, i}` reply line.
  - `SYSTEM` is 4 sentences (§2 mock). Delete `SYSTEM`/`REPLY`/`OPTION_NOTE`/`REPLY_OPTIONS`/`SCHEMA`, `_pick`, `_action`, `_option_text`, the free body, the name-list follow-up, the `EFFECTS`/`for_model` imports, and `Act.place/ops/expect`.
  - Keep `_ask_valid` (one retry naming the reason) and the buttons and type follow-ups. Typing gets no prefs.
  - In `brief.py`, delete `DEFAULTS` and `defaults()`. `GENERAL` stays (D1).
- **Acceptance:** `PT tests/agent/test_act.py`. The new tests:
  - the prompt holds focus, screen and rows, and none of `Flags`, `Known facts`, `Map:`, `Other open quests`;
  - `{"i": n}` and `"n"` return a copy of row n's action;
  - invalid → one retry → None;
  - a buttons row gets an enum follow-up with controls;
  - a type row gets a text follow-up of ≤ 40 chars;
  - `Act` has exactly `action, text, why, option`;
  - no game words in `SYSTEM`.
- **Risk:** scripts break until R3.4. That is expected; the full suite runs after R3.4.

### R3.2: Row = thing
- **Change:** this hunk lands after R1's `history` and R4's `explore_dirs`.
  - Add `_away(pos, tile)`, returning `"1 left, 6 down" | "here"` (moved from `view`), and `World.label(ref)`, which is `history(ref)["called"]`, else the name, else `place_label`.
  - Rebuild the navigable branch of `options` in the fixed order: exits, actors, things, explore, places, free. Within that order, sort by `_yx`.
- **Rows:**
  - exit: `go to <name>, <where>`, or `go through <name>, <where>` when `uses`. Notes: `crossed N× → <place>` plus the beyond suffix, `exit, never crossed`, or the failure.
  - actor: one `talk to <name>, <where>` (interact).
  - thing: `check <name>, <where>` (interact). Same-name things with no history and no `fails` share one row `check <name> (nearest of N), <where>` that acts on the nearest member.
  - remembered thing: `go to <name>, not on screen, <where>`, with action `{"go_to": ref}`.
  - explore: one row per `explore_dirs` key, `explore <DIR>, <len(route)> steps to unseen ground`. The action is `{"go_to": "unexplored", "dir": d}`; the coordinator confirmed it, and R4's dispatch reads `dir`.
  - place: one row each, `go to <place>, via <first exit>`.
  - free buttons: last. Its note has the blocked directions and the failed combos (which replaces `here_flag`).
  - modal branch: the rows stay as they are and gain `ref` and `state`.
- **State and notes, per row:**
  - `state = failed` if `fails[intent_key]` (a success clears it, per R4) or the exit status is refuted;
  - else `worked` if `history` has talked, checked or uses;
  - else `new`.
  - Notes give the evidence with steps: `talked step S: "<said ≤60>"`, `checked step S: nothing`, or `<why> N× (last step S)`.
- **Delete:**
  - `OPTION_CAP`, the quiet name-list and places-collapse rows, and the actor approach row;
  - `here_flag`;
  - `view`'s `compact` parameter and its branches;
  - `_tried`, and the `label` key of `_exits` (`view`'s `exit_text` builds its own text).
- **Acceptance:** `PT tests/agent/test_options.py tests/agent/test_exits.py tests/agent/test_world.py`. The new tests:
  - two same-name exit tiles → two rows with different labels;
  - 8 plain things, a same-name pair and 1 actor → no non-free row without an action, the pair is one `(nearest of 2)` row, and the actor has exactly one row;
  - a `fails` entry → that row is `failed` with its count and why;
  - a talked history → `worked` with the said text;
  - stubbed `explore_dirs` with 2 keys → 2 explore rows;
  - free is last and its note names the blocked directions;
  - every non-free row has a `ref`, and every row has a `state`.
- **Risk:** R1's `history` field names. Read R1's packet and `contracts.md` first, and map any field change in one place (`label`, state helper).

### R3.3: Bounded focus
- **Change:** `render_focus(label)` returns:
  - `Goal: <top quest text>`, plus `(done when: talked to <label(thing)>)` or `(done when: in <label(place)>)` when it has a target; `Goal: (none)` when there is no open quest;
  - `Because:` the quest's cited `text` lines, verbatim, as `<speaker>, step N: "<text>"`, each ≤ 160 chars, at most 3 (a knob);
  - `Recent:` the last 4 log lines, each ≤ 120 chars.

  Nothing else: no other quests, beliefs, notes or planner `why`. Delete `render_log` (dead).
- **Acceptance:** `PT tests/agent/test_notebook.py -k focus`. The tests:
  - a target is rendered through a stub label;
  - only cited lines appear, truncated;
  - no belief or note text appears even when present;
  - Recent has 4 lines, truncated;
  - an empty notebook renders `Goal: (none)`.

### R3.4: Wire the call site and scripts
- **Change, in `loop.py` (R3's hunk, last in MASTER §2 order):**
  - Delete the `act_flags` assembly: cycle, red-box, recent summary and `here_flag`.
  - The act call becomes `act(self.model, per, f"{self.notebook.render_focus(self.world.label)}\nHere: {self.world.place_label(place)}", options, list(self.env.inputs()), controls)`.
  - Delete `applied`, `places=`, `model_frame` and the `defaults` import.
  - The trace `act` row keeps `place` (the canonical label), `action` and `why`, and drops `ops` and `flags`; `watch_run` reads a missing `ops` as empty.
  - `Loop(decide=, act_text=)` stay as accepted no-ops (run_agent is frozen).
- **Change, in the scripts:**
  - `eval_act.py` and `eval_frames.py` call the new signature.
  - Delete `--arm`/`DECIDE` and any mode that only exercised the free or image path. List each deletion in §9.
  - `eval_plan.py:173` is R2's (they shrink that script).
- **Acceptance:**
  - `PT tests/agent`;
  - `PY scripts/eval_act.py --help` and `PY scripts/eval_frames.py --help` exit 0;
  - `git grep -nE "act_flags|here_flag|compact=True|defaults\(" -- agent/loop.py` has no output.

### R3.5: Offline menu check (0 requests)
- **Change:** a `--menu` mode in R1's `scripts/check_tracks.py`: `check_tracks.py RUN --menu [--first 1] [--steps N] [--only a,b,…] [--dump DIR]`. It is edited after R1's own check_tracks hunks (serial) and adds no new script.
  - Rebuild `World` for each recorded step with check_tracks' existing replay loop.
  - Fill `fails` from the trace by R4's rule: `failed`/`no_change` adds 1, any other result clears that intent. The recorded action maps to a ref through the step's on-screen ids, or `World.ref(name)` for `go_to`.
  - The notebook at each step comes from R2's `scripts.eval_plan.notebook_at(run, step)`, the one builder.
  - Build the options and the exact act prompt (a capture model, no HTTP), then print one line per step: `step rows dup namelist chars order`.
  - Exit 1 on any §2 invariant failure. `--dump` writes the prompts for the R3.7 steps to `.tmp/rcr/r3/prompts/<step>.txt`.
- **Acceptance:**
  - `PY scripts/check_tracks.py m1_bedroom_live --menu --first 1 --dump .tmp/rcr/r3/prompts` exits 0;
  - `PT tests/agent/test_options.py -k menu` passes (the `--menu` check on one synthetic 3-step trace).
- **Risk:** the replay can drift from the recording where R1 changes ids. The script reports the steps it covered; fewer than 80 of 99 is a §9 issue, not a pass.

### R3.6: State grouping (user-approved 10-15; MASTER §10; revert if S4 does not move)
- **Change:**
  - In `options`, stable-sort the rows by state (`new`, `worked`, `failed`) before numbering, with free buttons kept last.
  - `act` prints `Not tried here:` / `Worked here:` / `Failed here:` headers where the state changes, numbered straight through.
- **Tests:** no `failed` row precedes a `new` row; the numbers are contiguous; free is last. `--menu` turns on its `order` check.
- **Revert = restore the fixed order:** drop this WP's sort and headers.

### R3.7: Phase D model replay (needs the user's ledger cap: 20 requests)
- **Change:** `check_tracks.py RUN --menu --live --cap 20` sends each dumped prompt once through the cached model (role `act`). It refuses when the cap is exceeded, and counts retries as requests.
- **Steps:**
  - the 15 re-pick steps 38, 49, 50, 70-73, 75, 79, 82, 84-86, 88, 103;
  - S36;
  - 4 controls with a right recorded pick: S35 (advance), S58 (talk), S74 (door mat), and one Pallet step from the act table.
- **Scores:**
  - S4 proxy: a `failed` pick while a `new` row exists, target ≤ 2 of 15;
  - S36/S38 free-button picks: if both still fail, restore the one dialogue line (Q4) and re-send those 2 only;
  - the controls stay right.
- The ablation (flat vs grouped, +20) runs only if the S4 proxy misses.

## 6. Test layers

| Layer | Gates R3? | Command |
|---|---|---|
| Unit (StubModel, synthetic memory) | yes | `PT tests/agent` |
| Offline replay (recorded frames and perception, 0 requests) | yes | `PY scripts/check_tracks.py m1_bedroom_live --menu --first 1` |
| Model replay (20 requests) | Phase D | `PY scripts/check_tracks.py m1_bedroom_live --menu --live --cap 20` |
| Live run (after R3) | Phase D, user caps | MASTER §6 |

## 7. Budgets

- **Lines (planned, report only; not a pass condition):**
  - act.py ≈ −65;
  - brief.py ≈ −6;
  - world.py ≈ −40;
  - notebook.py render_focus ≈ −5;
  - loop.py ≈ −30.

  That is about −150 in `agent/`. The check_tracks `--menu` mode (≈ +85) is in `scripts/`.
- **Cloud calls:** Phase C 0. Phase D 20, plus 2 for the dialogue line, plus 20 for the ablation only if needed.

## 8. Out of scope

- Perceiver text and `brief.GENERAL` (D1).
- Skill behaviour (interact, explore walker, exit push): R4.
- The quest data model and planner: R2.
- Identity and exit truth: R1.
- `watch_run.py` (frozen).
- Pruning the old OM modes of `eval_act.py` beyond the free-path ones (a later cleanup proposal).

## 9. Result (filled in by the orchestrator)

**Latest state:** R3.2 passed 148/148 after external fixes. Resumed R3.4 final full suite: **608 passed, 1 failed**, retry allowance exhausted; see resumed evidence below. The initial stopped attempt is retained as history.

### Initial attempt (historical)

- **Gate: FAIL; stopped 2026-10-16.** Starting branch `feat/lean-agent`, HEAD `dc4113f`, working tree/index clean. R1/R4/R2 gates and required interfaces were present; live-view commit `8fbe5eb` was in history. The user's amendment supersedes the packet's old uncommitted-LV entry/staging condition.
- **Tier/attempt limit:** every spawn explicitly used **gpt-6-luna, reasoning high** (R3.1, R3.2, R3.3, R3.4, read-only R3.5 prep). R3.2 failed twice and received exactly one extra same-tier attempt with the measured failure output pasted into its prompt. It failed again. R3.4/R3.5 were interrupted; no further source repair, tier escalation, staging, commit, push, or automatic revert.

### Acceptance evidence

PY/PT/LV are the exact commands defined in this packet; shell commands used its TEMP/TMP prefix after it was read. All log names below are under `.tmp/rcr/r3/`.

- Entry **LV: 20 passed**, exit **0**, 3.19 s (`entry-lv.log`).
- R3.1 `PT tests/agent/test_act.py`: **6 passed**, exit **0**, 1.83 s (`r3_1.log`); LV **20 passed**, exit **0**, 3.13 s (`r3_1-lv.log`).
- R3.3 `PT tests/agent/test_notebook.py -k focus`: **4 passed**, exit **0**, 1.85 s (`r3_3.log`); LV **20 passed**, exit **0**, 3.46 s (`r3_3-lv.log`).
- R3.2 first `PT tests/agent/test_options.py tests/agent/test_exits.py tests/agent/test_world.py`: **100 passed, 3 failed, 2 collection errors**, exit **1**, 8.12 s (`r3_2.log`). Loop imported deleted `brief.defaults`; test-options imports then caused cascading fixture import failures. LV **20 passed**, exit **0**, 3.55 s (`r3_2-lv.log`).
- Dependency repair: R3.4 applied its owned loop import/call-site hunk before R3.2 could pass; no compatibility shim. Same R3.2 command: **136 passed, 12 failed**, exit **1**, 11.63 s (`r3_2-after-wire.log`). Failures included stale tests, an old act capture, and a place-row lookup reading `exit_tile` from a final route leg without an exit.
- R3.2 extra final same-tier attempt, same command: **143 passed, 5 failed**, exit **1**, 12.55 s (`r3_2-final.log`). Final LV **20 passed**, exit **0**, 3.58 s (`r3_2-final-lv.log`). No further repair attempted.
- Full suite, R3.4 help/grep acceptance, R3.5 menu replay, R3.6 grouping and gate were **not completed/run** after the final-attempt stop. R3.4 is partial and unaccepted. A read-only scan finds an unresolved `DECIDE` use at `scripts/eval_act.py:405` after its definition was removed.
- Baseline-only `PY scripts/eval_run.py runs/m1_bedroom_live --no-write`: exit **0** (`baseline-eval.log`). This scores the old recording, not the changed R3 code.

### Five remaining measured failures

- `tests/agent/test_exits.py:107`, `test_unverified_exit_with_another_known_destination_next_to_a_verified_one_stays_separate`: calls `.startswith` on free-buttons `ref=None`.
- `tests/agent/test_options.py:112`, `test_room_lists_exits_things_explore_and_free_input_in_rule_order_with_notes`: expects an `exit:` go-to target, receives `place:cave`.
- `tests/agent/test_options.py:196`, `test_two_room_exit_counts_only_departure_crossings`: expected `go through RIGHT` / `go through LEFT` labels absent in its direct two-place menu check.
- `tests/agent/test_options.py:120`, `test_on_screen_exit_that_is_also_a_tile_exit_appears_once`: calls `.startswith` on free-buttons `ref=None`.
- `tests/agent/test_world.py:629`, `test_loop_act_uses_world_facts_active_quests_and_labels`: partially migrated test still asserts removed `seen["places"]`.

### MASTER §5 metrics and Phase D

- **S1, S2, S3:** no post-R3 identity replay/live measurements.
- **S4:** unmeasured; no model replay/live failed-option score. **R3.6 was never applied**, so no grouping keep/revert decision.
- **S5, S6, S7:** no post-R3 closure/failure/transition measurements.
- **S8:** replay act/planner prompt maxima unmeasured; `--menu` is not implemented.
- **S9:** replay rows/max/mean, duplicates, name lists, missing actions, ordering and follow-up counts unmeasured; replay coverage **0**. Live calls per new tile unmeasured.
- **S10:** partial `agent/*.py` lines **4,063 → 3,988**, net **−75**, report-only, not a gate pass (`final-audit.log`, `git diff --numstat`).
- **S11, S12, S13, S14:** no post-R3 unsettled/reply-size/targeted-done/finished-target-re-add measurements.
- **0 agent cloud calls.** R3.7 `--cap 20`, conditional +2/+20 fallbacks, and the clean-boot **150-step / 400-call** live run were **not launched** because the gate failed. `runs/rcr_r3_live` does not exist.
- Ledger unchanged: **4,027 total / 3,977 M1 / 50 smoke**, **788** remaining under TOTAL **4,815** / M1 **4,765**. Viewer `/state` returned HTTP **200**. Smoke is exhausted; the previous funded-M1 launcher approach would be needed for the requested non-m1 output path. No launcher was created or used.

### Scope, deletions and preservation

- Agent net lines: `act.py −43`, `brief.py −7`, `loop.py −29`, `notebook.py +1`, `world.py +3`; sum **−75**.
- Other net lines: `scripts/eval_act.py −20`, `scripts/eval_frames.py −3`; tests `test_act −150`, `test_exits +11`, `test_harness_reliability +8`, `test_loop −111`, `test_notebook +27`, `test_om_eval 0`, `test_options −46`, `test_qs_e2e 0`, `test_trace_brief −4`, `test_world −65`; plus Result/MASTER/handoff docs.
- Coordinator-only necessary caller migrations outside worker cards: `test_loop.py` (explicit user amendment), `test_trace_brief.py` (retired defaults), `test_harness_reliability.py` (act/focus/menu callers). These changes lack full-suite acceptance. Workers edited only their card files.
- Partial R3.4 removes eval_act's non-OM free/image replay mode and `--frames`, `--max-calls`, `--out`, `--arm options|decide`; deletes its DECIDE definition/arm wiring; removes `eval_frames --arm image|text` and migrates evaluation to options. Interrupted and unvalidated; do not treat as complete cleanup.
- R3.5 only prepared read-only; `scripts/check_tracks.py` unchanged. R3.6/R3.7 unimplemented; no dumped prompts.
- Final audit (`final-audit.log`): **0** prohibited-word matches in added agent lines (env_pyboy excluded); **10/10** protected SHA-256 hashes unchanged; **4/4** loop live-runtime methods and **2/2** test-loop live functions unchanged from HEAD. Exact LV **20/20** green.
- `brief.GENERAL`, `perceive.py`, `loop/`, `run_agent.py`, `watch_run.py`, and `contracts.md` unchanged.
- Oscillation check: `git log -S compact -- agent/world.py` points to OM3/OM3.1 (`a044f0a`, `e6fc5fb`); `git log -S DECIDE_SYSTEM -- agent/act.py` points to `a2d925e`. R3 proposes per-ref row evidence and bounded context; no measured improvement is claimed.
- **Every partial change remains unstaged; index empty; no gate commit/push.** No initially uncommitted user LV hunks existed; no additional file was marked LV. The amendment permits R3-only loop/test-loop staging at a future passing gate, but this failed gate commits nothing.

### Provisional contract text for the coordinator

Do not merge this as a passed contract.

- `act(model, per, focus, options, buttons, controls) -> Act(action, text, why, option)`: options-only with buttons/type follow-ups; R3.1 isolated tests pass.
- `Notebook.render_focus(label)`: top goal/target condition, up to 3 cited lines of 160 chars and 4 log lines of 120 chars; R3.3 isolated tests pass.
- `World.label(ref)`, `World.options(per, place, fails, step, inputs=())` exist; rows carry ref/state including synthetic modal/explore refs. Menu/history/exit invariants remain unaccepted.
- `World.view(goal_text, place)` drops compact. Partial diff removes here_flag, OPTION_CAP, name lists, old free parsing, Act.place/ops/expect, brief.defaults/DEFAULTS and loop act flags. Trace act/option wiring is partial R3.4 work. `contracts.md` untouched.

**Open issues:** retry allowance exhausted; stop here. A later user-authorized continuation must address the five failures and interrupted R3.4, then full suite and remaining gates before Phase D. Grouping's revert-on-S4 rule and pre-approved Phase D caps remain in force, unexercised.

### Resumed after overseer fixes (10-16): R3.2 passed; R3.4 still fails one test

- User authorized continuation and resuming interrupted workers. Overseer fixed place-scoped World.label(ref, place=None), exit-label call sites, four stale assertions, and the remaining DECIDE use. R3.2 was independently reaccepted: PY -m pytest -q -p no:cacheprovider -n auto tests/agent/test_options.py tests/agent/test_exits.py tests/agent/test_world.py → **148 passed**, exit **0**, **11.96 s** (.tmp/rcr/r3/resume-r3_2.log). This supersedes the five failures in the initial-attempt section above.
- Existing R3.4/R3.5 workers resumed at their explicitly spawned **gpt-6-luna / reasoning high** tier. R3.4 had one usage-limit interruption and was resumed at the user's explicit instruction. Workers stayed within their card files; R3.5 remained read-only because R3.4 never passed.
- R3.4 corrected the remaining live snapshot access to removed Act.place: _live now writes self.world.place_label(place). Root full suite PT tests/agent → **24 failed / 587 passed**, exit **1**, **83.80 s** (resume-full-1.log). Last completed exact LV → **20 passed**, exit **0**, **15.12 s** (resume-lv-1.log).
- One coherent repair round migrated old reply/schema/call-site test contracts and remaining owned replay producers. Two tests of the deleted non-OM free/image CLI mode and their unused fixtures were removed from test_eval_act.py; its reliability fake uses the six-argument act signature. Transition/routing unit tests script lower-level choices rather than answering a free-buttons follow-up with a perception. Failure-note tests copy actual option actions/numbers so counters use the offered intent key.
- Authorized golden regeneration: verified target C:/Users/Abj1/Documents/GitHub/Avatar/tests/agent/data/w0_golden/act; W0_REGEN=1 PT tests/agent/test_world_golden.py → **1 passed**, exit **0**, **14.38 s** (resume-golden.log). Its scripted eight-step episode now uses numbered choices, the buttons follow-up and two invalid replies on each observation-only step: **8 steps / 15 model asks**. Exactly **8 trace rows** changed; memory fixtures unchanged. W0_REGEN was removed afterward.
- Both PY scripts/eval_act.py --help and PY scripts/eval_frames.py --help exited **0**; git grep -nE "act_flags|here_flag|compact=True|defaults\(" -- agent/loop.py had **0 matches**. Logs: resume-eval-act-help.log and resume-eval-frames-help.log.
- Root full suite after repair: **1 failed / 608 passed**, exit **1**, **73.29 s** (resume-full-2.log). Remaining fixture assumed route['dirs'] even when check/interact supplies no route.
- **Extra final same-tier attempt used:** measured failure output was pasted into the gpt-6-luna/high worker's prompt after the two failed suite acceptances. Worker confirmed the unowned fixture mismatch and recommended reusing fail_affordance in that single test; root applied the coordinator-owned change. No worker edited an unowned file.
- Final PT tests/agent: **1 failed / 608 passed**, exit **1**, **110.80 s** (resume-full-final.log). At tests/agent/test_loop.py:1488, test_chooser_rows_show_failure_evidence asserts '"cause:" not in focus'; the required **Because:** heading contains that substring. The preceding failed-row/route-blocked evidence assertion now passes. This is a test assertion introduced by the coordinator, not evidence of a production failure. No further correction was applied after the final-attempt limit.
- 10-16 Haiku finish: test_loop.py:1488 now checks no focus line starts with `cause:`; `act.py` `_ask_valid` initialises `reason`; `eval_frames.py` `_point_for` drops the undefined `affs` lookup (NameError since HEAD). Full suite **609 passed, 0 failed**; LV **20/20**; eval_act/eval_frames `--help` exit 0; generality grep empty; agent net **−74**. R3.5, R3.6, gate and Phase D still not run; nothing staged.
- A concurrent final exact LV rerun did not complete beyond worker startup; it was interrupted (exit **1**, no test result; resume-lv-final.log). Do not call this rerun a pass. The latest completed exact LV is **20/20** above, and the final full suite's only failure is the unrelated chooser assertion.
- **Current gate: FAIL.** R3.2 is passed, R3.4 has the one remaining assertion failure; R3.5–R3.7 and gate commit remain uncompleted. No grouping was applied; S4 keep/revert is unexercised. No model replay/fallback/live run; **0 cloud calls**. Post-R3 S1–S9 and S11–S14 remain unavailable; replay coverage **0**.
- **S10 unchanged:** agent **4063 → 3988**, net **−75**. Current full-tree net lines: act −43, brief −7, loop −29, notebook +1, world +3; eval_act −2, eval_frames −3; tests test_act −150, test_eval_act −38, test_exits +11, test_harness_reliability +8, test_loop −92, test_notebook +27, test_om_eval +1, test_options −46, test_qs_e2e 0, test_trace_brief −4, test_world −66, test_world_golden +10; golden trace net 0.
- Final audit resume-final-audit.log: **10/10** protected file hashes unchanged; **3/4** live loop methods unchanged, with _live changed only for the required canonical place label; **2/2** test-loop live functions unchanged; prohibited-word matches **0**. Ledger unchanged **4027 total / 3977 M1 / 50 smoke**, **788** calls remaining. runs/rcr_r3_live absent. All changes remain **unstaged**, index empty; no commit/push.
- Minimal future test correction would check complete lines for a cause: prefix instead of a substring inside Because:. It is recorded only, not applied. Stop after this final permitted attempt; any later continuation requires a new user instruction. Contracts remain provisional; contracts.md and frozen runtime/perception files are untouched.

### Gate run (10-16, after R3.5 and R3.6): FAIL on S8 and duplicate labels

- R3.5 `--menu` in `scripts/check_tracks.py` (≈ +58): replays every step, rebuilds options and the exact act prompt with a capture model (0 requests). Fix after review: `menu()` now registers each place the way `_default` does; without it `World._tiles` returned nothing and every row read "here".
- R3.6 grouping applied: `World.options` stable-sorts world rows new → worked → failed (free last, modal untouched); `act` prints `Not tried here:` / `Worked here:` / `Failed here:` on world screens. The same WP fixed a crash: tied sort keys compared row dicts (`TypeError`, steps 65-74); every `(key, row)` sort in `options` now uses `key=lambda t: t[0]`.
- Unit `PT tests/agent`: **613 passed, 0 failed** (`gate-full.log`). LV **20/20** (`gate-lv.log`). Generality grep: no output. Agent net lines **−59** (report only).
- `PY scripts/check_tracks.py m1_bedroom_live --menu --first 1 --dump .tmp/rcr/r3/prompts`: exit **1** (`gate-menu.log`). Steps **105/105**, rows max **41** / mean **10.4**, name lists **0**, rows without action **0**, crashes **0**, failed-before-new **0**.
  - **S8 FAIL:** max prompt **2,320** chars (> 2,000) at step 98; steps 96-100 in place 4 are over. Step 98: options 1,890 chars over 40 rows; 9 of them are fence-post rows (437 chars). No row cap applied (Q3).
  - **Duplicates FAIL:** **68** duplicate labels on steps 77-100, all fence posts: two remembered entity ids on one tile (e.g. step 98 rows 28/29 "go to fence post, not on screen, 1 right, 1 up"). Per §2 this is an identity bug for R1; no label patch.
  - The old S65-74 window twins are gone once tiles exist (three windows get distinct positions).
- Open: (1) R1 owns one id per tile for remembered things; that alone brings step 98 to ≈1,880 chars. (2) Off-screen remembered things are one row each, while on-screen plain things group by name ("nearest of N"); grouping remembered rows the same way is an R3 design choice for the coordinator. (3) Step 65 lists the stairs twice: "go to stairs" (exit) and "check exit: stairs" (thing). (4) The trace has no `failed`/`no_change` verdicts, so the replayed `fails` stay empty and S4 is not measured offline.
- Nothing staged or committed; 0 cloud calls; Phase D not started.
- **Re-gate baseline (coordinator, 10-19):** `runs/m1_bedroom_live` was lost (deleted 10-16 through a worktree junction). The re-gate runs on `runs/m1_rcr_baseline` (74 steps, HEAD dc4113f): `PY scripts/check_tracks.py m1_rcr_baseline --menu --first 1`. Coverage bar scaled from 80/99 (81%) to **at least 60 of 74 steps**; full coverage is not required, since ordinary replay divergence is not a gate failure. Hold until R1 (memory.py, world.py) and R2 (notebook.py, plan.py) land.

### Re-gate on m1_rcr_baseline (10-19, after R1 and R2 amendments): FAIL on S8 and duplicates

- Unit `PT tests/agent`: **611 passed, 1 skipped** (`gate3-full.log`). The skip is `test_observer.py:160`, whose `runs/m1_bedroom_live/checkpoint/000100` was lost. It is not re-pointed: the baseline's checkpoints are 0, 25 (bedroom, text box open) and 50, and none reproduces "3× DOWN reaches y ≥ 10". S11 passed at R4's gate. LV **20/20**. Generality grep: no output. Agent net **−42** (report only).
- `PY scripts/check_tracks.py m1_rcr_baseline --menu --first 1`: exit **1** (`gate3-menu.log`). Coverage **74/74** (bar 60). Rows max **36**, mean **9.4**. Name lists, rows without an action, crashes and failed-before-new are all **0**.
  - **S8 FAIL:** max prompt **2,180** at step 70; steps 53-57, 59 and 70-72 exceed 2,000. Step 70 (2,251 chars with the header) is 1,727 chars of options over 36 rows, of which **18 are off-screen "go to X, not on screen" rows (878 chars)**; there are 6 on-screen check/talk rows. Off-screen remembered things are one row each, while on-screen plain things group by name ("nearest of N"). No row cap applied (Q3).
  - **Duplicates FAIL (4):**
    - Step 55: "check shelf, 2 left, 3 up" ×2, two ids on one tile (R1).
    - Step 69: "go to exit: door, here" ×2; every row on that step reads "here", so no thing has a tile in that place (R1's "unplaced" place, or no tiles in a non-map place). Two exits with no tile get the same label.
- Not committed: the packet commits only at a passing gate. Decision needed from the coordinator.

### Final gate on m1_rcr_baseline (10-19, coordinator decision (b)): PASS

- R1 fixed the two duplicates: one static thing per tile per frame; tile-less identical texts are one thing; `World._where(tile)` replaces the "here" fallback; `menu()` promotes a recorded id once a map frame shows it.
- R3 applied the approved off-screen grouping in `World.options`. Remembered off-screen things with no history and no failure group by label into one row, "go to X (nearest of N), not on screen, …", which resolves to the nearest member. A thing with history or a failure keeps its own row and notes. No row cap. Test: `test_off_screen_things_with_one_name_are_one_row_to_the_nearest`.
- Unit `PT tests/agent`: **612 passed, 1 skipped** (observer:160, recorded above). LV **20/20**. Generality grep: no output. Agent net **−23** (report only; plan ≈ −150).
- `PY scripts/check_tracks.py m1_rcr_baseline --menu --first 1`: **exit 0** (`gate4-menu.log`).
  - Coverage **74/74** (bar 60). Rows max **29**, mean **10.1**.
  - **Max prompt 1,909** (step 57; steps 59 and 72 at 1,906): **S8 ≤ 2,000 PASS**.
  - Duplicates, name lists, rows without an action, crashes and failed-before-new are all **0**.
- **Gate PASS.** Committed the shared tree (R1, R2 and R3 amendments plus docs). Next: Phase D (R3.7 `--menu --live --cap 20`) needs the user's ledger cap; S4 (failed re-picks) is unmeasured offline because the baseline trace has no failed verdicts.

## 10. Handoff notes

Rewrite `docs/plans/handoff-notes.md` per `_template.md` §10: under 80 lines, fixed headings, one fact per bullet with a number, a command or a `file:line`.
