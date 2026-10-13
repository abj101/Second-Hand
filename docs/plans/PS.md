# PS: Mark the player for the model, and split describing from deciding

Status: in progress (S0 baseline, S2 and E complete; STOP 6a awaiting live-spend approval; PS 40/160 requests, 2026-10-13) · Branch: feat/lean-agent · Program: [program-r1-wm.md](program-r1-wm.md) row 11 · Follows: [TL.md](TL.md) §7 STOP 5

## 0. Why this phase exists (evidence, 2026-10-13, 54 diagnostic requests)
- **The L2 stall started with perception.** The model cannot pick out the player when the sprite faces away. In L2 it labelled the TV, then Mom, as `player`, and the real player as `monster`/`rival`/`NPC`. From step 37 onward the notebook's only goal was "go talk to the character in the centre", i.e. the player. 87 identical choices followed.
- **The offline A/B data is in `.tmp/perc_ab/`** (`run.py`, `marker.py`, `split.py` plus `*.json`; not committed). It uses frames 37 (2F, facing away), 72 (2F, facing the camera) and 78 (1F, facing away, Mom present), with self spot (0.45, 0.444).
  - Correct `player` label, 2 tries per cell:

    | Condition | 37 | 72 | 78 |
    |---|---|---|---|
    | As run | 0/2 | 2/2 | 0/2 |
    | Memory lines removed | 0/2 | 2/2 | 0/2 |
    | Memory removed + text hint with the coordinates | 2/2 | 2/2 | 0/2 |
    | **Red box + caption** | **2/2** | n/a | **2/2** |
    | Red box + caption + memory lines | 2/2 | n/a | 2/2 |
    | Red box, no caption | 2/2 | n/a | 0/2 ("masked figure") |

    (Run 72 was correct without help, so it was not re-run with the box.)
  - Sensible action, 1 try per cell (`split.py`). A = today's options-then-pick; B = describe only, then the decision call reasons with `go_to`/`buttons` only:

    | | 37 | 72 | 78 | Total |
    |---|---|---|---|---|
    | A, no box | ✗ self | – computer | ✗ self | 0/3 |
    | B, no box | ✗ door in wall | ✓ stairs | ✗ back upstairs | 1/3 |
    | A, box | ✓ stairs | – cabinet | ✓ Mom | 2/3 |
    | B, box | ✓ stairs | ~ opening above stairs | ~ Mom (name not resolvable) | 3/3 by intent |

  - The `{"thing": n, "verb"}` reply format this packet introduces was **not** in the A/B. E measures it.
- **Why this design.**
  - The system already measures who the player is (TL: 99/99), so it should *tell* the model rather than ask.
  - The perception call should only describe. It currently writes the action list and its option list becomes the decision space, so one mislabel becomes an option like "walk to yourself".
  - The decision call should reason and point at things by number, not by free-text name.
  - Replies at temperature 0.2 are almost deterministic: asking again with the same input changes nothing.

## 1. How this phase works
- Same working rules as TL §0: small slices, each with one quick check; checks report and do not block; only a broken suite or a crash stops the phase.
- **Two stops.** STOP 6a comes after the offline frame eval (the user approves live spend). STOP 6b comes after the live run.
- **Game-neutral.** Everything keys off `World.self_spot`, the perception's `screen` and `observer.cut`, never off game content. Nothing new filters or overrides the model's choice (program §2 rule 4).
  - The system settles *identity* (a measured fact). That is the same category as TL's `fix_player`, not a new gate.
  - Picking the player itself stays blocked only by the existing skill check ("target is the player", skills.py:67).
- **Only model inputs are marked.** Everything else keeps the raw frame:
  - `before`, `after`, `prev_frame`;
  - saved PNGs;
  - `cut`/`scroll`;
  - `observe_after` HUD crops (world.py:259);
  - the miss hash (world.py:139);
  - the trace `frame_hash`.

## 2. Entry criteria
- TL committed (`a8864a5`). The user approves this packet.
- On disk: `runs/m1_long/checkpoint/000070/`, `runs/m1_long/frames/` with `trace.jsonl`/`truth.jsonl`, `runs/m1/` (trace ends at step 94; its frames 95–163 are stale leftovers dated 2026-10-11, so don't use them), the ROM, `.cache/model/`, `.tmp/perc_ab/split.py` (prototype for the eval).
- Ledger before this phase (2026-10-13): total 931/1100, smoke 48/50, M1 883/1050, so **M1 left: 167**. The 54 diagnostic requests: 30 smoke (`run.py`, `marker.py`) and 24 M1 (`split.py`). Program §4's "M1 191 remaining" is stale; correct it there.

## 3. Slices

| Slice | What | Files | Quick check | Requests |
|---|---|---|---|---|
| S1 | System-owned player and the red-box marker | `agent/world.py`, `agent/loop.py`, tests | `pytest tests/agent/test_world.py tests/agent/test_loop.py` | 0 |
| S0 | Frame-eval script plus labelled frames; **baseline = today's prompts + S1 marker** ("A, box") | `scripts/eval_frames.py`, `scripts/frames.json` | baseline table printed | ≤ 26 (hard stop) |
| S2 | Describe-only perception **and** the decision call building the action from a numbered entity (one slice: they cannot be split without double-rewriting fixtures) | `agent/perceive.py`, `agent/act.py`, `agent/loop.py`, `agent/world.py` (tier deletion), tests, goldens | full suite; goldens regenerated and explained | 0 |
| E | Frame eval on the new code: same frames, same seeding as S0 | — | table vs baseline | ≤ 26 (hard stop) |
| **STOP 6a** | The user reads the S0-vs-E table and approves live spend | | | |
| L3a | Live leg 1: resume checkpoint 70 into a copy, 30 steps | `runs/m1_ps` | repeat check (below) | ≤ 60 |
| L3b | Live leg 2: resume from L3a's last checkpoint, the remaining requests | `runs/m1_ps` | progress, then health | ≤ 48 |
| **STOP 6b** | Report | | | |

**Order:** S1 → S0 → S2 → full suite → E → STOP 6a → L3a → L3b → STOP 6b.
- S0 runs after S1 so that S0 and E differ **only** in the describe/decide split. The no-marker baseline already exists: `.tmp/perc_ab/split_results.json` and the L2 trace.

## 4. Slice cards

### S1: system-owned player and the marker
- **`World.fix_player(per, size)`** keeps its name. It has 5 call sites: loop.py:48, 63, 139; check_world.py:363, 365. Its new rule applies when the self spot is locked (`_self_from_motion` and `self_spot`) and `per["screen"] == "world"`. Distances are Chebyshev px, as in world.py:39:
  1. If the model's own `player`-kind entity is within 1.5 tiles of `self_spot`, keep it. Today's tolerance (world.py:40) applies; only its name becomes `player`.
  2. Otherwise, the nearest entity within **`tile_px / 2`** becomes `{"kind": "player", "name": "player"}` (the same threshold as skills.py:67). One tile would catch an NPC standing directly beside the player.
  3. Otherwise, insert `{"kind": "player", "name": "player", "at": self_spot}`.
  4. Every *other* `player`-kind entity becomes kind `character`. If its name is `player`, the name becomes `character`.
- **Return value:** `{"from": old_at | None, "to": new_at}` when anything changed, and `None` when nothing changed.
  - It is **idempotent**: a second call on its own output returns `None`.
  - check_world.py:365 and the per-step re-fix of cached perceptions rely on that.
  - When the self spot is unlocked, today's behaviour applies (the model's label stands).
