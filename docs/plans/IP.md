# IP: Does the planner keep what the game told it ("PROF. OAK, next door, is looking for you") and plan toward it?

Status: not started   ·   Branch: feat/lean-agent

## Paste block

    You are the orchestrator for phase IP (intent planning) of the lean agent, in repo
    C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

    1. Read, in order: CLAUDE.md, docs/plans/handoff-notes.md, docs/plans/contracts.md (sections
       "notebook and act" and "world"), docs/plans/OM3.md sections 11-13, then docs/plans/IP.md.
       Then read only the files IP.md lists.
    2. Execute the work packages in the order IP.md's "Run order" gives. Delegate each WP to a
       subagent: Sonnet orchestrator -> Haiku workers (never Opus; always set model and effort
       explicitly). Paste the CLAUDE.md rules (Ponytail full, CodeGraph first, Headroom) and the
       WP card into every subagent prompt.
    3. After each WP, run its acceptance commands yourself. A WP is done only when they exit 0.
       Requests are scarce: only IP.5's planner check may spend them (cap 8). Unit tests use
       StubModel only. Run the full suite after each parallel batch:
       `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider -n auto`.
    4. Stay inside the files IP.md lists. Never edit contracts.md; IP.md section 4 lists the
       contract text the user will merge. No game-specific code in agent/ (no "Oak", "Mom",
       "Pallet" in agent/ or its prompts).
    5. Run the gate (section 2), fill in section 9 "Result", set the Status line.
    6. Rewrite docs/plans/handoff-notes.md per section 10.
    7. If the gate passed, commit with explicit paths (never `git add -A`, never push):
       "IP: <one-line gate result>". Do not start a live run; the user decides that.

## Why this phase (diagnosis from m1_decide3, 2026-10-14)
- Steps 42-46 logged Mom's dialogue as screen fragments: `"MOM: Right." / "All boys leave"`, ... `"PROF. OAK, next" / "door, is looking"`, `"door, is looking for you."` (`runs/m1_decide3/memory/notebook.json` log).
- The planner was triggered at step 48 and 72 by `new game text was seen (see the log)` and kept its focus ("talk to the person located there", "door mat"). No goal mentioning Oak was created in the whole run; outside (steps 80-140) every focus was `talk_to_npc_p4`.
- Causes:
  1. **No fact store is written.** `Plan` (agent/plan.py:24) has only `ops` (forced to section goals, plan.py:34) and `lesson`. The notebook's `hints` section was written only by the old act call; the decide arm (`act(..., decide=True)`, agent/act.py `_decide`) writes no notebook ops, so since Phase T `hints` is always empty. What was heard lives only in the scrolling log (`render_log(40)`, plan.py:29).
  2. **The planner prompt points away from what was heard** (agent/plan.py:11-13): "When nothing tells you where to go, the goal is to explore", "Never invent places" (so "the house next door", not on the map, is not allowed as a goal), "Keep goals that still hold and change only what the trigger calls for" (so a text trigger keeps the old focus).
  3. **Speaker names never attach to people.** "MOM:" opens the reply of the person the agent talked to, but the entity stays `person`; nothing links a speaker name to the entity talked to.
  4. **Dialogue reaches the planner as fragments** (`Loop._learn`, agent/loop.py:253-258, one log entry per screen of new lines), and the trigger text does not carry the words.

## 1. Entry criteria
- Previous: OM3 section 13 committed (6ade279), ledger caps commit d754dd6. Suite green (343).
- Required on disk: `runs/m1_decide3/` (trace, truth, memory, checkpoints 000050 and 000075), `.cache/model/` (its perceiver replies), ROM in `Rom/`, `.venv-agent`.
- Ledger `runs/e3_budget.json`: total 2858/2925, M1 2810/2875 (67 left). IP.5 may spend at most 8.

