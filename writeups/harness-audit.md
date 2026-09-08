# STS2 Harness Audit

Status: initial root-cause audit. Evidence window: latest 15 `sts2_collect` runs in `writeups/running-report.md`.

## Outcome

Three broken contracts explain current failures:

1. Planner emits card→target chord; STS2 OS path sends only card click.
2. Unknown entity becomes legal target before discovery; hover path cannot reliably bind entity ID to mark or persist learned name.
3. Invalid/incomplete LLM1 scene stops LLM2; valid scenes still mix stale identities and controls.

## P0 — card targeting encoded, not executed

- `action_from_labels()` writes primary `click_x/y` plus target `click_x2/y2` ([`loop/os/aim.py`](../loop/os/aim.py#L2235)).
- `Sts2Fixture.execute_chord()` sends one primitive from primary coordinates, then explicitly ignores second coordinates as “leftover schema” ([`loop/fixtures/sts2.py`](../loop/fixtures/sts2.py#L216)).
- Logs still name action `mk_card>mk_target`, creating false evidence that two clicks reached SendInput.
- Run `20260902T191059Z`, steps 1–3: three card→enemy actions carry second coordinates; `executed` records chord name, while fixture only dispatches first click. All three reward deltas remain `0`.

Impact: targeted attack cannot reliably occur. Primary click may select card; target click never follows.

## P0 — selected-card state guessed from geometry

- `_update_selected()` treats lower-screen click as selected card based on `y` and label; no visual selection confirmation ([`loop/agent/loop.py`](../loop/agent/loop.py#L945)).
- State clears/advances from indirect HUD/pixel signals, not card highlight, card removal, energy spend, or enemy HP change.
- Run `20260902T191059Z`, step 4: planned `mk_2>mk_10`; final action reduced to `mk_2`; `executed_as_proposed=false`, reward `0`.

Impact: harness can believe card selected after wrong/no-op click, then misclassify later actions and block recovery.

## P0 — unknown discovery loop never closes

- Auto-inspect runs only when no allowed target exists ([`loop/agent/loop.py`](../loop/agent/loop.py#L2982)). Unknown enemy itself enters allowed targets, so reasoner attacks it instead of inspecting.
- `action_inspect_label()` resolves text labels only ([`loop/os/aim.py`](../loop/os/aim.py#L2205)). Entity tracker exposes `unknown_character_N`, while aim often labels same bbox `icon` / `unidentified_enemy`; no entity-ID→bbox→mark fallback.
- Run `20260902T191059Z`: step 1 directly attacks `unidentified_enemy`; step 2 identity becomes `Goliath`; step 3 returns to `unidentified_enemy`.

Impact: unknown object may be targetable but not inspectable; identity oscillates instead of monotonically moving `unknown → confirmed`.

## P1 — hover learning has brittle gates

- Tooltip OCR runs only when `action.hover && pixel_changed` ([`loop/agent/loop.py`](../loop/agent/loop.py#L3864)). Small tooltip changes below pixel threshold produce no OCR and no learning.
- Lexicon accepts tooltip text only near hovered sprite ([`loop/memory/lexicon.py`](../loop/memory/lexicon.py#L254)). STS tooltip/title can render away from sprite.
- Learned entry uses `screen_from_labels()` rather than authoritative resolved host screen ([`loop/agent/loop.py`](../loop/agent/loop.py#L3869)). Wrong screen key prevents later lookup.
- Non-combat enemy guard can reject hover before discovery: run `20260902T191059Z`, step 7 returns `non-combat-enemy-target` for `hv_7`.

Impact: even successful hover may neither OCR nor persist identity.

## P0 — LLM1 failure removes LLM2 entirely

- `perceive_scene()` retries schema/truncation, then returns `schema_valid=false` ([`loop/agent/perceive.py`](../loop/agent/perceive.py#L859)).
- `_run_two_stage_vlm()` skips reasoner on invalid schema ([`loop/agent/loop.py`](../loop/agent/loop.py#L1617)). No minimal canonical fallback reaches LLM2.
- Latest 15 runs: 63 LLM1 raw records; 36 end mid-JSON; only 27 parse as JSON.

Impact: perception formatting/token failure becomes empty action/dead turn instead of degraded but actionable scene.

## P1 — LLM1→LLM2 scene contract is not canonical

- Cached controls preserve old discovery/card rows while fresh OCR appends new rows ([`loop/agent/perceive.py`](../loop/agent/perceive.py#L1119)). No generation/freshness field.
- Entity identity uses spatial matching and screen-scoped lexicon; same sprite may appear simultaneously as `Goliath`, `unidentified_enemy`, and `unknown_character_N`.
- Latest 15: host/LLM1 screen disagreement in 7/19 comparable parsed records.
- Reason JSON is syntactically healthy (83/83), but semantics follow poisoned inputs: `Singleplayer` chosen on map; `Prev` / `icon` treated as progress controls; `Player` appears in target/control vocab.

Impact: LLM2 reasons coherently over contradictory/stale scene data. Better prompting alone cannot fix it.

## Required invariants before next long run

- Card attack success requires two dispatched OS clicks or explicit two-turn transaction with observed selected-card confirmation.
- `executed` must describe primitives actually sent, not requested chord name.
- Every object has stable `entity_id`, bbox, role, label status (`unknown|confirmed`), source, confidence, and scene generation.
- Unknown enemy remains inspectable and non-attackable until confirmed, unless policy explicitly permits blind target by stable ID.
- Hover discovery binds via entity bbox, OCRs tooltip without requiring global MAD threshold, then atomically renames same entity ID.
- LLM2 always receives one validated scene snapshot. Invalid LLM1 JSON degrades to canonical host/CV scene instead of skipping reasoner.
- Prompt, validator, and click mapper consume same snapshot ID and same control/target sets.

## Next verification

Use short controlled combat fixture before live long run:

1. Emit one `Strike → unknown_enemy` transaction.
2. Assert two actual mouse primitives and intermediate selected-card evidence.
3. Hover one unknown sprite; assert same entity ID changes to confirmed label next frame.
4. Inject truncated LLM1 JSON; assert LLM2 still receives canonical fallback scene.
5. Reject stale controls from prior scene generation.

