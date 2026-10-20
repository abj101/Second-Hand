# RCR R2: Quest state and planner contract

Status: Phase A approved 10-15 · R2.1–R2.4 implemented 10-16; gate blocked (43 failed / 587 passed), see §9 Result · Branch: feat/lean-agent · Evidence: `runs/m1_bedroom_live`

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

Status: implemented 10-16; full-suite gate blocked, no staging or commit · Branch: feat/lean-agent · Approved solution: §1-§6 above, with the coordinator's rulings (MASTER §10).

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
- R4 gate: commit 3f521e8 (648 tests green on 10-16). It holds only R4's non-live files. R4's hunks in `agent/loop.py`, `observer.py`, `test_loop.py`, `test_observer.py` and `test_run_provenance.py` stay uncommitted next to the live-view hunks: the hold removal, the single walker, the result/`Stuck` wiring and the settle window. 3f521e8 alone does not run; the working tree is the only runnable R4. Treat every uncommitted hunk in those files as protected, R4's and live-view alike.
- R2 then runs serially in the main tree. Because R2.1 removes `FINISHED`, `apply_plan` and the Notebook constructor args that loop.py imports, nothing in R2 runs in parallel with R4.
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
  2. Plan site: call `results = self.notebook.apply_ops(p.ops, step, p.why)`. The plan row becomes `{"trigger", "events", "focus", "ops": results, "why"}`. R4's "changed nothing" test becomes `not any(r["result"] == "ok" for r in results)`. Delete `_apply_plan`'s quest diffing and `FINISHED`, plus `stall_unchanged`, the fallback quest and `plan_flags` (R4 left all of them; also their `state`/`restore` keys and the "last stall changed nothing" evidence line). R4's T4 test in `_after_outcome` still reads `self.stall_unchanged > 0 and self.stuck.progress_n >= 10`; drop that clause so T4 fires only on `Stuck`'s `no_progress`.
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
- **Live-view hunks:** before editing, save `git diff agent/loop.py tests/agent/test_loop.py > .tmp/r2/pre_r24.diff`. Edit only the R2 hunks listed above. The snapshot holds R4's uncommitted hunks as well as the live-view ones (§1); keep both. Afterwards, every live-view hunk in the pre-snapshot must still be in the new diff (check by hand or with `git diff` hunk headers), and `<live>` must pass.
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

**2026-10-19 amendments (coordinator-approved, uncommitted; the coordinator commits the tree).** Haiku workers made the notebook, planner, replay-script and test edits. The orchestrator wired loop.py, the observe/heard lines only.

*Amendment 1, from runs/m1_rcr_baseline:*
- **Beliefs deleted.** The `believe`/`refute` ops, `data["beliefs"]`, MAX_HELD and the Held/Ruled-out sections are gone.
  - A quest's `why` cites t-keys directly. Errors name the citable keys: `why must cite game-text keys, newest: t2, t1`.
  - This fixes S45, where the belief cap blocked Mom's directive.
- **Closure evidence.**
  - "Recently closed" shows the quest text, the target's current label (via `World.label`), the lines that thing said after the closure, and "target set by you at step N".
  - New `reopen` op.
  - The replay at S45 shows `talked to MOM, who said t2 … "PROF. OAK, next door, is looking for you." (target set by you at step 39)`.
- **Target errors list candidates.** Example: `target "desk" matches no known place or thing; closest: debris, outside house, Professor Oak`. Candidates are only labels that `World.ref` resolves.
- **loop.py:** `Notebook(..., label=world.label, refs=<live place/thing refs>)`; dialogue flushes pass `who=thing:<tid>` of the thing that started the conversation.

*Amendment 2, from runs/m1_rcr_post_r3 (quests closed the moment they were created):*
- **Closure is now an event, not a state.**
  - `observe(step, place)` has no `talked` argument. It closes a place quest only on an arrival: the previous observed place (`data["here"]`, persisted) differs, the current place is the target, and the target was set at or before this step.
  - `heard(..., who)` closes a thing quest when `who` is the target and the line comes at or after the target step. The next `observe` returns that key, so T3 is unchanged.
  - loop.py clears a stale `who` when the screen returns to the world without dialogue. Touching a thing no longer credits it with a later conversation.
  - `render_focus` says "(done when: you enter X)" or "(done when: X speaks to you)".
