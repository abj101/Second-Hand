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

Order: 1 → (2 ∥ 3) → 4. `skill_on` A/B blocked until Batch 4 passes.
