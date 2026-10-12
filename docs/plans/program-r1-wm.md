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
| 7 | P4: delete the gated path | [P4.md](P4.md) | 0 | continue |
| 8 | W4: anchors from motion, HUD regions | [W4.md](W4.md) | 0 | **STOP 4**: program done |

- **Next phase** = the first row whose packet Status is not `gate passed`. A phase marked `gate failed` or `blocked` means stop and report; do not skip it.
- **At a STOP:** fill in the packet's Result, update `docs/plan.md` and `handoff-notes.md`, commit, then end the session with a short report to the user. The user restarts the same kickstart prompt to continue.
- **Any failed gate:** stop immediately, report numbers and evidence, and do not attempt a second design. One repair round is allowed only where a packet says so.

## 2. Rules for every phase (paste these into every subagent prompt)

1. **Tools (CLAUDE.md, mandatory):** Ponytail full (reuse > stdlib > minimal code; no unrequested abstractions; mark deliberate shortcuts with a `ponytail:` comment); CodeGraph first (`codegraph_explore` MCP or `codegraph explore "<query>"`) before grep/Read, and check blast radius before editing; Headroom (`headroom_compress`/`headroom_retrieve`) for large outputs and logs.
2. **Scope:** only the files the WP card lists. **Line budgets are suspended (user, 2026-10-12):** record line counts in the Result, never stop or trim for them; a compression pass follows once the program works. Never touch `loop/`. Never edit `docs/plans/contracts.md`; if a contract must change, stop and report the proposed text.
3. **Game-neutral:** nothing game-specific in `agent/`: no game, place, character, item, route or RAM address. RAM truth (`scripts/truth_pokered.py`, `truth.jsonl`) is for evaluation only and is never read by `agent/`.
4. **No new gates:** World Memory stores facts, answers queries and executes the model's own `go_to`; it never picks goals, filters options or overrides the model (user decision 2026-10-12).
5. **Tests:** unit tests use `StubModel`/`GridEnv` from `tests/agent/fakes.py` and make 0 cloud calls. Each non-trivial branch leaves one runnable check. Use `.venv-agent/Scripts/python.exe`, forward-slash paths.
6. **Cloud calls:** only P2 and P3 spend requests, only through the persistent ledger in `scripts/run_agent.py` (`_ledger`, M1 bucket), never above the packet cap. Never reset a ledger, refund or raise a cap. Preserve `.cache/model/`. Runner exit code 3 (cap) or 4 (model unavailable) → checkpoint, report, stop.
7. **Data safety:** never modify `runs/m1/` or its checkpoints; copy to a new directory first, as the packets say. Never `git add -A`, never push.

## 3. Model tiers

- **Orchestrator:** GPT-6.1 Sol. Writes exact WP specs from the packet, delegates, reviews diffs against the card, runs every acceptance command itself.
- **Subagents:** `gpt-6-luna` at **high** reasoning effort, for every WP, no exceptions. Set model and effort explicitly on every spawn. No other subagent models.
- One WP per subagent. Paste: §2 rules, the WP card, and the contracts.md text the card cites. A subagent that fails a WP twice → the orchestrator stops and reports; it does not take over the implementation itself.

## 4. Budgets across the program

- **Cloud requests:** P2 ≤ 40 + P3 ≤ 80 = **≤ 120** from the M1 bucket (483 left on 2026-10-12; phase total cap 1,100, 585 used).
- **Lines:** budgets suspended 2026-10-12 (report only; compress after the program). Original estimates, for reference (`agent/`, 1,988 on 2026-10-12): W0 +54 (actual), W1 ≈ +35, W2 ≈ +65, W3 ≈ +105, P4 ≈ −400 or more, W4 ≈ +60. Each packet states its own budget; overruns are reported, never hidden by compacting unrelated code.

## 5. Commits

At each gate pass: `git add` the paths the packet lists plus `docs/`, message `<PHASE>: <one-line gate result>`, with the trailer the repo uses. No push.

## 6. Kickstart prompt (paste this into GPT-6.1 Sol)

```text
You are the orchestrator for the R1 + World Memory program of the lean game-playing agent,
in repo C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

Model tiers are fixed by the user: you are GPT-6.1 Sol. Every subagent you spawn is
gpt-6-luna with reasoning effort HIGH. Set model and effort explicitly on every spawn.
Never use any other subagent model. One work package (WP) per subagent.

1. Read, in order: CLAUDE.md; docs/plans/program-r1-wm.md (all of it);
   docs/plans/handoff-notes.md; docs/plans/contracts.md (sections: memory, space, trace,
   run directory and truth, notebook and act, world); docs/plans/world-memory.md and
   docs/plans/autonomy-redesign.md for design context. Read code only via codegraph_explore,
   and only what the current packet lists.
2. Find the next phase: the first row of program-r1-wm.md §1 whose packet Status is not
   "gate passed". If a packet says "gate failed" or "blocked", report it and stop.
   Open that packet and execute it exactly: entry criteria, WPs in run order, acceptance
   commands, gate.
3. For each WP: write the subagent prompt from the WP card, paste program §2 rules and the
   cited contracts.md text into it, spawn gpt-6-luna (high), review its diff against the
   card (files, line budget, no game-specific code, no new gates), then run the acceptance
   commands yourself. A WP is done only when they exit 0 and the budget holds. After the
   last WP of a phase, run the full suite:
   .venv-agent/Scripts/python.exe -m pytest tests/agent -q
   (~5 min; run it in the background if your shell times out).
4. Cloud calls only in P2 and P3, only through the ledger, never above the packet cap.
   Exit code 3 or 4 from a runner: checkpoint, report, stop. Never modify runs/m1/.
5. Gate: run it exactly as the packet says. Fill in the packet's Result section and Status
   line, update the phase table row in docs/plan.md, and rewrite
   docs/plans/handoff-notes.md (rewrite, don't append; under 80 lines; fixed headings).
   On a pass, commit the listed paths plus docs/ ("<PHASE>: <one-line gate result>");
   never git add -A, never push.
6. Then continue with the next phase, unless program §1 marks a STOP after this phase or
   a gate failed. At a STOP or failure, end with a short report to the user: phase,
   gate numbers with the commands that produced them, deviations, open questions,
   and requests spent.
7. Never edit contracts.md or anything under loop/. If a contract has to change, stop and
   report the exact proposed text.
```
