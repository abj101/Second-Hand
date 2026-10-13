# GP: Goal planner — goals worked backwards to one focus, plus a done log

Status: STOP 7d (option-C clean-boot run reached Pallet Town at step 113, then bounced at its own door; ledger exhausted) · Branch: feat/lean-agent · Follows: [PS.md](PS.md) §9c · User decision 2026-10-13: Option B (event-triggered planner)

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

### 9b. Live run from a clean boot (`runs/m1_gp`, 150 steps, 2026-10-13)
- **Setup.** The user approved a clean boot with empty memory and caps raised by 200 (total 1500, M1 1450).
- **Spend:** 248 requests. Ledger is now total 1474/1500 and M1 1426/1450.
- **Outcome:**
  - The intro and naming took steps 1–45; both names were entered.
  - From step 46 to 150 the agent stayed in the 2F bedroom.
  - It visited only tiles x 1–5, y 6–7. The stairs at (7,1) were never on screen.
  - Party 0. Verdicts: hit 98, miss 37, surprise 14.
- **What worked (tracking):**
  - 22 planner calls.
  - 7 goals were closed `done` with evidence: start game, name, rival name, read the intro, dismiss the dialogue/menu, …
  - No set/remove churn, and no finished conversation was repeated.
- **What failed:**
  1. **The planner micro-manages.**
     - 17 of the 22 calls came from `stuck` triggers. The planner answered with button-level goals: "press B", "try Start", "navigate to CANCEL", "try a different direction", "interact with the plant/box/cabinet".
     - Nothing above "explore the current room" ever existed, because the intro text gives no destination.
  2. **No systematic exploration.**
     - From step 45 on, act used `buttons` 90 times and `go_to` 0 times.
     - It concluded that "UP and DOWN fail" from the one blocked tile at (3,6). The view's frontier ("untried edges") was never used, so it never walked right and then up to where the stairs are.
  3. **About 35 steps (50–85) were lost** to the Start menu and Options after the SNES text: dismissing a dialogue box with START opened the menu.
- **Reading.** Goal bookkeeping is fixed. The bottleneck is now (a) goals that are moves instead of outcomes, and (b) having no cheap way for the model to say "go somewhere I haven't seen". In `m1_long` the agent left 2F at step 37 via a `go_to`/approach that routed it to the stairs.

### 9c. Option C: outcome goals, `go_to unexplored`, pixel drift veto (2026-10-13)
- **A. Planner prompt.**
  - Goals must be outcomes: reach, find, talk to, get, use, win. Never buttons, directions or retries.
  - With no instruction, the default goal is to explore: unseen parts of the map, unused exits.
  - `stuck` replans wait at least 8 steps after the last plan (was 3).
- **B. `go_to "unexplored"`** is a tool the model may choose (`Loop._unexplored`). It routes to the nearest untried edge, preferring edges into unseen cells, then steps across it. It uses the existing `route` and the approach skill, and it is never forced. There is a unit test.
- **New finding: false drift corrections corrupted the map.**
  - At m1_gp step 92 the entity-median drift fix moved `pos` by (+3, −2) with no movement. Step 113 undid it. For 21 steps tiles were written about 3 off, which made a fake upper area, and that is where `unexplored` pointed on the step-100 memory.
  - The stored views put the agent exactly at the true tile on both steps (score .979 / .992).
  - **Fix:** `World.observe_after` applies an entity drift only when no stored view of this place matches, or when the matching view agrees with the corrected tile.
  - `Memory.seen_view(..., only=place)` locates within one place regardless of screen type. This place was first recorded under a dialogue box, so its type is `dialogue`.
  - The stationary-drift test is now parametrised: with views it vetoes, without views it still corrects.
- **Offline P3** (m1_gp 2F stall, 8 requests): not a clean test. The checkpoint-75 log ends inside the Options menu, so the planner rightly plans "exit the menu → select CANCEL". With the inherited goals cleared, its top goal became the outcome "explore the world (done when all map edges are tried)".
- `pytest tests/agent` is green. **Ledger:** total 1482/1500, M1 1434/1450.

### 9d. Live clean-boot run with option C (`runs/m1_gp2`, 2026-10-13)
- **Setup:** caps +200 (total 1700, M1 1650). The run stopped at step 137 on the 210-request cap. Ledger: total 1692/1700, M1 1644/1650.
- **Progress vs `m1_gp`** (which never left 2F in 150 steps):

  | Milestone | Step |
  |---|---|
  | Intro done | 46 |
  | Reached 1F | 84 (via the stairs, while trying things in the room) |
  | Talked to Mom | 107 (logged: "MOM: Right. All boys leave home…") |
  | Planner focus: the door mat ("a known exit exists on the map") | 107 |
  | **Out the front door into Pallet Town (map 0)** | **113** |

  Party 0.
- **Healthy signs:** no false drift fixes (0, against 2 in m1_gp). The planner closed goals with evidence. The log carried Mom's line and every move.
- **Failure after step 113: bouncing at its own front door about 10 times** (town ↔ 1F).
  1. **Bug, fixed.** `go_to "unexplored"` at step 114 walked straight back in. The door tile's *inferred* exit edge counts as untried in `frontier`, so it was the nearest "unexplored" edge at distance 0. `_unexplored` now skips any edge that is an exit, observed or inferred. The regression test fails without the fix.
  2. **Open.** Outside, act kept choosing "enter the house to find Prof. Oak". "Next door" was read as *this* door, and both buildings are just `world [p_…]`, so nothing names "my house" versus another house.
  3. **Open.** The focus set inside the house (`reach_room_edge`) was not replanned on arriving outdoors. Arriving in a new place is not a planner trigger.
- **Next candidates (not built):**
  - Make "arrived in a place not visited before" a planner trigger.
  - Name places from what the model sees, so "next door" can mean "not the house I came out of".

### 9e. Name-entry stall (`runs/m1_gp3`, `runs/m1_gp4`, 2026-10-13; preferred names removed per user, `9a5b1bd`)
- **Symptom.** Steps 21–93 were spent on the name keyboard. Pressing `A` entered the letter "A" twice (steps 31 and 41), but `type` always failed with "keyboard not identified", and about half the replies produced no action.
- **Causes, all fixed in `962661e`:**
  1. The model replies `{"type": "RED"}` or `{"verb": "type", "text": "RED"}`. `act` dropped the first shape and ignored text inside `action`, so there was no action or an empty text.
  2. `type_text` rejected correct keyboard readings whose rows were lists of cells, or whose `done_key` was null.
  3. `type_text` with empty text read back the "A" the agent had entered and **erased it with B**.
  4. The prompt never said that picking an offered preset is fine. Before today the "Hand" preference pushed it to type.
  5. A reply with no valid action changed nothing and said nothing. It now raises a flag on the next step.
- **Offline, real model + emulator:**
  - `type_text("RED")` on the m1_gp4 step-25 save state typed the name, pressed the done key, and RAM `player_name = RED`. The game continued ("Right! So your name is RED!"). 2 requests.
  - Act on the name menu (frame 20: NEW NAME / RED / ASH / JACK) chose `select RED` 2/2.
  - Act on the question box (frame 19) now returns `type` with text "Red", parsed correctly.
- **Ledger:** total 1886/1975, M1 1838/1925.
