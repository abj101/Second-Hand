# OM: Options from memory — truthful memory, and the map offered as the action menu

Status: not started · Branch: feat/lean-agent · Follows: [GP.md](GP.md) §9e, `m1_gp5` · User decision 2026-10-13: build fixes 1 + 2 (truthful memory + options menu); movement fixes (3) and hard gates (4) are out.

## Paste block (for GPT-6.1 Sol; every subagent gpt-6-luna HIGH)

    You are the orchestrator for phase OM of the lean game-playing agent, in repo
    C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

    Model tiers (user decision). Never use Opus.
    - If you are GPT-6.1 Sol: every subagent is gpt-6-luna with reasoning effort HIGH.
    - If you are Claude Sonnet: every subagent is Claude Haiku (model: "haiku").
    - Otherwise: stop and ask the user which tier to use.
    Set model and effort explicitly on every spawn. One work package (WP) per subagent.

    1. Read, in order: CLAUDE.md; docs/plans/handoff-notes.md; docs/plans/OM.md (all of it);
       docs/plans/program-r1-wm.md §2 (rules); docs/plans/contracts.md sections
       "memory", "notebook and act", "world". Read code via codegraph_explore first, and only
       the symbols the WP cards name.
    2. Execute OM's work packages in its "Run order". For each WP write a self-contained
       subagent prompt containing: the WP card verbatim; program §2 rules verbatim; OM §0
       "Generality rules" verbatim; the contract text the card cites; the exact files and
       symbols (file:line) it may touch; its acceptance commands; and an instruction to
       return a diff summary plus the command output. The subagent has none of your context.
    3. Review every diff against its card: only the listed files; nothing game-specific in
       agent/ (run the OM §2 generality check); no gate that removes or blocks an option;
       the fixes in 46920f2 (Loop._fail_key, slow cycles) kept. Then run the acceptance
       commands yourself. One repair round with the exact failing output; if a WP fails
       twice, stop and report. Do not implement WPs yourself.
    4. After each parallel batch and before the eval, run the full suite in the background
       (~5 min) and read pytest's own summary line:
       .venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider
       Regenerate goldens only with W0_REGEN=1; review the data diff; explain each change in
       the Result; git add -f tests/agent/data/w0_golden.
    5. Cloud requests: only OM.6 spends, only through the scripts/run_agent.py ledger, at most
       the OM §7 cap. Read runs/e3_budget.json before and after. Never reset, refund or raise a
       cap; never start a live run (that needs the user's approval and a cap raise). Never
       modify runs/m1_gp5 or runs/m1_gp2: copy first, use --no-write. Preserve .cache/model/.
    6. Finish: fill in OM §9 Result and the Status line; rewrite docs/plans/handoff-notes.md
       (rewrite, don't append; under 80 lines; the template's fixed headings). Commit only the
       paths OM lists plus docs/ (message "OM: <one-line gate result>", with the repo's
       Co-Authored-By trailer). Never git add -A, never commit runs/ or .tmp/, never push.
    7. Stop after OM (STOP 8). End with a short report: gate numbers with the commands that
       produced them, deviations, open questions, requests spent and the ledger after.

## 0. Why (evidence) and the generality rules

**Evidence (`runs/m1_gp5`, 150 steps; map text rebuilt from its saved memory).** The act model does follow our input; its `why` quotes "the goal and the map". The input is what is wrong:
- **Wrong beliefs are never revised.** At steps 103–135 the perceiver saw a "doorway" north of the 2F stairs (a wall, by RAM truth). Memory stored `exit: doorway`, the planner made `interact_with_doorway`, and act pressed `UP A` 29 times (25 no-ops) while `here_flag` said `blocked UP`. When that goal was blocked (step 118), the planner made `interact_with_vent` on the same tile (127). Failures are counted against action strings (`Loop.fails`), never against the belief that produced them.
- **Places lie.** Five intro dialogue screens are stored as places aliased `lab`, so "Known places" tells the model a lab is known. Both house floors are labelled `world`, so `stairs -> world [p_world_2]` carries no meaning. 8 of 11 places in the trace are dialogue/menu/title screens; they fired 8 "arrived somewhere new" replans.
- **The useful part is buried.** The only unused exit on 1F (the door mat) is one line among 12 landmarks. The exits that matter, with where they lead and how often they were used, are spread across an ASCII grid, a letter legend and two coordinate systems inside ~1,200 tokens of equal-weight text.
- **Free-form actions bypass memory.** 84/150 actions were raw `buttons`. A raw button press never consults the map, so no warning or map fact can apply to it.

**Fix.** (1) Memory tells the truth: outcomes revise beliefs, only navigable screens become places, places carry real names. (2) Each step, code turns memory into a short numbered list of what can be done right now, each option annotated with its history; the model picks one. Warnings become attributes of options, so ignoring one means choosing the option that carries it in plain sight. The model still decides; free actions stay available as the last option.

**Generality rules (paste into every subagent prompt).**
1. Nothing in `agent/` names a game, place, character, item, route, button layout or RAM address. Inputs come from `env.inputs()`; screen classes come from the perceiver's existing enum; entity kinds are the perceiver's own words, compared only through `normalize_kind` and the existing generic kind sets (`ACTOR_KINDS`, `TRANSIENT`, `exit`).
2. Options are built from the **situation**, never from a per-game table: what the screen type allows (navigable vs. modal), what is on screen, and what memory holds for this place. A tool (go_to, approach, interact, advance, select, cancel, type, explore, buttons) appears only when the situation supports it, and each option says which tool runs it.
3. Code offers and annotates; it never removes, hides, reorders by preference, or blocks an option because of its history (program §2 rule 4). Ordering is a fixed, documented, history-free rule.
4. Every new behaviour has a unit test on `GridEnv`/`StubModel` from `tests/agent/fakes.py` (a synthetic game, not Pokémon frames), plus the Pokémon replay eval in OM.6 for evidence only. RAM truth is for evaluation only.

## 1. Entry criteria
- HEAD at or after `c85c3a1` (cleanup + `_fail_key` + slow cycles). `pytest tests/agent` green (254 at `46920f2`).
- On disk: `runs/m1_gp5/` and `runs/m1_gp2/` (frames, trace, truth, memory), `.cache/model/`, the ROM in `Rom/`. If anything is missing, stop and ask.

## 2. Gate
| Metric | Pass | Measured by |
|---|---|---|
| Unit suite | all pass | `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` |
| Generality | 0 hits (exit 1) | `git grep -niP "pok[eé]mon\|pallet\|\boak\b\|\bmom\b\|pikachu\|kanto\|\brival\b" -- agent/ ':!agent/briefs/' ':!agent/env_pyboy.py'` (env_pyboy.py is the emulator adapter) |
| Places (offline replay of m1_gp5 memory rebuild, OM.6) | dialogue/menu/title places = 0; no known-place name equals a screen type | `scripts/eval_act.py --om places` |
| Decision points (OM.6) | options arm right on ≥ 6/8 cases; old arm (from the recorded trace) right on ≤ 3/8 | `scripts/eval_act.py --om cases` |
| Fake-exit refutation (OM.6, m1_gp5 2F) | the 2F "doorway" renders as refuted with its evidence by the 3rd failed attempt | `scripts/eval_act.py --om places` |
| Requests | ≤ 30 | `runs/e3_budget.json` before/after |

- **Decision this gate produces:** whether to spend a live clean-boot run (needs a cap raise; user decides at STOP 8).
- **If it fails:** report per case which option was chosen and the model's `why`; no live run.

## 3. Read list
- `agent/act.py` (whole, 63 lines): `SYSTEM`, `REPLY`, `_action`, `act`.
- `agent/loop.py`: `Loop.step` lines ~60–160 (modal flag, `match_place`, `World.name`, act call, action resolution into `affs`/`targets`/`choice`), ~225–236 (failure recording with `_fail_key`), `_moved`, `_fail_key`, `_plan_trigger`.
- `agent/memory.py`: `match_place` (~111), `relate` (~292, sup/con/refuted), `name_place` (~203), `observe` (~225).
- `agent/world.py`: `view` (~381), `here_flag` (~225), `place_label` (~373), `resolve` (~350), `plan`/`travel` (~536–600), `name` (~599), `_failed_exit` (~128).
- `agent/predict.py`: `PRED`, `effects`, `verdict`.
- `agent/plan.py` (planner prompt; reads `World.view`).
- `tests/agent/fakes.py` (`GridEnv`, `StubModel`), `tests/agent/test_loop.py`, `test_world.py`, `test_memory.py`, `test_act.py`.
- `scripts/eval_act.py`, `scripts/run_agent.py` (`_ledger`).

## 4. Contracts
- **Uses:** contracts.md#memory (`relate`, refuted rule), #notebook-and-act (`act`, action `{"i": option index}`), #world (`view`, `resolve`, `plan`, `travel`, `name`).
- **Additions approved with this packet (additive only; the orchestrator does not edit contracts.md, it records the final text in §9 for the user to merge):**
  - `World.options(per: dict, place: str, fails: dict, step: int) -> list[dict]` — pure, no model calls, deterministic. Option row: `{"n": int, "tool": str, "label": str, "action": <an action dict act already accepts>, "notes": list[str]}`.
  - `act(..., options: list[dict] | None = None)` — when given, the prompt lists the options and the reply may be `{"i": n}` (already in the contract); all existing reply shapes stay valid.
  - `match_place`: only navigable screens (`screen == "world"`) mint or match places; any other screen returns the current place unchanged (the screen is a state of that place, not a place). Before the first navigable screen, the place is a single run-start place.
  - Place names: a place's display label is the model's `place` name when it has one, else `place <n>`; never a screen type.
  - Entity belief: an entity whose actions keep producing `does_nothing` (via `relate`, existing refuted rule) is shown as refuted with its evidence; it is never deleted.

## 5. Work packages

| WP | Goal | Owns files | Depends on | Tier |
|---|---|---|---|---|
| OM.1 | Only navigable screens are places; real names | `agent/memory.py` (match_place, name_place), `agent/loop.py` (modal/cut lines only), `agent/world.py` (place_label, name), tests | — | Luna high |
| OM.2 | Outcomes revise beliefs | `agent/loop.py` (failure path only), `agent/world.py` (`_failed_exit`, view rendering of things), tests | — | Luna high |
| OM.3 | `World.options` builder | `agent/world.py` (new method + helpers), `tests/agent/test_options.py` | OM.1, OM.2 | Luna high |
| OM.4 | act offers options; loop executes the pick | `agent/act.py`, `agent/loop.py` (act call + action resolution), tests | OM.3 | Luna high |
| OM.5 | Planner and act read the same truthful view | `agent/plan.py`, `agent/world.py` (`view` sections), tests | OM.3 | Luna high |
| OM.6 | Replay eval + gate | `scripts/eval_act.py`, `scripts/om_cases.json` | OM.4, OM.5 | Luna high |

**Run order:** OM.1 ‖ OM.2 → OM.3 → OM.4 ‖ OM.5 → OM.6. OM.1 and OM.2 both touch `loop.py`/`world.py` but in disjoint functions; if a merge conflict appears, run them sequentially.

### OM.1: Only navigable screens are places; real names
- **Goal:** dialogue, menu, title, battle and keyboard screens never create, match or name places; places are labelled by the model's name.
- **Change:** `Memory.match_place` returns `prev` for non-navigable screens (and a single run-start place when there is no `prev` yet); `Loop.step` stops treating world↔modal transitions as cuts wherever that is still needed; `World.place_label` shows the model name (`World.name` alias) or `place <n>`, never a screen type; the known-places list passed to act/plan excludes candidates that were never navigable. No game-specific screen names beyond the perceiver's enum.
- **Acceptance:** new tests in `tests/agent/test_memory.py`/`test_world.py`: (a) a GridEnv sequence world→dialogue→menu→world yields one place; (b) a title→dialogue×5→world boot yields exactly one navigable place and no place whose label is a screen type; (c) existing place-recognition tests (views, revisits) still pass. `pytest tests/agent/test_memory.py tests/agent/test_world.py tests/agent/test_loop.py -q`.
- **Risks:** the W0 golden changes (fewer places) — regenerate and explain. A game whose whole play happens on non-world screens (menus only) gets one place; that is correct for now.

### OM.2: Outcomes revise beliefs
- **Goal:** when an action aimed at a remembered thing fails (`no_change`, `blocked`, or a `miss` verdict), the thing itself accumulates contradicting evidence through `Memory.relate(target_id, "does_nothing", ...)`; at the existing refuted rule it renders as refuted ("not working: walked into 3×, steps 104–107") in `World.view`, and as a refuted note on its option (OM.3). Repeated identical text from the same thing records "says the same as before (step N)".
- **Change:** in the `Loop.step` failure path, also call `relate` for the chosen target id (`targets[choice.i]`), including button presses whose direction points at a remembered thing adjacent to the player (generic: use the press direction and the thing's tile; no kind names). Text repetition uses the existing `says` relation. `_failed_exit` and `view` read refuted state. Never delete the entity.
- **Acceptance:** GridEnv test: a fake "exit" entity drawn on a wall, pressed into 3 times → refuted, rendered with evidence, still present in memory; a real exit used once → not refuted. Existing `test_second_failure_of_same_action_flags_act_and_replans` and `test_fail_key_merges_rewordings_and_cycle_marks` pass. `pytest tests/agent/test_loop.py tests/agent/test_world.py tests/agent/test_memory.py -q`.
- **Risks:** a real exit that fails once for a movement reason (an NPC in the way) gets refuted. Mitigation: the refuted rule needs `con >= 2 and con > sup`, and one success clears it.

### OM.3: `World.options` builder
- **Goal:** a pure, deterministic list of what can be done right now, annotated with history, built only from the situation.
- **Rules (fixed, history-free order):**
  1. On a modal screen (not navigable): the screen's own choices from perception (`select` per menu item/entity, `advance`, `cancel`, `type` when a keyboard/text field is perceived), then `buttons`.
  2. On a navigable screen: (a) each exit of this place, seen or used, with "leads to <label> (used N×, last step S)" or "never used"; (b) each on-screen thing with the verbs that apply (`approach`/`interact`); (c) remembered things here not on screen (`go_to`); (d) `explore` (go_to "unexplored") when the place has untried edges, with the count; (e) other known places reachable by `World.plan` (`go_to`); (f) `buttons` (free input).
  3. Notes per option, from memory only: times tried here and the last outcome (`Loop.fails` via `_fail_key`), relations (`leads_to`, `says`, `does_nothing`), refuted state with evidence, "part of a back-and-forth cycle (step S)", "talked; same text as before" when `says` repeats.
  4. At most 12 options; when more exist, keep every exit and on-screen thing and summarise the rest in one line `also reachable by go_to: a, b, c`. No option is dropped for its history.
- **Create:** `World.options(per, place, fails, step)` in `agent/world.py`; `tests/agent/test_options.py` with GridEnv scenarios: modal screen, room with a used exit + an unused exit + an NPC + a refuted fake exit, and a >12 overflow.
- **Acceptance:** `pytest tests/agent/test_options.py -q`; the generality check from §2.
- **Risks:** duplicate exits (an exit seen both as an on-screen thing and as a tile exit). Mitigation: de-duplicate by entity id / tile, test it.

### OM.4: act offers options; loop executes the pick
- **Goal:** act sees `Options:` with one line per option (`n. <tool> <label> — <notes>`) and answers `{"i": n}`; loop runs the chosen option's `action` through the existing paths (`thing` → `run_affordance`, `go_to` → `World.resolve/plan/travel` or route, `advance/cancel/type` → existing, `buttons` → `press_seq`).
- **Change:** `act(..., options=None)`: when options are given, the prompt carries them right after the screen text, `REPLY` says "pick one option by number; use the free shapes only if no option fits", and the "failed here before" flag and cycle-exit flags are not duplicated in `flags` (their content is now in option notes). `SYSTEM` says once: the notes are what already happened; prefer options that have not failed. `Loop.step` builds options before the act call and maps `{"i": n}` into the existing `action` dict before resolution, so `_fail_key`, verdicts, cycles and the trace work unchanged. The trace row records `"option": {"n", "tool", "label", "notes"}`.
- **Acceptance:** `tests/agent/test_act.py`: `{"i": 2}` parses to option 2's action; out-of-range → None; free shapes still parse. `tests/agent/test_loop.py`: a StubModel that answers `{"i": n}` drives GridEnv through an exit; the trace row has the option. `pytest tests/agent/test_act.py tests/agent/test_loop.py -q`.
- **Risks:** prompt growth. Mitigation: options replace the "Things on screen" list and the duplicated flags; report the act prompt token count before/after on one OM.6 case.

### OM.5: Planner and act read the same truthful view
- **Goal:** `World.view` (planner input) shows per place a short exit table (`exit -> where, used N×` / `never used`) and refuted things under "don't believe", and drops the duplicate intro places (OM.1 already prevents new ones). The planner prompt says goals should name an option-level outcome (reach a place, use an exit, talk to someone), never a refuted thing.
- **Change:** `World.view` sections; one sentence in `plan.SYSTEM`. Keep the map grid for the planner.
- **Acceptance:** `tests/agent/test_world.py` view test with a used exit, an unused exit and a refuted thing; W0 golden regenerated if it changes, with the diff explained. `pytest tests/agent/test_world.py tests/agent/test_world_golden.py -q`.

### OM.6: Replay eval and gate
- **Goal:** measure OM on real frames without a live run.
- **Cases (`scripts/om_cases.json`, built by the subagent from `runs/m1_gp5` and `runs/m1_gp2` trace + `truth.jsonl`):** 8 decision points where the recorded agent chose wrong, each with a "right" set defined from RAM truth in terms of option tools/targets, e.g. m1_gp5 step 82 (1F after Mom: right = the unused door exit or explore; wrong = stairs, Mom), step 91, step 103 and step 110 (2F on the stairs: right = the stairs exit; wrong = the fake doorway / UP), plus m1_gp2's door-bounce steps after 113. Record the old arm from the trace (0 requests).
- **Method:** copy each run to `.tmp/om/<run>`; rebuild memory up to the case step with the new code by replaying the recorded perceptions (the perceiver prompt is unchanged, so `.cache/model` serves them; abort if more than 3 perceiver calls miss the cache); build options; call act once per case (uncached → 1 request each). `--om places` reports the place list, labels, and refuted things after replaying all of m1_gp5 (0 requests if fully cached).
- **Acceptance:** `.venv-agent/Scripts/python.exe scripts/eval_act.py --om places` and `--om cases` print the §2 metrics and exit 0 when the gate passes, 1 when not; requests ≤ 30 via the ledger.
- **Risks:** replay drift (memory rebuilt by new code differs from the run). Mitigation: cases are judged on option tool + target, not coordinates; report any case whose options don't contain a "right" option as an OM.3 defect, not a model failure.

## 6. Test layers
| Layer | Gates OM? | Command |
|---|---|---|
| Unit (GridEnv/StubModel, synthetic game) | yes | `pytest tests/agent -q -p no:cacheprovider` |
| Generality grep | yes | §2 |
| Replay (Pokémon frames, cached perception) | yes | `scripts/eval_act.py --om places|cases` |
| Live PyBoy run | no (STOP 8, user approval + cap raise) | `scripts/run_agent.py ...` |

## 7. Budgets
- **Cloud requests:** ≤ 30, OM.6 only. Ledger at writing: M1 2056/2125 (69 left).
- **Lines:** report only (budgets suspended); expect `agent/` +150–250.

## 8. Out of scope
- Movement fixes (walking around NPCs, "walk onto then push" exits, routing over seen tiles) — next packet; OM.6 cases judge the choice, not its execution.
- Hard gates that remove options (option 4).
- Perceiver prompt changes (they would invalidate the perception cache for OM.6).
- Any per-game code, brief edits, or RAM reads in `agent/`.

## 9. Result (filled in by the orchestrator)
- **Gate:**
- **Decision taken:**
- **Deviations from the packet:**
- **Contract text to merge (§4 additions as built):**
- **Open issues for the next phase:**

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` per [_template.md](_template.md) §10 (under 80 lines, fixed headings). Next phase after STOP 8: the user picks between a live clean-boot run (cap raise) and the movement packet.
