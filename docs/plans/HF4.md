# HF4: Can we distinguish pending observation from failed execution and reconstruct a decision?

Status: done 2026-10-14 (offline). Depends on: HF3 gate passed and replay limitations recorded. Requests: 0.

Read the [program](program-harness-reliability.md), [handoff](HF-handoff.md), shared contracts 5–6, and HF3 Result. Complexity: medium–high; implementation is broader than a trace-only patch.

## Read

CodeGraph: `wait_settled SkillResult _result World.travel Loop.step Loop.state Loop.restore effects verdict Ollama.ask Ollama._log run_agent.main`.
Read associated observer/skills/predict/loop/model/runner tests. The current observer already produces a settlement flag; the loop discards it. Current call logs store metadata without prompts/responses/config. Reuse those paths rather than inventing a separate tracing service.

## Work packages and ownership

**A || B -> C**. A and B have disjoint files and may run in parallel; C begins after both are reviewed. All HIGH workers.

| WP | Owns | Deliverable / targeted tests |
|---|---|---|
| A | `agent/skills.py`, `agent/world.py`, `agent/loop.py`, `agent/predict.py`, `tests/agent/test_observer.py`, `tests/agent/test_skills.py`, `tests/agent/test_loop.py`, `tests/agent/test_predict.py` | Propagate settlement through skill aggregation, pause new actions while pending, preserve original attribution, prevent uncertain belief updates. Tests: observer, skills, loop, predict. Observer code should already suffice; request scope expansion only with a concrete missing behavior. |
| B | `agent/model.py`, `agent/trace.py`, `scripts/run_agent.py`, `tests/agent/test_model.py`, new `tests/agent/test_run_provenance.py` | Local invocation manifest and complete correlated model records; explicit runtime mode restoration. Preserve runner's pre-existing budget diff. Tests: model, new provenance, existing run_agent_budget. |
| C | `agent/loop.py`, `scripts/run_agent.py`, `tests/agent/test_loop.py`, `tests/agent/test_run_provenance.py` | Join step/action/call correlation and checkpoint provenance; no duplicated pending execution on resume. Tests: loop, provenance, run_agent_budget. |

Acceptance commands for A, B, C respectively (provenance test file is created by B):

```powershell
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_observer.py tests/agent/test_skills.py tests/agent/test_loop.py tests/agent/test_predict.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_model.py tests/agent/test_run_provenance.py tests/agent/test_run_agent_budget.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_loop.py tests/agent/test_run_provenance.py tests/agent/test_run_agent_budget.py -q -p no:cacheprovider
```

## Required state-machine tests

1. Pre-action settlement times out: no chooser/input against the unstable frame, no failure/crossing evidence, bounded return with pending status.
2. Action completes inputs but post-action settlement times out: retain its before-state and identity; next step observes only, attributes a later change to that action, and records each input/effect once. Do not enqueue another action.
3. Forever animated/unsettled fixture: bounded per-step waits, no infinite internal loop, truthful pending status; budget/external step cap still terminates the run.
4. Checkpoint while pending and restore: no replayed input, lost action identity, duplicate failure, or accidental mode change. Include an old checkpoint without new fields: obtain fresh observation, mark provenance incomplete, and do not fabricate past certainty.
5. Failed zero-input selection with correct `no_change` prediction remains `verdict=hit` but `execution=failed`; metrics must not call it successful interaction. Unsettled observation cannot establish `no_change`, refute an exit, or promote a relation.
6. A scene cut spanning the observation retry keeps the action's source and before-frame. Test travel aggregation and raw buttons, not just single-step approach. Don't fold uncertain movement or cross twice.

## Required provenance tests

- Stub calls cover success, cache hit, retry, malformed reply, unavailable model and interrupted call. Correlate exact system/user/schema, response, image reference hash, role/model/parameters, step, and actual request count without contacting a service.
- Resolve each logged image reference locally and verify its hash. Deduplicate identical content. Serialize no credentials or environment dumps; caller-provided test sentinel secrets in transport headers must be absent.
- Fresh/resume manifests record explicit `decide`/`act_text`, start checkpoint, source revision and dirty hashes. Conflicting explicit mode flags fail clearly before input; unspecified flags restore saved mode. For legacy checkpoints require an explicit mode or record the chosen fallback as incomplete provenance.
- Logging does not alter cache keys, charged POST counts, or existing cap behavior. An unwritable log produces a visible diagnostic; never advertise that run as fully reconstructable.

## Gate and handoff

All required cases plus full suite/boundaries pass. Reconstruct one fake decision exclusively from saved manifest, request record, and image references; compare byte-for-byte inputs and output. Zero requests and unchanged ledger. Record on-disk schema size and legacy limitations, not guessed performance gains.

## Result

