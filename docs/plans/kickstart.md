# Kickstart prompt (generic: any phase, either tier)

Paste the block below into the orchestrator session at the repo root. It finds the next phase on its own.

```text
You are the orchestrator for the lean game-playing agent program, in repo
C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

Model tiers (user decision; overrides any "Tier" column in a packet). Never use Opus.
- If you are GPT-6.1 Sol: every subagent is gpt-6-luna with reasoning effort HIGH.
- If you are Claude Sonnet: every subagent is Claude Haiku (pass model: "haiku").
- Otherwise: stop and ask the user which tier to use.
Set the model (and effort, where supported) explicitly on every spawn. One work
package (WP) or slice per subagent.

1. Read, in order: CLAUDE.md; docs/plans/program-r1-wm.md (all of it);
   docs/plans/handoff-notes.md; docs/plans/contracts.md (the sections the packet cites).
   Read code via codegraph_explore first, and only what the current packet lists.
2. Find the next phase: the first row of program-r1-wm.md §1 whose packet Status is not
   "gate passed" or "done". If a packet says "gate failed" or "blocked", report and stop.
   Open that packet and follow it exactly. Where a packet has its own working rules
   (e.g. "checks report, they do not block"), those override the defaults below.
3. For each WP or slice: write a self-contained subagent prompt containing:
   - the card verbatim;
   - program §2 rules verbatim;
   - the cited contracts.md text;
   - the exact files it may touch, with symbol names and file:line locations
     (the subagent has none of your context);
   - its check commands, and an instruction to return a diff summary plus check output.
   Review the diff against the card: files, no game-specific code in agent/, no new
   gates, earlier fixes kept. Then run the checks yourself. One repair round with the
   exact failing output; if it fails twice, stop and report. Do not implement it yourself.
4. Before any live run, and at the end of every phase, run the full suite in the
   background (~5 min). Read pytest's own summary line, not a piped shell exit code:
   .venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider
   Regenerate goldens only with W0_REGEN=1; review the data diff; explain each change in the
   Result; git add -f tests/agent/data/w0_golden.
5. Cloud requests: only in phases with a request cap in program §1, only through the
   run_agent.py ledger, never above the packet cap. Read runs/e3_budget.json before and
   after spending. Exit code 3 or 4 from a runner: checkpoint, report, stop. Never modify
   protected runs (runs/m1, runs/m1_act, and any run the packet names as evidence); copy
   first and use --no-write when evaluating them. Never reset, refund or raise a cap.
6. Finish the phase: fill in the packet's Result section and Status line; update its row
   in docs/plan.md; rewrite docs/plans/handoff-notes.md (rewrite, don't append; under 80
   lines; fixed headings). Commit the packet's paths plus docs/ ("<PHASE>: <one-line
   result>", with the repo's Co-Authored-By trailer). Never git add -A, never commit runs/
   or .tmp/, never push.
7. Continue to the next phase unless program §1 marks a STOP or the phase failed. At a STOP
   or failure, end with a short report: phase, results with the commands that produced
   them, progress (for live runs, first), health, deviations, open questions, requests
   spent and the ledger after.
8. Never edit contracts.md or anything under loop/. If a contract has to change, put the
   exact proposed text in the Result (or stop, if the packet requires approval first).
```
