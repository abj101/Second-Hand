# QS: Does a quest-state planner keep its line of thought, revise what it believes, and stay out of act's tactics?

Status: passed — Amendment A complete; live 3/3 + 3/3; HF3 cache exception recorded   ·   Branch: feat/lean-agent   ·   Design: [quest-state.md](quest-state.md) (approved 2026-10-14)

## Paste block

```text
You are the orchestrator (GPT-6.1 Sol) for phase QS (quest state) of the lean agent,
repo C:\Users\Abj1\Documents\GitHub\Avatar, branch feat/lean-agent. Shell: PowerShell.
Python: .venv-agent/Scripts/python.exe -B. There is no AGENTS.md; CLAUDE.md holds the repo rules.

READ, in order: CLAUDE.md, docs/plans/handoff-notes.md, docs/plans/HF-handoff.md,
docs/plans/contracts.md ("notebook and act" only), docs/plans/quest-state.md (approved
design), docs/plans/QS.md. Then only the files QS.md section 3 lists.

ENTRY: check QS.md section 1 first. If any item fails (including a dirty
`git status --short agent scripts tests`), stop and report. Do not fix or stash anything.

WORKERS: dispatch every card to a gpt-6-luna worker with reasoning effort high, set
explicitly and verified before dispatch. Never substitute a tier or omit high; if the
host can't, report the exact missing capability and stop. Use the host's subagent
tools, not user-visible chats. Each worker prompt contains: the CLAUDE.md rules
(Ponytail Full, CodeGraph before reading code, Headroom for large output; report a
tool as unavailable rather than skipping it silently), its card verbatim from QS.md
section 5, the design sections it cites, the files it owns, and its acceptance
commands. A worker writes only its owned files. Never two writers on one file,
including shared tests. Read-only reviewers may run beside an implementer.

ORDER: QS.1 || QS.2 || QS.3 -> QS.4 || QS.5 -> QS.6. QS.2 is prompt wording:
review its SYSTEM text yourself against quest-state.md section 1 and the QS.2 card
before accepting it.

ACCEPTANCE: run each card's acceptance commands yourself. A card is done only when
they exit 0. A failed card gets one precise repair round, then report the blocker;
never broaden scope. After each parallel batch run
`.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto`.
TEMP FIRST: the sandbox TEMP (AppData\Local\Packages\sandbox...\AC\Temp) is not
writable, which breaks pytest and every tempfile user. In each shell, before any
pytest or script command, run:
  New-Item -ItemType Directory -Force .tmp/qs/tmp | Out-Null; $env:TEMP = "$PWD\.tmp\qs\tmp"; $env:TMP = $env:TEMP
and confirm `python -c "import tempfile; print(tempfile.gettempdir())"` prints that
folder. Workers' shells need the same line.
Unit tests use StubModel only. Locate code by symbol (CodeGraph), not line number.

LIMITS: never edit contracts.md (the section 4 text goes into the Result). No game
names in agent/: `rg -i "oak|mom|pallet|gary|daisy" agent` must print nothing.
Requests: only QS.6's two live commands, at most 6 total (cap 8), after the ledger
check in section 1. No other network calls, no cap or budget changes, no live game run.
Protect runs/ and checkpoints; scratch goes under .tmp/qs/.

FINISH: run the section 2 gate. Fill section 9 with every number and its command
(never claim a pass without the evidence). Set Status, update the QS row in
docs/plan.md, and rewrite docs/plans/handoff-notes.md per section 10. If the gate
passed, commit explicit paths only (never `git add -A`, never push):
"QS: <one-line gate result>". Then stop.
```

## Why this phase
Planning audit 2026-10-14 (memory `project-planning-audit`; design §0). On m1_hf_checkpoint_1 there were 41 planner calls in 144 steps. 24 of them came from single action failures (`agent/loop.py:305`). 6 of the 10 leaves were doors or coordinates (`try_door_2_7_5` was blocked 5× under new keys). The heard lead "Grandpa's lab" (step 92) was never focused, and `find_prof_oak` stayed open for 85+ steps. The planner is stateless (`agent/plan.py:30-33`). Its prompt forces a door-level focus (`plan.py:6-7`, `14-15`). No code reads `done_when`, and the decide arm can't close anything (`agent/act.py:102`, `ops=[]`).

## 1. Entry criteria
- HF1–HF5 committed (e5420d3). **The working tree has uncommitted edits** in `agent/loop.py`, `agent/memory.py`, `agent/world.py`, `scripts/run_agent.py`, `docs/plans/HF5.md`, `tests/agent/*` and the w0 golden trace (seen 2026-10-14). They must be committed or explicitly parked by the user before QS starts. If `git status --short agent scripts tests` shows changes you didn't make, stop and ask.
- The baseline suite has been measured at entry, with TEMP/TMP set to `.tmp/qs/tmp` as the paste block says, and its count recorded in the Result. Expected, measured 2026-10-14 that way: **521 passed, 0 failed**. Any failure: stop.
- History:
  - The first Sol entry attempt exited 3 because pytest couldn't use the sandbox TEMP.
  - The second attempt, with `--basetemp`, still failed `test_eval_run.py::test_e4_metrics_and_offline_repredict_preserve_chooser_guess`, because `_repredict()` uses `tempfile`. Redirecting TEMP/TMP fixes both.
  - `test_run_provenance.py::test_source_fingerprint_without_git_is_incomplete` sets `GIT_CEILING_DIRECTORIES`, so an in-repo temp folder doesn't count as a git checkout.
  - The third attempt hit `PermissionError [WinError 5]` in `run_agent._ledger`'s rapid `tmp.replace`, because the sandbox briefly holds fresh files. It's fixed for real:
    - `run_agent._replace` retries the replace, like `Notebook._save` does.
    - The formerly "known" failure was a stale test with the old caps 2925/2875 hard-coded. It now reads `run_agent.TOTAL_CAP`/`M1_CAP`.