- **`eval_plan.notebook_at` re-derives closures.** It ignores recorded observe events and replays each step's planner events, then text through `heard`, then `observe(next_place)`.
- **post_r3 replay:**

| Quest | Result |
|---|---|
| q5 (door mat) | open |
| q3, q4 | open from S87 while the player is already in the target place; close at S99 after a real leave (S98) and re-enter |
| q2 | reached at S70, target set at S69 |
| q6 | reached at S102, target set at S102; the plan runs before that step's move, which crosses into the target |
| q8 | open |

- 0 closures while the player is already in the target place.
- **Open issue, for the next owner: q2 and q6 close at the wrong places.** They are real arrivals, but the planner targeted the wrong place: q6 "Find Professor Oak next door" targeted p_place_4, which is outside, not the lab. This is a planner targeting error, not a closure bug, and no gate was added. The closed-quest evidence line makes the mismatch visible to the planner, and `reopen` undoes it.
- **Acceptance:**
  - full suite `-n auto`: **629 passed, 1 skipped** (missing bedroom checkpoint), 0 failed;
  - live-view tests: 11 passed, and 9 passed;
  - `eval_plan.py --check all`: exits 0 on m1_rcr_post_r3 and on m1_rcr_baseline;
  - generic grep on notebook.py/plan.py is clear.
- **Data note:** `--check observe` on post_r3 lists `says` relations whose subject is a place id (`thing:p_place_4`). That is an R1 memory oddity and harmless to R2.

**2026-10-16 (finalize): gate PASS.** The user authorized fixing the three blocked consumers, and a Haiku worker migrated them:
- `scripts/eval_run.py` carries its own copy of the old `_TACTICAL` regex, since it is a measurement script. A T4 plan row counts as unchanged when it has an `ops` list with no `"ok"` result. The `quests_changed` and history fallbacks stay for old runs.
- `test_harness_reliability.py` door/checkpoint test: the stub reply is now v3 `ops/why` citing a `heard` t-line. Its asserts check `data["text"]` and `Top quest:` after the restore.
- `test_world.py` act test: the quest is seeded through `heard` + `apply_ops`, and the test expects `Top quest: Find Chest` plus the cited line.

Orchestrator reruns:
- Full suite: **630 passed, 0 failed** (77 s).
- `<live>`: **11 passed**, and **9 passed**.
- `eval_plan.py --check all`: exit 0.

R2-only paths are committed. `agent/loop.py` and `tests/agent/test_loop.py` stay unstaged (live-view). The BLOCKED record below is kept as history.

**2026-10-16: gate BLOCKED.** R2.1 through R2.4 were implemented serially in the main tree on `feat/lean-agent`, starting at `3f521e8`. No worktree, agent cloud calls, live agent runs, staging, commit or push. Every worker spawn explicitly used **gpt-6-luna, reasoning high**. All acceptance results below were run by the orchestrator.

**Gate metrics (§2):**

