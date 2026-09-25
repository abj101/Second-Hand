# Spatial trackers: experiment and perception integration

Status: plan only. Branch `feat/spatial-object-tracking` holds the current uncommitted perception work. This document does not install models or run Mario.

## Objective

Give every visible object one id that survives motion and pixel animation. Attach OCR text and a semantic class to that id. Call semantics once when the id is new, and again only when it returns after being lost. Compare two local trackers on the Infinite Mario benchmark against the current motion sensor.

`loop/adaptive/` stays application-agnostic. Mario prompts, seeds, and reports live under `benchmarks/infinite_mario/`. Do not commit frames or sprite crops. Upstream artwork is Nintendo IP.

## Why this shape

Today two sensors disagree:

- `SceneAssembler` assigns `registry_id` from the OCR string (`_assign_ocr_registry`). A title whose letters change becomes a new offer. `control_identity_stability` and `eligible_surface_stability` measure those ids.
- `MotionPerception.qualify` (`loop/adaptive/snapshot.py`) builds one entity from `detect_regions` plus a single `motion_bbox`. Mario's `MarioPerception` only renames that one id to `player_track_id`. A scrolling level fuses the player, tiles, and enemies into one box.

The track id has to be the registry id. The spelling and the semantic class are attributes of it.

## Frozen contract

```text
TrackHit
  track_id: str          # stable while the object is visible
  bbox: (x0, y0, x1, y1) # normalized to the frame
  appearance: bytes      # small crop hash or embedding, for return matching
  detector_label: str    # model prompt hit, may be empty
  confidence: float
  state: "new" | "continuing" | "returned"
```

- `ObjectTracker.update(image) -> tuple[TrackHit, ...]`. No game names, no clicks, no schema writes.
- One id per object per frame. Two hits may not share an id.
- `continuing` means the id was present on the previous frame.
- `returned` means the id was absent, then matched by appearance within a fixed window (start at 30 frames).
- `new` means neither. Semantics run on `new` and `returned` only.
- OCR stays a label. If a token overlaps exactly one track above IoU 0.3, write it onto that track. It does not create a second registry id. Unmatched text stays diagnostic evidence.
- Offers use track ids. Area cap, forbidden ids, and semantic mode stay as they are. A track with no semantic class yet is withheld when semantic mode is `required`, and provisional when it is `off`.
- A real screen replacement clears live tracks and pending semantic calls. Appearance memory may still mark a later hit `returned`. It may not invent a box that is not in the current frame.
- Missing weights or a failed load yield zero hits and a visible `unavailable` status. The loop keeps running on the current sensor. Do not pretend the tracker ran.

## Where it sits in the loop

`SceneAssembler.update` is the only production call site.

1. Tracker proposes hits.
2. Existing OCR runs unchanged.
3. Bind each OCR token to at most one hit.
4. Registry id on the track node is `track_id`, `registry_owner` is `track`.
5. `SemanticConfirmer.observe` receives only hits whose `state` is `new` or `returned`, and only when semantic mode is on.
6. `offer_decisions` / `registry_offers` read those track nodes.
7. `MotionPerception.qualify` and therefore `MarioPerception` consume the same hits. Delete the "first motion box wins" path once a tracker is selected. Until then it remains the baseline arm.

`loop/adaptive/cli.py` perception and shadow stages grow one flag: `--tracker none|yolo-world|sam2`. Default `none` is today's behavior.

## The two arms

Both are optional extras, imported only when the flag is set. Core `pytest -q` must not download weights.

### Arm A — YOLO-World + BoT-SORT

- Detector: Ultralytics YOLO-World, small variant. Prompts are an argument, not hardcoded in `loop/`.
- Association: BoT-SORT (appearance + motion). ByteTrack is the fallback if the appearance head will not load. Record which one actually ran.
- `detector_label` is the prompt that fired. Empty when confidence is below a named threshold.
- Mario prompts live in `benchmarks/infinite_mario/track_prompts.json` and are passed in by the benchmark runner. Suggested prompts for the experiment only: `character`, `enemy`, `block`, `coin`, `pipe`. The generic loop default is an empty prompt list, which means this arm returns no hits rather than guessing a game.

### Arm B — SAM 2

- SAM 2 does not discover objects. A proposer must hand it the first box.
- Proposer for this experiment: Arm A's boxes, with the tracker disabled, so both arms see the same proposals and the comparison is association, not "who found Mario."
- SAM 2 then propagates masks. The hit bbox is the mask's axis-aligned box. `appearance` is the mask crop.
- Use the small SAM 2 checkpoint. If CUDA is absent, mark the arm unavailable. Do not run the large checkpoint on CPU and call that a result.
- `detector_label` is copied from the proposer on `new`, then kept on that id.

## Mario measurement

Harness: `benchmarks/infinite_mario/track_experiment.py`, marker `benchmark`. It drives `MarioEnvironment` for train seeds 1–5, 200 frames each, no native input. Three arms on the same frames: `none` (current `MotionPerception`), `yolo-world`, `sam2`.

Report one JSON file per arm. Do not save frames.

| Metric | Pass for the winner |
|---|---|
| Player id lifetime | One id covers the player whenever it is visible, measured by overlap with the benchmark's own player box if the adapter exposes it, otherwise by the single largest moving non-tile hit |
| ID switches per 200 frames | Lowest, and below the `none` arm |
| Semantic calls | Equal to `new` + `returned` counts, never once per frame |
| Track set stability | Jaccard of track ids, whole run and after 3 warmup frames |
| Latency | Median and p95 ms per frame, model load separated |
| Tile explosion | Count of new ids whose box is a repeating background cell. Lower is better. A scrolling level will try to track every tile |

The winner is the arm with fewer ID switches than `none` and a median frame time under 1000 ms on the machine that runs it. If both lose to `none`, keep the default at `none` and write that in the report. Do not lower the bar.

`AVATAR_ALLOW_TEST_SEEDS` stays required for seeds 101–103. This experiment uses train seeds only.

## Build order

1. Protocol, fake tracker, and OCR-to-track binding tests. No weights. Registry id comes from the fake track. Two OCR strings on one moving box stay one id.
2. `--tracker none` path unchanged. Existing perception tests stay green.
3. Arm A adapter and a unit test with a stubbed detector that emits scripted boxes, plus one marked `gpu` test that loads real weights when present.
4. Arm B adapter with the same stubbed proposer. Real SAM 2 is a `gpu` test.
5. Point `MotionPerception` at the tracker when one is configured. Mario runner gains `--tracker`.
6. Run the 200-frame comparison. Commit the JSON summaries, not images.

## Out of scope

- Armed clicks, Qwen reaction, and cloud semantic confirmation as the tracker.
- Training or fine-tuning on Mario sprites.
- Replacing RapidOCR. It remains the label reader.
- Making either arm the default before the Mario report exists.
