# RCR R2: Quest state and planner contract

Status: Phase A approved 10-15 · Phase B packet written (below), not started · Branch: feat/lean-agent · Evidence: `runs/m1_bedroom_live`

## 1. Mechanism: the missing invariant

**Missing invariant: the notebook is not the fold of the planner's accepted writes, and the planner never learns which writes did not apply.** Everything else in R2 follows from that.

- **Restatement plus silent reconcile.** Each reply re-sends the whole quest list. `apply_plan` (`notebook.py:154-268`) merges it against old state and has 9 ways to refuse quietly: merge by text (176-180), duplicate key, `_TACTICAL` and button words (185), `_EXPLORE` (188), check swaps (198-211), done/dropped without evidence (212-215), dropping a heard quest without a reference (216-219), check not passed (220-223), cap parking by list position (237-243). Omission keeps the quest (230-236).
- **A refused write keeps the old quest.** A rejected restatement is not in `incoming`, so the omission path (230-236) re-appends the old quest. The S55, S64 and S76 plans all flagged "rejected explore_place_2: exploring is not a quest", and the quest stayed open as the 4th of 5 until the end. The filter's only effect was to freeze the quest.
- **Refusals barely reach the planner.** Flags come back as one `reconcile flags:` line of at most 80 chars, inside the 600-char evidence packet (`loop.py:541-552`).
  - Plans 1-3 got no flag line. Plans 4-6 got one flag each, out of 3-4.
  - The "rejected …" flags never reached the planner.
  - "omitted find_exit_place_2: keep, park or drop it with a reason" reached it twice. The planner omitted the quest again and wrote "dropped" in its note. The quest stayed open, and act saw both (S70).
  - The same flags repeat in every plan from S55 to S99.
- **Closure is string matching on names that churn.** reach compares place names within edit distance 1 (`notebook.py:399-404`), but the quests' args are "place 2/3/4", which memory refuses as names (`memory.py:35-47`), so they can never pass. speak/text run regexes over log strings (405-411). No op closes a quest. Final state: 5 open plus 1 parked, 0 closed in 105 steps. `explore_place_3` was opened at S55 *inside* place 3, with `reach place 3`.
- **Game text is paraphrased by the planner.** Facts and quest text are planner-written (`_apply_facts` 302-321). The fact kept "MOM says PROF. OAK, next door, is looking for you." The quest became "Find and speak with PROF. OAK" with `speak PROF. OAK`, and only the quest text reaches act as the goal.
- **Context is history-shaped.** `render()` (511-530) holds every fact, belief, quest, the notes and the chapters, plus a 40-entry log span (`plan.py:121`), then trims by characters. The prompt grew from 1.6k to 10k chars and replies from 1k to 4k, because each one restates that state. At S99 the user prompt was 10,084 chars. The notebook state was 5,680 of them: notes 2,050, quests 1,494, facts 1,055, beliefs 1,045. The log span added 1,225, the map 2,002 (World) and the reply schema 676. The S99 reply was 3,956 chars with `quests_changed: 0`: a full restatement that changed nothing. Across the 6 replies:
- 13 of 19 quest objects restated the prior state unchanged (69% of quest bytes);
- 0 used done, dropped or parked;
- 4 notes (S55, S64, S76, S99) claimed `find_exit_place_2` closed or dropped.

Reach checks also need `entered > opened`, so a quest opened just after its place was entered (S55, S76) cannot pass.
- **Word filters cannot hold against paraphrase.** `_EXPLORE` (1f8118a) was evaded in its first run ("Determine the nature of place 3", S55; "place 4", S76). `_TACTICAL` names labels (door, mat, rug), which guardrail 1 forbids. The S41-54 object tour obeyed a top quest ("Investigate the objects in place 2") that nothing could close or remove.

## 2. Solution: ops in, results out, ids for closure, verbatim text, count caps

**Invariant:** `notebook = fold(applied ops)`. Each op returns `ok` or `error: <why>`, and the results are shown first in the next planner prompt. Closure and "already done" are id events, never string matches. Game text is stored verbatim by code. Every prompt section has a count cap.

**Data (notebook.json v3).** Code-owned except the fields the ops set:
- `text`: verbatim game lines `{key:"t<n>", step, speaker, place: <place id>, text}`. Written by code from `_flush_conversation` and world-screen text (`Notebook.heard`). Never planner-written. Replaces planner `facts` and the dialogue half of `log`.
- `quests`: `{key, text, why:[t-key|b-key], target:{"reach":<place id>}|{"talk":<entity id>}|null, status: open|done|dropped, opened, closed, reason}`. List order is priority, and the first open quest is the focus. At most 5 open.
- `beliefs`: `{key, text, because:[keys], status: held|refuted, reason}`. At most 5 held.
- `last`: the op results of the latest reply, plus that reply's `why` (at most 200 chars).