- On disk: `runs/m1_hf_checkpoint_1`, `runs/m1_ip` (checkpoints 000100, 000125), `runs/m1_decide3`, `runs/m1_gp5`, and `.venv-agent`. The ROM is not needed: there's no emulator in this phase.
- Ledger `runs/e3_budget.json` has been read at entry. If the remaining cap is < 6, QS.6's live part waits for a user cap decision. Everything else proceeds.

## 2. Gate
| Metric | Pass | Measured by |
|---|---|---|
| Unit suite | entry baseline + new tests, no new failures | `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider -n auto` |
| Invariants (design §6) | each of 1–8 has a named test that passes | `... -m pytest tests/agent/test_notebook.py tests/agent/test_plan.py -q` |
| Trigger replay, 0 requests | m1_hf_checkpoint_1 ≤ 15 planner calls (design §9 measured 13), no failure-only triggers; m1_decide3 and m1_gp5 printed for the record | `.venv-agent/Scripts/python.exe scripts/eval_run.py --lifecycle runs/m1_hf_checkpoint_1` (and the two others) |
| v1 migration, 0 requests | m1_ip checkpoint 000100's notebook migrates; both heard leads (Oak, Grandpa's lab) are open quests; no `try_*` quest survives as open | `scripts/eval_act.py --om plan --run m1_ip --from-step 100` (dry: prints the migrated state and the prompt) |
| Planner keeps both leads (live, ≤ 3 requests) | from m1_ip 100 (stall trigger), ≥ 2/3 tries return ≥ 2 open quests citing heard facts, 0 quests rejected as door/coord/button-level, and a non-empty note | `... --om plan --run m1_ip --from-step 100 --tries 3 --live` |
| Stall pivots by belief (live, ≤ 3 requests) | from m1_ip 125 (second stall, "last stall changed nothing"), ≥ 2/3 tries revise or refute ≥ 1 belief with a reason, or reorder quests citing a belief | `... --om plan --run m1_ip --from-step 125 --tries 3 --live` |
| Prompt sizes | planner prompt ≤ 3,000 tokens (len/4) at both checkpoints; act/decide prompt within the existing 1,700 evaluator limit | printed by the dry `--om plan` and `--om cases --arm decide` where its cache allows (HF3 cache note) |
| Requests | ≤ 6, ledger before and after printed | the two live commands |

- **Decision this gate produces:** whether to fund a clean-boot live run with QS (user decides, cap raise).
- **If it fails:** report each try's quests, beliefs, note and why in the Result. Don't iterate on prompt wording with live spend. The dry parts (QS.1–QS.5) stand on their own and can be committed if their checks pass, with the live result marked open.

## 3. Read list
- Design: `docs/plans/quest-state.md`, all of it. §2 state, §4 triggers, §5 checks, §6 invariants, §7 compaction and §9 walk-through are the spec for the WPs.
- `agent/notebook.py`: all (~100 lines). `agent/plan.py`: all (~50 lines).
- `agent/loop.py`:
  - plan block 105–114;
  - act call and focus-closed check 148–153;
  - failure urgency 286–306 (line 305);
  - `_flush_conversation` 402–413;
  - `_moved` 459–470;
  - `_goal_text` 471;
  - `_plan_trigger` 475–486;
  - `state`/`restore` planner dict 488, 506–509.
- `agent/tasks.py`: `Stuck` (all). `agent/act.py`: `SYSTEM` 9–15, `DECIDE_SYSTEM` 58–61, `_decide` 70–102.
- `agent/world.py`: `place_label` 489, `view` 570 (goal_text use only).
- `scripts/eval_run.py`: `evaluate` 618 and the `_e4_metrics` plan-row counting (533–560). `scripts/eval_act.py`: `om_plan` (~627) and its argparse at line 68. `scripts/watch_run.py`: goals panel 136–152 and 215–225. `scripts/eval_plan.py`: `_setup`/`_run_try` (50–165).
- Tests: `tests/agent/test_notebook.py`, `tests/agent/test_tasks.py`, `tests/agent/test_loop.py` (802–850 planner tests), `tests/agent/test_om_eval.py`, `tests/agent/test_eval_run.py`.
- CodeGraph query to start: `codegraph_explore "Notebook apply render render_focus set_focus path plan Plan Loop._plan_trigger Loop._flush_conversation Stuck.update _goal_text om_plan"`.

