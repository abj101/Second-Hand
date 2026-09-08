# Perception V3 — canonical target identity

Follow-up to `bugfix/perception2/`. V2 closed empty combat inventory and transition softlocks; live Run B then bound-looped on `target-not-found:object:N`.

| File | Batch | Gate |
|---|---|---|
| `00-orchestrator-plan.md` | — | root causes, contracts, order |
| `01-target-identity.md` | 1 | Run B step 1/6 replay → one `enemy_1`, no `object:N` on domain roles |
| `02-phantom-and-confidence.md` | 2 | Run B step 7/19 replay → one target, confidence in `[0,1]`, route `act` |
| `02b-reopen-dual-store-ids.md` | 2 reopen | Live Run B dual `enemy_1`+`enemy_2` store rows → one clustered target |
| `03-live-tooling.md` | 3 | monitor completes on Run B; `inspect-run` reports id hygiene |
| `04-live-validation.md` | 4 | Run A ≥1 card from title; B+C consecutive wins; zero `target-not-found` |
| `05-card-confirm.md` | 5 | Hand-band gate, blocked-retry, like-for-like frames, replan vs softlock; then B+C |
| `06-run-b-dual-target.md` | 6 | HP/body column stack, TNF fail-closed, map replan; then B+C |

Order: 1 → (2 ∥ 3) → 4. Batch 5 closed card-confirm. Batch 6 closes dual `enemy_2` TNF + post-loot map softlock. `skill_on` A/B blocked until Batch 4 B+C pass.
