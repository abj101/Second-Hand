# Plan: deterministic hover, capture, crop, and OCR discovery

## Vision

Discovery should be a bounded sensor workflow, not free-form reasoning:

1. Find every actionable unknown object.
2. Hover exactly one unknown without pressing a mouse button.
3. Wait for the UI to settle.
4. Capture a fresh full frame.
5. Derive focused crops for the hovered object, enlarged card, and/or tooltip.
6. Run OCR on those crops.
7. Bind verified text to the exact hovered object.
8. Repeat for the next unknown.
9. Only then hand the completed scene to the action reasoner.

The system must preserve uncertainty when evidence is insufficient. It must never guess a familiar Slay the Spire name from appearance, memory, or convention.

## Scope

Initial object classes:

- Hand cards with unknown title or description.
- Enemy sprites with unknown identity or intent tooltip.
- Unknown combat buttons/icons that affect legal action.

Excluded from enemy discovery:

- Player, allies, pets, summons known to be friendly.
- HP bars and OCR text anchors without a corresponding sprite.
- Decorative UI, top chrome, and generic detector blobs.

## Core state machine

Use an explicit discovery state machine per combat scene:

1. `INVENTORY_UNKNOWN`
2. `CHOOSE_UNKNOWN`
3. `RELEASE_INPUT`
4. `CAPTURE_BEFORE`
5. `HOVER_OBJECT`
6. `WAIT_FOR_TOOLTIP`
7. `CAPTURE_AFTER`
8. `LOCALIZE_CHANGE`
9. `BUILD_CROPS`
10. `OCR_CROPS`
11. `OPTIONAL_VLM_READ`
12. `VERIFY_AND_BIND`
13. `NEXT_UNKNOWN` or `DISCOVERY_COMPLETE`

Every transition should produce structured evidence. A timeout or ambiguity returns to `NEXT_UNKNOWN` with an unresolved result; it must not fall through into a click.

## Phase 1: build a reliable unknown inventory

### 1.1 Fuse observations without losing provenance

- Start from OCR card-title anchors, GUI/CV proposals, LLM1 typed bboxes, host HP data, and the entity tracker.
- Preserve source and confidence for every field rather than replacing the whole object.
- Give each physical object:
  - stable `object_id`,
  - current-frame `mark_id`,
  - role,
  - bbox,
  - target point,
  - current label/name,
  - identification status,
  - inspectability,
  - evidence references.

### 1.2 Deduplicate before discovery

- Merge enemy rows that overlap the same sprite using bbox IoU and center distance.
- Associate HP/text anchors with the nearest compatible sprite rather than publishing them as targets.
- Remove player/ally rows before unknown enumeration.
- Preserve duplicate cards as separate instances even when their titles match.

### 1.3 Prioritize unknowns

Recommended order:

1. Unknown hostile entities required for a legal targeted action.
2. Unknown cards currently playable with available energy.
3. Unknown combat controls that gate progress.
4. Informational unknowns that do not affect the current decision.

Within a class, inspect left-to-right using stable object IDs. Do not choose by transient mark number.

## Phase 2: safe hover execution

### 2.1 Enforce buttonless discovery

- Call `release_all()` before moving to any inspection target.
- Emit only a mouse-move primitive for hover.
- Assert no mouse buttons are down before, during, and after the move.
- Never use drag, mouse-down, or click during discovery.
- If a card is already selected, cancel or safely resolve that state before inspecting another object; do not drag the selected card across the scene.

### 2.2 Bind hover to a frame-local contract

- Explorer must use an inspection contract containing `run_id`, `step_id`, `object_id`, and current `mark_id`.
- Validate role and bbox immediately before movement.
- Reject stale contracts and recapture rather than approximating coordinates.
- Record the actual screen coordinate and object bbox used.

### 2.3 Detect hover readiness

- Capture a before image immediately prior to movement.
- After movement, poll short captures until one of these occurs:
  - a stable tooltip/change region appears,
  - the card visibly lifts/enlarges,
  - a bounded timeout expires.
- Require two consecutive sufficiently similar after frames before OCR so animated tooltips do not produce partial text.

## Phase 3: focused capture and crop generation

### 3.1 Save the full evidence frame

- Always save the full post-hover client image for audit and scene context.
- Keep the full-resolution image; create resized encodings only for model calls.

### 3.2 Produce object-specific crops

For every inspected object, generate:

- `object_crop`: padded bbox around the hovered card/enemy.
- `local_context_crop`: larger region including nearby labels, HP, and intent.
- `change_crop`: bounding rectangle of significant before/after pixel differences outside animation-only regions.
- `tooltip_crop`: text-like region associated with the hover change.

For cards, additionally generate:

- `enlarged_card_crop`: detect the lifted/enlarged card after hover instead of assuming it remains inside the original hand bbox.
- Separate title, cost, type, and description subcrops when geometry is reliable.

For enemies, additionally generate:

- sprite/name crop,
- HP/status crop,
- intent/tooltip crop when present.

### 3.3 Localize tooltips dynamically

- Compute a before/after difference mask while excluding the cursor and known animated sprite regions.
- Group changed pixels into candidate panels.
- Rank candidates by text density, rectangularity, proximity to the hovered object, and novelty versus the before frame.
- Do not hard-code one tooltip location; retain conservative class-specific priors only as tie-breakers.

## Phase 4: OCR-first identification

### 4.1 Run OCR per crop

- OCR the tooltip and enlarged-card crops at native resolution plus one 2x upscaled variant.
- Use contrast-normalized and sharpened variants for low-contrast text, while retaining the original crop as evidence.
- Keep word boxes and confidence, not only flattened text.
- Reconstruct lines using spatial ordering.

### 4.2 Parse typed fields

Card parser:

