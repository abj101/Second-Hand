# Kickstart prompt: GPT-6.1 Sol orchestrator, gpt-6-luna high subagents

Paste the block below into GPT-6.1 Sol, in the repo root. (P4 and W4 ran on Sonnet + Haiku; L1 runs on this tier.)

```text
You are the orchestrator for the R1 + World Memory program of the lean game-playing agent,
in repo C:\Users\Abj1\Documents\GitHub\Avatar on branch feat/lean-agent.

Model tiers (user decision): you are GPT-6.1 Sol. Every subagent you spawn is gpt-6-luna
with reasoning effort HIGH. Set model and effort explicitly on every spawn. Never use any
other subagent model. One work package (WP) per subagent.

1. Read, in order: CLAUDE.md; docs/plans/program-r1-wm.md (all of it);
   docs/plans/handoff-notes.md; docs/plans/contracts.md (sections: run directory and
   truth, trace, world, notebook and act). Read code via codegraph_explore first, and only
   what the current packet lists.
2. Find the next phase: the first row of program-r1-wm.md §1 whose packet Status is not
   "gate passed" (expected: L1, docs/plans/L1.md). W4 counts as passed; its anchor row
   was deferred into L1 by the user. If a packet says "gate failed" or "blocked", report
   it and stop. Execute the packet exactly: entry criteria, WPs in run order, acceptance
   commands, gate.
3. For each WP (L1.1 only; L1.2 and L1.3 are yours): write a self-contained subagent
   prompt. It must hold the WP card verbatim; the program §2 rules verbatim; the cited
   contracts.md text; the exact files it may touch; and the acceptance commands. Spawn
   gpt-6-luna (high). Review its diff against the card (files, no game-specific code in
   agent/, evaluation-only fields), then run the acceptance commands yourself. A WP is done
   only when they exit 0. One repair round with the exact failing output; if it fails
   twice, stop and report.
4. After L1.1, run the full suite in the background (~5 min). Read the real pytest
   summary line, not a piped shell exit code:
   .venv-agent/Scripts/python.exe -m pytest tests/agent -q -p no:cacheprovider
5. Live run (L1.2), the only request spend left:
   - Read runs/e3_budget.json first; stop if M1 has < 220 left. Record it before and after.
   - Fresh boot into runs/m1_long (never resume runs/m1 or runs/m1_act). First half:
     .venv-agent/Scripts/python.exe scripts/run_agent.py --run runs/m1_long --steps 100 --max-calls 110 --stage c
   - Then: scripts/eval_run.py runs/m1_long --health, and
     scripts/check_world.py anchors runs/m1_long (W4's deferred gate row).
   - Any red row except screen_places → do not run the second half; report.
     Otherwise run the second half with --resume --steps 100 --max-calls 110.
   - Exit code 3 or 4: checkpoint, report, stop. Never modify runs/m1/ or runs/m1_act/
     (use --no-write when evaluating them). Never reset, refund or raise a ledger cap.
   - Do not fix, tune or revert agent/ code during L1. Red rows are evidence for the user.
6. Gate and report (L1.3): fill in L1's Result section and Status line, update the phase
   row in docs/plan.md, and rewrite docs/plans/handoff-notes.md (rewrite, don't append;
   under 80 lines; fixed headings). Commit the packet's paths plus docs/
   ("L1: <one-line result>", with the repo's Co-Authored-By trailer). Never commit runs/
   or .tmp/, never git add -A, never push.
7. L1 ends at STOP 4. End with a short report: the health table after each half (with the
   commands), the W4 anchor confusion matrix, progress (truth maps reached and when, party
   size, evidence-backed goals, go_to travel), the worst steps for every red row with
   frame paths and the act "why", requests spent and the ledger after.
8. Never edit contracts.md or anything under loop/. If a contract has to change, stop and
   report the exact proposed text.
```