- **`World.mark(frame, screen_was_world)`** returns `(image, marked)`.
  - It marks only when the self spot is locked **and** `screen_was_world`.
  - It draws a 1-px red rectangle on a **copy** of the raw frame at exactly the tested geometry: `[x-9, y-10, x+9, y+9]` around the self-spot pixel centre (`x, y = self_spot × frame size`).
  - That rectangle sits outside the sprite's columns, which `_box` would not (`_box` gives `[64, 56, 80, 72]` and draws over the sprite).
  - `# ponytail: fixed red outline and offsets from the 2026-10-13 test; pick a contrasting colour per frame if a colour game hides it.`
- **`Loop`:**
  - **`screen_was_world`** for the before frame means the previous perception's screen was `world` **and** `cut(self.prev_frame, before, blank_seen)` is false, so the first frame after a world→battle or menu cut is not marked.
  - For the after frame, `screen_was_world` uses this step's `per` and the skill's cut flag. The next step's before frame is the same raw frame and must reach the same decision, so the perception is not repeated.
  - `_see` and `act` receive the marked image. The **raw** frame hash stays the cache key and the trace `frame_hash`, because `World._arrival` (world.py:567) matches raw hashes.
  - Add `self.cached_marked` and require it to equal this call's `marked` for a cache hit (loop.py:32).
  - When marked, append `CAPTION` to the perceiver context and the flag `the sprite inside the red box is you` to the act flags. `CAPTION` is exactly: `The sprite inside the red box is the player's own character (you); list it with kind "player".`
- **Trace:** add `marked: bool` per step.
- **Checks:**
  - unit test: the model's player within 1.5 tiles is kept, renamed `player`, and the second call returns `None`;
  - unit test: an entity named `NPC` at the spot (< tile/2) becomes player/player, and the old player-kind entity elsewhere becomes `character`;
  - unit test: an NPC one tile to the side is **not** taken; a player is inserted at the spot instead;
  - unit test: the marked image differs from the raw image only on the rectangle outline;
  - unit test: a cut between frames means not marked.

### S0: frame eval (baseline = today's prompts + marker)
- **What gets created.** Promote `.tmp/perc_ab/split.py` into `scripts/eval_frames.py`. It runs the real `Loop._see` → `World.fix_player` → `act` path on saved frames, with an empty notebook and no history.
- **It seeds the world state, the same way for S0 and E:**
  - `world.tile_px = 16`;
  - `world.self_spot` and `world._self_from_motion` from the **previous** trace row's `self_spot`/`self_locked` (m1_long);
  - `loop.prev = {"screen": <previous row's screen>}`.
  - For `runs/m1` frames (no `self_spot` field), inject the truth-derived spot. That is harness-only, stated in the output, never read by `agent/`.
- **The frame set** is `scripts/frames.json`, **at most 13 frames**.
  - Use only frames that have a trace row **and no probe presses**. A probe step saves the post-probe frame while truth is read before the probe: at m1_long 71, truth says 1F but the frame shows 2F.
  - Cover:
    - title, dialogue, the naming keyboard, the preset-name menu and the start menu;
    - 2F with the player facing up and facing down;
    - 1F with Mom;
    - one frame with the player facing sideways mid-room.
- **Each entry** is `{run, step, self: [x, y] | null, ok_verbs: [...], ok_near: [[x, y], ...], why}`, labelled from the PNG plus truth.
  - `ok` = the chosen verb is in `ok_verbs`, and, for world screens, the target is within 0.1 of an `ok_near` point or the buttons move toward one.
- **Each row prints:**
  - screen;
  - who is labelled `player`;
  - `marked`;
  - the chosen action and target point;
  - `self_target` (within 0.08 of the truth self spot);
  - `ok`.
- **Requests:**
  - The cache is bypassed (`Ollama(cache_dir=None)`) and requests are charged through `scripts.run_agent._ledger` with a run name containing `m1`.
  - Every HTTP attempt is charged, including retries and the schema fallback (model.py:46–49), so the `_post` wrapper counts and **raises at 26**.
  - Output goes to `.tmp/ps/baseline.json`.
