# Perception improvement plan: a harness for unfamiliar games

Date: 2026-09-07. Scope: research and implementation plan; no gameplay or harness changes performed. The STS2 failure is a regression case, not a specification for game-specific production behavior.

## Decision

Implement **complementary visual candidate fusion** first: preserve useful OCR, GUI-detector, and classical-vision proposals together, with overlap deduplication and a budget that preserves spatial coverage. Current code suppresses all classical-vision candidates whenever any GUI detection survives. The audit reproduced the resulting omission on the saved failure frame. Fixing this is a concrete, game-independent step that uses sensors already present.

Then add evidence-driven perception recovery: when the observation lacks trustworthy candidates, or an attempted action has no verified effect, obtain fresh proposals independently of the old candidate list. Validate and publish them under the current observation ID before asking the policy to act again.

Do not implement parchment boundaries, map-node rules, card bands, or game-name branches as this fix. Generic invariants are appropriate: coordinate transforms must be reversible, objects must belong to the current observation, model confidence must not become sensor proof, and failed actions must influence the next decision.

The target is a game-independent core. Window selection, input device bindings, timing capabilities, and optional evaluation/reset adapters remain configurable. Names, rules, layouts, and control meanings should be learned from visible evidence and interaction. Unknown information must remain unknown. A universal interface is achievable before universal playing competence; turn-based success does not establish real-time control capability.

## What the evidence actually establishes

- In `20260907T030739Z_sts2_collect`, steps 5–27 contain **23 map actions with no detected pixel change**: 16 generic-icon clicks and 7 `node_0` clicks. The supplied action sets omit the visible central navigation targets. All 23 snapshots have `last_execution: null`.
- `should_run_llm1_perceive` skips vision for every non-combat activity unless forced. That optimization assumes OCR and existing marks are adequate for graphical navigation. See [perceive.py](C:/Users/Abj1/Documents/GitHub/Avatar/loop/agent/perceive.py:1325).
- `number_spatial_nodes` promotes placeholders using coarse coordinates; `ground_spatial_labels` can bind a node label to a remaining placeholder. Renaming a candidate is not evidence that its function was identified. See [aim.py](C:/Users/Abj1/Documents/GitHub/Avatar/loop/os/aim.py:1052).
- `_rescan_scene` reuses the last observation and supplied detector candidates. Another model pass can preserve the original proposal omission. [loop.py](C:/Users/Abj1/Documents/GitHub/Avatar/loop/agent/loop.py:3028).
- Candidate fusion admits classical-vision proposals only when there are no retained GUI boxes (`not gui_kept`). On the saved step-5 frame, the audit worker's current-code replay found five CV candidates without GUI input, but only `Back` after active GPA proposal fusion. The GPA cap was dominated by legend/chrome detections. This reproduces the omission with current code; it does not reconstruct the historical detector's exact internal state. [aim.py](C:/Users/Abj1/Documents/GitHub/Avatar/loop/os/aim.py:2041).
- No-op clicks without execution errors clear the structured failure field. Dispatch success and effect success therefore need separate representation. [loop.py](C:/Users/Abj1/Documents/GitHub/Avatar/loop/agent/loop.py:7107).
- Mean logged OCR time was **3.93 seconds**, versus **1.12 seconds** for `think_ms`, over 27 steps. These are recorded timing fields, not a complete non-overlapping wall-time breakdown. Perception quality and latency both matter.

Correction to the earlier audit: old reward content in perception audit fields does **not alone prove** a stale reward scene drove the map action. The current code builds an OCR scene and only reuses cached semantic controls for combat. The map snapshots themselves say `map`. Missing visual candidates and their incorrect promotion are better-supported causes. Also, evaluation tools exist, and `--no-learn` skips post-run curation rather than all memory writes.

## Research implications

Microsoft's OmniParser separates finding interactable elements from describing their function. That supports separating our proposals from semantic interpretation and action admission. It does not establish that OmniParser recognizes game artwork reliably. Treat it as an optional offline challenger, not an assumed upgrade. [Microsoft Research](https://www.microsoft.com/en-us/research/articles/omniparser-v2-turning-any-llm-into-a-computer-use-agent/)