Baseline a3695e9 + existing dirty tree (HF1-HF3, runner/budget edits; budget-only diff saved at `.tmp/hf/HF4/preexisting_budget.diff` and preserved). Orchestrator Sonnet 5.5; workers Haiku effort high (set per dispatch); WP A||B then C; B had one repair round (runner read `model.log_failed` that test fakes lack -> `getattr`).

- **SkillResult**: trailing `settled: bool|None=None`; `Ctx.settles` list shared through `replace()`; flag = AND of every settle inside a skill, nested skills and `World.travel` legs included. **Trace row (all rows)**: `observation_status` settled|pending, `execution` (skill status|null), `action_step` (original step on resolved rows), `invocation`, `call_ids`; `degraded`, `retries`, `provenance_incomplete` only when applicable. Pending action rows: verdict `unobservable`, outcome null. **Loop.state()**: `pending` (JSON dict: action_step, before-frame ref, place, intent, choice, pred, skill summary, per, world bits, action state), `modes` {decide, act_text} (omitted after a legacy restore). **harness.json**: `provenance` {invocation, manifest, last_call, incomplete}. **Loop**: `pending`, `invocation`, `provenance_incomplete`; restore raises ValueError if constructor modes conflict with saved ones.
- **Behavior**: unsettled before-frame -> one hold row, no perception/chooser/input; unsettled after an input -> pending row, no relations/edges/crossings/fails/stuck/prediction from that frame; next steps observe only (no second action) until settled, then resolve once through the same tail (`_settle`) against the original before-frame/place; checkpoint while pending restores without replaying input; a missing before-frame resolves `degraded` (execution counted once, no spatial/relational evidence). Zero-input failed select with a correct no_change prediction: verdict hit, execution failed.
- **Provenance**: `Ollama.seq`, per-call records in calls.jsonl (system, user, schema, params, response, key, cache hit|miss|none, posts, images [{sha,mode,size,path}], error) with images saved once to `run/images/<sha>.png` (`load_image_ref` verifies the hash); `run/manifest.jsonl` one line per invocation (modes, start checkpoint, git head + dirty content hashes, provenance complete|incomplete, notes); `--decide/--no-decide`, `--act-text/--no-act-text` tri-state, conflict with a checkpoint fails before PyBoyEnv; unwritable log -> stderr diagnostic + manifest flagged incomplete. Sizes (stub run): call record 3.2-4.6 KB per line (one image file ~0.5 KB), manifest line 319 B, trace row +35 B.
- **Tests**: A 11 new (settled flag, travel AND/fold-once, pre-action hold, post-action pending, forever-animated, checkpoint-pending, zero-input hit/failed, no-evidence-when-unsettled, scene cut across retry for press and travel, degraded); B 10 model/provenance + resolve_modes/manifest/fingerprint tests; C 10 (modes round trip, legacy incomplete, mode conflict, call_ids, resolved-pending ids, runner pending checkpoint, provenance block, resume modes, reconstruction). Workers confirmed each behavior test fails on the pre-change code (several trip on the new key before the behavioral assertion; `forever` fails behaviorally, StopIteration).
- **Reconstruction gate**: `test_one_step_is_rebuilt_from_run_files_alone` rebuilds a real 4-call step from manifest + trace `call_ids` + calls.jsonl + image refs only: recomputed cache keys equal recorded keys, image bytes and recorded responses match, zero network.
- **Commands (orchestrator)**: full `tests/agent`: 494 passed, 2 failed, exit 1 = the known baseline `test_stage_b_cap_override...` (hard-codes the old 2925/2875 caps) + `test_type_text::test_approach_keeps_sidestepping_past_a_two_tile_obstacle`, whose `wait_settled` fake lacked `.settled`; I added `settled=True` to that fixture (one line in tests/agent/test_type_text.py, outside the packet's ownership; not re-run in the full suite afterwards, but test_type_text+test_options+test_skills: 76 passed). `test_world_golden`+`test_boundaries`: 4 passed. The w0_golden act trace was regenerated twice, each time verified additive only (old == new minus `observation_status/execution/action_step`, then minus `call_ids/invocation`; 8 rows).
- Requests 0; ledger total 3120 / m1 3072 unchanged; `runs/` untouched.
- **Limitations**: `params.format` in a record stays "schema" when a 4xx forces the json fallback (visible only via `posts`); probe presses at the head of `Loop.step` are not settle-gated; `world._promote` runs at action time; zero-input unsettled actions yield a pending row with no pending state; skill `settled` is False if any settle inside timed out (incl. the first), so the loop may defer when only the start settle failed; degraded resolution has no spatial evidence; resumes of checkpoints from before this phase are marked provenance-incomplete and carry no `modes`; `eval_run` metrics were not changed (use `execution`, not `verdict`, as the success signal); image hash round-trip tested for RGB/L only; new schema fields are additive and contracts.md is unchanged.
- Gate: PASSED offline (required state-machine + provenance cases, reconstruction, suite with one baseline failure). Next: HF5; stop after handoff.
