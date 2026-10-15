# HF5: Do the integrated repairs improve behavior, and what is actually validated?

Status: offline gate PASSED (after one user-approved production fix in `agent/loop.py`); live validation not authorized. Depends on: HF1–HF4 offline gates passed. Approved requests: 0.

Read the [program](program-harness-reliability.md), [handoff](HF-handoff.md), and all preceding Results. This phase must distinguish deterministic correctness, emulator execution, and model autonomy. None substitutes for the next.

## Work packages and ownership

**A || B -> C**, HIGH workers. C is read-only validation; orchestrator runs final commands itself.

| WP | Owns | Deliverable |
|---|---|---|
| A | New `tests/agent/test_harness_reliability.py`; `tests/agent/fakes.py` only for a missing reusable fixture | Small integrated regressions using production Loop/World/skills with StubModel; no copied production logic. |
| B | `scripts/eval_act.py`, `scripts/eval_run.py`, `tests/agent/test_eval_act.py`, `tests/agent/test_eval_run.py` | Extend existing evaluator with one `--om reliability` mode, strict offline behavior and explicit pass/fail/inconclusive results. Preserve existing modes. |
| C | Read-only production and test files | Review truth boundary, cache/replay validity, action identity, duplicate movement, and outcome metrics. Return only actionable defects or confirmation of checked invariants. |

The proposed command below does not exist until B implements it. Its result contract is fixed here:

```powershell
.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om reliability --run m1_ip --from-step 125
```

Output is confined to `.tmp/hf/HF5/`; original run and cache entries stay unchanged. Exit 0=all required executed assertions pass, 1=behavioral failure, 2=missing evidence/cache/ROM or incomplete execution. Print request count and ledger equality. A cache miss cannot return success or enable HTTP.

Worker acceptance commands for A and B respectively:

```powershell
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_harness_reliability.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_eval_act.py tests/agent/test_eval_run.py -q -p no:cacheprovider
```

## Offline acceptance matrix

| Case | Independent outcome assertion |
|---|---|
| Known actor between player and goal, with side path | Model chooses the destination; production execution detours and reaches it within the fixture's finite shortest-path-derived input bound. No repeated collision or false wall. |
| No available path; actor subsequently moves | No invalid known-blocked inputs; truthful route-stage feedback; same model goal succeeds after a fresh observation opens the path. |
| Duplicate inferred/observed exits + renamed destination | The selected physical exit and arrival match the canonical record; its own failure note increments through rename and resume. |
| Dialogue over room objects | Furniture is never a select option; valid advance progresses text; genuine choice/typing menus remain usable. |
| Pending result then delayed transition, including resume | One input sequence, one attributed effect; no premature failure, false completion, or duplicate crossing. |
| Existing successful navigation and IP memory | Door crossings, stable place identity, full heard facts and goal-path retention still pass existing regressions. |

For real checkpoint125, load a **copy**, obtain its recorded before-state, and run the actual chosen route/skill with HTTP disabled. Assert current actor is excluded, first input is not DOWN into it, position changes, and reported failure stage matches what occurred. RAM is evaluator-only and checks those outcomes; it never supplies route/perception. An actual exit is additional evidence, not presumed from a good route. If a new post-frame lacks cached perception, stop: this primitive execution can be validated, but the continuation is **inconclusive**. Report these as separate results; never reuse old-path perception on a diverged frame.

Full offline command set:

```powershell
.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om cases
.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om talk --run m1_decide3
.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om reliability --run m1_ip --from-step 125
```

If HF3 made historical recorded choices inapplicable, B must retain those captures unchanged and explicitly map/annotate only the affected **evaluation fixtures** with valid-role evidence. Report original legacy result separately; the HF fixture's positive and negative assertions must remain independent of current option numbering. No blanket skip or score adjustment.

## Optional live validation: prepare first, then obtain a spend decision

Live calls are **not authorized by this packet**. The ledger has only three M1 requests left at planning. Prepare exact commands, destination directory, cap accounting, stop conditions, manifest, and before-ledger snapshot; request a budget decision only after the offline report is reviewable. Never raise/refund/reset caps yourself.

Proposed staged envelope (not additive automatic authorization):

1. **Checkpoint repair:** new `runs/m1_hf_checkpoint_<id>` copied from checkpoint125; at most 25 decisions / 60 actual requests including retries. Original `m1_ip` remains untouched. Gate: autonomously leave the trapped room within 25 decisions, retain the heard objective, and no three repetitions of the same blocked segment in an unchanged situation. A new explanation without changed execution is not recovery. If it fails, report exact trace and stop; no live prompt-tuning loop.
2. **Fresh boot:** only after checkpoint gate and separate remaining-spend authorization; new `runs/m1_hf_fresh_<id>`, at most 180 decisions / 300 actual requests including retries. Same gameplay model and explicit chooser mode; implementation orchestration models do not replace it. Gate: reach outside, retain the heard objective, avoid the audited modal/actor stalls, and make verified progress beyond baseline (evaluator confirms a new navigable area beyond baseline maps or first party member). Report success/failure and longest no-progress streak; 180 steps without that progress is failure, not completion.

