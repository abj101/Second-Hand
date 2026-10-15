# QS: Quest state — a planner that keeps a line of thought, revises what it believes, and stays out of tactics

Status: design approved 2026-10-14 (§12 defaults accepted); build packet: [QS.md](QS.md) · Branch: feat/lean-agent · Date: 2026-10-14
Follows: planning audit 2026-10-14 (memory `project-planning-audit`), [GP.md](GP.md), [IP.md](IP.md), [HF5.md](HF5.md)
Out of scope (backburner): cross-episode memory / embeddings (same-game recall, decided 2026-10-14).

## 0. Problem (measured, 4 runs)

| Symptom | Evidence | Cause in code |
|---|---|---|
| Planner micromanages act | HF: 41 plan calls / 144 steps; 24 triggered by single action failures; 6 of 10 leaves are doors/coords (`try_door_2_7_5`…) | `loop.py:305` makes every new failure urgent (skips throttle); `plan.py:6-7` forces the focus down to "something startable now"; `plan.py:14-15` tells it to pick another exit |
| Older work silently ignored | `find_grandpas_lab` (heard step 92) never focused; `find_prof_oak` open 85+ steps, never closed; 2–5 active goals removed per run | `remove` allowed on active goals; act sees only root→focus path (`notebook.py:77`); no code reads `done_when` |
| No continuity of reasoning | each call re-derives intent from goal keys; `why` is discarded | `plan()` is stateless: tree + last 40 log lines only (`plan.py:30-33`) |
| Mistakes lock in / plans churn | same door blocked 5× under new keys; `set` overwrites `tried`/`evidence` | edits-as-diffs, no invariants, `set` replaces whole dict (`notebook.py:41`) |
| Act can't close anything | decide arm returns `ops=[]` (`act.py:102`); HF act ops = 0 every step | closure lives only in the planner |

Root cause: one stateless call is strategist + failure handler + tree bookkeeper, woken by tactical events.

## 1. Design intent

**The planner interprets what matters and keeps the strategy; act executes it.**

- Planner = strategist. Reads important information (game text, new places, a real stall), keeps **facts**, **beliefs**, **quests** and its own **notes**, and pivots by *revising beliefs*, not by renaming goals.
- Act = tactician. Pursues the top quest through the option list; failures stay in its domain (option notes, `fails`, flags).
- Code = bookkeeper. Owns invariants, diffs, mechanical done-checks, compaction triggers, and history. The model never edits structure by diff.

## 2. State model (`notebook.json` v2)

Everything is append-or-revise; nothing model-written is ever deleted, only superseded with a reason. `notebook.history` keeps every change.

