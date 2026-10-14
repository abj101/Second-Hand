# OM2: Options that tell the truth — verified exits, what lies beyond, less noise

Status: built, STOPPED at the size gate (no live measure) 2026-10-13 · Branch: feat/lean-agent · Follows: [OM.md](OM.md) §9 (STOP 8: decision points 3/8, gate 6) · User decision 2026-10-13: build fixes 1–6 of the STOP 8 diagnosis, then re-measure once.

## Paste block (for GPT-6.1 Sol; every subagent gpt-6-luna HIGH)

    You are the orchestrator for phase OM2 of the lean game-playing agent, in repo
    C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

    Model tiers (user decision). Never use Opus.
    - If you are GPT-6.1 Sol: every subagent is gpt-6-luna with reasoning effort HIGH.
    - If you are Claude Sonnet: every subagent is Claude Haiku (model: "haiku").
    - Otherwise: stop and ask the user which tier to use.
    Set model and effort explicitly on every spawn. One work package (WP) per subagent.

    1. Read, in order: CLAUDE.md; docs/plans/handoff-notes.md; docs/plans/OM2.md (all of it);
       docs/plans/OM.md §0 (generality rules) and §9 (as built); docs/plans/program-r1-wm.md §2
       (rules). Read code via codegraph_explore first, and only the symbols the WP cards name.
    2. Execute OM2's work packages in its "Run order". For each WP write a self-contained
       subagent prompt containing: the WP card verbatim; program §2 rules verbatim; OM §0
       "Generality rules" verbatim; the exact files and symbols (file:line) it may touch; its
       acceptance commands; and an instruction to return a diff summary plus the command
       output. The subagent has none of your context.
    3. Review every diff against its card: only the listed files; nothing game-specific in
       agent/ (run the OM2 §2 generality check); no option removed, hidden or reordered because
       of its history; the OM and 46920f2 behaviour kept. Then run the acceptance commands
       yourself. One repair round with the exact failing output; if a WP fails twice, stop and
       report. Do not implement WPs yourself.
    4. After each parallel batch and before the eval, run the full suite in the background
       (~5 min) and read pytest's own summary line:
       .venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider
       Regenerate goldens only with W0_REGEN=1; review the data diff; explain each change in
       the Result; git add -f tests/agent/data/w0_golden.
    5. Cloud requests: only OM2.5's single live measure spends, through the existing
       scripts/eval_act.py ledger path, at most the OM2 §7 cap. Run every dry gate first and
       spend nothing unless all of them pass. Read runs/e3_budget.json before and after. Never
       reset, refund or raise a cap; never start a live run; never re-measure after seeing the
       live result. Never modify runs/m1_gp5 or runs/m1_gp2; .tmp/om/m1_gp5 is the replay copy.
       Never change the perceiver prompt (it would invalidate the replay cache). Preserve
       .cache/model/.
    6. Finish: fill in OM2 §9 Result and the Status line; rewrite docs/plans/handoff-notes.md
       (rewrite, don't append; under 80 lines; the template's fixed headings). Commit only the
       paths OM2 lists plus docs/ (message "OM2: <one-line gate result>", with the repo's
       Co-Authored-By trailer). Never git add -A, never commit runs/ or .tmp/, never push.
    7. Stop after OM2 (STOP 9). End with a short report: gate numbers with the commands that
       produced them, per-case picks with the model's why, deviations, open questions,
       requests spent and the ledger after.

