# Handoff notes

Last written: 2026-10-14, after IP (gate PASS; planner 3/3 + 3/3; six requests). Branch `feat/lean-agent`. Packet: [IP.md](IP.md); previous: [OM3.md](OM3.md), sections 11–13.

## State of the code
- Planner `Plan.facts` stores relevant heard/seen information in notebook `hints` (20 entries, keys overwrite). `Notebook.render()` and `render_focus()` label it `facts (what the game told or showed):`; planner and decide see it. Render trimming drops lessons, finished goals, then facts.
- `Loop._learn` buffers fresh off-world dialogue/menu lines, joins prefix/overlap extensions, strips continuation marks, and flushes once on return to world or place change: `<speaker or text> at <place>: "<joined sentence(s)>"`. World-screen text still logs immediately; knowledge stays per line. Fully known conversations do not log again.
- Planner checkpoint fields `conversation: {place, lines}` and `text_for_trigger` keep an unfinished conversation across restore. The world-screen text trigger includes up to 200 characters and bypasses the three-step floor.
- `Memory.call(entity_id, name, step)` attaches the first `called` speaker name to an actor. World option/view labels prefer it; resolve matches it case-insensitively. Failure keys and actions retain perceived names.
- Planner rules prefer heard goals, allow heard places before mapping, and put exploration under the purpose it serves. `render_focus` shows 10 recent log entries; persisted history and planner `render_log(40)` are unchanged.
- `eval_act.py --om talk --run R` uses recorded decisions/cached perception and stops after the first later named interaction. `--om plan --run R --from-step N --tries K` prints the checkpoint planner prompt; `--live` makes independent, uncached planner tries. Each POST, including retries, counts against K (at most 8).
- Earlier world fixes remain: first place name sticks; open cells beyond screen edges count as unexplored; off-screen verified exits travel through their recorded tile; long travel uses per-press cuts. Moving-actor identity is still unsolved.

## Decisions in force
- Ponytail full, CodeGraph first, Headroom on every coding task. Python: `.venv-agent/Scripts/python.exe`.
- This phase used Luna high workers with explicit model/effort, per user instruction overriding old packet tier names.
- No game-specific code/prompts in `agent/`; no mechanical option filtering or history-based removal/reordering.
- Commit at passed gates using explicit paths; never `git add -A`, never push. Only the user merges contract proposals and approves budget cap raises/live runs.

## Measured numbers
- Final suite: **354 passed**, `-m pytest tests/agent -q -p no:cacheprovider -n auto` (~35 s). All WP acceptance commands exited 0.
- `scripts/eval_act.py --om cases`: right present 8/8, exit truth 8/8, size 8/8; 1,374–1,601 tokens. Existing evaluator cap 1,700 unchanged. Zero requests.
- `scripts/eval_act.py --om talk --run m1_decide3`: one first-conversation log at step 47 includes the full Oak/next-door sentence and starts `MOM at tiled room`; `e_person_2` and `e_npc_2` have `called: MOM`; step 56 offers `interact with MOM`. Zero requests.
- `scripts/eval_act.py --om plan --run m1_decide3 --from-step 50 --tries 3 --live`: facts plus heard Oak goal 3/3; focus goes next door beneath finding Oak.
- Same command with `--from-step 75`: relevant focus path 3/3, `finish the game > find Professor Oak > explore outside a house`.
- Ledger `runs/e3_budget.json`: total **2864/2925**, M1 **2816/2875**; IP spent **6/8**, no retries/cap changes. Remaining total 61, M1 59. Evidence: `.tmp/ip/gate-{suite,cases,talk,plan50,plan75}.txt`; dry prompts `.tmp/ip/plan{50,75}-dry.txt`.
- Recommend funding a clean-boot run; checkpoint intent checks passed, but destination navigation has not been measured. No live run started.

## Environment and gotchas
- Original `runs/m1_decide3`, checkpoints 000050/000075 and model cache are untouched; replays use `.tmp/om/` copies.
- Changing agent code/evaluator rebuilds OM snapshots (hash includes both). Older pickle snapshots receive defaults for the new conversation fields. Recorded option matching tolerates called-label changes via the cached target index.
- `.claude/` remains untracked. Live viewer: `scripts/watch_run.py` on 127.0.0.1:8765; check it before a future live run.
- Golden `tests/agent/data/w0_golden` is ignored but tracked; regenerate with `W0_REGEN=1` only for a justified world change, then stage explicit paths with `-f`.

## Deviations from plan and contracts
- IP's merge-ready contract additions are in [IP.md section 9](IP.md#9-result-filled-in-by-the-orchestrator); **contracts.md was not edited**. User must merge them.
- Act log view was bounded from 15 to 10 entries because conversation-sized entries retained old boot/menu text and failed the dry size gate; full history/planner history remain. Initial dry size 6/8, fresh-line correction 7/8, final 8/8. Limits unchanged.
- Notebook comment `moment` became `briefly` to pass the literal game-name substring scan. Talk replay stops once its evidence is complete (step 56).

## Do not retry
- Do not re-measure these six live planner tries or the old eight live option cases. Do not iterate prompt wording with live spend.
- Do not raise caps or launch a clean-boot run autonomously. Existing requests cannot fund the proposed ~250-request run.

## Open issues for the next phase (user decides)
1. Moving-NPC identity: m1_decide3 has 39 actor ids; this replay attaches MOM to two ids. Match nearby known actors so names/history follow them.
2. Outside explore routes were blocked twice at step 80; investigate separately from intent planning.
3. Outdoor perception/re-looks cost about 2.4 requests/step; a clean-boot run needs a user-approved cap raise.
4. Planner edits can leave children of removed exploration parents; the outside leaf's completion condition remains broad. Check sustained pursuit and observable completion in the next funded run.