Use `scripts/run_agent.py` and its ledger/cache, not a new live runner. Check viewer before live execution. Exit 3/4, missing provenance, input error, or budget exhaustion: checkpoint and stop. Independent wall-clock limit: 15 minutes for checkpoint stage, 45 for fresh boot; request graceful checkpoint/termination if exceeded, then classify incomplete. Stop a run on three identical confirmed blocked segments without changed state; this is an evaluation run limit, **not** production option filtering.

Historical m1_ip lacks full prompts/mode manifest, so it is not a controlled A/B model experiment. Do not claim causal model-performance gains from one new run. Report actual action outcomes, verified destinations, no-change sequences, requests, and retained objective; never use prediction-hit rate as win rate.

## Result and completion states

- Synthetic integration: **PASS (8/8)**. `tests/agent/test_harness_reliability.py` (new): actor detour, corridor block then actor moves, duplicate exits + rename + resume, dialogue furniture / flagged menu / typing, pending + resume, door crossing + IP memory. Mutation-proven: cases 1, 3, 5; not proven: 4, 6. Case 2 first failed on the baseline: a first-attempt no-route approach ran greedy into the actor and a wall (inputs 3, `failure=None`). **Production fix (user-approved)** in `agent/loop.py`: `refused = bool(t and act_route is None)` (was also gated on a prior `held`), plus `reuse = refused and prior.get("avoid")` in the failure tail so a first-attempt refusal doesn't KeyError on a missing `avoid`. A refusal now yields zero inputs, status blocked, `no route avoids blocker`, intent counter 1, no wall written for the actor's tile; after the actor moves the same goal succeeds and the counter clears. The test asserts the refusal from the row (the synthesized refusal skill never reaches `run_affordance`); unused `record_skills` helper removed. Trade-off: any on-screen approach with no route avoiding known blockers is now refused on its first attempt rather than tried greedily.
- Real checkpoint primitive (`eval_act.py --om reliability --run m1_ip --from-step 125`, new): **PASS** for the primitive; continuation **INCONCLUSIVE (step 126)**, after-frame perceiver cache miss. Route excludes the actor tiles, first press LEFT (recorded run pressed DOWN into the clerk), position changed, failure-stage check consistent with RAM; map 39 -> 0 reported as extra evidence only. Command exit is 2 by design (partial replay is never 0). Requests 0, ledger unchanged. Limits: first leg only, plan stubbed, act scripted to the recorded option, actor set = RAM non-player sprites (assert-only, never given to the Loop).
- Legacy OM regressions: `--om cases` and `--om talk --run m1_decide3` both exit 2 "incompatible old replay / inconclusive: 4 perceiver cache misses (limit 3), first at step 1, last at step 2" (HF3 prompt/schema change). Previously talk exited 1. No fixture adaptation, no score change, captures untouched; fixture re-record is a separate decision.
- Evaluator: `eval_run.execution_success` is now execution-based and counts each action once (pending skipped, legacy rows fall back to skill status; m1_ip legacy: 77/150). Its `total` now counts settled actions; new keys `failed`, `pending_rows`.
- Review (WP C, read-only): truth boundary holds; three evaluator defects found, fixed in one repair round (continuation miss exited 0; false COMPLETED label; vacuous stage check). Open notes: `test_boundaries.py` does not forbid `scripts`/`truth_pokered` imports in `agent/`; tests select some options by label; `+2` movement tolerance is loose.
- Commands (orchestrator), after the fix: `test_loop`+`test_world`+`test_harness_reliability` 206 passed, 1 failed; `tests/agent` full: 513 passed, 1 failed (only the baseline `test_stage_b_cap_override...`), exit 1; `test_boundaries` 3 passed; eval_act/eval_run/om_eval 64 passed. eval_act/eval_run/om_eval/boundaries: 67 passed. Workers Haiku effort high, set per dispatch.
- Live checkpoint / fresh boot: **not authorized**; not run.
- Preservation: `runs/` unchanged; no `.cache` writes; ledger before/after total 3120 / m1 3072; scratch under `.tmp/hf/HF5/`. Requests 0.
- Program status: **offline verified** (required offline cases pass; the one suite failure is the pre-existing budget-cap test); the continuation of the real checkpoint is inconclusive, the legacy OM replays are incompatible, and **autonomy is not validated** (live gates not authorized). Cross-game generalization and moving-actor identity remain unsolved.
