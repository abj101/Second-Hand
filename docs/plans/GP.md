# GP: Goal planner — goals worked backwards to one focus, plus a done log

Status: STOP 7a (built `aa301bb` + follow-ups; offline eval done 2026-10-13; awaiting live approval) · Branch: feat/lean-agent · Follows: [PS.md](PS.md) §9c · User decision 2026-10-13: Option B (event-triggered planner)

## 0. Why (evidence)
- **Run `runs/m1_ps_fp`, steps 81–180.** The agent never left the house and its party stayed at 0. Spend: 113 requests. Haiku extracted the evidence from `notebook.history` and the trace.
  - **Churn.** There were 29 notebook edits in 100 steps. `find_prof_oak` and `leave_room` were each set and removed about 10 times, under three different wordings.
  - **Nothing gets finished.** No goal reached `done` after step 19. "Leave the house" and "Find Prof. Oak" stayed active from step 130 to 180, and nothing touched the notebook after step 130.
  - **Repeats.** The agent re-talked to Mom for about 40 steps (93–131). Nothing recorded that this conversation was done or what she said ("All boys leave home some day", "PROF. OAK, next door, is looking for you").
  - **No working backwards.** The agent held "Find Prof. Oak" and "Leave the house" but never derived "find the way out on 1F → the untried door/rug". It reached for the stairs `E` exit 8 times.
- **Dialogue is forgotten.** `Memory.know` stores every game text line in `knowledge.jsonl`, but no live code reads it back. Only the dead `Memory.view` does. Once a line scrolls off the screen, act never sees it again. The log (§2.1) carries it from now on.
- **Root cause.** One act call per step reads the screen, names the place, edits the goals and picks the button. Goals are a flat list with no parent, no completion test, no record of what was tried, and no log of what is done. Planning only happens as a side effect of choosing a button.
- **Why this is not the planner P4 deleted** ([P4.md](P4.md), [autonomy-redesign.md](autonomy-redesign.md) §1).
  - The deleted planner could only emit code-defined conditions (`new_place / text_seen / …`), so dialogue could never become a goal.
  - This planner is the model writing its own goals in its own words, in a dedicated call. The autonomy-redesign evidence ("model-written goal stacks beat fixed curricula") supports this.
  - No code picks or overrides actions (program §2 rule 4; memory: agent autonomy over gating).

## 1. Research (Haiku, 2026-10-13)
- **Prior art.** Claude Plays Pokémon, Gemini Plays Pokémon, Voyager, ADaPT, DEPS, Reflexion, Plan-and-Act. Earlier survey: `.tmp/research/prompting-survey.md`.
- **What we adopt:**
  - **Planner and executor are separate calls** (Plan-and-Act). The planner receives done/blocked feedback with evidence.
  - **Each goal states how you will know it is done** (Voyager self-verification). The act call judges completion against it and quotes evidence.
  - **The done log is append-only.** Claude Plays Pokémon deleted its Mt. Moon notes, concluded the cave was beaten, and looped.
  - **Decompose further only when needed** (ADaPT). The planner splits the focus when act reports it `blocked` or there is no progress, not up front.
  - **Replanning gets a description of what went wrong** (DEPS). The trigger reason and the last actions go into the planner prompt.
  - **The planner may not invent walkthroughs.** Gemini's critic sent its agent to the wrong exit. Every goal needs `source: saw|heard|assumed` plus evidence, and the prompt forbids facts not on screen or in memory.
- **What we skip:** critic agents, a separate pathfinder subagent (we have `route`/`go_to`), periodic summaries, and a knowledge graph (Gemini removed its own as unused).

## 2. Design
### 2.1 Notebook shape (same file and store: `agent/notebook.py`)
- **`goals`** gain three fields:
  - `parent` (a goal key, or null for a top goal).
  - `done_when`: one observable sentence, e.g. "the screen shows outdoors / a new place".
  - `tried`: up to 3 short strings.
- **Status values:** `todo | active | done | blocked | dropped`.
- **New `focus`** holds one goal key: the leaf being worked on now.
- **New `log`**: an append-only list of `{step, text}`. Code appends to it; the model never deletes from it. Render shows the last 15 entries, and `notebook.history` keeps every entry.
  - Entries are written when a goal turns `done`, `blocked` or `dropped`, quoting its evidence.
  - Entries are written when a dialogue ends, with up to 120 characters of the text that was shown.
  - Planner lessons are also written here.
- **Trimming never orphans children.** Finished goals are trimmed only if no live goal names them as its parent.

### 2.2 Planner call (new `agent/plan.py`, role `plan`, text only, no image)
- **Input:**
  - The trigger reason.
  - The current goals as a tree, plus the focus.
  - The log.
  - Known facts: game text from memory, the same as act sees.
  - `World.view` (the map, exits and things).
  - Screen text.
  - The last 5 actions with verdicts.
- **Instruction (game-neutral):**
  - The top goal is "finish the game".
  - For each active goal, work backwards: ask "what must be true just before this?" until you reach something doable from where you are now. That leaf is the focus.
  - Use only what the screen or memory shows. If something is a guess, mark it `assumed`.
  - Do not re-create goals that the log says are done.
  - Keep goals that still hold. Change only what the trigger requires.