- **Suite: FAIL — 43 failed, 587 passed**, 25,434 warnings, 68.96 s, exit 1. Command: `<py> -m pytest tests/agent -q -p no:cacheprovider -n auto`.
- **Live-view kept: PASS.** `<py> -m pytest tests/agent/test_realtime_env.py -q -p no:cacheprovider -n auto`: **11 passed**, exit 0. `<py> -m pytest tests/agent/test_observer.py tests/agent/test_track.py tests/agent/test_watch_run.py -k test_live -q -p no:cacheprovider -n auto`: **9 passed**, exit 0. These commands also passed at the earlier WP boundaries.
- Preservation: compared `git diff agent/loop.py tests/agent/test_loop.py` to `.tmp/r2/pre_r24.diff` and the saved working-tree copies. R2 hunks only; continuous/live observation code and R4 behavior outside those hunks retained. `<py> .tmp/r2/check_preservation.py`: **15 protected tracked file hashes unchanged, 15 loop functions outside R2 unchanged, 2 live loop tests unchanged**, exit 0. The other changed test bodies concern retired planner state/evidence/check APIs. Existing untracked live-view files were not edited.
- **S8: PASS.** `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check size`: six notebook renders **322, 724, 793, 1,076, 1,242, 1,242 chars**, maximum **1,242 ≤ 3,500**, exit 0. These are the R2 notebook sections; historical map/evidence/screen/recent fields absent from plan rows are shown as empty/default sections, so the printed prompt total is not a reconstruction of the original full planner prompt.
- **S12 + S5a: PASS.** `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check ops`: six transcribed replies **410, 491, 240, 408, 349, 100 chars**, maximum **491 ≤ 600**; **14 ops, 14 result rows** (11 ok, 3 explicit errors), exit 0. Omission remains a visible no-op. S55's legacy source cannot be mapped and its add/top are refused; S76's add reaches the open cap. These refusals are printed, not silently repaired or counted as applied.
- **S5b proxy: PASS.** `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check observe`: **7/7** targeted quests close exactly at steps **6, 20, 26, 41, 55, 58, 76**, exit 0.
- **Finished-target re-adds: 0**, report only, from `--check ops`.
- `<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check all`: exit 0.
- **Generic: PASS.** `rg -ni 'door|window|rug|stairs|oak|mom|next door' agent/notebook.py agent/plan.py`: no matches, expected exit **1**.
- **Golden: PASS.** `W0_REGEN=1 <py> -m pytest tests/agent/test_world_golden.py -q -p no:cacheprovider`: **1 passed**, exit 0. The R2.4 comparison against HEAD, removing only each row's `plan`, exits 0: **all 8 rows' non-plan fields unchanged; 3 plan fields changed**. Only the tracked golden trace changed.
- **S10 (report only): R2 agent net −421 lines.** Requested `git diff --stat agent/ scripts/eval_plan.py scripts/eval_act.py` was run; it also includes protected pre-existing live/R4 work, so the R2-only numbers below use the pre-R2.4 working-tree copies for shared files and HEAD for R2-only files. Code/tests/deleted fixture net **−1,229 lines**.
- `git diff --check` on R2 code/tests: exit 0; only Git's CRLF notices. Existing SDL2 and Pillow warnings were not changed.

**WP acceptance, run by the orchestrator:**

- R2.1: `<py> -m pytest tests/agent/test_notebook.py -q -p no:cacheprovider` — **41 passed**; notebook **254 lines**. Notebook-only generic grep passed immediately; the two-file grep still matched the untouched old planner sentence until R2.2, then passed.
- R2.2: `<py> -m pytest tests/agent/test_plan.py -q -p no:cacheprovider` — **4 passed**; planner **64 lines**.
- R2.3: `<py> -m pytest tests/agent/test_eval_plan.py -q -p no:cacheprovider` — **3 passed**, plus the three replay commands above.
- R2.4: `<py> -m pytest tests/agent/test_loop.py tests/agent/test_qs_e2e.py tests/agent/test_om_eval.py tests/agent/test_world_golden.py -q -p no:cacheprovider -n auto` — **134 passed**, exit 0, 45.92 s. Its required full-suite gate fails as recorded above.

**Blocking failures and final attempt:**

