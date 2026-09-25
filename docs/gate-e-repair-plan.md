# Gate E perception repair pass

Status: repair pass executed, 2026-09-22; Gate E still fails. The frozen qualification contract in
[`visual-object-fusion-execution-plan.md`](visual-object-fusion-execution-plan.md)
remains authoritative. This pass adds diagnostics; it does not silently lower
that gate or switch the default tracker.

## Order of work

1. Fix the region-feature indexing failure seen with a fused tracker and leave
   one focused regression check. Profile OCR on saved frames, then retain only
   a measured, semantics-preserving improvement.
2. Diagnose the missing card proposals on the five-card hover and three-card
   saved frames. Repair the smallest shared cause in the game-agnostic splitter
   or its proposal input. Withhold ambiguous click targets.
3. Replay the frozen annotated frames through the existing arms. Report the
   unchanged Gate E verdict alongside separate safe-point localization,
   full-box matching, offer eligibility, identity sample count, and timing.
4. Compare a newer UI or visual grounding model only after the current path is
   measured on the same frames. A model must improve the actual missed-object
   or false-offer cases within the 8 GB and latency limits before integration.
5. Annotate distinct menu, reward, map, animation, and replacement surfaces
   when genuine saved frames exist; then run a 30-frame sequence with Qwen
   loaded through the normal reaction path. Live shadow F follows only after E.

The next model challenger is OmniParser's July 2026 interactive-region
detector, compared against GPA on the same missing-card and control frames.
Qwen3-VL is an offline semantic/point oracle candidate, not a new hot-path
dependency. The OmniParser detector weight was downloaded to an ignored local
cache for a bounded offline trial; Qwen3-VL has not been downloaded.

## Decision rules

- A safe point is useful for click targeting, but it does not replace the
  frozen actor/card IoU threshold. Report both until the product contract is
  explicitly revised.
- A detected object, an eligible provisional offer, and a verified action are
  separate outcomes. Do not count an unknown icon as a safe control merely to
  satisfy recall.
- Keep `none` as the general default while E fails. Do not infer identity
  stability from two comparisons or estimate p95 from a single frame.
- Model downloads and live interaction are not part of unit tests. No saved
  game frame goes to an external service during this repair pass.

## Existing evidence

The saved E report covers three combat frames. On those frames the fused path
proposed 5, 3, then 0 cards; control-ID retention used two comparisons. A
later manual Qwen-resident profile reports perception p50 1,546.7 ms, p95
3,016.6 ms, OCR p50 1,014.1 ms, and peak allocated VRAM 3,735.4 MiB. That
profile does not yet exercise the normal reaction policy loader. These facts
come from ignored `.tmp/fusion_eval/` reports and need fresh replay after the
repairs above.

The original offline evaluator supplied precomputed OCR tokens to
`SceneAssembler`, which bypasses the callable OCR and region cache used by
the live path. The repair pass will measure the actual callable path before
using replay latency as live evidence. This may expose a larger OCR tail:
one saved hover frame had 29 crop reads taking 29,460 ms versus one full-frame
read taking 2,292.9 ms, but the full-frame read found eight fewer raw tokens.

## Execution result

- Fixed previous-feature alignment when a tracker is active. A stored track
  feature list is no longer indexed as if it belonged to freshly detected
  previous-frame regions.
- For experimental visual-ownership trackers, replaced per-region OCR caching
  with one full-frame OCR call per frame. On the saved hover and three-card
  frames, the old crop path made 28 and 25 calls yet delivered only two
  unrelated numeric tokens to fusion. The full-frame path restores card-title
  evidence. The legacy tracker retains its region cache.
- Selected the installed RapidOCR Small model only for the experimental fused
  live path; the legacy path retains Medium. A same-process three-frame A/B
  found identical card matches, offer IDs, false offers, and relevant title
  labels. Both models read the hovered `Bash` as `Bas`.
- Rejected an OCR-driven hand-box expansion after the combined replay showed
  extra false offers. The card splitter remains conservative, with a focused
  regression for partial narrow crops.
- Replayed Gate E through the actual callable OCR selector and added separate
  localization, offer-eligibility, OCR-call, identity-denominator, and
  cold/warm timing diagnostics. The manual Qwen-resident profile remains
  supplemental; it does not satisfy same-path policy coresidency.
- Tested Microsoft's July 2026 OmniParser interactive-region detector with
  the official PR #37 weight and published inference wrapper on the same
  three local frames. This TorchVision build lacks CUDA NMS, so official GPU
  postprocessing stopped before boxes. The official CPU fallback completed at
  1.195/1.025/1.039 seconds per frame after a 0.987-second load. It returned
  19/18/19 regions and covered 2/7, 1/6, and 1/4 annotated offerable objects
  under a 25%-area-overlap screen; all covered objects were controls, with no
  card body covered. These overlap counts are not the frozen IoU metric. The
  detector does not address the missing-card cases and is not integrated.
- Loaded Qwen through the normal shadow-policy worker with local weights in
  `.venv-decider` (`HF_HUB_OFFLINE=1`): load 11.946 seconds, 1,656.1 MiB CUDA
  allocated and 1,740.0 MiB reserved, with no OOM. A valid same-process GPA
  run stopped at `ModuleNotFoundError: No module named 'ultralytics'` in that
  environment; the optional `track` dependency from `pyproject.toml` is not
  installed there. An OCR-only assembler pass is not detector coresidency and
  does not satisfy Gate E.

The final saved report is `.tmp/fusion_eval/offline_qualification.json`; the
frozen verdict is `.tmp/fusion_eval/e_verdict.json`. Fused-GPA used Small OCR:
one cold frame took 3,255 ms, while its two warm frames had p50/p95 731 ms.
Three frames cannot establish a production p95. The ordinary, hover, and
three-card frames produced 5/3/0 card proposals, 5/3/1 matched card boxes,
and 0/1/0 false offers. Actor full-box matches remain 0/2 on each frame;
raw DINO centers were inside both annotated actor regions on all three.
Fused and fused-base warm p95 were 1,698 and 1,295 ms, respectively.

The next gate work requires distinct annotated menu/reward/map/animation/
replacement frames, a 30-frame identity sequence, and a same-process Qwen
policy-plus-detector run after the optional tracker dependency is installed
in the policy environment. Card misses and the hovered false offer need better
current-frame evidence. OmniParser did not supply it on these frames; any
further challenger must pass a side-by-side offline screen before integration.
Actor extent and safe-point targeting are reported separately, and the frozen
IoU gate remains unchanged.

Final focused validation: 62 perception, evaluator, capture, and offer tests
passed. A separate shadow CLI test failed reproducibly because its two-step
run ended with `QwenReactionPolicy_pending`; it uses the default tracker, so
this repair pass does not claim to have resolved that test.