**Planner reply** `{"ops":[...], "why": str}`, at most 8 ops. An empty `ops` means "no change" and is valid. Ops apply in order, so `drop` then `add` works in one reply:

| op | fields | error when |
|---|---|---|
| `add` | key, text, why, reach\|talk? | the key is already used; 5 open; `why` cites an unknown key; the target id is unknown to memory |
| `done` / `drop` | key, reason | the key is not open; `reason` is empty |
| `top` | key | the key is not open |
| `target` | key, reach\|talk | the key is not open; the id is unknown |
| `believe` / `refute` | key, text+because / reason | the key is used / not held; 5 held |

Errors are validation only, not judgement: the planner decides what to add, close, drop or reorder. No evidence, reference or check is needed to close or drop. The reply cannot restate, so it stays small.

**Closure from ids (push, no parsing).** After each settled step, Loop calls `notebook.observe(step, place=<canon place id>, talked=[<canon entity ids that spoke this step>]) -> closed keys`. An open quest whose target matches, with `step >= opened`, closes as done (reason `reached p_x` / `talked to e_y`). The existing T3 event and `Stuck.progress()` stay. "reach" means "be in the place", so a quest added while standing in its target closes at the end of that step, which replaces `_impossible_check`. Quests without a target close only by `done`.

**Directives keep their meaning.** A quest cites its source line (`why:["t14"]`), and renders show the verbatim cited line next to the quest text (R3 for act, R2 for the planner). Nothing parses for "next door"; the game's own words ride along.

**Bounded planner prompt** (`plan.py` builds it; no char-budget eviction):
1. Trigger, then `Your last ops:` with each result verbatim, then the last `why`.
2. Open quests in order, with their targets and cited lines (≤5); quests closed since the last plan, with reasons (≤5).
3. Held beliefs (≤5); refuted beliefs, newest first (≤5).
4. Game text: lines cited by open quests in full, plus the 8 newest cut to 160 chars.
5. Done so far, from memory by id (R1): places reached, entities talked to, exits crossed (≤10 lines).
6. R4's evidence packet, the map view, text on screen, then the ops schema.

The R2-owned sections come to about 3k chars at most, constant in run length; the map is World's (R1). Notes, chapters, the log span and the full closed history are no longer in the prompt.

**Why root-level:** the gap between words and effects closes structurally (no silent path is left), closure moves onto R1's ids, and the size bound comes from the shape of the data rather than trimming. It removes 9 refusal rules and 2 word filters and adds none. Published op-based stores work the same way: the Anthropic memory tool and Claude Code's task tools take per-item ops and return errors to the model. The failures reported for other Pokémon harnesses are stale or poisoned goal state: Claude deleted its own notes, and Gemini fixated on a hallucinated goal (`scratchpad r2_research.md`).

**Oscillation (MASTER §4.7).** GP (aa301bb) had diff ops (`set`/`remove` on any section, `focus`, `tried`). QS (266681e) replaced them with restatement because:
- invalid ops were skipped silently;
- `remove` deleted heard goals;
- `set` overwrote `tried`/`evidence` (quest-state.md §0).

What differs now: ops are typed; there is no `remove` and no whole-record `set`; `opened`/`closed` stay code-owned; dropped and refuted entries stay, with their reasons; every op's result reaches the planner. GP's micromanaging came from failure-urgent triggers, which R4 keeps off; it was not a property of ops.

**Rejected (one line each):**
- Feed the existing flags back more prominently: keeps restatement, merge heuristics and growth.
- Make omission mean drop: one forgotten quest silently deletes a directive, the same bug inverted.
- Keep name checks once R1 lands: string matching is what failed; ids make it exact.
- An LLM judge for completion: costs scarce calls, and the planner already judges via `done`.
- Keep chapters for long runs: with count caps nothing grows, so there is nothing to compact.
- Rank quests by a code priority score: that is a mechanical gate; order is the planner's call through `top`.

## 3. Deletions (approximate lines)

