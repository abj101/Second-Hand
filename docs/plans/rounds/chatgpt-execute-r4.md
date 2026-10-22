# Executor prompt: Avatar harness round r4 (ORDERS round 4)

You have repo access to `C:\Users\Abj1\Documents\GitHub\Avatar` (branch `feat/lean-agent`). Execute the plan in `docs/plans/harness-fixes-r4.md`. Read it completely first: it holds the root causes, the offline evidence, the tests to add and the go/no-go gates. This prompt adds the standing rules, the exact edit specs and the report format. Where this prompt and the plan differ, this prompt wins.

## Standing rules

1. **Smallest change.** Make exactly the edits below, in the files named. No refactors, no new abstractions, no new gates, no removed options, no game names (`pokemon`, `oak`, `ball`, …) in code or prompts. Match the surrounding style (dense one-line style in `agent/loop.py` and `agent/world.py`).
2. **One commit per item**, in this order: P1a, P1b, P2, P3, P4. Commit message: `P<n> <title from the plan table>` plus a one-line body; end with the attribution line your environment requires. Stage explicit paths only (`git add agent/notebook.py tests/agent/test_notebook.py`); never `git add -A`, `git add .`, or `git commit -a`.
3. **Never touch the user's own work in progress:** `scripts/eval_frames.py`, `scripts/run_agent.py`, `scripts/frames_combo.json`, `baseline_eval.txt`, and everything under `docs/plans/`. Do not stage, edit, stash, reset, checkout or format them. Do not switch branches.
4. **Stop and report on mismatch.** If the code at a cited line is not what the plan or this prompt describes, or an existing test fails after your edit, stop that item, do not improvise, and report: file, line, what you found, the failing test and its output.
5. **No assertion or golden edits.** Every item must pass with zero changes to existing assertions. If an existing test asserts the old behaviour (for example the literal `(N too long)`, `stall at 20`, a 160-character text cut, or `changed` for a repeated identical reply), stop and report the test name and assertion. Do not edit it.
6. **Interpreter:** `.venv/Scripts/python.exe` for everything (system `python` lacks pyboy). Tests: `.venv/Scripts/python.exe -m pytest -n auto -q` (never serial). Baseline: **694 passed, 1 skipped**.
7. **No model calls except the two approved steps below** (micro-check: 4 asks; resume run: at most 120 asks). Never edit `runs/e3_budget.json` or the caps.
8. **Windows and paths:** `scripts/run_agent.py --run` needs the full path `runs/<name>`. Copy run folders with `cp -r`; never create junctions or symlinks to `runs/` or `Rom/`. Use `PYTHONIOENCODING=utf-8` for any script that prints game text.
9. **Manual or GUI checks never block.** Do not use Computer Use or any screen-driving tool, and do not open the viewer.
10. **Do not change the plan.** If you think an edit is wrong, say why in the report's notes column and still follow the spec, or stop if following it would break a test.

## Decisions (user)

- P1a, P1b, P2, P3, P4: **approved**. P4 and P2 change behaviour but no existing assertion.
- P1 micro-check, 4 cloud asks charged to the ledger: **approved**. Run it right after P1b is committed.
- Live resume from checkpoint 100 into `runs/m1_r4_resume100` (at most 120 asks): **approved, only if all five items are committed and the suite is green**. Run it even if the micro-check was a no-go, but say so in the report.
- Full 150-step run from boot: **not approved** until the user says the caps were raised (the user edits `scripts/run_agent.py`; you never do).

## Exact edit specs

**P1a, `agent/notebook.py` `Notebook.render` (about lines 257-280).** Replace the line
`lines.extend(self._text_line(row, 160) for row in recent if row.get("key") not in cited_keys)`
with whole lines, newest first, within a 1500-character budget, printed oldest first as today. The newest uncited row is always kept; only that row is cut to 1500 if it is longer on its own. Cited rows and the `recent = d["text"][-8:]` window are unchanged. Leave `_text_line`'s `limit` parameter in place and leave the T1 trigger cut in `loop.py` alone. Tests (`tests/agent/test_notebook.py`): a 500-character newest row renders whole; rows over the budget lose the oldest whole lines, never a cut middle line.

