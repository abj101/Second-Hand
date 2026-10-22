# Executor prompt: Avatar harness round r3 (ORDERS round 3)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`). Execute the plan in `docs/plans/harness-fixes-r3.md` (a file in the repo; read it completely first: it holds the audit, the exact edits, the tests, the offline verification snippets and the go/no-go gates). This prompt only adds the rules and the report format.

## Standing rules

1. **Smallest change.** Make exactly the edits the plan lists, in the files it names. No refactors, no new abstractions, no new gates, no removed options, no extra comments beyond the one-line comments the plan shows. Match the surrounding code style (dense one-line style in `agent/loop.py`).
2. **One commit per work item**, in the plan's order: R0 (no commit), R1, R2, R4, then R3 and R5 **only if the plan section "Decisions" below says the user approved them** (they are not approved by default). Commit message: `R<n> <title from the plan table>` plus a one-line body; end with the attribution line your environment requires. Stage with explicit paths only (`git add agent/act.py tests/agent/test_act.py`); never `git add -A`, `git add .`, or `git commit -a`.
3. **Never touch the user's own work in progress:** `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json`, and everything under `docs/plans/`. Do not stage, edit, stash, reset, checkout or format them. Do not switch branches.
4. **Stop and report on mismatch only for code the plan edits.** If the code at a cited line is not what the plan shows (after reading the file), or an existing test fails after your edit, stop that work item, do not improvise, and report: file, line, what you found, the failing test name and output. Do not stop for anything outside the plan's edit set.
5. **Assertions and goldens do not change** unless the plan lists the exact assertion and says the user approved it. R1, R2, R4 and R5 must pass with zero assertion edits; if a pre-existing assertion fails, that is a stop-and-report, not a fix. Only R3 lists assertion edits (4 of them) and only when approved.
6. **Interpreter:** `.venv/Scripts/python.exe` for everything (system `python` lacks pyboy). Tests: `.venv/Scripts/python.exe -m pytest -n auto -q` (the project default; never run them serially). Baseline: 687 passed, 1 skipped.
7. **No model calls unless a step explicitly says so.** The offline verification snippets in the plan make zero requests. The optional live micro-check and the final live-confirmation run spend cloud asks from the ledger (`runs/e3_budget.json`, 163 asks of headroom): do them only if the user's decision below says so, and flag their ask counts in the report. Never edit the ledger file or the caps.
8. **Windows and paths:** `scripts/run_agent.py --run` needs the full path `runs/<name>`; a bare name creates a folder in the repo root. `scripts/check_tracks.py` takes the run NAME (e.g. `m1_w8_live_150`), not a path. Never create junctions or symlinks to `runs/` or `Rom/` in any worktree.
9. **Manual and GUI checks never block.** If a step needs a person at the viewer or the game window, mark it "manual, not done" in the report and carry on. Do not use Computer Use or any screen-driving tool. Do not open the viewer.
10. **Do not change the plan.** If you think an edit is wrong, report why in the "notes" column; do not deviate.
11. **Reading output:** use `PYTHONIOENCODING=utf-8` for any script printing game text (the game text contains non-ASCII characters).

## Decisions (all approved by the user)

- R3 approved (the 4 assertion edits listed in the plan, and only those): **YES**
- R5 approved (eval gate semantics): **YES**
- Live micro-check right after R1 (10 cloud asks, charged to the ledger by the plan snippet): **YES**
- Final live confirmation (resume from checkpoint 100, about 105-130 asks, fits the ledger): **YES**
- Ledger cap raise for a full 150-step run: **approved in principle, but the user makes the edit** in `scripts/run_agent.py` (their WIP file; you never touch it). Until the user says the caps are raised, run only the resume-from-100 confirmation.

Run the micro-check right after R1. If it is a no-go, still do R2-R5 (they are offline-safe), skip the live run, and report the replies verbatim. Run the live confirmation last. Do the full 150-step run only if the user tells you the caps were raised.

## Workers (Luna, high effort)

You are the lead. Delegate to Luna workers at **high** effort and repeat these standing rules in every worker prompt (smallest change, explicit-path staging, never `git add -A`, never touch the WIP files, `.venv/Scripts/python.exe`, `pytest -n auto`, no model calls except where the plan says so, stop and report on mismatch).
- **Workers, MECHANICAL:** R0 baseline commands and table; the offline rule check and offline evals; the optional micro-check; the live resume run and filling the success-target table; copying the run folder (`cp -r`, never a junction).
- **Workers may also draft R1, R2, R4, R5 code and tests** from the plan's exact edits, one worker per item and one at a time (R2 and R4 both edit `agent/loop.py`: serialize them; never two writers on one file).
- **You, JUDGMENT:** review each worker diff against the plan before committing, make the commit, decide go/no-go and every stop-and-report, do R3 yourself (assertion edits), any revert, and the final report.

## Order of work

1. R0: run the baseline commands in the plan, fill the baseline numbers.
2. R1, then R2, then R4, then R3, then R5 (each: edit, add the plan's test, run the full suite, run the plan's offline verification, commit).
3. (R3 and R5 are approved and are part of step 2.)
4. Offline checks listed under each item; record the printed numbers.
5. Live resume confirmation (approved) only if the micro-check was a go; then fill the success-target table.

## Report format (reply with exactly this)

**Summary (3 lines):** commits made, what passed, what you stopped on.

| item | commit sha | files changed | suite (passed / skipped / failed) | assertion edits | offline check result | go / no-go | notes |
|---|---|---|---|---|---|---|---|
| R0 | - | - | 687 / 1 / 0 expected | - | `repeated_mistakes` 11, gate false | - | |
| R1 | | | | 0 | SYSTEM length, sentence count | | |
| R2 | | | | 0 | firings on `m1_h1h4_live`: ? ; on `m1_w8_live_150`: ? (expected 0 and steps 118-150) | | |
| R4 | | | | 0 | n/a (unit test) | | |
| R3 | | | | 4 if approved | | | |
| R5 | | | | 0 | recorded run: purity per map, edges, gate | | |
| live micro-check | - | - | - | - | choices at steps 18, 24, 111, 113 (x2 each), asks spent | | |
| live run | - | - | - | - | table of the success targets in the plan, each with measured value | | |

Then list: (a) every file you edited, (b) every command you ran that made a model request and its ask count (or "none"), (c) anything you skipped or could not verify, (d) anything surprising. Say plainly if anything failed; do not hedge results you verified.
