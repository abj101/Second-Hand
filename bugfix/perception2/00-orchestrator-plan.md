# Orchestrator Plan: Perception V2

**Overall complexity: High**
**Execution model:** one orchestrator, at most two concurrent workers
**Target outcome:** combat inventory from current-frame LLM1 + aim marks; transition frames do not softlock; proposal/effect benches runnable from CLI; two consecutive `no_skill` combat wins.

## Run evidence to preserve

- `runs/20260907T190633Z_sts2_collect` — 8 steps from title through combat entry. Step 1.4 is a transition frame (`marks=0`) that logged `empty-action-surface`. Step 1.8 clicked leftover `Next` while combat was live.
- `runs/20260907T191012Z_sts2_collect` — LLM1 listed 5 hand cards + End Turn + enemy bbox; ACT `TurnSnapshot` had `cards=[]`, `targets=[]`; ended `decision_softlock`.

Do not overwrite or delete these runs.

## Locked contracts (do not reopen)

1. **Cause #1 overlay fix is a prerequisite, not this set's product work.** `infer_layout` must not classify a combat board as `paginated_overlay` from leftover `Prev`/`Next` when no tutorial is open. That fix lives in `loop/os/frame_model.py` and is committed before Batch 1.
2. **Promotion is current-frame and geometry-gated.** LLM1 `hand_cards` may stamp `role="card"` only when `allows_domain_entities`, `y >= HAND_Y`, bbox match, and mark role is in `{unknown, board_candidate, icon}`. Never promote `button` / `dialog` / `nav_*`. Card `object_id` stays `card_N` from `_prepare_frame_marks`.
3. **Anonymous hostiles become inspectable, not verified.** Publish a low-confidence target with current-frame geometry and add `inspect` to legal intents. Do not auto-inspect. Do not let prompt ids (`unknown_character_N`) become store `object_id`s.
4. **Transition frames are observation-only.** `marks==0` plus dark/hud-off/moving pixels must not increment the empty-surface softlock counter.
5. **Workers never drive the live game.** Worker A owns fixtures/tests/review. Worker B owns one production module per batch. Orchestrator alone edits `loop/agent/loop.py` turn-loop integration and runs live collects.

## Agent assignment

| Batch | Agent | Owns |
|---|---|---|
| 1 Regression lock | Worker A (standard) | fixtures, xfail replay, overlay commit review |
| 2 Combat inventory | Worker B + orchestrator | `frame_model.py` / `sts2.py` / `overlay_state.py` (B); `loop.py` reconcile + snapshot (orchestrator) |
| 3 Transition frames | Orchestrator + Worker A tests | `_run_v2_act` and telemetry in `loop.py` |
| 4 Non-combat gates | Worker B | `loop/__main__.py`, `loop/eval/*` |
| 5 Live validation | Orchestrator | collect runs; no product edits |

Worker brief: objective, files, allowed edits, expected evidence, stop condition. Handoff: finding, file:line evidence, changed files, test command + counts, unresolved concerns. One corrective retry, then narrow or escalate.

## Mandatory workflow for every batch

1. Record `git status --short` and preserve unrelated user changes.
2. Read this file and the assigned batch brief.
3. Inspect current behavior and existing tests before editing.
4. Add a failing regression test for each stated bug (Batch 1 xfails D1/D2 until Batch 2).
5. Implement the smallest fix that satisfies the brief.
6. Run the focused tests listed in the brief.
7. Run `python -m pytest tests/loop -q`. Do not lose a passing test.
8. Review `git diff --check` and the final diff.
9. Commit only files belonging to that batch (`fix:` / `test:` prefixes).
10. Report changed files, tests, remaining risks, and commit hash.

## Batch order and promotion gates

### Batch 1 — Regression lock

Continue only when the overlay commit is in, frozen frames exist, overlay assertions pass, and D1/D2 assertions are `xfail(strict=True)`.

### Batch 2 — Combat inventory

Continue only when the 191012Z replay yields 5 cards + End Turn + 1 target (or inspectable low-confidence enemy), buttons cannot become cards, and exports still exclude uncertain rows.

### Batch 3 — Transition frames

Continue only when the 190633Z 1.4 replay produces no error row, two transition frames do not softlock, and a stable empty combat frame still softlocks.

### Batch 4 — Non-combat gates

Continue only when `python -m loop benchmark-proposals` and `benchmark-effects` run on fixtures with zero legend false promotions.

### Batch 5 — Live validation

Continue only when Run A plays at least one card with zero transition-frame `empty-action-surface` and zero `decision_softlock`, and Runs B+C produce two consecutive combat wins.

## Stop / rollback

Stop if a batch:

- lowers the focused or cumulative pass count;
- lets LLM1 ids or prompt ids become store `object_id`s;
- promotes buttons/nav controls to cards;
- makes an uncertain target look verified in exports;
- requires the legacy adapter branch to widen game-specific logic;
- requires enabling skills to make `no_skill` pass.

Revert only the offending batch commit.
