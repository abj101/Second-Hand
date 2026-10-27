# Kickoff prompt: round r24 (paste into the ChatGPT orchestrator session at the repo root)

```text
You are the orchestrator for Avatar round r24 in repo C:\Users\Abj1\Documents\GitHub\Avatar,
branch feat/lean-agent, HEAD 4770721. You are GPT-6.1 Sol. Your one subagent is gpt-6-luna
at reasoning effort HIGH, set explicitly on the spawn, with fresh context.

Read, in order, before doing anything:
1. CLAUDE.md (Ponytail full, CodeGraph first, Headroom for big outputs; repeat these rules in
   the subagent prompt).
2. docs/plans/fields-r24.md, all of it. It is your brief, and §6 is your protocol.
3. docs/plans/execute-r18-fix.md §6 (standing rules 1-9). Paste them verbatim into the F block,
   with scratch at .tmp\r24\F\.
4. docs/plans/handoff-notes.md and the contracts.md sections the brief amends
   (## perception, ## notebook and act, ## observer).

What r24 is: replace r23's free-text "meters" with "fields". A field is one on-screen readout
with an owner, a printed label, a kind (bar or number) and a box. The perceiver says where it
is and what it is; the harness measures what it can:
- a number keeps its value only if its digits are in the screen text;
- a bar is never valued by the model: agent.observer.bar_fill reads its fill from pixels.
  Offline it matched RAM HP within 1 px on 61/61 drawn bars (.tmp/r24/bars2.py).
Readings feed the existing notebook status line, with bars shown as "%".
The system stays game-agnostic and is inert when no fields are perceived. Direction work is
out of scope.

User decisions already made (2026-10-27):
- The design in fields-r24.md and its contract amendments are approved.
- The round's ledger ceiling is 11600. The effective ledger is 11239: runs/e3_budget.json
  shows 11223, plus 16 r23 H1 requests charged outside it. Caps: G-paid at most 20 requests,
  LV24f at most 45. Spend nothing else.
- agent/ stays game-agnostic: no game words, no RAM/ROM reads, no new module, class, store
  or config key.

Execute fields-r24.md §6 exactly:
1. Wave 0: check git status; commit the amendments with this brief and prompt; run the
   baseline suite (expect 959 passed, 1 skipped); record the ledger.
2. Wave 1: worker F in C:\Users\Abj1\Documents\GitHub\Avatar-wt\r24F; review the diff; run
   the suite and G-off; merge --no-ff.
3. Wave 2: freeze and run G-paid yourself; then full suite, regression gates and golden check.
4. LV24f once, only if G-paid passes.
You alone review diffs, spend model requests, merge and run the live check. Do not implement
the WP yourself. Allow one repair round per gate with the exact failing output; if a gate
fails twice, stop and report.

Hard rules:
- Never push.
- Never git worktree remove --force.
- Never link runs/ or Rom/ into worktrees.
- Never set W0_REGEN.
- Never edit the user's five files: scripts/eval_frames.py, scripts/watch_run.py,
  tests/agent/test_watch_run.py, baseline_eval.txt, scripts/frames_combo.json.
- runs/ is read-only, except creating runs/m1_r24_fields (a copy of runs/m1_r23_odo) for
  LV24f: --resume --from-step 215 --steps 15 --max-calls 45.
- Always run pytest with -n auto and read pytest's own summary line. Run it from the repo
  root, where ROM tests find the ROM.
- Before LV24f, check the live viewer (curl -s http://127.0.0.1:8765/state); start
  `python scripts/watch_run.py --no-browser` in the background if it is down.

Stop and ask the user on any stop condition in fields-r24.md §5.

Finish with .tmp/r24/report.md, an updated docs/plans/handoff-notes.md (under 80 lines) and
the commit "docs: r24 result".
```
