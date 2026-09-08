# Commercial experiment — Platinum, StS, and skill reuse

**Stage:** Platinum + Slay the Spire through the OS harness; L3 constructor on traces.

## Checkpoints

- melonDS window titled `melonDS`, user-owned Platinum dump **not in git** — not present on this machine
- Steam Slay the Spire window — not present
- Act JSONL traces to mine (`--traces`) — used L2 2048 dry-run turns

Constructor rules (GameSense 4.5.3 fix): merge if name/recognize match, spawn only under a soft cap of 8, max 3 rewrites, skills **disabled** until a human glance (`enabled: true`). Constructor is a batch job, not a per-turn pause.

```powershell
python scripts/run_commercial.py --traces runs\<act-run>\turns.jsonl
```

## Results

Commercial windows skipped (expected). Constructor still ran on 2048 Act traces:

| metric | value |
|---|---|
| skill_count | 1 |
| spawned | 1 |
| merged | 0 |
| rewritten | 0 |
| invokes | 0 |

One repeated swipe sequence became one skill (not 12 duplicate modules). Unit tests cover merge-not-spawn and the rewrite cap of 3. Plot: [skill_reuse.png](figures/skill_reuse.png).

When Platinum/StS windows exist, the same script captures the window, runs L2 named actions, and appends traces before mining.

Phase 2 GSMs are a **separate** registry ([08-gsm.md](08-gsm.md)). Discrete skills stay sequences. Those fixtures keep an empty GSM list (mixed pacing) unless we later add a slow overworld walker.