## 0. Why (evidence from OM §9 and the cached act replies)
The model now follows the options when they are clear (old arm 0/8 → 3/8; on 1F it took the unused rug exit 3 of 4 times). It fails where the options mislead:
1. **Perception guesses are listed as facts.** On 2F (steps 103–137) the options list four exits: `exit door: never used`, `exit doorway#2: never used`, `exit RIGHT: leads to place 3 (never used)`, `exit stairs: leads to place 3 (used 1×, last step 75)`. Only the stairs are real (RAM truth). The fake exits carry the most attractive label; at 103 the model picked `doorway#2` ("I already explored the stairs… try the other exit").
2. **Exit history is wrong.** The 2F stairs show "used 1×, last step 75" after several crossings (button-driven crossings and arrivals are not counted). 1F lists three exits to the bedroom (stairs, RIGHT, UP); RIGHT/UP are inferred duplicates of the stairs.
3. **Nothing says what lies beyond an exit.** On 2F every unexplored area is through the stairs, and 1F still has an unused exit, but each label is one hop deep. With the goal "next door", avoiding the stairs back is the rational choice; at 110/113/137 the model wrote "I have already tried the doorway and stairs" and pressed free buttons.
4. **Noise.** 13–19 options per step, ~10 of them furniture (`approach stool` ×4), plus an `info` row of 20+ names; act prompt 2.3–2.7k tokens. The explore label is jargon ("26 untried, 0 into unseen").
5. **Free input is the fallback** when no option matches the model's intent (step 82: "explore the room to find a door"; the door mat was option 2, `exit rug`).
6. **Parsing.** In 7 of 8 replies `why`/`expect` sit inside `action`, so the trace loses them. The model echoes our label `place 3` as its place name.

**Rules carried over:** OM §0 generality rules 1–4 apply unchanged. Information and labels only: every option stays offered; history changes words, never presence or order.

## 1. Entry criteria
- HEAD at or after `fec26e3` (OM built, STOP 8). `pytest tests/agent` green (285).
- On disk: `.tmp/om/m1_gp5` (replay copy), `scripts/om_cases.json`, `.cache/model/`, `Rom/`. If anything is missing, stop and ask.

## 2. Gate
| Metric | Pass | Measured by |
|---|---|---|
| Unit suite | all pass | `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` |
| Generality | 0 hits (exit 1) | `git grep -niP "pok[eé]mon\|pallet\|\boak\b\|\bmom\b\|pikachu\|kanto\|\brival\b" -- agent/ ':!agent/briefs/' ':!agent/env_pyboy.py'` |
| Places + refutation (unchanged from OM) | PASS | `scripts/eval_act.py --om places` |
| Dry: right option present | 8/8 | `scripts/eval_act.py --om cases` |
| Dry: exit truth | at every 2F case (103, 110, 113, 137): the stairs are the only exit not labelled unverified/refuted, and their label states the beyond-summary; no place lists two exits with the same destination and tile; on 1F the rug is labelled as an unused (unverified) exit | `scripts/eval_act.py --om cases` (new dry checks, 0 requests) |
| Dry: size | every case ≤ 10 options (excluding the free-input row) and act prompt ≤ 1,600 tokens (`len(text)/4`) | same |
| Live: decision points | options arm right ≥ 6/8 | `scripts/eval_act.py --om cases --live` (one measure) |
| Requests | ≤ 10 | `runs/e3_budget.json` before/after |

- **Decision this gate produces:** whether to spend a live clean-boot run (cap raise; user decides at STOP 9).
- **If a dry gate fails:** fix it inside the WP's repair round; never spend. **If the live gate fails:** report per case pick + why; no second measure.

## 3. Read list
- `agent/world.py`: `World.options` (~591) and its `option`/`free`/`slot` helpers, `_exit_rows` (~407), `view` (~427, `exit_text`), `_paths` (~363), `plan` (~691), `place_label` (~396), `_notes` (~150), `_failed_exit` (~143), `mark_exit` (~277), `after` (~261), `fail_key` (~21), constants `OPTION_CAP`, `ACTOR_KINDS`, `FURNITURE`.
- `agent/space.py`: `mark_exit`, tile `exit` rows (`src`: `observed` | `inferred`).
- `agent/memory.py`: `_nameable` (~40), `place_name`, `relate` (refuted rule).
- `agent/act.py`: `SYSTEM`, `OPTION_NOTE`, `REPLY_OPTIONS`, `_pick`, `act` (~52–75).
- `agent/loop.py`: where `World.options` and `act` are called, the `_moved` move log, the `World.prepare/after` calls.
- `agent/plan.py`: `SYSTEM`, `plan` (reads `World.view`).
- `scripts/eval_act.py` (`om_cases`, `om_places`, `Replay`), `scripts/om_cases.json`, `tests/agent/test_options.py`, `tests/agent/test_om_eval.py`, `tests/agent/test_act.py`, `tests/agent/fakes.py`.