**P1b, `agent/world.py` `World._named_things` (about line 601).** Add `e.get("called")` to the name set: `[e.get("name"), e.get("called"), *e.get("aliases", [])]`. Nothing else. Test (`tests/agent/test_world.py`): an entity named `scientist` with `called: "OAK"` on a world-map place → `ref("OAK")` and `ref("oak")` return `thing:<id>`. Two things with the same `called` → `ties()` lists both.

**P2, `agent/notebook.py` + `agent/loop.py`.**
- Move the quest-closing loop out of `Notebook.heard` (about lines 159-166) into `Notebook.talked(self, step, who) -> list` (its events). Same conditions, same `since` rule, same `_heard_closed.append`, with `who` canonicalised as `heard` does today. `heard` calls `talked` and saves its events exactly as before, so the `heard` behaviour and the events written are byte-identical.
- In `Loop._settle`, where `said_step is not None` (about line 288), call `self.notebook.talked(<step>, f"thing:{tid}")` and save its events through the notebook's existing save path.
- Use the same step value that `self.notebook.observe(...)` receives a few lines later (about line 317), so the quest lands in that call's `closed` list via `_heard_closed` (drained in `observe`, about line 174) and the row gets verdict `progress` plus T3.
- Tests: a quest targeting thing X, then two interacts with X giving the identical reply → the quest closes on the second with `reason="talked"`, verdict `progress`. A quest retargeted after the earlier reply does not close on a reply from before the retarget.

**P3, `agent/loop.py` `Loop._evidence` (about lines 482-513).**
- In `fit()`: when no whole entry fits, keep the newest entry cut to the remaining cap, ending in `…`, instead of `(N too long)`. The behaviour when entries fit is unchanged.
- Replace the literal `(stall at 20)` with `(stall at {STALL * 2**self.stuck.u})`, importing `STALL` from `agent.tasks` if it is not already imported.
- Tests: a single 90-character failed entry renders as one cut entry; the stall number follows `stuck.u`.

**P4, `agent/loop.py` `Loop._settle`.**
- Set `said_step = None` before the `if choice and tid and not route:` block (about line 278), so it is defined on every path. Keep the assignment inside the block as is.
- After the R2 `idle |= …` line (about line 322), add `idle |= said_step is not None` as one line, with a short trailing comment in the file's style (the same thing gave the same reply as before: that option did nothing new).
- In the fails update, the why becomes `"same reply"` when the idle came from `said_step` and stays `"did nothing"` otherwise.
- A quest closed by P2 on the same row still wins: `progress` is checked before `failed` in the verdict line. Do not change that line.
- Test: two interacts with the same thing and the same reply, no targeted quest → the second row has verdict `failed`, and `loop.fails[intent]["why"] == "same reply"`.

## Offline checks (0 requests; record the numbers)

1. After P1a: render the notebook from `runs/m1_r3b_live_150_20261021_150630/memory/notebook.json` (load it into a `Notebook` the way the tests do). Confirm that the t12 line appears whole and contains `BALL`. Print the Game-text block's length.
2. After P4: count the rows where the new rule would fire, reading `memory/relations.jsonl` rows whose `why` contains `says the same as before`. Expected steps:
   - r3b: 115, 118, 121, 125, 128, 131, 134, 139, 145, 148
   - `m1_w8_live_150`: 94, 98
   - `m1_h1h4_live`: none
3. After every item: the full suite, then `eval_run.py runs/m1_r3b_live_150_20261021_150630 --no-write`, which must be unchanged (it reads recorded rows).