OSWorld-G evaluates text matching, element recognition, layout understanding, and precise manipulation. Adopt that separation in evaluation: correct JSON and recognizable labels do not demonstrate correct click geometry. Its desktop benchmark is not evidence of game-playing performance. [OSWorld-G](https://osworld-grounding.github.io/)

Anthropic's orchestrator-worker experience emphasizes bounded assignments and warns that multi-agent work consumes more tokens and suits independently separable work. Use inexpensive workers for isolated evidence, fixtures, and patches; keep integration and acceptance with one orchestrator. [Engineering report](https://www.anthropic.com/engineering/multi-agent-research-system)

## Orchestrator and inexpensive workers

Use one orchestrator and at most two concurrent workers. This investigation dispatched two workers with the available low-cost `gpt-5.6-luna` configuration. No dollar savings are claimed: actual model usage and retries must be measured.

The orchestrator owns the hypothesis, interfaces, phase gates, integration, and final review. Give each worker a short brief containing its objective, relevant files, allowed edits, expected evidence, and stop condition. Prefer fresh context to copying the entire conversation. Require a compact handoff: finding, file/line evidence, changed files, checks, unresolved concerns. One corrective retry, then escalate or narrow the task; no recursive delegation by default.

Worker A owns fixtures and scoring. Worker B owns the currently selected production module and its focused tests. The orchestrator alone changes the shared turn-loop integration. After each patch, reuse the fixture worker for independent regression review. Parallelize independent work only; never let workers compete to drive the same live game window.

Keep this development team separate from runtime perception. Initially, runtime recovery needs one bounded visual specialist call, not a permanent team of model agents on every frame. The runtime controller owns all input execution; visual specialists return evidence and hypotheses only.

## Phased delivery

### Phase 0 — Establish a trustworthy baseline

Extend the existing grounding and perception benchmarks. The current grounding runner supplies `gui_marks=[]` and applies expected enemy boxes to predictions. Preserve this as an explicitly oracle-assisted component test; add a production-path benchmark where truth is available only to the scorer. [grounding_benchmark.py](C:/Users/Abj1/Documents/GitHub/Avatar/loop/eval/grounding_benchmark.py:89)

Start with 30 manually reviewed observations/transition pairs across three visually different available games or controlled environments. Include textless controls, overlays, scrolling, repeated icons, resolution changes, and genuine no-ops. Split by scene/session, reserving one game for transfer testing; adjacent frames cannot span train and held-out sets. If other-game captures are unavailable, mark the transfer gate pending rather than claiming generality.

Record raw proposals, filtered candidates, rejection reasons, detector health, transforms, observation IDs, active scene, and the actual policy input. Distinguish an unavailable detector from a healthy detector returning zero boxes. **Gate:** reproduce the supplied failure without live inputs; missing fixtures and leaked truth fail the evaluation.

### Phase 1 — First production fix: complementary proposals, then reacquisition

Start with a narrowly scoped fusion patch: remove the all-or-nothing CV suppression, preserve proposal provenance, deduplicate overlapping proposals, and allocate remaining capacity across source and spatial coverage. Verify that unrelated GUI detections and dense clusters cannot remove all candidates elsewhere. Names such as `icon` or `node_0` must not themselves establish action legality.

Replace the blanket non-combat skip with a generic evidence gate. Trigger recovery on a material scene change, only unidentified candidates, contradictory evidence, or an unverified action effect. These are uncertainty signals, not a claim that candidate completeness can be proven from one image.

Give a visual proposal provider the raw frame, task intent, and recent observed outcome. Let it propose bounded boxes and interaction hypotheses without requiring names from the old detector list. Use the existing proposal-provider seam where practical. If a target is too small, allow one targeted higher-resolution crop and retain its transform to client coordinates.

Keep localization, semantic confidence, and evidence of interactability separate. Geometry checks do not prove a button is usable; repeated agreement with the same model is not independent corroboration. Uncertain candidates may support a bounded exploratory action selected by the policy, but must not be labeled verified or silently exported as successful training data.

Audit proposal fusion first: retain complementary non-overlapping visual candidates even when the GUI detector finds something. Do not simply raise the global mark cap; reserve bounded proposal coverage and let semantic verification reject unsupported candidates. This is a small, game-independent implementation seam within the recovery change.

Retain stable object identity across harmless motion; invalidate obsolete bindings on observed transitions. Publish only current-observation candidates. **Proposed gate:** zero footer/legend false promotions on the known regression, at least 90% candidate recall and 95% click containment on annotated held-out interactable objects, and zero stale-coordinate dispatches. Also report abstentions and false-positive rates so suppressing every action cannot pass.

### Phase 2 — Close the perception/action feedback loop

Always report both dispatch outcome and observed effect to the policy, including successful dispatch with no verified effect. Track failures by object identity, observation context, and spatial overlap—not temporary mark numbers alone.

Use a bounded sequence: initial observation → action → effect check → fresh proposal/crop → one materially different attempt or explicit unresolved outcome. Do not ban a point permanently: release suppression when the scene or relevant evidence changes. Learn expected effects from observations; do not embed lists of game-specific progress events in the core.

**Gate:** the known repeating cycle cannot recur; unchanged evidence never produces unlimited retries; tooltip/animation-only changes do not count as task progress. Uncertain results stay inconclusive.

### Phase 3 — Generalize and reduce perception cost

Move existing card/enemy bands, game-specific screen rules, and reward semantics out of the shared path into optional legacy adapters. Compare adapter-on and adapter-off behavior explicitly. Reuse one observation bundle for OCR, proposals, and verification; use region-level refresh and call vision on uncertainty rather than every frame.

Benchmark the current visual model, crop recovery, and at most one alternative parser on identical held-out inputs before adding dependencies. Measure p50/p95 observation-to-action time, detector availability, call/token counts, recovery success, and cost per verified transition. **Gate:** preserve Phase 1 quality while reducing measured runtime cost; demonstrate transfer to the reserved game without adding a game-name branch.

### Phase 4 — Validate actual play

Run bounded fresh sessions across games, keeping continuation probes separate. Report verified transitions, unresolved perception failures, task completion, and full-game wins separately. Extend validation to keyboard/controller holds, camera motion, continuous tracking, and action deadlines before claiming real-time support.

Train only after perception produces reliable evidence. Keep proposed actions, actual input, verified effects, and uncertainty distinct in exports. **Gate:** repeatable improvement across held-out sessions and no increase in false action admission; frozen-frame success alone cannot qualify the harness for live play.

## Next implementation assignment

Deliver Phase 0 and the fusion portion of Phase 1 first: production-faithful replay coverage plus complementary candidate fusion in `loop/os/aim.py`, with focused tests. Add uncertainty-triggered reacquisition as the next dependent patch. Use the failed STS2 sequence to demonstrate the fix, and an unfamiliar layout to test transfer. Defer broad refactoring, new model infrastructure, and training until this gate passes. All thresholds above are proposed acceptance criteria, not measured achievements.

Audit checks: the workers report 72 passing aim/perception-gate tests and four passing grounding/perception-benchmark tests. These confirm the existing tests pass despite the reproduced omission; they do not verify a fix. No production files were edited in this investigation.
