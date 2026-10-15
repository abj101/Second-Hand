# Harness reliability: execution, feedback, and evidence

Status: planned; no implementation started. Branch at planning: `feat/lean-agent`.

## Start here

This is the implementation handoff for the 2026-10-14 audit of `runs/m1_ip`, not an instruction to restart the older R1/WM program. One phase is one orchestrator session. Use the paste block below; do not use the older generic kickstart to select these phases.

| Phase | Deliverable | Dependency | Offline gate | Live requests |
|---|---|---|---|---|
| [HF1](HF1.md) | Actor-aware local routing and truthful collision results | none | Detour, blocked corridor, actor moves, no double folding | 0 |
| [HF2](HF2.md) | One exit authority and one action identity through feedback | HF1 | Observed edge wins; current failure appears on the chosen option | 0 |
| [HF3](HF3.md) | Mode-valid modal actions | HF2 | Dialogue furniture never becomes a menu choice; real menus still work | 0 |
| [HF4](HF4.md) | Explicit unsettled outcomes and reproducible run records | HF3 | Pending is not failure; request/config provenance round-trips | 0 |
| [HF5](HF5.md) | Integrated regression and optional funded autonomy validation | HF1–HF4 | Synthetic closed-loop cases + protected checkpoint checks | 0 approved; live proposal in packet |

Run phases sequentially: they repeatedly touch `world.py`, `skills.py`, and `loop.py`. Parallelism is inside a phase only where its ownership table permits it. Do not open five competing implementation agents.

## Orchestration profiles

| Profile | Orchestrator | Every implementation/review worker |
|---|---|---|
| Codex | GPT-6.1 Sol (`gpt-6.1-sol`) | `gpt-6-luna`, reasoning effort `high` |
| Claude | Sonnet 5.5, selected by the user | Haiku, effort `high` |

Resolve Claude's exact model identifier and effort setting through that host's available controls; these are user-requested role names, not a claim that this Codex host exposes Claude. Never silently substitute another tier or omit HIGH. If unavailable, report the precise missing capability before dispatch. The Codex profile is exposed by this host's tooling. Orchestrator effort is the user's session setting.

Orchestrators own contracts, sequencing, review, integration, and gates. Workers implement one bounded card. A reviewer can run beside an implementer only when it is read-only. Never allow concurrent writers to the same file, including shared tests or fakes. Use the host's subagent tools; do not create user-visible chats for WPs.

## Paste block

```text
You are the orchestrator for the harness reliability program in
C:\Users\Abj1\Documents\GitHub\Avatar.
Read CLAUDE.md, docs/plans/program-harness-reliability.md,
docs/plans/HF-handoff.md, then the first incomplete HF phase packet.
Use GPT-6.1 Sol -> gpt-6-luna HIGH, or Sonnet 5.5 -> Haiku HIGH.
Explicitly set worker model/effort and verify host support; do not substitute.
Apply Ponytail Full. Use CodeGraph before code reads; report unavailable tools.
Execute only that phase. Delegate its cards with exact ownership, shared
contracts, test cases, and commands. Review and run each acceptance command
yourself. Give one precise repair round for a failed card; report any remaining
blocker rather than silently broadening scope. Do not claim a gate passed
without its evidence. Update that phase's Result and HF-handoff.md, then stop.
No live calls or budget changes unless this session separately authorizes them.
```

## Scope and authority