| Where | What | Lines |
|---|---|---|
| `notebook.py` | `_TACTICAL`, `_EXPLORE`, field whitelists, `normalize_quest_text`, `_valid_check`, `_strip_place_id`, `_edit_distance_one` | 45 |
| `notebook.py` | `apply_plan` reconcile (115) and `_apply_beliefs` (31), replaced by `apply_ops` (~50) | -96 net |
| `notebook.py` | `_apply_facts`, `_append_evidence`, `_bad_quest`, `_impossible_check`, `_heard_quest`, `_has_reference`, `_place_entries`, `_check_passes`, `_fit`, `apply_chapter` | 135 |
| `notebook.py` | `run_checks` (21), replaced by `observe` (~12); v1 `_migrate` and `_pending_migration` (~45) plus the string-log branch of `_load_log` (8): runs start from a clean boot (MASTER §6), and an older file loads as empty; `render` (20) rewritten (~20) | 62 net |
| `notebook.py` | `parked` status, `buttons`/`places`/`labels` constructor args, `flags` | 8 |
| `plan.py` | `chapter`, `CHAPTER_*`, `_chapter_end`, `_chapter_log`, `_closed_quests`, belief-confidence parse; SYSTEM 14 sentences → ~6 | 80 net |
| `loop.py` (R4 file) | stall fallback quest and `stall_unchanged` (505-515, 553), chapter wiring (561-563, `chapter_after`, `_chapter_due`), `_places`, the `reconcile flags`/`closed checks` evidence lines | 25 |
| `tests/agent/test_notebook.py`, `test_plan.py`, `test_qs_e2e.py` | inv1-8, check, chapter, migration and reconcile tests (`tests/agent/data/qs/notebook-v1.json`), replaced by an op table test plus observe and render tests | ~770 → ~250 |
| `scripts/eval_plan.py` (215) | QS `--om plan` gate tool built on `apply_plan`; replaced by the offline replay in §5 (~60) | ~155 net |
| `scripts/eval_act.py:695` | `trial.apply_plan` becomes `apply_ops` | 0 |

Net in `agent/` (S10): notebook.py 541 → ~155, plan.py 164 → ~85, loop.py -25: about -490 lines.

## 4. Interfaces

**R2 gives:**
- `Notebook.apply_ops(reply, step) -> list[{op, key, result}]`, `Notebook.heard(step, speaker, place, text) -> key`, `Notebook.observe(step, place, talked) -> list[key]`, and `focus` (unchanged meaning).
- Data for R3's act render (R3 owns `render_focus`): the open quests in order, each with its cited verbatim lines and its target label. Proposed rule: act never sees `why`, notes or beliefs, and `log` keeps moves only (R3 decides whether act needs it).
- `plan(model, trigger, notebook, view, screen_text, recent, evidence, done)` → `Plan(ops, why)`. The trace `plan` row becomes `{trigger, events, focus, ops: [{op, key, result}], why}`, replacing `quests_changed`, `beliefs_changed` and `flags`.

**R2 needs from R1:**
- stable canonical place and entity ids, with `canon(id)` for both, so a target survives a merge;
- `known(id)`, used to validate a target;
- a short label per id;
- an id-keyed history (`talked: e→last step`, `reached: p→visits`, `crossed: exit→place`) for "Done so far".

**R2 needs from R4 (loop.py):**
- call `observe` after each settled step, and `heard` where `notebook.note` writes dialogue now (`loop.py:404, 429`);
- emit T3 for the keys `observe` closes (keep `_closed`);
- drop the stall fallback, the chapter wiring, and these evidence lines: `reconcile flags`, `closed checks`, and `new text since last plan`, which the prompt's game-text section now covers.

Triggers stay R4's. R2 needs T1 on new game text (so the planner can `add` or `done` against a fresh `t` key) and T3 on closure. It needs no failure trigger.

**Contract changes (contracts.md, "notebook and act", QS invariants):** the QS invariants inv1-8 are replaced by: ops are applied in order or answered with an error; ≤5 open quests; ≤5 held beliefs; game text is add-only and verbatim; nothing is deleted (dropped and refuted entries stay, with reasons); closure is by `done` or a target id event. `Plan` becomes `(ops, why)`, and `chapter()` is removed.

## 5. Scorecard effect and offline verification

- **S5, quests said closed but still open / quests closed** (now 1 / 0): the first half goes to 0 by construction. Closing is an op whose result is shown, and `why` never reaches act. The second half depends on R1 ids for targets; otherwise quests close only by planner `done`.
- **S8, planner prompt chars max** (now 10,084): the R2 sections go from 8.1k to ≤3.5k flat; the map (2.0k) is R1/R3.
- **S10, `agent/*.py` lines:** about -490 (§3).
- **S7, steps from 1F to Pallet** (now 21): indirect, since stale top quests can be dropped or closed. Live only.
- **S4, failed-option re-picks:** none (R3/R4).
- **Optional new metrics:** reply chars (now 1.1k-4.0k; target ≤600) and refused writes with no error shown to the planner (now ≥3 per plan; target 0).

Offline checks (0 cloud calls):
1. **Op table unit test:** every op × {ok, each error} returns the stated result, and state equals the fold of the ok ops.
2. **Prompt-size replay:** rebuild the planner prompt from `notebook.history` at each of the 6 plan steps of `m1_bedroom_live`. The script converts v2 to v3 for eval only. Assert the sections outside the map are ≤3.5k chars and do not grow with step.
3. **Op transcription of the 6 recorded replies:** diff each reply against prior state into ops, apply, and check that the dropped-but-open case becomes either an applied `drop` or a visible no-op in `Your last ops`.
4. **Observe replay:** feed the trace's per-step place and speaker ids through `observe` with quests targeted by hand at the real ids. Each must close on the step truth shows it reached (eval-only join).