## 4. Contracts
- **Uses:** the OM §9 "contract text to merge" (as built; the user has not merged it yet) plus contracts.md#memory, #world, #notebook-and-act.
- **Additions approved with this packet (additive; record final text in §9, do not edit contracts.md):**
  - Exit status in `World.options`/`World.view`: `verified` (crossed at least once, either direction, by any means), `unverified` (seen by perception or inferred, never crossed), `refuted` (existing rule). Exits are keyed by (tile, destination); two exit records with the same destination whose tiles are within 1 tile are one exit.
  - `World.beyond(place) -> dict` (pure): for each exit of `place`, `{"unused_exits": n, "unexplored": n, "places": [labels]}` counted over everything reachable through it without coming back through `place`; plus `dead_end: bool` (all unexplored and unused exits lie through one exit).
  - `act()` reads `why`/`expect` from inside `action` when absent at the top level.
  - Place names: `place <n>` (our own label form) is never stored as a name.

## 5. Work packages

| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| OM2.1 | Exit truth: verified/unverified/refuted, dedupe, both-direction use counts | `agent/world.py` (exit rows, `after`/`mark_exit` bookkeeping, `options` exit group), `agent/loop.py` (`_moved` hook only, if needed), tests | — | Luna high |
| OM2.2 | `World.beyond` + dead-end line | `agent/world.py` (new method near `_paths`), `tests/agent/test_options.py` | — | Luna high |
| OM2.3 | Less noise, plain labels | `agent/world.py` (`options` grouping/labels, `OPTION_CAP`), `agent/act.py` (option wording only), tests | OM2.1, OM2.2 | Luna high |
| OM2.4 | Parsing + names + planner facts | `agent/act.py` (`act` parse), `agent/memory.py` (`_nameable`), `agent/plan.py`, `agent/world.py` (`view` exit table), tests | OM2.1, OM2.2 | Luna high |
| OM2.5 | Dry checks, then one live measure | `scripts/eval_act.py`, `scripts/om_cases.json` (only to add expected labels, never to change cases or "right" sets), `tests/agent/test_om_eval.py` | OM2.3, OM2.4 | Luna high |

**Run order:** OM2.1 ‖ OM2.2 → OM2.3 ‖ OM2.4 → OM2.5. OM2.1/OM2.2 and OM2.3/OM2.4 touch `world.py` in disjoint functions; run sequentially if a merge conflict appears.

