# Execute prompt: round r23 (paste into the ChatGPT orchestrator session at the repo root)

```text
You are the orchestrator for Avatar round r23 in repo C:\Users\Abj1\Documents\GitHub\Avatar,
branch feat/lean-agent, HEAD 8170f0a. You are GPT-6.1 Sol. Every subagent is gpt-6-luna at
reasoning effort HIGH, set explicitly on every spawn, with fresh context and one work package each.

Read, in order, before doing anything:
1. CLAUDE.md (Ponytail full, CodeGraph first, Headroom for big outputs; these rules also go
   into every subagent prompt).
2. docs/plans/execute-r23.md, all of it. It is your brief, and §7 is your protocol.
3. docs/plans/execute-r18-fix.md §6 (standing rules 1-9). Paste them verbatim into every
   lane block, with scratch at .tmp\r23\<WP>\.
4. docs/plans/handoff-notes.md and the contracts.md sections the brief cites.

What r23 is: fix the mechanical errors left after r22, before any work on the agent's sense of
direction. Four modules, each game-agnostic and active only when the game shows the capability
at runtime:
- T terrain (grid games only): routes prefer walked floor over unknown cells; a new wall
  counts as learning; explore never re-offers a failed target; the walk past a target stops
  at known obstacles.
- S status meters: the perceiver reports labelled gauges/counters; the notebook remembers the
  latest reading per name; act and planner see one "Status (last seen)" line. No thresholds
  or advice.
- N place names: a game-given name beats the perceiver's picture guess in every display.
- O odometer: fate tags land on the logged segments so the dropped counter matches the log.
Plus D4 (diagnose the step-220 odometer error) and H1 (measure meter reading before S merges).

User decisions already made (2026-10-26):
- The design in execute-r23.md §2 and the contract amendments in §2.1 are approved.
- The round's ledger ceiling is raised to 11260 (H1 at most 20 requests, LV23 cap 160).
- Direction / place-identity work is out of scope for r23.
- agent/ stays game-agnostic: no game words, no RAM/ROM reads, no per-game config keys. Each
  module is inert when its capability is absent, and each WP proves that with one test.

Execute §7 exactly: wave 0 (status check, commit the plan and amendments, baseline suite,
ledger), wave 1 (T, D4, H1 in parallel worktrees under C:\Users\Abj1\Documents\GitHub\Avatar-wt\),
wave 2 (NO, then run H1's paid measurement yourself), wave 3 (S only if H1 passed), G23,
then LV23 once. You alone review diffs, run gates, spend model requests, merge (--no-ff) and
run the live run. Do not implement any WP yourself. Allow one repair round per gate with the
exact failing output; if a gate fails twice, stop and report.

Hard rules:
- Never push.
- Never git worktree remove --force.
- Never link runs/ or Rom/ into worktrees.
- Never set W0_REGEN.
- Never edit the user's five files: scripts/eval_frames.py, scripts/watch_run.py,
  tests/agent/test_watch_run.py, baseline_eval.txt, scripts/frames_combo.json.
- runs/ is read-only, except creating runs/m1_r23_odo for LV23.
- Always run pytest with -n auto and read pytest's own summary line.
- Before LV23, check the live viewer (curl -s http://127.0.0.1:8765/state); start
  `python scripts/watch_run.py --no-browser` in the background if it is down.

Stop and ask the user on any §7 stop condition, especially:
- a non-pre-approved assertion failure;
- golden data changes;
- T18 gate changes;
- the ledger would pass 11260;
- D4 finds the step-220 error reached stored memory (ask before LV23).

Finish with .tmp/r23/report.md, an updated docs/plans/handoff-notes.md (under 80 lines) and
the commit "docs: r23 result".
```
