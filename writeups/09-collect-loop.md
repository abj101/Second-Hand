# Collect-loop baseline (historical)

**Status:** historical — fake/Elden collect-only baseline from early `feat/collect-loop`.  
**Live path:** turn-based STS2 on this branch — see [`system.md`](../system.md).  
**Chapel appendix:** pad/explore/Ratchet era — [`loop/DESIGN.md`](../loop/DESIGN.md) (not the operator driver here).

**Package:** `loop/` — game-agnostic screenshot → unlabeled OS device → pixel score → JSONL.

## What shipped (baseline era)

- Fixture protocol (`id`, window, unlabeled `action_table`, pixel `scorer`, `terminal`, in-game `after_fail`)
- OS port: SendInput + vgamepad, no GSM. Tables: `xbox360`, `keyboard`, `lab` (device names only)
- Realtime agent: idle (`release_all`) during think, one burst, `think_ms` / `obs_age_ms`
- `fake` fixture for pytest / `--skip-models`; `elden` plugin (window `ELDEN RING`, pad first, keyboard if ViGEm missing)
- Persistent sqlite memory (64×64 thumb cosine)
- HUD scorer + hack audits (fail-screen vs vitals, stuck, menu stretch, `failed_while_thinking`)

## Commands (baseline only)

```powershell
pytest tests/loop -q
python -m loop smoke
python -m loop collect --fixture fake --lives 10 --skip-models --memory
python -m loop collect --fixture elden --lives 10 --memory
```

Elden Ring: borderless windowed, offline, user-owned copy. Put a post-character-create save at `data/loop/elden/start.sl2` (gitignored) when you want snapshot restore for eval. Live sessions use the game's own respawn.

For STS2 collect, export, train, and eval, use [`system.md`](../system.md).

## Fake baseline (this writeup)

Ran `python -m loop collect --fixture fake --lives 10 --skip-models --memory`. Ten short lives, round-robin unlabeled pad names, memory grown across lives, fail rate 1.0 as designed (synthetic fail screen after three steps). `runs/` is gitignored.

Control coverage is a session metric (`unique_device_names` in `summary.json`). Skip-models round-robin is session-scoped so it is not stuck on `a,b,x`.

## Tests

`pytest tests/loop` — schema round-trip, unlabeled tables, HUD crops under `loop/fixtures/elden_hud/`, fake collect + recall, audits. No Steam required.