- This plan authorizes documentation only until an implementation phase is invoked. Its proposed HF contract addendum below then defines that phase's intended behavior. Keep `contracts.md` unchanged; record exact additive signature changes in the phase Result for later consolidation. Do not stop merely because an additive HF change is absent from the older contract document.
- Preserve the existing decision: models choose goals and actions. This program repairs execution and rejects structurally inapplicable actions; it does **not** hide/reorder otherwise applicable options because of their failure count, choose a different goal, or impose a generic history-based veto. If good feedback still yields repeated poor choices, HF5 records an autonomy failure. A history-based suppression policy is a separate product decision, not a covert fix.
- Preserve working IP conversation/fact flow, self tracking, place-merge checks, bounded skills, response cache, request ledger, and checkpoint recovery. No new planner, tracker, memory database, embeddings, model switch, prompt-tuning campaign, or general action-policy framework.
- Game names, RAM addresses, expected maps, and recorded coordinates belong only in evaluation/tests. Production `agent/` must not read evaluator truth. A second game, fixed-camera locomotion, alternate controller mappings, actor identity reconciliation, and complete goal verification are deferred, not claimed solved.
- Existing dirty files at planning: `scripts/run_agent.py`, `tests/agent/test_run_agent_budget.py`, and untracked `.claude/`. Preserve them. HF4 may touch the runner only after capturing and separating its pre-existing budget-only diff. No budget edits, `git add -A`, push, or automatic golden regeneration. Stage only phase-owned hunks if a phase is committed.
- Protect `runs/m1_ip`, `runs/m1_decide3`, all original checkpoints, and existing cache entries. Work on disposable copies under `.tmp/hf/<phase>/`; verify resolved cleanup paths stay inside that directory. Do not resume a protected run in place.
- Ponytail Full and CodeGraph apply to all workers. Headroom source compression was rejected during the audit and its proxy was unreachable. Use status checks if useful; do not retry the blocked export or make it a dependency. Local bounded inspection is sufficient.

## Shared HF contracts: freeze before parallel work

These are scoped proposals for this program, not edits already applied to runtime code. Preserve positional callers and old checkpoints; add optional fields/keyword arguments with defaults. Reuse plain dictionaries and existing records, not new abstraction layers.

1. **Occupancy:** extend current `route(..., strict=True)` with keyword-only `occupied=frozenset()`. Occupied tiles are never traversal or goal cells; the start cell is exempt. Apply only fresh occupancy in the current place, never stale actors from another place. Route-to-actor means a reachable adjacent tile plus facing, not entering its occupied tile; an intentional facing input for interaction is distinct from attempted traversal. The `strict=False` retry and appended exploration/exit steps must retain occupancy constraints. Do not persist actors as permanent walls.
2. **Execution failure:** append optional `failure=None` to `SkillResult`; when populated it is a small dict containing `stage` (`route`, `crossing`, `interaction`, or `settling`), the original `reason`, and available `place`, `tile`, `direction`, `blocker_id`. Preserve the old human-readable `reason`. Aggregate results retain the first actual blocking stage; route failure is not an exit contradiction. Inputs and movement already completed remain accounted for exactly once.
3. **Exit authority:** reuse `_exits`' grouping and extend its record only as needed to retain the chosen edge's direction/arrival/provenance. Prefer direct observed/crossed evidence over a reverse inference within one physical exit group. Do not merge distinct destinations or distant doors to the same place. Routing and display use the same representative; infeasible representatives can yield another feasible exit, not a hidden return to a weaker duplicate. An inferred reverse write must not overwrite an established observed edge.
4. **Action identity:** options carry internal `intent_key` (and a selected exit reference when relevant), never derived from presentation labels or option numbers. Use canonical source-place + entity/exit/destination IDs and verb family; raw inputs retain ordered keys. Do not conflate interaction with navigation failure. Capture the resolved identity before input; use it for execution, counters, option notes, trace, and resume. Named-list/free-action follow-ups resolve before keying. A selected specific exit must not be replaced by another door to the same destination. Retain legacy name-keyed failures as history; migrate only unambiguous matches, never sum aliases blindly.
5. **Settling and outcomes (HF4):** append optional `settled=None` to `SkillResult` (legacy unknown). Trace `observation_status` distinguishes `settled` and `pending`; keep prediction `verdict` separate from execution status. No new action on an unresolved observation: one bounded observe-only retry per outer step; no unbounded inner wait. Pending writes neither success/failure evidence nor permanent spatial contradictions and cannot count as task progress. Preserve a single pending action's identity and before-state until resolution, including across resume; account inputs once. Each outer step still emits one row; resolution references the original `action_step`, not a second execution. Retain the before-frame via a checkpoint-safe local reference. Update action/stuck counters once at resolution, not on observe-only rows. Existing `verdict='hit'` continues to mean correct prediction, not successful goal completion.
6. **Provenance (HF4):** one manifest record per invocation, and one correlated request record per logical model call/retry/cache result. Record actual mode flags, source fingerprint including dirty content hashes, prompt/schema/model parameters, response, local image-content references, step/call ID, cache status, and request count. Local only; no credentials, environment dumps, base64 copies per call, or external export. Cached calls must reference recoverable replies too. Resume mode disagreement is explicit, never silently inherited from a CLI default. Old runs remain readable and are marked provenance-incomplete.