### OM2.1: Exit truth
- **Goal:** an exit is only presented as a way out once it has been crossed; duplicates collapse; every crossing counts.
- **Change:**
  - Status per exit: `verified` when a place change was observed through it in either direction (an observed tile exit, an incoming exit whose `arrive` tile it is, or a `_moved` place change starting/ending on that tile, whatever the input was: `go_to`, approach, or free buttons); `unverified` when it is only a perceived `exit:` entity or an `inferred` tile exit; `refuted` per the existing rule (refuted wins over unverified).
  - Labels: `exit <name> → <place> (used N×, last step S)` for verified; `possible exit <name> (unverified, never crossed)` for unverified; `not an exit: <name> (walked into N×, steps a–b)` for refuted. Never "never used" for something nobody has shown to be an exit.
  - Dedupe: records with the same destination within 1 tile of each other are one exit (keep the observed one's name and tile); an inferred exit that duplicates a verified one disappears from the list (it is the same exit, not a removed option).
  - Use counts per exit (not per destination), counting both directions: leaving through it, and arriving onto its tile from its destination.
- **Acceptance:** GridEnv tests in `tests/agent/test_options.py`: (a) a two-room grid crossed 3 times by free button presses → the exit shows used 3× and verified in both rooms; (b) a perceived `exit:` entity on a wall never crossed → unverified, and after 2 failed tries → refuted label; (c) an inferred exit next to the verified one → listed once. `pytest tests/agent/test_options.py tests/agent/test_world.py -q`.
- **Risks:** the W0 golden changes (labels) — regenerate and explain.

### OM2.2: `World.beyond` and the dead-end line
- **Goal:** each exit says what can still be explored through it; a place whose only way to anything new is one exit says so.
- **Change:** `World.beyond(place)` over the place graph (reuse `_paths`/the edges `plan` uses; no new store): for each verified exit, count unused or unverified exits and untried edges in places reachable through it without passing back through `place`; `dead_end` when this place has no untried edge or unverified exit of its own and exactly one exit leads to anything unexplored. Label suffix: `→ place 3 (1 unexplored exit, 10 unexplored spots beyond)`; dead-end line as the first option note of that exit and one line in `World.view`: `Everything unexplored from here is through: <exit>`.
- **Acceptance:** GridEnv test: three rooms A–B–C in a line, C has an unused exit; standing in A (no unexplored of its own) → `beyond` reports it through the A→B exit and `dead_end` is true; in B both directions report correctly. `pytest tests/agent/test_options.py -q`.
- **Risks:** counting "unexplored spots" from untried edges double counts across places; count per place, then sum.

### OM2.3: Less noise, plain labels
- **Goal:** ≤ 10 options per step, plain wording, the important ones visible.
- **Change (fixed, history-free order; nothing dropped):**
  - Exits first (all of them, with their new labels), then characters (`ACTOR_KINDS`) and things with any interaction history (`says`, `does_nothing`, fails), then explore, then other known places, then free input.
  - Every other on-screen or remembered thing collapses into one option `look at or go to a thing here: <names>` whose action is `go_to` by name (the model writes the name in `go_to`), so each stays reachable.
  - Explore label: `walk to an unexplored part of this place (N spots)`; free input: `free buttons (not checked against memory): <inputs>`.
  - Drop the `info` overflow row (its names move into the collapsed things option or the known-places option).
- **Acceptance:** GridEnv test with 12 plain furniture items, 1 character, 2 exits → ≤ 10 options, the furniture names all appear in one option, exits come first. `act` prompt test: a 19-thing room renders ≤ 1,600 tokens. `pytest tests/agent/test_options.py tests/agent/test_act.py -q`.

### OM2.4: Parsing, names, planner facts
- **Change:** `act()` takes `why`/`expect` from `action` when the top level lacks them; `_nameable` rejects `place <n>`; `World.view` exit table uses the OM2.1 statuses and the OM2.2 dead-end line; one sentence in `plan.SYSTEM`: an unverified exit is a guess, and a goal that needs "somewhere new" must go through an exit whose beyond-summary shows something unexplored.
- **Acceptance:** `tests/agent/test_act.py` (nested why/expect parsed; top level wins when both present), `tests/agent/test_memory.py` (`place 3` not stored as a name), `tests/agent/test_world.py` (view shows statuses and the dead-end line). `pytest tests/agent/test_act.py tests/agent/test_memory.py tests/agent/test_world.py -q`.

### OM2.5: Dry checks, then one live measure
- **Change:** `scripts/eval_act.py --om cases` (dry) prints, per case, the option list with notes and the act prompt token count, and evaluates the §2 dry gates (exit truth, size, right option present) with exit 1 on any failure. `--om cases --live` refuses to run unless the dry gates pass in the same invocation. Do not change the 8 cases or their right sets.
- **Acceptance:** dry run exits 0 with all dry gates PASS (0 requests); then exactly one `--om cases --live` (≤ 10 requests) whose output is copied into §9. `tests/agent/test_om_eval.py` covers the dry-gate checks on a synthetic case.

## 6. Test layers
| Layer | Gates OM2? | Command |
|---|---|---|
| Unit (GridEnv/StubModel) | yes | `pytest tests/agent -q -p no:cacheprovider` |
| Generality grep | yes | §2 |
| Replay dry (cached perception, 0 requests) | yes | `scripts/eval_act.py --om places`, `--om cases` |
| Replay live (one measure) | yes | `scripts/eval_act.py --om cases --live` |
| Live PyBoy run | no (STOP 9) | — |

## 7. Budgets
- **Cloud requests:** ≤ 10, one live measure only. Ledger at writing: M1 2064/2125 (61 left).
- **Lines:** report only; expect `agent/` roughly +60 to +120, and some removed from the old exit/info code.

## 8. Out of scope
- Perceiver prompt changes (e.g. [perceive.py:34](../../agent/perceive.py) "list every passage as `exit:`"); they invalidate the replay cache. Revisit after OM2 with a fresh run.
- Movement fixes (walking around NPCs, walk-onto-then-push exits): the next packet.
- Removing or hiding options, or reordering them by history (a gate).
- A different act model.

## 9. Result (filled in by the orchestrator)
- **Gate** (built 2026-10-13; replay of `runs/m1_gp5`, copy in `.tmp/om/m1_gp5`; Sonnet orchestrator, Haiku subagents; every command below made 0 requests):
  | Metric | Result | Pass? | Command |
  |---|---|---|---|
  | Unit suite | 310 passed (285 at entry) | yes | `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` (`310 passed, 1 warning in 299.11s`) |
  | Generality | 0 hits (exit 1) | yes | `git grep -niP ...` as in section 2 |
  | Places + refutation | PASS: 3 places, 0 modal places, 0 screen-type names; `exit: doorway` refuted at step 105 after 2 failed attempts; 0 press/frame drift, 0 perceiver misses | yes | `scripts/eval_act.py --om places` (exit 0) |
  | Dry: right option present | 8/8 | yes | `scripts/eval_act.py --om cases` |
  | Dry: exit truth | 8/8: on all four 2F cases the stairs are the only verified exit and read e.g. `exit stairs → place 3 (used 4×, last step 102) (1 unexplored exit, 11 unexplored spots beyond)`; the fake `doorway#2` reads `possible exit` (and `refuted:` in its notes from step 110); on 1F the rug / door mat is `possible exit ... (unverified, never crossed)`; no duplicate (destination, tile) exit records | yes | same |
  | Dry: size, options | every case 5-9 options excluding the free row (limit 10) | yes | same |
  | **Dry: size, act prompt** | **0/8: 1,861 / 1,966 / 1,982 / 2,045 / 2,065 / 2,012 / 2,140 / 2,052 tokens (len/4; limit 1,600)** | **NO** | same (exit 1) |
  | Live: decision points | not run | n/a | `--live` refuses when a dry gate fails |
  | Requests | 0 (ledger `runs/e3_budget.json` unchanged: total 2112/2175, M1 2064/2125, 61 M1 left) | yes | `runs/e3_budget.json` before/after |
- **Prompt composition** (chars at steps 82-137, from `.tmp/om/om2_prompts/`): system 1,159; notebook 1,800-2,300; known facts (`World.view`) ~2,000; options 570-1,030; reply-format text 1,638; the rest ~500. Removing the facts' `Thing`/`Landmark`/`E at` lines (which the options now repeat) would give 1,691-1,872 tokens, still over; the reply text and notebook log would also have to shrink.
- **Per case (pick, why, right/wrong):** none: the live measure was not run. Option lists per case are in `.tmp/om/om2_dry.txt` (right option present in all 8; old arm 0/8 right).
- **Decision taken:** the user (asked at the size gate, 2026-10-13) chose "Stop, no live measure". No second pass was tried. The size gate is for the user to decide at STOP 9 (compact the act facts and reply text, or relax the limit).
- **Deviations from the packet:**
  - OM2.1 and OM2.2 ran one after the other (OM2.2 needs OM2.1's status); OM2.3 and OM2.4 ran in parallel.
  - OM2.1: new memory store `crossings` (`World._cross`, row `{id: "<place>@x,y><other>", n, last}`, counted on both sides of a crossing in `World.after` and `World.travel`); `loop.py` was not touched. A place change seen only by the next observation (no tile) is not counted. `golden`: only the new file `tests/agent/data/w0_golden/act/memory/crossings.jsonl` (the trace rows did not change).
  - OM2.1 test (b) uses a floor tile: `_failed_exit` needs `floor is True`, so an entity on a wall can never be refuted. Two failed tries are two failing edges.
  - OM2.2: `World.beyond(place, screen=())` takes the on-screen exit things (as `options` does); `World._paths(start, avoid=())`.
  - OM2.3: the modal free row uses the same label as the navigable one; `act()` prints the tool word once (`approach npc`, not `approach approach npc`); the collapse of known places also fires for a single place over the cap.
  - OM2.4: the beyond suffix is on the current place's view lines only; a refuted exit's view line is its option label.
  - OM2.5: `om_cases` always runs the dry replay first and `--live` runs a second replay only if the dry gates pass; `OM_CAP` lowered 30 -> 10; stdout set to UTF-8 (labels hold arrows); the prompts at case steps are written to `.tmp/om/om2_prompts/` (scratch). `tests/agent/test_loop.py` label strings were updated (3 expectations).
  - Line counts: `agent/` +172/-94 across world.py, act.py, memory.py, plan.py; `scripts/eval_act.py` +123 net.
- **Contract text to merge (§4 additions as built):**
  - `world`: `World._exits(pid, live, screen=()) -> list[{"tile","dir","dest","name","head","idx","ident","pushed","uses","last","status","label"}]` (the one source of exit records for `options` and `view`); `status` is `verified` (a crossing counted in either direction, a confirmed `leads_to`, or an observed exit), `refuted` (existing rule; only when never crossed) or `unverified`; records with the same destination within Chebyshev distance 1 are one exit. Labels: `exit <head> → <place> (used N×[, last step S])`, `possible exit <head> (unverified, never crossed)`, `not an exit: <head> (walked into N×[, steps a–b])`, each verified one followed by `_beyond_suffix`. `World.beyond(place, screen=()) -> {"exits": {tile: {"unused_exits","unexplored","places"}}, "dead_end", "through"}`. `World.options` order: exits, characters and things with history, explore, known places (one row, or one `go to another known place:` row when over `OPTION_CAP = 10`), `look at or go to a thing here: <names>` (action None), free buttons; the `info` row is gone. `World.view` prints a status per exit and `Everything unexplored from here is through: <exit>`.
  - `memory`: store `crossings`; `_nameable` rejects `place <n>`.
  - `notebook and act`: `act()` reads `why`/`expect` from `action` when absent at the top level; option lines print the tool word once; rows with `action: None` are answered with `{"go_to": "<name>"}` or `{"buttons": [...]}`.
- **Open issues for the next phase:**
  1. The size gate (act prompt 1,861-2,140 vs 1,600 tokens) is open. The options block is no longer the large part; the facts (`World.view`, ~2,000 chars, with an ASCII map and repeated thing lists), the reply-format text and the notebook log are.
  2. The live decision-point measure was never run on the new menu; 10 requests are still available (M1 61 left) but a clean-boot run needs a cap raise.
  3. The collapsed `look at or go to a thing here` row lists up to ~50 names on 2F (perceiver noise: `vent 1`, `vent 2`, `cabinet#7`); duplicated names could be shortened.
  4. Exit crossings seen only by the next observation (no exit tile) are not counted; `Loop._moved` could pass the tile.
  5. Counts beyond two exits that share a downstream place overlap (`beyond`).

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` per [_template.md](_template.md) §10 (under 80 lines, fixed headings). Next after STOP 9: the user picks between a live clean-boot run (cap raise), the movement packet, and a perceiver-prompt pass.
