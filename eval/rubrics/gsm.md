# Human scoring — GSM experiment (Phase 2)

Tag every row with `harness_level`, `fixture`, `paused`, `gsm` (`off` | `rule` | `rl`), `state_source` (`extracted` | `pixels` | `n/a`), `opt_iter` (0–3).

## Arms (change one at a time)

| Arm | What runs |
|---|---|
| Pause-and-plan | Existing paused Tetris L1/L2 |
| L2 realtime | `run_tetris_realtime.py` — VLM every decision, gravity on |
| Rule GSM | Detector + policy, no VLM tick while invoked |
| RL GSM | DoubleDQN policy, no VLM tick while invoked |

n=20 per cell when collecting the public table.

## Verdicts

- `success`: micro-task completed (piece locked in a legal column; survived N pieces; cleared ≥1 line — pick one and freeze it in the run tag)
- `partial`: some control, task not met (piece moved but topped out)
- `failure`: no useful control, focus-lost, or GSM never returned
- `stuck`: GSM or planner repeated a failing action / hit timeout

## Extra columns (required)

- `vlm_calls_per_episode` — must drop on GSM arms vs L2 realtime
- `gsm_ticks` / `gsm_author_ms` / `gsm_author_usd`
- `detector_family` — `grounding-dino` required to claim `state_source=extracted`; `pixel-blob` stays `pixels`
- `sandbox` pass/fail if this row used a rewritten spec
- `duplicate_spawn` 1 if a new module was created when retrieve should have merged

Win rate / lines / max-level are descriptive, not gates. Do not import Wukong 60% as a Tetris target.

## Mixed pacing (2048 / StS / Platinum)

StS / Platinum expect `gsm=off` and invoke count 0. 2048 `choose_swipe` is allowed when the run tag says `gsm=rule`. A GSM invoke on StS/Platinum is a harness bug unless the run tag says otherwise.
