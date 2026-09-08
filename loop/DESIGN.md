# Collect-loop design

> **Chapel-era design — not this branch's driver.** Live collect path: [`system.md`](../system.md) (`sts2`, turn-based). This file is a frozen appendix for Elden pad/explore/Ratchet.

Screenshot in, OS device out. The VLM is a slow planner and skill/GSM developer. Anything faster than a VLM call is a GSM. Artifacts commit only when an independent checker sees the claimed delta. Weights may update (SFT/RL) sequentially on the 4060. Heuristics that rewrite the model’s chord after it speaks are not a write path.

Keep: no game API, no teleports, no `eval()`, frozen GSM *kinds* per fixture, Ratchet cap/retire/Bernoulli `p_on−p_off`, F12 panic, unlabeled device *names*.

## Pitch

This project studies whether a vision-language model can improve at a realtime game through verified experience. Naive NL skills without a state-delta check match the SkillsBench self-authored number (~0). The librarian question Ratchet left open outside code still holds — but only if a skill or GSM actually fires the pad and an independent checker scores the claim.

Pipeline: labeled-HUD perception → collect (VLM chord **or** `invoke_gsm explore` **or** a verified skill chord) → outcome contract → curate commit-on-pass → optional sequential SFT/RL.

Comparisons (not dependencies): Ratchet ([arXiv:2605.22148](https://arxiv.org/abs/2605.22148)), SkillsBench ([arXiv:2602.12670](https://arxiv.org/abs/2602.12670)), BALROG ([arXiv:2411.13543](https://arxiv.org/abs/2411.13543)), ORAK ([arXiv:2506.03610](https://arxiv.org/abs/2506.03610)), GameSense.

## Fixture protocol

Per-game plugin only. Agent, logger, memory, and OS executor do not name the title.

- `id`, `window_title`, `device` (`pad` | `keyboard` | `mouse`)
- `action_table` — unlabeled device names (`a`, `b`, `stick_l_n`, …), never game verbs
- `start_snapshot` — gitignored save taken once after boot/naming
- `scorer(image) -> metrics` — pixel-only; `None` fields when HUD is absent
- `terminal(metrics, image) -> alive|fail|win|timeout`
- `success(steps, terminal) -> bool` — chapel is not “survived K”
- `after_fail()` — in-game continue; snapshot restore only for softlock/eval
- `execute_chord(action)` — simultaneous stick + held + tapped

Driver fixture: Elden Ring (`elden`), pad, chapel start. `fake` is pytest (`good_action` / `bad_action`, survive-K). Tetris GSM remains the cheap RL lab.

## Chapel success

A life is success if `terminal != fail` **and** at least one of:

- interact prompt cleared: a **face tap** with CV-backed `prompt_cleared`, **and** two later steps still have the bar off. Walking out of range is not a win. A tap that follows a look/walk on the same still-up glyph is a lock probe, not Open/Talk.
- `rune_delta > 0` on a **visible HUD** (health/stamina present), and the new count holds for two later HUD steps. A lone counter flicker with hud off is not a win.
- **view coverage**: ≥3 look-only `stick_r` steps with `view_changed`, plus at least one walk that changed pixels.

`terminal == fail` is always failure, even if runes went up. DINO-only flicker is not a prompt clear. Timeout in a wall with one DINO hint and no yaw is **failure**. Ratchet contribution uses this predicate.

## Outcome contract

Each `StepResult` records:

- `view_changed` — look: center-crop MAD (HUD/prompt excluded) **and** column-shift sign matching `stick_r`
- `prompt_cleared` — same `detect_interact` path on before and after (not CV-only after)
- `stall` — pure `stick_l`, not `view_changed`, not `pixel_changed` (no MAD < 0.015 gate)
- `executed_as_proposed` plus log **`proposed` and `executed` (`action`) separately**
- `invoked_gsm` / `gsm_ticks` when the explore motor ran

Each `StepLog` also records `prompt_source` / `prompt_source_after` / `prompt_text`. Chapel success only counts `prompt_cleared` when `prompt_source` includes `cv`, the clearing chord includes a face tap, two later steps still have the bar off, and that tap was not a lock probe after a look/walk on the same glyph. SFT export still requires CV-backed `prompt_cleared`.

Calibrate τ on historical look-only frames (`python -m loop calibrate-view`). Prefer `20260825T050750Z` / `055445Z`. Do not SFT on look→walk contaminated runs. Drop fail-episode rows and DINO-only prompt clears at export.

## Dispatch

```text
screenshot → sensors (HUD, prompt, MAD, DINO hint)
  → OCR prompt inset (optional pytesseract) + lock/stuck check
  → prompt glyph interrupt (CV bar, or labeled DINO face after 2 consecutive frames; never default-A)
     skipped when prompt copy is a condition (need item / only those who …) or the same face bar is still up
     (look/walk while that bar is up does not reset the stuck glyph; leaving the volume does)
  → skill_on: execute the skill chord or invoke_gsm (not prompt-only)
  → explore GSM if busy or stall (keep until yield; stall=last_stall so walk-north can run).
     Lock-yield cancels explore so the VLM sees journal + prompt text.
  → else VLM chord (empty JSON fills look/back, never a DINO-only A tap; bind thought-named glyphs **only while the bar is visible**; no look→walk, no patch-walk)
  → execute as proposed
  → outcome → log
```

Sensor **journal** (not dump_all cosine): keyed facts `kind|heading|prompt` written from CV/OCR/DINO/outcome, never from `thought`. A compact block (≤10 lines) is always in the user prompt. Bindings are `(glyph, prompt copy, device) → lock|cleared|noop|jump`. Ratchet may `{"tool": "recall", "query": "doorway"}` for tag lookup; still emit a chord. `remember` stays dump_all only.

DINO/bright patches are overlay + prompt **hints**. They never `set stick_l` after parse. Generic inset labels (`interact prompt`) do not skip the VLM. Face-tap interrupts are for **resolved** interact (Open/Talk/Pickup), not persistent lock copy. Interrupt does not reset walk stall. Interrupt cancels a busy explore GSM. Lock-yield also cancels explore. `skill_on` still beats stall GSM (Ratchet Q3).

World DINO keeps `MAX_BOXES=2` with `_KIND_RANK` (doorway before trail) while approaching. At arrival (close doorway/arch), trail ranks first so the overlay can show the item path. After a lock-yield, doorway/arch kinds are **demoted in hint rank only** for the rest of the episode; their overlay copy stays “condition, not Open / not stick_l into it,” even when the bar is currently off. The VLM still chooses the chord. A sensor lock line stays in the user prompt after that yield (not a thought memory). `format_grounded` lists every kept box.

Explore GSM (frozen kind `explore` on Elden): no VLM per tick while busy. Stall → yaw e, yaw w, back (`ESCAPE_CYCLE`). Else short walk north, periodic yaw. Yield idle on a **resolved** prompt or fail. A lock-yield prompt cancels the motor so the VLM can look or walk a different heading. Sequential VRAM: PLAN → DETECT → TRAIN, never co-resident on 8 GB.

Thought-named face bind does not steal look/walk: a dark bar, lock-yield, look-intent, and failed/past-tense face narrative leave the VLM chord unchanged. Chapel success needs a face tap plus two later steps with the bar still off, and rejects lock-probe taps after a look/walk on the same glyph. Rune wins need a visible HUD and two later steps holding the higher count.

## Measurable arms

Each session declares one `arm`. Each **episode** in `ratchet` rolls Bernoulli(`skill_off_frac`, default 0.5).

- **no_skill** — no tools, no skills, no dump_all sqlite. Writes 0. Sensor journal (CV/OCR facts) still appears in the prompt.
- **dump_all** — sqlite `MemoryStore`, k=3 cosine. Ablation. Not a live learning arm.
- **ratchet** — typed bank + journal `recall`. When `skill_on`, the verified chord **fires the pad**. Writes only at curate (synth from ≥3 same-signature fails, commit-on-pass).

`SessionSummary` / `EpisodeLog` / `StepLog` log `arm`, `skill_on`, `active_skill_id`, `success`. Analysis is matched `p̂_on − p̂_off`: `python -m loop analyze --fixture elden`.

## Librarian

Typed skill: `id`, `recognize`, `avoid_or_do` (chord JSON or `invoke_gsm`, pitfall without game verbs), `end`, contribution stats.

- **n_trials = episodes** that scheduled that skill on.
- **contribution** = (successes − failures) / n_trials, success from fixture `success()`.
- Retire DEPRECATED when `n_trials ≥ N_min` AND `contribution ≤ −τ`. Only in curate. `N_min=20`, τ=0.10. Cap **C=8**.
- Birth signature: `(stall_frac, look_frac, prompt_cleared, terminal)` — not last-3 rewritten names.
- Look skill commits only if `view_changed` on a look-only step whose `stick_r` matches the skill chord. Tap skill only if a **CV-backed** `prompt_cleared` or prompt stayed off.
- Stall-without-look synth is `stick_r`, not walk north.

## Post-training (sequential)

Export high-score rows: `python -m loop export-sft --fixture elden`. Filter `executed_as_proposed` and (`view_changed` | CV-backed `prompt_cleared`). Drop rows from episodes with `terminal == fail`. Then `python -m loop train-sft --fixture elden` unloads Ollama, sandboxes the dataset, and writes Unsloth chat JSONL. Pass `--execute` for QLoRA on the 4060 (4-bit, vision encoder frozen). RL explore GSM: `python -m loop train-rl --fixture elden` sandboxes `explore_reward` then trains DoubleDQN on logged explore pairs (no live pad). Orak: high-score traces beat mixed/low-score.

STS2 (turn) is a different write path. Collect planner is `qwen3.5:4b-instruct-q4_K_M`. SFT/DPO use `unsloth/Qwen3.5-4B` (instruct VL), never `Qwen3.5-4B-Base` and never Qwen3-VL. Each `StepLog` stores `prompt_id` (hash of the frozen system prior); export keeps one hash. `export_sft_rows` for sts2 requires `turn_reward > 0` (screen progress, energy/block/HP after a card+target pair, labeled Continue/Resume/Got it). Pixel flicker and Escape/Give Up are not trained. Chat JSONL uses `SYSTEM_TURN_NO_SKILL` and mark / gx,gy / target. `python -m loop export-dpo --fixture sts2` writes same-frame (or same-screen ±3) chosen/rejected pairs. Do **not** point `train-rl` at STS2.

GRPO/GSPO is not live on Steam. One window cannot execute `num_generations >= 2` chords without destroying the frame, and there is no STS2 snapshot reset. Valid later: frozen-frame groups scored by `turn_reward` without executing (format + labeled-mark verifier), or online single-sample REINFORCE after a real snapshot exists. If Unsloth GRPO is used, `importance_sampling_level="sequence"`, `loss_type="dr_grpo"`, `fast_inference=False`. Eval: tutorial-clear, title→map, combats started, mean floor, false-fail, two-click rate, Escape rate, on a frozen `prompt_id`. `Sts2Fixture.success` counts leaving the title into combat / floor-up, not only `terminal == win`.

## Commands

```text
python -m loop perceive --fixture elden
python -m loop collect --fixture elden --arm ratchet --lives 10 --skill-off-frac 0.5
python -m loop curate --preview
python -m loop calibrate-view
python -m loop export-sft --fixture elden
python -m loop export-dpo --fixture sts2
python -m loop train-sft --fixture elden
python -m loop train-rl --fixture elden
python scripts/run_cloud_librarian.py
python -m loop analyze --fixture elden
python -m loop ui
```

F12 panic: `release_all` + exit. `--eval-snapshot` copies `start.sl2` over `AVATAR_ELDEN_SAVE` after fail.
