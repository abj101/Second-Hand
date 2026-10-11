# Phase packet template

Every phase (E1b, E2, E3, E4, E5) gets one file, `docs/plans/<PHASE>.md`, in exactly this shape.
One phase = one orchestrator session. The user pastes the **Paste block** and nothing else; the orchestrator reads everything else from the repo.

Why this shape:
- **Short paste, long file.** The paste only points at files, so a plan edit never leaves a stale copy in someone's clipboard.
- **One contract file.** `docs/plans/contracts.md` holds every frozen signature. Phases cite it and never restate it, so two orchestrators can't drift apart.
- **Disjoint file ownership.** Each work package (WP) owns its files. WPs with no shared files and no dependency between them run as parallel subagents.
- **Acceptance means commands.** Every WP ends in commands that exit 0, not prose to interpret.
- **Mandatory handback.** The orchestrator fills in the Result section, which feeds the user's gate decision. It then rewrites `handoff-notes.md`, the compacted memory the next orchestrator reads first.

---

```markdown
# <PHASE>: <one-line question this phase answers>

Status: not started | in progress | gate passed | gate failed | blocked   ·   Branch: feat/lean-agent

## Paste block

    You are the orchestrator for phase <PHASE> of the lean agent, in repo
    C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

    1. Read, in order: CLAUDE.md, docs/plans/handoff-notes.md, docs/plans/contracts.md,
       docs/plans/<PHASE>.md.
       Then read only the files that packet lists.
    2. Execute the work packages in the order its "Run order" gives. Delegate each WP to a
       subagent with the model tier the packet names (Haiku or Sonnet; never Opus; always set it
       explicitly). Paste the CLAUDE.md rules and the WP card into every subagent prompt.
    3. After each WP, run its acceptance commands yourself. A WP is done only when they exit 0.
       Cloud calls are scarce. Never exceed the packet's call cap. Unit tests use StubModel only,
       and every run goes through the response cache. A WP's acceptance runs only that WP's test
       files; you run the full `tests/agent` suite after each parallel batch. Use forward-slash
       commands (`.venv-agent/Scripts/python.exe ...`); they work in PowerShell and Git Bash.
    4. Stay inside the files the packet lists. Never touch loop/. Never edit contracts.md.
       If a contract has to change, stop and report it.
    5. Run the phase gate, fill in the packet's "Result" section, set its Status line, and update
       the phase table in docs/plan.md.
    6. Hand off: rewrite docs/plans/handoff-notes.md as a semantically compacted digest,
       following section 10 of the packet.
    7. If the gate passed, commit this phase's files: `git add` the paths the packet lists,
       plus docs/. Never `git add -A`, never push. Message: "<PHASE>: <one-line gate result>".
       Then stop. Do not start the next phase.

## 1. Entry criteria
- Previous gate: <PHASE-1> passed (link to its Result section), or "none".
- Required on disk: <ROM path / .tmp data / venv>. If anything is missing, stop and ask.

## 2. Gate
| Metric | Pass | Measured by (command) |
|---|---|---|
| ... | ... | `...` |

- **Decision this gate produces:** <e.g. Chooser = Gemma vs decider-2b>
- **If it fails:** <the fallback WP, or what to report to the user>

## 3. Read list
Only what this phase needs: spec sections by § number, plus exact files. Old `loop/` sources are
marked "copy and trim, never import".

## 4. Contracts
- **Uses:** contracts.md#<anchor>, ...
- **Provides:** contracts.md#<anchor>. These are already frozen there; this phase implements them.

## 5. Work packages

| WP | Goal | Owns files (line budget) | Depends on | Tier | Est. |
|---|---|---|---|---|---|
| <PHASE>.1 | ... | `agent/x.py` (≤60) | — | Haiku | ½ d |

**Run order:** <PHASE>.1 ‖ <PHASE>.2 → <PHASE>.3   (‖ = run in parallel)

### <PHASE>.1: <name>
- **Goal:** one sentence.
- **Read:** files and symbols (CodeGraph query to use).
- **Create or change:** `path` (≤N lines). Nothing else.
- **Implements:** contracts.md#<anchor>.
- **Acceptance:**
  - `.venv-agent/Scripts/python.exe -m pytest tests/agent/test_x.py -q`
  - `.venv-agent/Scripts/python.exe scripts/check_x.py ...` (the one runnable check, per Ponytail)
- **Done when:** the acceptance commands exit 0, the line budget holds, and there are no imports from `loop/`.
- **Risks:** ... and the mitigation for each.

## 6. Test layers for this phase
| Layer | Gates this phase? | Command |
|---|---|---|
| Unit (pure logic) | yes | `pytest tests/agent -q` |
| Replay (saved frames) | ... | ... |
| PyBoy integration (needs the ROM) | ... | ... |
| Metrics (spec §11) | ... | ... |

## 7. Budgets
- **Lines:** this phase +N. Running total vs 1,500: ...
- **Cloud calls:** per step ..., per gate run ... (free tier: <throttle assumption>)

## 8. Out of scope
- Never touch `loop/`. Also out: real-time games, per-game training, a trained System-1 model.
- Plus this phase's specific exclusions.

## 9. Result (filled in by the orchestrator)
- **Gate:** pass or fail, with each metric's number and the command that produced it.
- **Decision taken:** ...
- **Deviations from the packet:** files, budgets, scope.
- **Contract changes needed:** none, or the proposed change and why. These are applied by the user, not by the orchestrator.
- **Open issues for the next phase:** ...

## 10. Handoff notes (the last thing the orchestrator does)
Rewrite `docs/plans/handoff-notes.md`. It is the only memory passed between orchestrators.
- **Rewrite; don't append.** Merge this phase's lessons into the existing notes, and drop anything that is:
  - superseded;
  - already stated in contracts.md or spec.md;
  - only of historical interest. History stays in Result sections.
- **Keep it under 80 lines**, under these fixed headings:
  - State of the code
  - Decisions in force
  - Measured numbers
  - Environment and gotchas
  - Deviations from plan and contracts
  - Do not retry
  - Open issues for <NEXT PHASE>
- **One fact per bullet**, with a number, a command or a `file:line` where possible. No narrative, no "we tried".
- **Test:** could the next orchestrator start correctly from this file plus its own packet, with no transcript? If not, add what's missing.
- **Check:** every path mentioned exists, and every number matches a Result section.
```