## 2. Gate
| Metric | Pass | Measured by |
|---|---|---|
| Unit suite | all pass | `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider -n auto` |
| OM dry gates (no regression) | right 8/8, exit truth 8/8, size 8/8 | `.venv-agent/Scripts/python.exe scripts/eval_act.py --om cases` |
| Conversation log (0 requests) | the m1_decide3 replay's notebook log holds ONE entry for Mom's first conversation, containing `PROF. OAK, next door, is looking for you.` as one sentence, attributed to the speaker | `.venv-agent/Scripts/python.exe scripts/eval_act.py --om talk --run m1_decide3` (IP.5) |
| Speaker name (0 requests) | after the replay, the entity talked to at step 41-42 has `called: "MOM"`, and an option at a later step at that place reads `interact with MOM` | same command |
| Planner keeps what it heard (live, ≤3 requests) | from checkpoint 50 (in Mom's dialogue), ≥2 of 3 tries return a fact whose text matches `/oak/i` and `/next door/i`, and a goal with `source: "heard"` whose text matches `/oak/i` | `.venv-agent/Scripts/python.exe scripts/eval_act.py --om plan --run m1_decide3 --from-step 50 --tries 3 --live` |
| Planner acts on it (live, ≤3 requests) | from checkpoint 75 (outside, just left the house), ≥2 of 3 tries choose a focus whose goal path (focus and ancestors) matches `/oak|next door/i` | `... --om plan --run m1_decide3 --from-step 75 --tries 3 --live` |
| Requests | ≤ 8 total, ledger before/after printed | the two commands above |

- **Decision this gate produces:** whether a clean-boot live run is worth funding (user decides; needs a cap raise).
- **If it fails:** report each try's reply (goals, facts, focus, why) in the Result; do not iterate on prompt wording with live spend. The dry parts (IP.1-IP.3) stand on their own.

## 3. Read list
- `agent/plan.py` (all, 37 lines): SYSTEM, REPLY, SCHEMA, `Plan`, `plan()`.
- `agent/notebook.py`: `SECTIONS`, `apply` (lines 20-45), `note`, `render_log`, `render_focus` (line 77), `render` (line 84).
- `agent/loop.py`: `_learn` (253-258) and its two callers (84, 231); the plan call block (85-94); the relation code that records what a target `says` (search `PRED[effect]`, around line 195-205); `_plan_trigger` (299-310); `state`/`restore` planner dict (312, 323).
- `agent/world.py`: `options` thing rows (lines ~790-805: `approach {name}`, `interact with {name}`, `go to {name} (...)`), `view` thing lines, `resolve` (456).
- `agent/memory.py`: `observe` (entity rows, ~259-310), `know` (402).
- `scripts/eval_act.py`: `Replay`, `om_truth`, `om_branch` (the branch pattern IP.5 copies), `om_main`, argparse in `main`.
- Tests to extend: `tests/agent/test_loop.py`, `tests/agent/test_world.py`, `tests/agent/test_options.py`, `tests/agent/test_notebook.py`, and wherever `from agent.plan import` appears in tests/agent.
- CodeGraph query to start: `codegraph_explore "Loop._learn plan Plan Notebook.apply render_focus World.options resolve _plan_trigger"`.

## 4. Contracts
- **Uses:** contracts.md#notebook-and-act, contracts.md#world.
- **Proposed additions (the user merges them into contracts.md; the orchestrator does not):**
  - `Plan` gains `facts: list[dict]`; the planner reply gains `"facts": [{"key", "text", "from", "source": "heard|saw"}]`. Facts are stored as notebook `hints` ops (section unchanged; CAPS hints 20 unchanged) and rendered under the heading `facts (what the game told or showed):` instead of `hints:` in `render` and `render_focus`.
  - Entity rows may carry `called: <speaker name>`; World labels (options, view) print `called` when present; `resolve` matches it.
  - Log entries for dialogue become one entry per conversation: `<speaker or "text"> at <place>: "<joined sentence(s)>"`.
  - `new game text` trigger carries the conversation text.

## 5. Work packages

| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| IP.1 | One conversation = one log line | `agent/loop.py` (`_learn` + new buffer), `tests/agent/test_loop.py` | — | Haiku |
| IP.2 | Speaker names attach to the person talked to | `agent/memory.py` (entity `called`), `agent/world.py` (labels, resolve), `tests/agent/test_world.py`, `tests/agent/test_options.py` | — | Haiku |
| IP.3 | Planner returns facts; facts stored and shown | `agent/plan.py` (REPLY/SCHEMA/Plan/parse), `agent/notebook.py` (render headings), `agent/loop.py` (apply facts after plan, lines 85-94), tests | IP.1 (both touch loop.py) | Haiku |
| IP.4 | Planner rule: heard goals outrank exploring | `agent/plan.py` (SYSTEM only), `agent/loop.py` (`_plan_trigger` text trigger) | IP.3 (same files) | Sonnet (prompt wording) |
| IP.5 | Checks: `--om talk` (0 requests) and `--om plan` (live, capped) | `scripts/eval_act.py`, `tests/agent/test_om_eval.py` | IP.1-IP.4 | Haiku |

**Run order:** IP.1 ‖ IP.2 → IP.3 → IP.4 → IP.5   (‖ = parallel; IP.1 and IP.2 own disjoint files)

### IP.1: one conversation, one log line
- **Goal:** the planner and decide call see `MOM at tiled room: "Right. All boys leave home some day. It said so on TV. PROF. OAK, next door, is looking for you."` instead of five fragment entries.
- **Change:** in `Loop._learn` (agent/loop.py:253), keep a conversation buffer while the screen is not `world` (dialogue/menu): append each fresh line; when a new line extends the previous one (the previous line is a prefix of it, or the previous line's tail overlaps its head, as with `"PROF. OAK, next"` then `"PROF. OAK, next door, is looking ▼"`), replace instead of append; drop the `▼`/`▶` continuation marks. Write ONE `notebook.note` when the conversation ends (the next perception is a `world` screen, or the place changes), set `text_pending = True` then, and keep the joined text for the trigger (IP.4). A speaker prefix `NAME:` on the first line (regex `^([A-Z][A-Za-z.' -]{0,19}):\s*`, at least two letters) becomes the entry's head: `<NAME> at <place>: "<rest>"`; without one: `text at <place>: "<joined>"`. Text seen on a `world` screen (signs, single lines) keeps today's behaviour. `Memory.know` rows stay per line (unchanged).
- **Persist:** the buffer goes in `state()["planner"]` and back in `restore` so checkpoints keep a conversation in progress.
- **Acceptance:** a new test in `tests/agent/test_loop.py` feeds the five m1_decide3 fragments (steps 42-46, copied verbatim from `runs/m1_decide3/memory/notebook.json`) then a world screen, and asserts exactly one new log entry whose text contains `PROF. OAK, next door, is looking for you.` and starts with `MOM at`; plus a sign line on a world screen still logs immediately. `pytest tests/agent/test_loop.py -q`.
- **Risks:** a menu (not dialogue) also has text: only join while consecutive steps stay off `world`; a run ending mid-conversation loses the entry (acceptable; the buffer is in state).

### IP.2: speaker names attach to the person talked to
- **Goal:** after talking to Mom, her entity is known as `MOM`, and options say `interact with MOM (talked; same text as before)`.
- **Change:** where the loop relates a target to the text it produced (`PRED["text_appears"]`, "says", around agent/loop.py:195-205) — if the target is an actor (`ACTOR_KINDS` in agent/world.py:17) and the first new line has a speaker prefix (same regex as IP.1), upsert the entity row with `called: NAME` (first one sticks, like place names). In `World.options` thing rows (agent/world.py ~794-803) and `World.view` thing lines, print `called` instead of the perceived name when present (option `target` for fail keys stays the perceived name, so failure notes keep matching). `World.resolve` (456) also matches `called` (casefold).
- **Note:** this owns `agent/memory.py`/`agent/world.py` only; the one-line hook in `agent/loop.py` is added by IP.3's worker after IP.1 merges (to keep file ownership disjoint), using a helper `Memory.call(entity_id, name, step)` that IP.2 provides.
- **Acceptance:** tests: `Memory.call` keeps the first name; an options test where an actor entity row has `called: "MOM"` lists `interact with MOM`; `resolve("mom", ...)` finds it. `pytest tests/agent/test_world.py tests/agent/test_options.py -q`.
- **Risks:** moving NPCs get new ids each sighting outdoors (m1_decide3: 39 actor ids), so a name may not follow them; out of scope here (see section 8).

### IP.3: the planner keeps facts
- **Goal:** what the game says is kept as a fact the planner and the decide call always see, not as a scrolling log line.
- **Change:**
  - `agent/plan.py`: REPLY adds `"facts": list of {"key": short id, "text": one sentence of what you were told or shown that matters for goals (who, where, what to do), "from": who said it or null, "source": "heard|saw"}, [] if nothing new`; SCHEMA adds `"facts": {"type": "array", "items": {"type": "object"}}` (not required); `Plan` gains `facts: list`; parse keeps dict items with a non-empty `text`, `key` ≤40 chars.
  - `agent/loop.py` plan block (85-94): after `self.notebook.apply(p.ops, ...)`, apply `[{"op": "set", "section": "hints", "key": f["key"], "value": {k: f[k] for k in ("text", "from", "source") if f.get(k)}} for f in p.facts]`. Also add IP.2's `Memory.call` hook here (see IP.2).
  - `agent/notebook.py`: `render` and `render_focus` print the hints section under `facts (what the game told or showed):`. Keep facts before lessons when trimming (`render` drop order: lessons first, then finished goals, then hints).
- **Acceptance:** tests: a StubModel plan reply with one fact lands in `notebook.data["hints"]` and appears in `render()` and `render_focus()` under the new heading; a reply without `facts` still parses. `pytest tests/agent/test_loop.py tests/agent/test_notebook.py -q`.
- **Risks:** facts duplicate across replans: keys overwrite (set), so the model should reuse keys; acceptable.

### IP.4: purpose before exploring
- **Goal:** a goal from something heard outranks exploring; heard places may be goals before they are mapped; a text trigger is checked for a new goal.
- **Change (agent/plan.py SYSTEM, game-neutral wording):**
  - Replace "When nothing tells you where to go, the goal is to explore: ..." with: "What the game tells you comes first. When text names a person, place or task (someone is looking for you, go somewhere, get something), add it as a goal with source heard, above exploring, and keep it until its done_when is seen or it is shown false. Explore only as a child of a goal, to find what that goal needs; with no such goal, explore to find one. ..." (keep the existing sentences about unverified exits and beyond-summaries).
  - Replace "Never invent places, items or routes." with: "Never invent places, items or routes; a place you were told about is not invented: it may be a goal before it is on the map (source heard), and its done_when says how you will recognise it."
  - Replace "Keep goals that still hold and change only what the trigger calls for." with: "Keep goals that still hold. When the trigger is new game text, first record what it tells you as facts, then check whether it gives a new or more urgent goal."
  - Add: "Facts are what the game told or showed; trust them over guesses."
- **Change (agent/loop.py `_plan_trigger`, line 307):** the text trigger reads `new game text: <joined conversation from IP.1>` (≤200 chars) and is checked before the 3-step floor (line 303), like `urgent`.
- **Acceptance:** `pytest tests/agent -q -n auto` (prompt-content assertions in existing tests may need their expected strings updated; update them, do not delete them); `grep -riE "oak|mom|pallet" agent/` returns nothing.
- **Risks:** the planner may now chase any line of text; the done_when / shown-false clause and the facts store are the brake. Measured in IP.5.

### IP.5: checks
- **`--om talk --run R` (0 requests):** run `om_truth`'s replay (recorded decisions, cached perception) and print, for the replayed steps: the notebook log entries that came from dialogue, and every entity row with `called`; then print the options of the first later step at the same place that offers an `interact with` row. Exit 0 when one entry contains `PROF. OAK, next door, is looking for you.` and some entity has `called` (the gate's text check is in the Result, not hard-coded in agent/).
- **`--om plan --run R --from-step N --tries K [--live]` (≤ K requests, only with `--live`):** restore the run_agent checkpoint like `om_branch` (copy `checkpoint/<N>/memory` to `.tmp/om/<R>_plan_<N>/`, `Loop.restore(harness.json)`), build the planner input exactly as `Loop.step` does (`World.view(goal_text, place)`, `notebook`, `per["text"]` from the checkpoint's cached perception, recent rows), and call `plan()` K times through `Ollama(OM_MODEL, cache_dir=None)` so tries are not served from cache; each request goes through `run_agent._ledger(Path("runs/m1_om"), None, 8)`. Without `--live` print the prompt only (0 requests). Print per try: facts, goal edits, focus, the focus's goal path, why. Use the trigger `new game text: <the log's last dialogue entry>` at step 50 and `arrived somewhere new: <place label>` at step 75.
- **Acceptance:** `tests/agent/test_om_eval.py` gets one test for each mode with a StubModel and a tiny fake checkpoint (or monkeypatched restore); then the gate commands in section 2, live ones last.