## Common validation and handoff

- Every behavior regression first fails on the baseline for the claimed reason, then passes; do not make xfail, skipped tests, changed labels, or a higher token cap stand in for correctness. Use `GridEnv`/`StubModel` and a failing network stub for offline checks.
- Worker: targeted tests. Orchestrator: run the full suite once per integrated phase, after its last change:
  `.venv-agent/Scripts/python.exe -B -m pytest tests/agent -q -p no:cacheprovider`
  Add `-n auto` only where tests do not share scratch paths. Read pytest's summary and exit code directly.
- Every phase reruns `tests/agent/test_boundaries.py`. HF3/HF5 also run existing OM/IP dry checks. Their cached replay shows prompt/options compatibility, not new autonomous success.
- Old replay can fail to match a recorded option that HF3 intentionally removes, or miss perception cache entries after a new trajectory. Report that as an incompatible old replay or inconclusive branch, with the exact step. Never replace the new state with old recorded observations, enable network fallback, or count a partial replay as a pass.
- Budget at planning: ledger total 3120/3125, M1 3072/3075. This is a snapshot; reread before any future spend. HF1–HF4 and HF5 offline authorize **zero** requests. Proposed live caps do not raise or authorize the persistent ledger.
- Each Result records baseline/commit+dirty fingerprint, files changed, failing-then-passing cases, command/exit/summary, requests and ledger before/after, limitations, and exact schema additions. Update `HF-handoff.md` (under 80 lines, rewrite); don't overwrite the older program's handoff during this program. Update only the HF row in `docs/plan.md`.
- After a passed phase, follow the existing commit convention only for its owned changes and HF documents; use explicit paths/hunks and never stage the whole docs tree or unrelated dirty files. A commit is not a push. If committing is unavailable, record the exact uncommitted phase diff for the next session.

## Audit anchors and measurement rules

- `m1_ip`: 150 trace rows, 328 model-call events, 256 actual requests. Steps 120–149 select `outside house`; steps 123–150 yield 28 consecutive no-change results. Truth is sampled **before** step S; trace `tile` is **after** S: compare it with truth S+1.
- Final player `(0,-5)` equals evaluator `(2,2)` relative to entry `(2,7)`; actor `(0,-4)` blocks DOWN. Checkpoint `000125` reproduces the stationary failure; `000150` preserves final counters.
- At checkpoint 150, `outside house` failures=31 while `exit: door mat` failures=7. The latter's displayed history stays at step 114. Source: `World.options`, `Loop.step`, `fail_key`.
- Wrong modal selection appears at step 26 and repeatedly at 34–45; step 43 and 46–47 advance successfully. Step 36 is a failed zero-input skill with a correct `no_change` prediction: never use aggregate verdict hits as the action-success score.
- Earlier crossings at 78, 85, 94, 97, 104, 107 are counterexamples to blanket navigation/memory failure. Keep those behaviors. No confirmed localization drift explains the final stall.

## Program result

Not run. Planning is complete only when packets and links validate; implementation, offline gates, and live autonomy gates remain unmeasured.