- **41 failures in `tests/agent/test_eval_run.py`** flow through `scripts/eval_run.py:741`, which imports the deliberately removed `agent.notebook._TACTICAL`.
- `test_harness_reliability.py::test_door_crossing_keeps_place_identity_heard_facts_and_the_top_quest_through_a_checkpoint` still supplies the old `quests/facts/beliefs/note` planner reply and checks the old fact store. The v3 planner consumes `ops/why`.
- `test_world.py::test_loop_act_uses_world_facts_active_quests_and_labels` still seeds `facts` and expects `Focus: find_chest`; R2's required interim render is `Focus:\nTop quest: ...\nBecause:` with cited t-lines.
- Orchestrator isolated all three failure classes with `<py> -m pytest tests/agent/test_eval_run.py::test_lifecycle_replays_stuck_and_reads_v2_history tests/agent/test_harness_reliability.py::test_door_crossing_keeps_place_identity_heard_facts_and_the_top_quest_through_a_checkpoint tests/agent/test_world.py::test_loop_act_uses_world_facts_active_quests_and_labels -q -p no:cacheprovider --tb=short`: **3 failed**, exit 1. These are R2-induced integration gaps, not pre-existing failures.
- The worker's first R2.4 preflight had **131 passed, 2 failed** (unset seeded `prev_frame`, removed `Plan.facts` assertion); one test-adaptation round produced **133 passed** before the orchestrator's acceptance. After the full-suite failure, the **one final same-tier attempt** was explicitly spawned as **gpt-6-luna high** with the measured failure output pasted into its prompt. It found **no contract-compliant repair within the WP's owned files** and made **no edits**. No tier escalation or compatibility shim was attempted.
- `scripts/eval_run.py` is coordinator-owned; it and `tests/agent/test_eval_run.py`, `test_harness_reliability.py`, `test_world.py` are outside every R2 WP's ownership. The coordinator must authorize/perform those consumer and fixture migrations before this gate can pass. Restoring filters, facts/restatement, or old focus formatting would contradict §4. Stopped at this gate; no layered repair.
- R2.3 used one repair round after the orchestrator's assertion reproduced `_cite(nb, 'b', [{'key': 'b', 'because': []}]) == []` instead of `['b']`. The same worker received that failure output, included same-reply belief keys, added a regression, and passed the orchestrator's rerun. No second R2.3 failure or final attempt was needed.

**Net lines (before → after; R2-only):**

- `agent/notebook.py`: **541 → 254 (−287)**.
- `agent/plan.py`: **164 → 64 (−100)**.
- `agent/loop.py`: **572 → 538 (−34)**, versus the pre-R2.4 working tree.
- `scripts/eval_plan.py`: **215 → 312 (+97)**.
- `scripts/eval_act.py`: **893 → 805 (−88)**.
- `tests/agent/test_notebook.py`: **512 → 184 (−328)**.
- `tests/agent/test_plan.py`: **151 → 59 (−92)**.
- `tests/agent/test_eval_plan.py`: **0 → 93 (+93)**.
- `tests/agent/test_loop.py`: **1,956 → 1,906 (−50)**, versus the pre-R2.4 working tree.
- `tests/agent/test_qs_e2e.py`: **108 → 89 (−19)**.
- `tests/agent/test_om_eval.py`: **333 → 185 (−148)**.
- Deleted `tests/agent/data/qs/notebook-v1.json`: **273 → 0 (−273)**.
- Replay tool growth is the legacy/current history adapter, three actual replay checks, printed section/result evidence and standalone CLI. Lines are report-only; no code was compressed to hit a line target.

**Commit list:** **none; index remains empty and HEAD remains `3f521e8`** because the gate failed. R2-only changes remain unstaged: `agent/notebook.py`, `agent/plan.py`, `scripts/eval_plan.py`, `scripts/eval_act.py`, `tests/agent/test_notebook.py`, `tests/agent/test_plan.py`, new `tests/agent/test_eval_plan.py`, `tests/agent/test_qs_e2e.py`, `tests/agent/test_om_eval.py`, deleted v1 fixture, `tests/agent/data/w0_golden/act/trace.jsonl`, and this document. **`agent/loop.py` and `tests/agent/test_loop.py` remain unstaged**, carrying R2 plus protected live/R4 edits.

