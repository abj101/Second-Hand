# HF3: Are modal actions actually available on the current screen?

Status: done 2026-10-14 (offline). Depends on: HF2 gate passed. Requests: 0.

Read the [program](program-harness-reliability.md), [handoff](HF-handoff.md), shared action-identity contract, and HF2 Result. Expected impact: high; complexity: low–medium.

## Read and boundary

CodeGraph: `World.options perceive normalize SCHEMA select run_affordance act _decide`.
Read `agent/perceive.py`, `world.py` option generation, `skills.py` select/type paths, and existing perception/options/act/type tests. The bad input is a correctly labeled dialogue containing background furniture; fix action validity, not that screen label.

Prefer existing explicit menu-choice/affordance data when trustworthy. If current perception cannot distinguish a selectable choice, add one optional role field to entities (e.g. `selectable: true`), validated conservatively; do not create a game-name allowlist or infer selectable from mere visibility. Dialogue can have genuine choices, so a blanket `screen != menu => no select` rule is also wrong. A legacy fixture lacking role evidence must still advance/cancel, and genuine menu fixtures need explicit choice evidence.

## Work packages and ownership

Run **A -> B -> C**; HIGH workers. The orchestrator freezes the minimal role representation in the handoff before B consumes it.

| WP | Owns | Deliverable / targeted test files |
|---|---|---|
| A | `agent/perceive.py`, `tests/agent/test_perceive.py` | Preserve/normalize explicit current-screen selectable roles; compatibility default is not selectable. Tests: `test_perceive.py`. No prompt change if current affordances suffice. |
| B | `agent/world.py`, `agent/skills.py`, `tests/agent/test_options.py`, `tests/agent/test_skills.py`, `tests/agent/test_type_text.py` | Offer applicable actions and validate free-form actions at dispatch, including a stale chosen option. Tests: `test_options.py test_skills.py test_type_text.py`. |
| C | `agent/act.py`, `agent/loop.py`, `tests/agent/test_act.py`, `tests/agent/test_loop.py` | Both chooser modes preserve validity/identity and clear unsupported-action feedback; no goal-policy changes. Tests: `test_act.py test_loop.py`. |

Acceptance commands for A, B, C respectively:

```powershell
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_perceive.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_options.py tests/agent/test_skills.py tests/agent/test_type_text.py -q -p no:cacheprovider
.venv-agent/Scripts/python.exe -B -m pytest tests/agent/test_act.py tests/agent/test_loop.py -q -p no:cacheprovider
```

## Required behavior and tests

- Dialogue over furniture/NPCs: no select-furniture or world-navigation option; advance/cancel/free supported inputs remain. A hand-authored captured-perception fixture from steps26/34/44 reproduces the baseline failure; record what was manually annotated and why.
- Dialogue with real yes/no choices: select remains available only for those choices. Menu with decorative sprites: only menu choices are selectable. Include keyboard naming/type, title menu, battle menu, and ordinary world interaction fixtures.
- Stale/free-form `select television` on dialogue fails applicability before a needless menu-reading model call or input. Genuine menu selection retains its existing single-read, bounded cursor movement behavior. Test pointer and pad dispatch without hardcoding a game's labels.
- Bad/missing role fields normalize safely; old cached replies never make all background entities selectable. If prompt/schema changes, increment the perception cache/version boundary where required so resumed old normalized perceptions cannot masquerade as new-role evidence.
- End-to-end StubModel loop: the old dialogue scene progresses with advance; requesting an invalid select is reported accurately and never counted as successful execution. Do not force the model to advance automatically.

## Gate and replay caveat

Run targeted tests, full suite/boundaries, and these existing dry checks:

```powershell
.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om cases
.venv-agent/Scripts/python.exe -B scripts/eval_act.py --om talk --run m1_decide3
```

These commands use old cached perception and recorded actions. Any intentional incompatibility must be explained case-by-case and covered by the explicit new fixture; it is not permission to relax an expected outcome or claim the old replay passed. Do not alter protected captures/cache entries, spend to refill them, or raise prompt-size limits. Record zero requests and ledger equality. **HF3's gate is the required runtime cases + full suite + an honest legacy replay report.** Historical replay completion is a separate HF5 integration gate; an incompatible old recording alone does not block HF4. HF5 owns any minimal evaluator adaptation after this semantic change.

## Result and handoff

Baseline a3695e9 + existing dirty tree (HF1/HF2 and runner/budget edits); orchestrator Sonnet 5.5, workers Haiku effort high (set explicitly per dispatch).

- Role contract: optional entity field `selectable`; `perceive.normalize` keeps it only when exactly `True`, else omitted (legacy/missing = not selectable). SCHEMA entity property `selectable: boolean` (not required) + one PROMPT sentence. `World.options` modal branch offers `select <name>` only for `selectable is True` entities (advance, cancel, type, free buttons unchanged; world branch untouched). `skills.run_affordance` verb `select` first requires a same-named selectable entity in the perception, else returns failed "not a selectable choice on this screen" (inputs 0, calls 0, no model call, no press, `failure={"stage": "interaction", ...}`), identical for pointer and pad. `loop.py:207`: stage `interaction` is treated like `route` (a refused choice is no evidence about its target: it wrote does_nothing/opens relations and verdict "miss" against the television before). New failure stage value: `interaction` (already named in the HF contract).
- Schema/cache boundary: the PROMPT and SCHEMA change alters the model-cache key (sha256 of system, user, images, schema), so old perceptions never act as role evidence; no separate version constant needed.
- Cases (failed on baseline for the stated reason, pass now): dialogue over furniture/NPC offers no select (fixture hand-authored from the steps 26/34/44 shape: television, player, dialogue_box, girl, none flagged); dialogue with two flagged yes/no offers select only for them; menu/title/battle with decorative unflagged sprites; keyboard still offers type; stale `select television` (pointer and pad) fails with 0 model calls, 0 presses; loop-level stale free-form select records a failed skill, a fails entry under its intent key, cause text "failed at interaction", no relations on the television; advance option progresses a dialogue; flagged menu choice picked by number still reads the menu once and moves DOWN then A (non-regression guard, passes on baseline by design).
- Commands: test_perceive 11 passed (exit 0); options+skills+type_text 73 passed (exit 0); act+loop 106 passed, 1 failed (exit 1, only the baseline `test_stage_b_cap_override_keeps_phase_and_m1_limits`); boundaries 3 passed. Full `tests/agent`: 454 passed, 1 failed (same baseline test, ledger-cap arithmetic), exit 1.
- Old replay (honest report): `eval_act.py --om cases` ABORTS ("4 perceiver cache misses (limit 3), last at step 2") and `--om talk --run m1_decide3` stops with the same reason (exit 1). Cause: the perceiver prompt/schema changed, so every old cached perception misses by design. These are incompatible old replays, not passes; the new fixtures above are the replacement evidence. Not enabled: network fallback, cache refill, limit increase. HF5 owns the evaluator adaptation / re-recording decision.
- Requests 0; ledger before and after total 3120 / m1 3072 (runs/e3_budget.json unchanged).
- Limitations: real-model compliance with the `selectable` prompt is unmeasured (needs a live perceiver run, not authorized); until then legacy-style perception (no flag) means modal screens offer only advance/cancel/type/free buttons, so a real menu the perceiver fails to flag cannot be selected by name (free buttons remain). Title/battle/keyboard prompt wording is generic, not tuned.
- Gate: PASSED offline for required runtime cases + full suite (one baseline failure); historical replay INCOMPATIBLE (reported above). Next: HF4.