## 6. Risks and open questions

1. **Quality depends on R1.** If ids still churn, targets miss. A quest without a target still closes by `done`, so this degrades gracefully. R2 lands after R1 or behind R1's `canon`.
2. **`done` and `drop` need only a reason**, so a hallucinated completion or a dropped directive is possible (as with Gemini's poisoned goal). This is accepted under "the planner's judgement decides", and the cited line stays visible so the planner can re-add. Alternative: `done` on a targeted quest errors until memory has the event. Coordinator: which?
3. **Removing `_TACTICAL`** may bring back tactical quests (GP-era micromanaging). Targets give the planner a legitimate concrete form, and the prompt rule stays. Watch the first run.
4. **Caps of 5/5/8/8** are guesses. Coordinator may set them.
5. **Golden trace** `tests/agent/data/w0_golden/act/trace.jsonl` changes its plan fields, which needs the user's OK to regenerate. Old checkpoints' notebooks load as empty, per the MASTER §6 clean boot.
6. **Shared files:** `render_focus` (R3) and loop.py's planner wiring (R4). MASTER should give notebook.py's data and ops to R2 and `render_focus` to R3.

---

# Phase B: implementation packet (R2)

Status: not started · Branch: feat/lean-agent · Approved solution: §1-§6 above, with the coordinator's rulings (MASTER §10).

**Changes from Phase A** (simplifications; the interfaces are unchanged):
- **Target is one string.** The planner writes a place or thing name, our printed label, or a ref. `World.ref` resolves it, and the ref kind (`place:` means be there, `thing:` means talk to it) replaces the `reach`/`talk` fields.
- **No "Done so far" section.** The heard lines, the recently closed quests (with reasons) and R3's per-row history in the view already carry it. Add it only if S5/S7 show the planner redoing finished things.
- **T3 and progress come only from `observe` closures.** A planner `done`/`drop` is bookkeeping the planner already knows about, so it must not wake the planner or reset the stall.
- **`beliefs` stays a dict keyed by key**, which the frozen `watch_run.py` (lines 416-418) iterates.

## Paste block

    You are the orchestrator for RCR section R2 (quest state and planner contract), repo
    C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent.
    1. Read in order: CLAUDE.md, docs/plans/rcr/MASTER.md, docs/plans/rcr/R2-quests.md (all of it),
       then only the files in its "Read list".
    2. Check the entry criteria. Run the WPs in "Run order". Delegate each WP to a Haiku subagent
       (model "haiku", set explicitly), with the CLAUDE.md rules and the WP card pasted into its prompt.
    3. After each WP, run its acceptance commands yourself. A WP is done only when they exit 0.
       Cloud calls: 0. Tests use stub models only.
    4. Stay inside the files each WP owns. Build on the user's uncommitted live-view edits and keep every
       one of those hunks (MASTER §3). Re-read each file before editing it. Do not edit contracts.md.
       Put proposed contract text in the Result section.
    5. Run the gate. Fill in "Result". Add R2 rows to MASTER §8 and §9 via the coordinator.
    6. If the gate passes, commit only the R2-only paths in §9's commit list ("R2: <gate line>").
       Never `git add` a file marked "live-view" unless the coordinator has asked the user.
       Never `git add -A`. Never push.

## 1. Entry criteria
- R1 has landed (`World.ref/canon/known/history`). The live-view work stays uncommitted, and R2 builds on top of it (user decision, MASTER §3). In R2's files, `agent/loop.py` and `tests/agent/test_loop.py` carry live-view hunks.
- R4's gate commit exists. R2 then runs serially in the main tree. Because R2.1 removes `FINISHED`, `apply_plan` and the Notebook constructor args that loop.py imports, nothing in R2 runs in parallel with R4.
- On disk: `runs/m1_bedroom_live` (calls.jsonl, trace.jsonl, memory/notebook.history, memory/relations.jsonl) and `.venv-agent`.

## 2. Gate

`<py>` = `.venv-agent/Scripts/python.exe -B`.
`<live>` = the live-view tests; both commands must exit 0:
- `<py> -m pytest tests/agent/test_realtime_env.py -q -p no:cacheprovider -n auto`
- `<py> -m pytest tests/agent/test_observer.py tests/agent/test_track.py tests/agent/test_watch_run.py -k test_live -q -p no:cacheprovider -n auto`

| Metric | Pass | Command |
|---|---|---|
| suite | 0 failed | `<py> -m pytest tests/agent -q -p no:cacheprovider -n auto` |
| live-view kept | `<live>` green; `git diff` shows every live-view hunk in loop.py and test_loop.py still present | `<live>`, then compare `git diff agent/loop.py tests/agent/test_loop.py` with the pre-R2.4 snapshot (R2.4) |
| S8: R2 prompt sections at the 6 recorded plan steps | each ≤ 3,500 chars | `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check size` |
| S12 + S5a: the 6 replies transcribed into ops | each ≤ 600 chars; every op gets a result | `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check ops` |
| S5b proxy: observe replay | every targeted quest closes at its first entry or talk step | `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check observe` |
| S10: lines | report net lines per file (report only, no budget) | `git diff --stat agent/ scripts/eval_plan.py scripts/eval_act.py` |
| re-adds of a finished target | report only, never a gate | printed by `--check ops` |
| generic | no label words | `grep -niE "door|window|rug|stairs|oak|mom|next door" agent/notebook.py agent/plan.py` exits 1 |
| golden | only `plan` fields of trace rows changed | R2.4 acceptance |

**If it fails:** report the metric and do not layer fixes on top (MASTER §4.8).

## 3. Read list
- This doc §1-§6. MASTER §2, §3, §7 and §10. R1-identity.md §4 (refs, `World.ref/canon/known`). R4-feedback.md §2-§4 (step structure, backoff `u`).
- `agent/notebook.py` and `agent/plan.py` (whole files). `agent/loop.py`: CodeGraph `"Loop _apply_plan _plan_trigger _learn _flush_conversation _moved _after_outcome _evidence state restore _closed _goal_text"`.
- `scripts/eval_act.py` (`om_plan`, `_dialogue_entries`, `_independent_notebook`). `tests/agent/test_world_golden.py`.

## 4. Contracts (Provides; the coordinator merges them into contracts.md "notebook and act" at the gate)

```python
# agent/notebook.py: <memory root>/notebook.json v3 + notebook.history (append-only, one line per write)
MAX_OPEN, MAX_HELD, MAX_OPS = 5, 5, 8                      # knobs (MASTER §10)
class Notebook:
    def __init__(self, root: Path, *, ref=None, canon=None) -> None
        # ref(text) -> "place:<id>" | "thing:<id>" | other ref | None   (World.ref; default: always None)
        # canon(ref) -> ref                                            (World.canon; default: identity)
        # a file without "text" and "quests" lists (v1/v2) loads as empty (clean boot, MASTER §6)
    data: {"text": [{"key": "t<n>", "step", "speaker": str | None, "where": str, "text": str}],   # code-written, verbatim, add-only
           "quests": [{"key", "text", "why": [keys], "target": ref | None, "label": str | None,
                       "status": "open" | "done" | "dropped", "opened", "closed", "reason"}],   # list order = priority
           "beliefs": {key: {"key", "text", "because": [keys], "status": "held" | "refuted", "reason", "step"}},
           "last": {"step", "why", "results"}}
    log: list[{"step", "text"}]                            # moves only (note()); R3 decides whether act still needs it
    focus: str | None                                      # key of the first open quest
    def apply_ops(self, ops, step: int, why: str = "") -> list[dict]
        # [{"op": str | None, "key": str | None, "result": "ok" | "error: <why>"}], one per input item, in order;
        # a non-list `ops` gives one error row. "Changed nothing" == not any(r["result"] == "ok").
    def heard(self, step: int, speaker: str | None, where: str, text: str) -> str   # returns the new t-key
    def observe(self, step: int, place: str | None, talked=()) -> list[str]
        # closes each open quest whose canon(target) == place (place refs) or is in talked (thing refs);
        # reason "reached <label>" / "talked to <label>"; returns the closed keys
    def note(self, step: int, text: str) -> None           # unchanged
    def render(self) -> str                                # planner sections, count-capped (below)
    def render_focus(self, limit: int = 4000) -> str       # interim R2->R3 shape; R3 rewrites it

# agent/plan.py
@dataclass
class Plan: ops: list | None; why: str                     # why <= 200 chars; never shown to act
def plan(model, trigger: str, notebook, view: str, screen_text: list, recent: list[str], evidence: str | None = None) -> Plan
# removed: chapter(), CHAPTER_SYSTEM, CHAPTER_SCHEMA, Plan.quests/beliefs/facts/note
# trace "plan" row: {"trigger", "events", "focus", "ops": <apply_ops results>, "why"}
```

**Op validation (the only errors; no judgement):**

| op | needs | error when |
|---|---|---|
| any | a dict with `op` and `key` (≤ 40 chars) | not a dict; unknown `op`; missing key; item number > MAX_OPS ("not applied: more than 8 ops") |
| `add` | text, why, target? | key used by any quest; empty text; `why` empty or citing a key that is neither a t-line nor a belief; MAX_OPEN open; target unresolved or not `place:`/`thing:` |
| `done`, `drop` | reason | key not open; empty reason |
| `top` | — | key not open |
| `target` | target | key not open; target unresolved or of the wrong kind |
| `believe` | text, because? | key used; empty text; MAX_HELD held; `because` cites an unknown key |
| `refute` | reason | key not held; empty reason |

**Planner prompt `render()`**, in order, with count caps and no character trimming:
1. `Your last ops (step N):` with every result row, then `Your last why:`.
2. `Open quests (first is worked on now):` key, text, `[target: label]`, and each cited t-line verbatim.
3. `Recently closed:` the 5 newest, each with status and reason.
4. `Held beliefs:` (≤ 5, with because); `Ruled out:` the 5 newest refuted, with reason.
5. `Game text:` every line cited by an open quest in full, plus the 8 newest, each cut to 160 chars: `t<n> <speaker or "text"> at <where>: "<text>"`.

`plan()` user prompt: `Trigger`, `render()`, `Evidence since last plan` (R4), `Map and memory` (view), `Text on screen now`, `Recent actions`, then REPLY.

**SYSTEM** has 6 sentences plus "Return only JSON."
1. You are the strategist in an unfamiliar game. Quests are outcomes in the game world, never buttons, directions or coordinates. Another model chooses the moves.
2. Reply with operations that change your notebook. Reply with none when nothing should change.
3. Each operation is applied in order or answered with an error, and you see the results next time.
4. A quest's why cites the game-text line (t-key) or belief it comes from.
5. Give a quest a target when it means being in a place or talking to someone the map names. It then closes by itself when that happens.
6. Close a quest with done or drop and a reason. The first open quest is worked on now, and top changes it.

## 5. Work packages

| WP | Goal | Owns files (planned net lines, report) | Depends on | Tier |
|---|---|---|---|---|
| R2.1 | notebook v3 | `agent/notebook.py` (541 → ~170), `tests/agent/test_notebook.py` (~160); delete `tests/agent/data/qs/notebook-v1.json` | R4 gate | Haiku |
| R2.2 | planner I/O | `agent/plan.py` (164 → ~85), `tests/agent/test_plan.py` (~70) | R2.1 | Haiku |
| R2.3 | offline replay tool | `scripts/eval_plan.py` (rewrite, 215 → ~120), `tests/agent/test_eval_plan.py` (new, ~60) | R2.2 | Haiku |
| R2.4 | loop wiring + callers | R2 hunks in `agent/loop.py`; `scripts/eval_act.py` (`om_plan`, `_dialogue_entries`); R2 tests in `tests/agent/test_loop.py`, `test_qs_e2e.py`, `test_om_eval.py`; golden regen | R2.3 | Haiku |

**Run order:** [R4 gate commit] → R2.1 → R2.2 → R2.3 → R2.4 → gate. All four run serially in the main tree, back to back. Between R2.1 and the end of R2.4, the full suite may be red on loop.py and eval callers; at no other time.
- **Every WP:** re-read its files from disk before editing (other sections and the user's live-view work change them). Acceptance = its own commands plus `<live>`.

### R2.1: notebook v3
- **Goal:** `Notebook` = the fold of applied ops, with verbatim game text, id closure and count-capped renders, exactly per §4.
- **Read:** `agent/notebook.py` (whole file; keep `_save`'s atomic write and retry loop, and `note`/`log`). This doc §4.
- **Change:** rewrite `agent/notebook.py`. Delete everything §3 lists for notebook.py: `_TACTICAL`, `_EXPLORE`, `normalize_quest_text`, `_valid_check`, `_strip_place_id`, `_edit_distance_one`, `_migrate`, `apply_plan`, `_apply_beliefs`, `_apply_facts`, `_append_evidence`, `_bad_quest`, `_impossible_check`, `_heard_quest`, `_has_reference`, `_place_entries`, `_check_passes`, `run_checks`, `apply_chapter`, `_cited`, `_fit`, the `parked` status, `FINISHED`, `flags` and the `buttons`/`places`/`labels` args. Interim `render_focus` follows MASTER §7 R2→R3: `Focus:`, then `Top quest: <text> [target: <label>]`, `Because:` with the cited t-lines verbatim, then the newest log lines that fit `limit`. No beliefs, notes, why or other quests.
- **Rewrite `tests/agent/test_notebook.py` as:**
  - one parametrized op table covering every row of the §4 validation table, ok and each error, which then asserts that state equals the fold of the ok ops;
  - `drop` then `add` at MAX_OPEN in one reply;
  - a non-list reply, a non-dict item and a 9th op each return an error row, and `last.results` and history persist them;
  - `heard` keys are sequential and the text is verbatim;
  - `observe` closes a place target and a thing target via `canon` (a merged id maps onto the stored target), and ignores closed or untargeted quests;
  - `render()` respects every count cap, an old cited line stays in full, and the prompt is ≤ 3,500 chars with 20 long lines;
  - `render_focus` has no belief or why text;
  - a v2 file loads as empty.
- **Acceptance:** `<py> -m pytest tests/agent/test_notebook.py -q -p no:cacheprovider`, the §2 grep exits 1, and `<live>`. Report `wc -l agent/notebook.py`.
- **Risk:** a worker keeps reconcile logic "for safety". The card lists what to delete; review the diff for any `flags` or `check`.

### R2.2: planner I/O
- **Goal:** `plan()` sends the §4 prompt and returns `Plan(ops, why)` unvalidated; validation lives in `apply_ops` only.
- **Change:** rewrite `agent/plan.py`. Write SYSTEM and REPLY per §4, with a SCHEMA of `{"ops": array of flat objects {op enum, key, text, why, target, because, reason}, "why": string ≤ 200}`, required `["ops", "why"]`. `ops` passes through as given (a non-list becomes `None`); `why` is capped at 200 chars. Delete `chapter`, `CHAPTER_*`, `_records`, `_state`, `_dump`, `_chapter_end`, `_chapter_log`, `_closed_quests` and the belief-confidence parse.
- **Rewrite `tests/agent/test_plan.py`** (with a stub notebook whose `render()` returns a marker):
  - ops and why pass through, and why is capped;
  - a non-list `ops` becomes `None`;
  - prompt section order is per §4, with no chapter, notes or log span;
  - SYSTEM is ≤ 7 sentences and passes the §2 grep.
- **Acceptance:** `<py> -m pytest tests/agent/test_plan.py -q -p no:cacheprovider` and `<live>`. Report `wc -l agent/plan.py`.

### R2.3: offline replay tool
- **Goal:** `scripts/eval_plan.py --run <dir> --check size|ops|observe|all`, with 0 model calls, replacing the QS gate tool.
- **One public builder:** `notebook_at(run: Path, step: int) -> Notebook`, the v3 notebook as of `step`, built from `notebook.history` (in a temp root):
  - the quests come from the last `quests` snapshot at or before `step` (open stays open; keep key, text and why);
  - the text comes from `log` ops matching `<speaker> at <where>: "<text>"` or `text at <where>: ...`;
  - the beliefs are the held and refuted ones.

  R3's menu replay imports this function, so there is no second builder. Every check uses it.
- **Checks:**
  - `size`: at each trace row with a `plan` field, render `plan()`'s prompt from `notebook_at(run, step)` with a capturing model. Print each section's chars and fail if `render()` > 3,500.
  - `ops`: for each recorded `role == "plan"` reply in calls.jsonl, transcribe a diff against the prior snapshot into ops:
    - a new key → `add` (why mapped to a t-key or belief; target = the reply's `check.arg` resolved by a `World` over the run's memory, or none);
    - a status change → `done`/`drop`;
    - the first open quest changed → `top`;
    - an omission → nothing.

    Apply the ops and print the result rows plus the JSON chars of `{"ops", "why"}`. Also print the **re-adds of a finished target**: the count of `add` ops whose target equals the target of a quest that is already closed. This is report only, never a gate. Fail if any reply exceeds 600 chars or an op has no result row.
  - `observe`: for each place id in trace order after step 1, add the quest `reach place:<id>` one step before its first entry. For each `says` relation in memory/relations.jsonl, add the quest `talk thing:<s>` before its step. Replay `observe(step, "place:" + row.place, talked)` over the trace. Fail unless each quest closes exactly at its first entry or talk step.
- **Test:** `tests/agent/test_eval_plan.py` builds a 6-row synthetic run in `tmp_path`. It asserts that `notebook_at` returns the expected open quests and t-lines, that each check exits 0, that the re-add count is printed, and that one case fails (a reply over 600 chars).
- **Acceptance:** the test above, `<live>`, then the three §2 commands on `runs/m1_bedroom_live`, which exit 0. Paste their output, including the re-add count, into Result.

### R2.4: loop wiring and callers (after R4's gate)
- **Goal:** Loop uses `apply_ops`, `heard` and `observe`; T3 comes from id closures only; dead planner state is gone.
- **Change `agent/loop.py`** (only these hunks; against R4's step order `settle → observe → maybe_plan → decide → resolve → execute → settle → observe → result → record`):
  1. Construct `Notebook(mem.root, ref=self.world.ref, canon=self.world.canon)`. Delete `_places` and `entered`, including their state and restore keys.
  2. Plan site: call `results = self.notebook.apply_ops(p.ops, step, p.why)`. The plan row becomes `{"trigger", "events", "focus", "ops": results, "why"}`. R4's "changed nothing" test becomes `not any(r["result"] == "ok" for r in results)`. Delete `_apply_plan`'s quest diffing and `FINISHED`, plus `stall_unchanged`, the fallback quest and `plan_flags` if R4 left them.
  3. After the second settle/observe and before `result`: `self._closed(self.notebook.observe(step, f"place:{place}", talked), step, top)`. `talked` holds the `thing:<tid>` refs for which this step recorded a `says` relation (R1's speaker-binding site). `_closed` keeps T3 and `stuck.progress()` and drops `_chapter_due`.
  4. `_learn` world-screen text and `_flush_conversation`: `self.notebook.heard(step, speaker, self.world.place_label(place) if place else "?", text)` replaces `notebook.note(...)`. T1 is unchanged.
  5. Delete the chapter wiring (`chapter` import, `_chapter_due`, `chapter_after`, the `_after_outcome` chapter call).
  6. Delete these `_evidence` lines if present: `reconcile flags`, `closed checks`, `new text since last plan`, and "last stall changed nothing".
  7. `_goal_text` uses only the top quest's text.
- **`scripts/eval_act.py`:** delete `om_plan`, `_independent_notebook`, the `_valid_check` import and the `plan` choice in `--om`. `_dialogue_entries` reads `notebook.data["text"]` and formats rows as before. In `test_om_eval.py`, delete the 4 `om_plan` tests and adapt `om_talk`'s fixture.
- **Tests:**
  - In `test_loop.py`, delete the R2-era tests that R4 left: fallback quest, speak/reach checks, chapter, `stall_unchanged` round trip, failed chapter. Add:
    - (a) entering a targeted place closes the quest with T3 and no planner call;
    - (b) a `says` from the target thing closes a talk quest;
    - (c) a stub reply's ops apply, and the plan row carries the results; a zero-ok reply counts as unchanged for R4's `u`;
    - (d) a dialogue flush writes a t-line and still raises T1.
  - Rewrite `test_qs_e2e.py`'s two tests: planner calls bounded with no events lost (kept), and a targeted quest closing by id across a checkpoint restore (replaces the chapter test).
- **Golden** (authorised, MASTER §10):
  - `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py -q -p no:cacheprovider`, then
  - `<py> -c "import json,subprocess as s;f='tests/agent/data/w0_golden/act/trace.jsonl';o=[json.loads(l) for l in s.run(['git','show','HEAD:'+f],capture_output=True,text=True).stdout.splitlines()];n=[json.loads(l) for l in open(f)];d=lambda r:{k:v for k,v in r.items() if k!='plan'};assert list(map(d,o))==list(map(d,n)),'non-plan fields changed'"`.
  - The golden is git-ignored but tracked: `git add -f` it at the gate.
- **Acceptance:** `<py> -m pytest tests/agent/test_loop.py tests/agent/test_qs_e2e.py tests/agent/test_om_eval.py tests/agent/test_world_golden.py -q -p no:cacheprovider -n auto`, then `<live>`, then the full suite (§2).
- **Live-view hunks:** before editing, save `git diff agent/loop.py tests/agent/test_loop.py > .tmp/r2/pre_r24.diff`. Edit only the R2 hunks listed above. Afterwards, every live-view hunk in the pre-snapshot must still be in the new diff (check by hand or with `git diff` hunk headers), and `<live>` must pass.
- **Risk:** R4's restructure moves the speaker-binding site. Find it with CodeGraph `"says relate speaker call tid"`, not by line number.

## 6. Test layers
| Layer | Gates R2? | Command |
|---|---|---|
| Unit | yes | `pytest tests/agent -q -n auto` |
| Offline replay (recorded run, 0 calls) | yes | `scripts/eval_plan.py --check all` |
| PyBoy / live | no | none (MASTER §6: live runs after R1 and R3 only) |

## 7. Budgets
- **Lines (S10, report only, no budget):** planned notebook.py 541 → ~170, plan.py 164 → ~85, loop.py about −25 (R2 hunks), eval_plan.py 215 → ~120, eval_act.py about −70. Tests about −500.
- **Cloud calls:** 0.

## 8. Out of scope
- When the planner runs (R4); render_focus beyond the interim shape and the act prompt (R3); `World.view` contents (R3); refs, canon and history internals (R1); the perceiver; live runs; contracts.md edits.

## 9. Result (filled in by the orchestrator)
- **Gate:** (each §2 metric with its number and command)
- **Commit list:**
  - R2-only, add at the gate: `agent/notebook.py`, `agent/plan.py`, `scripts/eval_plan.py`, `scripts/eval_act.py`, `tests/agent/test_notebook.py`, `tests/agent/test_plan.py`, `tests/agent/test_eval_plan.py`, `tests/agent/test_qs_e2e.py`, `tests/agent/test_om_eval.py`, the deleted `tests/agent/data/qs/notebook-v1.json`, the golden (`git add -f`), `docs/plans/rcr/R2-quests.md`.
  - **live-view, do not add** unless the coordinator has asked the user: `agent/loop.py`, `tests/agent/test_loop.py`.
- **Deviations:** —
- **Contract text for the coordinator:** §4 above, plus any change.
- **For S0 (`eval_run.py:778`):** plan rows no longer have `quests_changed`/`beliefs_changed`. "Unchanged" = `not any(r["result"] == "ok" for r in row["plan"]["ops"])`.

## 10. Handoff
No `handoff-notes.md` rewrite from this section; the coordinator owns program memory in MASTER. Add the Result numbers to MASTER §8/§9 via the coordinator, and note in Result anything the R3 session needs for `render_focus`.