- title,
- energy cost,
- card type,
- rules description,
- target requirement when explicitly evidenced.

Enemy parser:

- proper name,
- current HP,
- status labels,
- intent text where visible.

Unknown UI parser:

- control label,
- tooltip description,
- whether it appears actionable.

### 4.3 Use VLM only as a bounded reader fallback

- If OCR is incomplete, send the crop—not only the whole screen—to LLM1 with a narrow transcription schema.
- Include the full frame as secondary context only when needed.
- Ask for visible text and object type, not strategy or inferred identity.
- Never accept a VLM name unless supported by crop pixels or independent OCR evidence.

## Phase 5: verification and exact attribution

### 5.1 Attribute evidence to one object

- Inspect one unknown per capture cycle.
- Compare before and after frames to prove which UI region changed.
- Bind extracted text only to the `object_id` in the active inspection contract.
- Do not enumerate all visible enemies and assume the returned name belongs to the hovered one.

### 5.2 Identity confidence rules

Mark an object `identified=true` only when:

- OCR yields a plausible name above threshold in the associated crop, or
- OCR and crop-focused LLM1 agree, or
- two independent hover captures produce the same normalized name.

Reject as identity evidence:

- names appearing elsewhere in the full HUD,
- journal or semantic memory alone,
- model recognition without readable pixels,
- card description words mistaken for titles,
- HP numbers without a name.

### 5.3 Persist evidence, not coordinates

- Persist canonical identity and verified UI semantics with provenance.
- Never persist frame-local mark IDs.
- Reuse learned identity only after a fresh-frame visual/object match; spatial proximity alone is insufficient across different combats.
- Store links to the full frame and crops that justified each identity.

## Phase 6: discovery scheduling and completion

### 6.1 Enumerate until complete

- Maintain a queue of unknown object IDs.
- After each successful or exhausted inspection, rebuild the current scene because hover can alter card layout and mark numbering.
- Rebind the remaining queue by stable object tracking.
- Continue until every decision-relevant unknown is `identified`, `identity_unresolved`, or no longer present.

### 6.2 Bound retries

- Suggested default per object:
  - up to two hover/capture attempts,
  - multiple OCR preprocessing variants within each attempt,
  - at most one crop-focused VLM fallback.
- If unresolved after the budget, record the exact failure reason: no tooltip, unstable animation, empty OCR, conflicting text, stale object, or ambiguous attribution.
- Apply an explicit fallback policy after the queue is exhausted. Do not loop forever between recheck and hover.

### 6.3 Handoff to ACT

Explorer returns a structured discovery result, not advice:

```json
{
  "scene_revision": 7,
  "objects": [
    {
      "object_id": "enemy_1",
      "status": "identified",
      "name": "Jaw Worm",
      "evidence": ["tooltip_crop", "ocr"]
    }
  ],
  "remaining_decision_unknowns": []
}
```

The runtime must then rebuild:

- grounded scene,
- screen-object inventory,
- game state,
- legal actions,
- card/target instance prompt.

ACT receives no stale marks or pre-hover action proposal.

## Artifact and logging layout

For each inspection, save something equivalent to:

```text
frames/<episode>/<step>/discovery/<object_id>/
  before.jpg
  after_full.jpg
  object_crop.jpg
  local_context_crop.jpg
  change_crop.jpg
  tooltip_crop.jpg
  ocr.json
  result.json
```

`result.json` should include contract IDs, timings, crop boxes in full-frame coordinates, OCR words/confidence, VLM fallback output, final status, and rejection reasons.

## Tests

### Unit tests

- Player exclusion and enemy/HP-anchor deduplication.
- Unknown queue ordering and stable ID rebinding.
- Hover emits movement only and begins with all buttons released.
- Difference-mask tooltip localization.
- Crop coordinates remain correct under letterboxing/content-box mapping.
- OCR line reconstruction and card/enemy field parsing.
- Identity evidence cannot cross from one inspected object to another.

### Recorded-frame tests

- Unknown enemy with a visible hover tooltip.
- Enemy hover with no textual tooltip.
- Five overlapping cards, including duplicate titles.
- Hovered card lifts outside its original hand bbox.
- Selected-card state present before discovery.
- Multiple enemies with only one hovered.
- Animated enemy where change detection must ignore sprite motion.

### Live gates

1. Hover ten cards and ten enemies with zero clicks, drags, or selected-card movement caused by physical button state.
2. Every inspection produces a full frame and at least one valid crop or an explicit crop-localization failure.
3. Known tooltip text is transcribed correctly on at least 95% of readable recorded cases.
4. With multiple enemies, identity is attributed to the hovered enemy with 100% precision; uncertain cases remain unresolved.
5. Discovery terminates within its configured budget and hands a fresh scene revision to ACT.

## Recommended implementation order

1. Unknown inventory, deduplication, and player exclusion.
2. Explicit discovery state machine and released-button hover contract.
3. Before/after capture plus artifact storage.
4. Object, context, and difference crops.
5. OCR-first extraction and typed parsers.
6. Crop-focused LLM1 fallback.
7. Evidence-bound identity persistence.
8. Queue scheduling, bounded retries, and fresh ACT handoff.
9. Recorded-frame benchmark followed by bounded live validation.

## Definition of done

- Every decision-relevant unknown is explicitly inspected or explicitly exhausted before unknown-first ACT.
- Discovery uses hover only; it never clicks or drags.
- Each hover is followed by a fresh full capture and targeted crop generation.
- OCR operates on the targeted crops and retains word-level evidence.
- Names and card details bind only to the object that was hovered.
- Player, HP anchors, and duplicate detections never enter the enemy queue.
- Explorer LLM retired; harness loop owns discovery. ACT alone chooses gameplay actions.
- All retries are bounded, auditable, and safe.