- **Reply:** `{"goals": [ops: set/remove with parent, done_when, status], "focus": key, "lesson": str|null, "why": str}`.
- **Triggers** (code detects events, the model decides what they mean). At most one planner call per step:
  1. There is no focus, or the focus is no longer active.
  2. Act marked the focus `done` or `blocked`.
  3. New game text was stored this step (the dialogue has advanced past it).
  4. `Stuck` reported `no_progress` or `same_action`.
  - Throttle: triggers 3 and 4 fire at most once every 3 steps. Triggers 1 and 2 always fire.
- **Cost:** about 1 extra request every 4–8 steps, judging from the event rate in the last run.

### 2.3 Act call changes (`agent/act.py`)
- **Act sees** the goal path from root to focus, each goal's `done_when` and `tried`, the last 15 log entries, plus hints and lessons. It does not see the full tree.
- **Act's notebook edits shrink** to:
  - `{"op": "focus", "status": "done|blocked", "evidence": "..."}`, where the evidence quotes the screen or the outcome;
  - `{"op": "tried", "text": "..."}`;
  - hints and lessons, as today.
- **Act can no longer create or remove goals.** The planner owns structure. This is the anti-churn rule.
  - It is not a gate on actions: act still picks any action.
- **`World.view`'s goal text** (`loop.py`) uses the root-to-focus path text.

### 2.4 Out of scope
- Mechanical completion checks (Option C).
- Ground-truth (RAM) state injection.
- A critic.
- Changes to perception or World.

## 3. Work packages
| WP | Files | Who |
|---|---|---|
| G1 notebook tree, focus, log, history, orphan-safe trim | `agent/notebook.py`, `tests/agent/test_notebook.py` | main |
| G2 planner call and trigger wiring | `agent/plan.py` (new), `agent/loop.py`, `tests/agent/test_plan.py`, `tests/agent/test_loop.py` (role test updated deliberately) | main |
| G3 act prompt and restricted ops | `agent/act.py`, `tests/agent/test_act.py` | main |
| G4 offline eval script | `scripts/eval_plan.py` | Haiku (mechanical, from the spec in §4) |

## 4. Offline eval: the gate before any live spend
- **Situation.** Start from `runs/m1_ps_fp/checkpoint/000080` memory: the floors are still clean there, 1F = `p_world` and 2F = `p_world_2`. Mom's dialogue is in knowledge. The frame is step 81 (1F, just after Mom).
- **Cases:**
  - **P1, planner from scratch.** The notebook has only "finish the game" and the log is empty; the trigger is new game text. **Pass:**
    - the tree contains a goal about Oak or leaving home;
    - the focus is about finding the way out of the current building, or about the door/rug/an untried exit on this floor;
    - the focus is not the stairs or going upstairs.
  - **P2, planner with history.** The log contains "talked to Mom: 'All boys leave home some day…'", and the focus "leave the house via the stairs" has just been marked `blocked` with the evidence "stairs lead to 2F where you started". **Pass:** the new focus is the untried door/rug/exit on 1F, and Mom is not targeted again.
  - **A1, act with a focus.** The P1 or P2 focus is given. **Pass:** the action is `go_to` or approach the door/rug, or a move toward the bottom of the room. It is not Mom and not the stairs.
- **Two tries per case** at temperature 0.2. **Gate:** P1 2/2, P2 2/2, A1 ≥ 1/2. **Cap:** ≤ 16 requests (the M1 ledger has 88 left).
- `pytest tests/agent` must stay green.

## 5. Stops
- **STOP 7a.** After the offline eval, the user approves live spend.
- **STOP 7b.** After a live run from checkpoint 80 (≤ 80 requests).

## 9. Result (2026-10-13)
- **Built.**
  - `agent/plan.py` is the planner call.
  - The notebook now has a goal tree, a `focus`, and an append-only `log` (goal closures, game text, lessons, and **moves between places**).
  - Act sees the root-to-focus path plus the log, and may only close or annotate the focus.
  - Trigger wiring is in `Loop._plan_trigger`.
  - `Notebook._save` retries `os.replace` on Windows `PermissionError`; Haiku found it failing intermittently.
  - `pytest tests/agent`: 243 passed.
- **Offline eval** (`scripts/eval_plan.py`; checkpoint-80 memory; frame 82; 16 requests over 2 rounds; M1 ledger now 1178/1250):

  | Case | Round 1 (log = game text) | Round 2 (log + place moves) |
  |---|---|---|
  | P1: planner from scratch | 0/2 (focus: stairs) | 0/2 in substance (both plan to leave via the stairs; try 1's focus text "Go to the house exit" passed the regex, but its act heads for the stairs) |
  | P2: stairs blocked, with history | 2/2 ("Find the exit door" → "Leave via the door") | 2/2 ("explore the ground floor for a door") |
  | A1: act on the P2 focus | n/a (frame 81 is dialogue, so `advance` was correct) | 2/2 (moves LEFT/DOWN, toward the real door at bottom-left) |

- **Reading.**
  - Backward reasoning works once a failure is known. P2 decomposes "leave house" → "find the door" and act heads for it.
  - From scratch the planner picks the only exit memory knows, the stairs. Memory at step 80 holds no door or rug on 1F, and both floors are named "world", so nothing says the stairs go back up to the starting bedroom.
  - The expected live behaviour is therefore ADaPT-style: one stairs round trip, then act marks the focus `blocked` ("arrived back in the bedroom"), the planner replans, and the agent heads for the door. The pre-registered P1 gate fails.
- **Not done.** P1 might pass with distinct place names (e.g. "bedroom" / "living room"), but that would be a perception or naming change outside this packet.