## 6. Test layers
| Layer | Gates this phase? | Command |
|---|---|---|
| Unit | yes | `pytest tests/agent -q -p no:cacheprovider -n auto` |
| Replay (recorded run, 0 requests) | yes | `scripts/eval_act.py --om talk --run m1_decide3`; `--om cases` (no regression) |
| Live planner (checkpoints) | yes, ≤ 6 requests | `scripts/eval_act.py --om plan ... --live` |
| Full live run | no (user decides after the gate) | `scripts/run_agent.py --run runs/m1_ip --steps 150 --max-calls 250 --decide` |

## 7. Budgets
- **Lines:** report only (no strict budget, per the user's 2026-10-12 decision). Expect ~+120 in agent/, ~+80 in scripts/, ~+80 in tests.
- **Requests:** ≤ 8 for the whole phase (6 planned). Ledger now total 2858/2925, M1 2810/2875; do not raise caps.

## 8. Out of scope
- Moving-NPC identity (one person, many ids outdoors: 39 actor ids in m1_decide3). Recommended next phase: match an actor to the nearest known actor within a few tiles regardless of the perceiver's label, so `called` names and talk history follow people.
- Explore route failures outside (`approach unexplored: route blocked` twice at m1_decide3 step 80) and the ~2.4 requests/step outdoors (perceiver re-looks): investigate separately.
- Any game-specific text in agent/; any change to act/decide output shape; option filtering by history.

## 9. Result (filled in by the orchestrator)
- **Gate:** each metric with its number and command output path (`.tmp/ip/`).
- **Decision taken:** live run recommended or not.
- **Deviations from the packet:** ...
- **Contract changes needed:** the section 4 additions as merged-ready text.
- **Open issues for the next phase:** ...

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` (≤ 80 lines, the template's fixed headings). Must include: the facts store and where it renders; `called` names; the conversation log format; the new `--om talk` / `--om plan` commands; ledger numbers after IP.5; the m1_decide3 findings still open (moving-NPC ids, explore route blocks, request rate).
