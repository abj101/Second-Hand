# Program: autonomy redesign (P2–P4) + World Memory (W0–W4)

Status: **approved 2026-10-12** (user chose the recommended order) · Branch: feat/lean-agent
Design docs: [autonomy-redesign.md](autonomy-redesign.md) (R1), [world-memory.md](world-memory.md) (WM). Frozen signatures: [contracts.md](contracts.md) (`notebook and act`, `world`, and the 2026-10-12 notes under memory, space, trace and run directory).

This file is the single entry point. The orchestrator pastes nothing else; it reads this file, finds the next phase, and runs that phase's packet.

## 1. Run order and stop points

| # | Phase | Packet | Cloud requests (cap) | After the gate |
|---|---|---|---|---|
| 1 | P2: act-prompt eval on recorded frames | [P2.md](P2.md) | ≤ 40 | **STOP 1**: user reviews the eval table |
| 2 | W0: extract World from the loop (no behaviour change) | [W0.md](W0.md) | 0 | continue |
| 3 | W1: one owner for places, merging established places | [W1.md](W1.md) | 0 | continue |
| 4 | W2: `go_to` through memory, routing across places | [W2.md](W2.md) | 0 | continue |
| 5 | W3: evidence states, seen tiles, known-things view | [W3.md](W3.md) | 0 | **STOP 2**: user approves the live spend |
| 6 | P3: live comparison, 40 steps with `--act` + World Memory | [P3.md](P3.md) | ≤ 80 | **STOP 3**: user decides "better?" |
| 6b | P3b: fix player identity, cut identity, recent actions (offline only; added 2026-10-12 at STOP 3) | [P3b.md](P3b.md) | 0 | continue |
| 7 | P4: delete the gated path (user decided 2026-10-12; no performance gate) | [P4.md](P4.md) | 0 | continue |
| 8 | W4: anchors from motion, HUD regions | [W4.md](W4.md) | 0 | continue |
| 9 | L1: health report against truth, then one long act run | [L1.md](L1.md) | ≤ 220 | done (health red: player identity deadlock) |
| 10 | TL: lean tracking layer (self spot from motion, probe, audit fixes), then L2 resume (added 2026-10-12) | [TL.md](TL.md) | ≤ 110 | **STOP 5**: user reads progress, then health |

**Build-first decision (user, 2026-10-12, after STOP 3):** finish the system before judging intelligence. Live 40-step "better than the gated path?" comparisons are retired: both paths were stuck in the same house for reasons unrelated to decision-making, and every live failure so far was a correctness bug. Phases gate on unit tests and on **correctness checks against RAM truth** (player identity, one place per map, cuts change place, seen cells in bounds, actions reach their target), computed offline at 0 requests. Progress (maps, party, text) is reported, never gated, until L1 shows green health.

- **Next phase** = the first row whose packet Status is not `gate passed`. A phase marked `gate failed` or `blocked` means stop and report; do not skip it.
- **At a STOP:** fill in the packet's Result, update `docs/plan.md` and `handoff-notes.md`, commit, then end the session with a short report to the user. The user restarts the same kickstart prompt to continue.
- **Any failed gate:** stop immediately, report numbers and evidence, and do not attempt a second design. One repair round is allowed only where a packet says so.

## 2. Rules for every phase (paste these into every subagent prompt)

1. **Tools (CLAUDE.md, mandatory):** Ponytail full (reuse > stdlib > minimal code; no unrequested abstractions; mark deliberate shortcuts with a `ponytail:` comment); CodeGraph first (`codegraph_explore` MCP or `codegraph explore "<query>"`) before grep/Read, and check blast radius before editing; Headroom (`headroom_compress`/`headroom_retrieve`) for large outputs and logs.
2. **Scope:** only the files the WP card lists. **Line budgets are suspended (user, 2026-10-12):** record line counts in the Result, never stop or trim for them; a compression pass follows once the program works. Never touch `loop/`. Never edit `docs/plans/contracts.md`; if a contract must change, stop and report the proposed text.
3. **Game-neutral:** nothing game-specific in `agent/`: no game, place, character, item, route or RAM address. RAM truth (`scripts/truth_pokered.py`, `truth.jsonl`) is for evaluation only and is never read by `agent/`.
4. **No new gates:** World Memory stores facts, answers queries and executes the model's own `go_to`; it never picks goals, filters options or overrides the model (user decision 2026-10-12).
5. **Tests:** unit tests use `StubModel`/`GridEnv` from `tests/agent/fakes.py` and make 0 cloud calls. Each non-trivial branch leaves one runnable check. Use `.venv-agent/Scripts/python.exe`, forward-slash paths.
6. **Cloud calls:** only phases with a request cap in §1 (P2, P3, L1, TL) spend requests, only through the persistent ledger in `scripts/run_agent.py` (`_ledger`, M1 bucket), never above the packet cap. Never reset a ledger, refund or raise a cap. Preserve `.cache/model/`. Runner exit code 3 (cap) or 4 (model unavailable) → checkpoint, report, stop.
7. **Data safety:** never modify `runs/m1/` or its checkpoints; copy to a new directory first, as the packets say. Never `git add -A`, never push.

## 3. Model tiers

- **Current (2026-10-12, for L1):** orchestrator **GPT-6.1 Sol**; subagents **gpt-6-luna at high reasoning effort** for every WP, set explicitly on every spawn. P4 and W4 ran on a Claude Sonnet orchestrator with Haiku subagents; P2–P3b ran on GPT-6.1 Sol with gpt-6-luna high. Never Opus.
- The orchestrator writes exact WP specs from the packet, delegates, reviews diffs against the card, and runs every acceptance command itself.
- One WP per subagent. Paste: §2 rules, the WP card, and the contracts.md text the card cites. A subagent that fails a WP twice → the orchestrator stops and reports; it does not take over the implementation itself.

## 4. Budgets across the program

- **Cloud requests:** P2 ≤ 40 (29 used) + P3 ≤ 80 (43 used) + L1 ≤ 220 (110 used) + TL ≤ 110 from the M1 bucket (411 left after P3 on 2026-10-12; P3b used 0).
- **Lines:** budgets suspended 2026-10-12 (report only; compress after the program). Original estimates, for reference (`agent/`, 1,988 on 2026-10-12): W0 +54 (actual), W1 ≈ +35, W2 ≈ +65, W3 ≈ +105, P4 ≈ −400 or more, W4 ≈ +60. Each packet states its own budget; overruns are reported, never hidden by compacting unrelated code.

## 5. Commits

At each gate pass: `git add` the paths the packet lists plus `docs/`, message `<PHASE>: <one-line gate result>`, with the trailer the repo uses. No push.

## 6. Kickstart prompt

The current prompt (GPT-6.1 Sol orchestrator, gpt-6-luna high subagents) is in [kickstart.md](kickstart.md).
