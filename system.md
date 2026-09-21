# Adaptive perception and action system

## Purpose

Build a reusable screen agent that discovers the state representation it needs while interacting with an unknown visual application. The system does not begin with an application-specific ontology. It begins with pixels, neutral sensors, an evidence ledger, and an empty schema.

## Non-negotiable rules

1. No application names, mechanics, control labels, fixed coordinates, or domain state classes in production code.
2. Pixels and executed input are the only environmental boundary. Model output is a hypothesis, never ground truth.
3. Every schema claim cites evidence. Every accepted action cites a repeated causal trace.
4. Schema revisions are immutable and linked through `schema/HEAD.json`.
5. Projection from `SceneGraph + AdaptiveSchema` is the single authority for state and legal actions.
6. Unknown or ambiguous state fails closed. A mode is active only when its activation predicate matches.
7. Shadow mode cannot send input. Armed mode requires trusted capture, one exact window handle, a unique selector, a current snapshot, and a non-empty verified effect.
8. A schema is scoped to its workspace and target identity. It must never be reused against a different application merely because the layout looks similar.

## Runtime flow

1. **Capture** the pinned client window and retain capture provenance.
2. **Perceive** OCR tokens, regions, colors, motion, and stable tracks.
3. **Assemble** a neutral scene graph. OCR tokens remain first-class nodes so small values are not lost inside coarse visual regions.
4. **Ledger** every observation as append-only evidence.
5. **Orchestrate** schema diffs from evidence. An optional multimodal proposer receives the actual image and may suggest semantics, but cannot promote them directly.
6. **Validate** proposals using repeated reads or correlated before/after traces.
7. **Compile** accepted fields into sensor recipes and selectors.
8. **Project** the current graph into a typed snapshot and legal action surface.
9. **Invoke** only a current advertised action whose bindings resolve uniquely.
10. **Verify** the declared effect on a fresh trusted capture. Failed or no-op probes do not become actions.

## Current qualification state

The live shadow path is connected and capture provenance is enforced. On the latest unknown-window trial, OCR and numeric-field discovery worked and a second process reused the same schema HEAD without revision churn. Track stability and field continuity remain below the armed threshold, and no action was learned. Therefore live input remains unqualified.

The next engineering gate is a generic experiment controller: propose a low-risk interaction, require a unique selector and explicit arming, capture before/after evidence, roll back when possible, and promote only repeatable causal effects. This must not encode a known control name.

## Qualification gates

- 30 steady shadow frames with trusted capture only.
- Track stability at least 0.95.
- Stable field keys after warm-up.
- No revision churn on process restart.
- Correct mode activation; no fallback mode.
- One explicitly armed, uniquely bound, reversible experiment.
- The same effect verified twice before action promotion.

Until all gates pass, the correct behavior is observation and refusal—not a guessed click.