Other pre-existing unstaged/untracked work retained: `agent/env_pyboy.py`, `agent/model.py`, `agent/observer.py`, `agent/track.py`; `scripts/run_agent.py`, `scripts/truth_pokered.py`, `scripts/watch_run.py`, `scripts/check_live_view.py`; `tests/agent/test_env.py`, `test_model.py`, `test_observer.py`, `test_run_provenance.py`, `test_track.py`, `test_watch_run.py`, `test_realtime_env.py`; `docs/plans/handoff-notes.md`, `docs/plans/rcr/MASTER.md`, `docs/plans/continuous-live-view.md`; `.claude/`. No MASTER, handoff-notes or contracts edit was made; the coordinator owns the cross-section handoff.

**Contract text for the coordinator / R3:** §4 stands: v3 `Notebook.apply_ops(ops, step, why="")` returns one result per item; `heard` writes code-owned verbatim t-lines; `observe` closes canonical place/thing targets; quests retain ordered priority, dropped history and cited sources. `Plan` is `(ops, why)` with a 200-char why cap. Target is one string resolved by `World.ref`. Only observed closures emit T3/progress; planner done/drop do not. Interim `render_focus` shows the top quest, target label, cited game text and fitting recent move log; no beliefs, planner why or other quests. R3 owns its next rewrite. The private unused `_closed` top argument was removed with chapters.

**For S0:** remove its dependency on the deleted filter and migrate v3 text/quest/op history consumers. Plan rows no longer have `quests_changed`/`beliefs_changed`. "Unchanged" = `not any(r["result"] == "ok" for r in row["plan"]["ops"])`. MASTER §8/§9 should receive the blocked gate, the −421 agent line delta and the measured replay numbers through the coordinator.

**Recorded-run command output (orchestrator; all three commands exit 0):**

`<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check size`

```text
step 1: notebook_total=322 notebook_last_ops=24 notebook_last_why=16 notebook_open_quests=115 notebook_closed=17 notebook_held_beliefs=129 notebook_ruled_out=11 notebook_game_text=10 trigger=22 evidence=30 map=20 screen_text=24 recent=20 reply=288 prompt_total=732
step 41: notebook_total=724 notebook_last_ops=24 notebook_last_why=16 notebook_open_quests=261 notebook_closed=17 notebook_held_beliefs=224 notebook_ruled_out=11 notebook_game_text=171 trigger=256 evidence=30 map=20 screen_text=24 recent=20 reply=288 prompt_total=1368
step 55: notebook_total=793 notebook_last_ops=24 notebook_last_why=16 notebook_open_quests=330 notebook_closed=17 notebook_held_beliefs=224 notebook_ruled_out=11 notebook_game_text=171 trigger=39 evidence=30 map=20 screen_text=24 recent=20 reply=288 prompt_total=1220
step 64: notebook_total=1076 notebook_last_ops=24 notebook_last_why=16 notebook_open_quests=397 notebook_closed=17 notebook_held_beliefs=319 notebook_ruled_out=11 notebook_game_text=292 trigger=120 evidence=30 map=20 screen_text=24 recent=20 reply=288 prompt_total=1584
step 76: notebook_total=1242 notebook_last_ops=24 notebook_last_why=16 notebook_open_quests=466 notebook_closed=17 notebook_held_beliefs=416 notebook_ruled_out=11 notebook_game_text=292 trigger=39 evidence=30 map=20 screen_text=24 recent=20 reply=288 prompt_total=1669
step 99: notebook_total=1242 notebook_last_ops=24 notebook_last_why=16 notebook_open_quests=466 notebook_closed=17 notebook_held_beliefs=416 notebook_ruled_out=11 notebook_game_text=292 trigger=27 evidence=30 map=20 screen_text=24 recent=20 reply=288 prompt_total=1657
```

`<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check ops`

