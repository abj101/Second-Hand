# LLM1 target-routing gap closure — live report

Run: `runs/20260905T020242Z_sts2_collect`  
Stopped early at **11 combat turns** (requested). Synthetic: `tests/loop` **546 passed**.

## Fixes landed (this pass)

1. Bbox-less `unknown_character` is player-excluded, not Explorer/choice/inspect.
2. After 2 failed inspects, route is `act (identity-unresolved-fallback)` — not silent Strike under `confirmed-action-surface`.
3. Typed combat (`source_role` / `source_object_id`) and hover skip `fill_from_aim` in `resolve_legal_chord`.

## Live gates

| Gate | Result |
| --- | --- |
| LLM1 first-pass schema valid | **11/11 (100%)**, 1 attempt each |
| Final schema valid | **11/11** |
| `mark_*` in choice/target | **0** |
| Player as Explorer blocker | **0** (11/11 facts `player-excluded`) |
| `unknown_character` in Allowed controls | **0** |
| First-seen hostile → Explorer | **yes** (steps 1–2: `epistemic-blocker:unknown_character_1`) |
| Then ACT after inspect | **yes** (step 2 hover `hv_10`, then `confirmed-action-surface`) |
| `identity-unresolved` | **0** (only one inspect; names recovered) |
| Role-swap / stale-mark dispatch | **0** |
| Card click executed | **0** — 9× `illegal-chord:mk_11:0.92` |

## What worked

- Step 1: unidentified right-lane sprite is an identify-before-act blocker; left-lane `unknown_character` is player, not a blocker.
- Allowed controls are only cards/End Turn. Player sprite is not a legal choice.
- Step 2 hover identified Nibbit / Aggressive; route flipped to ACT.
- Duplicate Strike cards got `choice_id` (`card_1`…`card_5`).

## Remaining live failure (not in this gap list)

After ACT, Strike/Defend bind to **mk_11** (left-lane player `unknown_character` at ~0.25, 0.58). Affordance marks that click illegal → skip. Energy stays 3; no card plays.

Prompt inventory is correct (Nibbit/Aggressive are targets). Dispatch still aims the player mark. Likely aim/fill bind of the card click onto the player sprite, not the routing policy this pass closed.

Also leftover: duplicate `unidentified_enemy` rows still appear under Allowed/Inspectable targets after Nibbit is named (HP-anchor / extra mark), and `unknown_character_1=Aggressive` keeps a stale explorer id.

## Verdict

Routing + schema + player-exclusion gaps from the audit: **closed and live-confirmed**. Combat still cannot spend energy until mk_11 card-bind is fixed.
