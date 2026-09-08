from __future__ import annotations

"""Optional stdio MCP server for Cursor debugging."""

from loop.mcp.registry import ToolRegistry


def run_stdio(registry: ToolRegistry) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit("Install mcp package: pip install mcp") from exc

    app = FastMCP("avatar-loop")

    for spec in registry._tools.values():  # noqa: SLF001 — wire all registered tools
        name = spec.name

        def _make_handler(tool_name: str):
            def _handler(**kwargs: object) -> str:
                return registry.call(tool_name, dict(kwargs))

            _handler.__name__ = tool_name
            return _handler

        app.tool(name=name, description=spec.description)(_make_handler(spec.name))

    app.run()
