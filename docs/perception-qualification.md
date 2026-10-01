# Perception qualification (Gates E and F)

Status: **Gate E retired 2026-09-30.** It is replaced by the teachability gate and the "one armed combat turn" gate in [architecture-review.md](architecture-review.md). Its last verdict was E FAIL, F BLOCKED_BY_E; the default tracker stays `none`. This file is kept as the historical record of the gate and its failures.
Last measured: 2026-09-24. Replaces the fusion, Gate E repair, lean-perception, spatial-tracker, play and build plans; their history is in git.

Vision: [north star](perception-reaction-orchestration-north-star.md). Current visual stack: [lean-visual-stack.md](lean-visual-stack.md). Invariants: [`system.md`](../system.md), ADR 0001.

## Frozen contract

Evaluation data lives in ignored `.tmp/fusion_eval/` (`annotations.json`, `matching_rules.json`, `e_verdict.json`). Annotations are drawn independently of model output and are never edited to fit detections.

Matching: small controls by center-hit; actors and cards by IoU ≥ 0.5; one-to-one greedy assignment. Oversized = area > 0.25 of the screen or a hand-wide box offered as one card.

**Gate E (offline, frozen frames)** passes when, on reviewed fixtures:

- every expected actionable control has a current, uniquely bound proposal;
- every visible card is its own proposal; player and enemy each have a distinct box with IoU ≥ 0.5;
- zero decorative, oversized, stale or duplicate offers, and no ID collision;
- control-ID retention ≥ 0.95 over a 30-frame animated sequence;
- end-to-end perception p95 ≤ 1,000 ms with Qwen resident and no OOM;
- coverage: ≥ 6 named scenario categories (combat, hover, menu, reward, map, replacement) and ≥ 100 warm timing samples.

Jaccard or box counts never pass the gate alone.

**Gate F (live shadow)**: the same metrics hold on a fresh live workspace. Shadow mode sends zero native input, black transitions don't terminate before the 60 s timeout, and nothing acts from old geometry.

## Where it stands (fused-gpa arm)

| Requirement | Result | State |
|---|---|---|
| Latency, Qwen resident | warm p50 364 ms, p95 424 ms, n=100 (Q0, 2026-09-24) | met, but `e_verdict.json` was never regenerated |
| VRAM | ~3.0 GB used on 8 GB with GPA, OCR and Qwen | met |
| Cards, ordinary fan | 5/5, 0 false | met |
| Cards, hover frame | 3/5, 1 false offer, hovered card withheld | **fail** |
| Cards, reduced hand | 1/3 | **fail** |
| Actors | 0/6; fused-gpa has no world detector, and DINO centres land inside but box IoU < 0.5 | **fail** |
| Settings gear | detected but withheld (unlabelled icon) | **fail** |
| Scenario coverage | 3 annotated combat frames; 30 held-out frames unlabelled | **absent** |
| 30-frame identity sequence | not run | **absent** |

The card misses come from `card_proposals.propose_hand_cards`. It only splits inside one GPA "hand" box and gives every card that box's vertical band. A hovered card rises well above the band, so its box can't reach IoU 0.5 and its title falls outside the band. When GPA's hand box is missing or coarse, nothing is split at all. Two routing repairs (expanding rows, adding a lower band) made false offers worse and were rejected.

## Next

No further Gate E repairs. The next step is the Phase 0 measurement spike in [architecture-review.md §9](architecture-review.md). Nothing in this section authorizes input.
