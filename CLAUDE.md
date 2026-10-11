# Agent tooling (mandatory — main agent AND every subagent)

Always on, no prompting needed. When spawning subagents, repeat these rules in their prompt.

- **Ponytail (full)**: invoke the `ponytail` skill on every coding task; follow its ladder (reuse > stdlib > minimal code). Only "stop ponytail" / "normal mode" turns it off.
- **CodeGraph**: `.codegraph/` exists. Use `codegraph_explore` (MCP) or `codegraph explore "<query>"` (shell) BEFORE grep/find/Read to locate or understand code and check blast radius before editing.
- **Headroom**: use `headroom_compress` on large tool output / files / logs before they enter context, and `headroom_retrieve` to fetch the original when needed; `headroom_stats` to check savings.