```text
step 1: source_reply_chars=1083 ops_json_chars=410
  ops=[{"op": "believe", "key": "bel_game_freak_clue", "text": "The text 'GAME FREAK' is a clue or identifier for the game world", "because": []}, {"op": "add", "key": "explore_place_1", "text": "Investigate the surroundings of place 1", "why": ["bel_game_freak_clue"], "target": "place:p_place"}, {"op": "top", "key": "explore_place_1"}]
  results=[{"op": "believe", "key": "bel_game_freak_clue", "result": "ok"}, {"op": "add", "key": "explore_place_1", "result": "ok"}, {"op": "top", "key": "explore_place_1", "result": "ok"}]
step 41: omitted quests are no-ops: explore_place_1
step 41: source_reply_chars=2220 ops_json_chars=491
  ops=[{"op": "believe", "key": "bel_starting_room", "text": "Place 2 is Ash's bedroom", "because": []}, {"op": "add", "key": "explore_place_2", "text": "Investigate the objects in place 2", "why": ["bel_starting_room"], "target": null}, {"op": "add", "key": "find_exit_place_2", "text": "Find a way out of the current room", "why": ["bel_starting_room"], "target": null}, {"op": "top", "key": "explore_place_2"}]
  results=[{"op": "believe", "key": "bel_starting_room", "result": "ok"}, {"op": "add", "key": "explore_place_2", "result": "ok"}, {"op": "add", "key": "find_exit_place_2", "result": "ok"}, {"op": "top", "key": "explore_place_2", "result": "ok"}]
ERROR step 55: cannot map quest explore_place_3 source 'fact_reached_place_3' to a t-key or belief
step 55: omitted quests are no-ops: find_exit_place_2
step 55: source_reply_chars=3005 ops_json_chars=240
  ops=[{"op": "add", "key": "explore_place_3", "text": "Determine the nature of place 3", "why": [], "target": null}, {"op": "top", "key": "explore_place_3"}]
  results=[{"op": "add", "key": "explore_place_3", "result": "error: why must cite text or a belief"}, {"op": "top", "key": "explore_place_3", "result": "error: key is not open"}]
step 64: omitted quests are no-ops: find_exit_place_2
step 64: source_reply_chars=2178 ops_json_chars=408
  ops=[{"op": "believe", "key": "bel_tiled_room_home", "text": "The tiled room is part of the player's home.", "because": []}, {"op": "add", "key": "meet_prof_oak", "text": "Find and speak with PROF. OAK", "why": ["bel_tiled_room_home"], "target": null}, {"op": "top", "key": "meet_prof_oak"}]
  results=[{"op": "believe", "key": "bel_tiled_room_home", "result": "ok"}, {"op": "add", "key": "meet_prof_oak", "result": "ok"}, {"op": "top", "key": "meet_prof_oak", "result": "ok"}]
step 76: omitted quests are no-ops: find_exit_place_2
step 76: source_reply_chars=3574 ops_json_chars=349
  ops=[{"op": "believe", "key": "bel_place_4_is_outside", "text": "Place 4 is the area outside the house.", "because": []}, {"op": "add", "key": "explore_place_4", "text": "Determine the nature of place 4", "why": ["bel_place_4_is_outside"], "target": null}]
  results=[{"op": "believe", "key": "bel_place_4_is_outside", "result": "ok"}, {"op": "add", "key": "explore_place_4", "result": "error: maximum open quests reached"}]
step 99: omitted quests are no-ops: find_exit_place_2, explore_place_1
step 99: source_reply_chars=3956 ops_json_chars=100
  ops=[]
  results=[]
re-adds of finished target: 0
```

`<py> scripts/eval_plan.py --run runs/m1_bedroom_live --check observe`

```text
talk thing:e_new_game: expected=6 closed=6
talk thing:e_ash: expected=20 closed=20
talk thing:e_new_name: expected=26 closed=26
place place:p_place_2: expected=41 closed=41
place place:p_place_3: expected=55 closed=55
talk thing:e_character_3: expected=58 closed=58
place place:p_place_4: expected=76 closed=76
```

## 10. Handoff
No `handoff-notes.md` rewrite from this section; the coordinator owns program memory in MASTER. Add the Result numbers to MASTER §8/§9 via the coordinator, and note in Result anything the R3 session needs for `render_focus`.