## 4. Contracts
- **Uses:** contracts.md#notebook-and-act, contracts.md#world (unchanged), contracts.md#tasks.
- **Proposed replacement for the notebook part of "notebook and act"** (the user merges it; the orchestrator doesn't):

```python
# agent/notebook.py: quest state, code-stored at <memory root>/notebook.json (v2) + notebook.history op log
SECTIONS = ("facts", "beliefs", "quests", "notes", "chapters")   # + "log" (events, code-written)
MAX_OPEN = 5                                                      # extra open quests become "parked"
class Notebook:
    def __init__(self, root: Path) -> None                        # loads v2, or migrates v1 (goals/hints/lessons) in memory
    focus: str | None                                             # property: key of the first open quest (read-only)
    def apply_plan(self, plan: "Plan", step: int) -> dict         # reconcile (design §6); returns {"quests_changed", "beliefs_changed", "flags"}
    def run_checks(self, step: int) -> list[str]                  # design §5 on every open quest; returns keys closed this step
    def apply_chapter(self, chapter: dict, step: int) -> bool     # design §7 guard; False keeps notes verbatim
    def note(self, step: int, text: str) -> None                  # event log, unchanged
    def render(self, limit: int = 6000) -> str                    # planner view: facts, held beliefs, ruled out, quests, chapters, current notes
    def render_focus(self, limit: int = 4000) -> str              # act view: top quest (text, done_when, approach), other open quests one line each, facts, 10 log lines
```

  - `agent.plan`: `Plan(quests: list[dict], beliefs: list[dict], facts: list[dict], note: str, why: str)`. `plan(model, trigger, notebook, view, screen_text, recent, evidence: str | None = None)`, with `failure=` replaced by `evidence`. New `chapter(model, notebook) -> dict | None`.
  - `agent.tasks.Stuck`: progress means the first visit of a world place, or the first verification of an edge keyed `(place, next_place)`. `no_progress` fires at 20 steps (was 25 with any verified edge). `Stuck.progress()` resets the counter (called on game text). `state()` adds `seen_edges`.
  - Loop: planner triggers are T1–T5 (design §4.1). A failure no longer sets `urgent`; `cause` stays for act flags and the evidence packet. Trace `plan` rows become `{"trigger", "focus", "quests_changed", "beliefs_changed", "flags", "why"}`.
  - Act/decide output shape: unchanged. Act ops are no longer applied to the notebook (design decision 3).

## 5. Work packages

| WP | Goal | Owns files | Depends on | Worker |
|---|---|---|---|---|
| QS.1 | Notebook v2: state, reconcile, invariants, checks, migration, renders | `agent/notebook.py`, `tests/agent/test_notebook.py` | — | luna high |
| QS.2 | Planner and act prompts; `Plan` v2; chapter call | `agent/plan.py`, `agent/act.py` (prompt text only), new `tests/agent/test_plan.py` | — | luna high |
| QS.3 | Progress tracker: first-time edges, N = 20, reset on text | `agent/tasks.py`, `tests/agent/test_tasks.py` | — | luna high |
| QS.4 | Loop wiring: triggers, evidence packet, checks each step, compaction, state | `agent/loop.py`, `tests/agent/test_loop.py` | QS.1, QS.2, QS.3 | luna high |
| QS.5 | Tools: lifecycle replay and metrics, viewer panel, eval_plan v2 | `scripts/eval_run.py`, `scripts/watch_run.py`, `scripts/eval_plan.py`, `tests/agent/test_eval_run.py` | QS.1, QS.3 | luna high |
| QS.6 | `--om plan` on v2: migration print, evidence, live tries | `scripts/eval_act.py`, `tests/agent/test_om_eval.py` | QS.4 | luna high |

**Run order:** QS.1 ‖ QS.2 ‖ QS.3 → QS.4 ‖ QS.5 → QS.6   (‖ = parallel; owned files are disjoint)

### QS.1: notebook v2
- **Goal:** state is append-or-revise. The code enforces the invariants, so the model can't silently lose or corrupt anything.
- **Change:** `agent/notebook.py`, implementing design §2, §5, §6 and §7 (guard only).
  - **Sections.** v2 sections as in the contract above. `focus` is a read-only property: the first quest with status `open`.
  - **`apply_plan(plan, step)`.** Diff `plan.quests` (the full ordered list) against the stored quests by key. For each design §6 invariant:
    1. Omitted open heard quests are kept, with a flag `omitted <key>: keep, park or drop it with a reason`.
    2. `done`/`dropped` without evidence → refused + flag. `dropped` on a heard quest needs a fact or belief reference in `why` or `evidence`.
    3. Open quests beyond `MAX_OPEN` become `parked`.
    4. Reject quest text matching `#\d`, `\(\s*-?\d+\s*,\s*-?\d+\s*\)`, any name in `env.inputs()` passed as `buttons=`, or `^(press|move|walk|go) (up|down|left|right)` → flag `rejected <key>: name an outcome`.
    5. Belief updates append `{step, from_text, reason}` to `history`; `refuted` beliefs stay listed under "ruled out".
    6. A re-set quest merges fields and never overwrites `evidence`, `opened` or check results.
    7. Facts are add-only. An existing key with different text gets a new key with a `_2` suffix.
    8. Nothing is deleted.

    Every change is written to `notebook.history` as one JSON line. Notes are appended. Flags are returned, and the loop shows them on the next planner call.
  - **`run_checks(step)`.** Check kinds `reach` (compares `world.place_label` text, passed in through a `labels` callable the loop supplies), `speak` and `text`. The last two read log entries with `step > quest.opened`, using the log format `<SPEAKER> at <place>: "<text>"` from `agent/loop.py:411`. A passing check closes the quest `done` with the evidence "check <kind> <arg> at step N".
  - **`apply_chapter`.** Accept only if every open quest key and every held belief key is in the current state or in `open_leads`. On accept, the chapter is appended and the current notes are cleared (the notes stay in history).
  - **v1 migration on load.**
    - Goals with `source: heard`, not finished → open quests, ordered by step.
    - Other unfinished goals → chapter 0 summary text, one line each.
    - `hints` → facts; `lessons` → notes.
    - `try_*`/door-level goals never become quests (rule 4).
  - **Renders.** `render()` budget order: never drop facts or open quests. Drop the oldest chapters first, then closed quests, then ruled-out beliefs. Cut on line boundaries only.
- **Acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_notebook.py -q`, with one named test per invariant (`test_inv1_omitted_heard_quest_kept_and_flagged` … `test_inv8_nothing_deleted`) plus:
  - each check kind;
  - the chapter guard (accept and reject);
  - v1 migration of a fixture copied from `runs/m1_ip/checkpoint/000100/memory/notebook.json` into `tests/agent/data/qs/` (Oak and Grandpa's lab open, no `try_*` open);
  - a render budget test.
- **Risks:**
  - Rule 4 may reject legitimate quests that mention a number. The flag lets the planner reword them; measured in QS.6.
  - `reach` depends on place labels, which inherit the place-merge bug (HF5 bug 1). Checks only close quests and never open them, so a wrong label can at worst close one early. It's logged and visible.

### QS.2: planner and act prompts
- **Goal:** the planner acts as strategist (facts → beliefs → outcome quests, with a note carrying its reasoning) and never picks exits. Act works on the top quest and can see the others.
- **Change:**
  - `agent/plan.py`. Replace SYSTEM, REPLY and SCHEMA. Game-neutral wording; target ≤ 14 sentences. Must say:
    - Quests are outcomes in the game world; never name buttons, directions, coordinates or a specific door. Act chooses how.
    - Every quest cites why (a fact or belief key).
    - Beliefs are hypotheses: on new evidence, say which beliefs it supports or contradicts, and revise or refute with a reason. Refuted ideas stay ruled out.
    - What the game said is a fact and outranks beliefs. A place you were told about may be a quest before it is mapped.
    - On a stall, change something: revise a belief or reorder quests, and say why.
    - Return the full ordered quest list (≤ 5 open). The first open quest is worked on now.
    - Write a note (≤ 400 chars): what changed in your understanding, why this order, what you expect next.
    - Remove: "work backwards to a startable leaf" (lines 6-7), the exit-choosing sentences (14-15), and the arrival done_when sweep (16, now done by code checks).

    `Plan` and `plan()` as in the contract. The parse shapes types only (lists of dicts with string keys ≤ 40 chars, note ≤ 400, belief `confidence` ∈ low/mid/high). The `check` field is kept only if `kind` ∈ reach/speak/text with a string `arg`.

    `chapter(model, notebook)`: one call with current notes, the log span and closed quests. It returns `{summary ≤ 600, ruled_out, open_leads}`, or None.
  - `agent/act.py`, prompt text only:
    - `SYSTEM` line 10 → "A planner keeps your quests; work on the top quest. Other open quests are listed; if an option would finish one of them now, you may take it."
    - Same for `DECIDE_SYSTEM`. Drop the act line about marking the focus done/blocked (line 12): act ops are no longer applied.
    - Reply shapes unchanged.
- **Acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_plan.py tests/agent/test_act.py -q`. New `test_plan.py` covers: parse of a full reply, a reply missing fields, a malformed check dropped, the chapter parse, and a prompt-content test that the removed sentences are gone and the required rules are present. Then `grep -riE "oak|mom|pallet|gary|daisy" agent/` prints nothing.
- **Risks:** prompt length. Measured by the QS.6 dry print (≤ 3,000 tokens with the m1_ip state).

### QS.3: progress tracker
- **Goal:** T4 fires only when there has been no real progress. Repeat crossings between the same two places don't count.
- **Change:** `agent/tasks.py` `Stuck`:
  - Keep `seen_edges` (a set of `"place>next_place"` strings).
  - Progress = a new world place, or `verified_edge` whose `(place, next_place)` key is new. Drop the bare `row.get("verified_edge")` progress at line 31.
  - Threshold 25 → 20.
  - Add `progress()`, which sets `progress_n = 0`.
  - State and restore include `seen_edges` (old checkpoints → empty set).
- **Acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_tasks.py -q`. Update `test_stuck_escalates_after_25_world_steps_without_new_place_or_edge` to 20, keeping its name's intent with the new name `..._after_20_...`. Add `test_stuck_repeat_edge_is_not_progress`, and extend the round-trip test with `seen_edges`.
- **Risks:** `no_progress` also feeds act flags (`loop.py:484`), which will now appear 5 steps earlier. That's informational only, so acceptable.

### QS.4: loop wiring
- **Goal:** the planner runs on T1–T5 only, sees an evidence packet, and code closes satisfied quests every step.
- **Change:** `agent/loop.py`.
  - **`_plan_trigger` (475–486)**, in this order:
    1. focus closed by a check or the planner (T3);
    2. text on return to world (T1, existing);
    3. first visit of a world place (T2: `_moved` sets `arrived` only when the destination is new; "been here before" no longer triggers);
    4. no open quest (T5);
    5. a stall from `Stuck` returning `no_progress` (T4).

    The 3-step floor stays for T2/T4. Delete the failure urgency at line 305 (`self.urgent = ... new failure`); keep `cause` and `reported`. The cycle detector at 465 sets a flag for act, not `urgent`.
  - **Evidence packet** (new `_evidence()`, ≤ 600 chars), built from `self.recent`, `self.fails` and `self.moves` since `last_plan`:
    - new places;
    - exits/targets tried, grouped per place with outcome counts;
    - text heard (log entries since `last_plan`);
    - quests closed by checks;
    - steps since progress;
    - after a T4 whose reconcile changed nothing, the line "last stall changed nothing; revise a belief or reorder" (design §8.2).

    A second unchanged stall appends the fallback quest "explore an exit or area not yet tried" with `why: "stall"`.
  - **Plan block (105–114).** Call `plan(..., evidence=self._evidence())`, then `self.notebook.apply_plan(p, step)`, which returns flags kept for the next call's evidence. Remove the separate facts apply (`apply_plan` handles facts). Write the `_plan_row` per the contract.
  - **After act (151–153).** Remove `self.notebook.apply(a.ops, ..., structure=False)`. After each step's outcome, `closed = self.notebook.run_checks(step)`; if any closed, set `focus_closed`. If a closed quest is heard or was the top quest, or `len(notes) > 12`, call `chapter()` once and `apply_chapter` it.
  - **`_flush_conversation` (402–413):** call `self.stuck.progress()`.
  - **`_goal_text` (471):** the top quest's text + approach.
  - **`state()`/`restore()`:** add `stall_unchanged` (int) to the planner dict. Old checkpoints default to 0.
- **Acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_loop.py -q`. Replace `test_planner_owns_goals_and_act_closes_the_focus` with tests that use StubModel replies:
  - a failure doesn't trigger a replan;
  - text triggers T1;
  - a revisit doesn't trigger;
  - a stall triggers T4 and its evidence lists the tried exits;
  - an unchanged stall adds the flag, and a second one the fallback quest;
  - a `speak` check closes a non-top quest without a planner call;
  - a heard quest closing calls `chapter` once;
  - act ops never change the notebook;
  - state round-trips `stall_unchanged`.

  Then the full suite.
- **Risks:**
  - Loop line numbers drift from the uncommitted edits (entry criteria). Locate by symbol with CodeGraph, not by line.
  - `eval_act` OM replays hash agent code, so snapshots rebuild. HF3 cache misses still apply, so `--om cases`/`talk` may abort on old cached perceptions. Not a QS regression; record it.

### QS.5: tools
- **Goal:** lifecycle is measurable from a run folder with 0 requests, and the viewer shows the new state.
- **Change:**
  - **`scripts/eval_run.py --lifecycle RUN`.** Two parts:
    1. **Trigger replay:** feed trace rows to a fresh `Stuck` (QS.3). Mark text events from plan rows whose trigger starts with `new game text`, plus `_flush_conversation` log entries. Count the planner calls the QS rules would make (T1, T2 first world place, T4, T5 at step 1), and print the list with steps.
    2. **History metrics** from `memory/notebook.history`, v1 or v2: plan calls/step, heard leads opened/closed/still open/silently lost, quests or goals whose text matches the rule-4 regex, belief revisions with reason, stall calls that changed nothing.

    This replaces the scratch script used in design §9. `_e4_metrics` `reflection_usefulness` reads v2 quests when present.
  - **`scripts/watch_run.py`:** the goals panel shows the quest list (status and check), held and ruled-out beliefs, and the latest note. Fall back to v1 goals when `quests` is absent.
  - **`scripts/eval_plan.py`:** port `_setup`/`_run_try` to `apply_plan` and the v2 `Plan`. Cases keep their checkpoints. Scoring reads the top quest instead of the focus goal. Keep it dry-runnable; no live calls in this phase.
- **Acceptance:**
  - `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_eval_run.py -q`, with a lifecycle test on a tiny synthetic trace and history.
  - `.venv-agent/Scripts/python.exe scripts/eval_run.py --lifecycle runs/m1_hf_checkpoint_1` prints ≤ 15 planner calls and the v1 history metrics.
  - `python scripts/watch_run.py --run runs/m1_hf_checkpoint_1 --no-browser` serves `/state` (curl 127.0.0.1:8765/state returns JSON; stop it afterwards; if the user's viewer is already on 8765, skip this check and record that).
- **Risks:** the replay approximates text events from v1 traces. Record which source was used per run.

### QS.6: `--om plan` on v2
- **Goal:** the live gate measures the planner on real checkpoint state, migrated from v1.
- **Change:** `scripts/eval_act.py` `om_plan`:
  - build the notebook from the checkpoint (migration happens on load);
  - build `evidence` as `Loop._evidence()` would from the checkpoint's planner state (restore the loop, then call it);
  - use trigger `stall: no progress for 20 steps` at m1_ip 100. At m1_ip 125, use the same trigger plus the "last stall changed nothing" line (set `stall_unchanged = 1` before building evidence).

  Dry mode prints the migrated state, the full prompt, and its len/4 token count. Live mode prints per try: quests (with status, why and check), belief changes, note, why, and reconcile flags. The ledger handling is unchanged from IP.5.
- **Acceptance:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_om_eval.py -q` (stub test for each mode), then the gate rows in section 2, live last.
- **Risks:** m1_ip checkpoint perception may miss the new perceiver cache (HF3). `om_plan` only needs the `harness.json` state and memory, not perception calls. If a restore path needs perception, stub it and record that.

### Amendment A (user-authorized 2026-10-14, after the first QS stop)
No fixes exist on another branch or path. Continue in this checkout from the uncommitted QS work. This amendment overrides the one-repair rule and file ownership only as stated here.
- **QS.5, repair round 2 (last).** Fix the two lifecycle-metric bugs in `scripts/eval_run.py`:
  1. A heard lead removed or dropped without a closing status counts as **silently lost**, not still open. Use the final state plus the full history, not only the last snapshot.
  2. Planner calls are counted **once per plan event** (a trace `plan` row, or one step's group of planner writes), not once per goal/quest write.

  Move both cases from `.tmp/qs/lifecycle-audit.py` into owned regressions in `tests/agent/test_eval_run.py`. **Acceptance:** that test file passes, the audit script exits 0, and the three `--lifecycle` replays print again (HF ≤ 15).
- **QS.6 also owns** `tests/agent/test_eval_act.py`. Port `scripts/eval_act.py`'s callers of removed Notebook v1 APIs, plus the two failing tests there (`test_three_frames_metrics_and_ledger`, `test_cap_keeps_completed_frame`), together with the `test_om_eval.py` port. Everything else in QS.6 is unchanged.
- **New QS.7: legacy fixtures and golden** (gpt-6-luna high, one repair round, after QS.6).
  - **Owns:** `tests/agent/test_world.py` (only `test_loop_act_uses_world_facts_active_goals_and_labels`), `tests/agent/test_harness_reliability.py` (only `test_door_crossing_keeps_place_identity_heard_facts_and_the_goal_path_through_a_checkpoint`), and `tests/agent/data/w0_golden/**`.
  - **Port the two tests to v2.** Replace goal-tree replies and assertions with quests, facts and the top quest, and keep each test's intent: world facts and labels reach act; heard facts and the top quest survive a door crossing and a checkpoint restore. Rename the tests to match (`..._active_quests_...`, `..._top_quest_...`).
  - **Regenerate the golden** with `$env:W0_REGEN="1"` on `tests/agent/test_world_golden.py::test_act_eight_steps`, then unset it. Its diff may change only plan-row/trigger/notebook-derived fields. Print a per-field diff summary into the Result. If any perception, position, option, action or outcome field changes, stop and report instead of committing.
  - **Acceptance:** those three tests pass, then the full suite.
- **Order now:** QS.5 round 2 ‖ QS.6 → QS.7 → full suite (0 failures) → section 2 gate (QS.6 live tries last, ≤ 6 requests).
- **Commit staging at a passed gate:** also `git add -f tests/agent/data/qs/notebook-v1.json` and the regenerated `tests/agent/data/w0_golden/` files (both ignored by `data/`), plus the new `tests/agent/test_plan.py`.

## 6. Test layers for this phase
| Layer | Gates this phase? | Command |
|---|---|---|
| Unit (pure logic) | yes | `pytest tests/agent -q -p no:cacheprovider -n auto` |
| Replay (recorded runs, 0 requests) | yes | `scripts/eval_run.py --lifecycle runs/<run>` ×3; `scripts/eval_act.py --om plan --run m1_ip --from-step 100` (dry) |
| Live planner (checkpoints) | yes, ≤ 6 requests | `scripts/eval_act.py --om plan ... --live` |
| PyBoy integration / full live run | no (user decides after the gate) | `scripts/run_agent.py --run runs/m1_qs --steps 150 --decide` (cap set by the user) |

## 7. Budgets
- **Lines:** report only (user decision 2026-10-12). Expect agent/ +150 to +200 net (notebook grows; the planner prompt shrinks), scripts/ +120, tests +250.
- **Requests:** ≤ 6 planned, cap 8. Read the ledger at entry (last known total 3180, M1 3132). Don't raise caps.

## 8. Out of scope
- Cross-episode memory and embeddings (decided 2026-10-14: same game only, backburner).
- Perception, world memory, options, skills and travel. The place-merge bug (HF5 bug 1) and moving-NPC identity stay open. QS only makes their effects visible as low-confidence beliefs.
- Any change to act/decide output shape; option filtering or reordering by history; game-specific text in agent/.
- Running a clean-boot or checkpoint live game run.

## 9. Result (filled in by the orchestrator)
- **Gate: PASS, with the permitted HF3 cache exception.** Amendment A completed in this checkout: QS.5 round 2 parallel with QS.6, then QS.7, full suite, offline gate, and the two live commands last. Root independently ran every card acceptance. All implementation workers were explicitly dispatched as `gpt-6-luna`, reasoning `high`; ownership remained disjoint. No live game was run. A clean-boot run remains a user decision.
- **Environment for every test/script shell:** `New-Item -ItemType Directory -Force .tmp/qs/tmp | Out-Null; $env:TEMP = "$PWD\.tmp\qs\tmp"; $env:TMP = $env:TEMP`, then `.venv-agent/Scripts/python.exe -B -c "import tempfile; print(tempfile.gettempdir())"` printed `C:\Users\Abj1\Documents\GitHub\Avatar\.tmp\qs\tmp`. All Python commands below use that interpreter with `-B`; scratch stayed under `.tmp/qs/`.
- **Entry:** branch `feat/lean-agent`, clean `git status --short agent scripts tests`, HF commit `e5420d3` an ancestor, required runs/checkpoints present. Original baseline **521 passed, 0 failed**, exit 0: `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` (`.tmp/qs/entry-retry-suite.txt`). Amendment A explicitly authorized continuing the uncommitted QS work; no alternate checkout or branch was used.
- **Earlier diagnostics, superseded by the final gate:** the same full-suite command reported **449 passed / 97 failed** after the first batch (`.tmp/qs/batch1-suite.txt`), then **551 passed / 7 failed** before the authorized legacy ports (`.tmp/qs/batch2-gate-suite.txt`, reproduced in `.tmp/qs/resume-suite.txt`). The original lifecycle audit also failed. These were failures, not passes; Amendment A authorized the additional repair and fixture ownership.
- **Final full suite:** **564 passed, 0 failed**, exit 0, **60.55 s**: `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider -n auto` (`.tmp/qs/amendment-final-suite.txt`). It ran after QS.7 and before the section 2 gate. W0_REGEN was unset.
- **Root card acceptances, all exit 0:**
  - QS.1: **23 passed**; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_notebook.py -q` (`.tmp/qs/accept-qs1-repair.txt`).
  - QS.2: **24 passed**; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_plan.py tests/agent/test_act.py -q` (`.tmp/qs/accept-qs2-repair.txt`). Root reviewed SYSTEM against design section 1 and the card; **13 sentences**, all required strategic rules present. Sentence count: `.venv-agent/Scripts/python.exe -B -c "from agent.plan import SYSTEM; print(SYSTEM.count('.'))"`.
  - QS.3: **7 passed**; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_tasks.py -q` (`.tmp/qs/accept-qs3-repair.txt`).
  - QS.4: **112 passed**; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_loop.py -q` (`.tmp/qs/accept-qs4.txt`); full-suite acceptance is now green above.
  - QS.5 round 2: **40 passed**; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_eval_run.py -q` (`.tmp/qs/round2-accept-tests.txt`). `$env:PYTHONPATH=$PWD.Path; .venv-agent/Scripts/python.exe -B .tmp/qs/lifecycle-audit.py` exits 0 (`.tmp/qs/round2-accept-audit.txt`): removed heard lead lost **1**, still open **0**, one planner event with multiple writes counted **1**. Both cases are owned regressions now.
  - QS.6 after its precise repair: **35 passed**; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_om_eval.py tests/agent/test_eval_act.py -q` (`.tmp/qs/accept-qs6-repair.txt`). The repair fixed the exact dry CLI without --tries and required genuine relative reordering of retained quests for pivot scoring. Live gates below also exit 0.
  - QS.7: **3 passed**, exit 0; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_world.py::test_loop_act_uses_world_facts_active_quests_and_labels tests/agent/test_harness_reliability.py::test_door_crossing_keeps_place_identity_heard_facts_and_the_top_quest_through_a_checkpoint tests/agent/test_world_golden.py::test_act_eight_steps -q` (`.tmp/qs/accept-qs7.txt`). The two ports preserve world facts/labels reaching act and heard facts/top quest surviving crossing and restore.
- **Golden audit:** worker regenerated with `$env:W0_REGEN="1"; .venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_world_golden.py::test_act_eight_steps -q`, then unset W0_REGEN. Root ran `.venv-agent/Scripts/python.exe -B .tmp/qs/review-golden.py` (exit 0; `.tmp/qs/golden-review.json` and `.txt`). Only `tests/agent/data/w0_golden/act/trace.jsonl` changed, still **8 rows**. Per-field changed occurrences: `plan` **4**, `plan/beliefs_changed` **4**, `plan/flags` **4**, `plan/ops` **4**, `plan/quests_changed` **4**, `plan/trigger` **4**. **0 forbidden changes**: perception, position, options, actions and outcomes are unchanged. The four parent `plan` replacements are counted separately from field differences on the other rows.
- **Named invariant gate:** **29 passed**, exit 0; `.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_notebook.py tests/agent/test_plan.py -q` (`.tmp/qs/final-gate-invariants.txt`). All eight named tests are listed in the handoff. Migration, checks, chapter guard, protected rendering and reorder behavior also pass.
- **Lifecycle gate, zero requests:** `.venv-agent/Scripts/python.exe -B scripts/eval_run.py --lifecycle runs/<run>`, all exit 0; logs `.tmp/qs/gate-lifecycle-<run>.txt`. All three use `trace plan trigger metadata + notebook.history note() dialogue-log approximation`; no failure-only triggers. These are replayed triggers, distinct from historical plan counts:
  - `m1_hf_checkpoint_1`: **12** calls, within **15**. Steps/triggers: **1 T5; 48,51 T2; 61,63,64 T1; 67 T2; 78 T1; 81 T2; 92 T1; 113,133 T4**. Historical plan events **42**; heard leads opened **4**, closed **0**, parked **0**, still open **2**, silently lost **2**; rule-4 matches **6**; belief revisions **0**; unchanged stalls **0**.
  - `m1_decide3`: **12** calls. Steps/triggers: **1 T5; 36 T1; 39 T2; 42,48,72 T1; 75 T2; 93,97,110,128,133 T1**. Historical plan events **14**; all heard-lead counts **0**; rule-4 matches **0**; belief revisions **0**; unchanged stalls **1**.
  - `m1_gp5`: **12** calls. Steps/triggers: **1 T5; 38 T1; 41 T2; 61,62,72,76 T1; 79 T2; 82 T1; 85 T2; 125,146 T4**. Historical plan events **26**; all heard-lead counts **0**; rule-4 matches **1**; belief revisions **0**; unchanged stalls **4**.
- **Migration and planner size gate:** `.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om plan --run m1_ip --from-step 100` and the same command with **125**, both exit 0, **0 requests**, ledger unchanged (`.tmp/qs/gate-plan-100-dry.txt`, `gate-plan-125-dry.txt`). Each printed migrated state and exact `plan()` SYSTEM/USER prompt. Both heard leads are open: `find_prof_oak` cites `prof_oak_looking_for_player`; `find_grandpas_lab` cites `gary_at_grandpas_lab`. **0 open try_* quests**. SYSTEM+USER: **5619 chars / 1404.8 tokens** at 100; **5805 chars / 1451.2 tokens** at 125, both below **3000** by len/4. Trigger is `stall: no progress for 20 steps`; 125 sets stall_unchanged=1 and includes the required last-stall instruction. Restore uses copied harness/memory and an offline placeholder environment; no perception, ROM or PyBoy call is needed.
- **Act/decide size: INCONCLUSIVE, permitted cache exception.** `.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om cases --arm decide` exits **2** (`.tmp/qs/gate-decide-dry.txt`): **4** old-perceiver cache misses, limit **3**, first step **1**, last step **2**. No valid prompt-size measurement against the **1700** limit; **0 requests**. The packet explicitly qualifies this row with "where its cache allows (HF3 cache note)". No fixtures were re-recorded and no live perception was requested.
- **Live checkpoint 100:** `.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om plan --run m1_ip --from-step 100 --tries 3 --live`, exit 0, **3/3 successes, 3 requests** (`.tmp/qs/final-plan-100-live.txt`). Every try returned both open quests in order `find_prof_oak`, `find_grandpas_lab`, citing their respective heard fact, a nonempty note and **0 reconcile flags / 0 tactical quest rejections**. Try 1 and 3 checks are null; try 2 uses speak PROF. OAK / reach Grandpa's lab. Notes/why propose leaving the exhausted shop to seek the leads. Root inspected each returned quest and citation, rather than relying only on the score. Full quests, checks, beliefs, note and why are retained verbatim in that log.
- **Live checkpoint 125:** `.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om plan --run m1_ip --from-step 125 --tries 3 --live`, exit 0, **3/3 successes, 3 requests** (`.tmp/qs/final-plan-125-live.txt`). Every try reversed the retained pair to `find_grandpas_lab`, `find_prof_oak`, introduced held/mid belief `oak_in_lab` (both heard facts as because, nonempty reason), and cited that belief in quest why: try 1 both quests, try 2 the second quest, try 3 the first quest. All quest checks null, notes nonempty, **0 reconcile flags**. These pass via actual relative reordering with a belief citation, not via a revised/refuted belief. Full returned text is in the log. The asserted lab co-location remains an inference, not a heard fact.
- **Request/ledger gate:** exactly **6 total live requests**, no retries beyond those six posts and no other network/model commands. `Get-Content runs/e3_budget.json` before/after: **3216 → 3222 total**, **3168 → 3174 M1**; all other counters unchanged (logs `.tmp/qs/gate-ledger-before-live.txt`, `gate-ledger-after-live.txt`). Caps stay **3265 total / 3215 M1**, checked with `.venv-agent/Scripts/python.exe -B -c "from scripts import run_agent; print(run_agent.TOTAL_CAP, run_agent.M1_CAP)"`; remaining **43 / 41**. No cap changes. Live command logs independently print the intermediate **3219 / 3171** after checkpoint 100.
- **Other acceptance and protection:** viewer check skipped under the existing-listener rule: `Get-NetTCPConnection -State Listen -LocalPort 8765` found **PID 8676**, left untouched. `eval_plan` dry passed, status ok, **0 requests**, cases **P1–P4**: `.venv-agent/Scripts/python.exe -B -c "from pathlib import Path; import scripts.eval_plan as e; e.OUT=Path('.tmp/qs/eval-plan-dry.json'); raise SystemExit(e.main(['--dry','--tries','1']))"` (`.tmp/qs/eval-plan-dry.txt`/`.json`). `Get-FileHash -Algorithm SHA256` compared every Path/Hash entry in `.tmp/qs/protected-before.json`: **58 files, 0 changed**, before and after live (`.tmp/qs/gate-protected-final.txt`). `git diff --exit-code -- docs/plans/contracts.md` exits 0. `rg -i "oak|mom|pallet|gary|daisy" agent` prints nothing, expected exit **1** (`.tmp/qs/gate-names-final.txt`). `git diff --check` and `git diff --cached --check` pass.
- **Lines, report only:** `git diff --cached --numstat -- agent scripts tests`, summed by top-level directory (`.tmp/qs/gate-lines.txt`): agent **+659/-175, net +484**; scripts **+352/-59, net +293**; tests including fixtures **+1199/-180, net +1019**. This includes new test_plan **119** lines and copied v1 fixture **273** lines. Expected ranges were exceeded; no line cap was changed.
- **Deviations and limits:** Amendment A is the explicit authority for the last QS.5 repair and QS.6/QS.7 legacy ports. The earlier stopped state is superseded, not hidden. Headroom's proxy was unreachable; local compression/retrieval remained available. Earlier automatic approval review rejected one private-source export and a combined viewer-management command; bounded inspection and the permitted existing-viewer skip completed the work. QS.4 resumed on the same explicitly configured worker after an environment interruption. Legacy replay text events remain approximate. Live notes still mention specific doors despite strategic quest names; that wording limitation is recorded for a later user decision, without extra live tuning.
- **Finish:** Status and docs/plan.md updated, handoff rewritten, explicit QS paths staged (including force-staged ignored migration/golden fixtures and new test_plan.py). Commit message: `QS: 564 tests, 12 replay calls, planner gates 3/3 + 3/3`. No push. Pre-existing `.claude/` remains untouched.
- **Contract changes needed (proposal only; contracts.md remains unchanged):** the following is section 4's replacement, with the optional integration hooks actually implemented.

```python
# agent/notebook.py: quest state at <memory root>/notebook.json (v2)
# plus notebook.history op log; v1 load migrates in memory until a mutation.
SECTIONS = ("facts", "beliefs", "quests", "notes", "chapters")  # plus code-written log
MAX_OPEN = 5  # extra open quests are parked
class Notebook:
    def __init__(self, root: Path, *, buttons=(), labels=None) -> None
    # labels() returns the current canonical place label(s); buttons are env.inputs().
    focus: str | None  # read-only key of the first open quest
    def apply_plan(self, plan: "Plan", step: int, *, buttons=()) -> dict
    # returns {"quests_changed", "beliefs_changed", "flags"}; includes order changes.
    def run_checks(self, step: int) -> list[str]  # all open quests; keys closed now
    def apply_chapter(self, chapter: dict, step: int) -> bool  # rejected: notes kept
    def note(self, step: int, text: str) -> None  # complete code event log, unchanged
    def render(self, limit: int = 6000) -> str  # facts, beliefs, quests, notes, chapters
    def render_focus(self, limit: int = 4000) -> str  # top quest, other open, facts, log
```

  - `Notebook.data`: keyed dictionaries for `facts` and `beliefs`; ordered lists for `quests`, `notes`, `chapters`. Facts and open quests cannot be removed to meet a render budget; irreducible protected content may exceed it. Code events go to `log`, planner notes to `notes`; all mutations are retained in history.
  - `agent.plan`: `Plan(quests: list[dict], beliefs: list[dict], facts: list[dict], note: str, why: str)`. `plan(model, trigger, notebook, view, screen_text, recent, evidence: str | None = None)`, replacing `failure=` with `evidence=`. New `chapter(model, notebook) -> dict | None`; planner reads persisted notebook state including chapters and current-chapter log.
  - `agent.tasks.Stuck`: progress is a first world-place visit or first verified `(place, next_place)` edge. `no_progress` fires at 20 steps; `progress()` resets its counter for code-observed text/quest closure. State adds `seen_edges`; old checkpoints default to an empty set.
  - Loop proposal: triggers T1-T5 only; action failure no longer sets planner urgency. `cause` stays act-facing and in evidence. Plan trace rows are `{"trigger", "focus", "quests_changed", "beliefs_changed", "flags", "why"}`. Check all open quests after settled outcomes, compact on heard/top closure or notes >12, persist `stall_unchanged` (old state defaults to 0), plus optional evidence journal/baseline and pending-event state. Act ops are no longer applied.
  - Act/decide output shape, world options/view signatures, and frozen world/tasks contracts otherwise remain unchanged.
- **Open issues for the user:** clean-boot funding remains undecided; no live autonomy claim. Preserve HF place-merge labels, moving-NPC identity, old HF3 cache incompatibility, probe settle gating and remembered blockers on travel legs. Act/decide prompt size remains unmeasured on the old cache; planner notes can still include tactical suggestions.

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` (≤ 80 lines, the template's fixed headings), merging what is still current from `HF-handoff.md`. It must include:
- the v2 notebook sections and invariants, by test name;
- the trigger set T1–T5 and the `Stuck` change;
- the evidence packet;
- the new `--lifecycle` and `--om plan` behaviour;
- the replay numbers for the three runs;
- the live tries' outcomes;
- the ledger after QS.6;
- open issues (place-merge, moving NPCs, HF3 cache misses).
