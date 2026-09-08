# STS2 Collector — Running Report

Updated: 2026-09-02T20:58:24.204Z  
Source: latest 15 `runs/*_sts2_collect` directories. Regenerate: `node scripts/update_running_report.mjs`.
Root-cause audit: [`writeups/harness-audit.md`](harness-audit.md).

## Current

- Pipeline: two_stage, overlay; prompt ID: 4fba72e96df9268d, bb77d64cf5022f7f, d9bada8199a0505d.
- Window: 149 turns; score sum 5.65; positive-reward turns 21/149 (14%).
- Terminal records: 9/15; timeout=8, fail=1.

## Vision + reason

- LLM1 raw: 63; parseable log records 27/63; empty-control scenes 3.
- Host/LLM1 comparable screens: 19; conflicts 7. Reason JSON parseable: 83/83.

## Recent runs

- 20260902T064653Z_sts2_collect: 18 turns; score 0.65; progress 2; no-op 11; screen? 2; timeout; success=false; SFT 1.
- 20260902T073250Z_sts2_collect: 0 turns; score 0.00; progress 0; no-op 0; screen? 0; incomplete; SFT 0.
- 20260902T073322Z_sts2_collect: 27 turns; score 0.00; progress 0; no-op 24; screen? 0; timeout; success=false; SFT 0.
- 20260902T074034Z_sts2_collect: 25 turns; score 0.65; progress 2; no-op 17; screen? 2; timeout; success=false; SFT 0.
- 20260902T074439Z_sts2_collect: 1 turns; score 0.00; progress 0; no-op 1; screen? 0; incomplete; SFT 0.
- 20260902T074532Z_sts2_collect: 15 turns; score 0.70; progress 2; no-op 7; screen? 2; timeout; success=true; SFT 0.
- 20260902T074758Z_sts2_collect: 0 turns; score 0.00; progress 0; no-op 0; screen? 0; incomplete; SFT 0.
- 20260902T075004Z_sts2_collect: 9 turns; score 1.10; progress 4; no-op 1; screen? 5; timeout; success=true; SFT 3.
- 20260902T075216Z_sts2_collect: 9 turns; score 0.30; progress 1; no-op 2; screen? 8; timeout; success=true; SFT 1.
- 20260902T085028Z_sts2_collect: 8 turns; score 0.65; progress 2; no-op 3; screen? 1; timeout; success=false; SFT 2.
- 20260902T175753Z_sts2_collect: 15 turns; score 1.30; progress 4; no-op 6; screen? 2; timeout; success=true; SFT 2.
- 20260902T180905Z_sts2_collect: 11 turns; score 0.65; progress 2; no-op 3; screen? 4; incomplete; SFT 0.
- 20260902T181819Z_sts2_collect: 1 turns; score 0.00; progress 0; no-op 0; screen? 1; incomplete; SFT 0.
- 20260902T190928Z_sts2_collect: 0 turns; score 0.00; progress 0; no-op 0; screen? 0; incomplete; SFT 0.
- 20260902T191059Z_sts2_collect: 10 turns; score -0.35; progress 2; no-op 3; screen? 6; fail; success=false; SFT 1.

## Current issues

- 6/15 newest runs incomplete; do not use for policy comparison.
- HUD screen unknown 33/149 (22%); reward/state attribution weak.
- No-op rate 78/149 (52%); inspect aim/bind/stale gates.
- Only 10 SFT traces for 149 turns; data too thin for training claim.
- LLM1 raw capture unparseable 36/63; recorded text ends mid-JSON, blocking post-hoc scene audit.
- Host/LLM1 screen conflict 7/19; host wins, but reasoner sees unstable scene labels.
- Window mixes 3 prompt IDs; aggregate trend is directional only, not a controlled comparison.
- P0: card→target chord records second coordinates, but STS2 OS executor sends primary click only.
- P0: unknown target suppresses auto-inspect; label-only inspect mapping cannot bind entity ID to bbox mark.
- P0: invalid LLM1 schema skips LLM2 instead of sending canonical fallback scene.

## Read rules

- `success=true` with `terminal=timeout` means configured progress threshold, not full run clear.
- Compare only completed runs with same architecture, prompt ID, fixture, and starting condition.
- Report tracks harness evidence; it does not prove model quality without held-out paired eval.

