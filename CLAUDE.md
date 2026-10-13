# Agent tooling (mandatory — main agent AND every subagent)

Always on, no prompting needed. When spawning subagents, repeat these rules in their prompt.

- **Ponytail (full)**: invoke the `ponytail` skill on every coding task; follow its ladder (reuse > stdlib > minimal code). Only "stop ponytail" / "normal mode" turns it off.
- **CodeGraph**: `.codegraph/` exists. Use `codegraph_explore` (MCP) or `codegraph explore "<query>"` (shell) BEFORE grep/find/Read to locate or understand code and check blast radius before editing.
- **Headroom**: use `headroom_compress` on large tool output / files / logs before they enter context, and `headroom_retrieve` to fetch the original when needed; `headroom_stats` to check savings.

# Live run viewer

`scripts/watch_run.py` is a read-only web page (127.0.0.1:8765) showing the frame, the decision, the buttons pressed, the feed, the goals and the spatial map of an agent run.

- **The user keeps it open.** With no `--run` it follows whichever `runs/*/trace.jsonl` changed last, so any run started with `scripts/run_agent.py` streams to it automatically. There is nothing to enable per run.
- **Before a live run**, check that it is up (`curl -s http://127.0.0.1:8765/state > /dev/null`). If it isn't, start it in the background: `python scripts/watch_run.py` (add `--no-browser` if a tab is already open; `--run runs/<name>` pins one run).
- **A new runner** streams to it if it writes the same run-folder files as `run_agent.py`: `trace.jsonl` (one `Loop.step()` row per line), `frames/NNNNNN.png`, `memory/` (a `Memory` root plus `notebook.json`), `calls.jsonl`, and `presses.jsonl` (`{"step", "keys"}`, from wrapping `env.press`).