- **Check:** the table prints. Record the ledger before and after.

### S2: describe-only perception and the decision call builds the action
- **`agent/perceive.py`:**
  - `SYSTEM`, `PROMPT` and `SCHEMA` become the describe-only versions from `split.py`: screen, text, entities (kind, name, at, placement) and exits as `exit: <what>` entities, with no `affordances` section.
  - **`normalize` is untouched.** It already returns `affordances: []` when there are none. That keeps the legacy scripts (`run_goal.py`, `eval_act.py`, `e1_perceive.py`) loading, and they will see empty lists; list them in the Result.
  - Only `tests/agent/test_perceive.py:73` should change.
  - The player line stays. It matters before the lock and for games without a self sprite.
- **`Loop._see`:**
  - Drop the `mem.view("perceiver", ...)` context lines. The A/B showed they add no accuracy and spread stale names.
  - Delete "Previous-place memory is reference only".
  - Move the keyboard/modal sentences to the act prompt.
  - Update the repair suffix (loop.py:33), which still asks for "affordances".
  - `valid` no longer requires an `affordances` list.
  - **Bump `prompt_version`** (loop.py:22), so a restore cannot reuse checkpoint 70's old-prompt perception (loop.py:204–206).
- **`agent/act.py`:**
  - "Things on screen" is numbered (`0: player (player) at [..] ← you`). The "Options:" block is removed.
  - The reply action is one of:
    - `{"thing": n, "verb": approach|interact|select|click}`;
    - a bare `{"verb": advance|cancel|type}`, for dialogue screens with no listed entity (`run_affordance` ignores the target for these, and `type` takes the reply `text`, skills.py:240–244);
    - `{"go_to": name of a remembered thing or known place}` (unchanged);
    - `{"buttons": [...]}` (unchanged).
  - The REPLY `"text"` line becomes "text to type when the verb is type, else null".
  - Add the moved screen rules, in one short paragraph:
    - on a dialogue or menu screen, use advance, select or cancel;
    - on a text field with an on-screen keyboard, use `type` with the text;
    - never pick letter buttons one by one.
  - `_action` validates `n` in range and `verb` in `VERBS`, otherwise returns `None` (today's invalid-action path).
- **`Loop.step`:**
  - `affs` starts empty.
  - Build the `thing` or `verb` affordance **after** `world.name` (loop.py:110) as `{"verb", "target": entity name or "", "at": entity at or None}`.
  - Its target id comes from `self.world.entity_ids.get(n)`. That is an exact match, as in the `go_to` branch (loop.py:118). Do not use `resolve_target`: its fuzzy match can create `affordance` rows (memory.py:195–198).
  - `preds` is computed for that one affordance only.
  - **Delete** loop.py:115 (the re-projection over all options) and loop.py:49 (`via` from the first approach affordance). `pending_via` already carries the chosen target.
- **`agent/world.py`:** this is a **deletion**. Drop `targets`/`matched` from the tiering (world.py:92, 100, `or matched` at 104, 106). After S2 `matched` is always false anyway; `_promote` already receives the chosen target.
- **Goldens:** regenerate with `W0_REGEN=1`; explain every diff in the Result. Fixtures using `{"i": 0}` (test_loop.py:13, 15, 662, 692, 729, 760; test_act.py:23) move to `thing`/`verb` once.
- **Checks:**
  - unit test: `{"thing": 1, "verb": "approach"}` runs `approach` toward entity 1 with target id `entity_ids[1]`;
  - unit test: `{"verb": "advance"}` presses A;
  - unit test: an out-of-range `thing` gives an invalid action;
  - then the full suite.

### E: frame eval on the new code
- `scripts/eval_frames.py` on the same `frames.json`, with the same seeding and the hard stop at 26. Output `.tmp/ps/new.json`.
- Print the baseline-vs-new table: `ok`, `self_target`, player-label accuracy, and `ok` on dialogue/menu/keyboard frames separately (the §8 risk).
- **STOP 6a:** report the table, the requests spent and the ledger. No live run without the user's approval.

### L3: live in two legs (only after STOP 6a approval)
1. **Prepare the copy.**
   - Read the ledger (stop if M1 < 108).
   - Copy `runs/m1_long` to `runs/m1_ps` and SHA-256 verify. `runs/m1_long` stays as L2 evidence.
   - In the copy, delete `frames/` files above 000070.
   - `--resume --from-step 70` cuts trace, calls, truth and checkpoints back to 70 and restores memory (run_agent.py:60–72).
2. **L3a:** `.venv-agent/Scripts/python.exe scripts/run_agent.py --run runs/m1_ps --resume --from-step 70 --steps 30 --max-calls 60 --stage c`.
   - Check: if any choice (same verb and target, or the same `go_to`) repeats **≥ 10 times in a row**, stop and report at STOP 6b without L3b.
   - Reason: checkpoint 70 memory still holds the player as `e_rival_2` "rival" at tile [0, 0] (tier interest), and the notebook says "Talk to the rival". `World.view` will keep listing it, and `go_to "rival"` resolves through memory (world.py:344–345).
   - Leaving it in tests whether the marked view overrides a stale goal; the leg split caps the damage.
3. **L3b:** resume from L3a's last checkpoint with the remaining requests (≤ 48) and `--steps 40`.
   - A moving step costs about 2 requests (act plus after-frame perception), so **expect L3b to end on the cap (exit 3)**. That is normal: checkpoint, report.
4. Then run `eval_run.py runs/m1_ps --health --from-step 71`.
5. **Report:**
   - **Progress first:** truth maps and when they were reached (1F door, Town, lab), party.
   - **Then health:**
     - self spot;
     - player-label accuracy on marked steps, defined as steps with `marked` and `player_fix` None, i.e. the model labelled the player itself;
     - no-effect streak and repeats.
   - **The worst stuck window,** with frames and the act `why`.

## 5. Contract delta (orchestrator drafts it in the Result; the user applies it)
- `perception`: the prompt describes and does not propose actions, so `affordances` is `[]` in practice (field and `normalize` unchanged). E2's prompt line about edge exits as `approach` affordances becomes `exit:` entities.
- `notebook and act`: the reply action gains `{"thing": n, "verb": v}` and the bare `{"verb": advance|cancel|type}`. The `{"i": n}` option index is removed.
- `world`:
  - `fix_player`: kind **and** name, the 1.5-tile keep, `tile/2` takeover, the insert record, idempotence;
  - `World.mark(frame, screen_was_world)`;
  - only model inputs are marked.
- `memory`: `view("perceiver")` is no longer called by the loop (it is kept; it has a test caller).
- `trace`:
  - new: `marked`;
  - `options` is now 0 or 1;
  - `choice.i` is always 0;
  - `Stuck` (tasks.py:25) still counts `options == 0` as no change, which now means "no valid action".
- `loop`: `prompt_version` is bumped; the cache hit also requires the same `marked`.

## 6. Budgets
- **Requests:** S0 ≤ 26 + E ≤ 26 + L3a ≤ 60 + L3b ≤ 48 = **≤ 160** of M1's 167. All go through `_ledger` (M1 bucket); never reset or raise a cap. stage_c is at 306 of 546, so it fits.
- **Lines:** report only. Expected `agent/` net roughly 0 to +40: S2 deletes the option plumbing and tier matching; S1 adds marking and the fix rule.

## 7. Out of scope (carried forward)
- Act-prompt cleanup beyond S2: duplicate self entries in the `World.view` text, cryptic place IDs, flag wording.
- Place fragmentation (`p_world_*`, `merge_pending`) from TL §7.
- Invalidating notebook goals, or memory rows such as `e_rival_2`, whose evidence turned out to be the player.
- Games with no self sprite: there is no lock, so there is no marker, and behaviour is today's. Covered when a second game is added.
- `scripts/check_world.py roles_check`: it builds `World(None, …)` without a lock, so it is a no-op under both old and new code. It is not a validation of S1.
- `loop/`. Edits to contracts.md.

## 8. Risks
- **Dialogue, menus and keyboards** used to get their actions from the perceiver; now the act call must choose `advance`/`select`/`type` itself, partly through the new bare-verb reply. E reports those screens separately. If they drop, report it at STOP 6a rather than adding rules.
- **The `{"thing", "verb"}` reply is untested.** E is its first measurement.
- **A wrong lock would mark the wrong sprite and make the error stronger.** TL measured 99/99, and the lock already unlocks on contradiction (TL B2). L3 reports marked-step accuracy.
- **The stale "rival" memory row** at checkpoint 70 can pull `go_to` back to the player's old tile. The L3a repeat check covers it.
- **Cost:** two calls per moving step as before (the L2 trace shows no shortcut fired). The before/after mark decisions must agree (S1), or a frame is perceived twice.

## 9. Result (filled in by the orchestrator)

**STOP 6a (Codex orchestrator, 2026-10-13): S0 baseline, S2 and E complete; live spend awaits approval.**

**Recorded-frame comparison (same accepted ten frames, one try each):**

| Metric | S0: options + marker | E: describe/decide + marker |
|---|---:|---:|
| All frames `ok` | 8/10 | 10/10 |
| World frames `ok` | 3/5 | 5/5 |
| UI frames `ok` (including title) | 5/5 | 5/5 |
| Dialogue `ok` | 1/1 | 1/1 |
| Menu `ok` (start and preset-name menus) | 2/2 | 2/2 |
| Keyboard `ok` | 1/1 | 1/1 |
| Title `ok` | 1/1 | 1/1 |
| World `self_target` flags | 2/5 | 0/5 |
| Pre-fix player identity correct on marked world frames | 5/5 | 5/5 |
| Marked world frames with `player_fix is None` | 5/5 | 3/5 |
| HTTP attempts charged | 20/26 | 20/26 |

Pre-fix identity means exactly one model-labelled `player` within Chebyshev normalized distance .08 of the labelled truth self spot. The stricter no-fix metric is also shown because L3's packet uses it. E's two fixes (`m1` 71 and 64) renamed `player character` to `player`; their `from` and `to` points are equal, so these are naming corrections, not wrong sprite identities. The title's decorative `Trainer` player label is unmarked and excluded from world identity metrics.

| Frame | S0 choice | E choice | `ok` S0 → E |
|---|---|---|---|
| m1_long 3, title | press A | buttons A | true → true |
| m1_long 4, start menu | select NEW GAME | buttons A | true → true |
| m1_long 5, dialogue | advance dialogue_box | bare advance | true → true |
| m1_long 18, preset-name menu | select NEW NAME | select thing 1 | true → true |
| m1 23, keyboard | type name_input | bare type | true → true |
| m1_long 37, 2F facing up | approach door, [.452,.375], self-target flag | approach thing 4 (stairs), [.64,.49] | false → true |
| m1_long 73, 1F Mom | approach NPC, [.253,.812] | buttons DOWN | true → true |
| m1 71, 1F | approach NPC, [.752,.8] | buttons DOWN RIGHT | true → true |
| m1 88, 2F facing down | press d-pad up, [.448,.438], self-target flag | go_to exit: stairs, [.45,.26] | false → true |
| m1 64, 1F mid-room | approach character, [.952,.578] | approach thing 9, [.953,.573] | true → true |

- **Scoring limits (reported, no new gates):** accepted UI button scoring is permissive for A/START. S0's step-88 press point lies at the player, although its `why` says it intends to move up toward stairs; the `self_target` flag does not by itself prove the model chose to approach its own sprite. S0 step 37 says it will exit through a door directly ahead; E says it sees stairs leading elsewhere. These are recorded-frame intent/score measurements; no selected action was executed and no new maps or party progress were measured. The accepted harness and labels were not relabelled or rewritten to change these results.
- **S2 implementation:** perception describes screen/text/entities and `exit:` entities; action affordances are constructed from the decision reply after place naming, using exact `World.entity_ids[n]`. `act` numbers visible things, marks the player as you, accepts entity/verb and bare advance/cancel/type replies, and keeps `go_to`/buttons. Old option indices are invalid. Perceiver memory context and option-derived tier matching were removed. Prompt version is `e5-perceiver-9`, invalidating old-prompt checkpoint perceptions. `normalize` is unchanged; legacy `scripts/run_goal.py`, `scripts/eval_act.py`, `scripts/e1_perceive.py` remain loadable and see empty affordance lists from the new prompt. No game-specific agent code or new gameplay gates.
- **Checks and exact commands:**
  - S0: `.venv-agent/Scripts/python.exe scripts/eval_frames.py` → `status=ok requests=20`; `.tmp/ps/baseline.json`, `.tmp/ps/s0_baseline_run.log`.
  - E: `.venv-agent/Scripts/python.exe scripts/eval_frames.py --out .tmp/ps/new.json` → `status=ok requests=20`; `.tmp/ps/new.json`, `.tmp/ps/e_run.log`.
  - S2 targeted: `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_act.py tests/agent/test_perceive.py tests/agent/test_loop.py tests/agent/test_world.py -q -p no:cacheprovider`. Initial agent `2 failed, 95 passed, 1 warning in 162.28s`; root reproduced `2 failed, 95 passed, 1 warning in 159.06s`. One authorized repair corrected raw fixture coordinates to the 0–1000 grid and added the intended NPC entity to a blocked-action fixture without weakening assertions. Repair agent `97 passed, 1 warning in 158.26s`; root `97 passed, 1 warning in 187.31s`.
  - Golden regeneration: `W0_REGEN=1` with `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_world_golden.py -q -p no:cacheprovider` → `1 passed in 23.95s`; variable unset afterward. Root reviewed the full data diff; see explanation below.
  - Full suite in background: `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider`. Agent `240 passed, 1 warning in 274.38s`; root `240 passed, 1 warning in 382.40s`. Exact logs `.tmp/ps/s2_repair_agent_full.log` and `.tmp/ps/s2_orchestrator_full.log`.
  - Final closing full suite: 240 passed, 1 warning in 244.59s (0:04:04). Exact log `.tmp/ps/stop6a_closing_suite.log`.
  - Root offline comparison checks via `.venv-agent/Scripts/python.exe -`: matching ten frame keys, identical per-row self seed/lock/injection/mark/truth fields, status and attempt counts, accepted hashes and ledger totals all passed. E audit independently confirmed the same metrics and seeding. `git diff --check` passed. Only suite warning: pysdl2-dll 2.32.10.
- **Every golden change:** only `tests/agent/data/w0_golden/act/trace.jsonl` changed. Steps 1 and 2: `options` 2→1 and `choice.i` 1→0 because the model's sole chosen go_to/buttons action is no longer appended to a perception-proposed option list. Steps 3–8: `options` 1→0 because no valid action was selected; an unchosen perception option is no longer present. All other serialized trace fields are identical, including `marked`, flags, effects, input counts, calls and memory/identity results. All five memory JSONLs (entities, places, relations, seen, tiles) are unchanged. Goldens are staged with `git add -f tests/agent/data/w0_golden`.
- **Health and progress:** build/check health green after the one S2 repair; recorded-frame scores above. No PS live play, new maps or party measured; historical L2 remains house only / party 0. L3a/L3b unstarted and no live approval inferred from the continuation request.
- **Scope/safety:** four agent files; six affected test files including the explicitly approved one-line prompt-version expectation in `tests/agent/test_trace_brief.py:37`; golden trace; required docs. `normalize`, `World.fix_player`, `World.mark` verified unchanged. S1 raw-frame/mark/cache/cut/checkpoint behavior and TL motion/probe/HUD/recent/here/routing/merge fixes retained. No `loop/`, contracts, accepted evaluator, label or protected-run changes. 934 protected-run file hashes verified with zero changes. `.cache/model/` and unrelated untracked `.claude/` preserved.
- **Lines (report only):** S2 agent 2400→2399 (−1); scripts 3413→3413; tests/agent 3873→3916 (+43). PS overall agent 2369→2399 (+30), including S1. S2 diff before docs: 11 tracked files, 121 insertions / 79 deletions. No compression pass or unrelated cleanup.
- **Models/tools:** S2 subagent gpt-6-luna HIGH, one slice, same agent for one repair; E audit subagent gpt-6-luna HIGH, one slice, zero calls and no writes. No Opus. Full S2 self-contained prompt `.tmp/ps/s2_prompt_current.txt` contains the verbatim card, program §2, cited contracts, exact symbol/file scope and checks; the trace-brief version-only scope addition was explicitly sent. CodeGraph first, Ponytail full, Headroom local compression/retrieval; proxy unreachable.
- **Requests/ledger:** PS 40/160: S0 20/26, E 20/26, L3a 0/60, L3b 0/48. Ledger before S0 `total=931,m1=883`; after S0 `total=951,m1=903`; after E `total=971,m1=923`. Other fields unchanged: `smoke=48,stage_a=142,stage_b=297,stage_p=25,stage_q=60,stage_c=306`. M1 127 left; total 129 left. No reset, refund or cap increase; no runner exit 3/4. Planned live cap remains 108 (60+48), within both remaining ledger and PS allowance.
- **Deviations:** the inherited S0 failure was resolved outside this orchestrator by the user session (the accepted fix is retained below). S2 used its single repair for two fixture migrations. One accidental subagent command ran repository-wide pytest; it was interrupted at 17%, no files changed, and the exact prescribed agent-only suite was run successfully afterward. The interrupted log is `.tmp/ps/s2_repair_full.log`; no unrelated suite failure was used as a gate or repaired. No design, ledger cap or accepted scoring rules were changed.
- **Open questions/next:** STOP 6a awaits approval for L3's ≤108 requests. Then read the ledger, run the full suite before live, copy and SHA-256 verify `runs/m1_long` to `runs/m1_ps`, resume checkpoint 70 for L3a, and stop if a choice repeats ≥10 times; only then L3b within its 48 cap. Checkpoint 70's stale rival/player row and notebook goal are intentionally retained. Place fragmentation and stale player-evidence goals remain §7 exclusions.

**Exact proposed S2 contract text (not applied; contracts.md remains frozen):**

Replace the act action comment in `notebook and act` with:

```python
    # action: {"thing": n, "verb": "approach"|"interact"|"select"|"click"}
    #       | {"verb": "advance"|"cancel"|"type"}
    #       | {"go_to": name} | {"buttons": [<= 8 input names]}; anything else -> None.
    # n is an integer (not bool) indexing this perception's entities, in range.
    # A bare verb has no thing field. The old {"i": n} reply is removed.
    # text is the text to enter for type, otherwise null.
```

Replace perception's E2 edge-exit prompt paragraph with:

> The perception prompt describes visible screen, text and entities; it does not propose actions. Every visible passage (door, stairs, door mat, opening) is an entity named `exit: <what it is>`, at the tile to step onto. The `affordances` field and `normalize` API remain for legacy callers, but the describe-only prompt/schema omit actions and `normalize` returns `affordances: []` when absent. The player entity line remains.

Replace memory's perceiver-view caller statement with:

> The loop no longer calls `Memory.view("perceiver", ...)`. The method remains for existing test/legacy callers. Describe-only perception receives the game brief, repair/relook context and marker caption, without remembered-place naming context.

Add to trace/loop:

> `options` is 0 or 1, counting the single valid action constructed from the decision reply; `choice.i` is 0 when present. `Stuck` counting `options == 0` now means no valid action. A numbered action's target ID is `World.entity_ids[n]` after place naming; no fuzzy target resolution or all-option prediction pass. The perception prompt version is `e5-perceiver-9`; cache reuse still requires the same raw hash, prompt version and marker state. S1's proposed marker/raw-frame/checkpoint text below remains unchanged.

Contracts SHA-256 remains `cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70`.

**S0 evaluator fix (2026-10-13, user session after the S0 stop below; 0 requests). The evaluator is accepted; the orchestrator only runs it.**
- **Scoring.** `_buttons_toward` now scores a button press as toward a target when its first direction has a positive component toward an acceptable point and no pressed direction points away from it. One step toward a diagonal target is correct, since one move is all a step can make.
  - Checked with inline asserts: RIGHT, RIGHT DOWN and `press RIGHT` toward right-hand stairs pass; LEFT, RIGHT LEFT, UP and A fail; the 26-attempt guard raises.
- **Labels, checked against the PNGs:**
  - `m1_long` 37: target was the wall above the player; now stairs (.65, .49) and the PC desk (.10, .46).
  - `m1_long` 74: dropped. Its target was in the black void, and the player stands on the arrival stairs tile, so the right action is ambiguous.
  - `m1` 71: is 1F; Mom (.75, .82) added beside the stairs.
  - Added `m1` 88 (2F, facing down, stairs one tile above) and `m1` 64 (1F mid-room; Mom, plus the door mat at the bottom).
  - 10 frames = 20 requests, which is ≤ 26.
- **Seeding.**
  - Frames whose previous trace row has no motion lock (all `runs/m1`, and `m1_long` 37 from L1) use the labelled spot (harness-only, flagged `truth_injected`).
  - Every world frame is mark-eligible. The eval measures the prompts with the marker; S1's unit tests own the loop's eligibility rules. The harness's own `cut()` check disagreed with the trace's `scene_cut` (e.g. `m1_long` 72→73).
- **Cleanup.** Unused imports, a dead variable and the previous-frame load were removed; pyflakes is clean. `--dry` scores 10/10 rows as intended with 0 requests.
- **Next:** `.venv-agent/Scripts/python.exe scripts/eval_frames.py` (baseline, today's prompts + marker, ≤ 26 attempts) → S2 → full suite → `eval_frames.py --out .tmp/ps/new.json` → STOP 6a.

**S0 stop record (orchestrator, before the fix):**

**S0 stop (Codex orchestrator, 2026-10-13; 0 requests):**
- **Decision:** stopped after S0's initial implementation and its single authorized repair both failed review. S1 remains resolved. No cloud baseline was run; S2, E and L3 were not started. There is no STOP 6a comparison table. This is an evaluator implementation failure against the card, not a model-quality or gameplay-progress gate. The orchestrator did not implement a fix or start a third repair.
- **Initial failure:** labels included a black transition frame (`m1_long` 80), dialogue incorrectly called keyboard (`m1` 29), and acceptable targets at the player. Numeric `thing` and bare-verb actions decoded as invalid; wrong-way buttons scored true; marker flag was omitted from act; temporary memory could be reused. Root output: `.tmp/ps/s0_orchestrator_dry.log`, `.tmp/ps/s0_review_failures.log`.
- **Repair:** selected nine eligible frames: `m1_long` 3 title, 4 start menu, 5 dialogue, 18 preset-name menu, 37 upstairs facing up, 74 upstairs facing down, 73 downstairs with Mom; `m1` 23 keyboard and 71 sideways. Targets are computer `[.45,.35]`, stairs `[.69,.42]`, Mom `[.25,.82]`, right-edge stairs `[.95,.5]`. Excluded probe rows 71/72 of `m1_long`, black frame 80, and stale `m1` frames above 94. Root visually reviewed the PNGs. Frame 37 is correctly unmarked because its previous trace row is unlocked; three later world frames are marked. `m1` 71's injected self spot is harness-only, based on the visible fixed camera/player projection and paired truth coordinates.
- **Remaining failure:** `_buttons_toward` requires both nonzero coordinate directions in the same action. A single `RIGHT` press from `[.45,.4444444444444444]` toward the labelled stairs `[.69,.42]` reduces distance but returns false. Exact root output: `AssertionError: RIGHT toward labelled stairs rejected: requires both RIGHT and UP`. This contradicts the card's requirement that buttons moving toward an acceptable point count as `ok`; it can bias S0/E. UI button scoring is also broad (e.g. a title action containing B can be accepted); no cloud numbers are claimed.
- **Commands and results:**
  - `.venv-agent/Scripts/python.exe -m py_compile scripts/eval_frames.py` passed, before and after repair.
  - `.venv-agent/Scripts/python.exe scripts/eval_frames.py --dry --out .tmp/ps/s0_repair_orchestrator_dry.json` printed all nine rows, `status=ok requests=0`; synthetic `ok=True` rows only exercise the harness and are not model results.
  - Root offline assertions through `.venv-agent/Scripts/python.exe -`: numeric entity/bare verb decoding, wrong-way rejection and the 26-attempt mocked ledger guard passed (27th refused, no real ledger writes); the toward-stairs assertion above failed. Exact output: `.tmp/ps/s0_repair_orchestrator_check.log`.
  - `git diff --check` passed. The final shell command's exit is not used as evidence for the preceding Python assertion; its own traceback is the failure evidence.
  - Inherited full suite in background: `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` → `238 passed, 1 warning in 246.74s (0:04:06)`; `.tmp/ps/pre_s0_suite.log`.
  - Closing full suite: 238 passed, 1 warning in 245.97s (0:04:05).
- **Progress:** no PS live play or new maps/party measured. Historical L2 remains house only / party 0. **Health:** inherited agent suite green; S0 evaluator correctness red; model/player accuracy remains unmeasured in this session.
- **Scope and safety:** only new `scripts/eval_frames.py` and `scripts/frames.json`, plus this packet, `docs/plan.md` and rewritten handoff. No `agent/` edits, game-specific agent code, new decision gates, earlier-fix removal, contracts or `loop/` edits. Protected-run hashes verified: 934 files, zero changes. Unrelated untracked `.claude/` untouched. `.cache/model/` preserved.
- **Goldens:** unchanged; no regeneration or new data changes. S1's existing golden explanation below remains valid. S2's prescribed regeneration was not reached.
- **Lines (report only):** agent 2400→2400, scripts 3122→3421 (+299), tests/agent 3873→3873; eval_frames 299, frames.json 11 lines. The subagent's final report said 264 script lines; the orchestrator counted the on-disk artifact as 299.
- **Models/tools:** one S0 subagent, explicitly gpt-6-luna HIGH, fork without context; same agent for one repair. No Opus. Self-contained full prompt `.tmp/ps/s0_prompt_current.txt` includes the card, program §2, cited contracts, exact scope and checks. CodeGraph first; Ponytail full; Headroom local compression/retrieval worked, proxy unreachable.
- **Requests:** PS 0/160; S0 0/26, E 0/26, L3a 0/60, L3b 0/48. Before/after ledger identical: `total=931, smoke=48, m1=883, stage_a=142, stage_b=297, stage_p=25, stage_q=60, stage_c=306`; M1 167 remaining, total 169 remaining. No reset/refund/cap increase. No runner exit 3/4.
- **Deviations:** stopped before the baseline after the second implementation failure, per the user's one-repair limit. PS's measured-quality checks still report without blocking; this stop concerns the evaluator's adherence to its scoring card. S0 uses nine frames (within 13), so a successful eval would ordinarily cost 18 attempts per arm before retries. No live spend or phase cap changed.
- **Open question:** resolve evaluator scoring under revised instructions or explicit further-repair authorization, then establish the S0 baseline before S2. Place fragmentation and stale player/rival goal evidence remain §7 exclusions. No additional contract change is proposed by S0; S1's exact proposed text and unchanged SHA-256 remain below.

**S1 resolution (2026-10-13, user session after the stop below; 0 requests):**
- **Cause, verified.**
  - The failing test's fixture was a uniform black frame. `observer._blank` (observer.py:16) treats a frame where one colour covers 95% or more as a blank transition frame.
  - Step 1's `wait_until` therefore reported `blank_seen`, which set `scene_cut`, so `after_mark_eligible` was False. The code was right; the fixture was not.
  - Fix: `tests/agent/test_loop.py` fills the fixture's top half grey (one line plus a comment). The test now passes unchanged otherwise.
- **Golden regenerated** with `W0_REGEN=1`; only `act/trace.jsonl` changed, row by row:
  - every row gains `marked`;
  - steps 3–8 are `marked: true`, and their act flags gain `the sprite inside the red box is you`;
  - steps 1–2 are unmarked (no previous frame, then a scene cut);
  - memory files are unchanged.
  - S2 regenerates it again for its own changes.
- **Full suite:** `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` → `238 passed, 1 warning in 269.00s`.
- **Next:** S0 (baseline = today's prompts + S1 marker), as §3 orders. The stop record below is kept for history.

**Stop record (orchestrator, before the resolution):**

- **Decision:** stop at S1. The slice failed its first check and its single repair round; the orchestrator did not take over implementation or authorize another repair. This is a broken test suite, the stopping condition in §1. S0, S2, E and L3 are unstarted. No STOP 6a comparison table exists yet.
- **Progress:** no PS live play or frame-eval requests; no new maps, party or gameplay progress measured. Historical L2 progress remains house only / party 0 (TL §7).
- **Health:** build/check health red. The unresolved test expects the second same-raw step to be marked and reuse the after perception; it observes `marked == False`. Test: `tests/agent/test_loop.py:112`, assertion at `:138`. Its fixture is a uniform black frame; whether blank-frame settling or eligibility causes the expectation mismatch remains an open question, not a verified diagnosis.
- **Acceptance command:** `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_world.py tests/agent/test_loop.py -q -p no:cacheprovider`.
  - First subagent: `3 failed, 78 passed, 1 warning in 208.16s`.
  - First orchestrator: `3 failed, 78 passed, 1 warning in 161.58s`; exact output `.tmp/ps/s1_orchestrator.log`.
  - One repair fixed the interior half-tile assertion, the perception reply fixture and the old four-value `_see` unpack; added the required near-player keep/rename/idempotence and actual-step cut/cache checks.
  - Repair subagent: `1 failed, 81 passed, 1 warning in 207.21s`.
  - Repair orchestrator: `1 failed, 81 passed, 1 warning in 276.95s`; exact output `.tmp/ps/s1_repair_orchestrator.log`.
- **Closing full suite (background):** `.venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider` → pytest's own summary: `2 failed, 236 passed, 1 warning in 394.76s`. Exact output `.tmp/ps/closing_suite.log`. Failures: the same cut/cache test, plus `tests/agent/test_world_golden.py::test_act_eight_steps` (`trace.jsonl` comparison; five memory items identical). Only warning: pysdl2-dll 2.32.10.
- **Scope review:** edits only `agent/world.py`, `agent/loop.py`, `tests/agent/test_world.py`, `tests/agent/test_loop.py`. No game-specific agent code, new decision gates or cloud calls. Previous motion/probe/HUD, recent/here, routing, merge and memory fixes retained by diff review. Protected evidence runs, `.cache/model/`, `loop/` and unrelated untracked `.claude/` were not edited. `git diff --check` passed.
- **Goldens:** not regenerated, changed or staged; S2's prescribed regeneration has not been reached. The closing suite records the existing golden trace mismatch instead of accepting it. No data diff to explain.
- **Line counts (report only):** `agent/` 2369→2400 (+31), `scripts/` 3122→3122 (+0), `tests/agent/` 3775→3872 (+97). Files: world 668, loop 218, test_world 681, test_loop 821 lines. Diff: 158 insertions, 30 deletions.
- **Models/tools:** one gpt-6-luna HIGH subagent for S1, same subagent for its one repair; no Opus. Self-contained prompt `.tmp/ps/s1_prompt.txt` contained the card, program §2, cited contracts, exact scope and checks. CodeGraph first; Ponytail full. Headroom local compression/retrieval worked, but its configured proxy was unreachable.
- **Requests:** PS 0/160; S0 0/26, E 0/26, L3a 0/60, L3b 0/48. Ledger before and after identical: `total=931, smoke=48, m1=883, stage_a=142, stage_b=297, stage_p=25, stage_q=60, stage_c=306`; total 169 remaining, M1 167 remaining. No cap reset, refund or increase. No runner exit 3/4 occurred.
- **Deviations:** stopped before S0 because S1 failed twice, as the kickstart requires. Added private `seen_image`/`seen_marked` fields so `_see` retains its existing four-value return shape. Added `frame_mark_eligible` checkpoint state to preserve the after-frame eligibility decision on the next identical before frame. No cap or phase scope changed. Packet §2's stale-budget correction is already present in program §4 (M1 883/1050, 167 left); docs/plan.md now uses that ledger.
- **Open questions (resolved above):** resolve the cut/cache fixture versus blank-frame behavior under a revised S1 packet or explicit new repair authorization. The golden regeneration remains deferred to S2 after S1 is resolved. Place fragmentation and stale player/rival notebook evidence remain out of scope (§7).

**Exact proposed contract text for S1 only (not applied; contracts.md is unchanged):**

Add to `world`:

```python
def fix_player(self, per: dict, size: tuple[int, int]) -> dict | None
    # On a world screen with a motion-locked self_spot and known tile_px:
    # keep a player-kind entity within 1.5 tiles (Chebyshev pixel distance), renaming it player;
    # otherwise promote the nearest entity within tile_px/2 to kind/name player;
    # otherwise insert {"kind":"player","name":"player","at":self_spot}.
    # Demote every other player-kind entity to character; rename player to character on those rows.
    # Return {"from": old_at or None, "to": chosen_at} for any change, otherwise None.
    # Idempotent. Without the lock or on another screen, leave the model's labels unchanged.
def mark(self, frame: Image, screen_was_world: bool) -> tuple[Image, bool]
    # Only when motion-locked and screen_was_world, draw a 1px red rectangle on a copy:
    # [x-9,y-10,x+9,y+9], x,y = self_spot * native frame size. Otherwise return (frame,False).
```

Only perception/act model inputs receive marked images. Saved PNGs, `before`, `after`, `prev_frame`, cut/scroll observations, HUD crops, miss hashes and trace `frame_hash` use raw images. Before eligibility uses the previous world perception and no cut; after eligibility uses the current world perception and no skill cut. Persist the after eligibility for the next identical raw before frame. The perception cache requires the same raw hash, prompt version and `marked`; checkpoint state gains `perception_marked` (bool, default false) and `frame_mark_eligible` (bool or null).

Add to `trace`: `marked: bool`, whether this step's before/act model input was marked. S2's perception/action/options contract changes are not implemented or proposed as completed here.

Contracts SHA-256 before/after: `cced97713bc8dc71036a052611ba9d6bc05d5b9b678fbb6dca546a743e1ace70`.

## 10. Handoff notes
Rewrite `docs/plans/handoff-notes.md` per the template §10 (under 80 lines, fixed headings).