| Section | Written by | Shape | Lifetime | Revisable? |
|---|---|---|---|---|
| `facts` | planner (from text/screen), code (speaker lines) | `{key, text, from, source: heard\|saw, step, place}` | whole run, never compacted | no — only marked `superseded_by` when the game itself contradicts it |
| `beliefs` | planner | `{key, text, because: [fact/obs refs], confidence: low\|mid\|high, status: held\|revised\|refuted, step, history: [{step, from_text, reason}]}` | until refuted; refuted ones fold into the chapter's "ruled out" | yes, with a reason (logged) |
| `quests` | planner (whole list each call), code (status via checks) | ordered list, ≤5 open: `{key, text, why: fact/belief refs, done_when, check?: {kind, arg}, status: open\|done\|dropped\|parked, evidence, opened, closed, approach?}` | open until done/dropped with evidence | text/order/approach yes; removal no |
| `notes` | planner, one per call | `{step, trigger, text ≤ 400 chars}` — what changed in my beliefs, why the quest order is what it is, what I expect next | current chapter verbatim | no (they're a journal) |
| `chapters` | compaction call | `{from, to, closed: [quest keys], summary ≤ 600 chars, ruled_out: [...], open_leads: [...]}` | whole run | no |
| `log` | code (unchanged) | events: text, moves, closures | cap 200, planner sees chapter-scoped tail | — |

`approach` is a planner *suggestion* for act ("try the houses south of here"), never a door or button. `check` is optional and only of kinds code can verify (§5).

## 3. Components and data flow

```
            game text / new place / quest closed / stall / fallback-empty
                                   │ (triggers, §4)
                                   ▼
 ┌────────────── PLANNER call (strategist) ──────────────┐
 │ in : trigger + evidence since last call (§4.2)        │
 │      facts (all), beliefs (held), quests (all open +  │
 │      last 5 closed), chapters, current-chapter notes, │
 │      map summary (world.view)                         │
 │ out: quests (full list), beliefs (revise/add),        │
 │      facts (new), note, why                           │
 └───────────────────────────┬───────────────────────────┘
                             ▼
 ┌──────────── CODE: reconcile (notebook.apply_plan) ────┐
 │ diff new quest list vs old; enforce invariants (§6);  │
 │ log every change; keep omitted open quests + flag;    │
 │ attach mechanical checks; persist history             │
 └───────────────────────────┬───────────────────────────┘
                             ▼
 ┌──────────────── ACT / DECIDE (tactician) ─────────────┐
 │ in : top quest (text, done_when, approach), other     │
 │      open quests (one line each), facts, options with │
 │      failure notes, flags                             │
 │ out: one option (decide arm unchanged: no ops)        │
 └───────────────────────────┬───────────────────────────┘
                             ▼
 ┌──────────── CODE: after each step ────────────────────┐
 │ mechanical checks on ALL open quests (§5) → close     │
 │ with evidence; progress tracker (new place / first    │
 │ edge); stall detector; quest-closed → compaction?     │
 └───────────────────────────────────────────────────────┘
```

Unchanged interfaces: `world.options`, `world.view(goal_text, place)` (goal_text = top quest + approach), `act()` signature (the `notebook` string it receives changes content only), trace row shape (`plan` row gains `beliefs_changed`, `quests_changed`).

## 4. Triggers

### 4.1 When the planner runs (replaces `Loop._plan_trigger`)

| # | Trigger | Rule |
|---|---|---|
| T1 | Game text | a complete dialogue/sign reading ended (existing `text_pending`, on return to `world` screen) |
| T2 | New place | first visit to a **navigable** place (`screen == "world"`); menus/title screens don't count |
| T3 | Quest closed | a mechanical check or the planner itself closed a quest |
| T4 | Stall | no **progress** for N=20 steps (progress = first visit of a navigable place, first verification of an edge, a quest closing, or new game text); at most once per N steps |
| T5 | Empty | no open quest (start of run) |

Removed: per-failure urgency (`loop.py:305`), "same action failed twice", cycles, revisits. They remain act-facing information (option notes, flags) and feed T4's evidence. Re-entering a known place is not an event.

### 4.2 Evidence packet (what the planner gets beyond its state)

Code-built, short: places discovered since last call; exits tried and their outcomes, grouped (e.g. "outside house: 5 exits tried, 4 failed route, 1 refused"); text heard; quests the checks closed; step count since last progress. A stall call therefore sees *what was tried as a whole*, which is what makes pivoting by belief revision possible.

## 5. Mechanical quest checks (code-owned closure)

The planner may attach a `check` whose kind code can verify generically (no game-specific names in `agent/`):

| kind | arg | passes when |
|---|---|---|
| `reach` | place label as the game/agent names it | current place label matches (canonical place, merged ids resolved) |
| `speak` | speaker name | a dialogue line with that speaker is logged after the quest opened |
| `text` | phrase | screen text containing the phrase is logged after the quest opened |

Checks run on **every open quest every step**, so older quests close when satisfied even if act was working on something else. Quests without a check close only via the planner (on T1–T4) with evidence. A check never *opens* or *drops* a quest.

## 6. Invariants (enforced in `notebook.py`, unit-tested)

1. A quest with `why` referencing a heard fact can't disappear: omitted from the new list → kept as-is and flagged to the next call ("you omitted X; keep, park or drop it with a reason").
2. `done`/`dropped` require non-empty `evidence`; `dropped` of a heard quest also requires a belief or fact reference that explains it.
3. ≤5 open quests; extra ones are `parked` (kept, shown one line), never discarded.
4. A quest text naming coordinates, door numbers, buttons or directions is rejected at parse (regex on `#\d`, `\(\-?\d+,\-?\d+\)`, button names) — returned as a flag next call.
5. Belief revision always appends to `history` with the reason; `refuted` beliefs stay listed as "ruled out" (prevents relapse).
6. Re-setting a quest merges fields; `evidence`, `opened`, check history are never overwritten.
7. Facts are immutable; the planner can only add.
8. Nothing is deleted from disk state; caps hide, history keeps.

## 7. Compaction (chapters)

- **When:** a heard quest or the top quest closes (T3); or current-chapter notes exceed 12 (size fallback).
- **What:** one extra model call ("chapter") with current-chapter notes + log span + closed quests → `{summary, ruled_out, open_leads}`.
- **Guard:** code verifies every open quest and every held belief is either still in state or named in `open_leads`; if not, the chapter is rejected and notes stay verbatim (fail safe = more context, not less).
- **Cost:** ~1–3 extra calls per 150-step run.

## 8. Not locking into mistakes — how a pivot happens

1. Every quest cites *why* (fact or belief). A pivot is a belief revision: when evidence (§4.2) contradicts a belief, the planner marks it revised/refuted with a reason, and quests resting on it are re-ordered, parked or dropped *with that reference*.
2. Stall calls must change something: if a T4 call returns unchanged beliefs and quest order, code records `stall_unchanged` and the next T4 comes at N/2 with the flag "last stall changed nothing; revise a belief or reorder". Two unchanged stalls in a row → code appends the fallback quest "explore an unexplored exit not yet tried" (generic).
3. Act opportunism: act sees all open quests one line each, and §5 checks close any of them, so an older quest that becomes reachable gets done without a replan.
4. Upstream errors (e.g. place merge) surface as beliefs with low confidence when they come from `assumed`/label-fallback data; `world` facts that later contradict them are in the next evidence packet.

## 9. Logic walk-through on recorded runs (validation before build)

Trigger replay with the §4.1 rules, 0 requests (script in session scratchpad; progress there approximated by `verified_edge`, see note):

| Run | Plan calls today | Under QS | What disappears |
|---|---|---|---|
| m1_hf_checkpoint_1 | 41 | 13 | 24 failure-urgent calls; steps 95–144 door storm → 2 stall calls (112, 132) |
| m1_decide3 | 14 | 14 | failure calls replaced by stall calls (no new places for long stretches) |
| m1_gp5 | 26 | 14 | 3 dialogue "focus closed" loops, PC-menu micro-goals |

HF row uses the exact rule (first-time place/edge, text); the other two rows use the `verified_edge` approximation, which counts repeat crossings as progress. Exact HF result: last progress step 92, stalls fire at 112 and 132.

Event-by-event (HF run, real steps):

| Step | Event | Today | Under QS |
|---|---|---|---|
| 1 | start | goal `explore_place_1` | T5: quest "find out what the game wants (talk to people, read signs)"; note states no facts yet |
| 21, 41 | bedroom actions fail | 5 replans on "select television/console…" | act absorbs via option notes; T4 at 21/41 only if no progress — fine, cheap |
| 61 | Mom: "PROF. OAK, next door, is looking for you" | `find_prof_oak` added, `find_objective` removed, orphan child | T1: fact F1 (heard, Mom); belief B1 "Oak is in the house next door" (mid); quest Q1 "find and talk to Oak" `check: speak Oak`; prior quest closed `done` with evidence F1 |
| 79 | first entry to place_5 | `explore_place_5` | T2: evidence "new place, no Oak seen"; planner checks B1 vs place; keeps Q1 open, note "place_5 may be the house next door; Oak not seen yet" |
| 92 | "GARY is out at Grandpa's lab" | `find_grandpas_lab` created, never focused again | T1: fact F2; Q2 "find Grandpa's lab" (heard); invariant 1 keeps Q2 forever unless closed/dropped with reason; act sees Q2 one-line every step |
| 95–144 | doors fail, shop↔outside cycle | 20+ replans, 10 `try_door_*` goals | act handles; T4 at 112 (and 132) with packet "5 exits tried at outside house, 4 route-failed"; planner must revise: B1 "next door = place_5" → refuted? / B3 "routes here are blocked; the unexplored edge is south"; Q-order changes; no door goals possible (invariant 4) |
| any | dialogue with speaker "OAK" | never closed | §5 `speak` check closes Q1 the same step → T3 → chapter compaction |

Edge cases checked:

| Case | Outcome |
|---|---|
| Planner returns empty / malformed list | reconcile keeps old state, logs parse failure; act continues on old top quest |
| Planner hallucinates `done` | requires evidence; when a check exists, check result wins (a failing check re-opens with a flag) |
| Planner forgets an open heard quest | invariant 1 keeps it + flag |
| Text arrives during a stall | T1 fires (text resets progress), stall counter resets |
| Quest list never closes anything for 150 steps | size-fallback compaction keeps the context bounded; notes > 12 compacted |
| Wrong belief from bad perception | carries low confidence; contradicting evidence lists it; refuted beliefs stay as "ruled out" |
| Old checkpoint restored | migrate v1: open goals with `source: heard` → quests; others → chapter 0 summary text; hints → facts; lessons → notes |

## 10. Fit with the rest of the system (blast radius)

| File | Change |
|---|---|
| `agent/notebook.py` | v2 schema, `apply_plan` (reconcile + invariants), checks, render for planner/act, v1 migration, compaction guard |
| `agent/plan.py` | new prompt (strategist only; no "work backwards to a startable leaf", no exit choosing), schema: quests/beliefs/facts/note; chapter call |
| `agent/loop.py` | `_plan_trigger` (T1–T5, progress tracker), drop failure urgency at `:305` (keep `cause` for act/evidence), evidence packet, run checks each step, `_goal_text` = top quest + approach |
| `agent/act.py` | act/decide prompts: "work on the top quest; other open quests listed" (wording only) |
| `scripts/watch_run.py` | goals panel → quests / beliefs / notes |
| `scripts/eval_run.py` | `goals_with_evidence` → quests; add lifecycle metrics |
| `scripts/eval_plan.py` | cases rewritten against v2 |
| tests | `test_notebook.py` (invariants, migration), `test_loop.py` (triggers, checks), new `test_plan.py` (parse, reject door-level quests) |
| `contracts.md` | "notebook and act" section text — user merges |

Not touched: perception, world memory, options, skills, travel.

## 11. Gate (proposed)

| Metric | Pass | Measured by |
|---|---|---|
| Unit invariants | all §6 tested, StubModel only | `pytest tests/agent -q` |
| Plan calls on HF trigger replay | ≤ 15 (measured 13) | replay script, 0 requests |
| Heard leads silently lost (live run) | 0 | lifecycle metrics from `notebook.history` |
| Door/coord/button-level quests | 0 (rejected at parse) | same |
| Belief revisions with reason on stall calls | ≥ 1 per stall that follows a stall | same |
| Clean-boot live ≤ 150 steps | reaches the house next door and logs Oak's dialogue, or a stall call shows a belief revision explaining why not | live run, request cap set by user |

## 12. Decisions (approved by the user 2026-10-14)

1. Stall window N = 20 steps (HF fires at 112 and 132).
2. At most 5 open quests; extras are parked.
3. Act/decide never edits state; closure = mechanical checks + planner.
4. Chapter compaction is a separate model call (~1–3 per run).