## P1 micro-check (4 asks, run after P1b is committed)

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe - <<'E'
import json
from pathlib import Path
from agent.model import Ollama
from agent.plan import SYSTEM, SCHEMA
from scripts.run_agent import _ledger
R='runs/m1_r3b_live_150_20261021_150630'
m=Ollama("gemma4:31b-cloud"); post=m._post
m._post=lambda p: (_ledger(Path("runs/m1_r4_micro"), None, 4), post(p))[1]
t=next(r for r in json.load(open(R+'/memory/notebook.json',encoding='utf-8'))['text'] if r['key']=='t12')
full=f"t12 {t['speaker'] or 'text'} at {t['where']}: \"{t['text']}\""
C=[json.loads(l) for l in open(R+'/calls.jsonl',encoding='utf-8')]
for s in (112,115):
    u=next(x for x in C if x['step']==s and x['role']=='plan')['user']
    assert full[:160] in u and full not in u; u=u.replace(full[:160], full)
    for _ in range(2): print(s, json.dumps(m.ask(SYSTEM, u, [], SCHEMA, role='plan').data, ensure_ascii=False))
E
```

- **Go:** in at least 3 of the 4 replies, a quest's text or target names the balls, the display case, or its legend letter (`Landmark F` at step 112, `Landmark G` at step 115).
- **No-go:** report the 4 replies verbatim. Continue with P2-P4 and the resume run anyway (rule above).
- If the `assert` fails, stop the micro-check and report what the recorded line looks like.

## Live resume (last step)

```
cp -r runs/m1_r3b_live_150_20261021_150630 runs/m1_r4_resume100
.venv/Scripts/python.exe scripts/run_agent.py --run runs/m1_r4_resume100 --resume --from-step 100 --steps 50 --max-calls 120 --decide
.venv/Scripts/python.exe scripts/eval_run.py runs/m1_r4_resume100
```

Rows 1-100 are the old run's; only rows 101-150 test the new code. Success targets, with a measured value for each:
- first step with a non-empty `party` in `truth.jsonl`
- the quest text and target of every plan made after step 100
- whether any planner prompt after step 100 contains `BALL`
- the longest run of `interact` on one thing whose reply says the same as before
- verdicts of those rows (expect `failed` from the second repeat onward)
- the number of `(N too long)` strings in planner prompts after step 100 (expect 0)
- asks spent
- the final ledger

## Workers (Luna, high effort)

You are the lead. Delegate to Luna workers at **high** effort and repeat these standing rules in every worker prompt.
- **Workers, MECHANICAL:**
  - the baseline suite run
  - drafting each item's code and test from the specs above, one worker per item, one at a time. P2, P3 and P4 all edit `agent/loop.py`: serialize them, never two writers on one file.
  - the offline checks, the micro-check script, the resume run, and filling the success-target table
- **You, JUDGMENT:** review every diff against the spec before committing, make the commits, the go/no-go of the micro-check, every stop-and-report, and the final report.

## Report format (reply with exactly this)

**Summary (3 lines):** commits made, what passed, what you stopped on.

| item | commit sha | files changed | suite (passed / skipped / failed) | assertion edits | offline check result | go / no-go | notes |
|---|---|---|---|---|---|---|---|
| baseline | - | - | 694 / 1 / 0 expected | - | eval r3b: gate false, `repeated_mistakes` 10 | - | |
| P1a | | | | 0 | t12 whole in render? block length | | |
| P1b | | | | 0 | n/a (unit test) | | |
| micro-check | - | - | - | - | 4 replies (quest text and target), asks spent | | |
| P2 | | | | 0 | n/a (unit test) | | |
| P3 | | | | 0 | n/a (unit test) | | |
| P4 | | | | 0 | firing steps per run vs expected | | |
| live resume | - | - | - | - | success-target table | | |

Then list:
- (a) every file you edited
- (b) every command that made a model request, with its ask count
- (c) anything skipped or not verified
- (d) anything surprising

State failures plainly. Do not hedge results you verified.
